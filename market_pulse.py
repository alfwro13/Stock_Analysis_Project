# market_pulse.py
import yfinance as yf
import pandas as pd
import time
from config import load_config
from database import get_connection

# Dictionary mapping Yahoo Finance tickers to our clean UI display names
INDEX_TICKERS = {
    "^GSPC": "S&P 500",
    "ES=F": "S&P 500 Futures",
    "^NDX": "Nasdaq 100",
    "NQ=F": "Nasdaq 100 Futures",
    "^FTSE": "FTSE 100",
    "^TYX": "US 30Y Yield",
    "^TNX": "US 10Y Yield",
    "DX=F": "US Dollar Index",
    "TUKG10Y=X": "UK 10Y Gilt",
    "GBPUSD=X": "GBP/USD"
}

# Simple thread safety flag to prevent duplicate background fetch spawns
_FETCHING = False

def get_all_cached_pulse():
    """Returns all pulse data from DB for Jinja template pre-rendering."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM market_pulse_cache")
    rows = cursor.fetchall()
    conn.close()
    
    config_data = load_config()
    refresh_rate = config_data.get("UI_PREFERENCES", {}).get("REFRESH_RATE", 60)
    current_time = time.time()
    
    cache = {}
    for row in rows:
        is_stale = (current_time - row['last_updated']) > refresh_rate
        cache[row['ticker']] = {
            "ticker": row['ticker'],
            "name": row['name'],
            "price": row['price'],
            "change_pts": row['change_pts'],
            "change_pct": row['change_pct'],
            "is_positive": bool(row['is_positive']),
            "is_stale": is_stale
        }
    return cache

def get_cached_pulse_from_db(asset_tickers, refresh_rate):
    """
    Fetches the latest live prices from the SQLite Cache to ensure instant UI rendering.
    Calculates if the data is stale based on the user's refresh rate.
    """
    if asset_tickers is None:
        asset_tickers = []
        
    config_data = load_config()
    ignored_tickers = config_data.get("IGNORED_TICKERS", [])
        
    requested_assets = [t for t in asset_tickers if t not in INDEX_TICKERS and t not in ignored_tickers]
    all_tickers = list(INDEX_TICKERS.keys()) + requested_assets
    
    conn = get_connection()
    cursor = conn.cursor()
    
    rows = []
    if all_tickers:
        placeholders = ','.join('?' for _ in all_tickers)
        cursor.execute(f"SELECT * FROM market_pulse_cache WHERE ticker IN ({placeholders})", all_tickers)
        rows = cursor.fetchall()
    conn.close()

    results = {"indexes": [], "assets": []}
    current_time = time.time()
    found_tickers = set()

    # Load found data from the database
    for row in rows:
        ticker = row['ticker']
        found_tickers.add(ticker)
        is_stale = (current_time - row['last_updated']) > refresh_rate
        data_obj = {
            "ticker": ticker,
            "name": row['name'],
            "price": row['price'],
            "change_pts": row['change_pts'],
            "change_pct": row['change_pct'],
            "is_positive": bool(row['is_positive']),
            "is_stale": is_stale
        }
        if ticker in INDEX_TICKERS:
            results["indexes"].append(data_obj)
        else:
            results["assets"].append(data_obj)
            
    # For completely missing tickers, mark them as stale skeletons immediately
    missing = [t for t in all_tickers if t not in found_tickers]
    for t in missing:
        data_obj = {
            "ticker": t,
            "name": INDEX_TICKERS.get(t, t),
            "price": "0.00",
            "change_pts": "0.00",
            "change_pct": "0.00",
            "is_positive": True,
            "is_stale": True
        }
        if t in INDEX_TICKERS:
            results["indexes"].append(data_obj)
        else:
            results["assets"].append(data_obj)

    return results

def fetch_and_save_pulse(tickers_to_fetch):
    """
    Background Task: Connects to Yahoo Finance to fetch raw ticks and saves them to the DB.
    Never blocks the main FastAPI UI thread.
    """
    global _FETCHING
    if _FETCHING:
        return
    _FETCHING = True
    
    try:
        df_daily = yf.download(tickers_to_fetch, period="5d", interval="1d", group_by='ticker', progress=False)
        df_live = yf.download(tickers_to_fetch, period="1d", interval="2m", prepost=True, group_by='ticker', progress=False)
        
        
        conn = get_connection()
        cursor = conn.cursor()
        current_time = time.time()
        
        for ticker in tickers_to_fetch:
            try:
                # Robust extraction handling multi-index vs single-index to prevent YF duplication bugs
                if isinstance(df_daily.columns, pd.MultiIndex):
                    if ticker not in df_daily.columns.get_level_values(0) or ticker not in df_live.columns.get_level_values(0):
                        # TICKER MISSING FROM YAHOO: Update timestamp to prevent infinite loop
                        cursor.execute("UPDATE market_pulse_cache SET last_updated = ? WHERE ticker = ?", (current_time, ticker))
                        if cursor.rowcount == 0:
                            name = INDEX_TICKERS.get(ticker, ticker)
                            cursor.execute("INSERT INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, is_positive, last_updated) VALUES (?, ?, ?, ?, ?, ?, ?)", (ticker, name, "0.00", "0.00", "0.00", 1, current_time))
                        continue
                        
                    t_daily = df_daily[ticker].copy()
                    t_live = df_live[ticker].copy()
                else:
                    t_daily = df_daily.copy()
                    t_live = df_live.copy()
                    
                t_daily.dropna(subset=['Close'], inplace=True)
                t_live.dropna(subset=['Close'], inplace=True)
                
                if t_daily.empty or t_live.empty:
                    # DATA EMPTY: Update timestamp to prevent infinite loop
                    cursor.execute("UPDATE market_pulse_cache SET last_updated = ? WHERE ticker = ?", (current_time, ticker))
                    if cursor.rowcount == 0:
                        name = INDEX_TICKERS.get(ticker, ticker)
                        cursor.execute("INSERT INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, is_positive, last_updated) VALUES (?, ?, ?, ?, ?, ?, ?)", (ticker, name, "0.00", "0.00", "0.00", 1, current_time))
                    continue

                # Normal processing for successful data
                current_price = t_live['Close'].iloc[-1]
                last_daily_date = t_daily.index[-1].date()
                live_date = t_live.index[-1].date()
                
                if last_daily_date >= live_date and len(t_daily) >= 2:
                    prev_close = t_daily['Close'].iloc[-2]
                else:
                    prev_close = t_daily['Close'].iloc[-1]
                    
                change_pts = current_price - prev_close
                change_pct = (change_pts / prev_close) * 100.0 if not pd.isna(prev_close) and prev_close != 0 else 0.0

                name = INDEX_TICKERS.get(ticker, ticker)
                price_str = f"{current_price:,.2f}"
                change_pts_str = f"{change_pts:,.2f}"
                change_pct_str = f"{change_pct:,.2f}"
                is_positive = int(change_pts >= 0)
                
                cursor.execute('''
                    INSERT OR REPLACE INTO market_pulse_cache 
                    (ticker, name, price, change_pts, change_pct, is_positive, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (ticker, name, price_str, change_pts_str, change_pct_str, is_positive, current_time))
                
            except Exception as e:
                print(f"[MARKET PULSE BACKGROUND] Error processing {ticker}: {e}")
        conn.commit()
        conn.close()
    except Exception as e:
        print(f"[MARKET PULSE BACKGROUND] Batch download failed: {e}")
    finally:
        _FETCHING = False