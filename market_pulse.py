# market_pulse.py
import yfinance as yf
import pandas as pd
import time
from config import load_config

# Dictionary mapping Yahoo Finance tickers to our clean UI display names
INDEX_TICKERS = {
    "^GSPC": "S&P 500",
    "ES=F": "S&P 500 Futures",
    "^NDX": "Nasdaq 100",
    "NQ=F": "Nasdaq 100 Futures",
    "^FTSE": "FTSE 100",
    "^FTMC": "FTSE 250",
    "^KS11": "KOSPI"
}

# In-Memory Cache to prevent YF API thrashing on rapid page navigation
_CACHE = {}

def get_cached_pulse() -> dict:
    """Returns the current state of the cache for instant Jinja HTML rendering."""
    return {k: v['data'] for k, v in _CACHE.items()}


def get_market_pulse(asset_tickers=None, refresh_rate=60) -> dict:
    """
    Fetches the latest live prices and daily percentage changes
    for both global market indexes and dynamically requested portfolio assets.
    Implements a strict TTL cache to prevent API bans and ensure fast page loads.
    """
    if asset_tickers is None:
        asset_tickers = []
        
    config_data = load_config()
    ignored_tickers = config_data.get("IGNORED_TICKERS", [])
        
    requested_assets = [t for t in asset_tickers if t not in INDEX_TICKERS and t not in ignored_tickers]
    all_tickers = list(INDEX_TICKERS.keys()) + requested_assets
    
    current_time = time.time()
    tickers_to_fetch = []
    
    # 1. Determine which tickers are missing or have stale data
    for ticker in all_tickers:
        if ticker not in _CACHE or (current_time - _CACHE[ticker]['timestamp']) > refresh_rate:
            tickers_to_fetch.append(ticker)
            
    # 2. Only hit YFinance for the stale/missing tickers
    if tickers_to_fetch:
        try:
            df_daily = yf.download(tickers_to_fetch, period="5d", interval="1d", group_by='ticker', progress=False)
            df_live = yf.download(tickers_to_fetch, period="1d", interval="1m", prepost=True, group_by='ticker', progress=False)
            
            for ticker in tickers_to_fetch:
                try:
                    if len(tickers_to_fetch) > 1:
                        if ticker not in df_daily.columns.get_level_values(0) or ticker not in df_live.columns.get_level_values(0):
                            continue
                        t_daily = df_daily[ticker].copy()
                        t_live = df_live[ticker].copy()
                    else:
                        t_daily = df_daily.copy()
                        t_live = df_live.copy()
                        
                    t_daily.dropna(subset=['Close'], inplace=True)
                    t_live.dropna(subset=['Close'], inplace=True)
                    
                    if t_daily.empty or t_live.empty:
                        continue

                    current_price = t_live['Close'].iloc[-1]
                    
                    last_daily_date = t_daily.index[-1].date()
                    live_date = t_live.index[-1].date()
                    
                    if last_daily_date >= live_date and len(t_daily) >= 2:
                        prev_close = t_daily['Close'].iloc[-2]
                    else:
                        prev_close = t_daily['Close'].iloc[-1]
                        
                    change_pts = current_price - prev_close
                    
                    if pd.isna(prev_close) or prev_close == 0:
                        change_pct = 0.0
                    else:
                        change_pct = (change_pts / prev_close) * 100.0

                    data_obj = {
                        "ticker": ticker,
                        "price": f"{current_price:,.2f}",
                        "change_pts": f"{change_pts:,.2f}",
                        "change_pct": f"{change_pct:,.2f}",
                        "is_positive": bool(change_pts >= 0)
                    }

                    if ticker in INDEX_TICKERS:
                        data_obj["name"] = INDEX_TICKERS[ticker]

                    # Update the memory cache
                    _CACHE[ticker] = {
                        "timestamp": current_time,
                        "data": data_obj
                    }

                except Exception as e:
                    print(f"[MARKET PULSE] Error processing {ticker}: {e}")
                    
        except Exception as e:
            print(f"[MARKET PULSE] Batch download failed: {e}")

    # 3. Assemble the final response purely from the lightning-fast memory cache
    results = {"indexes": [], "assets": []}
    for ticker in all_tickers:
        if ticker in _CACHE:
            if ticker in INDEX_TICKERS:
                results["indexes"].append(_CACHE[ticker]["data"])
            else:
                results["assets"].append(_CACHE[ticker]["data"])

    return results
