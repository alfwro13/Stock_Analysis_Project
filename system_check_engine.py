import logging
from typing import Any, Dict, List, Optional

from config import load_config
from database import get_connection

logger = logging.getLogger(__name__)


def _time_to_minutes(time_str: str) -> int:
    h, m = map(int, time_str.split(':'))
    return h * 60 + m


def _load_train_universe_size() -> Optional[int]:
    try:
        import joblib
        from pathlib import Path
        stats = joblib.load(Path("models/feature_stats.joblib"))
        return stats.get("_meta", {}).get("train_universe_size")
    except Exception:
        return None


def run_system_checks() -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []
    config = load_config()
    scheduling = config.get("SCHEDULING", {})

    ml_training = scheduling.get("ML_TRAINING", {})
    ml_backfill = scheduling.get("ML_BACKFILL", {})
    training_enabled = ml_training.get("ENABLED", True)
    backfill_enabled = ml_backfill.get("ENABLED", False)

    if training_enabled and not backfill_enabled:
        issues.append({
            "key": "ml_training_without_backfill",
            "level": "warning",
            "message": (
                "ML Training is scheduled but ML Historical Backfill is disabled. "
                "Mid-week training runs will use stale momentum features. "
                "Enable ML Backfill (Settings → Scheduling)."
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
                        f"On {overlap_str}: ML Training runs at {ml_training.get('TIME')} "
                        f"but ML Backfill is not until {ml_backfill.get('TIME')} — "
                        "swap the times so Backfill completes first."
                    ),
                })

    conn = None
    try:
        conn = get_connection()
        row = conn.execute("""
            SELECT COUNT(DISTINCT ticker) AS cnt
            FROM quant_signals
            WHERE date = (
                SELECT MAX(date) FROM quant_signals
                WHERE mom_1m IS NOT NULL
                  AND atr_pct IS NOT NULL
                  AND rel_strength_5d IS NOT NULL
                  AND rel_strength_20d IS NOT NULL
            )
              AND mom_1m IS NOT NULL
              AND atr_pct IS NOT NULL
              AND rel_strength_5d IS NOT NULL
              AND rel_strength_20d IS NOT NULL
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
                    f"minimum {threshold} required. "
                    "Run Settings → ▶ Run Backfill Now to restore coverage."
                ),
            })
    except Exception as e:
        logger.warning("System check coverage query failed: %s", e)
    finally:
        if conn:
            conn.close()

    return issues
