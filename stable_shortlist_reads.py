from __future__ import annotations

import json
import logging
from datetime import datetime
from typing import Optional

import numpy as np

import time_engine
from config import load_config
from database import get_connection
from db_helpers import get_company_names
from stable_shortlist_engine import SIGNAL_ML, SIGNAL_QUANT

logger = logging.getLogger(__name__)

# GUI name: "Stable Shortlist". Read side of stable_shortlist_engine.py: the page/API payload, the forward-only track record and the Portfolio/Watchlist column values.

SIGNAL_LABELS = {SIGNAL_ML: "ML Upside Shortlist", SIGNAL_QUANT: "Quant Score Shortlist"}

REASON_LABELS = {
    "entered": "Entered the list",
    "retained": "Kept on the list",
    "blocked_hold_thresh": "Kept — a better-ranked name is waiting, but this one is inside its minimum hold",
    "dropped_rank": "Dropped — replaced by a better-ranked name",
    "dropped_ineligible": "Dropped — no longer qualifies",
    "dropped_sector_cap": "Dropped — its sector was over the sector cap",
    "blocked_sector_cap": "Not added — its sector is already at the sector cap",
    "not_selected": "Qualifies but not selected",
    "ineligible": "Does not qualify",
}
INELIGIBLE_LABELS = {
    "stale_signal": "Signal too old",
    "not_positive": "Predicted upside is not positive",
    "below_min_score": "Quant score below the minimum",
    "no_signal": "No signal on file",
    "left_scope": "No longer in this list",
}


def _mean(values: list[float]) -> Optional[float]:
    return float(np.mean(values)) if values else None


def get_evaluation(signal_type: str, scope: str) -> dict:
    """Forward-only track record: each resolved snapshot's equal-weight member return versus the average of that snapshot's other tracked names."""
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            """SELECT s.id, s.cycle_key, s.decision_ts, m.selected, m.forward_return_pct,
                      m.direction_correct, m.within_band_correct
               FROM stable_shortlist_snapshots s
               JOIN stable_shortlist_members m ON m.snapshot_id = s.id
               WHERE s.signal_type=? AND s.scope=? AND m.forward_return_pct IS NOT NULL
               ORDER BY s.id""",
            (signal_type, scope),
        ).fetchall()
        pending = conn.execute(
            """SELECT COUNT(DISTINCT s.id) AS n FROM stable_shortlist_snapshots s
               JOIN stable_shortlist_members m ON m.snapshot_id = s.id
               WHERE s.signal_type=? AND s.scope=? AND m.selected=1 AND m.target_date IS NOT NULL
                 AND m.actual_price IS NULL""",
            (signal_type, scope),
        ).fetchone()["n"]
    except Exception as e:
        logger.error("get_evaluation failed: %s", e)
        return {"snapshots": [], "summary": {}}
    finally:
        if conn:
            conn.close()

    by_snapshot: dict[int, dict] = {}
    for r in rows:
        snap = by_snapshot.setdefault(r["id"], {
            "cycle_key": r["cycle_key"], "decision_ts": r["decision_ts"], "members": [], "others": [],
            "direction": [], "band": [],
        })
        (snap["members"] if r["selected"] else snap["others"]).append(r["forward_return_pct"])
        if r["selected"] and r["direction_correct"] is not None:
            snap["direction"].append(r["direction_correct"])
            snap["band"].append(r["within_band_correct"])

    snapshots = []
    for snap in by_snapshot.values():
        member_avg, other_avg = _mean(snap["members"]), _mean(snap["others"])
        snapshots.append({
            "cycle_key": snap["cycle_key"], "decision_ts": snap["decision_ts"],
            "member_count": len(snap["members"]), "member_avg_return_pct": member_avg,
            "other_count": len(snap["others"]), "other_avg_return_pct": other_avg,
            "excess_pct": member_avg - other_avg if member_avg is not None and other_avg is not None else None,
        })
    comparable = [s["excess_pct"] for s in snapshots if s["excess_pct"] is not None]
    direction = [d for s in by_snapshot.values() for d in s["direction"]]
    band = [b for s in by_snapshot.values() for b in s["band"]]
    summary = {
        "snapshots_evaluated": len(snapshots),
        "snapshots_pending": pending,
        "avg_member_return_pct": _mean([s["member_avg_return_pct"] for s in snapshots if s["member_avg_return_pct"] is not None]),
        "avg_other_return_pct": _mean([s["other_avg_return_pct"] for s in snapshots if s["other_avg_return_pct"] is not None]),
        "avg_excess_pct": _mean(comparable),
        "snapshots_beating_others": sum(1 for x in comparable if x > 0),
        "snapshots_comparable": len(comparable),
        "direction_accuracy": _mean(direction) * 100.0 if direction else None,
        "within_band_accuracy": _mean(band) * 100.0 if band else None,
    }
    return {"snapshots": snapshots[::-1], "summary": summary}


def get_shortlist(signal_type: str, scope: str) -> dict:
    """The latest snapshot for one list: members, what changed against the previous snapshot, the other candidates with their reasons, and the forward-only evaluation."""
    cfg = load_config()["SCHEDULING"]["STABLE_SHORTLIST"]
    payload = {
        "signal_type": signal_type, "scope": scope, "label": SIGNAL_LABELS[signal_type],
        "schedule": {"enabled": bool(cfg.get("ENABLED")), "days": cfg.get("DAYS"), "time": cfg.get("TIME")},
        "snapshot": None, "members": [], "changes": [], "others": [],
        "evaluation": get_evaluation(signal_type, scope),
    }
    conn = None
    try:
        conn = get_connection()
        snap = conn.execute(
            "SELECT * FROM stable_shortlist_snapshots WHERE signal_type=? AND scope=? ORDER BY id DESC LIMIT 1",
            (signal_type, scope),
        ).fetchone()
        if not snap:
            return payload
        rows = [dict(r) for r in conn.execute(
            "SELECT * FROM stable_shortlist_members WHERE snapshot_id=?", (snap["id"],)
        ).fetchall()]
    except Exception as e:
        logger.error("get_shortlist failed: %s", e)
        return payload
    finally:
        if conn:
            conn.close()

    names = get_company_names([r["ticker"] for r in rows])
    for r in rows:
        r["company_name"] = names.get(r["ticker"])
        r["reason_label"] = REASON_LABELS.get(r["reason"], r["reason"])
        r["ineligible_label"] = INELIGIBLE_LABELS.get(r["ineligible_reason"]) if r["ineligible_reason"] else None
    snapshot = dict(snap)
    snapshot["config"] = json.loads(snapshot.pop("config_json"))
    snapshot["decision_local"] = time_engine.fmt_datetime(datetime.strptime(snapshot["decision_ts"], "%Y-%m-%d %H:%M:%S"))
    payload["snapshot"] = snapshot
    payload["members"] = sorted((r for r in rows if r["selected"]), key=lambda r: (r["rank"], r["ticker"]))
    payload["changes"] = sorted(
        (r for r in rows if r["reason"] == "entered" or r["reason"].startswith("dropped_")),
        key=lambda r: (r["reason"] != "entered", r["ticker"]),
    )
    payload["others"] = sorted(
        (r for r in rows if not r["selected"] and not r["reason"].startswith("dropped_")),
        key=lambda r: (r["rank"] is None, r["rank"] or 0, r["ticker"]),
    )
    return payload


def get_column_values(tickers: list[str]) -> dict[str, dict]:
    """{ticker: {ml_shortlist_member, ml_shortlist_rank, quant_shortlist_member, quant_shortlist_rank}} from each list's latest snapshot, for the optional Portfolio/Watchlist columns."""
    if not tickers:
        return {}
    conn = None
    try:
        conn = get_connection()
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"""SELECT s.signal_type, s.decision_ts, m.ticker, m.selected, m.rank
                FROM stable_shortlist_snapshots s
                JOIN stable_shortlist_members m ON m.snapshot_id = s.id
                WHERE s.id IN (SELECT MAX(id) FROM stable_shortlist_snapshots GROUP BY signal_type, scope)
                  AND m.ticker IN ({placeholders})
                ORDER BY s.decision_ts""",
            tickers,
        ).fetchall()
    except Exception as e:
        logger.error("stable shortlist get_column_values failed: %s", e)
        return {}
    finally:
        if conn:
            conn.close()
    prefix = {SIGNAL_ML: "ml_shortlist", SIGNAL_QUANT: "quant_shortlist"}
    values: dict[str, dict] = {}
    for r in rows:
        values.setdefault(r["ticker"], {}).update({
            f"{prefix[r['signal_type']]}_member": r["selected"],
            f"{prefix[r['signal_type']]}_rank": r["rank"],
        })
    return values
