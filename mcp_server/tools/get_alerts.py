from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from pydantic import Field

from alert_referee_engine import CONFLUENCE_ENGINE, TRAP_MONITOR_ENGINE, get_recent_evaluations
from bull_bear_trap_engine import ALERT_PHASES, phase_label
from constants import MCP_ALERT_TEXT_MAX_CHARS, MCP_ALERTS_MAX_DAYS, MCP_ALERTS_MAX_ROWS
from db_helpers import get_recent_alert_notifications, get_trap_monitor_results, parse_utc_epoch
from utils import clean_display_text

from ..envelope import envelope, iso_from_stored_utc
from ..server import READ_ONLY, server

DaysArg = Annotated[int, Field(ge=1, le=MCP_ALERTS_MAX_DAYS)]

TRAP_SCAN_STALE_AFTER_SECONDS = 3600  # the Market Trap & Recovery Monitor scans every 30 minutes by default

_UNITS = {
    "days": f"size of the look-back window, 1 to {MCP_ALERTS_MAX_DAYS} (the notification log is pruned after 30 days); each list holds at most {MCP_ALERTS_MAX_ROWS} rows, newest or most severe first, and truncated says more matched",
    "at/last_scan_at": "UTC time, YYYY-MM-DDTHH:MM:SSZ",
    "notifications": "alerts the dashboard raised, from the in-app notification log; type is the alert family (Crash, Moonshot, TrapMonitor, Earnings, ...). Only registered alert families are listed; job-status and system messages are left out. status is the log's delivery flag; text is cut to a fixed length. Only alerts routed to the in-app channel appear",
    "trap_monitor": "the current phase of each ticker whose latest Market Trap & Recovery Monitor scan is inside the window and in an alert phase (ACTIVE_SELLOFF, BULL_TRAP_RISK, CAPITULATION_FORMING, BEAR_TRAP_RISK), most severe first. One row per ticker (the latest scan only, not a history). ema_distance_pct is the signed percent of price from the 20-day EMA; rsi is 0-100",
    "referee": "Alert Confidence Referee log per engine, newest first. fire_probability is 0-1 (calibrated confidence that the signal is worth alerting on). would_veto is true when it fell below the veto threshold: in mode 'shadow' the alert was still sent, in mode 'active' it was suppressed. phase is the trap phase (TrapMonitor) or the bullish/bearish direction (Confluence)",
}


def _limited(rows: list) -> dict:
    return {"rows": rows[:MCP_ALERTS_MAX_ROWS], "truncated": len(rows) > MCP_ALERTS_MAX_ROWS}


def _notifications(since: str) -> dict:
    rows = [
        {
            "at": iso_from_stored_utc(row["timestamp"]), "type": row["message_type"], "status": row["status"],
            "text": clean_display_text(row["message_text"], MCP_ALERT_TEXT_MAX_CHARS),
        }
        for row in get_recent_alert_notifications(since, MCP_ALERTS_MAX_ROWS + 1)
    ]
    return _limited(rows)


def _trap_monitor(since: str, missing: list) -> tuple:
    results = get_trap_monitor_results()
    last_scan = results[0]["scan_ts"] if results else None
    if last_scan is None:
        missing.append("Market Trap & Recovery Monitor has produced no results yet")
    elif last_scan < since:
        missing.append(f"Market Trap & Recovery Monitor last scanned at {iso_from_stored_utc(last_scan)}, before the requested window")
    flagged = sorted(
        (r for r in results if r["phase"] in ALERT_PHASES and r["scan_ts"] and r["scan_ts"] >= since),
        key=lambda r: ALERT_PHASES.index(r["phase"]),
    )
    rows = [
        {
            "ticker": r["ticker"], "phase": r["phase"], "phase_label": phase_label(r["phase"]),
            "bull_trap_level": r["bull_trap_level"], "bear_trap_level": r["bear_trap_level"],
            "capitulation_level": r["cap_level"], "wyckoff_level": r["wyckoff_level"],
            "ema_distance_pct": r["ema_distance"], "rsi": r["rsi"], "at": iso_from_stored_utc(r["scan_ts"]),
        }
        for r in flagged
    ]
    return {"last_scan_at": iso_from_stored_utc(last_scan), **_limited(rows)}, parse_utc_epoch(last_scan) or None


def _referee(since: str) -> dict:
    parts = {}
    for engine in (TRAP_MONITOR_ENGINE, CONFLUENCE_ENGINE):
        rows = [
            {
                "ticker": r["ticker"], "phase": r["phase"] or r["direction"], "fire_probability": r["fire_probability"],
                "would_veto": bool(r["vetoed"]), "mode": r["mode"], "at": iso_from_stored_utc(r["scan_ts"]),
            }
            for r in get_recent_evaluations(engine, limit=MCP_ALERTS_MAX_ROWS + 1, since_ts=since)
        ]
        parts[engine] = _limited(rows)
    return parts


@server.tool(name="get_alerts", annotations=READ_ONLY)
def get_alerts(days: DaysArg = 7) -> dict[str, Any]:
    """Recent alerts: those the dashboard raised in the last `days` days (default 7, at most 30), the tickers the Market Trap & Recovery Monitor currently flags with their phase, and the Alert Confidence Referee's confidence for the engines it covers. Reading only: nothing is sent or acknowledged."""
    since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d %H:%M:%S")
    missing: list[str] = []
    trap, last_scan_epoch = _trap_monitor(since, missing)
    return envelope(
        {"days": days, "since": iso_from_stored_utc(since), "notifications": _notifications(since), "trap_monitor": trap, "referee": _referee(since)},
        as_of=last_scan_epoch, units=_UNITS, missing=missing, stale_after_seconds=TRAP_SCAN_STALE_AFTER_SECONDS,
    )
