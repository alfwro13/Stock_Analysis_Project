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

# Configure robust module-level logging
logger = logging.getLogger(__name__)

# Constants
MODELS_DIR = BASE_DIR / "models"
MODELS_DIR.mkdir(parents=True, exist_ok=True)
MODEL_PATH = MODELS_DIR / "ml_ensemble.joblib"

# [BUG-03 FIX] Path for persisted training population statistics.
# At inference time, features are normalized using these saved stats rather than
# recomputing z-scores on a tiny batch (3-10 tickers), which collapses all
# scores to the base rate and makes the model non-discriminative.
FEATURE_STATS_PATH = MODELS_DIR / "feature_stats.joblib"

# [LEAKAGE RESOLVED & Z-SCORING IMPLEMENTED]
# Replaced absolute continuous features with cross-sectional Z-scored variants (_z)
FEATURE_COLS = [
    'rsi_14_z', 'macd_pct_z', 'macd_signal_pct_z', 'macd_hist_pct_z',
    'volume_surge', 'bullish_cross', 'dist_sma_50_z', 'dist_sma_200_z',
    'sector_code', 'dollar_vol_log_z'
]

# Static mapping for GICS sectors to integer codes for the ML model
SECTOR_MAP = {
    "Technology": 1, "Healthcare": 2, "Financials": 3,
    "Financial Services": 3, "Real Estate": 4, "Energy": 5,
    "Basic Materials": 6, "Consumer Cyclical": 7, "Industrials": 8,
    "Utilities": 9, "Consumer Defensive": 10, "Communication Services": 11,
    "Unknown": 99  # Explicit unknown code — distinct from any real sector
}

# Continuous features that require normalization
CONTINUOUS_FEATURES = [
    'rsi_14', 'macd_pct', 'macd_signal_pct', 'macd_hist_pct',
    'dist_sma_50', 'dist_sma_200', 'dollar_vol_log'
]


def cross_sectional_zscore(series: pd.Series) -> pd.Series:
    """Calculates Z-Score dynamically. Safe against 0 standard deviation."""
    std = series.std()
    if pd.isna(std) or std == 0:
        return series - series.mean()
    return (series - series.mean()) / std


def apply_fixed_zscore(df: pd.DataFrame, feature_stats: Dict[str, Dict[str, float]]) -> pd.DataFrame:
    """
    [BUG-03 FIX] Applies fixed normalization to inference features using population
    statistics saved from the training dataset.

    During training, cross-sectional z-scores are computed per date across hundreds
    of tickers. At inference on a small batch (3-350 tickers), recomputing z-scores
    on the batch itself produces statistically meaningless values — a stock with RSI 80
    and a stock with RSI 30 both receive near-zero z-scores if they happen to be each
    other's only peers. This collapses all model outputs to the ~26% base rate.

    The correct fix is to normalize each inference feature using the global mean and
    std from the training population, ensuring the model sees features on the same
    statistical scale it was trained on.

    Args:
        df:            DataFrame containing raw (un-normalized) continuous features.
        feature_stats: Dict of {feature_name: {'mean': float, 'std': float}} loaded
                       from FEATURE_STATS_PATH.

    Returns:
        DataFrame with _z columns appended.
    """
    for col in CONTINUOUS_FEATURES:
        if col not in feature_stats:
            logger.warning(f"Feature '{col}' not found in saved stats. Using zero-fill.")
            df[f'{col}_z'] = 0.0
            continue
        mean = feature_stats[col]['mean']
        std  = feature_stats[col]['std']
        if std > 0:
            df[f'{col}_z'] = (df[col] - mean) / std
        else:
            df[f'{col}_z'] = df[col] - mean
    return df


def get_target_tickers() -> List[str]:
    """
    Combines the user's existing portfolio/watchlist tickers with a dynamic,
    randomly sampled cross-section of the market universe. This prevents
    Mega-Cap liquidity bias during model training.

    Returns:
        List[str]: A sorted list of valid ticker strings.
    """
    logger.info("Extracting user portfolio and watchlist tickers...")
    try:
        engine = DataEngine()
        user_tickers = engine.get_all_tickers()
    except Exception as e:
        logger.error(f"Failed to fetch user tickers from DataEngine: {e}")
        user_tickers = []

    logger.info("Dynamically sampling market universe to ensure balanced training distribution...")
    try:
        conn = get_connection()
        cursor = conn.cursor()
        # Randomly sample ~300 tickers to represent a true cross-section of the market
        cursor.execute("SELECT ticker FROM market_universe ORDER BY RANDOM() LIMIT 300")
        universe_sample = [row[0] for row in cursor.fetchall()]
        conn.close()
    except Exception as e:
        logger.warning(f"Failed to sample from market_universe (DB might be empty): {e}")
        universe_sample = []

    # Combine, deduplicate, and sort for determinism
    combined_set = set(user_tickers).union(set(universe_sample))

    # Filter out mutual funds (0P...) or known bad tickers
    cleaned_list = [t for t in combined_set if t and not t.startswith("0P")]
    final_tickers = sorted(cleaned_list)[:350]

    logger.info(f"Targeting {len(final_tickers)} unique tickers for historical backfill.")
    return final_tickers


def sync_ticker_metadata(tickers: List[str]) -> None:
    """
    Ensures institutional structural data (sector) is available.
    Creates and populates the ticker_metadata table idempotently.
    Note: Beta and Market Cap are fetched but no longer used in historical training
    to prevent lookahead bias.

    Args:
        tickers (List[str]): List of ticker symbols to synchronize.
    """
    logger.info(f"Syncing metadata for {len(tickers)} tickers to contextualize ML features...")
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS ticker_metadata (
            ticker TEXT PRIMARY KEY,
            sector TEXT,
            beta REAL,
            market_cap REAL
        )
    """)

    cursor.execute("SELECT ticker FROM ticker_metadata")
    existing_tickers = {row[0] for row in cursor.fetchall()}

    missing_tickers = [t for t in tickers if t not in existing_tickers]

    if not missing_tickers:
        logger.info("All ticker structural metadata is already up to date.")
        conn.close()
        return

    records: List[Tuple[str, str, float, float]] = []
    for ticker in missing_tickers:
        try:
            info = yf.Ticker(ticker).info
            sector = info.get('sector', 'Unknown')
            beta   = info.get('beta', 1.0)
            mcap   = info.get('marketCap', 0.0)

            records.append((
                ticker,
                sector,
                float(beta) if beta else 1.0,
                float(mcap) if mcap else 0.0
            ))
        except Exception as e:
            logger.warning(f"Failed to fetch metadata for {ticker}: {e}")
            records.append((ticker, 'Unknown', 1.0, 0.0))

        # Rate limit protection for Yahoo Finance
        time.sleep(0.1)

    if records:
        cursor.executemany("""
            INSERT OR REPLACE INTO ticker_metadata (ticker, sector, beta, market_cap)
            VALUES (?, ?, ?, ?)
        """, records)
        conn.commit()
        logger.info(f"Injected structural metadata for {len(records)} new tickers.")

    conn.close()


def run_historical_backfill() -> None:
    """
    Downloads 2 years of daily data per ticker, calculates vectorized
    technical indicators, synchronizes metadata, and executes bulk database insertions.
    """
    tickers = get_target_tickers()
    if not tickers:
        logger.warning("No tickers found to backfill. Aborting.")
        return

    sync_ticker_metadata(tickers)

    log_notification("Info", f"ML Historical Backfill initiated for {len(tickers)} assets.")
    conn = get_connection()
    cursor = conn.cursor()

    try:
        total_inserted = 0
        total_tickers  = len(tickers)

        for i, ticker in enumerate(tickers):
            logger.info(f"[{i+1}/{total_tickers}] Processing 2y historical data for {ticker}...")

            try:
                df = yf.download(ticker, period="2y", interval="1d", progress=False, auto_adjust=True)

                if df.empty:
                    continue

                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                df.dropna(subset=['Close', 'Volume'], inplace=True)

                if len(df) < 252:  # Require 1 full trading year for a stable SMA-200
                    continue

                # Vector-calculate technical indicators via 'ta' library
                df['rsi_14'] = ta.momentum.RSIIndicator(close=df['Close'], window=14).rsi()

                macd_indicator  = ta.trend.MACD(close=df['Close'])
                df['macd']        = macd_indicator.macd()
                df['macd_signal'] = macd_indicator.macd_signal()
                df['macd_hist']   = macd_indicator.macd_diff()

                df['sma_50']     = ta.trend.SMAIndicator(close=df['Close'], window=50).sma_indicator()
                df['sma_200']    = ta.trend.SMAIndicator(close=df['Close'], window=200).sma_indicator()
                df['vol_sma_20'] = df['Volume'].rolling(window=20).mean()

                df['volume_surge'] = (df['Volume'] > (df['vol_sma_20'] * 1.5)).astype(int)
                df['bullish_cross'] = (
                    (df['macd'] > df['macd_signal']) &
                    (df['macd'].shift(1) <= df['macd_signal'].shift(1))
                ).astype(int)

                df.dropna(inplace=True)
                if df.empty:
                    continue

                records: List[Tuple] = []
                for index, row in df.iterrows():
                    date_str = index.strftime('%Y-%m-%d')
                    records.append((
                        ticker, date_str, float(row['Close']), int(row['Volume']),
                        float(row['rsi_14']), float(row['macd']), float(row['macd_signal']),
                        float(row['macd_hist']), float(row['sma_50']), float(row['sma_200']),
                        int(row['volume_surge']), int(row['bullish_cross'])
                    ))

                # Correct Upsert to prevent staleness and protect ML/Risk columns
                query = """
                    INSERT INTO quant_signals
                    (ticker, date, close_price, volume, rsi_14, macd, macd_signal, macd_hist, sma_50, sma_200, volume_surge, bullish_cross)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ticker, date) DO UPDATE SET
                        close_price=excluded.close_price,
                        volume=excluded.volume,
                        rsi_14=excluded.rsi_14,
                        macd=excluded.macd,
                        macd_signal=excluded.macd_signal,
                        macd_hist=excluded.macd_hist,
                        sma_50=excluded.sma_50,
                        sma_200=excluded.sma_200,
                        volume_surge=excluded.volume_surge,
                        bullish_cross=excluded.bullish_cross
                """
                cursor.executemany(query, records)
                conn.commit()

                inserted       = cursor.rowcount
                total_inserted += inserted

            except Exception as e:
                logger.error(f"Error processing ticker {ticker}: {e}")
                conn.rollback()
            finally:
                time.sleep(0.5)

            processed = i + 1
            if total_tickers >= 2 and processed == total_tickers // 2:
                log_notification("Info", f"ML Historical Backfill is 50% complete ({processed}/{total_tickers}).")

        logger.info(f"--- BACKFILL COMPLETE. Injected/Updated {total_inserted} historical rows. ---")
        log_notification("Success", f"ML Historical Backfill completed successfully. Injected/Updated {total_inserted:,} data points.")

    except Exception as e:
        logger.error(f"Fatal error during historical backfill execution: {e}")
        log_notification("Error", f"ML Historical Backfill failed: {str(e)}")
    finally:
        conn.close()


def train_global_ml_model() -> None:
    """
    Connects to the local SQLite DB, builds technical/structural features,
    implements true Anchored Walk-Forward Validation with strict Temporal Embargos,
    executes Hyperparameter Optimization using RandomizedSearchCV, and trains an
    institutional-grade global ensemble model predicting >3% returns over 5 days.

    [BUG-03 FIX] After z-scoring, saves per-feature population statistics (mean, std)
    computed from the full training dataset to FEATURE_STATS_PATH. These are loaded
    at inference time by update_daily_ml_predictions() to apply consistent fixed
    normalization rather than recomputing z-scores on a small batch.
    """
    logger.info("Initiating Global ML Model Training pipeline with Hyperparameter Optimization...")
    log_notification("Info", "Global ML Model Training pipeline initiated.")

    try:
        conn = get_connection()

        # Join structural metadata (strictly sector mapping to avoid lookahead bias)
        query = """
            SELECT qs.ticker, qs.date, qs.close_price, qs.volume, qs.rsi_14, qs.macd, qs.macd_signal, qs.macd_hist,
                   qs.sma_50, qs.sma_200, qs.volume_surge, qs.bullish_cross,
                   tm.sector
            FROM quant_signals qs
            LEFT JOIN ticker_metadata tm ON qs.ticker = tm.ticker
            ORDER BY qs.date ASC
        """
        df = pd.read_sql_query(query, conn)
        conn.close()

        if df.empty:
            logger.warning("No quantitative data found in DB. Aborting ML training.")
            return

        # Feature Engineering: Price Normalization & Point-In-Time Proxies
        logger.info(f"Extracting features from {len(df)} historical records...")
        df['dist_sma_50']  = (df['close_price'] - df['sma_50'])  / df['sma_50']
        df['dist_sma_200'] = (df['close_price'] - df['sma_200']) / df['sma_200']

        df['macd_pct']        = df['macd']        / df['close_price']
        df['macd_signal_pct'] = df['macd_signal'] / df['close_price']
        df['macd_hist_pct']   = df['macd_hist']   / df['close_price']

        df['volume_surge']  = df['volume_surge'].fillna(0).astype(int)
        df['bullish_cross'] = df['bullish_cross'].fillna(0).astype(int)

        # Contextual Features
        df['sector_code'] = df['sector'].map(SECTOR_MAP).fillna(99).astype(int)

        # Point-In-Time Proxy
        df['dollar_vol_log'] = np.log1p(df['close_price'] * df['volume'])

        df.replace([np.inf, -np.inf], np.nan, inplace=True)

        # --- [BUG-03 FIX] SAVE TRAINING POPULATION STATISTICS ---
        # Compute and persist the global mean and std of each continuous feature
        # from the full training dataset BEFORE z-scoring. These will be used at
        # inference time to apply identical normalization to single-batch predictions,
        # preventing the z-score population collapse that causes score clustering.
        logger.info("Computing and saving training population statistics for inference-time normalization...")
        feature_stats: Dict[str, Dict[str, float]] = {}
        for col in CONTINUOUS_FEATURES:
            col_data = df[col].dropna()
            feature_stats[col] = {
                'mean': float(col_data.mean()),
                'std':  float(col_data.std())
            }
            logger.info(
                f"  Feature '{col}': mean={feature_stats[col]['mean']:.4f}, "
                f"std={feature_stats[col]['std']:.4f} "
                f"(computed from {len(col_data):,} training observations)"
            )

        joblib.dump(feature_stats, FEATURE_STATS_PATH)
        logger.info(f"✅ Feature statistics saved to {FEATURE_STATS_PATH}")

        # --- CROSS-SECTIONAL Z-SCORING (training only) ---
        # Forces the model to evaluate features relative to the rest of the market
        # on that specific trading day, neutralizing Absolute Size / Mega-Cap bias.
        logger.info("Applying cross-sectional Z-scoring to normalize features across liquidity regimes...")
        for col in CONTINUOUS_FEATURES:
            df[f'{col}_z'] = df.groupby('date')[col].transform(cross_sectional_zscore)

        df.dropna(subset=FEATURE_COLS, inplace=True)

        # [EXECUTION LEAKAGE RESOLVED]
        # Since 'open_price' is not in the schema, we use Close(T+1) as a realistic,
        # conservative execution proxy to prevent overnight gap leakage.
        df['next_close']   = df.groupby('ticker')['close_price'].shift(-1)
        df['future_close'] = df.groupby('ticker')['close_price'].shift(-5)
        df.dropna(subset=['next_close', 'future_close'], inplace=True)

        # Classification Target: 1 if executable return > 3%, else 0
        df['target'] = ((df['future_close'] - df['next_close']) / df['next_close'] > 0.03).astype(int)

        if len(df) < 1000:
            logger.warning(f"Insufficient training samples ({len(df)}). Need more historical data.")
            log_notification("Error", f"Insufficient training samples ({len(df)}). Backfill required.")
            return

        # Log class distribution for transparency
        pos = (df['target'] == 1).sum()
        neg = (df['target'] == 0).sum()
        logger.info(f"Class distribution — Positive (1): {pos:,} ({pos/len(df):.1%}) | Negative (0): {neg:,} ({neg/len(df):.1%})")

        # Sort values strictly by date to ensure temporal linearity
        df.sort_values('date', inplace=True)
        df.reset_index(drop=True, inplace=True)

        X_full = df[FEATURE_COLS]
        y_full = df['target']

        neg_count_full       = (y_full == 0).sum()
        pos_count_full       = (y_full == 1).sum()
        scale_pos_weight_full = neg_count_full / pos_count_full if pos_count_full > 0 else 1.0

        # --- Strict Temporal Out-Of-Sample (OOS) Split for Panel Data ---
        logger.info("Constructing Strict 5-Fold Walk-Forward Splits for Hyperparameter Optimization...")

        unique_dates = np.sort(df['date'].unique())
        date_series  = df['date'].reset_index(drop=True)

        cv_splits = []
        tscv = TimeSeriesSplit(n_splits=5)

        for train_date_idx, test_date_idx in tscv.split(unique_dates):
            # Apply a strict 5-day embargo gap to prevent target leakage
            if len(train_date_idx) > 5:
                train_dates = set(unique_dates[train_date_idx[:-5]])
                test_dates  = set(unique_dates[test_date_idx])

                # Map valid dates back to explicit integer row indices required by GridSearchCV
                train_idx = date_series.index[date_series.isin(train_dates)].tolist()
                test_idx  = date_series.index[date_series.isin(test_dates)].tolist()

                if train_idx and test_idx:
                    cv_splits.append((train_idx, test_idx))

        # --- Hyperparameter Grids ---
        rf_base = RandomForestClassifier(class_weight='balanced', random_state=42, n_jobs=-1)
        xgb_base = XGBClassifier(
            scale_pos_weight=scale_pos_weight_full,
            random_state=42,
            n_jobs=-1,
            eval_metric='logloss'
        )

        rf_param_dist = {
            'n_estimators':    [100, 150, 200, 250],
            'max_depth':       [4, 6, 8, 10],
            'min_samples_leaf': [1, 5, 10]
        }

        xgb_param_dist = {
            'n_estimators':    [100, 150, 200],
            'max_depth':       [3, 5, 7],
            'learning_rate':   [0.01, 0.05, 0.1],
            'subsample':       [0.7, 0.9, 1.0],
            'colsample_bytree': [0.7, 0.9, 1.0]
        }

        # --- Execute Randomized Search ---
        logger.info("Executing Randomized Search to find optimal structural boundaries...")

        rf_search = RandomizedSearchCV(
            estimator=rf_base,
            param_distributions=rf_param_dist,
            n_iter=10,
            cv=cv_splits,
            scoring='average_precision',
            random_state=42,
            n_jobs=-1
        )

        xgb_search = RandomizedSearchCV(
            estimator=xgb_base,
            param_distributions=xgb_param_dist,
            n_iter=10,
            cv=cv_splits,
            scoring='average_precision',
            random_state=42,
            n_jobs=-1
        )

        # Fit searches over the fully assembled dataset
        rf_search.fit(X_full, y_full)
        xgb_search.fit(X_full, y_full)

        best_rf  = rf_search.best_estimator_
        best_xgb = xgb_search.best_estimator_

        logger.info(f"Optimal RF Params Found:  {rf_search.best_params_}")
        logger.info(f"Optimal XGB Params Found: {xgb_search.best_params_}")

        avg_oos_pr_auc = (rf_search.best_score_ + xgb_search.best_score_) / 2.0
        logger.info(f"Averaged Optimized OOS Avg-Precision (PR-AUC) across 5 expanding regimes: {avg_oos_pr_auc:.4f}")
        logger.info(f"  (Random baseline PR-AUC for this dataset = {pos_count_full / len(y_full):.4f})")

        # --- Production Model Assembly ---
        logger.info("Calibrating base estimators individually before assembling production Voting Classifier...")

        # Reuse the date-blocked cv_splits to prevent future data from
        # leaking into the probability calibration folds.
        calibrated_rf  = CalibratedClassifierCV(estimator=best_rf,  method='isotonic', cv=cv_splits)
        calibrated_xgb = CalibratedClassifierCV(estimator=best_xgb, method='isotonic', cv=cv_splits)

        production_ensemble = VotingClassifier(
            estimators=[('rf', calibrated_rf), ('xgb', calibrated_xgb)],
            voting='soft'
        )

        production_ensemble.fit(X_full, y_full)

        joblib.dump(production_ensemble, MODEL_PATH)
        logger.info(f"✅ Production ML Ensemble successfully trained and saved to {MODEL_PATH}")
        log_notification("Success", f"Global ML Model trained & optimized (WF PR-AUC: {avg_oos_pr_auc:.2%}, baseline: {pos_count_full/len(y_full):.2%}).")

    except Exception as e:
        logger.error(f"Fatal error during ML model optimization & training: {e}")
        log_notification("Error", f"ML Model Training failed: {str(e)}")


def update_daily_ml_predictions(tickers: List[str]) -> None:
    """
    Loads the trained model, fetches the latest row for ALL tickers in the
    database, computes cross-sectional z-scores across the full population
    (replicating training methodology exactly), then writes confidence scores
    back for the requested ticker subset.

    [BUG-03 FIX - CORRECT APPROACH] The training pipeline computes z-scores
    per date across hundreds of tickers. The correct inference equivalent is
    to fetch ALL tickers sharing the latest date and z-score across that full
    population — not across a 3-10 ticker batch, and not against global
    historical means (which introduces market-regime bias).

    Args:
        tickers (List[str]): Tickers whose ml_confidence_score should be updated.
                             Z-scores are computed across ALL tickers in the DB
                             regardless of this list, to ensure a valid population.
    """
    if not tickers:
        logger.warning("Empty ticker list provided for ML inference. Skipping.")
        return

    if not MODEL_PATH.exists():
        logger.warning(f"Model file {MODEL_PATH} not found. Awaiting weekend training cycle.")
        return

    logger.info(f"Initiating ML Inference for {len(tickers)} assets...")

    conn = None
    try:
        model = joblib.load(MODEL_PATH)
        conn  = get_connection()

        # --- Fetch ALL tickers at the latest available date ---
        # This replicates the training cross-section: z-scores are computed
        # across all tickers on a given day, not just the requested subset.
        # The tickers parameter only governs which rows are written back.
        query = """
            SELECT qs.ticker, qs.date, qs.close_price, qs.volume,
                   qs.rsi_14, qs.macd, qs.macd_signal, qs.macd_hist,
                   qs.sma_50, qs.sma_200, qs.volume_surge, qs.bullish_cross,
                   tm.sector
            FROM quant_signals qs
            LEFT JOIN ticker_metadata tm ON qs.ticker = tm.ticker
            WHERE qs.date = (SELECT MAX(date) FROM quant_signals)
        """
        df = pd.read_sql_query(query, conn)

        if df.empty:
            logger.warning("No recent data found to run inference on.")
            conn.close()
            return

        logger.info(
            f"Loaded {len(df)} tickers for cross-sectional normalization "
            f"(date: {df['date'].iloc[0]})."
        )

        # Feature Engineering: identical pipeline as training
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

        # --- Cross-sectional z-scoring across the full population ---
        # All tickers share the same date here, so groupby('date') produces
        # a single group containing the full inference population — exactly
        # matching the training methodology.
        logger.info(
            f"Applying cross-sectional Z-scoring across {len(df)} tickers "
            f"(replicates training methodology)..."
        )
        for col in CONTINUOUS_FEATURES:
            df[f'{col}_z'] = df.groupby('date')[col].transform(cross_sectional_zscore)

        # --- Score only the requested tickers ---
        target_set = set(tickers)
        update_payloads = []

        for _, row in df.iterrows():
            if row['ticker'] not in target_set:
                continue
            if pd.isna(row[FEATURE_COLS]).any():
                continue

            X_infer = pd.DataFrame([row[FEATURE_COLS]])

            prob                = model.predict_proba(X_infer)[0][1]
            ml_confidence_score = round(prob * 100.0, 2)

            update_payloads.append((ml_confidence_score, row['ticker'], row['date']))

        if update_payloads:
            cursor = conn.cursor()
            update_query = """
                UPDATE quant_signals
                SET ml_confidence_score = ?
                WHERE ticker = ? AND date = ?
            """
            cursor.executemany(update_query, update_payloads)
            conn.commit()
            logger.info(f"✅ Executed ML predictions for {len(update_payloads)} assets.")
        else:
            logger.warning("No valid payloads generated. Check that tickers exist in quant_signals for the latest date.")

    except Exception as e:
        logger.error(f"Fatal error during ML inference: {e}")
    finally:
        if conn:
            conn.close()