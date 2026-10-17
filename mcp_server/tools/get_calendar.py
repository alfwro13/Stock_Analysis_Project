import time
from datetime import datetime, timedelta, timezone
from typing import Annotated, Any

from pydantic import Field

from constants import MCP_CALENDAR_MAX_DAYS, MCP_CALENDAR_MAX_ROWS
from db_helpers import (
    get_ex_dividend_dates, get_next_earnings_dates, get_portfolio_tickers, get_upcoming_macro_events,
    get_watchlist_only_tickers, parse_utc_epoch,
)
from scheduler_engine import get_all_job_last_runs
from utils import is_iso_date

from ..envelope import NIGHTLY_STALE_AFTER_SECONDS, envelope, iso_from_stored_utc, iso_utc
from ..server import READ_ONLY, server

DaysArg = Annotated[int, Field(ge=1, le=MCP_CALENDAR_MAX_DAYS)]

MACRO_CALENDAR_JOB_ID = "macro_calendar_job"
MACRO_STALE_AFTER_SECONDS = 2 * 86400  # the Macro Calendar update runs daily

_STAMP = "%Y-%m-%d %H:%M:%S"

_UNITS = {
    "days": f"size of the look-ahead window, 1 to {MCP_CALENDAR_MAX_DAYS}; today counts as day 0 and the last day is included; each list holds at most {MCP_CALENDAR_MAX_ROWS} rows, soonest first, and truncated says more matched",
    "earnings": "next earnings date of each held (list portfolio) or watched-only (list watchlist) ticker, from the nightly Quant Scan (Yahoo); date is YYYY-MM-DD, days_until counts from today (UTC). A ticker with no known date is left out. A date Yahoo has not confirmed can move",
    "ex_dividends": "ex-dividend date of the same tickers, as Yahoo last reported it: only dates from today on are listed, and a company that has not announced its next ex-date is left out, so an empty list does not mean no dividend is coming. date is YYYY-MM-DD",
    "macro_events": "High-impact US (USD) and UK (GBP) releases and central-bank decisions, from the Macroeconomic Automation Schedulers (Calendar) job, which reads a feed of the current week only. at is UTC time YYYY-MM-DDTHH:MM:SSZ. forecast, previous and actual are in the release's own unit (percent, thousands, an index level); actual is null until released",
    "macro_events.ai_consensus_miss_probability": "0-1: the dashboard's model of how likely the release is to miss the consensus forecast; null when the model has not scored the event (it scores the next 48 hours)",
    "macro_events.ai_volatility_forecast_pct": "the dashboard's model of the absolute S&P 500 move after the release, in percent, direction unknown; null with the probability",
    "<part>.as_of": "the oldest source time behind that part (the last update of the stock rows, or the last Macro Calendar run); stale is true when it is older than 5 days for tickers, 2 days for macro events",
}


def _limited(rows: list) -> dict:
    return {"rows": rows[:MCP_CALENDAR_MAX_ROWS], "truncated": len(rows) > MCP_CALENDAR_MAX_ROWS}


def _ticker_dates(readings: dict, field: str, lists: dict, updated: dict, today: str, last_day: str) -> tuple:
    rows, epochs = [], []
    for ticker, reading in readings.items():
        day = reading[field]
        if not is_iso_date(day) or not today <= day <= last_day:
            continue
        rows.append({
            "ticker": ticker, "company_name": reading["company_name"], "list": lists[ticker], "date": day,
            "days_until": (datetime.strptime(day, "%Y-%m-%d") - datetime.strptime(today, "%Y-%m-%d")).days,
        })
        epochs.append(parse_utc_epoch(updated.get(ticker)) or None)
    rows.sort(key=lambda row: (row["date"], row["ticker"]))
    return rows, epochs


def _part(epochs: list, rows: list, stale_after: int) -> dict:
    known = [e for e in epochs if e is not None]
    as_of = min(known) if known else None
    return {"as_of": None if as_of is None else iso_utc(as_of), "stale": as_of is not None and time.time() - as_of > stale_after, **_limited(rows)}


def _macro(start: str, end: str, last_day: str, missing: list) -> tuple:
    result = get_upcoming_macro_events(start, end)
    last_run = (get_all_job_last_runs().get(MACRO_CALENDAR_JOB_ID) or {}).get("last_run")
    run_epoch = parse_utc_epoch(last_run) or None
    latest = result["latest_event_date"]
    if run_epoch is None:
        missing.append("The Macroeconomic Automation Schedulers (Calendar) job has no recorded run, so the age of the macro events is not known")
    if latest is None:
        missing.append("The macro calendar holds no events yet")
    elif latest[:10] < last_day:
        missing.append(f"The macro calendar only lists events up to {iso_from_stored_utc(latest)}; later releases in the window are not known")
    rows = [
        {
            "at": iso_from_stored_utc(e["event_date"]), "currency": e["currency"], "impact": e["impact"], "name": e["event_name"],
            "forecast": e["forecast_val"], "previous": e["previous_val"], "actual": e["actual_val"],
            "ai_consensus_miss_probability": e["ai_consensus_miss_prob"],
            "ai_volatility_forecast_pct": None if e["ai_consensus_miss_prob"] is None else e["ai_volatility_warning"],
        }
        for e in result["events"]
    ]
    part = {"as_of": None if run_epoch is None else iso_utc(run_epoch), "stale": run_epoch is None or time.time() - run_epoch > MACRO_STALE_AFTER_SECONDS, **_limited(rows)}
    return part, run_epoch


@server.tool(name="get_calendar", annotations=READ_ONLY)
def get_calendar(days: DaysArg = 7) -> dict[str, Any]:
    """What is coming up in the next `days` days (default 7, at most 30): earnings and ex-dividend dates of the Portfolio and Watchlist tickers, and the High-impact US and UK macro releases (central-bank decisions, CPI, jobs) with forecast, previous value and the dashboard's surprise model. Reading only. The macro calendar covers the current week, so later releases are reported as missing."""
    now = datetime.now(timezone.utc)
    today = now.strftime("%Y-%m-%d")
    last_day = (now + timedelta(days=days)).strftime("%Y-%m-%d")
    lists = {**{t: "watchlist" for t in get_watchlist_only_tickers()}, **{t: "portfolio" for t in get_portfolio_tickers()}}
    tickers = sorted(lists)
    missing: list[str] = []
    ex_dividends = get_ex_dividend_dates(tickers)
    updated = {ticker: reading["last_updated"] for ticker, reading in ex_dividends.items()}
    earnings_rows, earnings_epochs = _ticker_dates(get_next_earnings_dates(tickers), "next_earnings_date", lists, updated, today, last_day)
    ex_div_rows, ex_div_epochs = _ticker_dates(ex_dividends, "ex_dividend_date", lists, updated, today, last_day)
    macro, macro_epoch = _macro(now.replace(hour=0, minute=0, second=0, microsecond=0).strftime(_STAMP), f"{last_day} 23:59:59", last_day, missing)
    data = {
        "days": days, "from": today, "to": last_day,
        "earnings": _part(earnings_epochs, earnings_rows, NIGHTLY_STALE_AFTER_SECONDS),
        "ex_dividends": _part(ex_div_epochs, ex_div_rows, NIGHTLY_STALE_AFTER_SECONDS),
        "macro_events": macro,
    }
    known = [e for e in (*earnings_epochs, *ex_div_epochs, macro_epoch) if e is not None]
    return envelope(data, as_of=min(known) if known else None, units=_UNITS, missing=missing, stale_after_seconds=NIGHTLY_STALE_AFTER_SECONDS)
