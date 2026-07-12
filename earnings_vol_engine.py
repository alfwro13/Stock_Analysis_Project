import time
import random
import logging
from datetime import datetime, timedelta, timezone
from typing import List, Optional, Tuple

import numpy as np
import pandas as pd

from data_engine import load_or_fetch_daily_history
from database import get_connection, log_notification
from db_helpers import filter_equity_tickers, get_next_earnings_dates
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

def get_historical_earnings_move(ticker: str) -> Optional[float]:
    try:
        earnings_dates = yahoo_engine.get_earnings_dates(ticker, limit=10)
        if earnings_dates is None or earnings_dates.empty:
            return None

        if earnings_dates.index.tz is None:
            earnings_dates.index = earnings_dates.index.tz_localize(timezone.utc)
        now = pd.Timestamp.now(tz=timezone.utc)
        past_dates = earnings_dates[earnings_dates.index < now].index
        if len(past_dates) == 0:
            return None

        full_hist = load_or_fetch_daily_history(ticker)

        moves = []
        for e_date in past_dates[:4]:
            try:
                if full_hist is None or full_hist.empty:
                    break
                start_date = (e_date - timedelta(days=3)).strftime('%Y-%m-%d')
                end_date = (e_date + timedelta(days=4)).strftime('%Y-%m-%d')
                hist = full_hist.loc[start_date:end_date]
                if len(hist) < 2:
                    continue
                    
                # normalize() avoids [s] vs [us] resolution conflicts; utc=True standardises both to UTC-aware midnight
                hist_dates = pd.to_datetime(hist.index, utc=True).normalize()
                target_date = pd.to_datetime(e_date, utc=True).normalize()
                
                time_diffs = abs(hist_dates - target_date)
                closest_idx = time_diffs.argmin()
                
                # 2-session window (pre vs post close) captures AMC/BMO timing without guessing report time
                if closest_idx > 0 and (closest_idx + 1) < len(hist):
                    pre_close  = hist['Close'].iloc[closest_idx - 1]
                    post_close = hist['Close'].iloc[closest_idx + 1]
                    if pre_close > 0:
                        move_pct = abs((post_close - pre_close) / pre_close) * 100.0
                        moves.append(move_pct)
                
            except Exception as e:
                logger.debug("Could not calculate specific earnings event move: %s", e)
                continue
                
        if moves:
            return float(np.mean(moves))
            
    except Exception as e:
        logger.debug("Error fetching historical earnings dates: %s", e)
        
    return None

def get_implied_straddle_move(ticker: str, underlying_price: float, target_date: datetime) -> Tuple[Optional[float], int, Optional[str]]:
    try:
        options = yahoo_engine.get_options_expirations(ticker)
        if not options:
            return None, 0, None

        valid_expiries = [opt for opt in options if datetime.strptime(opt, '%Y-%m-%d').replace(tzinfo=timezone.utc) >= target_date]
        if not valid_expiries:
            return None, 0, None

        target_expiry = valid_expiries[0]
        chain_result = yahoo_engine.get_options_chain(ticker, target_expiry)
        if chain_result is None:
            return None, 0, None

        calls, puts = chain_result
        
        if calls.empty or puts.empty:
            return None, 0, None
            
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
        
        # Straddle cost / underlying = literal market-priced move; OI not volume as proxy (OI persists outside hours)
        implied_move_pct = (call_price + put_price) / underlying_price * 100.0
        volume = int(atm_call.get('openInterest', 0)) + int(atm_put.get('openInterest', 0))
        
        return implied_move_pct, volume, target_expiry

    except Exception as e:
        logger.debug("Error calculating implied straddle: %s", e)
        return None, 0, None

def run_earnings_vol_scan(ticker_list: List[str]) -> None:
    ticker_list = filter_equity_tickers(ticker_list)
    total_tickers = len(ticker_list)
    if not ticker_list:
        logger.warning("Ticker list is empty. Aborting scan.")
        return

    logger.info("Starting earnings volatility scan for %d assets...", total_tickers)
    log_notification("Info", f"Earnings Volatility Scan initiated for {total_tickers} assets.")
    
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        today = datetime.now(timezone.utc)
        cutoff_date = today + timedelta(days=14)

        cached_earnings_dates = get_next_earnings_dates(ticker_list)

        for i, ticker in enumerate(ticker_list):
            try:
                e_date_str = cached_earnings_dates.get(ticker, {}).get('next_earnings_date')
                if not e_date_str or e_date_str == 'Unknown':
                    continue

                try:
                    earnings_date = datetime.strptime(e_date_str, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                except ValueError:
                    continue

                if not (today <= earnings_date <= cutoff_date):
                    continue

                logger.info("Analyzing %s (Earnings Date: %s)...", ticker, e_date_str)

                hist = load_or_fetch_daily_history(ticker)
                hist = hist.tail(30) if hist is not None else pd.DataFrame()

                if hist.empty or len(hist) < 20:
                    logger.warning("Insufficient underlying price data available for %s. Skipping.", ticker)
                    continue

                underlying_price = hist['Close'].iloc[-1]

                hist = hist.copy()
                hist['Returns'] = np.log(hist['Close'] / hist['Close'].shift(1))
                historical_hv = hist['Returns'].std() * np.sqrt(252)

                if pd.isna(historical_hv) or historical_hv == 0:
                    continue

                hist_move_pct = get_historical_earnings_move(ticker)
                implied_move_pct, opt_volume, target_expiry = get_implied_straddle_move(ticker, underlying_price, earnings_date)
                
                if hist_move_pct is None or implied_move_pct is None or target_expiry is None:
                    logger.debug("Missing volatility parameters or liquidity for %s. Skipping edge calculation.", ticker)
                    continue

                # Subtract diffusion over (days_to_expiry - 1) days to isolate the earnings jump from theta
                target_expiry_date = datetime.strptime(target_expiry, '%Y-%m-%d').replace(tzinfo=timezone.utc)
                days_to_expiry = max((target_expiry_date - datetime.now(timezone.utc)).days, 1)
                non_earnings_days = max(days_to_expiry - 1, 0)
                
                daily_hv = historical_hv / np.sqrt(252)
                total_implied_pct = implied_move_pct / 100.0
                non_earn_pct = daily_hv * np.sqrt(non_earnings_days)
                isolated_variance = max(total_implied_pct**2 - non_earn_pct**2, 0)
                isolated_implied_move = np.sqrt(isolated_variance) * 100.0 if isolated_variance > 0 else 0.01

                edge_score = hist_move_pct - isolated_implied_move
                last_updated = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

                # Store isolated_implied_move in implied_move_pct column — reflects true event variance, not raw straddle
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
                
                logger.info("[%s] Edge: %.2f%% | Isolated Implied: %.2f%% (Raw: %.2f%%) | Hist: %.2f%%",
                            ticker, edge_score, isolated_implied_move, implied_move_pct, hist_move_pct)
                
            except Exception as e:
                logger.error("Error analyzing %s: %s", ticker, e)
                conn.rollback()
            finally:
                time.sleep(random.uniform(0.5, 1.5))

            processed = i + 1
            if total_tickers >= 4 and processed % max(1, total_tickers // 4) == 0 and processed < total_tickers:
                pct = int((processed / total_tickers) * 100)
                log_notification("Info", f"Earnings Volatility Scan Progress: {pct}% ({processed}/{total_tickers} tickers evaluated).")

        logger.info("Earnings volatility options scan complete.")
        log_notification("Success", f"Earnings Volatility Options Scan completed successfully across {total_tickers} tracked assets.")

    except Exception as e:
        logger.error("Fatal error during Earnings Scan: %s", e)
        log_notification("Error", f"Earnings Volatility Scan failed with a fatal error: {str(e)}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    # Standalone execution logic for testing
    test_tickers = ["AAPL", "NVDA", "MSFT", "TSLA"]
    run_earnings_vol_scan(test_tickers)