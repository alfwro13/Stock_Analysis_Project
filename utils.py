# Lightweight helpers with no heavy dependencies — safe to import from any module.
import logging
import os
import re
from typing import Any, Optional

logger = logging.getLogger(__name__)

_SAFE_TICKER_PATH_RE = re.compile(r"^[A-Za-z0-9^.=_\-]+$")

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


def check_requirements_drift(req_path: Optional[str] = None) -> list[str]:
    """Compares installed package versions against requirements.txt pins. The only existing
    reinstall path (api_routes_system.py's git-pull -> _requirements_changed_pending -> restart
    flow) tracks that flag in an in-memory global, so it's lost on any restart that doesn't go
    through the Settings 'Pull Latest from GitHub' + Restart buttons — a crash, `systemctl
    restart`, a server reboot, or a manual `git pull` on the server. This runs at every startup
    instead, independent of how the process came up, so drift is never silently invisible."""
    import re
    import importlib.metadata
    from packaging.version import Version, InvalidVersion

    if req_path is None:
        req_path = os.path.join(os.path.dirname(__file__), "requirements.txt")
    mismatches: list[str] = []
    try:
        with open(req_path) as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning("Could not read requirements.txt for drift check: %s", e)
        return mismatches

    for raw_line in lines:
        line = raw_line.split("#", 1)[0].strip()
        if not line:
            continue
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(==|>=)?\s*([A-Za-z0-9_.\-]*)$", line)
        if not m:
            continue
        name, op, pinned = m.group(1), m.group(2), m.group(3)
        try:
            installed = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            mismatches.append(f"{name}: not installed (requirements.txt requires '{line}')")
            continue
        if not op or not pinned:
            continue
        try:
            installed_v, pinned_v = Version(installed), Version(pinned)
        except InvalidVersion:
            continue
        if op == "==" and installed_v != pinned_v:
            mismatches.append(f"{name}: installed {installed}, requirements.txt pins =={pinned}")
        elif op == ">=" and installed_v < pinned_v:
            mismatches.append(f"{name}: installed {installed}, requirements.txt requires >={pinned}")
    return mismatches


def notify_requirements_drift() -> None:
    """Startup check (main.py lifespan) — logs and sends an in-app/log notification when
    installed packages disagree with requirements.txt. Detect-only: does not run pip install,
    to avoid adding a network call and installer run to every boot."""
    mismatches = check_requirements_drift()
    if not mismatches:
        return
    detail = "\n".join(mismatches)
    logger.warning("requirements.txt drift detected: %s", detail)
    from notification_engine import notify
    notify(
        "system_update_status",
        "Warning",
        "Installed packages don't match requirements.txt:\n" + detail
        + "\n\nRun: pip install -r requirements.txt",
        level="warning",
    )


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
    apart. exchange_currently_open, when supplied (time_engine.is_market_open(exchange) /
    market_pulse.is_exchange_open(exchange) for the ticker in question), is the authoritative
    answer: False means the session has genuinely ended, so the bar can never be "still forming"
    regardless of date collision. A caller may omit it only if every path that reaches this
    function is provably gated on the exchange already being confirmed open earlier in the same
    call — e.g. intraday_bottom_engine.run_scan() and intraday_orchestrator.py's scan loop, which
    both `continue` past is_exchange_open()/is_quote_settled() checks before ever calling this
    (verified 2026-07-15) — not merely "usually called during market hours". Found 2026-07-13:
    data_engine.py's nightly bulk/single-ticker fetchers passed no exchange signal at all, so
    every night's Update Pipeline run trimmed that day's just-completed, fully-final close off the
    daily parquet, permanently rolling stock_signals.current_price and quant_signals one trading
    day stale. Found 2026-07-15: market_pulse.fetch_and_save_pulse() had the same gap — it's
    reachable from age-based staleness refreshes and on-demand single-ticker fetches with no
    exchange-open precondition at all, so it now resolves and passes the ticker's own exchange
    state too."""
    if exchange_currently_open is False:
        return False
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()
    return last_daily_date >= last_live_date and last_daily_date >= today
