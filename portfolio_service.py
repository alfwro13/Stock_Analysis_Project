# portfolio_service.py
import logging
from typing import Dict
from config import BASE_CURRENCY
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

# Last successfully fetched rates kept as stale fallback when yahoo_engine returns None.
_last_known_rates: Dict[str, float] = {}


def _fx_rate(pair: str) -> float:
    rate = yahoo_engine.get_fx_rate(pair)
    if rate is not None:
        _last_known_rates[pair] = rate
        return rate
    if pair in _last_known_rates:
        logger.warning(f"Using stale FX rate for {pair}.")
        return _last_known_rates[pair]
    logger.warning(f"No FX data for {pair}. Returning 1.0 fallback.")
    return 1.0


def get_rate_to_base(stock_currency: str) -> float:
    """Converts Native to Base (e.g., USD -> GBP)."""
    if stock_currency == 'GBp' and BASE_CURRENCY == 'GBP':
        return 0.01  # Special LSE Math

    if not stock_currency or stock_currency in [BASE_CURRENCY, 'GBp', 'GBP']:
        return 1.0

    return _fx_rate(f"{stock_currency}{BASE_CURRENCY}=X")


def get_rate_from_base(stock_currency: str) -> float:
    """Converts Base to Native (e.g., GBP -> USD)."""
    if not stock_currency or stock_currency in [BASE_CURRENCY, 'GBp', 'GBP']:
        return 1.0

    return _fx_rate(f"{BASE_CURRENCY}{stock_currency}=X")
