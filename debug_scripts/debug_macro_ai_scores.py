"""
debug_scripts/debug_macro_ai_scores.py

Comprehensive debug harness for MacroAIEngine. Covers:

  1. Data availability  — row counts, date ranges, null rates, training readiness
  2. VIX join quality   — detects the silent all-null join failure (bug #5)
  3. Training log       — per-model CV score history with trend arrows
  4. Inference readiness — upcoming 48h events, warning / miss-prob population
  5. Target distribution — post_event_spy_gap stats and severe-event rate

Usage (run from project root):
    python debug_scripts/debug_macro_ai_scores.py
    python debug_scripts/debug_macro_ai_scores.py --last 5
    python debug_scripts/debug_macro_ai_scores.py --model rf_consensus_miss
    python debug_scripts/debug_macro_ai_scores.py --section training
"""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from constants import (
    MACRO_CAL_MIN_TRAIN_ROWS,
    MACRO_HMM_MIN_TRAIN_ROWS,
    MACRO_SEVERE_VOL_THRESHOLD,
    MACRO_VIX_DEFAULT,
)
from database import get_connection

SECTIONS = ("data", "vix", "training", "inference", "distribution")

# ── formatting helpers ────────────────────────────────────────────────────────

SEP_HEAVY = "═" * 66
SEP_LIGHT = "─" * 66

HIGHER_IS_BETTER = {
    "accuracy":                  True,
    "roc_auc":                   True,
    "log_likelihood_per_sample": True,
    "rmse":                      False,
    "rmse_log1p":                False,
    "neg_root_mean_squared_error": False,
}

COL_W = {
    "trained_at":    19,
    "n_samples":      9,
    "cv_score_mean": 14,
    "cv_score_std":  10,
    "delta":         10,
    "trend":          5,
}


def _section(title: str) -> None:
    print(f"\n{SEP_LIGHT}")
    print(f"  {title}")
    print(SEP_LIGHT)


def _ok(msg: str)   -> None: print(f"  OK   {msg}")
def _warn(msg: str) -> None: print(f"  WARN {msg}")
def _err(msg: str)  -> None: print(f"  ERR  {msg}")
def _info(msg: str) -> None: print(f"       {msg}")


def _table_exists(conn: Any, name: str) -> bool:
    cur = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (name,)
    )
    return cur.fetchone() is not None


def _null_rate(conn: Any, table: str, column: str, where: str = "") -> tuple[int, int]:
    """Returns (null_count, total_count) for column in table."""
    clause = f"WHERE {where}" if where else ""
    cur = conn.execute(f"SELECT COUNT(*) FROM {table} {clause}")
    total = cur.fetchone()[0]
    cur = conn.execute(
        f"SELECT COUNT(*) FROM {table} {clause} AND {column} IS NULL"
        if where else
        f"SELECT COUNT(*) FROM {table} WHERE {column} IS NULL"
    )
    nulls = cur.fetchone()[0]
    return nulls, total


# ── section 1: data availability ─────────────────────────────────────────────

def section_data_availability(conn: Any) -> None:
    _section("1. DATA AVAILABILITY")

    # macro_indicators
    if not _table_exists(conn, "macro_indicators"):
        _err("macro_indicators table missing")
    else:
        cur = conn.execute(
            "SELECT COUNT(*) as n, MIN(date) as lo, MAX(date) as hi FROM macro_indicators"
        )
        row = cur.fetchone()
        n, lo, hi = row["n"], row["lo"], row["hi"]
        print(f"\n  macro_indicators  {n} rows  |  {lo} → {hi}")
        for col in ("us_m2", "us_jobless_claims", "us_high_yield_spread", "us_yield_curve"):
            nulls, total = _null_rate(conn, "macro_indicators", col)
            pct = nulls / total * 100 if total else 0
            line = f"    {col:<28} {nulls} null  ({pct:.1f}%)"
            (_warn if nulls else _ok)(line.strip()) if False else print(f"  {'WARN' if nulls else '  OK'} {line.strip()}")
        if n >= MACRO_HMM_MIN_TRAIN_ROWS:
            _ok(f"HMM training READY  ({n} >= {MACRO_HMM_MIN_TRAIN_ROWS})")
        else:
            _warn(f"HMM training NOT READY  ({n} < {MACRO_HMM_MIN_TRAIN_ROWS} required)")

    # macro_calendar
    print()
    if not _table_exists(conn, "macro_calendar"):
        _err("macro_calendar table missing")
    else:
        cur = conn.execute("SELECT COUNT(*) FROM macro_calendar")
        total_cal = cur.fetchone()[0]
        cur = conn.execute("SELECT COUNT(*) FROM macro_calendar WHERE is_event_passed = 1")
        passed = cur.fetchone()[0]
        cur = conn.execute(
            "SELECT COUNT(*) FROM macro_calendar "
            "WHERE date(event_date) >= date('now') AND date(event_date) <= date('now', '+2 days') "
            "AND is_event_passed = 0"
        )
        upcoming = cur.fetchone()[0]
        print(f"  macro_calendar    {total_cal} rows total  |  {passed} passed  |  {upcoming} upcoming 48h")

        for col in ("forecast_val", "previous_val", "actual_val"):
            nulls, tot = _null_rate(conn, "macro_calendar", col, "is_event_passed = 1")
            pct = nulls / tot * 100 if tot else 0
            status = "WARN" if pct > 10 else "  OK"
            print(f"  {status} {col:<20} {nulls} null  ({pct:.1f}%)  of passed rows")

        cur = conn.execute(
            "SELECT COUNT(*) FROM macro_calendar WHERE is_event_passed = 1 AND post_event_spy_gap IS NOT NULL"
        )
        with_gap = cur.fetchone()[0]
        nulls_gap = passed - with_gap
        pct_gap = nulls_gap / passed * 100 if passed else 0
        status = "WARN" if pct_gap > 30 else "  OK"
        print(f"  {status} post_event_spy_gap   {nulls_gap} null  ({pct_gap:.1f}%)  of passed rows")

        cur = conn.execute(
            "SELECT COUNT(*) FROM macro_calendar "
            "WHERE is_event_passed = 1 AND actual_val IS NOT NULL "
            "AND forecast_val IS NOT NULL AND previous_val IS NOT NULL"
        )
        labelled = cur.fetchone()[0]
        if labelled >= MACRO_CAL_MIN_TRAIN_ROWS:
            _ok(f"RF training READY  ({labelled} labelled >= {MACRO_CAL_MIN_TRAIN_ROWS})")
        else:
            _warn(f"RF training NOT READY  ({labelled} labelled < {MACRO_CAL_MIN_TRAIN_ROWS})")

        if with_gap >= MACRO_CAL_MIN_TRAIN_ROWS:
            _ok(f"XGB training READY  ({with_gap} with spy_gap >= {MACRO_CAL_MIN_TRAIN_ROWS})")
        else:
            _warn(f"XGB training NOT READY  ({with_gap} with spy_gap < {MACRO_CAL_MIN_TRAIN_ROWS})")

    # market_regimes
    print()
    if not _table_exists(conn, "market_regimes"):
        _err("market_regimes table missing")
    else:
        cur = conn.execute(
            "SELECT COUNT(*) as n, MIN(date) as lo, MAX(date) as hi FROM market_regimes"
        )
        row = cur.fetchone()
        n, lo, hi = row["n"], row["lo"], row["hi"]
        print(f"  market_regimes    {n} rows  |  {lo} → {hi}")

        vix_nulls, vix_total = _null_rate(conn, "market_regimes", "vix_close")
        pct = vix_nulls / vix_total * 100 if vix_total else 0
        status = "WARN" if vix_nulls else "  OK"
        print(f"  {status} vix_close            {vix_nulls} null  ({pct:.1f}%)")

        cur = conn.execute(
            "SELECT COUNT(*) FROM market_regimes WHERE ai_hmm_state IS NOT NULL"
        )
        hmm_set = cur.fetchone()[0]
        pct_set = hmm_set / n * 100 if n else 0
        status = "  OK" if pct_set > 0 else "WARN"
        print(f"  {status} ai_hmm_state         {hmm_set} set  ({pct_set:.1f}%)")

        cur = conn.execute(
            "SELECT ai_hmm_state, COUNT(*) as cnt FROM market_regimes "
            "WHERE ai_hmm_state IS NOT NULL GROUP BY ai_hmm_state ORDER BY ai_hmm_state"
        )
        state_rows = cur.fetchall()
        if state_rows:
            dist = "  ".join(f"state {r['ai_hmm_state']}={r['cnt']}" for r in state_rows)
            _info(f"HMM state distribution: {dist}")


# ── section 2: vix join quality ───────────────────────────────────────────────

def section_vix_join_quality(conn: Any) -> None:
    _section("2. VIX JOIN QUALITY")

    if not _table_exists(conn, "macro_calendar") or not _table_exists(conn, "market_regimes"):
        _err("Required tables missing — skipping.")
        return

    cur = conn.execute("""
        SELECT COUNT(*) as total,
               SUM(CASE WHEN r.vix_close IS NOT NULL THEN 1 ELSE 0 END) as matched
        FROM macro_calendar c
        LEFT JOIN market_regimes r ON date(c.event_date) = r.date
        WHERE c.is_event_passed = 1 AND c.post_event_spy_gap IS NOT NULL
    """)
    row = cur.fetchone()
    total, matched = row["total"], row["matched"] or 0
    unmatched = total - matched
    rate = matched / total * 100 if total else 0.0

    print(f"\n  Passed events with spy_gap : {total}")
    print(f"  Matched to market_regimes  : {matched}  ({rate:.1f}%)")
    print(f"  Unmatched (VIX={MACRO_VIX_DEFAULT}) : {unmatched}  ({100-rate:.1f}%)")

    if rate == 0.0:
        _err(
            "0% match — date(event_date) and market_regimes.date formats diverge. "
            f"Entire XGBoost model trains on constant VIX={MACRO_VIX_DEFAULT}."
        )
    elif rate < 50.0:
        _warn(f"Low match rate {rate:.1f}% — check date storage formats.")
    else:
        _ok(f"Join match rate {rate:.1f}%")

    # Show sample dates from each side to help diagnose format issues
    cur = conn.execute(
        "SELECT event_date FROM macro_calendar WHERE is_event_passed = 1 LIMIT 3"
    )
    samples_cal = [r["event_date"] for r in cur.fetchall()]

    cur = conn.execute("SELECT date FROM market_regimes ORDER BY date DESC LIMIT 3")
    samples_reg = [r["date"] for r in cur.fetchall()]

    print(f"\n  Sample macro_calendar.event_date : {samples_cal}")
    print(f"  Sample market_regimes.date       : {samples_reg}")


# ── section 3: training log ───────────────────────────────────────────────────

def _arrow(delta: float, metric: str) -> str:
    higher_better = HIGHER_IS_BETTER.get(metric, True)
    if abs(delta) < 1e-6:
        return "  =="
    improved = (delta > 0) == higher_better
    return ("+ " if improved else "! ") + ("up" if delta > 0 else "dn")


def _log_header(metric: str) -> str:
    return (
        f"  {'Run time':<{COL_W['trained_at']}}  "
        f"{'Samples':>{COL_W['n_samples']}}  "
        f"{f'Mean ({metric})':>{COL_W['cv_score_mean']}}  "
        f"{'Std':>{COL_W['cv_score_std']}}  "
        f"{'Delta':>{COL_W['delta']}}  "
        f"{'':>{COL_W['trend']}}"
    )


def _log_row(row: dict, prev_mean: Optional[float], metric: str) -> str:
    mean = row["cv_score_mean"]
    std  = row["cv_score_std"]
    n    = row["n_samples"]

    delta_str = "     --"
    trend     = "  --"
    if prev_mean is not None and mean is not None:
        delta     = mean - prev_mean
        delta_str = f"{delta:+.4f}"
        trend     = _arrow(delta, metric)

    return (
        f"  {row['trained_at'][:COL_W['trained_at']]:<{COL_W['trained_at']}}  "
        f"{str(n) if n is not None else '?':>{COL_W['n_samples']}}  "
        f"{f'{mean:.4f}' if mean is not None else '    None':>{COL_W['cv_score_mean']}}  "
        f"{f'{std:.4f}' if std is not None else '    None':>{COL_W['cv_score_std']}}  "
        f"{delta_str:>{COL_W['delta']}}  "
        f"{trend:>{COL_W['trend']}}"
    )


def _print_model_log(model_name: str, rows: list[dict], last: Optional[int]) -> None:
    print(f"\n  [{model_name}]")
    if not rows:
        _info("(no training runs logged)")
        return

    metric = rows[0]["score_metric"] or "score"
    if last:
        rows = rows[-last:]

    sep = "  " + "-" * (
        COL_W["trained_at"] + COL_W["n_samples"] + COL_W["cv_score_mean"]
        + COL_W["cv_score_std"] + COL_W["delta"] + COL_W["trend"] + 10
    )
    print(_log_header(metric))
    print(sep)

    prev_mean = None
    for row in rows:
        print(_log_row(row, prev_mean, metric))
        prev_mean = row["cv_score_mean"]

    means = [r["cv_score_mean"] for r in rows if r["cv_score_mean"] is not None]
    if len(means) >= 2:
        overall   = means[-1] - means[0]
        direction = "improved" if (overall > 0) == HIGHER_IS_BETTER.get(metric, True) else "degraded"
        print(sep)
        _info(
            f"Over {len(rows)} run(s): {direction} by {abs(overall):.4f}  "
            f"(first={means[0]:.4f}  last={means[-1]:.4f})"
        )


def section_training_log(conn: Any, model_filter: Optional[str], last: Optional[int]) -> None:
    _section("3. MODEL TRAINING LOG")

    if not _table_exists(conn, "model_training_log"):
        _warn("model_training_log does not exist yet — run bg_init_macro_pipeline once.")
        return

    if model_filter:
        cur = conn.execute(
            "SELECT * FROM model_training_log WHERE model_name = ? ORDER BY trained_at ASC",
            (model_filter,),
        )
        _print_model_log(model_filter, [dict(r) for r in cur.fetchall()], last)
    else:
        cur = conn.execute(
            "SELECT DISTINCT model_name FROM model_training_log ORDER BY model_name"
        )
        models = [r["model_name"] for r in cur.fetchall()]
        if not models:
            _warn("model_training_log is empty — run bg_init_macro_pipeline once.")
            return
        for name in models:
            cur = conn.execute(
                "SELECT * FROM model_training_log WHERE model_name = ? ORDER BY trained_at ASC",
                (name,),
            )
            _print_model_log(name, [dict(r) for r in cur.fetchall()], last)


# ── section 4: inference readiness ───────────────────────────────────────────

def section_inference_readiness(conn: Any) -> None:
    _section("4. INFERENCE READINESS  (next 48h)")

    if not _table_exists(conn, "macro_calendar"):
        _err("macro_calendar table missing — skipping.")
        return

    cur = conn.execute("""
        SELECT event_id, event_date, event_name, forecast_val, previous_val,
               ai_volatility_warning, ai_consensus_miss_prob
        FROM macro_calendar
        WHERE date(event_date) >= date('now')
          AND date(event_date) <= date('now', '+2 days')
          AND is_event_passed = 0
        ORDER BY event_date ASC
    """)
    events = [dict(r) for r in cur.fetchall()]

    if not events:
        _info("No upcoming events in the next 48h.")
        return

    print(f"\n  {len(events)} upcoming event(s):\n")
    hdr = f"  {'event_date':<22} {'event_name':<30} {'forecast':>10} {'warning':>9} {'miss_prob':>10}"
    print(hdr)
    print("  " + "-" * (len(hdr) - 2))

    unscored = 0
    severe   = 0
    for e in events:
        warn = e["ai_volatility_warning"]
        prob = e["ai_consensus_miss_prob"]
        warn_str = f"{warn:.2f}" if warn is not None else "   --"
        prob_str = f"{prob:.2%}" if prob is not None else "      --"
        flag = ""
        if warn is None or warn == 0.0:
            flag = "  (unscored)"
            unscored += 1
        elif warn > MACRO_SEVERE_VOL_THRESHOLD:
            flag = "  !! SEVERE"
            severe += 1
        name = (e["event_name"] or "")[:30]
        print(
            f"  {str(e['event_date']):<22} {name:<30} "
            f"{str(e['forecast_val'] or '--'):>10} {warn_str:>9} {prob_str:>10}{flag}"
        )

    print()
    if unscored:
        _warn(f"{unscored}/{len(events)} events unscored — XGBoost model may not be trained yet.")
    if severe:
        _warn(f"{severe} event(s) exceed MACRO_SEVERE_VOL_THRESHOLD ({MACRO_SEVERE_VOL_THRESHOLD}%).")
    if not unscored and not severe:
        _ok("All upcoming events scored.")


# ── section 5: target distribution ───────────────────────────────────────────

def section_target_distribution(conn: Any) -> None:
    _section("5. TARGET DISTRIBUTION  (post_event_spy_gap)")

    if not _table_exists(conn, "macro_calendar"):
        _err("macro_calendar table missing — skipping.")
        return

    cur = conn.execute("""
        SELECT post_event_spy_gap
        FROM macro_calendar
        WHERE is_event_passed = 1 AND post_event_spy_gap IS NOT NULL
    """)
    gaps = [r["post_event_spy_gap"] for r in cur.fetchall()]

    if not gaps:
        _warn("No post_event_spy_gap values found — pipeline hasn't backfilled gaps yet.")
        return

    n       = len(gaps)
    g_min   = min(gaps)
    g_max   = max(gaps)
    g_mean  = sum(gaps) / n
    sorted_gaps = sorted(gaps)
    mid     = n // 2
    g_med   = sorted_gaps[mid] if n % 2 else (sorted_gaps[mid-1] + sorted_gaps[mid]) / 2
    variance = sum((x - g_mean) ** 2 for x in gaps) / n
    g_std   = variance ** 0.5
    zeros   = sum(1 for g in gaps if g == 0.0)
    severe  = sum(1 for g in gaps if g > MACRO_SEVERE_VOL_THRESHOLD)

    print(f"\n  Rows with gap data : {n}")
    print(f"  min={g_min:.2f}  max={g_max:.2f}  mean={g_mean:.3f}  median={g_med:.3f}  std={g_std:.3f}")
    print(f"  Zeros (gap=0.0)    : {zeros}  ({zeros/n*100:.1f}%)")
    print(f"  Severe (>{MACRO_SEVERE_VOL_THRESHOLD}%)  : {severe}  ({severe/n*100:.1f}%)")

    # Rough histogram in 0.5% buckets
    buckets: dict[int, int] = {}
    for g in gaps:
        b = int(g / 0.5)
        buckets[b] = buckets.get(b, 0) + 1
    print(f"\n  Distribution (0.5% buckets):\n")
    for b in sorted(buckets):
        lo   = b * 0.5
        hi   = lo + 0.5
        bar  = "█" * buckets[b]
        print(f"  {lo:4.1f}–{hi:4.1f}%  {bar:<40} {buckets[b]}")


# ── entry point ───────────────────────────────────────────────────────────────

def main() -> None:
    parser = argparse.ArgumentParser(description="MacroAIEngine comprehensive debug report.")
    parser.add_argument("--model",   help="Training log: filter to one model name")
    parser.add_argument("--last",    type=int, help="Training log: show only N most recent runs")
    parser.add_argument("--section", choices=SECTIONS,
                        help="Run only one section instead of all")
    args = parser.parse_args()

    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    print(f"\n{SEP_HEAVY}")
    print(f"  MACRO AI ENGINE — DEBUG REPORT   {now}")
    print(SEP_HEAVY)

    conn = get_connection()
    try:
        run = args.section
        if not run or run == "data":
            section_data_availability(conn)
        if not run or run == "vix":
            section_vix_join_quality(conn)
        if not run or run == "training":
            section_training_log(conn, args.model, args.last)
        if not run or run == "inference":
            section_inference_readiness(conn)
        if not run or run == "distribution":
            section_target_distribution(conn)
    finally:
        conn.close()

    print(f"\n{SEP_HEAVY}\n")


if __name__ == "__main__":
    main()
