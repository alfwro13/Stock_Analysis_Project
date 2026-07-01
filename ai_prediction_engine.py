# ai_prediction_engine.py
import time
import logging
import psutil
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Tuple, Dict, Optional, Any

import pandas as pd
import numpy as np
import joblib
from data_engine import load_or_fetch_daily_history
from yahoo_engine import yahoo_engine
from indicators import (
    compute_rsi,
    compute_macd,
    compute_smas,
    compute_atr,
    compute_volume_sma,
    compute_volume_surge,
    compute_bullish_cross,
)

from xgboost import XGBClassifier, XGBRegressor
from sklearn.ensemble import RandomForestClassifier, VotingClassifier
from sklearn.metrics import accuracy_score, classification_report, average_precision_score
from sklearn.base import clone
from sklearn.frozen import FrozenEstimator
from sklearn.model_selection import TimeSeriesSplit, RandomizedSearchCV
from sklearn.calibration import CalibratedClassifierCV

from config import BASE_DIR
from database import get_connection, log_notification
from data_engine import DataEngine
from constants import PREDICTION_HORIZON_DAYS, PREDICTION_RETURN_THRESHOLD

logger = logging.getLogger(__name__)

# GUI name: "Historical Data Backfill & Sync / Global Model Training (Walk-Forward) / Daily ML Inference". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.

MODELS_DIR = BASE_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
MODEL_PATH          = MODELS_DIR / "ml_ensemble.joblib"
FEATURE_STATS_PATH  = MODELS_DIR / "feature_stats.joblib"
QUANTILE_Q10_PATH   = MODELS_DIR / "quantile_q10.joblib"
QUANTILE_Q90_PATH   = MODELS_DIR / "quantile_q90.joblib"

# ─────────────────────────────────────────────────────────────────────────────
# FEATURE REGISTRY  (18 features — 6 fundamental features removed 2026-05-29)
#
# TECHNICAL FEATURES (10):
#   rsi_14, macd_pct, macd_signal_pct, macd_hist_pct, volume_surge,
#   bullish_cross, dist_sma_50, dist_sma_200, sector_code, dollar_vol_log
#
# MOMENTUM FACTORS (4) — Jegadeesh & Titman (1993):
#   mom_1m, mom_3m, mom_6m, mom_12m_skip1m
#
# VOLATILITY REGIME (2):
#   atr_pct, hist_vol_20
#
# RELATIVE STRENGTH VS SPY (2):
#   rel_strength_5d, rel_strength_20d
#
# FUNDAMENTAL FACTORS (6) — Fama-French value/quality/growth:
#   trailing_pe     — Valuation. Fama-French HML value factor proxy.
#                     Negative PE (loss-making) → NaN → median-imputed.
#                     Capped at 100 to prevent growth-stock outliers
#                     dominating the cross-sectional z-score.
#
#   price_to_book   — Value factor. Low P/B = deep value.
#                     Capped at 20 (covers Amazon/tech at peak multiples).
#
#   profit_margin   — Quality factor. Already a ratio [-1, 1].
#                     Negative margin = the company is burning cash.
#
#   roe             — Quality factor (Fama-French QMJ). Return on Equity.
#                     Capped at 2.0 to handle asset-light businesses with
#                     very high ROE (e.g. NVIDIA at 120% ROE → clipped to 2.0).
#
#   revenue_growth  — Growth factor. YoY revenue change as a decimal.
#                     Capped at 3.0 (300%) to remove post-merger spikes.
#
#   debt_to_equity  — Leverage/risk factor. Yahoo Finance returns this as
#                     a percentage (100 = 1.0x D/E ratio). Capped at 500
#                     (= 5.0x D/E) to handle financial stocks and REITs
#                     whose leverage is structural not distress-driven.
#
# KNOWN LIMITATION — POINT-IN-TIME BIAS:
#   stock_signals stores only the most recent fundamental snapshot (ticker
#   is the PRIMARY KEY). When this snapshot is joined to historical
#   quant_signals rows during training, it applies today's fundamentals
#   to rows from 18 months ago — mild lookahead bias.
#   Acceptable for a hobbyist project where fundamentals change slowly
#   (margins, ROE) or the signal direction is stable (high-PE stocks tend
#   to remain high-PE within a 2-year window). Documented here for
#   transparency. A production system would store time-stamped fundamental
#   snapshots and join on the closest prior date.
#
# NULL HANDLING — CROSS-SECTIONAL MEDIAN IMPUTATION:
#   ETFs, futures, and stocks without reported fundamentals return NULL
#   from yfinance. Rather than dropping these tickers entirely (which would
#   bias the universe toward pure equities), NULLs are filled with the
#   cross-sectional median for that date before z-scoring. These stocks
#   receive a neutral z-score of ~0.0 — they are neither rewarded nor
#   penalised for absent fundamentals.
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
    #
    # FUNDAMENTAL FACTORS REMOVED — 2026-05-29 (audit item 2a)
    # A/B diagnostic (debug_scripts/ab_fundamentals_diagnostic.py) ran 5 seeds
    # comparing 24-feature vs 18-feature model on 250k rows / 1,060 tickers:
    #
    #   WITH    fundamentals (24 feats): mean PR-AUC 0.4015  lift +0.0746
    #   WITHOUT fundamentals (18 feats): mean PR-AUC 0.4047  lift +0.0778
    #   Delta: −0.0032 ± 0.0043  SNR 0.7×  (18-feat arm won all 5 seeds)
    #
    # Delta is statistically indistinguishable from zero (noise > signal).
    # The 18-feature model never scored lower across any seed.
    # Removing them closes the documented point-in-time lookahead bias
    # (stock_signals stores only the latest snapshot; joining it to
    # historical rows applies today's fundamentals to 18-month-old data).
    #
    # 'trailing_pe_z', 'price_to_book_z', 'profit_margin_z',
    # 'roe_z', 'revenue_growth_z', 'debt_to_equity_z',
]

SECTOR_MAP = {
    "Technology": 1, "Healthcare": 2, "Financials": 3,
    "Financial Services": 3, "Real Estate": 4, "Energy": 5,
    "Basic Materials": 6, "Consumer Cyclical": 7, "Industrials": 8,
    "Utilities": 9, "Consumer Defensive": 10, "Communication Services": 11,
    "Broad Market ETF": 12, "ETF": 12, "Futures": 13,
    "Unknown": 99
}

# All continuous features that receive cross-sectional z-scoring.
# Fundamental features are included here — winsorization is applied
# before z-scoring in the feature engineering block.
CONTINUOUS_FEATURES = [
    'rsi_14', 'macd_pct', 'macd_signal_pct', 'macd_hist_pct',
    'dist_sma_50', 'dist_sma_200', 'dollar_vol_log',
    'mom_1m', 'mom_3m', 'mom_6m', 'mom_12m_skip1m',
    'atr_pct', 'hist_vol_20',
    'rel_strength_5d', 'rel_strength_20d',
    'trailing_pe', 'price_to_book', 'profit_margin',
    'roe', 'revenue_growth', 'debt_to_equity',
]

# Fundamental features requiring winsorization + median imputation
FUNDAMENTAL_FEATURES = [
    'trailing_pe', 'price_to_book', 'profit_margin',
    'roe', 'revenue_growth', 'debt_to_equity',
]

# Winsorization bounds per fundamental feature.
# (lower_bound, upper_bound) — None means no bound on that side.
FUNDAMENTAL_BOUNDS: Dict[str, Tuple[Optional[float], Optional[float]]] = {
    'trailing_pe':    (0.0,   300.0),   # raised: p95=265, was clipping 15%
    'price_to_book':  (-20.0, 80.0),    # raised cap: p95=65, was clipping 27%
                                         # floor lowered: negative P/B is valid data
    'profit_margin':  (-1.0,  1.0),     # correct — no change
    'roe':            (-1.0,  1.5),     # correct — no change
    'revenue_growth': (-1.0,  5.0),     # raised: p95=2.04, minor headroom added
    'debt_to_equity': (0.0,   500.0),   # correct — no change
}


def cross_sectional_zscore(series: pd.Series) -> pd.Series:
    """Calculates Z-Score dynamically. Safe against 0 standard deviation."""
    std = series.std()
    if pd.isna(std) or std == 0:
        return series - series.mean()
    return (series - series.mean()) / std


def _winsorize_and_impute_fundamentals(df: pd.DataFrame) -> pd.DataFrame:
    """
    Applies per-feature winsorization and cross-sectional median imputation
    to the fundamental feature columns.

    Winsorization: clips values to [lower, upper] bounds defined in
    FUNDAMENTAL_BOUNDS. For trailing_pe, negative values are set to NaN
    before clipping because a negative PE (loss-making company) is a
    qualitatively different state from a cheap company — it should not be
    treated as "very low PE" and should receive the neutral median instead.

    Imputation: fills NaN values (including original NULLs from ETFs/futures
    and the negative-PE NaNs just created) with the cross-sectional median
    for that date. This gives non-equity assets a neutral fundamental profile
    rather than dropping them from the universe.

    Args:
        df: DataFrame containing raw fundamental columns and a 'date' column.

    Returns:
        DataFrame with fundamentals winsorized and NULLs imputed.
    """
    # trailing_pe: negative values are loss-making companies, not cheap stocks
    if 'trailing_pe' in df.columns:
        df['trailing_pe'] = df['trailing_pe'].where(df['trailing_pe'] > 0)

    for col, (lo, hi) in FUNDAMENTAL_BOUNDS.items():
        if col not in df.columns:
            continue
        df[col] = df[col].clip(lower=lo, upper=hi)

    # Cross-sectional median imputation per date
    for col in FUNDAMENTAL_FEATURES:
        if col not in df.columns:
            continue
        null_count = df[col].isna().sum()
        if null_count > 0:
            df[col] = df.groupby('date')[col].transform(
                lambda x: x.fillna(x.median())
            )

    return df



def _download_spy_benchmark() -> Optional[pd.DataFrame]:
    """
    Loads SPY OHLCV data for relative strength calculations.
    Called once before the main ticker loop. Returns None on failure.
    """
    try:
        spy = load_or_fetch_daily_history("SPY")
        if spy is None:
            spy = pd.DataFrame()
        if spy.empty:
            logger.warning("SPY download returned empty. Relative strength will be skipped.")
            return None
        spy = spy[['Close']].copy()
        spy['spy_ret_5d']  = spy['Close'].pct_change(5)
        spy['spy_ret_20d'] = spy['Close'].pct_change(20)
        logger.info(
            f"SPY benchmark downloaded: {len(spy)} rows "
            f"({spy.index[0].date()} to {spy.index[-1].date()})"
        )
        return spy
    except Exception as e:
        logger.warning(f"SPY download failed: {e}. Relative strength will be skipped.")
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
        cursor.execute("SELECT ticker FROM market_universe ORDER BY RANDOM() LIMIT 500")
        universe_sample = [row[0] for row in cursor.fetchall()]
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to sample from market_universe: {e}")
        universe_sample = []

    combined_set  = set(user_tickers).union(set(universe_sample))
    cleaned_list  = [t for t in combined_set if t and not t.startswith("0P")]
    final_tickers = sorted(cleaned_list)[:600]

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
            info   = yahoo_engine.get_ticker_info(ticker) or {}
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


def run_historical_backfill(tickers: Optional[List[str]] = None) -> None:
    """Fundamental features are NOT stored in quant_signals — joined from stock_signals at training/inference time."""
    if tickers is None:
        tickers = get_target_tickers()
    if not tickers:
        logger.warning("No tickers found to backfill. Aborting.")
        return

    sync_ticker_metadata(tickers)

    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
    start_idx = 0

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT last_processed_ticker FROM quant_scan_states "
            "WHERE scan_type = 'ml_backfill' AND status = 'IN_PROGRESS' "
            "ORDER BY scan_date DESC LIMIT 1"
        )
        state = cursor.fetchone()

        if state:
            last_ticker = state['last_processed_ticker']
            if last_ticker and last_ticker in tickers:
                start_idx = tickers.index(last_ticker) + 1
                resume_ticker = tickers[start_idx] if start_idx < len(tickers) else 'END'
                logger.info("Resuming ML Backfill from %s (skipping %d already-processed tickers).", resume_ticker, start_idx)
                log_notification("Info", f"Resuming ML Historical Backfill from {resume_ticker}.")
        else:
            cursor.execute(
                "INSERT INTO quant_scan_states (scan_date, scan_type, last_processed_ticker, status) VALUES (?, ?, ?, ?)",
                (today_str, 'ml_backfill', '', 'IN_PROGRESS')
            )
            conn.commit()

        spy_df = _download_spy_benchmark()

        log_notification("Info", f"ML Historical Backfill initiated for {len(tickers)} assets.")

        total_inserted = 0
        total_tickers  = len(tickers)

        for i in range(start_idx, total_tickers):
            ticker = tickers[i]
            logger.info("[%d/%d] Processing 2y historical data for %s...", i + 1, total_tickers, ticker)

            try:
                df = load_or_fetch_daily_history(ticker)
                if df is None:
                    df = pd.DataFrame()

                if df.empty:
                    continue

                df.dropna(subset=['Close', 'Volume', 'High', 'Low'], inplace=True)

                if len(df) < 252:
                    logger.warning("Skipping %s: insufficient data (%d rows < 252).", ticker, len(df))
                    continue

                df['rsi_14'] = compute_rsi(df['Close'])
                df['macd'], df['macd_signal'], df['macd_hist'] = compute_macd(df['Close'])
                _smas = compute_smas(df['Close'], [50, 200])
                df['sma_50']  = _smas[50]
                df['sma_200'] = _smas[200]
                df['vol_sma_20']    = compute_volume_sma(df['Volume'])
                df['volume_surge']  = compute_volume_surge(df['Volume'], df['vol_sma_20'])
                df['bullish_cross'] = compute_bullish_cross(df['macd'], df['macd_signal'])

                df['mom_1m']  = df['Close'].pct_change(21)
                df['mom_3m']  = df['Close'].pct_change(63)
                df['mom_6m']  = df['Close'].pct_change(126)
                df['mom_12m'] = df['Close'].pct_change(252)
                df['mom_12m_skip1m'] = df['mom_12m'] - df['mom_1m']
                df.drop(columns=['mom_12m'], inplace=True)

                df['atr_raw'] = compute_atr(df['High'], df['Low'], df['Close'])
                df['atr_pct'] = df['atr_raw'] / df['Close']
                df.drop(columns=['atr_raw'], inplace=True)
                log_returns       = np.log(df['Close'] / df['Close'].shift(1))
                df['hist_vol_20'] = log_returns.rolling(window=20).std() * np.sqrt(252)

                if spy_df is not None:
                    ticker_ret_5d  = df['Close'].pct_change(5)
                    ticker_ret_20d = df['Close'].pct_change(20)
                    df['rel_strength_5d']  = (
                        ticker_ret_5d  - spy_df['spy_ret_5d'].reindex(df.index)
                    )
                    df['rel_strength_20d'] = (
                        ticker_ret_20d - spy_df['spy_ret_20d'].reindex(df.index)
                    )
                else:
                    df['rel_strength_5d']  = np.nan
                    df['rel_strength_20d'] = np.nan

                df.dropna(inplace=True)
                if df.empty:
                    continue

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
                     atr_pct, hist_vol_20, rel_strength_5d, rel_strength_20d)
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

                cursor.execute(
                    "UPDATE quant_scan_states SET last_processed_ticker = ? WHERE scan_type = 'ml_backfill' AND status = 'IN_PROGRESS'",
                    (ticker,)
                )
                conn.commit()

            except Exception as e:
                logger.error("Error processing ticker %s: %s", ticker, e)
                conn.rollback()
            finally:
                time.sleep(0.5)

            processed = i + 1
            if total_tickers >= 4 and processed % max(1, total_tickers // 4) == 0 and processed < total_tickers:
                pct = int((processed / total_tickers) * 100)
                log_notification("Info", f"ML Historical Backfill Progress: {pct}% ({processed}/{total_tickers} tickers processed).")

        cursor.execute(
            "UPDATE quant_scan_states SET status = 'COMPLETED' WHERE scan_type = 'ml_backfill' AND status = 'IN_PROGRESS'"
        )
        conn.commit()

        logger.info("ML Backfill complete. Injected/updated %d historical rows.", total_inserted)
        log_notification("Success", f"ML Backfill completed. Injected/Updated {total_inserted:,} data points.")

    except Exception as e:
        logger.error("Fatal error during historical backfill: %s", e)
        log_notification("Error", f"ML Historical Backfill failed: {str(e)}")
    finally:
        if conn:
            conn.close()


def train_global_ml_model() -> None:
    """
    Builds an 18-feature ensemble model predicting returns exceeding
    PREDICTION_RETURN_THRESHOLD over PREDICTION_HORIZON_DAYS trading days,
    using Anchored Walk-Forward Validation with Temporal Embargos.

    FEATURE SET (18 features — see FEATURE REGISTRY block for history):
        Technical:         rsi_14, macd_pct, macd_signal_pct, macd_hist_pct,
                           volume_surge, bullish_cross, dist_sma_50, dist_sma_200,
                           sector_code, dollar_vol_log
        Momentum:          mom_1m, mom_3m, mom_6m, mom_12m_skip1m
        Volatility:        atr_pct, hist_vol_20
        Relative Strength: rel_strength_5d, rel_strength_20d

    Fundamental columns are still joined and preprocessed for feature drift
    diagnostics but are excluded from FEATURE_COLS and do not influence
    predictions. See the A/B test note in the FEATURE REGISTRY block above.
    """
    logger.info("Initiating Global ML Model Training pipeline with Hyperparameter Optimization...")

    # Memory guard — training requires ~1.5 GB free; abort or throttle rather than OOM-crashing the server
    _mem = psutil.virtual_memory()
    _avail_gb = _mem.available / (1024 ** 3)
    _ABORT_GB = 0.75
    _THROTTLE_GB = 1.5
    if _avail_gb < _ABORT_GB:
        msg = (
            f"ML Training aborted: only {_avail_gb:.1f} GB RAM available "
            f"(minimum {_ABORT_GB} GB required). Free memory and retry."
        )
        logger.error(msg)
        log_notification("Error", msg)
        return
    _n_iter = 4 if _avail_gb < _THROTTLE_GB else 10
    if _avail_gb < _THROTTLE_GB:
        logger.warning(
            f"Low memory ({_avail_gb:.1f} GB available) — throttling search to {_n_iter} iterations."
        )

    log_notification("Info", "Global ML Model Training pipeline initiated.")

    try:
        conn = get_connection()

        # LEFT JOIN stock_signals to pull fundamental snapshot per ticker.
        # stock_signals has ticker as PRIMARY KEY (one row per ticker).
        # Rows for tickers with no stock_signals entry get NULL fundamentals
        # which are handled by _winsorize_and_impute_fundamentals().
        query = """
            SELECT qs.ticker, qs.date, qs.close_price, qs.volume,
                   qs.rsi_14, qs.macd, qs.macd_signal, qs.macd_hist,
                   qs.sma_50, qs.sma_200, qs.volume_surge, qs.bullish_cross,
                   qs.mom_1m, qs.mom_3m, qs.mom_6m, qs.mom_12m_skip1m,
                   qs.atr_pct, qs.hist_vol_20,
                   qs.rel_strength_5d, qs.rel_strength_20d,
                   ss.trailing_pe, ss.price_to_book, ss.profit_margin,
                   ss.roe, ss.revenue_growth, ss.debt_to_equity,
                   tm.sector
            FROM quant_signals qs
            LEFT JOIN stock_signals  ss ON qs.ticker = ss.ticker
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

        # ── Fundamental preprocessing ─────────────────────────────────────────
        # Winsorize then impute NULLs with cross-sectional median per date.
        # Must happen BEFORE z-scoring so outliers don't compress valid signals.
        logger.info("Winsorizing and imputing fundamental features...")
        df = _winsorize_and_impute_fundamentals(df)

        # Log null counts after imputation for diagnostics
        for col in FUNDAMENTAL_FEATURES:
            remaining_nulls = df[col].isna().sum()
            if remaining_nulls > 0:
                logger.warning(
                    f"  {col}: {remaining_nulls} NULLs remain after imputation "
                    f"(dates with zero equity coverage — will be dropped by dropna)."
                )

        df.replace([np.inf, -np.inf], np.nan, inplace=True)

        # ── Save training population statistics ───────────────────────────────
        # Consumed by the inference coverage guard (Change A) and the
        # train-vs-serve drift diagnostic (Change B) in update_daily_ml_predictions.
        logger.info("Computing and saving training population statistics...")
        train_universe_size = int(df.groupby('date')['ticker'].size().median())
        feature_stats: Dict[str, Any] = {
            '_meta': {'train_universe_size': train_universe_size},
            'features': {},
        }
        for col in CONTINUOUS_FEATURES:
            col_data = df[col].dropna()
            feature_stats['features'][col] = {
                'mean': float(col_data.mean()),
                'std':  float(col_data.std())
            }
            logger.info(
                f"  Feature '{col}': mean={feature_stats['features'][col]['mean']:.4f}, "
                f"std={feature_stats['features'][col]['std']:.4f}  (n={len(col_data):,})"
            )
        logger.info(f"  Training universe size (median tickers/date): {train_universe_size:,}")
        joblib.dump(feature_stats, FEATURE_STATS_PATH)
        logger.info(f"✅ Feature statistics saved to {FEATURE_STATS_PATH}")

        # ── Cross-sectional Z-scoring ─────────────────────────────────────────
        logger.info("Applying cross-sectional Z-scoring to normalize features...")
        for col in CONTINUOUS_FEATURES:
            df[f'{col}_z'] = df.groupby('date')[col].transform(cross_sectional_zscore)

        df.dropna(subset=FEATURE_COLS, inplace=True)

        # ── Target Construction ───────────────────────────────────────────────
        # AFTER — 10-day horizon
        # Signal-to-noise improves at 10 days vs 5 days: technical momentum
        # has more time to play out before mean-reversion noise dominates.
        # Entry proxy remains close[T+1]. Exit moves to close[T+10].
        df['next_close']   = df.groupby('ticker')['close_price'].shift(-1)
        df['future_close'] = df.groupby('ticker')['close_price'].shift(-PREDICTION_HORIZON_DAYS)
        df.dropna(subset=['next_close', 'future_close'], inplace=True)

        df['target'] = (
            (df['future_close'] - df['next_close']) / df['next_close'] > PREDICTION_RETURN_THRESHOLD
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

        # ── Three-Way Temporal Split ──────────────────────────────────────────
        # Fixes BUG-04: calibration data leakage.
        #
        # The old approach used the same cv_splits for both hyperparameter
        # search and calibration. The hyperparameters were selected to maximise
        # PR-AUC on those exact folds, so the calibration was fitting isotonic
        # regression on optimistically biased out-of-fold predictions.
        #
        # The correct approach uses three non-overlapping temporal regions:
        #   Train  (60%): hyperparameter search via walk-forward CV
        #   Calib  (20%): isotonic calibration — never seen during tuning
        #   Test   (20%): true OOS evaluation — never touched during training
        #
        # The production model is then refitted on Train + Calib (80%) with
        # the best hyperparameters found on Train only, and calibrated again
        # on Calib only. The Test set is used solely for honest reporting.
        unique_dates = np.sort(df['date'].unique())
        date_series  = df['date'].reset_index(drop=True)
        n_dates      = len(unique_dates)

        train_end = int(n_dates * 0.60)
        calib_end = int(n_dates * 0.80)

        # Purge the last PREDICTION_HORIZON_DAYS dates from each partition so
        # their forward labels cannot bleed into the next region — same embargo
        # concept applied to the outer split that already exists inside CV folds.
        train_dates = set(unique_dates[:train_end - PREDICTION_HORIZON_DAYS])
        calib_dates = set(unique_dates[train_end:calib_end - PREDICTION_HORIZON_DAYS])
        test_dates  = set(unique_dates[calib_end:])

        train_idx = date_series.index[date_series.isin(train_dates)].tolist()
        calib_idx = date_series.index[date_series.isin(calib_dates)].tolist()
        test_idx  = date_series.index[date_series.isin(test_dates)].tolist()

        X_train = X_full.iloc[train_idx]
        y_train = y_full.iloc[train_idx]
        X_calib = X_full.iloc[calib_idx]
        y_calib = y_full.iloc[calib_idx]
        X_test  = X_full.iloc[test_idx]
        y_test  = y_full.iloc[test_idx]

        logger.info(
            f"Temporal split — Train: {len(X_train):,} rows "
            f"({unique_dates[0]} → {unique_dates[train_end - PREDICTION_HORIZON_DAYS - 1]})  |  "
            f"Calib: {len(X_calib):,} rows "
            f"({unique_dates[train_end]} → {unique_dates[calib_end - PREDICTION_HORIZON_DAYS - 1]})  |  "
            f"Test: {len(X_test):,} rows ({unique_dates[calib_end]} → {unique_dates[-1]})"
        )

        # scale_pos_weight derived from train set only to prevent test leakage
        neg_count_train        = (y_train == 0).sum()
        pos_count_train        = (y_train == 1).sum()
        scale_pos_weight_train = (
            neg_count_train / pos_count_train if pos_count_train > 0 else 1.0
        )
        # ── Walk-Forward CV Splits (Train region only) ────────────────────────
        logger.info("Constructing Strict 5-Fold Walk-Forward Splits on training region...")

        train_unique_dates = np.sort(df.iloc[train_idx]['date'].unique())
        train_date_series  = df.iloc[train_idx]['date'].reset_index(drop=True)

        cv_splits_train = []
        for tr_date_idx, te_date_idx in TimeSeriesSplit(n_splits=5).split(train_unique_dates):
            if len(tr_date_idx) > PREDICTION_HORIZON_DAYS:
                tr_dates = set(train_unique_dates[tr_date_idx[:-PREDICTION_HORIZON_DAYS]])
                te_dates = set(train_unique_dates[te_date_idx])
                tr_idx   = train_date_series.index[train_date_series.isin(tr_dates)].tolist()
                te_idx   = train_date_series.index[train_date_series.isin(te_dates)].tolist()
                if tr_idx and te_idx:
                    cv_splits_train.append((tr_idx, te_idx))

        # ── Hyperparameter Search (Train region only) ─────────────────────────
        rf_base = RandomForestClassifier(
            class_weight='balanced', random_state=42, n_jobs=1
        )
        xgb_base = XGBClassifier(
            scale_pos_weight=scale_pos_weight_train,
            random_state=42, n_jobs=1, eval_metric='logloss'
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

        logger.info("Executing Randomized Search on training region only...")

        rf_search = RandomizedSearchCV(
            estimator=rf_base, param_distributions=rf_param_dist,
            n_iter=_n_iter, cv=cv_splits_train, scoring='average_precision',
            random_state=42, n_jobs=1
        )
        xgb_search = RandomizedSearchCV(
            estimator=xgb_base, param_distributions=xgb_param_dist,
            n_iter=_n_iter, cv=cv_splits_train, scoring='average_precision',
            random_state=42, n_jobs=1
        )

        rf_search.fit(X_train, y_train)
        xgb_search.fit(X_train, y_train)

        best_rf  = rf_search.best_estimator_
        best_xgb = xgb_search.best_estimator_

        logger.info(f"Optimal RF Params Found:  {rf_search.best_params_}")
        logger.info(f"Optimal XGB Params Found: {xgb_search.best_params_}")

        # CV score on train region only — informational, not the headline metric
        train_cv_pr_auc = (rf_search.best_score_ + xgb_search.best_score_) / 2.0
        logger.info(f"Train-region CV Avg-Precision: {train_cv_pr_auc:.4f}")

        # ── Calibration on held-out Calib region ─────────────────────────────
        # cv='prefit' tells sklearn the estimator is already fitted.
        # Isotonic regression is fitted on Calib predictions only —
        # these rows were never seen during hyperparameter search.
        logger.info("Calibrating on held-out calibration region (never seen during tuning)...")

        calibrated_rf  = CalibratedClassifierCV(
            estimator=FrozenEstimator(best_rf),  method='isotonic'
        )
        calibrated_xgb = CalibratedClassifierCV(
            estimator=FrozenEstimator(best_xgb), method='isotonic'
        )
        calibrated_rf.fit(X_calib,  y_calib)
        calibrated_xgb.fit(X_calib, y_calib)

        # ── True OOS Evaluation on Test region ───────────────────────────────
        # Test region was never touched during training or calibration.
        # This is the only honest PR-AUC number.
        ensemble_probs_test = (
            calibrated_rf.predict_proba(X_test)[:, 1] +
            calibrated_xgb.predict_proba(X_test)[:, 1]
        ) / 2.0
        true_oos_pr_auc  = average_precision_score(y_test, ensemble_probs_test)
        true_oos_baseline = float(y_test.mean())
        logger.info(
            f"✅ True OOS PR-AUC (clean holdout): {true_oos_pr_auc:.4f}  "
            f"(random baseline = {true_oos_baseline:.4f})"
        )

        # ── Production Model: refit on Train + Calib (80% of data) ───────────
        # Best hyperparameters are fixed. We now refit on more data (Train +
        # Calib) to give the production model as much signal as possible,
        # then recalibrate on Calib again (cv='prefit').
        logger.info("Refitting production model on Train + Calib region (80% of data)...")

        X_prod = pd.concat([X_train, X_calib])
        y_prod = pd.concat([y_train, y_calib])

        final_rf  = clone(best_rf).fit(X_prod, y_prod)
        final_xgb = clone(best_xgb).fit(X_prod, y_prod)

        final_calibrated_rf  = CalibratedClassifierCV(
            estimator=FrozenEstimator(final_rf),  method='isotonic'
        )
        final_calibrated_xgb = CalibratedClassifierCV(
            estimator=FrozenEstimator(final_xgb), method='isotonic'
        )
        final_calibrated_rf.fit(X_calib,  y_calib)
        final_calibrated_xgb.fit(X_calib, y_calib)

        production_ensemble = VotingClassifier(
            estimators=[('rf', final_calibrated_rf), ('xgb', final_calibrated_xgb)],
            voting='soft'
        )
        # Estimators are already fitted — manually mark VotingClassifier as fitted
        production_ensemble.estimators_ = [final_calibrated_rf, final_calibrated_xgb]
        production_ensemble.classes_    = np.array([0, 1])
        # n_features_in_ is auto-derived from estimators_ in sklearn 1.6+

        joblib.dump(production_ensemble, MODEL_PATH)
        logger.info(f"✅ Production ML Ensemble saved to {MODEL_PATH}")
        log_notification(
            "Success",
            f"ML Model trained — True OOS PR-AUC: {true_oos_pr_auc:.2%}  "
            f"(baseline: {true_oos_baseline:.2%})."
        )

    except Exception as e:
        logger.error(f"Fatal error during ML training: {e}")
        log_notification("Error", f"ML Model Training failed: {str(e)}")


def update_daily_ml_predictions(tickers: List[str]) -> None:
    """
    Fetches the latest row for ALL tickers in the DB with complete feature
    data, applies fundamental preprocessing, computes cross-sectional
    z-scores across the full population, then writes confidence scores back
    for the requested ticker subset.

    Design notes:
    - Z-scoring is intentionally **per-date cross-sectional**: each feature is
      normalised relative to every other ticker on that same date, matching
      exactly how the model was trained.  Do NOT replace with fixed/global
      scaling parameters — that would break the model's design.
    - The **full stored universe** for the latest date is the normalisation
      population at both train time and serve time.  The SQL query fetches all
      tickers unconditionally; the `tickers` argument is never used to narrow
      the cross-section.
    - The `tickers` argument filters **writes only** — only the requested
      tickers receive an updated ml_confidence_score in the database.
    - A coverage guard refuses to score when the universe for the latest date
      is too small for reliable z-scores (e.g. after a partial scan that left
      only a handful of rows).
    - A drift diagnostic compares today's raw feature distributions against the
      saved training statistics and logs a warning when divergence is large.
      It never alters any predictions.

    Fundamental features are joined from stock_signals (latest snapshot per
    ticker) — identical join to training, consistent with the known
    point-in-time bias documented in train_global_ml_model().

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

        query = """
            SELECT qs.ticker, qs.date, qs.close_price, qs.volume,
                   qs.rsi_14, qs.macd, qs.macd_signal, qs.macd_hist,
                   qs.sma_50, qs.sma_200, qs.volume_surge, qs.bullish_cross,
                   qs.mom_1m, qs.mom_3m, qs.mom_6m, qs.mom_12m_skip1m,
                   qs.atr_pct, qs.hist_vol_20,
                   qs.rel_strength_5d, qs.rel_strength_20d,
                   ss.trailing_pe, ss.price_to_book, ss.profit_margin,
                   ss.roe, ss.revenue_growth, ss.debt_to_equity,
                   tm.sector
            FROM quant_signals qs
            LEFT JOIN stock_signals   ss ON qs.ticker = ss.ticker
            LEFT JOIN ticker_metadata tm ON qs.ticker = tm.ticker
            WHERE qs.mom_1m           IS NOT NULL
              AND qs.atr_pct          IS NOT NULL
              AND qs.rel_strength_5d  IS NOT NULL
              AND qs.rel_strength_20d IS NOT NULL
              AND qs.date = (
                  SELECT MAX(qs2.date) FROM quant_signals qs2
                  WHERE qs2.ticker          = qs.ticker
                    AND qs2.mom_1m          IS NOT NULL
                    AND qs2.atr_pct         IS NOT NULL
                    AND qs2.rel_strength_5d  IS NOT NULL
                    AND qs2.rel_strength_20d IS NOT NULL
              )
        """
        df = pd.read_sql_query(query, conn)

        if df.empty:
            logger.warning("No data with complete feature set found. Re-run backfill first.")
            conn.close()
            return

        # ── Load saved stats (shared by Change A and Change B) ────────────────
        saved_stats: Optional[Dict[str, Any]] = None
        try:
            if FEATURE_STATS_PATH.exists():
                saved_stats = joblib.load(FEATURE_STATS_PATH)
        except Exception as _e:
            logger.info(
                f"Could not load feature_stats.joblib ({_e}); "
                "coverage/drift checks will use fallbacks."
            )

        # ── Change A: Universe-coverage guard ─────────────────────────────────
        # Refuse to score when the cross-section is too thin for reliable
        # z-scores.  Threshold = 25% of the training median tickers/date,
        # with an absolute floor of 30.
        n_universe: int = df['ticker'].nunique()
        train_universe_size: Optional[int] = (
            saved_stats.get('_meta', {}).get('train_universe_size')
            if saved_stats is not None else None
        )
        coverage_threshold: int = (
            max(30, int(0.25 * train_universe_size))
            if train_universe_size is not None else 30
        )
        if n_universe < coverage_threshold:
            msg = (
                f"Inference universe too small for reliable z-scoring: "
                f"{n_universe} tickers present, threshold {coverage_threshold} "
                f"(25 %% of training universe {train_universe_size}). "
                "Skipping inference — run a full quant scan first."
            )
            logger.error(msg)
            log_notification("Error", msg)
            conn.close()
            return

        logger.info(
            f"Loaded {len(df)} rows for cross-sectional normalisation "
            f"({n_universe} unique tickers, date: {df['date'].iloc[0]})."
        )

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

        # Fundamental preprocessing — identical to training pipeline
        df = _winsorize_and_impute_fundamentals(df)

        df.replace([np.inf, -np.inf], np.nan, inplace=True)

        # ── Cross-sectional Z-scoring ─────────────────────────────────────────
        logger.info(
            f"Applying cross-sectional Z-scoring across {len(df)} tickers..."
        )
        for col in CONTINUOUS_FEATURES:
            df[f'{col}_z'] = df.groupby('date')[col].transform(cross_sectional_zscore)

        # ── Change B: Train-vs-serve feature drift diagnostic ─────────────────
        # Compares today's raw (pre-z-score) feature means against the pooled
        # training statistics saved in feature_stats.joblib.  Emits a single
        # WARNING listing all drifted features; never alters any predictions.
        try:
            if saved_stats is not None:
                train_feature_stats: Dict[str, Dict[str, float]] = (
                    saved_stats.get('features', {})
                )
                drifted: List[str] = []
                for col in CONTINUOUS_FEATURES:
                    if col not in train_feature_stats or col not in df.columns:
                        continue
                    today_mean: float = float(df[col].mean())
                    train_mean: float = train_feature_stats[col]['mean']
                    train_std:  float = train_feature_stats[col]['std']
                    if abs(today_mean - train_mean) / (train_std + 1e-9) > 1.0:
                        drifted.append(col)
                if drifted:
                    logger.warning(
                        f"Feature drift vs training distribution detected in "
                        f"{len(drifted)} feature(s): {drifted}. "
                        "Predictions may be less reliable today."
                    )
            else:
                logger.info(
                    "Feature drift check skipped — feature_stats.joblib not found."
                )
        except Exception as _drift_e:
            logger.info(f"Feature drift check skipped due to error: {_drift_e}")

        # ── Score only requested tickers ──────────────────────────────────────
        target_set      = set(tickers)
        update_payloads = []

        for _, row in df.iterrows():
            if row['ticker'] not in target_set:
                continue
            if pd.isna(row[FEATURE_COLS]).any():
                continue

            X_infer = pd.DataFrame([row[FEATURE_COLS]])
            prob    = model.predict_proba(X_infer)[0][1]
            if not (0.0 <= prob <= 1.0):
                continue
            ml_confidence_score = float(round(prob * 100.0, 2))

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
                "No valid payloads generated. Ensure stock_signals is populated "
                "by running the daily quant scan before ML inference."
            )

    except Exception as e:
        logger.error(f"Fatal error during ML inference: {e}")
    finally:
        if conn:
            conn.close()


def train_quantile_models() -> None:
    """
    Trains two XGBoost quantile regressors on the same 18-feature pipeline
    as the binary classifier, with a continuous 10-day return target.

    Q10 (quantile_alpha=0.10): pessimistic floor — 10th-percentile return.
    Q90 (quantile_alpha=0.90): optimistic ceiling — 90th-percentile return.

    Inference converts returns to prices:
        price_qN = close_price * (1 + qN_predicted_return)

    Models are saved to models/quantile_q10.joblib and quantile_q90.joblib.
    """
    logger.info("Initiating Quantile Regression model training (Q10 / Q90)...")

    _mem = psutil.virtual_memory()
    _avail_gb = _mem.available / (1024 ** 3)
    if _avail_gb < 0.75:
        msg = (
            f"Quantile training aborted: only {_avail_gb:.1f} GB RAM available "
            "(minimum 0.75 GB required)."
        )
        logger.error(msg)
        log_notification("Error", msg)
        return

    try:
        conn = get_connection()
        query = """
            SELECT qs.ticker, qs.date, qs.close_price, qs.volume,
                   qs.rsi_14, qs.macd, qs.macd_signal, qs.macd_hist,
                   qs.sma_50, qs.sma_200, qs.volume_surge, qs.bullish_cross,
                   qs.mom_1m, qs.mom_3m, qs.mom_6m, qs.mom_12m_skip1m,
                   qs.atr_pct, qs.hist_vol_20,
                   qs.rel_strength_5d, qs.rel_strength_20d,
                   ss.trailing_pe, ss.price_to_book, ss.profit_margin,
                   ss.roe, ss.revenue_growth, ss.debt_to_equity,
                   tm.sector
            FROM quant_signals qs
            LEFT JOIN stock_signals   ss ON qs.ticker = ss.ticker
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
            logger.warning("No data found for quantile training. Aborting.")
            return

        # Feature engineering — identical to train_global_ml_model()
        df['dist_sma_50']  = (df['close_price'] - df['sma_50'])  / df['sma_50']
        df['dist_sma_200'] = (df['close_price'] - df['sma_200']) / df['sma_200']
        df['macd_pct']        = df['macd']        / df['close_price']
        df['macd_signal_pct'] = df['macd_signal'] / df['close_price']
        df['macd_hist_pct']   = df['macd_hist']   / df['close_price']
        df['volume_surge']  = df['volume_surge'].fillna(0).astype(int)
        df['bullish_cross'] = df['bullish_cross'].fillna(0).astype(int)
        df['sector_code']    = df['sector'].map(SECTOR_MAP).fillna(99).astype(int)
        df['dollar_vol_log'] = np.log1p(df['close_price'] * df['volume'])

        df = _winsorize_and_impute_fundamentals(df)
        df.replace([np.inf, -np.inf], np.nan, inplace=True)

        for col in CONTINUOUS_FEATURES:
            df[f'{col}_z'] = df.groupby('date')[col].transform(cross_sectional_zscore)

        df.dropna(subset=FEATURE_COLS, inplace=True)

        # Continuous return target (not binary)
        df['next_close']   = df.groupby('ticker')['close_price'].shift(-1)
        df['future_close'] = df.groupby('ticker')['close_price'].shift(-PREDICTION_HORIZON_DAYS)
        df.dropna(subset=['next_close', 'future_close'], inplace=True)
        df['return_target'] = (
            (df['future_close'] - df['next_close']) / df['next_close']
        ).clip(-1.0, 1.0)

        if len(df) < 1000:
            logger.warning(f"Insufficient training samples ({len(df)}) for quantile training.")
            return

        df.sort_values('date', inplace=True)
        df.reset_index(drop=True, inplace=True)

        X_full = df[FEATURE_COLS]
        y_full = df['return_target']

        # 80/20 temporal split with embargo — no calibration step for regression
        unique_dates = np.sort(df['date'].unique())
        date_series  = df['date'].reset_index(drop=True)
        n_dates      = len(unique_dates)
        train_end    = int(n_dates * 0.80)
        train_dates  = set(unique_dates[:train_end - PREDICTION_HORIZON_DAYS])
        test_dates   = set(unique_dates[train_end:])

        train_idx = date_series.index[date_series.isin(train_dates)].tolist()
        test_idx  = date_series.index[date_series.isin(test_dates)].tolist()

        X_train = X_full.iloc[train_idx]
        y_train = y_full.iloc[train_idx]
        X_test  = X_full.iloc[test_idx]
        y_test  = y_full.iloc[test_idx]

        logger.info(
            f"Quantile temporal split — Train: {len(X_train):,} rows | "
            f"Test: {len(X_test):,} rows"
        )

        _xgb_params = dict(
            n_estimators=200, max_depth=5, learning_rate=0.05,
            subsample=0.8, colsample_bytree=0.8,
            random_state=42, n_jobs=1,
        )

        q10 = XGBRegressor(objective='reg:quantileerror', quantile_alpha=0.10, **_xgb_params)
        q90 = XGBRegressor(objective='reg:quantileerror', quantile_alpha=0.90, **_xgb_params)
        q10.fit(X_train, y_train)
        q90.fit(X_train, y_train)

        q10_preds = q10.predict(X_test)
        q90_preds = q90.predict(X_test)
        crossing_pct = float(np.mean(q10_preds >= q90_preds))
        logger.info(
            f"Test-set quantile diagnostics — "
            f"Q10 median: {float(np.median(q10_preds)):.4f}, "
            f"Q90 median: {float(np.median(q90_preds)):.4f}, "
            f"crossing rate: {crossing_pct:.2%}"
        )

        # Refit on full dataset with fixed hyperparameters
        final_q10 = XGBRegressor(objective='reg:quantileerror', quantile_alpha=0.10, **_xgb_params)
        final_q90 = XGBRegressor(objective='reg:quantileerror', quantile_alpha=0.90, **_xgb_params)
        final_q10.fit(X_full, y_full)
        final_q90.fit(X_full, y_full)

        joblib.dump(final_q10, QUANTILE_Q10_PATH)
        joblib.dump(final_q90, QUANTILE_Q90_PATH)
        logger.info(
            f"✅ Quantile models saved — "
            f"Q10: {QUANTILE_Q10_PATH}, Q90: {QUANTILE_Q90_PATH}"
        )
        log_notification(
            "Success",
            f"Quantile Regression models trained — Q10/Q90 price bands ready. "
            f"Test crossing rate: {crossing_pct:.2%}."
        )

    except Exception as e:
        logger.error(f"Fatal error during quantile training: {e}")
        log_notification("Error", f"Quantile Regression Training failed: {str(e)}")


def score_quantile_predictions(tickers: List[str]) -> None:
    """
    Loads the Q10/Q90 quantile regressors and writes price_q10 / price_q90
    into quant_signals for each requested ticker.

    price_q10 = close_price * (1 + Q10_return)  — pessimistic 10-day floor
    price_q90 = close_price * (1 + Q90_return)  — optimistic 10-day ceiling
    """
    if not tickers:
        return

    if not QUANTILE_Q10_PATH.exists() or not QUANTILE_Q90_PATH.exists():
        logger.info(
            "Quantile models not found — skipping quantile scoring "
            "(run ML training first to generate Q10/Q90 models)."
        )
        return

    logger.info(f"Scoring quantile price bands for {len(tickers)} tickers...")

    conn = None
    try:
        q10_model = joblib.load(QUANTILE_Q10_PATH)
        q90_model = joblib.load(QUANTILE_Q90_PATH)
        conn = get_connection()

        query = """
            SELECT qs.ticker, qs.date, qs.close_price, qs.volume,
                   qs.rsi_14, qs.macd, qs.macd_signal, qs.macd_hist,
                   qs.sma_50, qs.sma_200, qs.volume_surge, qs.bullish_cross,
                   qs.mom_1m, qs.mom_3m, qs.mom_6m, qs.mom_12m_skip1m,
                   qs.atr_pct, qs.hist_vol_20,
                   qs.rel_strength_5d, qs.rel_strength_20d,
                   ss.trailing_pe, ss.price_to_book, ss.profit_margin,
                   ss.roe, ss.revenue_growth, ss.debt_to_equity,
                   tm.sector
            FROM quant_signals qs
            LEFT JOIN stock_signals   ss ON qs.ticker = ss.ticker
            LEFT JOIN ticker_metadata tm ON qs.ticker = tm.ticker
            WHERE qs.mom_1m           IS NOT NULL
              AND qs.atr_pct          IS NOT NULL
              AND qs.rel_strength_5d  IS NOT NULL
              AND qs.rel_strength_20d IS NOT NULL
              AND qs.date = (
                  SELECT MAX(qs2.date) FROM quant_signals qs2
                  WHERE qs2.ticker          = qs.ticker
                    AND qs2.mom_1m          IS NOT NULL
                    AND qs2.atr_pct         IS NOT NULL
                    AND qs2.rel_strength_5d  IS NOT NULL
                    AND qs2.rel_strength_20d IS NOT NULL
              )
        """
        df = pd.read_sql_query(query, conn)

        if df.empty:
            logger.warning("No data for quantile scoring. Re-run quant scan first.")
            return

        # Feature engineering — identical to training and binary inference
        df['dist_sma_50']  = (df['close_price'] - df['sma_50'])  / df['sma_50']
        df['dist_sma_200'] = (df['close_price'] - df['sma_200']) / df['sma_200']
        df['macd_pct']        = df['macd']        / df['close_price']
        df['macd_signal_pct'] = df['macd_signal'] / df['close_price']
        df['macd_hist_pct']   = df['macd_hist']   / df['close_price']
        df['volume_surge']  = df['volume_surge'].fillna(0).astype(int)
        df['bullish_cross'] = df['bullish_cross'].fillna(0).astype(int)
        df['sector_code']    = df['sector'].map(SECTOR_MAP).fillna(99).astype(int)
        df['dollar_vol_log'] = np.log1p(df['close_price'] * df['volume'])

        df = _winsorize_and_impute_fundamentals(df)
        df.replace([np.inf, -np.inf], np.nan, inplace=True)

        for col in CONTINUOUS_FEATURES:
            df[f'{col}_z'] = df.groupby('date')[col].transform(cross_sectional_zscore)

        target_set = set(tickers)
        payloads: List[Tuple[float, float, str, str]] = []

        for _, row in df.iterrows():
            if row['ticker'] not in target_set:
                continue
            if pd.isna(row[FEATURE_COLS]).any():
                continue

            X_infer   = pd.DataFrame([row[FEATURE_COLS]])
            q10_ret   = float(q10_model.predict(X_infer)[0])
            q90_ret   = float(q90_model.predict(X_infer)[0])
            close     = float(row['close_price'])
            price_q10 = close * (1.0 + q10_ret)
            price_q90 = close * (1.0 + q90_ret)
            payloads.append((price_q10, price_q90, row['ticker'], row['date']))

        if payloads:
            cursor = conn.cursor()
            cursor.executemany("""
                UPDATE quant_signals
                SET price_q10 = ?, price_q90 = ?
                WHERE ticker = ? AND date = ?
            """, payloads)
            conn.commit()
            logger.info(f"✅ Quantile price bands written for {len(payloads)} assets.")
        else:
            logger.warning("No valid quantile payloads generated.")

    except Exception as e:
        logger.error(f"Fatal error during quantile scoring: {e}")
    finally:
        if conn:
            conn.close()