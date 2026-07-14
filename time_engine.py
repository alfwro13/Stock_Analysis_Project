"""Central time utility. USER_TIMEZONE + HOME_EXCHANGE in config.json drive all tz-aware behaviour."""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Optional

import exchange_calendars as xcals
import pandas as pd

logger = logging.getLogger(__name__)

_EXCHANGE_HOURS_PATH = os.path.join(os.path.dirname(__file__), "data", "exchange_hours.json")

# Hardcoded fallback used when the JSON file is absent or malformed
_BUILTIN_EXCHANGE_HOURS: dict[str, dict] = {
    "NYSE": {
        "open":           "09:30",
        "close":          "16:00",
        "tz":             "America/New_York",
        "currency":       "USD",
        "suffixes":       [],
        "premarket_open": "04:00",
    },
    "LSE": {
        "open":     "08:00",
        "close":    "16:30",
        "tz":       "Europe/London",
        "currency": "GBP",
        "suffixes": [".L"],
        "quote_delay_minutes": 15,
    },
    "XETRA": {
        "open":     "09:00",
        "close":    "17:30",
        "tz":       "Europe/Berlin",
        "currency": "EUR",
        "suffixes": [".DE", ".F"],
    },
    "TSE": {
        "open":     "09:00",
        "close":    "15:30",
        "tz":       "Asia/Tokyo",
        "currency": "JPY",
        "suffixes": [".T"],
    },
}

_FALLBACK_EXCHANGE = "NYSE"

# App exchange id -> exchange_calendars code. NSE/SZSE/Euronext deliberately share a sibling
# exchange's calendar (no distinct upstream calendar exists) since each genuinely follows the
# same trading-holiday calendar as its sibling (India NSE/BSE; PRC SSE/SZSE; harmonised
# Euronext venues since 2002).
_EXCHANGE_CALENDAR_CODES: dict[str, str] = {
    "NYSE": "XNYS", "LSE": "XLON", "XETRA": "XETR", "TSE": "XTKS", "ASX": "XASX",
    "KRX": "XKRX", "HKEX": "XHKG", "SGX": "XSES", "NSE": "XBOM", "BSE": "XBOM",
    "SSE": "XSHG", "SZSE": "XSHG", "TWSE": "XTAI", "TSX": "XTSE", "BOVESPA": "BVMF",
    "BMV": "XMEX", "Euronext": "XPAR", "SIX": "XSWX", "MIL": "XMIL", "BME": "XMAD",
    "OMXS": "XSTO", "OMXH": "XHEL", "OMXC": "XCSE", "OSE": "XOSL", "WBAG": "XWBO",
    "WSE": "XWAR", "JSE": "XJSE", "TASE": "XTAE", "Tadawul": "XSAU",
}

_registry_cache: dict | None = None
_suffix_cache: dict[str, str] | None = None


def _load_exchange_registry() -> dict[str, dict]:
    global _registry_cache
    if _registry_cache is not None:
        return _registry_cache
    try:
        with open(_EXCHANGE_HOURS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict) and data:
            _registry_cache = data
            return _registry_cache
    except Exception as exc:
        logger.warning("exchange_hours.json load failed (%s); using built-in defaults", exc)
    _registry_cache = _BUILTIN_EXCHANGE_HOURS
    return _registry_cache


def _build_suffix_lookup() -> dict[str, str]:
    global _suffix_cache
    if _suffix_cache is not None:
        return _suffix_cache
    registry = _load_exchange_registry()
    mapping: dict[str, str] = {}
    for exchange, info in registry.items():
        for suffix in info.get("suffixes", []):
            if suffix:
                mapping[suffix.upper()] = exchange
    _suffix_cache = mapping
    return _suffix_cache


# Public: populated at module import time; re-read after reload_exchange_registry()
EXCHANGE_HOURS: dict[str, dict] = {}


def _refresh_globals() -> None:
    global EXCHANGE_HOURS, _registry_cache, _suffix_cache
    _registry_cache = None
    _suffix_cache = None
    EXCHANGE_HOURS.clear()
    EXCHANGE_HOURS.update(_load_exchange_registry())


_refresh_globals()

# Module-level suffix → exchange map (public for etf_predictor_engine)
_SUFFIX_TO_EXCHANGE: dict[str, str] = _build_suffix_lookup()


def reload_exchange_registry() -> None:
    """Force a re-read of exchange_hours.json. Call after editing the file at runtime."""
    global _SUFFIX_TO_EXCHANGE
    _refresh_globals()
    _SUFFIX_TO_EXCHANGE = _build_suffix_lookup()


def _load_config() -> dict:
    from config import load_config as _lc   # lazy import avoids circular-import issues
    return _lc()


def _parse_hm(hm: str) -> dtime:
    h, m = map(int, hm.split(":"))
    return dtime(h, m)


def get_user_tz() -> ZoneInfo:
    tz_name = _load_config().get("USER_TIMEZONE", "Europe/London")
    try:
        return ZoneInfo(tz_name)
    except ZoneInfoNotFoundError:
        return ZoneInfo("Europe/London")


def now_local() -> datetime:
    return datetime.now(get_user_tz())


def to_local(dt: datetime) -> datetime:
    """Convert *dt* to the user's display timezone. Naive datetimes are assumed UTC."""
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(get_user_tz())


def fmt_time(dt: datetime) -> str:
    """Format as '16:05 GMT' or '16:05 BST'."""
    return to_local(dt).strftime("%H:%M %Z")


def fmt_datetime(dt: datetime) -> str:
    """Format as '2026-06-05 16:05 GMT'."""
    return to_local(dt).strftime("%Y-%m-%d %H:%M %Z")


def _match_suffix(ticker: str) -> Optional[str]:
    """Longest-matching registered ticker suffix's exchange (data/exchange_hours.json), or None."""
    suffix_map = _build_suffix_lookup()
    # Check longest matching suffix first to handle multi-part suffixes like .TWO
    for length in (4, 3, 2, 1):
        dot_pos = -(length + 1)
        if len(ticker) > length and ticker[dot_pos] == ".":
            candidate = ticker[dot_pos:].upper()
            if candidate in suffix_map:
                return suffix_map[candidate]
    return None


def ticker_exchange(ticker: str, currency: str = "") -> str:
    """Infer exchange from ticker suffix (JSON registry) or currency fallback."""
    matched = _match_suffix(ticker)
    if matched:
        return matched
    # Currency fallbacks for plain tickers (no recognised suffix)
    if currency in ("GBp", "GBP"):
        return "LSE"
    if currency == "EUR":
        return "XETRA"
    if currency == "JPY":
        return "TSE"
    if currency == "USD":
        return "NYSE"
    return _load_config().get("HOME_EXCHANGE", _FALLBACK_EXCHANGE)


def ticker_exchange_from_suffix(ticker: str) -> str:
    """Exchange from ticker suffix only — never falls back to HOME_EXCHANGE; plain tickers default to NYSE."""
    return _match_suffix(ticker) or "NYSE"


def ticker_exchange_or_none(ticker: str) -> Optional[str]:
    """Exchange from a recognised ticker suffix only, incl. data/exchange_hours.json's full
    registry (KRX, SGX, TSX, etc.); None (not a guess) for an unrecognised or absent suffix — the
    caller must decide what "no match" means (skip, or treat a bare ticker as NYSE) rather than
    this function silently defaulting one way for every caller (see markets_engine.py)."""
    return _match_suffix(ticker)


def exchange_tz(exchange: str) -> str:
    """Return the IANA timezone string for *exchange*, falling back to the default exchange."""
    info = EXCHANGE_HOURS.get(exchange, EXCHANGE_HOURS[_FALLBACK_EXCHANGE])
    return info["tz"]


def localize_naive_to_utc(dt_naive: datetime, exchange: str) -> datetime:
    """Attach the exchange local timezone to a naive datetime and convert to UTC."""
    tz = ZoneInfo(exchange_tz(exchange))
    return dt_naive.replace(tzinfo=tz).astimezone(timezone.utc)


def _local_today(tz: ZoneInfo):
    return datetime.now(timezone.utc).astimezone(tz).date()


def market_window_utc(
    exchange: Optional[str] = None,
    include_premarket: bool = False,
) -> tuple[dtime, dtime]:
    """Return (open_utc, close_utc) as naive UTC times for today, honouring DST and any
    early-close/half-day the exchange_calendars-backed override reports (e.g. NYSE's
    post-Thanksgiving 13:00 ET close) — falls back to the static exchange_hours.json hours when
    no calendar mapping exists for *exchange*, or today isn't a valid trading session for it."""
    if exchange is None:
        exchange = _load_config().get("HOME_EXCHANGE", _FALLBACK_EXCHANGE)

    info = EXCHANGE_HOURS.get(exchange, EXCHANGE_HOURS[_FALLBACK_EXCHANGE])
    tz = ZoneInfo(info["tz"])
    today = _local_today(tz)
    override = _session_window_override(exchange, today)

    if include_premarket and "premarket_open" in info:
        # exchange_calendars models no extended/premarket session, so this stays static.
        open_local = datetime.combine(today, _parse_hm(info["premarket_open"]), tzinfo=tz)
    elif override is not None:
        open_local = override[0]
    else:
        open_local = datetime.combine(today, _parse_hm(info["open"]), tzinfo=tz)

    close_local = override[1] if override is not None else datetime.combine(today, _parse_hm(info["close"]), tzinfo=tz)

    open_utc  = open_local.astimezone(timezone.utc).time().replace(tzinfo=None)
    close_utc = close_local.astimezone(timezone.utc).time().replace(tzinfo=None)
    return open_utc, close_utc


_calendar_cache: dict[str, object] = {}
_uncovered_calendar_warned: set[str] = set()


def _get_exchange_calendar(exchange: str):
    if exchange in _calendar_cache:
        return _calendar_cache[exchange]
    code = _EXCHANGE_CALENDAR_CODES.get(exchange)
    if code is None:
        if exchange not in _uncovered_calendar_warned:
            logger.warning(
                "No exchange_calendars mapping for %s; holiday check disabled for it", exchange
            )
            _uncovered_calendar_warned.add(exchange)
        cal = None
    else:
        try:
            cal = xcals.get_calendar(code)
        except Exception as exc:
            logger.error("Failed to build exchange_calendars calendar %s for %s: %s", code, exchange, exc)
            cal = None
    _calendar_cache[exchange] = cal
    return cal


def _session_window_override(exchange: str, local_date):
    """Today's (open, close) as tz-aware UTC Timestamps from exchange_calendars — None if no
    calendar mapping exists for *exchange*, or *local_date* isn't a valid trading session for it
    (holiday/weekend), so the caller falls back to the static exchange_hours.json hours."""
    calendar = _get_exchange_calendar(exchange)
    if calendar is None:
        return None
    try:
        session = pd.Timestamp(local_date)
        if not calendar.is_session(session):
            return None
        return calendar.session_open(session), calendar.session_close(session)
    except Exception as exc:
        logger.error("Failed to resolve session window for %s on %s: %s", exchange, local_date, exc)
        return None


def is_exchange_holiday(exchange: Optional[str] = None) -> bool:
    """True if *exchange* is closed today for a scheduled holiday, per exchange_calendars.
    Unmapped exchanges fail open (return False) rather than blocking the caller."""
    if exchange is None:
        exchange = _load_config().get("HOME_EXCHANGE", _FALLBACK_EXCHANGE)
    calendar = _get_exchange_calendar(exchange)
    if calendar is None:
        return False
    local_date = _local_today(ZoneInfo(exchange_tz(exchange)))
    return not calendar.is_session(pd.Timestamp(local_date))


def is_market_open(
    exchange: Optional[str] = None,
    include_premarket: bool = False,
) -> bool:
    """True if current UTC time falls within *exchange*'s trading hours and it isn't a holiday."""
    if is_exchange_holiday(exchange):
        return False
    open_utc, close_utc = market_window_utc(exchange, include_premarket=include_premarket)
    now = datetime.now(timezone.utc).time().replace(tzinfo=None)
    return open_utc <= now <= close_utc


def is_trading_session(exchange: Optional[str] = None, include_premarket: bool = False) -> bool:
    """True only if today is Mon–Fri AND current UTC time falls within exchange trading hours."""
    if datetime.now(timezone.utc).weekday() >= 5:
        return False
    return is_market_open(exchange, include_premarket=include_premarket)


def reset_cron_trigger_params(exchange: Optional[str] = None) -> dict:
    """Return APScheduler CronTrigger kwargs for 5 min after exchange close; uses exchange tz so DST is automatic."""
    if exchange is None:
        exchange = _load_config().get("HOME_EXCHANGE", _FALLBACK_EXCHANGE)
    info = EXCHANGE_HOURS.get(exchange, EXCHANGE_HOURS[_FALLBACK_EXCHANGE])
    close = _parse_hm(info["close"])
    total_min = close.hour * 60 + close.minute + 5
    return {
        "day_of_week": "mon-fri",
        "hour": total_min // 60,
        "minute": total_min % 60,
        "timezone": info["tz"],
    }


def fmt_reset_time(exchange: Optional[str] = None) -> str:
    """Return the formatted (user-tz) time at which the daily reset fires for *exchange*."""
    params = reset_cron_trigger_params(exchange)
    reset_dt = datetime.combine(
        now_local().date(),
        dtime(params["hour"], params["minute"]),
        tzinfo=ZoneInfo(params["timezone"]),
    )
    return fmt_time(reset_dt)


def fmt_et_time_local(time_str: str) -> str:
    """Convert a 'HH:MM' Eastern Time string to the user's configured local timezone.

    Returns e.g. '18:15 BST' when USER_TIMEZONE is Europe/London during summer.
    Used to display ET-anchored auction check times in the user's local timezone.
    """
    h, m = map(int, time_str.split(":"))
    et_dt = datetime.combine(
        now_local().date(),
        dtime(h, m),
        tzinfo=ZoneInfo(exchange_tz("NYSE")),
    )
    return fmt_time(et_dt)


def fmt_et_time_value(time_str: str) -> str:
    """Convert a 'HH:MM' Eastern Time string to 'HH:MM' in the user's local timezone.

    Returns only the time digits (no tz suffix), suitable for <input type="time"> values.
    """
    h, m = map(int, time_str.split(":"))
    et_dt = datetime.combine(
        now_local().date(),
        dtime(h, m),
        tzinfo=ZoneInfo(exchange_tz("NYSE")),
    )
    return et_dt.astimezone(get_user_tz()).strftime("%H:%M")
