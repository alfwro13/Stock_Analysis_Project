import logging
from contextlib import contextmanager
from contextvars import ContextVar
from config import BASE_CURRENCY, load_config
from utils import normalize_currency_bucket
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

_persisted_fx_reads = ContextVar("persisted_fx_reads", default=False)


@contextmanager
def persisted_fx_reads():
    """Read-only request paths use the persisted FX quote (background refresh) instead of awaiting Yahoo."""
    token = _persisted_fx_reads.set(True)
    try:
        yield
    finally:
        _persisted_fx_reads.reset(token)


def _fx_rate(pair: str, *, cache_only: bool = False):
    if cache_only:
        return yahoo_engine.get_cached_fx_rate(pair)["rate"]
    if _persisted_fx_reads.get():
        quote = yahoo_engine.get_cached_fx_rate(pair, max_age=load_config()["PERFORMANCE"]["HA_FX_MAX_AGE_SECONDS"])
        if quote["rate"] is not None:
            return quote["rate"]
    rate = yahoo_engine.get_fx_rate(pair)
    if rate is not None:
        return rate
    persisted = yahoo_engine.get_cached_fx_rate(pair, refresh=False)["rate"]
    if persisted is not None:
        logger.warning("Using last persisted FX rate for %s.", pair)
        return persisted
    logger.warning("No usable FX rate for %s; the value is unavailable.", pair)
    return None


def get_rate_to_base(stock_currency: str, *, cache_only: bool = False):
    if not stock_currency or stock_currency == BASE_CURRENCY:
        return 1.0
    if stock_currency == 'GBp':
        if BASE_CURRENCY == 'GBP':
            return 0.01  # Special LSE Math: pence → pounds
        rate = _fx_rate(f"GBP{BASE_CURRENCY}=X", cache_only=cache_only)
        return 0.01 * rate if rate is not None else None
    return _fx_rate(f"{stock_currency}{BASE_CURRENCY}=X", cache_only=cache_only)


def get_rate_from_base(stock_currency: str, *, cache_only: bool = False):
    if cache_only:
        pair = fx_pair(stock_currency, from_base=True)
        return _fx_rate(pair, cache_only=True) if pair else 1.0
    if not stock_currency or stock_currency in [BASE_CURRENCY, 'GBp', 'GBP']:
        return 1.0

    return _fx_rate(f"{BASE_CURRENCY}{stock_currency}=X", cache_only=cache_only)


def fx_pair(currency, *, from_base=False):
    native = normalize_currency_bucket(currency)
    if not native or native == BASE_CURRENCY:
        return None
    return f"{BASE_CURRENCY}{native}=X" if from_base else f"{native}{BASE_CURRENCY}=X"


def get_fx_cache_status(currencies, *, from_base=False, include_fresh=False):
    import time_engine
    from datetime import datetime, timezone

    statuses = []
    pairs = {fx_pair(currency, from_base=from_base) for currency in currencies}
    for pair in sorted(pair for pair in pairs if pair):
        status = yahoo_engine.get_cached_fx_rate(pair, refresh=False)
        if include_fresh or status["stale"]:
            status["updated_display"] = time_engine.fmt_datetime(
                datetime.fromtimestamp(status["updated_at"], timezone.utc)
            ) if status["updated_at"] is not None else None
            statuses.append(status)
    return statuses


def refresh_fx_rates(currencies):
    pairs = {fx_pair(currency) for currency in currencies}
    failed = [pair for pair in sorted(pair for pair in pairs if pair) if yahoo_engine.get_fx_rate(pair, force=True) is None]
    if failed:
        raise RuntimeError(f"FX refresh failed for {', '.join(failed)}; last-good cached data retained.")
