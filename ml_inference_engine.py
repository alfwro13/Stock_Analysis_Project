import logging
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from constants import ML_FEATURE_MAX_AGE_TRADING_DAYS
from db.helpers import get_portfolio_watchlist_tickers, ml_fresh_since
from model_compatibility_engine import load_sklearn_artifact
from db.database import get_connection, log_notification
from ml_features import (
    MODEL_PATH, FEATURE_STATS_PATH, QUANTILE_Q10_PATH, QUANTILE_Q90_PATH, FEATURE_COLS,
    CONTINUOUS_FEATURES, LATEST_FEATURES_QUERY, build_model_features, inference_cross_section,
    load_feature_stats,
)
from notification_engine import notify

logger = logging.getLogger(__name__)

# GUI name: "Daily ML Inference". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.

_MAX_TICKERS_IN_WARNING = 15
_STALE_FEATURES_ENGINE = "ml_inference_stale_features"


def _coverage_threshold(saved_stats: Optional[Dict[str, Any]]) -> Tuple[int, Optional[int]]:
    train_universe_size = saved_stats.get('_meta', {}).get('train_universe_size') if saved_stats is not None else None
    threshold = max(30, int(0.25 * train_universe_size)) if train_universe_size is not None else 30
    return threshold, train_universe_size


def _log_cross_section(summary: Dict[str, Any], threshold: int) -> None:
    per_date = ', '.join(f'{d}={n}' for d, n in list(summary['date_counts'].items())[:6])
    logger.info(
        'Inference cross-section: %s of %s latest rows used (dates %s to %s); %s older than %s trading days, '
        '%s on dates below the %s-ticker coverage threshold. Tickers per date: %s.',
        summary['latest_rows'] - summary['too_old'] - summary['thin_dates'], summary['latest_rows'],
        summary['min_date'], summary['max_date'], summary['too_old'], ML_FEATURE_MAX_AGE_TRADING_DAYS,
        summary['thin_dates'], threshold, per_date,
    )


def _ever_scored(cursor: Any, tickers: set) -> set:
    if not tickers:
        return set()
    placeholders = ",".join("?" for _ in tickers)
    cursor.execute(
        f"SELECT DISTINCT ticker FROM quant_signals WHERE ml_scored_at IS NOT NULL AND ticker IN ({placeholders})",
        sorted(tickers),
    )
    return {row[0] for row in cursor.fetchall()}


def alert_unscored_holdings(unscored: Optional[List[str]]) -> None:
    """Warns once per Portfolio/Watchlist ticker that had a score and lost it (re-arms when scored again); a ticker never scored (short history, fund codes) is only logged."""
    if unscored is None:
        return
    missing = set(unscored) & set(get_portfolio_watchlist_tickers())
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        lost = _ever_scored(cursor, missing)
        cursor.execute("SELECT ticker FROM alert_state WHERE engine = ?", (_STALE_FEATURES_ENGINE,))
        warned = {row[0] for row in cursor.fetchall()}

        never_scored = sorted(missing - lost)
        if never_scored:
            logger.info(
                "Daily ML Inference left %d Portfolio/Watchlist ticker(s) unscored that have never had a score "
                "(typically fewer than 200 daily bars, or a fund code): %s",
                len(never_scored), ", ".join(never_scored),
            )

        new = lost - warned
        if new:
            shown = sorted(lost)[:_MAX_TICKERS_IN_WARNING]
            extra = f" (+{len(lost) - len(shown)} more)" if len(lost) > len(shown) else ""
            notify(
                _STALE_FEATURES_ENGINE, "Warning",
                f"Daily ML Inference could not score {len(lost)} Portfolio/Watchlist ticker(s) that had a score before: "
                f"their newest quant features are older than {ML_FEATURE_MAX_AGE_TRADING_DAYS} trading days or missing: "
                f"{', '.join(shown)}{extra}. Check the Daily Quant Screener and the Update Pipeline for these tickers.",
                level="warning",
            )

        now = datetime.now(timezone.utc)
        today = now.date().isoformat()
        cursor.executemany(
            "DELETE FROM alert_state WHERE engine = ? AND ticker = ?",
            [(_STALE_FEATURES_ENGINE, ticker) for ticker in warned - lost],
        )
        cursor.executemany(
            "INSERT INTO alert_state (engine, ticker, last_fired_utc, state_date) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(engine, ticker) DO UPDATE SET last_fired_utc = excluded.last_fired_utc, state_date = excluded.state_date",
            [(_STALE_FEATURES_ENGINE, ticker, now.strftime("%Y-%m-%d %H:%M:%S"), today) for ticker in new],
        )
        cursor.executemany(
            "UPDATE alert_state SET state_date = ? WHERE engine = ? AND ticker = ?",
            [(today, _STALE_FEATURES_ENGINE, ticker) for ticker in warned & lost],
        )
        conn.commit()
    except Exception as e:
        logger.error("Daily ML Inference unscored-holdings alert failed: %s", e)
    finally:
        if conn:
            conn.close()


def update_daily_ml_predictions(tickers: List[str]) -> Optional[List[str]]:
    """Scores `tickers` from the latest quant row of every stored ticker, z-scored per date as in training; returns the requested tickers left unscored (None when inference did not run)."""
    if not tickers:
        logger.warning("Empty ticker list. Skipping inference.")
        return None

    if not MODEL_PATH.exists():
        logger.warning('Model not found at %s. Awaiting training cycle.', MODEL_PATH)
        return None

    logger.info('Initiating ML Inference for %s assets...', len(tickers))

    conn = None
    try:
        model = load_sklearn_artifact(MODEL_PATH, "ml_training_job")
        conn  = get_connection()
        df = pd.read_sql_query(LATEST_FEATURES_QUERY, conn)

        if df.empty:
            logger.warning("No data with complete feature set found. Re-run backfill first.")
            return None

        saved_stats: Optional[Dict[str, Any]] = load_feature_stats(FEATURE_STATS_PATH)
        threshold, train_universe_size = _coverage_threshold(saved_stats)

        df, summary = inference_cross_section(df, datetime.now(timezone.utc).date(), threshold)
        _log_cross_section(summary, threshold)
        if df.empty:
            msg = (
                f"No date holds a usable cross-section for inference: no date within the last "
                f"{ML_FEATURE_MAX_AGE_TRADING_DAYS} trading days has {threshold} tickers "
                f"(25% of training universe {train_universe_size}). "
                "Skipping inference — run a full quant scan first."
            )
            logger.error(msg)
            log_notification("Error", msg)
            return None

        df = build_model_features(df)

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
                    train_mean: Optional[float] = train_feature_stats[col]['mean']
                    train_std:  Optional[float] = train_feature_stats[col]['std']
                    if train_mean is None or train_std is None:
                        continue
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
                    "Feature drift check skipped — feature_stats.json not found."
                )
        except Exception as _drift_e:
            logger.info('Feature drift check skipped due to error: %s', _drift_e)

        target_set      = set(tickers)
        scored_at       = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
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

            update_payloads.append((ml_confidence_score, scored_at, row['ticker'], row['date']))

        scored_tickers = {payload[2] for payload in update_payloads}
        unscored = sorted(target_set - scored_tickers)

        if update_payloads:
            cursor = conn.cursor()
            cursor.executemany("""
                UPDATE quant_signals
                SET ml_confidence_score = ?, ml_scored_at = ?
                WHERE ticker = ? AND date = ?
            """, update_payloads)
            # stock_signals.ml_confidence mirrors the same value onto the one-row-per-ticker
            # snapshot table so consumers that already join off stock_signals (e.g. the Regime-
            # Weighted Conviction Score) don't need a second quant_signals subselect for it.
            cursor.executemany("""
                UPDATE stock_signals
                SET ml_confidence = ?
                WHERE ticker = ?
            """, [(score, ticker) for score, _scored_at, ticker, _date in update_payloads])
            conn.commit()
            logger.info('✅ Executed ML predictions for %s assets.', len(update_payloads))
        else:
            logger.warning(
                "No valid payloads generated. Ensure stock_signals is populated "
                "by running the daily quant scan before ML inference."
            )

        if unscored:
            cursor = conn.cursor()
            cursor.executemany("""
                UPDATE stock_signals SET ml_confidence = NULL
                WHERE ticker = ? AND ml_confidence IS NOT NULL
                  AND NOT EXISTS (SELECT 1 FROM quant_signals q
                                  WHERE q.ticker = stock_signals.ticker AND q.ml_scored_at >= ?)
            """, [(ticker, ml_fresh_since()) for ticker in unscored])
            conn.commit()
        return unscored

    except Exception as e:
        logger.error('Fatal error during ML inference: %s', e)
        return None
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

        threshold, _ = _coverage_threshold(load_feature_stats(FEATURE_STATS_PATH))
        df, summary = inference_cross_section(df, datetime.now(timezone.utc).date(), threshold)
        _log_cross_section(summary, threshold)
        if df.empty:
            logger.warning("No date holds a usable cross-section for quantile scoring. Re-run quant scan first.")
            return

        df = build_model_features(df)

        target_set = set(tickers)
        scored_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        payloads: List[Tuple[float, float, str, str, str]] = []

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
            payloads.append((price_q10, price_q90, scored_at, row['ticker'], row['date']))

        if payloads:
            cursor = conn.cursor()
            cursor.executemany("""
                UPDATE quant_signals
                SET price_q10 = ?, price_q90 = ?, quantile_scored_at = ?
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
