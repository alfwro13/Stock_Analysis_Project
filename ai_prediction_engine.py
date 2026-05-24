# ai_prediction_engine.py
import time
import logging
import sqlite3
from pathlib import Path
from typing import List, Tuple, Dict, Optional

import pandas as pd
import numpy as np
import joblib
import yfinance as yf
import ta

from xgboost import XGBClassifier
from sklearn.ensemble import RandomForestClassifier, VotingClassifier
from sklearn.metrics import accuracy_score, classification_report
from sklearn.model_selection import TimeSeriesSplit, RandomizedSearchCV
from sklearn.calibration import CalibratedClassifierCV

from config import BASE_DIR
from database import get_connection, log_notification
from data_engine import DataEngine

logger = logging.getLogger(__name__)

MODELS_DIR = BASE_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
MODEL_PATH         = MODELS_DIR / "ml_ensemble.joblib"
FEATURE_STATS_PATH = MODELS_DIR / "feature_stats.joblib"

# ─────────────────────────────────────────────────────────────────────────────
# FEATURE REGISTRY  (18 features, up from 16)
#
# TECHNICAL FEATURES (10):
#   rsi_14, macd_pct, macd_signal_pct, macd_hist_pct, volume_surge,
#   bullish_cross, dist_sma_50, dist_sma_200, sector_code, dollar_vol_log
#
# MOMENTUM FACTORS (4) — Jegadeesh & Titman (1993):
#   mom_1m          — 21-day return.
#   mom_3m          — 63-day return.
#   mom_6m          — 126-day return.
#   mom_12m_skip1m  — 252-day return minus 21-day return.
#                     Skip-1-month removes short-term reversal contamination.
#
# VOLATILITY REGIME FEATURES (2):
#   atr_pct         — 14-day ATR / close price. Noise envelope normalised
#                     to price level. Distinguishes clean breakouts from
#                     volatile panic moves.
#   hist_vol_20     — 20-day rolling std of log returns × √252.
#                     Medium-horizon realised volatility view.
#
# RELATIVE STRENGTH VS SPY (2):
#   rel_strength_5d  — 5-day stock return minus 5-day SPY return.
#   rel_strength_20d — 20-day stock return minus 20-day SPY return.
#
#   Rationale: A stock with RSI 65 and positive momentum looks very different
#   depending on whether the whole market is at RSI 65 (neutral) or RSI 40
#   (genuinely strong). Relative strength strips out the market beta component
#   and isolates idiosyncratic price leadership. A positive rel_strength_20d
#   combined with positive cross-sectional momentum is the hallmark of a true
#   market leader — not just a stock being carried by the tide.
#
#   Benchmark: SPY (S&P 500 ETF) is used as a universal benchmark for both
#   US and UK stocks. Global equity markets are ~85% correlated at daily
#   frequency, making SPY a reasonable proxy. A per-asset benchmark (SPY for
#   US, ISF.L for UK) would be marginally more accurate but requires benchmark
#   detection logic. Documented as a known approximation for UK names.
#
#   Warmup: pct_change(20) requires 20 days — well below the 252-day momentum
#   constraint. No additional row loss from these features.
# ─────────────────────────────────────────────────────────────────────────────

FEATURE_COLS = [
    # Technical
    'rsi_14_z', 'macd_pct_z', 'macd_signal_pct_z', 'macd_hist_pct_z',
    'volume_surge', 'bullish_cross', 'dist_sma_50_z', 'dist_sma_200_z',
    'sector_code', 'dollar_vol_log_z',
    # Momentum
    'mom_1m_z', 'mom_3m_z', 'mom_6m_z', 'mom_12m_skip1m_z',
    # Volatility regime
    'atr_pct_z', 'hist_vol_20_z',
    # Relative strength vs SPY
    'rel_strength_5d_z', 'rel_strength_20d_z',
]

SECTOR_MAP = {
    "Technology": 1, "Healthcare": 2, "Financials": 3,
    "Financial Services": 3, "Real Estate": 4, "Energy": 5,
    "Basic Materials": 6, "Consumer Cyclical": 7, "Industrials": 8,
    "Utilities": 9, "Consumer Defensive": 10, "Communication Services": 11,
    "Broad Market ETF": 12, "ETF": 12, "Futures": 13,
    "Unknown": 99
}

CONTINUOUS_FEATURES = [
    'rsi_14', 'macd_pct', 'macd_signal_pct', 'macd_hist_pct',
    'dist_sma_50', 'dist_sma_200', 'dollar_vol_log',
    'mom_1m', 'mom_3m', 'mom_6m', 'mom_12m_skip1m',
    'atr_pct', 'hist_vol_20',
    'rel_strength_5d', 'rel_strength_20d',
]


def cross_sectional_zscore(series: pd.Series) -> pd.Series:
    """Calculates Z-Score dynamically. Safe against 0 standard deviation."""
    std = series.std()
    if pd.isna(std) or std == 0:
        return series - series.mean()
    return (series - series.mean()) / std


def _migrate_quant_signals_schema(cursor: sqlite3.Cursor) -> None:
    """
    Idempotent schema migration. Adds all feature columns to quant_signals
    if they do not already exist. Safe to run on every backfill.
    """
    new_columns = [
        ("mom_1m",           "REAL"),
        ("mom_3m",           "REAL"),
        ("mom_6m",           "REAL"),
        ("mom_12m_skip1m",   "REAL"),
        ("atr_pct",          "REAL"),
        ("hist_vol_20",      "REAL"),
        ("rel_strength_5d",  "REAL"),
        ("rel_strength_20d", "REAL"),
    ]
    for col_name, col_type in new_columns:
        try:
            cursor.execute(
                f"ALTER TABLE quant_signals ADD COLUMN {col_name} {col_type}"
            )
            logger.info(f"Schema migration: added column '{col_name}' to quant_signals.")
        except sqlite3.OperationalError:
            pass  # Column already exists


def _download_spy_benchmark(period: str = "2y") -> Optional[pd.DataFrame]:
    """
    Downloads SPY OHLCV data for use as a market benchmark in relative
    strength calculations. Called once before the main ticker loop.

    Returns a DataFrame with DatetimeIndex and a 'Close' column, or None
    if the download fails (in which case relative strength features will
    be skipped gracefully for all tickers).
    """
    try:
        spy = yf.download('SPY', period=period, interval='1d',
                          progress=False, auto_adjust=True)
        if spy.empty:
            logger.warning("SPY benchmark download returned empty. Relative strength features will be skipped.")
            return None
        if isinstance(spy.columns, pd.MultiIndex):
            spy.columns = spy.columns.get_level_values(0)
        spy = spy[['Close']].copy()
        spy['spy_ret_5d']  = spy['Close'].pct_change(5)
        spy['spy_ret_20d'] = spy['Close'].pct_change(20)
        logger.info(f"SPY benchmark downloaded: {len(spy)} rows ({spy.index[0].date()} to {spy.index[-1].date()})")
        return spy
    except Exception as e:
        logger.warning(f"SPY benchmark download failed: {e}. Relative strength features will be skipped.")
        return None


def get_target_tickers() -> List[str]:
    """
    Combines user portfolio/watchlist tickers with a randomly sampled
    cross-section of the market universe to prevent Mega-Cap bias.
    """
    logger.info("Extracting user portfolio and watchlist tickers...")
    try:
        engine       = DataEngine()
        user_tickers = engine.get_all_tickers()
    except Exception as e:
        logger.error(f"Failed to fetch user tickers from DataEngine: {e}")
        user_tickers = []

    logger.info("Dynamically sampling market universe to ensure balanced training distribution...")
    try:
        conn   = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT ticker FROM market_universe ORDER BY RANDOM() LIMIT 300")
        universe_sample = [row[0] for row in cursor.fetchall()]
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to sample from market_universe: {e}")
        universe_sample = []

    combined_set  = set(user_tickers).union(set(universe_sample))
    cleaned_list  = [t for t in combined_set if t and not t.startswith("0P")]
    final_tickers = sorted(cleaned_list)[:350]

    logger.info(f"Targeting {len(final_tickers)} unique tickers for historical backfill.")
    return final_tickers


def sync_ticker_metadata(tickers: List[str]) -> None:
    """
    Ensures sector metadata is available in ticker_metadata.
    Idempotent — only fetches missing tickers.
    """
    logger.info(f"Syncing metadata for {len(tickers)} tickers...")
    conn   = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ticker_metadata (
            ticker TEXT PRIMARY KEY, sector TEXT, beta REAL, market_cap REAL
        )
    """)

    cursor.execute("SELECT ticker FROM ticker_metadata")
    existing_tickers = {row[0] for row in cursor.fetchall()}
    missing_tickers  = [t for t in tickers if t not in existing_tickers]

    if not missing_tickers:
        logger.info("All ticker metadata is already up to date.")
        conn.close()
        return

    records: List[Tuple[str, str, float, float]] = []
    for ticker in missing_tickers:
        try:
            info   = yf.Ticker(ticker).info
            sector = info.get('sector', 'Unknown')
            beta   = info.get('beta', 1.0)
            mcap   = info.get('marketCap', 0.0)
            records.append((
                ticker, sector,
                float(beta) if beta else 1.0,
                float(mcap) if mcap else 0.0
            ))
        except Exception as e:
            logger.warning(f"Failed to fetch metadata for {ticker}: {e}")
            records.append((ticker, 'Unknown', 1.0, 0.0))
        time.sleep(0.1)

    if records:
        cursor.executemany("""
            INSERT OR REPLACE INTO ticker_metadata (ticker, sector, beta, market_cap)
            VALUES (?, ?, ?, ?)
        """, records)
        conn.commit()
        logger.info(f"Injected metadata for {len(records)} new tickers.")

    conn.close()


def run_historical_backfill() -> None:
    """
    Downloads 2 years of daily OHLCV data per ticker, computes all features
    (technical, momentum, volatility, relative strength), and upserts into
    quant_signals.

    RELATIVE STRENGTH STRATEGY:
    SPY is downloaded once before the main ticker loop and stored as a
    date-indexed Series of 5-day and 20-day returns. For each ticker, SPY
    returns are aligned to the ticker's date index via reindex(). Date
    mismatches (UK bank holidays vs US, ticker-specific halts) produce NaN
    which are removed by the unified dropna() call. No additional row loss
    beyond the existing 252-day momentum warmup constraint.
    """
    tickers = get_target_tickers()
    if not tickers:
        logger.warning("No tickers found to backfill. Aborting.")
        return

    sync_ticker_metadata(tickers)

    conn   = get_connection()
    cursor = conn.cursor()
    _migrate_quant_signals_schema(cursor)
    conn.commit()

    # Download SPY benchmark once before the main loop
    spy_df = _download_spy_benchmark(period="2y")

    log_notification("Info", f"ML Historical Backfill initiated for {len(tickers)} assets.")

    try:
        total_inserted = 0
        total_tickers  = len(tickers)

        for i, ticker in enumerate(tickers):
            logger.info(f"[{i+1}/{total_tickers}] Processing 2y historical data for {ticker}...")

            try:
                df = yf.download(
                    ticker, period="2y", interval="1d",
                    progress=False, auto_adjust=True
                )

                if df.empty:
                    continue

                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                df.dropna(subset=['Close', 'Volume', 'High', 'Low'], inplace=True)

                if len(df) < 252:
                    logger.warning(
                        f"Skipping {ticker}: insufficient data ({len(df)} rows < 252)."
                    )
                    continue

                # ── Technical Indicators ──────────────────────────────────────
                df['rsi_14']      = ta.momentum.RSIIndicator(
                    close=df['Close'], window=14
                ).rsi()

                macd_ind          = ta.trend.MACD(close=df['Close'])
                df['macd']        = macd_ind.macd()
                df['macd_signal'] = macd_ind.macd_signal()
                df['macd_hist']   = macd_ind.macd_diff()

                df['sma_50']     = ta.trend.SMAIndicator(
                    close=df['Close'], window=50
                ).sma_indicator()
                df['sma_200']    = ta.trend.SMAIndicator(
                    close=df['Close'], window=200
                ).sma_indicator()
                df['vol_sma_20'] = df['Volume'].rolling(window=20).mean()

                df['volume_surge']  = (
                    df['Volume'] > (df['vol_sma_20'] * 1.5)
                ).astype(int)
                df['bullish_cross'] = (
                    (df['macd'] > df['macd_signal']) &
                    (df['macd'].shift(1) <= df['macd_signal'].shift(1))
                ).astype(int)

                # ── Momentum Factors ──────────────────────────────────────────
                df['mom_1m']  = df['Close'].pct_change(21)
                df['mom_3m']  = df['Close'].pct_change(63)
                df['mom_6m']  = df['Close'].pct_change(126)
                df['mom_12m'] = df['Close'].pct_change(252)
                df['mom_12m_skip1m'] = df['mom_12m'] - df['mom_1m']
                df.drop(columns=['mom_12m'], inplace=True)

                # ── Volatility Regime Features ────────────────────────────────
                df['atr_raw'] = ta.volatility.AverageTrueRange(
                    high=df['High'], low=df['Low'], close=df['Close'], window=14
                ).average_true_range()
                df['atr_pct'] = df['atr_raw'] / df['Close']
                df.drop(columns=['atr_raw'], inplace=True)

                log_returns       = np.log(df['Close'] / df['Close'].shift(1))
                df['hist_vol_20'] = log_returns.rolling(window=20).std() * np.sqrt(252)

                # ── Relative Strength vs SPY ──────────────────────────────────
                # Compute ticker returns, then subtract aligned SPY returns.
                # reindex() aligns SPY dates to the ticker's date index —
                # any dates in SPY not present in the ticker (or vice versa)
                # produce NaN and are dropped by the unified dropna() below.
                if spy_df is not None:
                    ticker_ret_5d  = df['Close'].pct_change(5)
                    ticker_ret_20d = df['Close'].pct_change(20)

                    spy_ret_5d_aligned  = spy_df['spy_ret_5d'].reindex(df.index)
                    spy_ret_20d_aligned = spy_df['spy_ret_20d'].reindex(df.index)

                    df['rel_strength_5d']  = ticker_ret_5d  - spy_ret_5d_aligned
                    df['rel_strength_20d'] = ticker_ret_20d - spy_ret_20d_aligned
                else:
                    # SPY unavailable — store NULL so training SQL WHERE filter
                    # excludes these rows rather than silently training on zeros
                    df['rel_strength_5d']  = np.nan
                    df['rel_strength_20d'] = np.nan

                # ── Unified dropna ────────────────────────────────────────────
                df.dropna(inplace=True)
                if df.empty:
                    continue

                # ── Build upsert records ──────────────────────────────────────
                records: List[Tuple] = []
                for index, row in df.iterrows():
                    records.append((
                        ticker,
                        index.strftime('%Y-%m-%d'),
                        float(row['Close']),
                        int(row['Volume']),
                        float(row['rsi_14']),
                        float(row['macd']),
                        float(row['macd_signal']),
                        float(row['macd_hist']),
                        float(row['sma_50']),
                        float(row['sma_200']),
                        int(row['volume_surge']),
                        int(row['bullish_cross']),
                        float(row['mom_1m']),
                        float(row['mom_3m']),
                        float(row['mom_6m']),
                        float(row['mom_12m_skip1m']),
                        float(row['atr_pct']),
                        float(row['hist_vol_20']),
                        float(row['rel_strength_5d']),
                        float(row['rel_strength_20d']),
                    ))

                upsert_query = """
                    INSERT INTO quant_signals
                    (ticker, date, close_price, volume, rsi_14, macd, macd_signal,
                     macd_hist, sma_50, sma_200, volume_surge, bullish_cross,
                     mom_1m, mom_3m, mom_6m, mom_12m_skip1m,
                     atr_pct, hist_vol_20,
                     rel_strength_5d, rel_strength_20d)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ticker, date) DO UPDATE SET
                        close_price      = excluded.close_price,
                        volume           = excluded.volume,
                        rsi_14           = excluded.rsi_14,
                        macd             = excluded.macd,
                        macd_signal      = excluded.macd_signal,
                        macd_hist        = excluded.macd_hist,
                        sma_50           = excluded.sma_50,
                        sma_200          = excluded.sma_200,
                        volume_surge     = excluded.volume_surge,
                        bullish_cross    = excluded.bullish_cross,
                        mom_1m           = excluded.mom_1m,
                        mom_3m           = excluded.mom_3m,
                        mom_6m           = excluded.mom_6m,
                        mom_12m_skip1m   = excluded.mom_12m_skip1m,
                        atr_pct          = excluded.atr_pct,
                        hist_vol_20      = excluded.hist_vol_20,
                        rel_strength_5d  = excluded.rel_strength_5d,
                        rel_strength_20d = excluded.rel_strength_20d
                """
                cursor.executemany(upsert_query, records)
                conn.commit()
                total_inserted += cursor.rowcount

            except Exception as e:
                logger.error(f"Error processing ticker {ticker}: {e}")
                conn.rollback()
            finally:
                time.sleep(0.5)

            processed = i + 1
            if total_tickers >= 2 and processed == total_tickers // 2:
                log_notification(
                    "Info",
                    f"ML Historical Backfill is 50% complete ({processed}/{total_tickers})."
                )

        logger.info(
            f"--- BACKFILL COMPLETE. Injected/Updated {total_inserted} historical rows. ---"
        )
        log_notification(
            "Success",
            f"ML Backfill completed. Injected/Updated {total_inserted:,} data points."
        )

    except Exception as e:
        logger.error(f"Fatal error during historical backfill: {e}")
        log_notification("Error", f"ML Historical Backfill failed: {str(e)}")
    finally:
        conn.close()


def train_global_ml_model() -> None:
    """
    Builds an 18-feature ensemble model predicting >3% returns over 5 trading
    days using Anchored Walk-Forward Validation with Temporal Embargos.

    FEATURE SET (18 features):
        Technical:         rsi_14, macd_pct, macd_signal_pct, macd_hist_pct,
                           volume_surge, bullish_cross, dist_sma_50, dist_sma_200,
                           sector_code, dollar_vol_log
        Momentum:          mom_1m, mom_3m, mom_6m, mom_12m_skip1m
        Volatility:        atr_pct, hist_vol_20
        Relative Strength: rel_strength_5d, rel_strength_20d
    """
    logger.info("Initiating Global ML Model Training pipeline with Hyperparameter Optimization...")
    log_notification("Info", "Global ML Model Training pipeline initiated.")

    try:
        conn = get_connection()

        query = """
            SELECT qs.ticker, qs.date, qs.close_price, qs.volume,
                   qs.rsi_14, qs.macd, qs.macd_signal, qs.macd_hist,
                   qs.sma_50, qs.sma_200, qs.volume_surge, qs.bullish_cross,
                   qs.mom_1m, qs.mom_3m, qs.mom_6m, qs.mom_12m_skip1m,
                   qs.atr_pct, qs.hist_vol_20,
                   qs.rel_strength_5d, qs.rel_strength_20d,
                   tm.sector
            FROM quant_signals qs
            LEFT JOIN ticker_metadata tm ON qs.ticker = tm.ticker
            WHERE qs.mom_1m           IS NOT NULL
              AND qs.mom_12m_skip1m   IS NOT NULL
              AND qs.atr_pct          IS NOT NULL
              AND qs.hist_vol_20      IS NOT NULL
              AND qs.rel_strength_5d  IS NOT NULL
              AND qs.rel_strength_20d IS NOT NULL
            ORDER BY qs.date ASC
        """
        df = pd.read_sql_query(query, conn)
        conn.close()

        if df.empty:
            logger.warning("No data found in DB. Aborting ML training.")
            return

        logger.info(f"Extracting features from {len(df)} historical records...")

        # ── Feature Engineering ───────────────────────────────────────────────
        df['dist_sma_50']  = (df['close_price'] - df['sma_50'])  / df['sma_50']
        df['dist_sma_200'] = (df['close_price'] - df['sma_200']) / df['sma_200']

        df['macd_pct']        = df['macd']        / df['close_price']
        df['macd_signal_pct'] = df['macd_signal'] / df['close_price']
        df['macd_hist_pct']   = df['macd_hist']   / df['close_price']

        df['volume_surge']  = df['volume_surge'].fillna(0).astype(int)
        df['bullish_cross'] = df['bullish_cross'].fillna(0).astype(int)

        df['sector_code']    = df['sector'].map(SECTOR_MAP).fillna(99).astype(int)
        df['dollar_vol_log'] = np.log1p(df['close_price'] * df['volume'])

        # All stored features already in df from SQL

        df.replace([np.inf, -np.inf], np.nan, inplace=True)

        # ── Save training population statistics ───────────────────────────────
        logger.info("Computing and saving training population statistics...")
        feature_stats: Dict[str, Dict[str, float]] = {}
        for col in CONTINUOUS_FEATURES:
            col_data = df[col].dropna()
            feature_stats[col] = {
                'mean': float(col_data.mean()),
                'std':  float(col_data.std())
            }
            logger.info(
                f"  Feature '{col}': mean={feature_stats[col]['mean']:.4f}, "
                f"std={feature_stats[col]['std']:.4f}  (n={len(col_data):,})"
            )
        joblib.dump(feature_stats, FEATURE_STATS_PATH)
        logger.info(f"✅ Feature statistics saved to {FEATURE_STATS_PATH}")

        # ── Cross-sectional Z-scoring ─────────────────────────────────────────
        logger.info("Applying cross-sectional Z-scoring to normalize features...")
        for col in CONTINUOUS_FEATURES:
            df[f'{col}_z'] = df.groupby('date')[col].transform(cross_sectional_zscore)

        df.dropna(subset=FEATURE_COLS, inplace=True)

        # ── Target Construction ───────────────────────────────────────────────
        df['next_close']   = df.groupby('ticker')['close_price'].shift(-1)
        df['future_close'] = df.groupby('ticker')['close_price'].shift(-5)
        df.dropna(subset=['next_close', 'future_close'], inplace=True)

        df['target'] = (
            (df['future_close'] - df['next_close']) / df['next_close'] > 0.03
        ).astype(int)

        if len(df) < 1000:
            logger.warning(f"Insufficient training samples ({len(df)}).")
            return

        pos = (df['target'] == 1).sum()
        neg = (df['target'] == 0).sum()
        logger.info(
            f"Class distribution — Positive (1): {pos:,} ({pos/len(df):.1%}) | "
            f"Negative (0): {neg:,} ({neg/len(df):.1%})"
        )

        df.sort_values('date', inplace=True)
        df.reset_index(drop=True, inplace=True)

        X_full = df[FEATURE_COLS]
        y_full = df['target']

        neg_count_full        = (y_full == 0).sum()
        pos_count_full        = (y_full == 1).sum()
        scale_pos_weight_full = (
            neg_count_full / pos_count_full if pos_count_full > 0 else 1.0
        )

        # ── Walk-Forward CV Splits ────────────────────────────────────────────
        logger.info("Constructing Strict 5-Fold Walk-Forward Splits...")

        unique_dates = np.sort(df['date'].unique())
        date_series  = df['date'].reset_index(drop=True)

        cv_splits = []
        tscv = TimeSeriesSplit(n_splits=5)

        for train_date_idx, test_date_idx in tscv.split(unique_dates):
            if len(train_date_idx) > 5:
                train_dates = set(unique_dates[train_date_idx[:-5]])
                test_dates  = set(unique_dates[test_date_idx])
                train_idx   = date_series.index[date_series.isin(train_dates)].tolist()
                test_idx    = date_series.index[date_series.isin(test_dates)].tolist()
                if train_idx and test_idx:
                    cv_splits.append((train_idx, test_idx))

        # ── Hyperparameter Grids ──────────────────────────────────────────────
        rf_base = RandomForestClassifier(
            class_weight='balanced', random_state=42, n_jobs=-1
        )
        xgb_base = XGBClassifier(
            scale_pos_weight=scale_pos_weight_full,
            random_state=42, n_jobs=-1, eval_metric='logloss'
        )

        rf_param_dist = {
            'n_estimators':     [100, 150, 200, 250],
            'max_depth':        [4, 6, 8, 10],
            'min_samples_leaf': [1, 5, 10]
        }
        xgb_param_dist = {
            'n_estimators':    [100, 150, 200],
            'max_depth':       [3, 5, 7],
            'learning_rate':   [0.01, 0.05, 0.1],
            'subsample':       [0.7, 0.9, 1.0],
            'colsample_bytree': [0.7, 0.9, 1.0]
        }

        logger.info("Executing Randomized Search to find optimal structural boundaries...")

        rf_search = RandomizedSearchCV(
            estimator=rf_base, param_distributions=rf_param_dist,
            n_iter=10, cv=cv_splits, scoring='average_precision',
            random_state=42, n_jobs=-1
        )
        xgb_search = RandomizedSearchCV(
            estimator=xgb_base, param_distributions=xgb_param_dist,
            n_iter=10, cv=cv_splits, scoring='average_precision',
            random_state=42, n_jobs=-1
        )

        rf_search.fit(X_full, y_full)
        xgb_search.fit(X_full, y_full)

        best_rf  = rf_search.best_estimator_
        best_xgb = xgb_search.best_estimator_

        logger.info(f"Optimal RF Params Found:  {rf_search.best_params_}")
        logger.info(f"Optimal XGB Params Found: {xgb_search.best_params_}")

        avg_oos_pr_auc = (rf_search.best_score_ + xgb_search.best_score_) / 2.0
        logger.info(
            f"Averaged Optimized OOS Avg-Precision (PR-AUC): {avg_oos_pr_auc:.4f}  "
            f"(random baseline = {pos_count_full / len(y_full):.4f})"
        )

        # ── Production Ensemble Assembly ──────────────────────────────────────
        logger.info("Calibrating base estimators and assembling production Voting Classifier...")

        calibrated_rf  = CalibratedClassifierCV(
            estimator=best_rf,  method='isotonic', cv=cv_splits
        )
        calibrated_xgb = CalibratedClassifierCV(
            estimator=best_xgb, method='isotonic', cv=cv_splits
        )

        production_ensemble = VotingClassifier(
            estimators=[('rf', calibrated_rf), ('xgb', calibrated_xgb)],
            voting='soft'
        )
        production_ensemble.fit(X_full, y_full)

        joblib.dump(production_ensemble, MODEL_PATH)
        logger.info(f"✅ Production ML Ensemble saved to {MODEL_PATH}")
        log_notification(
            "Success",
            f"ML Model trained (PR-AUC: {avg_oos_pr_auc:.2%}, "
            f"baseline: {pos_count_full/len(y_full):.2%})."
        )

    except Exception as e:
        logger.error(f"Fatal error during ML training: {e}")
        log_notification("Error", f"ML Model Training failed: {str(e)}")


def update_daily_ml_predictions(tickers: List[str]) -> None:
    """
    Fetches the latest row for ALL tickers in the DB with complete feature
    data, computes cross-sectional z-scores across the full population, then
    writes confidence scores back for the requested ticker subset.

    Relative strength, volatility, and momentum features are read directly
    from stored DB values — no OHLCV or SPY lookback required at inference.

    Args:
        tickers: Tickers whose ml_confidence_score should be updated.
    """
    if not tickers:
        logger.warning("Empty ticker list. Skipping inference.")
        return

    if not MODEL_PATH.exists():
        logger.warning(f"Model not found at {MODEL_PATH}. Awaiting training cycle.")
        return

    logger.info(f"Initiating ML Inference for {len(tickers)} assets...")

    conn = None
    try:
        model = joblib.load(MODEL_PATH)
        conn  = get_connection()

        # Fetch ALL tickers at the latest date with complete feature data.
        # Z-scores are computed across this full population to replicate
        # the training cross-sectional methodology exactly.
        query = """
            SELECT qs.ticker, qs.date, qs.close_price, qs.volume,
                   qs.rsi_14, qs.macd, qs.macd_signal, qs.macd_hist,
                   qs.sma_50, qs.sma_200, qs.volume_surge, qs.bullish_cross,
                   qs.mom_1m, qs.mom_3m, qs.mom_6m, qs.mom_12m_skip1m,
                   qs.atr_pct, qs.hist_vol_20,
                   qs.rel_strength_5d, qs.rel_strength_20d,
                   tm.sector
            FROM quant_signals qs
            LEFT JOIN ticker_metadata tm ON qs.ticker = tm.ticker
            WHERE qs.date = (
                SELECT MAX(date) FROM quant_signals
                WHERE mom_1m           IS NOT NULL
                  AND atr_pct          IS NOT NULL
                  AND rel_strength_5d  IS NOT NULL
                  AND rel_strength_20d IS NOT NULL
            )
              AND qs.mom_1m           IS NOT NULL
              AND qs.atr_pct          IS NOT NULL
              AND qs.rel_strength_5d  IS NOT NULL
              AND qs.rel_strength_20d IS NOT NULL
        """
        df = pd.read_sql_query(query, conn)

        if df.empty:
            logger.warning(
                "No data with complete feature set found. Re-run backfill first."
            )
            conn.close()
            return

        logger.info(
            f"Loaded {len(df)} tickers for cross-sectional normalization "
            f"(date: {df['date'].iloc[0]})."
        )

        # ── Feature Engineering (mirrors training pipeline exactly) ───────────
        df['dist_sma_50']  = (df['close_price'] - df['sma_50'])  / df['sma_50']
        df['dist_sma_200'] = (df['close_price'] - df['sma_200']) / df['sma_200']

        df['macd_pct']        = df['macd']        / df['close_price']
        df['macd_signal_pct'] = df['macd_signal'] / df['close_price']
        df['macd_hist_pct']   = df['macd_hist']   / df['close_price']

        df['volume_surge']  = df['volume_surge'].fillna(0).astype(int)
        df['bullish_cross'] = df['bullish_cross'].fillna(0).astype(int)

        df['sector_code']    = df['sector'].map(SECTOR_MAP).fillna(99).astype(int)
        df['dollar_vol_log'] = np.log1p(df['close_price'] * df['volume'])

        df.replace([np.inf, -np.inf], np.nan, inplace=True)

        # ── Cross-sectional Z-scoring across full population ──────────────────
        logger.info(
            f"Applying cross-sectional Z-scoring across {len(df)} tickers..."
        )
        for col in CONTINUOUS_FEATURES:
            df[f'{col}_z'] = df.groupby('date')[col].transform(cross_sectional_zscore)

        # ── Score only requested tickers ──────────────────────────────────────
        target_set      = set(tickers)
        update_payloads = []

        for _, row in df.iterrows():
            if row['ticker'] not in target_set:
                continue
            if pd.isna(row[FEATURE_COLS]).any():
                continue

            X_infer             = pd.DataFrame([row[FEATURE_COLS]])
            prob                = model.predict_proba(X_infer)[0][1]
            ml_confidence_score = round(prob * 100.0, 2)

            update_payloads.append((ml_confidence_score, row['ticker'], row['date']))

        if update_payloads:
            cursor = conn.cursor()
            cursor.executemany("""
                UPDATE quant_signals
                SET ml_confidence_score = ?
                WHERE ticker = ? AND date = ?
            """, update_payloads)
            conn.commit()
            logger.info(f"✅ Executed ML predictions for {len(update_payloads)} assets.")
        else:
            logger.warning(
                "No valid payloads generated. Ensure tickers have been "
                "backfilled with relative strength data before running inference."
            )

    except Exception as e:
        logger.error(f"Fatal error during ML inference: {e}")
    finally:
        if conn:
            conn.close()