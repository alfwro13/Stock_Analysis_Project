# options_engine.py
import logging
import threading
import time
import numpy as np
import yfinance as yf
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

OPTIONS_CACHE_TTL_SECONDS: int = 900

_options_cache: Dict[str, Dict[str, Any]] = {}
_options_cache_lock: threading.Lock = threading.Lock()


def clear_options_cache(ticker: Optional[str] = None) -> None:
    """Clear one ticker's cached options chain, or the entire cache when ticker is None."""
    with _options_cache_lock:
        if ticker is None:
            _options_cache.clear()
            logger.info("Options cache cleared (all tickers).")
        else:
            _options_cache.pop(ticker.upper(), None)
            logger.info("Options cache cleared for %s.", ticker.upper())

def fetch_options_chain(ticker: str) -> Dict[str, Any]:
    """
    Fetches the current underlying price and the nearest options expiration
    chains (Calls and Puts) for a given ticker. Increased slice to 5 chains
    to properly capture high-liquidity standard monthly (3rd Friday) expirations.

    Results are cached in-process for OPTIONS_CACHE_TTL_SECONDS (900 s). Only
    successful responses are cached; error dicts are never stored.
    """
    normalized: str = ticker.upper()

    # Lock is held only for the dict read — the slow yfinance fetch runs outside.
    with _options_cache_lock:
        entry: Optional[Dict[str, Any]] = _options_cache.get(normalized)

    if entry is not None and (time.monotonic() - entry["ts"]) < OPTIONS_CACHE_TTL_SECONDS:
        logger.info("Options cache hit for %s; returning cached chain.", normalized)
        return entry["data"]

    logger.info("Fetching options chain for %s...", normalized)
    try:
        tk = yf.Ticker(ticker)
        expirations = tk.options

        if not expirations:
            return {"error": f"No options data available for {ticker}."}

        # Expanded to nearest 5 expirations to guarantee capturing standard Monthlies
        target_exps = list(expirations[:5])

        chain_data: Dict[str, Any] = {}
        for exp in target_exps:
            chain = tk.option_chain(exp)

            # Clean and isolate relevant columns, now including critical liquidity metrics
            # Fill numeric trading fields with 0, but preserve None for implied volatility
            #  so the UI can display "N/A" rather than a misleading 0.00% figure.
            numeric_cols = ['strike', 'lastPrice', 'bid', 'ask', 'volume', 'openInterest']
            calls_df = chain.calls[numeric_cols + ['impliedVolatility']].copy()
            puts_df  = chain.puts[numeric_cols  + ['impliedVolatility']].copy()
            calls_df[numeric_cols] = calls_df[numeric_cols].fillna(0)
            puts_df[numeric_cols]  = puts_df[numeric_cols].fillna(0)
            calls = calls_df.where(calls_df.notna(), other=None).to_dict('records')
            puts  = puts_df.where(puts_df.notna(),   other=None).to_dict('records')
            chain_data[exp] = {"calls": calls, "puts": puts}

        # Fetch the latest underlying close price
        hist = tk.history(period="1d")
        current_price: float = float(hist['Close'].iloc[-1]) if not hist.empty else 0.0

        logger.info("Successfully retrieved chain for %s. Underlying: %s", normalized, current_price)
        result: Dict[str, Any] = {
            "ticker": normalized,
            "current_price": current_price,
            "expirations": target_exps,
            "chains": chain_data,
        }

        with _options_cache_lock:
            _options_cache[normalized] = {"ts": time.monotonic(), "data": result}

        return result

    except Exception as e:
        logger.error("Failed to fetch options chain for %s: %s", normalized, e)
        return {"error": str(e)}

def calculate_payoff_matrix(strategy_legs: List[Dict[str, Any]], current_price: float) -> Dict[str, list]:
    """
    Generates a vectorized payoff matrix for a combined multi-leg options strategy.
    Evaluates P&L at expiration across a +/- 30% underlying price range.
    """
    # Generate 500 price points for high-resolution zero-crossing in the UI
    min_price = current_price * 0.70
    max_price = current_price * 1.30
    prices = np.linspace(min_price, max_price, 500)
    
    total_payoff = np.zeros_like(prices)
    
    for leg in strategy_legs:
        # Expected keys: type (call/put), strike, premium, position (long/short), quantity
        opt_type = leg.get('type', 'call').lower()
        strike = float(leg.get('strike', 0))
        premium = float(leg.get('premium', 0))
        position = leg.get('position', 'long').lower()
        qty = int(leg.get('quantity', 1))
        
        # Position multiplier
        multiplier = 1 if position == 'long' else -1
        
        # Standard intrinsic value at expiration
        if opt_type == 'call':
            intrinsic_value = np.maximum(0, prices - strike)
        else: # put
            intrinsic_value = np.maximum(0, strike - prices)
            
        # Net Payoff for this specific leg: (Intrinsic - Premium Paid) * Position * Quantity * 100
        leg_payoff = (intrinsic_value - premium) * multiplier * qty * 100
        total_payoff += leg_payoff
        
    return {
        "prices": prices.tolist(),
        "payoffs": total_payoff.tolist()
    }