# options_engine.py
import logging
import numpy as np
import yfinance as yf
from typing import List, Dict, Any

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - OPTIONS_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def fetch_options_chain(ticker: str) -> Dict[str, Any]:
    """
    Fetches the current underlying price and the nearest options expiration 
    chains (Calls and Puts) for a given ticker. Increased slice to 5 chains 
    to properly capture high-liquidity standard monthly (3rd Friday) expirations.
    """
    logger.info(f"Fetching options chain for {ticker}...")
    try:
        tk = yf.Ticker(ticker)
        expirations = tk.options
        
        if not expirations:
            return {"error": f"No options data available for {ticker}."}
        
        # Expanded to nearest 5 expirations to guarantee capturing standard Monthlies
        target_exps = list(expirations[:5])
        
        chain_data = {}
        for exp in target_exps:
            chain = tk.option_chain(exp)
            
            # Clean and isolate relevant columns, now including critical liquidity metrics
            calls = chain.calls[['strike', 'lastPrice', 'bid', 'ask', 'volume', 'openInterest', 'impliedVolatility']].fillna(0).to_dict('records')
            puts = chain.puts[['strike', 'lastPrice', 'bid', 'ask', 'volume', 'openInterest', 'impliedVolatility']].fillna(0).to_dict('records')
            
            chain_data[exp] = {"calls": calls, "puts": puts}
            
        # Fetch the latest underlying close price
        hist = tk.history(period="1d")
        current_price = float(hist['Close'].iloc[-1]) if not hist.empty else 0.0
        
        logger.info(f"Successfully retrieved chain for {ticker}. Underlying: {current_price}")
        return {
            "ticker": ticker.upper(),
            "current_price": current_price,
            "expirations": target_exps,
            "chains": chain_data
        }
        
    except Exception as e:
        logger.error(f"Failed to fetch options chain for {ticker}: {e}")
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