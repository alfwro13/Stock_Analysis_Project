# earnings_vol_engine.py
import time
import random
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

from database import get_connection

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - EARNINGS_VOL_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def log_notification(message_type: str, message_text: str) -> None:
    """Helper function to log scan progress to the system notification center."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)",
            (message_type, message_text)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to log notification: {e}")
    finally:
        conn.close()

def get_historical_earnings_move(ticker_obj: yf.Ticker) -> Optional[float]:
    """
    Calculates the average absolute percentage gap of the last 4 earnings events.
    Uses yfinance's earnings dates to isolate price action immediately following reports.
    """
    try:
        # Retrieve recent historical earnings calendar dates
        earnings_dates = ticker_obj.get_earnings_dates(limit=10)
        if earnings_dates is None or earnings_dates.empty:
            return None
            
        # Safely handle timezone matching between the current time and the index
        now = pd.Timestamp.now(tz='UTC')
        if earnings_dates.index.tz is None:
            now = now.tz_localize(None)
            
        # Filter for dates strictly in the past
        past_dates = earnings_dates[earnings_dates.index < now].index
        if len(past_dates) == 0:
            return None

        moves = []
        # Analyze up to the last 4 reported quarters
        for e_date in past_dates[:4]:
            try:
                # Fetch a short window around the earnings date to capture the gap
                start_date = (e_date - timedelta(days=3)).strftime('%Y-%m-%d')
                end_date = (e_date + timedelta(days=4)).strftime('%Y-%m-%d')
                
                hist = ticker_obj.history(start=start_date, end=end_date)
                if len(hist) < 2:
                    continue
                    
                # CRITICAL FIX: Strip timezones from BOTH the history index and the target date 
                # to prevent pandas dtype comparison crashes (tz-aware vs tz-naive)
                if hist.index.tz is not None:
                    hist.index = hist.index.tz_localize(None)
                    
                tz_naive_date = e_date.tz_localize(None) if e_date.tz is not None else e_date
                
                # Find the exact index closest to the earnings date
                closest_idx = hist.index.get_indexer([tz_naive_date], method='nearest')[0]
                
                # Calculate Close-to-Close jump over the binary earnings event
                if closest_idx > 0 and closest_idx < len(hist):
                    pre_close = hist['Close'].iloc[closest_idx - 1]
                    post_close = hist['Close'].iloc[closest_idx]
                    
                    if pre_close > 0:
                        pct_move = abs((post_close - pre_close) / pre_close) * 100.0
                        moves.append(pct_move)
            except Exception as e:
                logger.debug(f"Could not calculate specific earnings event move: {e}")
                continue
                
        if moves:
            return float(np.mean(moves))
            
    except Exception as e:
        logger.debug(f"Error fetching historical earnings dates: {e}")
        
    return None

def get_implied_straddle_move(ticker_obj: yf.Ticker, underlying_price: float, target_date: datetime) -> Tuple[Optional[float], int]:
    """
    Finds the At-The-Money (ATM) Call and Put for the nearest expiration after the target date.
    Calculates the implied move % based on Straddle cost and records the combined options volume.
    """
    try:
        options = ticker_obj.options
        if not options:
            return None, 0
            
        # Find the first expiration date that occurs AFTER the earnings report
        valid_expiries = [opt for opt in options if datetime.strptime(opt, '%Y-%m-%d') >= target_date]
        if not valid_expiries:
            return None, 0
            
        target_expiry = valid_expiries[0]
        chain = ticker_obj.option_chain(target_expiry)
        
        calls = chain.calls
        puts = chain.puts
        
        if calls.empty or puts.empty:
            return None, 0
            
        # Locate the ATM Strike (closest mathematically to the current underlying price)
        atm_strike = calls.iloc[(calls['strike'] - underlying_price).abs().argsort()[:1]]['strike'].values[0]
        
        atm_call = calls[calls['strike'] == atm_strike].iloc[0]
        atm_put = puts[puts['strike'] == atm_strike].iloc[0]
        
        # Prefer Mid-Price (Bid/Ask spread) if available, otherwise fallback to lastPrice
        def get_price(opt_row):
            if opt_row['bid'] > 0 and opt_row['ask'] > 0:
                return (opt_row['bid'] + opt_row['ask']) / 2.0
            return opt_row['lastPrice']
            
        call_price = get_price(atm_call)
        put_price = get_price(atm_put)
        
        straddle_cost = call_price + put_price
        implied_move_pct = (straddle_cost / underlying_price) * 100.0
        
        # Aggregate liquidity indicator
        volume = int(atm_call.get('volume', 0)) + int(atm_put.get('volume', 0))
        
        return implied_move_pct, volume

    except Exception as e:
        logger.debug(f"Error calculating implied straddle: {e}")
        return None, 0

def run_earnings_vol_scan(ticker_list: List[str]) -> None:
    """
    Iterates through assets, filters for upcoming earnings within the next 14 days, 
    calculates quantitative option mispricings (Edge), and saves to the SQLite database.
    """
    total_tickers = len(ticker_list)
    if not ticker_list:
        logger.warning("Ticker list is empty. Aborting scan.")
        return

    logger.info(f"Starting earnings volatility scan for {total_tickers} assets...")
    log_notification("Info", f"Earnings Volatility Scan initiated for {total_tickers} assets.")
    
    try:
        conn = get_connection()
        cursor = conn.cursor()
        
        # Pre-fetch existing earnings dates from the core tracker table to minimize redundant API hits
        placeholders = ','.join('?' for _ in ticker_list)
        cursor.execute(f"SELECT ticker, next_earnings_date FROM stock_signals WHERE ticker IN ({placeholders})", ticker_list)
        db_earnings_map = {row['ticker']: row['next_earnings_date'] for row in cursor.fetchall()}

        today = datetime.now()
        cutoff_date = today + timedelta(days=14)

        for i, ticker in enumerate(ticker_list):
            try:
                # 1. Evaluate Earnings Timeline
                e_date_str = db_earnings_map.get(ticker)
                if not e_date_str or e_date_str == 'Unknown':
                    continue
                    
                try:
                    earnings_date = datetime.strptime(e_date_str, '%Y-%m-%d')
                except ValueError:
                    continue

                # Bypass if earnings are more than 14 days away or have already passed
                if not (today <= earnings_date <= cutoff_date):
                    continue
                    
                logger.info(f"Analyzing {ticker} (Earnings Date: {e_date_str})...")
                
                ticker_obj = yf.Ticker(ticker)
                hist = ticker_obj.history(period="5d")
                
                if hist.empty:
                    logger.warning(f"No underlying price data available for {ticker}. Skipping.")
                    continue
                    
                underlying_price = hist['Close'].iloc[-1]
                
                # 2. Calculate Mathematical Vectors
                hist_move_pct = get_historical_earnings_move(ticker_obj)
                implied_move_pct, opt_volume = get_implied_straddle_move(ticker_obj, underlying_price, earnings_date)
                
                # Require both metrics to calculate a valid edge
                if hist_move_pct is None or implied_move_pct is None:
                    logger.debug(f"Missing volatility parameters for {ticker}. Skipping edge calculation.")
                    continue

                # 3. Calculate the Options Mispricing Edge 
                # (Positive indicates options are mathematically underpriced relative to historical reality)
                edge_score = hist_move_pct - implied_move_pct
                last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # 4. Save to Database securely
                cursor.execute('''
                    INSERT OR REPLACE INTO earnings_volatility 
                    (ticker, next_earnings_date, implied_move_pct, historical_avg_move_pct, edge_score, options_volume, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    ticker, 
                    e_date_str, 
                    round(implied_move_pct, 2), 
                    round(hist_move_pct, 2), 
                    round(edge_score, 2), 
                    opt_volume, 
                    last_updated
                ))
                conn.commit()
                
                logger.info(f"[{ticker}] Edge: {edge_score:.2f}% | Implied: {implied_move_pct:.2f}% | Hist: {hist_move_pct:.2f}%")
                
            except Exception as e:
                logger.error(f"Error analyzing {ticker}: {str(e)}")
                conn.rollback()
            finally:
                # Mandated randomized API throttling
                time.sleep(random.uniform(0.5, 1.5))

            # --- Progress Heartbeat ---
            processed = i + 1
            if total_tickers >= 4 and processed % max(1, total_tickers // 4) == 0 and processed < total_tickers:
                pct = int((processed / total_tickers) * 100)
                log_notification("Info", f"Earnings Volatility Scan Progress: {pct}% ({processed}/{total_tickers} tickers evaluated).")

        logger.info("Earnings volatility options scan complete.")
        log_notification("Success", f"Earnings Volatility Options Scan completed successfully across {total_tickers} tracked assets.")

    except Exception as e:
        logger.error(f"Fatal error during Earnings Scan: {str(e)}")
        log_notification("Error", f"Earnings Volatility Scan failed with a fatal error: {str(e)}")
    finally:
        conn.close()

if __name__ == "__main__":
    # Standalone execution logic for testing
    test_tickers = ["AAPL", "NVDA", "MSFT", "TSLA"]
    run_earnings_vol_scan(test_tickers)