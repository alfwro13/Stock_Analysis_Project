"""Central time utility. USER_TIMEZONE + HOME_EXCHANGE in config.json drive all tz-aware behaviour."""
from __future__ import annotations

from datetime import datetime, time as dtime, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
from typing import Optional

EXCHANGE_HOURS: dict[str, dict] = {
    "NYSE": {
        "open":           "09:30",
        "close":          "16:00",
        "tz":             "America/New_York",
        "premarket_open": "04:00",
    },
    "LSE": {
        "open":  "08:00",
        "close": "16:30",
        "tz":    "Europe/London",
        # LSE has no recognised pre-market session
    },
    "XETRA": {
        "open":  "09:00",
        "close": "17:30",
        "tz":    "Europe/Berlin",
    },
    "TSE": {
        "open":  "09:00",
        "close": "15:30",
        "tz":    "Asia/Tokyo",
    },
}

_FALLBACK_EXCHANGE = "NYSE"

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

def ticker_exchange(ticker: str, currency: str = "") -> str:
    """Infer exchange from ticker suffix or currency; ambiguous USD-less tickers fall back to HOME_EXCHANGE."""
    if ticker.endswith(".L") or currency in ("GBp", "GBP"):
        return "LSE"
    if ticker.endswith(".DE") or currency == "EUR":
        return "XETRA"
    if ticker.endswith(".T"):
        return "TSE"
    if currency == "USD":
        return "NYSE"   # USD without exchange suffix → US market
    return _load_config().get("HOME_EXCHANGE", _FALLBACK_EXCHANGE)

def market_window_utc(
    exchange: Optional[str] = None,
    include_premarket: bool = False,
) -> tuple[dtime, dtime]:
    """Return (open_utc, close_utc) as naive UTC times for today, honouring DST."""
    if exchange is None:
        exchange = _load_config().get("HOME_EXCHANGE", _FALLBACK_EXCHANGE)

    info = EXCHANGE_HOURS.get(exchange, EXCHANGE_HOURS[_FALLBACK_EXCHANGE])
    tz = ZoneInfo(info["tz"])
    today = datetime.now(timezone.utc).date()

    open_key = "premarket_open" if (include_premarket and "premarket_open" in info) else "open"
    open_local  = datetime.combine(today, _parse_hm(info[open_key]),  tzinfo=tz)
    close_local = datetime.combine(today, _parse_hm(info["close"]), tzinfo=tz)

    open_utc  = open_local.astimezone(timezone.utc).time().replace(tzinfo=None)
    close_utc = close_local.astimezone(timezone.utc).time().replace(tzinfo=None)
    return open_utc, close_utc


def is_market_open(
    exchange: Optional[str] = None,
    include_premarket: bool = False,
) -> bool:
    """True if current UTC time falls within *exchange*'s trading hours."""
    open_utc, close_utc = market_window_utc(exchange, include_premarket=include_premarket)
    now = datetime.now(timezone.utc).time().replace(tzinfo=None)
    return open_utc <= now <= close_utc


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
