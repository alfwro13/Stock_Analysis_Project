# ai_prediction_engine.py
import time
import logging
import sqlite3
from pathlib import Path
from typing import List, Tuple, Dict

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
# FEATURE REGISTRY  (16 features, up from 14)
#
# MOMENTUM FACTORS (Jegadeesh & Titman, 1993):
#   mom_1m          — 21-day return.
#   mom_3m          — 63-day return.
#   mom_6m          — 126-day return.
#   mom_12m_skip1m  — 252-day return minus 21-day return.
#                     Skipping the last month removes the short-term reversal
#                     contamination identified by Jegadeesh & Titman.
#
# VOLATILITY REGIME FEATURES:
#   atr_pct         — 14-day Average True Range divided by close price.
#                     Normalises ATR to be comparable across price levels.
#                     Captures the current noise envelope of the stock:
#                     a stock with ATR 2% needs a much larger move to be
#                     "significant" than one with ATR 0.5%.
#                     A high atr_pct in the same cross-section as a positive
#                     momentum signal is the hallmark of a breakout setup.
#                     A high atr_pct with negative momentum signals a panic
#                     sell — not a buying opportunity.
#
#   hist_vol_20     — 20-day rolling standard deviation of log returns,
#                     annualised (× √252). Provides a longer-horizon view of
#                     realised volatility vs the single-day ATR.
#                     Together, atr_pct and hist_vol_20 give the model a
#                     two-speed volatility picture: short (14-day ATR) and
#                     medium (20-day HV).
#
# Both volatility features are stored in quant_signals during backfill
# (computed from the full OHLCV download before dropna) and read directly
# at training and inference — no lookback required at query time.
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
]


def cross_sectional_zscore(series: pd.Series) -> pd.Series:
    """Calculates Z-Score dynamically. Safe against 0 standard deviation."""
    std = series.std()
    if pd.isna(std) or std == 0:
        return series - series.mean()
    return (series - series.mean()) / std


def _migrate_quant_signals_schema(cursor: sqlite3.Cursor) -> None:
    """
    Idempotent schema migration. Adds momentum and volatility columns to
    quant_signals if they do not already exist. Safe to run on every backfill.
    """
    new_columns = [
        ("mom_1m",         "REAL"),
        ("mom_3m",         "REAL"),
        ("mom_6m",         "REAL"),
        ("mom_12m_skip1m", "REAL"),
        ("atr_pct",        "REAL"),
        ("hist_vol_20",    "REAL"),
    ]
    for col_name, col_type in new_columns:
        try:
            cursor.execute(
                f"ALTER TABLE quant_signals ADD COLUMN {col_name} {col_type}"
            )
            logger.info(f"Schema migration: added column '{col_name}' to quant_signals.")
        except sqlite3.OperationalError:
            pass  # Column already exists


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
    Ensures institutional structural data (sector) is available in
    ticker_metadata. Idempotent — only fetches missing tickers.
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
    Downloads 2 years of daily OHLCV data per ticker, computes all technical
    indicators, momentum factors, AND volatility regime features, then upserts
    everything into quant_signals.

    VOLATILITY COMPUTATION STRATEGY:
    ATR requires High and Low prices which are available in the yfinance
    download but not stored in quant_signals. Both atr_pct and hist_vol_20
    are computed HERE from raw OHLCV before dropna() and stored as columns,
    so training and inference can read them directly without OHLCV access.

    atr_pct   = AverageTrueRange(14) / Close          [noise envelope]
    hist_vol_20 = rolling(20).std(log_returns) * √252  [annualised realised vol]

    Row count: momentum's 252-day warmup remains the binding constraint.
    Adding 14-day ATR and 20-day HV introduces no additional row loss.
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

                df['sma_50']      = ta.trend.SMAIndicator(
                    close=df['Close'], window=50
                ).sma_indicator()
                df['sma_200']     = ta.trend.SMAIndicator(
                    close=df['Close'], window=200
                ).sma_indicator()
                df['vol_sma_20']  = df['Volume'].rolling(window=20).mean()

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
                # atr_pct: 14-day ATR normalised by close price.
                # High ATR = wide noise envelope. The model uses this to
                # distinguish a clean breakout (high momentum + moderate ATR)
                # from a panic move (high momentum + extreme ATR) which is far
                # less likely to continue in the signal direction.
                df['atr_raw'] = ta.volatility.AverageTrueRange(
                    high=df['High'],
                    low=df['Low'],
                    close=df['Close'],
                    window=14
                ).average_true_range()
                df['atr_pct'] = df['atr_raw'] / df['Close']
                df.drop(columns=['atr_raw'], inplace=True)

                # hist_vol_20: 20-day annualised realised volatility from
                # log returns. Complements ATR with a medium-horizon view.
                # Using log returns (vs simple returns) is standard for
                # volatility estimation as they are additive and better
                # approximate the normal distribution in the tails.
                log_returns       = np.log(df['Close'] / df['Close'].shift(1))
                df['hist_vol_20'] = log_returns.rolling(window=20).std() * np.sqrt(252)

                # ── Unified dropna ────────────────────────────────────────────
                # 252-day momentum remains the binding warmup constraint.
                # atr_pct (14-day) and hist_vol_20 (20-day) add no extra loss.
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
                    ))

                upsert_query = """
                    INSERT INTO quant_signals
                    (ticker, date, close_price, volume, rsi_14, macd, macd_signal,
                     macd_hist, sma_50, sma_200, volume_surge, bullish_cross,
                     mom_1m, mom_3m, mom_6m, mom_12m_skip1m,
                     atr_pct, hist_vol_20)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ticker, date) DO UPDATE SET
                        close_price     = excluded.close_price,
                        volume          = excluded.volume,
                        rsi_14          = excluded.rsi_14,
                        macd            = excluded.macd,
                        macd_signal     = excluded.macd_signal,
                        macd_hist       = excluded.macd_hist,
                        sma_50          = excluded.sma_50,
                        sma_200         = excluded.sma_200,
                        volume_surge    = excluded.volume_surge,
                        bullish_cross   = excluded.bullish_cross,
                        mom_1m          = excluded.mom_1m,
                        mom_3m          = excluded.mom_3m,
                        mom_6m          = excluded.mom_6m,
                        mom_12m_skip1m  = excluded.mom_12m_skip1m,
                        atr_pct         = excluded.atr_pct,
                        hist_vol_20     = excluded.hist_vol_20
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
    Builds a 16-feature ensemble model predicting >3% returns over 5 trading
    days using Anchored Walk-Forward Validation with Temporal Embargos.

    FEATURE SET (16 features):
        Technical:  rsi_14, macd_pct, macd_signal_pct, macd_hist_pct,
                    volume_surge, bullish_cross, dist_sma_50, dist_sma_200,
                    sector_code, dollar_vol_log
        Momentum:   mom_1m, mom_3m, mom_6m, mom_12m_skip1m
        Volatility: atr_pct, hist_vol_20
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
                   tm.sector
            FROM quant_signals qs
            LEFT JOIN ticker_metadata tm ON qs.ticker = tm.ticker
            WHERE qs.mom_1m          IS NOT NULL
              AND qs.mom_12m_skip1m  IS NOT NULL
              AND qs.atr_pct         IS NOT NULL
              AND qs.hist_vol_20     IS NOT NULL
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

        # atr_pct, hist_vol_20, and all momentum columns already in df from SQL

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
    Fetches the latest row for ALL tickers in the DB, computes cross-sectional
    z-scores across the full population, then writes confidence scores back for
    the requested ticker subset.

    Volatility and momentum features are read directly from stored DB values —
    no OHLCV lookback required at inference time.

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

        # Fetch ALL tickers at the latest date that has complete feature data.
        # Cross-sectional z-scores computed across this full population mirror
        # the training methodology exactly.
        query = """
            SELECT qs.ticker, qs.date, qs.close_price, qs.volume,
                   qs.rsi_14, qs.macd, qs.macd_signal, qs.macd_hist,
                   qs.sma_50, qs.sma_200, qs.volume_surge, qs.bullish_cross,
                   qs.mom_1m, qs.mom_3m, qs.mom_6m, qs.mom_12m_skip1m,
                   qs.atr_pct, qs.hist_vol_20,
                   tm.sector
            FROM quant_signals qs
            LEFT JOIN ticker_metadata tm ON qs.ticker = tm.ticker
            WHERE qs.date = (
                SELECT MAX(date) FROM quant_signals
                WHERE mom_1m IS NOT NULL
                  AND atr_pct IS NOT NULL
                  AND hist_vol_20 IS NOT NULL
            )
              AND qs.mom_1m      IS NOT NULL
              AND qs.atr_pct     IS NOT NULL
              AND qs.hist_vol_20 IS NOT NULL
        """
        df = pd.read_sql_query(query, conn)

        if df.empty:
            logger.warning(
                "No data with complete volatility features found. "
                "Re-run backfill first."
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

        # ── Cross-sectional Z-scoring ─────────────────────────────────────────
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
                "backfilled with volatility data before running inference."
            )

    except Exception as e:
        logger.error(f"Fatal error during ML inference: {e}")
    finally:
        if conn:
            conn.close()