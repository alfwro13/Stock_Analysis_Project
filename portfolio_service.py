# portfolio_service.py
import yfinance as yf
from config import BASE_CURRENCY

# --- Global Foreign Exchange (FX) Cache ---
# Stores FX pairs (e.g., "USDGBP=X": 0.79) to prevent slow API calls on every page refresh
fx_cache = {}

def get_rate_to_base(stock_currency: str) -> float:
    """
    Converts Native to Base (e.g., USD -> GBP).
    Used heavily by the Global Portfolio Summary Math.
    """
    global fx_cache
    exchange_rate = 1.0
    
    if stock_currency == 'GBp' and BASE_CURRENCY == 'GBP':
        exchange_rate = 0.01  # Special LSE Math
    elif stock_currency and stock_currency not in [BASE_CURRENCY, 'GBp', 'GBP']:
        pair = f"{stock_currency}{BASE_CURRENCY}=X"
        if pair not in fx_cache:
            try:
                fx_data = yf.Ticker(pair).history(period="1d")
                if not fx_data.empty:
                    fx_cache[pair] = fx_data['Close'].iloc[-1]
                else:
                    fx_cache[pair] = 1.0
            except Exception:
                fx_cache[pair] = 1.0
        exchange_rate = fx_cache[pair]
        
    return exchange_rate

def get_rate_from_base(stock_currency: str) -> float:
    """
    Converts Base to Native (e.g., GBP -> USD).
    Used to display individual Ghostfolio buys back in their native chart currencies.
    """
    global fx_cache
    exchange_rate = 1.0
    
    if stock_currency and stock_currency not in [BASE_CURRENCY, 'GBp', 'GBP']:
        pair = f"{BASE_CURRENCY}{stock_currency}=X"
        if pair not in fx_cache:
            try:
                fx_data = yf.Ticker(pair).history(period="1d")
                if not fx_data.empty:
                    fx_cache[pair] = fx_data['Close'].iloc[-1]
                else:
                    fx_cache[pair] = 1.0
            except Exception as e:
                print(f"[WARNING] Could not fetch exchange rate for {pair}: {e}")
                fx_cache[pair] = 1.0
        exchange_rate = fx_cache[pair]
        
    return exchange_rate