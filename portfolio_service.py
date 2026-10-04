import logging
from typing import Dict
from config import BASE_CURRENCY
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

# Last successfully fetched rates kept as stale fallback when yahoo_engine returns None.
_last_known_rates: Dict[str, float] = {}


def _fx_rate(pair: str, *, cache_only: bool = False):
    if cache_only:
        return yahoo_engine.get_cached_fx_rate(pair)["rate"]
    rate = yahoo_engine.get_fx_rate(pair)
    if rate is not None:
        _last_known_rates[pair] = rate
        return rate
    if pair in _last_known_rates:
        logger.warning("Using stale FX rate for %s.", pair)
        return _last_known_rates[pair]
    logger.warning("No FX data for %s. Returning 1.0 fallback.", pair)
    return 1.0


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
    native = "GBP" if currency == "GBp" else currency
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
    for pair in sorted(pair for pair in pairs if pair):
        if yahoo_engine.get_fx_rate(pair, force=True) is None:
            raise RuntimeError(f"FX refresh failed for {pair}; last-good cached data retained.")
