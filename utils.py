# Lightweight helpers with no heavy dependencies — safe to import from any module.
import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_SAFE_TICKER_PATH_RE = re.compile(r"^[A-Za-z0-9^.=\-]+$")

MERMAID_VERSION = "10.9.1"
MERMAID_URL = f"https://cdn.jsdelivr.net/npm/mermaid@{MERMAID_VERSION}/dist/mermaid.min.js"


def ensure_workflow_assets() -> None:
    """Fetch the vendored Mermaid bundle on first boot; it is gitignored, not committed."""
    target = os.path.join(os.path.dirname(__file__), "static", "js", "mermaid.min.js")
    if os.path.exists(target) and os.path.getsize(target) > 0:
        return
    try:
        import urllib.request
        with urllib.request.urlopen(MERMAID_URL, timeout=15) as resp:
            data = resp.read()
        with open(target, "wb") as f:
            f.write(data)
        logger.info("Fetched Mermaid %s into static/js/.", MERMAID_VERSION)
    except Exception as e:
        logger.warning("Could not fetch Mermaid bundle (%s); Workflow Monitor graph unavailable until fetched.", e)


def normalize_ticker(ticker: str) -> str:
    return str(ticker).strip().upper()


def safe_ticker_filename(ticker: Optional[str]) -> Optional[str]:
    """Ticker validated free of path-traversal characters, or None if unsafe — the single
    guard every HISTORICAL_DIR/INTRADAY_DIR/FUNDAMENTALS_DIR filename built from a ticker
    must pass through, since a ticker can originate from user-typed input (e.g. an account
    transaction) rather than only the trusted market universe."""
    ticker = str(ticker) if ticker is not None else ""
    if not ticker or not _SAFE_TICKER_PATH_RE.match(ticker):
        return None
    return ticker


def ignored_tickers_set(config: Optional[dict] = None) -> set:
    """Normalized Settings-page IGNORED_TICKERS — the single source every Yahoo-touching
    ticker-list builder must filter against, so an ignored ticker is actually ignored everywhere."""
    if config is None:
        from config import load_config
        config = load_config()
    return {normalize_ticker(t) for t in config.get("IGNORED_TICKERS", [])}


def is_synthetic_ticker(ticker: str) -> bool:
    """True for a structurally unfetchable synthetic ticker (TBILL-*, PENSION-*) that has no
    real Yahoo Finance listing and must never reach a Yahoo-touching call."""
    from treasury_bill_engine import parse_tbill_buy_txn_id
    from account_scraper_engine import parse_pension_account_id
    return parse_tbill_buy_txn_id(ticker) is not None or parse_pension_account_id(ticker) is not None


def is_excluded_from_yahoo_fetch(ticker: str, ignored: Optional[set] = None) -> bool:
    """True when `ticker` must never reach a Yahoo Finance-touching call: is_synthetic_ticker()
    or on the Settings-page Ignored Tickers list. Pass a pre-computed `ignored` (from
    ignored_tickers_set()) when filtering many tickers in a loop, to avoid reloading config
    on every call."""
    if is_synthetic_ticker(ticker):
        return True
    if ignored is None:
        ignored = ignored_tickers_set()
    return normalize_ticker(ticker) in ignored


def clamp_beta(raw: Any, lo: float = 0.5, hi: float = 2.0, default: float = 1.0) -> float:
    """Guards against empty strings / SQLite None for the beta column, which raises ValueError inside float()."""
    try:
        if raw is None:
            return default
        return max(lo, min(hi, float(raw)))
    except (TypeError, ValueError):
        return default


def is_daily_bar_still_forming(last_daily_date: Any, last_live_date: Any, exchange_currently_open: Optional[bool] = None) -> bool:
    """True when the daily feed's last date is on/after the live feed's last date AND is today's
    actual calendar date — Yahoo's daily endpoint often returns today's still-forming session as
    the 'close' when queried mid-session. Comparing against the live feed's date alone produces a
    false positive whenever the market is currently closed (pre-market, after-hours, weekend): the
    live feed hasn't produced a new-day bar either, so its last date matches daily's last date even
    though daily has already correctly caught up to a genuinely completed prior close. Requiring
    the daily bar's own date to also be >= real "today" rules that case out. Found 2026-07-08:
    fetch_and_save_data() was silently trimming a just-fetched, fully-closed prior-day bar every
    pre-market morning, permanently discarding that day's verified close.

    A same-UTC-calendar-day fetch that happens *after* the exchange has already closed (e.g. the
    22:30 nightly Update Pipeline) produces the exact same date signature as a genuine mid-session
    fetch — both have daily/live/today all equal — so the date-only check alone cannot tell them
    apart. exchange_currently_open, when supplied (time_engine.is_market_open(exchange) for the
    ticker in question), is the authoritative answer: False means the session has genuinely ended,
    so the bar can never be "still forming" regardless of date collision. Callers that only ever
    run while the exchange is confirmed open (the intraday scanners, already gated upstream) can
    omit it — the date-only check is exact in that context. Found 2026-07-13: data_engine.py's
    nightly bulk/single-ticker fetchers passed no exchange signal at all, so every night's Update
    Pipeline run trimmed that day's just-completed, fully-final close off the daily parquet,
    permanently rolling stock_signals.current_price and quant_signals one trading day stale."""
    if exchange_currently_open is False:
        return False
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()
    return last_daily_date >= last_live_date and last_daily_date >= today
