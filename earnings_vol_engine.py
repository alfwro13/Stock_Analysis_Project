# earnings_vol_engine.py
import time
import random
import logging
from datetime import datetime, timedelta
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd
import yfinance as yf

# [DESIGN-04 FIXED] Import centralized notification helper
from database import get_connection, log_notification

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - EARNINGS_VOL_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

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
                    
                # BULLETPROOF FIX: Convert everything to pure, timezone-naive normalized dates (00:00:00)
                # This completely destroys the [s] vs [us] resolution conflict crashing Pandas
                hist_dates = pd.to_datetime(hist.index).tz_localize(None).normalize()
                target_date = pd.to_datetime(e_date).tz_localize(None).normalize()
                
                # Calculate absolute differences in days to find the closest trading day
                time_diffs = abs(hist_dates - target_date)
                closest_idx = time_diffs.argmin()
                
                # Calculate the percentage gap using pre-earnings Close and post-earnings Close
                # [ISSUE-M09 FIXED] Shifted from post-open to post-close to capture the full session move
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

def get_implied_straddle_move(ticker_obj: yf.Ticker, underlying_price: float, target_date: datetime) -> Tuple[Optional[float], int, Optional[str]]:
    """
    Finds the At-The-Money (ATM) Call and Put for the nearest expiration after the target date.
    Calculates the implied move % based on Straddle cost and records the combined options volume.
    Returns: (implied_move_pct, volume, target_expiry_str)
    """
    try:
        options = ticker_obj.options
        if not options:
            return None, 0, None
            
        # Find the first expiration date that occurs AFTER the earnings report
        valid_expiries = [opt for opt in options if datetime.strptime(opt, '%Y-%m-%d') >= target_date]
        if not valid_expiries:
            return None, 0, None
            
        target_expiry = valid_expiries[0]
        chain = ticker_obj.option_chain(target_expiry)
        
        calls = chain.calls
        puts = chain.puts
        
        if calls.empty or puts.empty:
            return None, 0, None
            
        # Locate the ATM Strike (closest mathematically to the current underlying price)
        atm_strike = calls.iloc[(calls['strike'] - underlying_price).abs().argsort()[:1]]['strike'].values[0]
        
        atm_call = calls[calls['strike'] == atm_strike].iloc[0]
        atm_put = puts[puts['strike'] == atm_strike].iloc[0]
        
        # STRICT LIQUIDITY REQUIREMENT: Reject lastPrice fallbacks for untradable illiquid chains
        def get_price(opt_row) -> Optional[float]:
            if opt_row['bid'] > 0 and opt_row['ask'] > 0:
                return (opt_row['bid'] + opt_row['ask']) / 2.0
            return None
            
        call_price = get_price(atm_call)
        put_price = get_price(atm_put)
        
        if call_price is None or put_price is None:
            return None, 0, None
        
        # [ISSUE-M10 FIXED] Apply practitioner correction factor (~0.84) to ATM straddle to account for positive gamma/intrinsic value
        straddle_cost = (call_price + put_price) * 0.84
        implied_move_pct = (straddle_cost / underlying_price) * 100.0
        
        # Aggregate liquidity indicator
        volume = int(atm_call.get('volume', 0)) + int(atm_put.get('volume', 0))
        
        return implied_move_pct, volume, target_expiry

    except Exception as e:
        logger.debug(f"Error calculating implied straddle: {e}")
        return None, 0, None

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
        
        today = datetime.now()
        cutoff_date = today + timedelta(days=14)

        for i, ticker in enumerate(ticker_list):
            try:
                ticker_obj = yf.Ticker(ticker)
                earnings_date = None
                e_date_str = None
                
                # 1. LIVE VALIDATION: Strictly fetch fresh earnings dates from the API, bypassing stale SQLite values
                try:
                    # Method 1: Check .info dictionary first (fastest)
                    info = ticker_obj.info
                    earnings_ts = info.get('earningsTimestamp')
                    if earnings_ts:
                        earnings_date = datetime.fromtimestamp(earnings_ts)
                        
                    # Method 2: Validate against get_earnings_dates calendar (most accurate)
                    live_dates = ticker_obj.get_earnings_dates(limit=5)
                    if live_dates is not None and not live_dates.empty:
                        now_tz_naive = pd.Timestamp.now(tz='UTC').tz_localize(None)
                        if live_dates.index.tz is not None:
                            live_dates.index = live_dates.index.tz_convert('UTC').tz_localize(None)
                            
                        future_dates = live_dates.index[live_dates.index >= now_tz_naive]
                        if len(future_dates) > 0:
                            earnings_date = future_dates.min().to_pydatetime()
                            
                    if earnings_date:
                        e_date_str = earnings_date.strftime('%Y-%m-%d')
                except Exception as e:
                    logger.debug(f"Failed to fetch live earnings date for {ticker}: {e}")

                if not earnings_date:
                    continue

                # Bypass if the validated live date falls outside our strict 14-day actionable window
                if not (today <= earnings_date <= cutoff_date):
                    continue
                    
                logger.info(f"Analyzing {ticker} (Live Earnings Date: {e_date_str})...")
                
                # Fetch 1 month of history to calculate base Historical Volatility
                hist = ticker_obj.history(period="1mo")
                
                if hist.empty or len(hist) < 20:
                    logger.warning(f"Insufficient underlying price data available for {ticker}. Skipping.")
                    continue
                    
                underlying_price = hist['Close'].iloc[-1]
                
                # Calculate Base Historical Volatility (HV) for isolation math
                hist['Returns'] = np.log(hist['Close'] / hist['Close'].shift(1))
                historical_hv = hist['Returns'].std() * np.sqrt(252)
                
                if pd.isna(historical_hv) or historical_hv == 0:
                    continue
                
                # 2. Calculate Mathematical Vectors
                hist_move_pct = get_historical_earnings_move(ticker_obj)
                implied_move_pct, opt_volume, target_expiry = get_implied_straddle_move(ticker_obj, underlying_price, earnings_date)
                
                if hist_move_pct is None or implied_move_pct is None or target_expiry is None:
                    logger.debug(f"Missing volatility parameters or liquidity for {ticker}. Skipping edge calculation.")
                    continue

                # 3. ISOLATE EARNINGS MOVE: Strip out non-earnings volatility (theta decay)
                target_expiry_date = datetime.strptime(target_expiry, '%Y-%m-%d')
                days_to_expiry = (target_expiry_date - earnings_date).days
                non_earnings_days = max(days_to_expiry - 1, 0)
                
                daily_hv = historical_hv / np.sqrt(252)
                non_earnings_component = daily_hv * np.sqrt(non_earnings_days) * 100.0
                
                isolated_implied_move = max(implied_move_pct - non_earnings_component, 0.01) # Prevent negative implied moves

                # Calculate True Options Mispricing Edge
                edge_score = hist_move_pct - isolated_implied_move
                last_updated = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

                # 4. Save to Database
                # We store isolated_implied_move as implied_move_pct because it reflects the true expected event variance
                cursor.execute('''
                    INSERT OR REPLACE INTO earnings_volatility 
                    (ticker, next_earnings_date, implied_move_pct, historical_avg_move_pct, edge_score, options_volume, last_updated)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    ticker, 
                    e_date_str, 
                    round(isolated_implied_move, 2), 
                    round(hist_move_pct, 2), 
                    round(edge_score, 2), 
                    opt_volume, 
                    last_updated
                ))
                conn.commit()
                
                logger.info(f"[{ticker}] Edge: {edge_score:.2f}% | Isolated Implied: {isolated_implied_move:.2f}% (Raw: {implied_move_pct:.2f}%) | Hist: {hist_move_pct:.2f}%")
                
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