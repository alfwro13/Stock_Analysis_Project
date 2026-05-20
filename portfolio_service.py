# portfolio_service.py
import yfinance as yf
import time
import threading
import logging
from typing import Dict
from config import BASE_CURRENCY

# --- Logging Setup ---
logger = logging.getLogger(__name__)

# --- Global Foreign Exchange (FX) Cache ---
# Stores FX pairs with timestamps to prevent slow API calls and stale data.
# Format: {"USDGBP=X": {"rate": 0.79, "timestamp": 1684560000.0}}
fx_cache: Dict[str, Dict[str, float]] = {}
fx_lock = threading.Lock()
FX_CACHE_TTL = 600  # 10 minutes in seconds

def get_rate_to_base(stock_currency: str) -> float:
    """
    Converts Native to Base (e.g., USD -> GBP).
    Used heavily by the Global Portfolio Summary Math.
    Implements a thread-safe, TTL-based caching mechanism.
    """
    if stock_currency == 'GBp' and BASE_CURRENCY == 'GBP':
        return 0.01  # Special LSE Math
        
    if not stock_currency or stock_currency in [BASE_CURRENCY, 'GBp', 'GBP']:
        return 1.0

    pair = f"{stock_currency}{BASE_CURRENCY}=X"
    current_time = time.time()

    # Optimistic read without lock for performance
    cached = fx_cache.get(pair)
    if cached and (current_time - cached["timestamp"] < FX_CACHE_TTL):
        return cached["rate"]

    # Cache miss or stale data: acquire lock to fetch
    with fx_lock:
        # Double-check inside lock to prevent redundant API calls from queued threads
        cached = fx_cache.get(pair)
        if cached and (time.time() - cached["timestamp"] < FX_CACHE_TTL):
            return cached["rate"]

        try:
            logger.info(f"Fetching fresh FX rate for {pair} from yfinance...")
            fx_data = yf.Ticker(pair).history(period="1d")
            if not fx_data.empty:
                rate = float(fx_data['Close'].iloc[-1])
            else:
                logger.warning(f"Empty data returned for {pair}. Defaulting to 1.0.")
                rate = 1.0
        except Exception as e:
            logger.error(f"Failed to fetch exchange rate for {pair}: {e}")
            # Fallback to stale cache if API fails to prevent portfolio valuation collapse
            if cached:
                logger.warning(f"Using stale cache for {pair} due to API failure.")
                rate = cached["rate"]
            else:
                rate = 1.0

        # Update cache with the new rate and current timestamp
        fx_cache[pair] = {"rate": rate, "timestamp": time.time()}
        return rate


def get_rate_from_base(stock_currency: str) -> float:
    """
    Converts Base to Native (e.g., GBP -> USD).
    Used to display individual Ghostfolio buys back in their native chart currencies.
    Implements a thread-safe, TTL-based caching mechanism.
    """
    if not stock_currency or stock_currency in [BASE_CURRENCY, 'GBp', 'GBP']:
        return 1.0

    pair = f"{BASE_CURRENCY}{stock_currency}=X"
    current_time = time.time()

    # Optimistic read without lock for performance
    cached = fx_cache.get(pair)
    if cached and (current_time - cached["timestamp"] < FX_CACHE_TTL):
        return cached["rate"]

    # Cache miss or stale data: acquire lock to fetch
    with fx_lock:
        # Double-check inside lock to prevent redundant API calls from queued threads
        cached = fx_cache.get(pair)
        if cached and (time.time() - cached["timestamp"] < FX_CACHE_TTL):
            return cached["rate"]

        try:
            logger.info(f"Fetching fresh FX rate for {pair} from yfinance...")
            fx_data = yf.Ticker(pair).history(period="1d")
            if not fx_data.empty:
                rate = float(fx_data['Close'].iloc[-1])
            else:
                logger.warning(f"Empty data returned for {pair}. Defaulting to 1.0.")
                rate = 1.0
        except Exception as e:
            logger.error(f"Failed to fetch exchange rate for {pair}: {e}")
            # Fallback to stale cache if API fails to prevent portfolio valuation collapse
            if cached:
                logger.warning(f"Using stale cache for {pair} due to API failure.")
                rate = cached["rate"]
            else:
                rate = 1.0

        # Update cache with the new rate and current timestamp
        fx_cache[pair] = {"rate": rate, "timestamp": time.time()}
        return rate