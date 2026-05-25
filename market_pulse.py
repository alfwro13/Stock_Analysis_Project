# market_pulse.py
import time
import logging
from typing import List, Dict, Any
import pandas as pd
import yfinance as yf

from config import load_config
from database import get_connection

# Configure robust module-level logging
logger = logging.getLogger(__name__)

# Dictionary mapping market identifiers to clean UI display names.
# Registering 'UK10YG' directly below 'GBPUSD=X' places the tile right next to it.
INDEX_TICKERS: Dict[str, str] = {
    "^FTSE": "UK FTSE 100",
    "^FTMC": "UK FTSE 250",
    "GBPUSD=X": "GBP/USD",
    "UK10YG": "UK 10Y Gilt",
    "^GSPC": "US S&P 500",
    "^NDX": "US Nasdaq 100",
    "^TYX": "US 30Y Yield",
    "^TNX": "US 10Y Yield",
    "DX-Y.NYB": "US Dollar Index"
}

# Thread safety flag to prevent duplicate background fetch spawns
_FETCHING: bool = False


def get_all_cached_pulse() -> Dict[str, Dict[str, Any]]:
    """Returns all pulse data from DB for Jinja template pre-rendering."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT * FROM market_pulse_cache")
    rows = cursor.fetchall()
    conn.close()
    
    config_data = load_config()
    refresh_rate: int = int(config_data.get("UI_PREFERENCES", {}).get("REFRESH_RATE", 60))
    current_time: float = time.time()
    
    cache: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        is_stale: bool = (current_time - row['last_updated']) > refresh_rate
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


def get_cached_pulse_from_db(asset_tickers: List[str], refresh_rate: int) -> Dict[str, List[Dict[str, Any]]]:
    """
    Fetches the latest live prices from the SQLite Cache to ensure instant UI rendering.
    Calculates if the data is stale based on the user's refresh rate.
    Also merges the latest FinBERT sentiment score securely fetched via subquery.
    """
    if asset_tickers is None:
        asset_tickers = []
        
    config_data = load_config()
    ignored_tickers: List[str] = config_data.get("IGNORED_TICKERS", [])
        
    requested_assets: List[str] = [t for t in asset_tickers if t not in INDEX_TICKERS and t not in ignored_tickers]
    all_tickers: List[str] = list(INDEX_TICKERS.keys()) + requested_assets
    
    conn = get_connection()
    cursor = conn.cursor()
    
    rows = []
    sentiment_scores: Dict[str, float] = {}
    
    if all_tickers:
        placeholders = ','.join('?' for _ in all_tickers)
        
        # 1. Fetch Market Pulse baseline
        cursor.execute(f"SELECT * FROM market_pulse_cache WHERE ticker IN ({placeholders})", all_tickers)
        rows = cursor.fetchall()
        
        # 2. Fetch Latest Sentiment Output
        query = f"""
            SELECT ticker, sentiment_score
            FROM quant_signals
            WHERE ticker IN ({placeholders})
            AND sentiment_score IS NOT NULL
            AND date = (
                SELECT MAX(date) FROM quant_signals qs
                WHERE qs.ticker = quant_signals.ticker
                    AND qs.sentiment_score IS NOT NULL
            )
        """
        cursor.execute(query, all_tickers)
        sentiment_rows = cursor.fetchall()
        
        for s_row in sentiment_rows:
            sentiment_scores[s_row['ticker']] = s_row['sentiment_score']
            
    conn.close()

    results: Dict[str, List[Dict[str, Any]]] = {"indexes": [], "assets": []}
    current_time: float = time.time()
    
    # Map database rows for O(1) lookup
    db_map = {row['ticker']: row for row in rows}

    # Iterate through our strictly ordered all_tickers list
    for t in all_tickers:
        if t in db_map:
            row = db_map[t]
            is_stale: bool = (current_time - row['last_updated']) > int(refresh_rate)
            data_obj: Dict[str, Any] = {
                "ticker": t,
                "name": row['name'],
                "price": row['price'],
                "change_pts": row['change_pts'],
                "change_pct": row['change_pct'],
                "is_positive": bool(row['is_positive']),
                "is_stale": is_stale,
                "sentiment_score": sentiment_scores.get(t, None)
            }
        else:
            data_obj = {
                "ticker": t,
                "name": INDEX_TICKERS.get(t, t),
                "price": 0.0,
                "change_pts": 0.0,
                "change_pct": 0.0,
                "is_positive": True,
                "is_stale": True,
                "sentiment_score": sentiment_scores.get(t, None)
            }
            
        if t in INDEX_TICKERS:
            results["indexes"].append(data_obj)
        else:
            results["assets"].append(data_obj)

    return results


def fetch_and_save_pulse(tickers_to_fetch: List[str]) -> None:
    """
    Background Task: Connects to Yahoo Finance to fetch raw ticks and saves them to the DB.
    Intercepts and evaluates UK10YG exclusively via the official FT.com engine scraper.
    """
    global _FETCHING
    if _FETCHING:
        return
    _FETCHING = True
    
    try:
        # Separate the custom Financial Times target from the yfinance payload list
        handle_gilt: bool = False
        if "UK10YG" in tickers_to_fetch:
            handle_gilt = True
            tickers_to_fetch = [t for t in tickers_to_fetch if t != "UK10YG"]
            
        df_daily = pd.DataFrame()
        df_live = pd.DataFrame()
        
        if tickers_to_fetch:
            try:
                df_daily = yf.download(tickers_to_fetch, period="5d", interval="1d", group_by='ticker', progress=False)
                df_live = yf.download(tickers_to_fetch, period="1d", interval="2m", prepost=True, group_by='ticker', progress=False)
            except Exception as e:
                logger.error(f"YFinance batch download failed: {e}")
                
        conn = get_connection()
        cursor = conn.cursor()
        current_time: float = time.time()
        
        # 1. Ingest Standard Yahoo Finance Securities
        for ticker in tickers_to_fetch:
            try:
                t_daily = pd.DataFrame()
                t_live = pd.DataFrame()

                if not df_daily.empty:
                    if isinstance(df_daily.columns, pd.MultiIndex):
                        if ticker in df_daily.columns.get_level_values(0):
                            t_daily = df_daily[ticker].copy()
                    else:
                        if len(tickers_to_fetch) == 1:
                            t_daily = df_daily.copy()
                            
                if not df_live.empty:
                    if isinstance(df_live.columns, pd.MultiIndex):
                        if ticker in df_live.columns.get_level_values(0):
                            t_live = df_live[ticker].copy()
                    else:
                        if len(tickers_to_fetch) == 1:
                            t_live = df_live.copy()
                            
                t_daily.dropna(subset=['Close'], inplace=True)
                t_live.dropna(subset=['Close'], inplace=True)
                
                if t_daily.empty or t_live.empty:
                    cursor.execute("UPDATE market_pulse_cache SET last_updated = ? WHERE ticker = ?", (current_time, ticker))
                    if cursor.rowcount == 0:
                        name = INDEX_TICKERS.get(ticker, ticker)
                        cursor.execute(
                            "INSERT INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, is_positive, last_updated) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (ticker, name, 0.0, 0.0, 0.0, 1, current_time)
                        )
                    continue

                current_price: float = float(t_live['Close'].iloc[-1])
                last_daily_date = t_daily.index[-1].date()
                live_date = t_live.index[-1].date()
                
                if last_daily_date >= live_date and len(t_daily) >= 2:
                    prev_close: float = float(t_daily['Close'].iloc[-2])
                else:
                    prev_close = float(t_daily['Close'].iloc[-1])
                    
                change_pts: float = current_price - prev_close
                change_pct: float = (change_pts / prev_close) * 100.0 if not pd.isna(prev_close) and prev_close != 0 else 0.0

                name: str = INDEX_TICKERS.get(ticker, ticker)
                is_positive: int = int(change_pts >= 0)
                
                cursor.execute('''
                    INSERT OR REPLACE INTO market_pulse_cache 
                    (ticker, name, price, change_pts, change_pct, is_positive, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (ticker, name, current_price, change_pts, change_pct, is_positive, current_time))
                
            except Exception as e:
                logger.error(f"[MARKET PULSE BACKGROUND] Error processing {ticker}: {e}")
                
        # 2. Ingest Sovereign UK 10Y Gilt Exclusively via FT.com Scraper Engine
        if handle_gilt:
            try:
                from gilt_engine import GiltDataService
                from config import HISTORICAL_DIR
                
                gilt_service = GiltDataService()
                live_gilt_yield = gilt_service.fetch_live_ft_yield()
                parquet_path = HISTORICAL_DIR / "UK_GILT_BASELINE.parquet"
                
                # Resilient Fallback: If live scrape returns None, pull the last verified close from Parquet
                if live_gilt_yield is None and parquet_path.exists():
                    try:
                        df_gilt_hist = pd.read_parquet(parquet_path)
                        if not df_gilt_hist.empty:
                            live_gilt_yield = float(df_gilt_hist['Close'].iloc[-1])
                            logger.info(f"Live FT scrape returned None. Falling back to Parquet value: {live_gilt_yield}")
                    except Exception as ex:
                        logger.error(f"Failed to read Parquet fallback for market pulse: {ex}")
                
                if live_gilt_yield is not None:
                    gilt_prev_close: float = live_gilt_yield
                    
                    if parquet_path.exists():
                        try:
                            df_gilt_hist = pd.read_parquet(parquet_path)
                            if len(df_gilt_hist) >= 2:
                                gilt_prev_close = float(df_gilt_hist['Close'].iloc[-2])
                            elif len(df_gilt_hist) == 1:
                                gilt_prev_close = float(df_gilt_hist['Close'].iloc[-1])
                        except Exception:
                            pass
                            
                    gilt_change_pts: float = live_gilt_yield - gilt_prev_close
                    gilt_change_pct: float = (gilt_change_pts / gilt_prev_close) * 100.0 if gilt_prev_close != 0.0 else 0.0
                    
                    gilt_name: str = INDEX_TICKERS.get("UK10YG", "UK 10Y Gilt")
                    gilt_is_positive: int = int(gilt_change_pts >= 0)
                    
                    cursor.execute('''
                        INSERT OR REPLACE INTO market_pulse_cache 
                        (ticker, name, price, change_pts, change_pct, is_positive, last_updated)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', ("UK10YG", gilt_name, live_gilt_yield, gilt_change_pts, gilt_change_pct, gilt_is_positive, current_time))
                else:
                    # Enforce update boundary on scraper failure to avoid thread deadlock
                    cursor.execute("UPDATE market_pulse_cache SET last_updated = ? WHERE ticker = 'UK10YG'", (current_time,))
            except Exception as ex:
                logger.error(f"[MARKET PULSE BACKGROUND] FT Gilt pipeline execution failed: {ex}")
                
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[MARKET PULSE BACKGROUND] Batch download failed: {e}")
    finally:
        _FETCHING = False