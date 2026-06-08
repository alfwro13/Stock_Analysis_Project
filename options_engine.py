import logging
import numpy as np
from typing import Any, Dict, List, Optional

from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)


def clear_options_cache(ticker: Optional[str] = None) -> None:
    yahoo_engine.invalidate(ticker.upper() if ticker else None)
    logger.info("Options cache cleared%s.", f" for {ticker.upper()}" if ticker else " (all tickers)")


def fetch_options_chain(ticker: str) -> Dict[str, Any]:
    normalized: str = ticker.upper()
    logger.info("Fetching options chain for %s...", normalized)

    expirations = yahoo_engine.get_options_expirations(normalized)
    if not expirations:
        return {"error": f"No options data available for {ticker}."}

    target_exps = list(expirations[:5])

    chain_data: Dict[str, Any] = {}
    for exp in target_exps:
        chain_result = yahoo_engine.get_options_chain(normalized, exp)
        if chain_result is None:
            continue
        calls_df, puts_df = chain_result

        numeric_cols = ['strike', 'lastPrice', 'bid', 'ask', 'volume', 'openInterest']
        calls_df = calls_df[numeric_cols + ['impliedVolatility']].copy()
        puts_df  = puts_df[numeric_cols  + ['impliedVolatility']].copy()
        calls_df[numeric_cols] = calls_df[numeric_cols].fillna(0)
        puts_df[numeric_cols]  = puts_df[numeric_cols].fillna(0)
        calls = calls_df.where(calls_df.notna(), other=None).to_dict('records')
        puts  = puts_df.where(puts_df.notna(),   other=None).to_dict('records')
        chain_data[exp] = {"calls": calls, "puts": puts}

    if not chain_data:
        return {"error": f"All expiration fetches failed for {ticker}."}

    _hist = yahoo_engine.get_intraday([normalized], period="1d", interval="5m")
    hist = _hist.get(normalized)
    current_price: float = float(hist['Close'].iloc[-1]) if hist is not None and not hist.empty else 0.0

    logger.info("Successfully retrieved chain for %s. Underlying: %s", normalized, current_price)
    return {
        "ticker": normalized,
        "current_price": current_price,
        "expirations": target_exps,
        "chains": chain_data,
    }


def calculate_payoff_matrix(strategy_legs: List[Dict[str, Any]], current_price: float) -> Dict[str, list]:
    min_price = current_price * 0.70
    max_price = current_price * 1.30
    prices = np.linspace(min_price, max_price, 500)

    total_payoff = np.zeros_like(prices)

    for leg in strategy_legs:
        opt_type = leg.get('type', 'call').lower()
        strike = float(leg.get('strike', 0))
        premium = float(leg.get('premium', 0))
        position = leg.get('position', 'long').lower()
        qty = int(leg.get('quantity', 1))

        multiplier = 1 if position == 'long' else -1

        if opt_type == 'call':
            intrinsic_value = np.maximum(0, prices - strike)
        else:
            intrinsic_value = np.maximum(0, strike - prices)

        leg_payoff = (intrinsic_value - premium) * multiplier * qty * 100
        total_payoff += leg_payoff

    return {
        "prices": prices.tolist(),
        "payoffs": total_payoff.tolist()
    }
