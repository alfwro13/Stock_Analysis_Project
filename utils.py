# Lightweight helpers with no heavy dependencies — safe to import from any module.
import json
import logging
import math
import os
import re
import shlex
import stat
import sys
import tempfile
import unicodedata
from contextlib import contextmanager
from contextvars import ContextVar
from datetime import date, datetime, timezone
from pathlib import Path
from time import perf_counter
from typing import Any, Optional
from urllib.parse import quote, urlsplit

logger = logging.getLogger(__name__)

_request_stages: ContextVar[list[tuple[str, float]] | None] = ContextVar("request_stages", default=None)
_request_counts: ContextVar[dict[str, int] | None] = ContextVar("request_counts", default=None)


@contextmanager
def measure_request_stage(name: str):
    stages = _request_stages.get()
    if stages is None:
        yield
        return
    started = perf_counter()
    try:
        yield
    finally:
        stages.append((name, (perf_counter() - started) * 1000))


def count_request_event(name: str) -> None:
    counts = _request_counts.get()
    if counts is not None:
        counts[name] = counts.get(name, 0) + 1


def time_request_call(stage_name: str, function, *args, **kwargs):
    with measure_request_stage(stage_name):
        return function(*args, **kwargs)


_SAFE_TICKER_PATH_RE = re.compile(r"[A-Za-z0-9^.=_\-]{1,64}")
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")
_CURRENCY_CODE_RE = re.compile(r"[A-Za-z]{3}")
_ISO_DATE_RE = re.compile(r"[0-9]{4}-[0-9]{2}-[0-9]{2}")
_FILENAME_UNSAFE_RE = re.compile(r'[\s\\/:*?"<>|;]+')
_FILENAME_ASCII_UNSAFE_RE = re.compile(r"[^A-Za-z0-9.\-]+")
_FILENAME_STEM_MAX_CHARS = 64


def requirements_lock_path() -> Path:
    return Path(__file__).resolve().parent / "requirements.lock"


def pip_install_command() -> list[str]:
    """The one reinstall command; interpreter-based, so it works when venv/bin/pip or `activate` still point at a moved venv."""
    return [sys.executable, "-m", "pip", "install", "--require-hashes", "-r", str(requirements_lock_path())]


def check_requirements_drift(req_path: Optional[str] = None) -> list[str]:
    """Installed-vs-requirements.lock mismatches; read from disk, so it survives the in-memory pull flag being lost on a restart."""
    import importlib.metadata
    from packaging.markers import InvalidMarker, Marker
    from packaging.utils import canonicalize_name
    from packaging.version import Version, InvalidVersion

    if req_path is None:
        req_path = str(requirements_lock_path())
    file_name = os.path.basename(req_path)
    mismatches: list[str] = []
    try:
        with open(req_path) as f:
            lines = f.readlines()
    except OSError as e:
        logger.warning("Could not read %s for drift check: %s", file_name, e)
        return mismatches

    installed_versions = {}
    for dist in importlib.metadata.distributions():
        metadata = dist.metadata
        if metadata["Name"]:
            installed_versions[canonicalize_name(metadata["Name"])] = metadata["Version"]

    for raw_line in lines:
        line = raw_line.split("#", 1)[0].strip().rstrip("\\").strip()
        if not line or line.startswith("-"):
            continue
        requirement, _, marker = line.partition(";")
        if marker.strip():
            try:
                if not Marker(marker.strip()).evaluate():
                    continue
            except InvalidMarker:
                pass
        m = re.match(r"^([A-Za-z0-9_.\-]+)\s*(==|>=)?\s*([A-Za-z0-9_.\-]*)$", requirement.strip())
        if not m:
            continue
        name, op, pinned = m.group(1), m.group(2), m.group(3)
        installed = installed_versions.get(canonicalize_name(name))
        if installed is None:
            mismatches.append(f"{name}: not installed ({file_name} requires '{requirement.strip()}')")
            continue
        if not op or not pinned:
            continue
        try:
            installed_v, pinned_v = Version(installed), Version(pinned)
        except InvalidVersion:
            continue
        if op == "==" and installed_v != pinned_v:
            mismatches.append(f"{name}: installed {installed}, {file_name} pins =={pinned}")
        elif op == ">=" and installed_v < pinned_v:
            mismatches.append(f"{name}: installed {installed}, {file_name} requires >={pinned}")
    return mismatches


def notify_requirements_drift() -> None:
    """Startup check (main.py lifespan) — logs and sends an in-app/log notification when
    installed packages disagree with requirements.lock. Detect-only: does not run pip install,
    to avoid adding a network call and installer run to every boot."""
    mismatches = check_requirements_drift()
    if not mismatches:
        return
    detail = "\n".join(mismatches)
    logger.warning("requirements.lock drift detected: %s", detail)
    from notification_engine import notify
    notify(
        "system_update_status",
        "Warning",
        "Installed packages don't match requirements.lock:\n" + detail
        + "\n\nRestart the service from Settings to install them, or run this in a shell on the server "
        "and then restart the service:\n" + shlex.join(pip_install_command()),
        level="warning",
    )


def harden_runtime_permissions(base_dir: Any) -> None:
    """Startup pass (main.py lifespan): project dir 0700, .env/config.json 0600, no group/other write bit under data/ and models/."""
    base = Path(base_dir)
    changed = failed = 0
    first_error = ""

    def restrict(path: Any, target_mode) -> None:
        nonlocal changed, failed, first_error
        try:
            st = os.lstat(path)
            if stat.S_ISLNK(st.st_mode):
                return
            current = stat.S_IMODE(st.st_mode)
            target = target_mode(current)
            if target != current:
                os.chmod(path, target)
                changed += 1
        except FileNotFoundError:
            return
        except OSError as e:
            failed += 1
            first_error = first_error or f"{path}: {e}"

    def drop_group_other_write(mode: int) -> int:
        return mode & ~0o022

    restrict(base, lambda mode: 0o700)
    restrict(base / ".env", lambda mode: 0o600)
    restrict(base / "config.json", lambda mode: 0o600)
    for sub in ("data", "models"):
        root = base / sub
        if not root.is_dir():
            continue
        restrict(root, drop_group_other_write)
        for dirpath, dirnames, filenames in os.walk(root):
            for name in dirnames + filenames:
                restrict(os.path.join(dirpath, name), drop_group_other_write)

    if changed:
        logger.info("Tightened file permissions on %d path(s).", changed)
    if failed:
        logger.warning("Could not tighten permissions on %d path(s); first: %s", failed, first_error)


def normalize_ticker(ticker: str) -> str:
    return str(ticker).strip().upper()


def safe_ticker_filename(ticker: Optional[str]) -> Optional[str]:
    """Ticker validated free of path-traversal characters and at most 64 long (longer file names
    raise ENAMETOOLONG), or None if unsafe — the single guard every HISTORICAL_DIR/INTRADAY_DIR/
    FUNDAMENTALS_DIR filename built from a ticker must pass through, since a ticker can originate
    from user-typed input (e.g. an account transaction) rather than only the trusted market universe."""
    ticker = str(ticker) if ticker is not None else ""
    if not ticker or not _SAFE_TICKER_PATH_RE.fullmatch(ticker):
        return None
    return ticker


TICKER_FORMAT_RULE = "must be 1-64 characters from A-Z, a-z, 0-9 and ^ . = _ -"
INVALID_TICKER_MESSAGE = f"ticker {TICKER_FORMAT_RULE}"


def is_valid_ticker(value: Any) -> bool:
    """The entry-point form of safe_ticker_filename(): one pattern for what may be stored, case kept so lowercase Ghostfolio asset UUIDs pass."""
    return isinstance(value, str) and safe_ticker_filename(value) is not None


def csv_safe_cell(value: Any) -> Any:
    """Text that starts with = + - @ TAB or CR runs as a formula in Excel/LibreOffice, so it gets a leading apostrophe; numbers and None pass through."""
    if isinstance(value, str) and value.startswith(("=", "+", "-", "@", "\t", "\r")):
        return "'" + value
    return value


def attachment_content_disposition(stem: str, suffix: str) -> str:
    """Download header for a name taken from user text: whitespace, separators, quotes, ';' and control/format characters (CR/LF, bidi overrides) become '_' or vanish, the stem is capped, and a non-ASCII name travels in RFC 5987 `filename*` beside an ASCII `filename`."""
    cleaned = _FILENAME_UNSAFE_RE.sub("_", str(stem))
    cleaned = "".join(ch for ch in cleaned if unicodedata.category(ch) not in ("Cc", "Cf"))
    cleaned = cleaned.lstrip(".")[:_FILENAME_STEM_MAX_CHARS]
    ascii_stem = _FILENAME_ASCII_UNSAFE_RE.sub("_", cleaned).strip("_") or "download"
    header = f'attachment; filename="{ascii_stem}{suffix}"'
    if cleaned and cleaned != ascii_stem:
        header += f"; filename*=UTF-8''{quote(cleaned + suffix, safe='')}"
    return header


def clean_display_text(value: Any, max_chars: int) -> Optional[str]:
    """One-line text for a third-party string: whitespace runs (incl. line breaks) become one space, control/format characters (bidi overrides, zero-width) are dropped, and it is cut to `max_chars` with an ellipsis; None when nothing is left."""
    if value is None:
        return None
    cleaned = "".join(ch for ch in " ".join(str(value).split()) if unicodedata.category(ch) not in ("Cc", "Cf")).strip()
    if not cleaned:
        return None
    return cleaned if len(cleaned) <= max_chars else cleaned[:max_chars - 1].rstrip() + "…"


def safe_local_path(value: Optional[str]) -> Optional[str]:
    """Same-origin relative path or None — browsers drop tab/CR/LF before parsing a URL, so '/<TAB>/host' must not pass as local."""
    if not value or not value.startswith("/") or value.startswith("//") or "\\" in value:
        return None
    if _CONTROL_CHARS_RE.search(value):
        return None
    return value


def safe_http_url(value: Any) -> Optional[str]:
    """An absolute http(s) URL with a host, or None — the guard for a third-party link (Yahoo `website`) before it becomes an href, where `javascript:` would otherwise be clickable."""
    if not isinstance(value, str) or _CONTROL_CHARS_RE.search(value):
        return None
    value = value.strip()
    try:
        parts = urlsplit(value)
    except ValueError:
        return None
    if parts.scheme not in ("http", "https") or not parts.hostname:
        return None
    return value


def is_currency_code(value: Any) -> bool:
    """Three ASCII letters with case kept, so the GBp pence code passes — the guard for a currency taken from a request or a CSV before it is stored or put in a pair name."""
    return isinstance(value, str) and _CURRENCY_CODE_RE.fullmatch(value) is not None


def is_iso_date(value: Any) -> bool:
    """A real calendar date written exactly YYYY-MM-DD, the only form the ledger's string comparisons and strptime calls handle."""
    if not isinstance(value, str) or _ISO_DATE_RE.fullmatch(value) is None:
        return False
    try:
        date.fromisoformat(value)
    except ValueError:
        return False
    return True


def write_parquet_atomic(df: Any, path: Any) -> None:
    """Atomic replace, so a reader never meets a half-written file; the temp name never ends in .parquet, so a crash cannot leave a file that looks like a ticker."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
        df.to_parquet(temporary, engine="pyarrow")
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def has_cached_fundamentals(ticker: Optional[str]) -> bool:
    """Whether this ticker's raw Yahoo `.info` dump exists under FUNDAMENTALS_DIR. Presence in
    `asset_profiles` does not imply it — the universe scrape writes profiles without ever fetching
    the dump — so anything that would otherwise analyze the ticker with an empty `info` (and fall
    back on 'USD'/'EQUITY'/'Unknown' defaults) must gate on this, not on the profile row."""
    from config import FUNDAMENTALS_DIR
    safe_ticker = safe_ticker_filename(ticker)
    if not safe_ticker:
        return False
    return (FUNDAMENTALS_DIR / f"{safe_ticker}.json").exists()


def cached_fundamentals_extra(ticker: Optional[str]) -> Optional[dict]:
    """Raw numbers from the ticker's `.info` dump under FUNDAMENTALS_DIR, or None when the file is missing, unsafe or unreadable. Money fields are in Yahoo's units: `market_cap` in the quote currency's major unit (GBP for a GBp quote), the rest in `financial_currency`."""
    from config import FUNDAMENTALS_DIR
    safe_ticker = safe_ticker_filename(ticker)
    if not safe_ticker:
        return None
    root = os.path.realpath(FUNDAMENTALS_DIR)
    path = os.path.realpath(os.path.join(root, f"{safe_ticker}.json"))
    if not path.startswith(root + os.sep) or not os.path.exists(path):
        return None
    try:
        with open(path) as f:
            d = json.load(f)
        ex_dividend_date = None
        ex_dividend_ts = d.get("exDividendDate")
        if ex_dividend_ts:
            try:
                ex_dividend_date = datetime.fromtimestamp(ex_dividend_ts, tz=timezone.utc).strftime("%Y-%m-%d")
            except Exception:
                logger.warning("Could not parse exDividendDate timestamp %s", ex_dividend_ts)
        total_cash = d.get("totalCash")
        total_debt = d.get("totalDebt")
        return {
            "market_cap": d.get("marketCap"),
            "trailing_eps": d.get("trailingEps"),
            "forward_eps": d.get("forwardEps"),
            "earnings_growth": d.get("earningsGrowth"),
            "free_cash_flow": d.get("freeCashflow"),
            "total_debt": total_debt,
            "total_cash": total_cash,
            "net_cash": (total_cash - total_debt) if (total_cash is not None and total_debt is not None) else None,
            "roa": d.get("returnOnAssets"),
            "quick_ratio": d.get("quickRatio"),
            "insider_ownership": d.get("heldPercentInsiders"),
            "payout_ratio": d.get("payoutRatio"),
            "ex_dividend_date": ex_dividend_date,
            "average_volume": d.get("averageVolume"),
            "full_time_employees": d.get("fullTimeEmployees"),
            "website": d.get("website"),
            "financial_currency": d.get("financialCurrency"),
        }
    except Exception:
        return None


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


NON_YAHOO_REGISTRY_TICKERS = frozenset({"UK10YG"})


def is_excluded_from_yahoo_fetch(ticker: str, ignored: Optional[set] = None) -> bool:
    """True when `ticker` must never reach a Yahoo Finance-touching call: is_synthetic_ticker(),
    a registry series sourced elsewhere (UK10YG comes from FT/BoE), or on the Settings-page
    Ignored Tickers list. Pass a pre-computed `ignored` (from ignored_tickers_set()) when
    filtering many tickers in a loop, to avoid reloading config on every call."""
    normalized = normalize_ticker(ticker)
    if normalized in NON_YAHOO_REGISTRY_TICKERS or is_synthetic_ticker(normalized):
        return True
    if ignored is None:
        ignored = ignored_tickers_set()
    return normalized in ignored


_MISSING_SECTORS = {"", "none", "unclassified", "unknown"}


def is_missing_sector(sector: Optional[str]) -> bool:
    return (sector or "").strip().lower() in _MISSING_SECTORS


def normalize_currency_bucket(currency: Optional[str]) -> Optional[str]:
    """Collapses LSE pence (GBp/GBX) and GBP into one "GBP" bucket for same-currency grouping — Yahoo reports either depending on the ticker. Ratio-based callers never need the 100x conversion."""
    if not currency:
        return None
    return "GBP" if currency in ("GBp", "GBX", "GBP") else currency


def trading_days_forward(date_str: str, n_days: int) -> str:
    """Approximate 'n_days trading days forward' of date_str using numpy's Mon-Fri business-day
    calendar (no exchange-holiday awareness) — acceptable wherever the consumer resolves the
    target date via a "first actual close on/after target_date" lookup, which self-corrects for
    the few-day slack a missed holiday introduces."""
    import numpy as np
    d = np.datetime64(date_str, "D")
    return str(np.busday_offset(d, n_days, roll="forward"))


def sanitize_floats(obj):
    """Recursively replaces nan/inf with None so a payload is always JSON-safe."""
    if isinstance(obj, float):
        return obj if math.isfinite(obj) else None
    if isinstance(obj, dict):
        return {k: sanitize_floats(v) for k, v in obj.items()}
    if isinstance(obj, list):
        return [sanitize_floats(v) for v in obj]
    return obj


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
    market_session_helpers.is_exchange_open(exchange) for the ticker in question), is the authoritative
    answer: False means the session has genuinely ended, so the bar can never be "still forming"
    regardless of date collision. A caller may omit it only if every path that reaches this
    function is provably gated on the exchange already being confirmed open earlier in the same
    call — e.g. intraday_bottom_engine.run_scan() and intraday_orchestrator.py's scan loop, which
    both `continue` past is_exchange_open()/is_quote_settled() checks before ever calling this
    (verified 2026-07-15) — not merely "usually called during market hours". Found 2026-07-13:
    data_engine.py's nightly bulk/single-ticker fetchers passed no exchange signal at all, so
    every night's Update Pipeline run trimmed that day's just-completed, fully-final close off the
    daily parquet, permanently rolling stock_signals.current_price and quant_signals one trading
    day stale. Found 2026-07-15: market_pulse_write.fetch_and_save_pulse() had the same gap — it's
    reachable from age-based staleness refreshes and on-demand single-ticker fetches with no
    exchange-open precondition at all, so it now resolves and passes the ticker's own exchange
    state too."""
    if exchange_currently_open is False:
        return False
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).date()
    return last_daily_date >= last_live_date and last_daily_date >= today
