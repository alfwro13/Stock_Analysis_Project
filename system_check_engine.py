import logging
from pathlib import Path
from typing import Any, Dict, List, Optional

import joblib

from config import PORTFOLIO_PATH, WATCHLIST_PATH, load_config
from database import get_connection

logger = logging.getLogger(__name__)


def _time_to_minutes(time_str: str) -> int:
    h, m = map(int, time_str.split(':'))
    return h * 60 + m


def _load_train_universe_size() -> Optional[int]:
    try:
        stats = joblib.load(Path("models/feature_stats.joblib"))
        return stats.get("_meta", {}).get("train_universe_size")
    except Exception:
        return None


def run_system_checks() -> List[Dict[str, Any]]:
    from scheduler_engine import job_label
    training_name = job_label("ml_training_job")
    backfill_name = job_label("ml_backfill_job")
    issues: List[Dict[str, Any]] = []
    config = load_config()
    scheduling = config.get("SCHEDULING", {})

    ml_training = scheduling.get("ML_TRAINING", {})
    ml_backfill = scheduling.get("ML_BACKFILL", {})
    training_enabled = ml_training.get("ENABLED", True)
    backfill_enabled = ml_backfill.get("ENABLED", False)

    if not config.get("GHOSTFOLIO_ENABLED", False) and (PORTFOLIO_PATH.exists() or WATCHLIST_PATH.exists()):
        issues.append({
            "key": "ghostfolio_files_not_purged",
            "level": "warning",
            "message": (
                "Ghostfolio Integration is disabled but portfolio.json/watchlist.json still exist on disk. "
                "They will be removed by the next Database & File Maintenance run, or immediately via Settings → System Diagnostics → Database & File Maintenance → Run Now."
            ),
        })

    if training_enabled and not backfill_enabled:
        issues.append({
            "key": "ml_training_without_backfill",
            "level": "warning",
            "message": (
                f"{training_name} is scheduled but {backfill_name} is disabled. "
                "Mid-week training runs will use stale momentum features. "
                f"Enable {backfill_name} (Settings → Machine Learning & AI Engine)."
            ),
        })

    if training_enabled and backfill_enabled:
        train_days = set(ml_training.get("DAYS", ["sun"]))
        backfill_days = set(ml_backfill.get("DAYS", ["sat"]))
        overlap = train_days & backfill_days
        if overlap:
            train_mins = _time_to_minutes(ml_training.get("TIME", "04:00"))
            backfill_mins = _time_to_minutes(ml_backfill.get("TIME", "02:00"))
            if train_mins < backfill_mins:
                overlap_str = ", ".join(sorted(overlap))
                issues.append({
                    "key": "ml_training_before_backfill",
                    "level": "warning",
                    "message": (
                        f"On {overlap_str}: {training_name} runs at {ml_training.get('TIME')} "
                        f"but {backfill_name} is not until {ml_backfill.get('TIME')} — "
                        "swap the times so the backfill completes first."
                    ),
                })

    conn = None
    try:
        conn = get_connection()
        row = conn.execute("""
            SELECT COUNT(DISTINCT ticker) AS cnt
            FROM quant_signals qs
            WHERE qs.mom_1m IS NOT NULL
              AND qs.atr_pct IS NOT NULL
              AND qs.rel_strength_5d IS NOT NULL
              AND qs.rel_strength_20d IS NOT NULL
              AND qs.date = (
                  SELECT MAX(qs2.date) FROM quant_signals qs2
                  WHERE qs2.ticker           = qs.ticker
                    AND qs2.mom_1m           IS NOT NULL
                    AND qs2.atr_pct          IS NOT NULL
                    AND qs2.rel_strength_5d  IS NOT NULL
                    AND qs2.rel_strength_20d IS NOT NULL
              )
        """).fetchone()
        coverage = row["cnt"] if row else 0
        train_size = _load_train_universe_size()
        threshold = max(30, int(0.25 * train_size)) if train_size else 200
        if coverage < threshold:
            issues.append({
                "key": "low_inference_coverage",
                "level": "error",
                "message": (
                    f"ML inference universe: {coverage} tickers with complete features, "
                    f"minimum {threshold} required (25% of {train_size or 'unknown'} training tickers). "
                    "Run ▶ Run Backfill Now to restore coverage, "
                    "OR run ▶ Run Training Now to retrain the model on current data and reset the threshold."
                ),
            })
    except Exception as e:
        logger.warning("System check coverage query failed: %s", e)
    finally:
        if conn:
            conn.close()

    return issues
