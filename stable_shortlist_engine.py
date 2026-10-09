from __future__ import annotations

import json
import logging
from collections import Counter
from datetime import datetime, timezone
from typing import Optional

import numpy as np

from config import load_config
from constants import PREDICTION_HORIZON_DAYS
from database import get_connection
from db_helpers import (
    get_first_close_on_or_after,
    get_latest_composite_scores,
    get_latest_quantile_bands,
    get_portfolio_tickers,
    get_sectors,
    get_watchlist_only_tickers,
)
from predicted_movers_engine import SCOPE_PORTFOLIO, SCOPE_WATCHLIST
from utils import is_missing_sector, trading_days_forward

logger = logging.getLogger(__name__)

# GUI name: "Stable Shortlist". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.

SIGNAL_ML = "ml_upside"
SIGNAL_QUANT = "quant_score"
SIGNAL_TYPES = (SIGNAL_ML, SIGNAL_QUANT)
SCOPES = (SCOPE_PORTFOLIO, SCOPE_WATCHLIST)

UNKNOWN_SECTOR = "Unknown"


def load_params() -> dict:
    cfg = load_config()["SCHEDULING"]["STABLE_SHORTLIST"]
    return {
        "TOPK": int(cfg["TOPK"]),
        "N_DROP": int(cfg["N_DROP"]),
        "HOLD_THRESH": int(cfg["HOLD_THRESH"]),
        "SECTOR_CAP": int(cfg["SECTOR_CAP"]),
        "MAX_SIGNAL_AGE_DAYS": int(cfg["MAX_SIGNAL_AGE_DAYS"]),
        "MIN_QUANT_SCORE": int(cfg["MIN_QUANT_SCORE"]),
    }


def sector_bucket(sector: Optional[str]) -> str:
    return UNKNOWN_SECTOR if is_missing_sector(sector) else sector.strip()


def select_members(candidates: list[dict], prior: dict[str, dict], *, topk: int, n_drop: int,
                   hold_thresh: int, sector_cap: int) -> dict[str, dict]:
    """Turnover-controlled top-k pick. candidates: {ticker, sector, signal_value, eligible}; prior: {ticker: {cycles_held}} for the last snapshot's members. Returns {ticker: {selected, reason, rank, cycles_held}}."""
    ranked = sorted((c for c in candidates if c["eligible"]), key=lambda c: (-c["signal_value"], c["ticker"]))
    rank = {c["ticker"]: i for i, c in enumerate(ranked, 1)}
    sector = {c["ticker"]: c["sector"] for c in candidates}
    dropped: dict[str, str] = {}

    carried = sorted((t for t in prior if t in rank), key=rank.get)
    for t in prior:
        if t not in rank:
            dropped[t] = "dropped_ineligible"

    kept: list[str] = []
    count: Counter = Counter()
    for t in carried:
        if count[sector[t]] >= sector_cap:
            dropped[t] = "dropped_sector_cap"
        elif len(kept) >= topk:
            dropped[t] = "dropped_rank"
        else:
            kept.append(t)
            count[sector[t]] += 1

    entered: set[str] = set()
    blocked_cap: set[str] = set()
    for c in ranked:
        if len(kept) >= topk:
            break
        t = c["ticker"]
        if t in kept or t in dropped:
            continue
        if count[c["sector"]] >= sector_cap:
            blocked_cap.add(t)
            continue
        kept.append(t)
        entered.add(t)
        count[c["sector"]] += 1

    swaps = 0
    for c in ranked:
        if swaps >= n_drop:
            break
        t = c["ticker"]
        if t in kept or t in dropped:
            continue
        removable = sorted(
            (m for m in kept if m not in entered and prior[m]["cycles_held"] >= hold_thresh and rank[m] > rank[t]),
            key=rank.get, reverse=True,
        )
        if not removable:
            break
        partner = next((m for m in removable if sector[m] == c["sector"] or count[c["sector"]] < sector_cap), None)
        if partner is None:
            blocked_cap.add(t)
            continue
        kept.remove(partner)
        count[sector[partner]] -= 1
        dropped[partner] = "dropped_rank"
        kept.append(t)
        entered.add(t)
        count[c["sector"]] += 1
        swaps += 1

    decisions: dict[str, dict] = {}
    kept_set = set(kept)
    for c in candidates:
        t = c["ticker"]
        if t in kept_set:
            if t in entered:
                reason, cycles = "entered", 1
            else:
                cycles = prior[t]["cycles_held"] + 1
                protected = prior[t]["cycles_held"] < hold_thresh
                waiting = swaps < n_drop and any(
                    o["ticker"] not in kept_set and rank[o["ticker"]] < rank[t]
                    and (o["sector"] == sector[t] or count[o["sector"]] < sector_cap)
                    for o in ranked
                )
                reason = "blocked_hold_thresh" if protected and waiting else "retained"
            decisions[t] = {"selected": True, "reason": reason, "rank": rank[t], "cycles_held": cycles}
        elif t in dropped:
            decisions[t] = {"selected": False, "reason": dropped[t], "rank": rank.get(t), "cycles_held": 0}
        elif not c["eligible"]:
            decisions[t] = {"selected": False, "reason": "ineligible", "rank": None, "cycles_held": 0}
        else:
            reason = "blocked_sector_cap" if t in blocked_cap else "not_selected"
            decisions[t] = {"selected": False, "reason": reason, "rank": rank[t], "cycles_held": 0}
    for t, reason in dropped.items():
        decisions.setdefault(t, {"selected": False, "reason": reason, "rank": None, "cycles_held": 0})
    return decisions


def _scope_tickers(scope: str) -> list[str]:
    return get_portfolio_tickers() if scope == SCOPE_PORTFOLIO else get_watchlist_only_tickers()


def _cycle_key(now: datetime) -> str:
    year, week, _ = now.isocalendar()
    return f"{year}-W{week:02d}"


def _age_days(signal_date: str, today: str) -> int:
    return int((np.datetime64(today, "D") - np.datetime64(signal_date, "D")).astype(int))


def _ml_signals(tickers: list[str]) -> dict[str, dict]:
    signals = {}
    for row in get_latest_quantile_bands(tickers):
        close = row["close_price"]
        if not close or close <= 0:
            continue
        mid = (row["price_q10"] + row["price_q90"]) / 2.0
        signals[row["ticker"]] = {
            "signal_date": row["date"], "signal_value": (mid / close - 1.0) * 100.0,
            "reference_close": close, "price_q10": row["price_q10"], "price_q90": row["price_q90"],
        }
    return signals


def _quant_signals(tickers: list[str]) -> dict[str, dict]:
    signals = {}
    for row in get_latest_composite_scores(tickers):
        if not row["close_price"] or row["close_price"] <= 0:
            continue
        signals[row["ticker"]] = {
            "signal_date": row["date"], "signal_value": float(row["composite_score"]),
            "reference_close": row["close_price"], "price_q10": None, "price_q90": None,
        }
    return signals


def _signal_version(signal_type: str, signal_as_of: Optional[str]) -> str:
    if signal_type == SIGNAL_QUANT:
        return f"composite_score as of {signal_as_of}"
    try:
        from ml_features import QUANTILE_Q10_PATH, QUANTILE_Q90_PATH
        trained = max(QUANTILE_Q10_PATH.stat().st_mtime, QUANTILE_Q90_PATH.stat().st_mtime)
        return "quantile models saved " + datetime.fromtimestamp(trained, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    except OSError:
        return "quantile models (file time unavailable)"


def _eligibility(signal_type: str, signal: dict, today: str, params: dict) -> Optional[str]:
    """Why a signal cannot qualify (None when it can)."""
    if _age_days(signal["signal_date"], today) > params["MAX_SIGNAL_AGE_DAYS"]:
        return "stale_signal"
    if signal_type == SIGNAL_ML and signal["signal_value"] <= 0:
        return "not_positive"
    if signal_type == SIGNAL_QUANT and signal["signal_value"] < params["MIN_QUANT_SCORE"]:
        return "below_min_score"
    return None


def _load_previous(conn, signal_type: str, scope: str) -> tuple[dict[str, dict], dict[str, int]]:
    snap = conn.execute(
        "SELECT id FROM stable_shortlist_snapshots WHERE signal_type=? AND scope=? ORDER BY id DESC LIMIT 1",
        (signal_type, scope),
    ).fetchone()
    if not snap:
        return {}, {}
    rows = conn.execute(
        "SELECT ticker, selected, rank, cycles_held FROM stable_shortlist_members WHERE snapshot_id=?",
        (snap["id"],),
    ).fetchall()
    prior = {r["ticker"]: {"cycles_held": r["cycles_held"]} for r in rows if r["selected"]}
    prev_rank = {r["ticker"]: r["rank"] for r in rows if r["rank"] is not None}
    return prior, prev_rank


def take_snapshot(signal_type: str, scope: str, params: dict, now: Optional[datetime] = None) -> dict:
    """Writes one immutable shortlist snapshot per signal, list and ISO week; a week that already has one, or has no fresh signal at all, writes nothing."""
    now = now or datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    cycle_key = _cycle_key(now)
    result = {"signal_type": signal_type, "scope": scope, "cycle_key": cycle_key}

    conn = None
    try:
        conn = get_connection()
        exists = conn.execute(
            "SELECT id FROM stable_shortlist_snapshots WHERE signal_type=? AND scope=? AND cycle_key=?",
            (signal_type, scope, cycle_key),
        ).fetchone()
        if exists:
            return {**result, "status": "exists"}

        tickers = _scope_tickers(scope)
        if not tickers:
            return {**result, "status": "empty_scope"}
        signals = (_ml_signals if signal_type == SIGNAL_ML else _quant_signals)(tickers)
        if not any(_age_days(s["signal_date"], today) <= params["MAX_SIGNAL_AGE_DAYS"] for s in signals.values()):
            return {**result, "status": "no_fresh_signals"}

        sectors = get_sectors(list(signals))
        prior, prev_rank = _load_previous(conn, signal_type, scope)
        candidates, ineligible_reason = [], {}
        for ticker, sig in signals.items():
            reason = _eligibility(signal_type, sig, today, params)
            ineligible_reason[ticker] = reason
            candidates.append({"ticker": ticker, "sector": sector_bucket(sectors.get(ticker)),
                               "signal_value": sig["signal_value"], "eligible": reason is None})
        for ticker in prior:
            if ticker not in signals:
                ineligible_reason[ticker] = "left_scope" if ticker not in tickers else "no_signal"
                candidates.append({"ticker": ticker, "sector": UNKNOWN_SECTOR, "signal_value": 0.0, "eligible": False})

        decisions = select_members(
            candidates, prior, topk=params["TOPK"], n_drop=params["N_DROP"],
            hold_thresh=params["HOLD_THRESH"], sector_cap=params["SECTOR_CAP"],
        )
        member_count = sum(1 for d in decisions.values() if d["selected"])
        signal_as_of = max((s["signal_date"] for s in signals.values()), default=None)

        cur = conn.execute(
            """INSERT INTO stable_shortlist_snapshots
               (signal_type, scope, cycle_key, decision_ts, config_json, signal_version, signal_as_of,
                candidate_count, member_count)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (signal_type, scope, cycle_key, now.strftime("%Y-%m-%d %H:%M:%S"), json.dumps(params, sort_keys=True),
             _signal_version(signal_type, signal_as_of), signal_as_of, len(candidates), member_count),
        )
        snapshot_id = cur.lastrowid
        for cand in candidates:
            ticker = cand["ticker"]
            sig = signals.get(ticker)
            dec = decisions[ticker]
            reason = ineligible_reason[ticker]
            tracked = sig is not None and reason != "stale_signal"
            conn.execute(
                """INSERT INTO stable_shortlist_members
                   (snapshot_id, ticker, sector, signal_date, signal_value, reference_close, price_q10, price_q90,
                    rank, prev_rank, eligible, ineligible_reason, selected, reason, cycles_held, target_date)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (snapshot_id, ticker, cand["sector"],
                 sig["signal_date"] if sig else None, sig["signal_value"] if sig else None,
                 sig["reference_close"] if sig else None, sig["price_q10"] if sig else None,
                 sig["price_q90"] if sig else None,
                 dec["rank"], prev_rank.get(ticker), int(cand["eligible"]), reason,
                 int(dec["selected"]), dec["reason"], dec["cycles_held"],
                 trading_days_forward(sig["signal_date"], PREDICTION_HORIZON_DAYS) if tracked else None),
            )
        conn.commit()
        return {**result, "status": "created", "snapshot_id": snapshot_id,
                "candidates": len(candidates), "members": member_count}
    except Exception as e:
        logger.error("take_snapshot(%s, %s) failed: %s", signal_type, scope, e)
        if conn:
            conn.rollback()
        return {**result, "status": "error", "message": str(e)}
    finally:
        if conn:
            conn.close()


def resolve_outcomes() -> int:
    """Fills the forward outcome of every unresolved snapshot row whose target date has passed (the whole backlog each run, so a missed run strands nothing)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = None
    try:
        conn = get_connection()
        pending = conn.execute(
            """SELECT snapshot_id, ticker, reference_close, price_q10, price_q90, target_date
               FROM stable_shortlist_members
               WHERE actual_price IS NULL AND target_date IS NOT NULL AND target_date <= ?
                 AND reference_close > 0""",
            (today,),
        ).fetchall()
        resolved = 0
        for row in pending:
            future = get_first_close_on_or_after(conn, row["ticker"], row["target_date"])
            if not future or not future["close_price"]:
                continue
            actual, ref = future["close_price"], row["reference_close"]
            direction = within_band = None
            if row["price_q10"] is not None:
                mid = (row["price_q10"] + row["price_q90"]) / 2.0
                direction = 1 if actual != ref and np.sign(mid - ref) == np.sign(actual - ref) else 0
                within_band = 1 if row["price_q10"] <= actual <= row["price_q90"] else 0
            conn.execute(
                """UPDATE stable_shortlist_members
                   SET actual_price=?, actual_date=?, forward_return_pct=?, direction_correct=?, within_band_correct=?
                   WHERE snapshot_id=? AND ticker=?""",
                (actual, future["date"], (actual / ref - 1.0) * 100.0, direction, within_band,
                 row["snapshot_id"], row["ticker"]),
            )
            resolved += 1
        conn.commit()
        return resolved
    except Exception as e:
        logger.error("resolve_outcomes failed: %s", e)
        if conn:
            conn.rollback()
        return 0
    finally:
        if conn:
            conn.close()


def run_stable_shortlist() -> dict:
    """Resolves past outcomes, then takes this week's snapshot for both signals and both lists."""
    params = load_params()
    resolved = resolve_outcomes()
    snapshots = [take_snapshot(signal, scope, params) for signal in SIGNAL_TYPES for scope in SCOPES]
    return {"resolved": resolved, "snapshots": snapshots}
