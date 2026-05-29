"""
debug_scripts/test_ml_pipeline.py

End-to-end ML pipeline test: backfill → train → infer → verify.

Captures PR-AUC, CV score, class distribution and other key metrics,
persists them to debug_scripts/ml_pipeline_results.json, then prints a
comparison against the previous run.

Usage:
    python debug_scripts/test_ml_pipeline.py               # full run
    python debug_scripts/test_ml_pipeline.py --skip-backfill  # skip step 1
"""

import argparse
import json
import multiprocessing.resource_tracker as _mrt
import re
import sqlite3
import sys
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional

import pandas as pd

# ── Suppress spurious ResourceTracker noise on Python 3.13 ───────────────────
# joblib/scikit-learn spawn worker processes that each register their own
# ResourceTracker background process.  When the workers exit, the tracker's
# background process is already gone; Python 3.13's ResourceTracker.__del__
# then raises ChildProcessError in _stop_locked and prints the full traceback
# as "Exception ignored in: __del__".  Patching _stop to swallow that specific
# error eliminates the noise without affecting any real resource cleanup.
_orig_rt_stop = _mrt.ResourceTracker._stop

def _silent_rt_stop(self) -> None:
    try:
        _orig_rt_stop(self)
    except (ChildProcessError, OSError):
        pass

_mrt.ResourceTracker._stop = _silent_rt_stop
# ─────────────────────────────────────────────────────────────────────────────

# ── Make project root importable regardless of CWD ───────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from config import BASE_DIR, DB_PATH
from ai_prediction_engine import (
    run_historical_backfill,
    train_global_ml_model,
    update_daily_ml_predictions,
    get_target_tickers,
)

RESULTS_PATH = Path(__file__).parent / "ml_pipeline_results.json"
BACKFILL_STALE_HOURS = 20  # offer to skip if backfill ran within this window


# ── Metric capture ────────────────────────────────────────────────────────────

class _MetricCapture(logging.Handler):
    """Intercepts log records and extracts numeric metrics from known patterns."""

    PATTERNS: Dict[str, re.Pattern] = {
        "pr_auc":          re.compile(r"True OOS PR-AUC.*?:\s*([\d.]+)"),
        "random_baseline": re.compile(r"random baseline\s*=\s*([\d.]+)"),
        "cv_avg_precision": re.compile(r"Train-region CV Avg-Precision:\s*([\d.]+)"),
        "positive_rate":   re.compile(r"Positive \(1\):\s*[\d,]+\s*\(([\d.]+)%\)"),
        "train_rows":      re.compile(r"Train:\s*([\d,]+) rows"),
        "backfill_rows":   re.compile(r"Injected/Updated\s*([\d,]+) historical rows"),
        "inference_assets": re.compile(r"Executed ML predictions for\s*(\d+) assets"),
    }

    def __init__(self) -> None:
        super().__init__()
        self.metrics: Dict[str, Any] = {}

    def emit(self, record: logging.LogRecord) -> None:
        msg = record.getMessage()
        for key, pat in self.PATTERNS.items():
            if key in self.metrics:
                continue
            m = pat.search(msg)
            if m:
                raw = m.group(1).replace(",", "")
                self.metrics[key] = float(raw)


# ── Backfill recency ──────────────────────────────────────────────────────────

def _hours_since_last_backfill() -> Optional[float]:
    """Return hours since the most recent quant_signals row, or None if unknown."""
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(date) FROM quant_signals")
        row = cursor.fetchone()
        conn.close()
        if row and row[0]:
            last = datetime.strptime(row[0], "%Y-%m-%d")
            delta = datetime.now() - last
            return delta.total_seconds() / 3600
    except Exception:
        pass
    return None


def _prompt_skip_backfill() -> bool:
    """Return True if the user chooses to skip the backfill step."""
    hours = _hours_since_last_backfill()
    if hours is None or hours > BACKFILL_STALE_HOURS:
        return False
    print(
        f"\n⚡  Backfill appears recent — latest quant_signals row is "
        f"{hours:.1f}h old (threshold {BACKFILL_STALE_HOURS}h)."
    )
    answer = input("   Skip historical backfill? [y/N] ").strip().lower()
    return answer == "y"


# ── Result persistence & comparison ──────────────────────────────────────────

def _load_previous() -> Optional[Dict]:
    if RESULTS_PATH.exists():
        try:
            runs = json.loads(RESULTS_PATH.read_text())
            return runs[-1] if runs else None
        except Exception:
            pass
    return None


def _save_result(metrics: Dict[str, Any]) -> None:
    runs: list = []
    if RESULTS_PATH.exists():
        try:
            runs = json.loads(RESULTS_PATH.read_text())
        except Exception:
            runs = []
    runs.append(metrics)
    RESULTS_PATH.write_text(json.dumps(runs, indent=2))


def _print_comparison(current: Dict[str, Any], previous: Optional[Dict]) -> None:
    print("\n" + "=" * 60)
    print("  ML PIPELINE RESULTS")
    print("=" * 60)
    print(f"  Run time : {current['timestamp']}")

    key_metrics = [
        ("pr_auc",           "OOS PR-AUC",        0.01),
        ("cv_avg_precision", "CV Avg-Precision",   0.01),
        ("positive_rate",    "Positive label %",   1.0),
        ("train_rows",       "Train rows",         500),
        ("backfill_rows",    "Backfill rows",      1000),
        ("inference_assets", "Inferred assets",    1),
    ]

    for key, label, threshold in key_metrics:
        cur_val = current.get(key)
        if cur_val is None:
            continue
        prev_val = previous.get(key) if previous else None
        if prev_val is not None:
            delta = cur_val - prev_val
            arrow = "▲" if delta > threshold else ("▼" if delta < -threshold else "─")
            change = f"  {arrow} {delta:+.4g} vs prev ({prev_val:.4g})"
        else:
            change = "  (no previous run)"
        print(f"  {label:<22}: {cur_val:>10.4g}{change}")

    # Overall verdict
    if previous:
        pr_now  = current.get("pr_auc", 0)
        pr_prev = previous.get("pr_auc", 0)
        if pr_now > pr_prev + 0.01:
            verdict = "✅  Better than last run"
        elif pr_now < pr_prev - 0.01:
            verdict = "⚠️   Worse than last run — investigate before deploying"
        else:
            verdict = "➡️  In line with last run"
        print(f"\n  Verdict : {verdict}")

    print("=" * 60 + "\n")


# ── DB verification ───────────────────────────────────────────────────────────

def verify_database_state() -> None:
    if not DB_PATH.exists():
        logger.warning(f"Database not found at {DB_PATH}.")
        return
    try:
        conn = sqlite3.connect(DB_PATH)
        meta_df = pd.read_sql_query(
            "SELECT ticker, sector, beta, market_cap FROM ticker_metadata LIMIT 5", conn
        )
        logger.info(f"--- Ticker Metadata Sample ---\n{meta_df}")
        inf_df = pd.read_sql_query(
            """SELECT ticker, date, close_price, ml_confidence_score
               FROM quant_signals
               WHERE ml_confidence_score IS NOT NULL
               LIMIT 5""",
            conn,
        )
        logger.info(f"--- ML Inference Sample ---\n{inf_df}")
        conn.close()
    except Exception as exc:
        logger.error(f"Database verification failed: {exc}")


# ── Loky / multiprocessing cleanup ───────────────────────────────────────────

def _shutdown_worker_pool() -> None:
    """
    Explicitly shut down the loky reusable executor that scikit-learn and
    XGBoost leave behind.  Without this, Python 3.13's ResourceTracker emits
    spurious 'ChildProcessError: No child processes' tracebacks at exit.
    """
    try:
        from joblib.externals.loky import get_reusable_executor
        get_reusable_executor(max_workers=0).shutdown(wait=True, kill_workers=True)
    except Exception:
        pass


# ── Entry point ───────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[logging.StreamHandler()],
)
logger = logging.getLogger(__name__)

# Attach metric capture to the root logger so it intercepts all module logs
_capture = _MetricCapture()
_capture.setLevel(logging.INFO)
logging.getLogger().addHandler(_capture)


def main() -> None:
    parser = argparse.ArgumentParser(description="ML pipeline E2E test")
    parser.add_argument(
        "--skip-backfill", action="store_true",
        help="Skip step 1 (historical backfill)",
    )
    args = parser.parse_args()

    previous = _load_previous()
    logger.info("=== STARTING QUANT PIPELINE E2E TEST ===")

    # STEP 1 — Historical backfill
    skip_backfill = args.skip_backfill or _prompt_skip_backfill()
    if skip_backfill:
        logger.info("--- STEP 1: Skipping historical backfill (recent run detected) ---")
    else:
        logger.info("--- STEP 1: Running Historical Backfill & Metadata Sync ---")
        run_historical_backfill()

    # STEP 2 — Walk-forward training
    logger.info("--- STEP 2: Running Walk-Forward ML Training ---")
    train_global_ml_model()

    # STEP 3 — Inference
    logger.info("--- STEP 3: Running Daily ML Inference ---")
    sample_tickers = get_target_tickers()[:10]
    update_daily_ml_predictions(sample_tickers)

    # STEP 4 — Verification
    logger.info("--- STEP 4: Verifying SQLite Database State ---")
    verify_database_state()

    logger.info("=== TEST COMPLETE ===")

    # Persist and compare metrics
    result = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "skipped_backfill": skip_backfill,
        **_capture.metrics,
    }
    _save_result(result)
    _print_comparison(result, previous)

    # Clean up parallel workers to suppress ResourceTracker noise on exit
    _shutdown_worker_pool()


if __name__ == "__main__":
    main()
