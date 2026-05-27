# quant_engine.py
import time
import random
import logging
from datetime import datetime
from typing import List

import pandas as pd
import yfinance as yf
import ta

# [DESIGN-04 FIXED] Import centralized notification helper
from database import get_connection, log_notification

logger = logging.getLogger(__name__)

def run_daily_quant_scan(ticker_list: List[str], scan_type: str = 'daily') -> None:
    """
    Downloads historical OHLCV data, calculates technical indicators using vectorization,
    and inserts the data into the local SQLite database. Includes resumability, throttling,
    and institutional audit-trail heartbeats.
    """
    total_tickers = len(ticker_list)
    if not ticker_list:
        logger.warning(f"Ticker list is empty for scan type '{scan_type}'. Aborting scan.")
        return

    today_str = datetime.now().strftime('%Y-%m-%d')
    
    conn = get_connection()
    cursor = conn.cursor()

    log_notification("Info", f"Quant Scan ({scan_type}) initiated for {total_tickers} tickers.")

    try:
        # ---------------------------------------------------------
        # 1. State Management & Resumability Check
        # ---------------------------------------------------------
        # [DESIGN-12 FIXED] Utilizing proper composite primary keys for state engine tracking
        cursor.execute(
            "SELECT last_processed_ticker, status FROM quant_scan_states WHERE scan_date = ? AND scan_type = ?",
            (today_str, scan_type)
        )
        state = cursor.fetchone()

        start_idx = 0
        if state:
            status = state['status']
            last_ticker = state['last_processed_ticker']
            
            if status == 'COMPLETED':
                logger.info(f"Scan '{scan_type}' for {today_str} already completed. Skipping execution.")
                conn.close()
                log_notification("Info", f"Quant Scan '{scan_type}' for {today_str} bypassed (Already Completed).")
                return
                
            elif status == 'IN_PROGRESS' and last_ticker in ticker_list:
                # Find index of last processed and resume from the NEXT ticker
                start_idx = ticker_list.index(last_ticker) + 1
                resume_ticker = ticker_list[start_idx] if start_idx < len(ticker_list) else 'END'
                logger.info(f"Resuming incomplete '{scan_type}' scan for {today_str}. Starting from {resume_ticker}.")
                log_notification("Info", f"Resuming incomplete Quant Scan ({scan_type}) from {resume_ticker}.")
        else:
            # Initialize new daily state using the normalized schema
            cursor.execute(
                "INSERT INTO quant_scan_states (scan_date, scan_type, last_processed_ticker, status) VALUES (?, ?, ?, ?)",
                (today_str, scan_type, "", "IN_PROGRESS")
            )
            conn.commit()

        # ---------------------------------------------------------
        # 2. Sequential Throttled Download & Processing
        # ---------------------------------------------------------
        for i in range(start_idx, total_tickers):
            ticker = ticker_list[i]
            logger.info(f"Processing {ticker} ({i + 1}/{total_tickers}) [{scan_type}]...")
            
            try:
                # Fetch 2-years of data to guarantee an accurate 200-day SMA baseline
                df = yf.download(ticker, period="2y", interval="1d", progress=False, auto_adjust=True)
                
                if df.empty:
                    logger.warning(f"No OHLCV data returned for {ticker}. Skipping.")
                    continue

                # Handle multi-index columns returned by newer yfinance versions
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)

                df.dropna(subset=['Close', 'Volume'], inplace=True)
                
                if len(df) < 200:
                    logger.warning(f"Insufficient historical data for {ticker} (requires >= 200 days for SMA-200). Skipping.")
                    continue

                # --- Technical Indicator Math (Vectorized via pandas & ta) ---
                close_s = df['Close'].squeeze()
                volume_s = df['Volume'].squeeze()

                rsi_series = ta.momentum.RSIIndicator(close=close_s, window=14).rsi()
                macd_indicator = ta.trend.MACD(close=close_s)
                sma_50 = ta.trend.SMAIndicator(close=close_s, window=50).sma_indicator()
                sma_200 = ta.trend.SMAIndicator(close=close_s, window=200).sma_indicator()
                
                # Replaced 'ta' call with raw pandas rolling mean for reliability
                vol_sma_20 = volume_s.rolling(window=20).mean()
                
                # Calculate ATR and ATR Percentage for Position Sizing
                atr_series = ta.volatility.AverageTrueRange(high=df['High'], low=df['Low'], close=df['Close'], window=14).average_true_range()
                atr_pct_series = atr_series / df['Close']

                # Extract latest localized date and metrics
                last_date = df.index[-1].strftime('%Y-%m-%d')
                c_price = float(close_s.iloc[-1])
                c_vol = int(volume_s.iloc[-1])
                
                c_rsi = float(rsi_series.iloc[-1]) if not pd.isna(rsi_series.iloc[-1]) else None
                c_macd = float(macd_indicator.macd().iloc[-1]) if not pd.isna(macd_indicator.macd().iloc[-1]) else None
                c_signal = float(macd_indicator.macd_signal().iloc[-1]) if not pd.isna(macd_indicator.macd_signal().iloc[-1]) else None
                c_hist = float(macd_indicator.macd_diff().iloc[-1]) if not pd.isna(macd_indicator.macd_diff().iloc[-1]) else None
                c_sma50 = float(sma_50.iloc[-1]) if not pd.isna(sma_50.iloc[-1]) else None
                c_sma200 = float(sma_200.iloc[-1]) if not pd.isna(sma_200.iloc[-1]) else None
                c_atr_pct = float(atr_pct_series.iloc[-1]) if not pd.isna(atr_pct_series.iloc[-1]) else None

                # Logic Triggers
                vol_surge = False
                if not pd.isna(vol_sma_20.iloc[-1]):
                    vol_surge = bool(c_vol > (vol_sma_20.iloc[-1] * 1.5))

                bullish_cross = False
                # [MATH-09 RESOLUTION] This strictly manages the DB True/False flag for the Screener without double-scoring
                if c_macd is not None and c_signal is not None and len(macd_indicator.macd()) >= 2 and not pd.isna(macd_indicator.macd().iloc[-2]):
                    # Golden MACD Cross: MACD crosses ABOVE signal line
                    prev_macd = macd_indicator.macd().iloc[-2]
                    prev_sig = macd_indicator.macd_signal().iloc[-2]
                    bullish_cross = bool((c_macd > c_signal) and (prev_macd <= prev_sig))

                # --- Database Write (Safe Upsert) ---
                cursor.execute('''
                    INSERT INTO quant_signals 
                    (ticker, date, close_price, volume, rsi_14, macd, macd_signal, macd_hist, sma_50, sma_200, volume_surge, bullish_cross, atr_pct)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    ON CONFLICT(ticker, date) DO UPDATE SET
                        close_price=excluded.close_price,
                        volume=excluded.volume,
                        rsi_14=excluded.rsi_14,
                        macd=excluded.macd,
                        macd_signal=excluded.macd_signal,
                        macd_hist=excluded.macd_hist,
                        sma_50=excluded.sma_50,
                        sma_200=excluded.sma_200,
                        volume_surge=excluded.volume_surge,
                        bullish_cross=excluded.bullish_cross,
                        atr_pct=excluded.atr_pct
                ''', (ticker, last_date, c_price, c_vol, c_rsi, c_macd, c_signal, c_hist, c_sma50, c_sma200, vol_surge, bullish_cross, c_atr_pct))

                # Update State Engine
                cursor.execute("UPDATE quant_scan_states SET last_processed_ticker = ? WHERE scan_date = ? AND scan_type = ?", (ticker, today_str, scan_type))
                conn.commit()

            except Exception as e:
                logger.error(f"Error analyzing {ticker}: {str(e)}")
                conn.rollback() # Prevent partial/corrupted inserts
            finally:
                # Mandatory Throttling to prevent Yahoo Finance IP bans
                time.sleep(random.uniform(0.5, 1.5))

            # --- Progress Heartbeat ---
            processed = i + 1
            if total_tickers >= 4 and processed % max(1, total_tickers // 4) == 0 and processed < total_tickers:
                pct = int((processed / total_tickers) * 100)
                log_notification("Info", f"Quant Scan ({scan_type}) Progress: {pct}% ({processed}/{total_tickers} tickers processed).")

        # ---------------------------------------------------------
        # 3. Finalize State
        # ---------------------------------------------------------
        cursor.execute("UPDATE quant_scan_states SET status = 'COMPLETED' WHERE scan_date = ? AND scan_type = ?", (today_str, scan_type))
        conn.commit()
        
        logger.info(f"Quant scan '{scan_type}' for {today_str} successfully finished executing.")
        log_notification("Success", f"Quant Scan ({scan_type}) completed successfully. All {total_tickers} tracked assets processed.")

    except Exception as e:
        logger.error(f"Fatal error during Quant Scan '{scan_type}': {str(e)}")
        log_notification("Error", f"Quant Scan ({scan_type}) failed with a fatal error: {str(e)}")
    finally:
        conn.close()

if __name__ == "__main__":
    # Test script standalone
    run_daily_quant_scan(["AAPL", "MSFT", "NVDA"], scan_type='test')