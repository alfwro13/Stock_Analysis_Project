import time
from datetime import datetime, timezone
from typing import Any, Optional

from config import BASE_CURRENCY
from market_session_helpers import get_exchange_session_state
from utils import sanitize_floats

ENVELOPE_EXCHANGES = ("LSE", "NYSE")
STALE_AFTER_SECONDS = 900
NIGHTLY_STALE_AFTER_SECONDS = 5 * 86400  # Friday evening's run must still count as current on a Tuesday after a bank holiday


def iso_utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


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
