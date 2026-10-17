import time
from datetime import datetime, timezone
from typing import Any, Optional

from config import BASE_CURRENCY
from db_helpers import parse_utc_epoch
from market_session_helpers import get_exchange_session_state
from utils import sanitize_floats

ENVELOPE_EXCHANGES = ("LSE", "NYSE")
STALE_AFTER_SECONDS = 900
NIGHTLY_STALE_AFTER_SECONDS = 5 * 86400  # Friday evening's run must still count as current on a Tuesday after a bank holiday


def iso_utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def iso_from_stored_utc(stored_utc: Optional[str]) -> Optional[str]:
    epoch = parse_utc_epoch(stored_utc)
    return iso_utc(epoch) if epoch else None


def date_epoch(day: Optional[str]) -> Optional[float]:
    if not day:
        return None
    return datetime.strptime(day[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


def dated_part(epoch: Optional[float], payload: dict, *, as_of: Optional[str] = None, stale_after: int = NIGHTLY_STALE_AFTER_SECONDS, ages: bool = True) -> dict:
    if as_of is None and epoch is not None:
        as_of = iso_utc(epoch)
    stale = ages and epoch is not None and time.time() - epoch > stale_after
    return {"as_of": as_of, "stale": stale, **payload}


def envelope(data: Any, *, as_of: Optional[float], units: dict, missing: list, stale_after_seconds: int = STALE_AFTER_SECONDS) -> dict:
    market_state = {exchange: get_exchange_session_state(exchange) for exchange in ENVELOPE_EXCHANGES}
    age_seconds = None if as_of is None else max(0, int(time.time() - as_of))
    overdue = age_seconds is not None and age_seconds > stale_after_seconds and "open" in market_state.values()
    return sanitize_floats({
        "as_of": None if as_of is None else iso_utc(as_of),
        "age_seconds": age_seconds,
        "market_state": market_state,
        "base_currency": BASE_CURRENCY,
        "units": units,
        "stale": bool(missing) or overdue,
        "missing": missing,
        "data": data,
    })
