import time
import logging
import threading
from typing import List, Dict, Any
import pandas as pd

from config import load_config, HISTORICAL_DIR
from database import get_connection
from utils import normalize_ticker
from gilt_engine import GiltDataService
from yahoo_engine import yahoo_engine
from time_engine import is_trading_session

logger = logging.getLogger(__name__)

# UK10YG is registered directly below GBPUSD=X so its tile appears adjacent in the UI.
INDEX_TICKERS: Dict[str, str] = {
    "^FTSE": "UK FTSE 100",
    "^FTMC": "UK FTSE 250",
    "GBPUSD=X": "GBP/USD",
    "BZ=F": "Brent Crude",
    "UK10YG": "UK 10Y Gilt",
    "^GSPC": "US S&P 500",
    "^NDX": "US Nasdaq 100",
    "^TYX": "US 30Y Yield",
    "^TNX": "US 10Y Yield",
    "DX-Y.NYB": "US Dollar Index"
}

# Non-blocking lock prevents duplicate concurrent fetches without a check-then-set race.
_FETCH_LOCK = threading.Lock()


def get_all_cached_pulse() -> Dict[str, Dict[str, Any]]:
    """Returns all pulse data from DB for Jinja template pre-rendering."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT ticker, name, price, change_pts, change_pct, is_positive, last_updated FROM market_pulse_cache")
        rows = cursor.fetchall()
    except Exception as e:
        logger.error("[MARKET PULSE] Failed to read pulse cache: %s", e)
        return {}
    finally:
        conn.close()

    config_data = load_config()
    refresh_rate: int = int(config_data.get("UI_PREFERENCES", {}).get("REFRESH_RATE", 60))
    current_time: float = time.time()
    trading_now: bool = is_trading_session()

    cache: Dict[str, Dict[str, Any]] = {}
    for row in rows:
        age = current_time - row['last_updated']
        has_data = row['last_updated'] > 0 and row['price'] != 0.0
        if not trading_now:
            is_stale: bool = not has_data
        else:
            is_stale = not has_data or age > refresh_rate * 2
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
    """Returns cached pulse prices (with staleness flag + latest FinBERT sentiment) split into indexes vs. assets."""
    if asset_tickers is None:
        asset_tickers = []

    asset_tickers = [normalize_ticker(t) for t in asset_tickers]

    config_data = load_config()
    ignored_tickers = {normalize_ticker(t) for t in config_data.get("IGNORED_TICKERS", [])}

    seen: set = set(INDEX_TICKERS.keys())
    requested_assets: List[str] = []
    for t in asset_tickers:
        if t not in seen and t not in ignored_tickers:
            seen.add(t)
            requested_assets.append(t)
    all_tickers: List[str] = list(INDEX_TICKERS.keys()) + requested_assets
    
    conn = get_connection()
    rows: List[Any] = []
    sentiment_scores: Dict[str, float] = {}
    try:
        cursor = conn.cursor()

        if all_tickers:
            placeholders = ','.join('?' for _ in all_tickers)

            cursor.execute(f"SELECT ticker, name, price, change_pts, change_pct, is_positive, last_updated FROM market_pulse_cache WHERE ticker IN ({placeholders})", all_tickers)
            rows = cursor.fetchall()

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
    except Exception as e:
        logger.error("[MARKET PULSE] Failed to read pulse from DB: %s", e)
        return {"indexes": [], "assets": []}
    finally:
        conn.close()

    results: Dict[str, List[Dict[str, Any]]] = {"indexes": [], "assets": []}
    current_time: float = time.time()
    trading_now: bool = is_trading_session()

    db_map: Dict[str, Any] = {row['ticker']: row for row in rows}

    for t in all_tickers:
        if t in db_map:
            row = db_map[t]
            age = current_time - row['last_updated']
            has_data = row['last_updated'] > 0 and row['price'] != 0.0
            if not trading_now:
                is_stale: bool = not has_data
                needs_refresh: bool = False
            else:
                is_stale = not has_data or age > refresh_rate * 2
                needs_refresh = not has_data or age > int(refresh_rate)
            data_obj: Dict[str, Any] = {
                "ticker": t,
                "name": row['name'],
                "price": row['price'],
                "change_pts": row['change_pts'],
                "change_pct": row['change_pct'],
                "is_positive": bool(row['is_positive']),
                "is_stale": is_stale,
                "needs_refresh": needs_refresh,
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
                "is_stale": trading_now,
                "needs_refresh": trading_now,
                "sentiment_score": sentiment_scores.get(t, None)
            }
            
        if t in INDEX_TICKERS:
            results["indexes"].append(data_obj)
        else:
            results["assets"].append(data_obj)

    return results


def fetch_and_save_pulse(tickers_to_fetch: List[str]) -> None:
    """Fetches live ticks from Yahoo Finance and saves to DB; UK10YG is sourced exclusively from FT.com."""
    if not _FETCH_LOCK.acquire(blocking=False):
        return

    conn = None
    try:
        handle_gilt: bool = False
        if "UK10YG" in tickers_to_fetch:
            handle_gilt = True
            tickers_to_fetch = [t for t in tickers_to_fetch if t != "UK10YG"]
            
        daily_dfs: dict = {}
        live_dfs: dict = {}

        if tickers_to_fetch:
            daily_dfs = yahoo_engine.get_price_history(tickers_to_fetch, period="5d", interval="1d")
            live_dfs = yahoo_engine.get_intraday(tickers_to_fetch, period="2d", interval="2m", prepost=True)
                
        conn = get_connection()
        cursor = conn.cursor()
        current_time: float = time.time()

        # Pre-fetch existing cache rows for all tickers in one query to avoid N+1 lookups
        existing_cache: dict = {}
        if tickers_to_fetch:
            placeholders = ','.join('?' for _ in tickers_to_fetch)
            cursor.execute(
                f"SELECT ticker, price FROM market_pulse_cache WHERE ticker IN ({placeholders})",
                tickers_to_fetch,
            )
            existing_cache = {row['ticker']: row['price'] for row in cursor.fetchall()}

        for ticker in tickers_to_fetch:
            try:
                t_daily: pd.DataFrame = daily_dfs.get(ticker, pd.DataFrame())
                t_live: pd.DataFrame = live_dfs.get(ticker, pd.DataFrame())

                if not t_daily.empty:
                    t_daily = t_daily.dropna(subset=['Close'])
                if not t_live.empty:
                    t_live = t_live.dropna(subset=['Close'])

                if t_daily.empty and ticker not in INDEX_TICKERS:
                    fb = yahoo_engine.get_single_ticker_history(ticker, period="5d")
                    if fb is not None and not fb.empty:
                        fb = fb.dropna(subset=['Close'])
                        if not fb.empty:
                            t_daily = fb

                if t_daily.empty:
                    # No daily data at all — transient outage or genuinely invalid ticker.
                    price_in_cache = existing_cache.get(ticker)
                    in_cache = ticker in existing_cache
                    if in_cache and price_in_cache:
                        cursor.execute(
                            "UPDATE market_pulse_cache SET last_updated = ? WHERE ticker = ?",
                            (current_time, ticker)
                        )
                    elif in_cache:
                        cursor.execute(
                            "UPDATE market_pulse_cache SET last_updated = 0 WHERE ticker = ?",
                            (ticker,)
                        )
                    else:
                        name = INDEX_TICKERS.get(ticker, ticker)
                        cursor.execute(
                            "INSERT INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, is_positive, last_updated) VALUES (?, ?, ?, ?, ?, ?, ?)",
                            (ticker, name, 0.0, 0.0, 0.0, 1, 0)
                        )
                    continue

                if t_live.empty:
                    # Daily-priced instrument (e.g. mutual fund) — use most recent daily close.
                    current_price = float(t_daily['Close'].iloc[-1])
                    prev_close = float(t_daily['Close'].iloc[-2]) if len(t_daily) >= 2 else current_price
                else:
                    current_price = float(t_live['Close'].iloc[-1])
                    last_daily_date = t_daily.index[-1].date()
                    live_date = t_live.index[-1].date()
                    if last_daily_date >= live_date and len(t_daily) >= 2:
                        prev_close = float(t_daily['Close'].iloc[-2])
                    else:
                        prev_close = float(t_daily['Close'].iloc[-1])
                    
                change_pts: float = current_price - prev_close
                change_pct: float = (change_pts / prev_close) * 100.0 if not pd.isna(prev_close) and prev_close != 0 else 0.0

                if abs(change_pct) > 50.0:
                    logger.warning("Skipping %s: implausible daily change %.1f%% (possible split mismatch)", ticker, change_pct)
                    continue

                name: str = INDEX_TICKERS.get(ticker, ticker)
                is_positive: int = int(change_pts >= 0)
                
                cursor.execute('''
                    INSERT OR REPLACE INTO market_pulse_cache 
                    (ticker, name, price, change_pts, change_pct, is_positive, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (ticker, name, current_price, change_pts, change_pct, is_positive, current_time))
                
            except Exception as e:
                logger.error(f"[MARKET PULSE BACKGROUND] Error processing {ticker}: {e}")
                
        if handle_gilt:
            try:
                gilt_service = GiltDataService()
                live_gilt_yield = gilt_service.fetch_live_ft_yield()
                parquet_path = HISTORICAL_DIR / "UK_GILT_BASELINE.parquet"
                
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
                            logger.debug("Could not parse gilt history close price, using default prev_close")

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
                    cursor.execute("SELECT price FROM market_pulse_cache WHERE ticker = 'UK10YG'")
                    existing_gilt = cursor.fetchone()
                    if existing_gilt is not None and existing_gilt['price']:
                        cursor.execute(
                            "UPDATE market_pulse_cache SET last_updated = ? WHERE ticker = 'UK10YG'",
                            (current_time,)
                        )
                    else:
                        cursor.execute("UPDATE market_pulse_cache SET last_updated = 0 WHERE ticker = 'UK10YG'")
            except Exception as ex:
                logger.error(f"[MARKET PULSE BACKGROUND] FT Gilt pipeline execution failed: {ex}")
                
        conn.commit()
    except Exception as e:
        logger.error(f"[MARKET PULSE BACKGROUND] Batch download failed: {e}")
    finally:
        if conn:
            conn.close()
        _FETCH_LOCK.release()