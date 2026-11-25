import time
from datetime import datetime, timezone
from typing import Any, Optional

from config import BASE_CURRENCY
from constants import ML_FEATURE_MAX_AGE_TRADING_DAYS, PREDICTION_HORIZON_DAYS, PREDICTION_RETURN_THRESHOLD
from db.helpers import parse_utc_epoch
from market_session_helpers import get_exchange_session_state
from time_engine import session_neighbours
from utils import sanitize_floats

ENVELOPE_EXCHANGES = ("LSE", "NYSE")
STALE_AFTER_SECONDS = 900
NIGHTLY_STALE_AFTER_SECONDS = 5 * 86400  # Friday evening's run must still count as current on a Tuesday after a bank holiday
ML_CONFIDENCE_UNITS = (
    f"0-100: the calibrated model probability that the price rises more than {PREDICTION_RETURN_THRESHOLD:.0%} within "
    f"{PREDICTION_HORIZON_DAYS} trading days. It rises with volatility and is not an expected return: the calibration "
    "is a step function, so many tickers share one value and tickers on the same value are not ranked against each other. "
    f"Null once scored more than {ML_FEATURE_MAX_AGE_TRADING_DAYS} trading days ago"
)


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


def _session_context() -> dict:
    markets = {}
    for exchange in ENVELOPE_EXCHANGES:
        near = session_neighbours(exchange)
        markets[exchange] = {
            "trading_day": near["trading_day"],
            "last_session": None if near["last_session"] is None else near["last_session"].isoformat(),
            "next_session": None if near["next_session"] is None else near["next_session"].isoformat(),
        }
    shut = [exchange for exchange, part in markets.items() if not part["trading_day"]]
    note = None
    if shut:
        today = session_neighbours(shut[0])["today"]
        detail = "; ".join(f"{e}: last session {markets[e]['last_session']}, next session {markets[e]['next_session']}" for e in shut)
        note = (
            f"Today ({today.strftime('%A')} {today.isoformat()}) is not a trading day on {' or '.join(shut)}. "
            f"Prices and market figures are from the last session, not today ({detail}). "
            "Nightly figures change only after the next session closes."
        )
    return {"markets": markets, "note": note}


def envelope(data: Any, *, as_of: Optional[float], units: dict, missing: list, stale_after_seconds: int = STALE_AFTER_SECONDS) -> dict:
    market_state = {exchange: get_exchange_session_state(exchange) for exchange in ENVELOPE_EXCHANGES}
    age_seconds = None if as_of is None else max(0, int(time.time() - as_of))
    overdue = age_seconds is not None and age_seconds > stale_after_seconds and "open" in market_state.values()
    return sanitize_floats({
        "as_of": None if as_of is None else iso_utc(as_of),
        "age_seconds": age_seconds,
        "market_state": market_state,
        "session": _session_context(),
        "base_currency": BASE_CURRENCY,
        "units": units,
        "stale": overdue,
        "incomplete": bool(missing),
        "missing": missing,
        "data": data,
    })
