import logging
from typing import Any, Dict, List, Optional, Tuple

import joblib
import pandas as pd

from model_compatibility_engine import load_sklearn_artifact
from database import get_connection, log_notification
from ml_features import (
    MODEL_PATH, FEATURE_STATS_PATH, QUANTILE_Q10_PATH, QUANTILE_Q90_PATH, FEATURE_COLS,
    CONTINUOUS_FEATURES, LATEST_FEATURES_QUERY, build_model_features,
)

logger = logging.getLogger(__name__)

# GUI name: "Daily ML Inference". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.


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
        logger.warning('Model not found at %s. Awaiting training cycle.', MODEL_PATH)
        return

    logger.info('Initiating ML Inference for %s assets...', len(tickers))

    conn = None
    try:
        model = load_sklearn_artifact(MODEL_PATH, "ml_training_job")
        conn  = get_connection()
        df = pd.read_sql_query(LATEST_FEATURES_QUERY, conn)

        if df.empty:
            logger.warning("No data with complete feature set found. Re-run backfill first.")
            return

        # ── Load saved stats (shared by Change A and Change B) ────────────────
        saved_stats: Optional[Dict[str, Any]] = None
        try:
            if FEATURE_STATS_PATH.exists():
                saved_stats = joblib.load(FEATURE_STATS_PATH)
        except Exception as _e:
            logger.info(
                'Could not load feature_stats.joblib (%s); coverage/drift checks will use fallbacks.', _e
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
            return

        logger.info(
            'Loaded %s rows for cross-sectional normalisation (%s unique tickers, date: %s).',
            len(df),
            n_universe,
            df['date'].iloc[0],
        )

        df = build_model_features(df)

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
                        'Feature drift vs training distribution detected in %s feature(s): %s. Predictions may be less reliable today.',
                        len(drifted),
                        drifted,
                    )
            else:
                logger.info(
                    "Feature drift check skipped — feature_stats.joblib not found."
                )
        except Exception as _drift_e:
            logger.info('Feature drift check skipped due to error: %s', _drift_e)

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
            # stock_signals.ml_confidence mirrors the same value onto the one-row-per-ticker
            # snapshot table so consumers that already join off stock_signals (e.g. the Regime-
            # Weighted Conviction Score) don't need a second quant_signals subselect for it.
            cursor.executemany("""
                UPDATE stock_signals
                SET ml_confidence = ?
                WHERE ticker = ?
            """, [(score, ticker) for score, ticker, _date in update_payloads])
            conn.commit()
            logger.info('✅ Executed ML predictions for %s assets.', len(update_payloads))
        else:
            logger.warning(
                "No valid payloads generated. Ensure stock_signals is populated "
                "by running the daily quant scan before ML inference."
            )

    except Exception as e:
        logger.error('Fatal error during ML inference: %s', e)
    finally:
        if conn:
            conn.close()


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

    logger.info('Scoring quantile price bands for %s tickers...', len(tickers))

    conn = None
    try:
        q10_model = load_sklearn_artifact(QUANTILE_Q10_PATH, "ml_training_job")
        q90_model = load_sklearn_artifact(QUANTILE_Q90_PATH, "ml_training_job")
        conn = get_connection()
        df = pd.read_sql_query(LATEST_FEATURES_QUERY, conn)

        if df.empty:
            logger.warning("No data for quantile scoring. Re-run quant scan first.")
            return

        df = build_model_features(df)

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
            logger.info('✅ Quantile price bands written for %s assets.', len(payloads))
        else:
            logger.warning("No valid quantile payloads generated.")

    except Exception as e:
        logger.error('Fatal error during quantile scoring: %s', e)
    finally:
        if conn:
            conn.close()
