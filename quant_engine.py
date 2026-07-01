import time
import random
import logging
from datetime import datetime, timezone
from typing import List

import pandas as pd
from data_engine import load_or_fetch_daily_history
from indicators import (
    compute_rsi,
    compute_macd,
    compute_smas,
    compute_atr,
    compute_volume_sma,
    compute_volume_surge,
    compute_bullish_cross,
    compute_volume_profile,
    compute_keltner_channel,
)

from database import get_connection, log_notification

logger = logging.getLogger(__name__)

# GUI name: "Daily Quant Screener (Portfolio & Watchlist)". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.

def run_daily_quant_scan(ticker_list: List[str], scan_type: str = 'daily') -> None:
    """Vectorised TA scan: downloads 2y OHLCV, computes indicators, upserts quant_signals; resumable via quant_scan_states."""
    total_tickers = len(ticker_list)
    if not ticker_list:
        logger.warning("Ticker list is empty for scan type '%s'. Aborting scan.", scan_type)
        return

    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        log_notification("Info", f"Quant Scan ({scan_type}) initiated for {total_tickers} tickers.")

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
                logger.info("Scan '%s' for %s already completed. Skipping execution.", scan_type, today_str)
                log_notification("Info", f"Quant Scan '{scan_type}' for {today_str} bypassed (Already Completed).")
                return

            elif status == 'IN_PROGRESS' and last_ticker in ticker_list:
                start_idx = ticker_list.index(last_ticker) + 1
                resume_ticker = ticker_list[start_idx] if start_idx < len(ticker_list) else 'END'
                logger.info("Resuming incomplete '%s' scan for %s. Starting from %s.", scan_type, today_str, resume_ticker)
                log_notification("Info", f"Resuming incomplete Quant Scan ({scan_type}) from {resume_ticker}.")
        else:
            cursor.execute(
                "INSERT INTO quant_scan_states (scan_date, scan_type, last_processed_ticker, status) VALUES (?, ?, ?, ?)",
                (today_str, scan_type, "", "IN_PROGRESS")
            )
            conn.commit()

        for i in range(start_idx, total_tickers):
            ticker = ticker_list[i]
            logger.info("Processing %s (%d/%d) [%s]...", ticker, i + 1, total_tickers, scan_type)
            
            try:
                # 2 years guarantees an accurate 200-day SMA baseline
                df = load_or_fetch_daily_history(ticker)
                if df is None:
                    df = pd.DataFrame()

                if df.empty:
                    logger.warning("No OHLCV data returned for %s. Skipping.", ticker)
                    continue

                df.dropna(subset=['Close', 'Volume'], inplace=True)

                if len(df) < 200:
                    logger.warning("Insufficient historical data for %s (requires >= 200 days for SMA-200). Skipping.", ticker)
                    continue

                close_s = df['Close'].squeeze()
                volume_s = df['Volume'].squeeze()

                rsi_series                  = compute_rsi(close_s)
                macd_series, signal_series, hist_series = compute_macd(close_s)
                smas                        = compute_smas(close_s, [50, 200])
                sma_50                      = smas[50]
                sma_200                     = smas[200]
                vol_sma_20                  = compute_volume_sma(volume_s)
                atr_series                  = compute_atr(df['High'], df['Low'], df['Close'])
                atr_pct_series              = atr_series / df['Close'].replace(0, float('nan'))

                # 52-week range position: where current price sits between its 52W low and high
                close_safe = df['Close'].replace(0, float('nan'))
                high_52w = close_safe.rolling(252, min_periods=200).max()
                low_52w  = close_safe.rolling(252, min_periods=200).min()
                range_52w = (high_52w - low_52w).replace(0, float('nan'))
                week52_pct_series = (close_safe - low_52w) / range_52w

                last_date = df.index[-1].strftime('%Y-%m-%d')
                c_price = float(close_s.iloc[-1])
                c_vol = int(volume_s.iloc[-1])

                c_rsi    = float(rsi_series.iloc[-1])    if not pd.isna(rsi_series.iloc[-1])    else None
                c_macd   = float(macd_series.iloc[-1])   if not pd.isna(macd_series.iloc[-1])   else None
                c_signal = float(signal_series.iloc[-1]) if not pd.isna(signal_series.iloc[-1]) else None
                c_hist   = float(hist_series.iloc[-1])   if not pd.isna(hist_series.iloc[-1])   else None
                c_sma50  = float(sma_50.iloc[-1])        if not pd.isna(sma_50.iloc[-1])        else None
                c_sma200 = float(sma_200.iloc[-1])       if not pd.isna(sma_200.iloc[-1])       else None
                c_atr_pct    = float(atr_pct_series.iloc[-1])    if not pd.isna(atr_pct_series.iloc[-1])    else None
                c_week52_pct = float(week52_pct_series.iloc[-1]) if not pd.isna(week52_pct_series.iloc[-1]) else None

                # bool() cast: NaN from compute_volume_surge/compute_bullish_cross safely yields False
                vol_surge     = bool(compute_volume_surge(volume_s, vol_sma_20).iloc[-1])
                bullish_cross = bool(compute_bullish_cross(macd_series, signal_series).iloc[-1])

                vp = compute_volume_profile(df)
                c_vp_poc        = vp["poc"]
                c_vp_val        = vp["val"]
                c_vp_vah        = vp["vah"]
                c_vp_entry_zone = vp["entry_zone"]
                c_vp_exit_zone  = vp["exit_zone"]

                kc = compute_keltner_channel(df["High"], df["Low"], df["Close"])
                c_kc_z_score = kc["z_score"]

                trend_200d_up = c_sma200 is not None and c_price > c_sma200
                c_kc_entry_signal = int(
                    c_kc_z_score is not None and -3.0 < c_kc_z_score < -2.0 and trend_200d_up
                )
                c_kc_exit_signal = int(
                    c_kc_z_score is not None and c_kc_z_score > 3.0
                    and c_rsi is not None and c_rsi > 75
                )

                _v1m = close_s.pct_change(21).iloc[-1]
                _v3m = close_s.pct_change(63).iloc[-1]
                _v6m = close_s.pct_change(126).iloc[-1]
                _v12 = close_s.pct_change(252).iloc[-1]
                c_mom_1m = float(_v1m) if not pd.isna(_v1m) else None
                c_mom_3m = float(_v3m) if not pd.isna(_v3m) else None
                c_mom_6m = float(_v6m) if not pd.isna(_v6m) else None
                _m12     = float(_v12) if not pd.isna(_v12) else None
                c_mom_12m_skip1m = (_m12 - c_mom_1m) if (_m12 is not None and c_mom_1m is not None) else None

                cursor.execute('''
                    INSERT INTO quant_signals
                    (ticker, date, close_price, volume, rsi_14, macd, macd_signal, macd_hist,
                     sma_50, sma_200, volume_surge, bullish_cross, atr_pct, week52_pct,
                     vp_poc, vp_val, vp_vah, vp_entry_zone, vp_exit_zone,
                     kc_z_score, kc_entry_signal, kc_exit_signal,
                     mom_1m, mom_3m, mom_6m, mom_12m_skip1m)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                        atr_pct=excluded.atr_pct,
                        week52_pct=excluded.week52_pct,
                        vp_poc=excluded.vp_poc,
                        vp_val=excluded.vp_val,
                        vp_vah=excluded.vp_vah,
                        vp_entry_zone=excluded.vp_entry_zone,
                        vp_exit_zone=excluded.vp_exit_zone,
                        kc_z_score=excluded.kc_z_score,
                        kc_entry_signal=excluded.kc_entry_signal,
                        kc_exit_signal=excluded.kc_exit_signal,
                        mom_1m=excluded.mom_1m,
                        mom_3m=excluded.mom_3m,
                        mom_6m=excluded.mom_6m,
                        mom_12m_skip1m=excluded.mom_12m_skip1m
                ''', (
                    ticker, last_date, c_price, c_vol, c_rsi, c_macd, c_signal, c_hist,
                    c_sma50, c_sma200, vol_surge, bullish_cross, c_atr_pct, c_week52_pct,
                    c_vp_poc, c_vp_val, c_vp_vah, c_vp_entry_zone, c_vp_exit_zone,
                    c_kc_z_score, c_kc_entry_signal, c_kc_exit_signal,
                    c_mom_1m, c_mom_3m, c_mom_6m, c_mom_12m_skip1m,
                ))

                cursor.execute("UPDATE quant_scan_states SET last_processed_ticker = ? WHERE scan_date = ? AND scan_type = ?", (ticker, today_str, scan_type))
                conn.commit()

            except Exception as e:
                logger.error("Error analyzing %s: %s", ticker, str(e))
                conn.rollback()
            finally:
                # Mandatory throttling to prevent Yahoo Finance IP bans
                time.sleep(random.uniform(0.5, 1.5))


            processed = i + 1
            if total_tickers >= 4 and processed % max(1, total_tickers // 4) == 0 and processed < total_tickers:
                pct = int((processed / total_tickers) * 100)
                log_notification("Info", f"Quant Scan ({scan_type}) Progress: {pct}% ({processed}/{total_tickers} tickers processed).")

        cursor.execute("UPDATE quant_scan_states SET status = 'COMPLETED' WHERE scan_date = ? AND scan_type = ?", (today_str, scan_type))
        conn.commit()

        logger.info("Quant scan '%s' for %s successfully finished executing.", scan_type, today_str)
        log_notification("Success", f"Quant Scan ({scan_type}) completed successfully. All {total_tickers} tracked assets processed.")

    except Exception as e:
        logger.error("Fatal error during Quant Scan '%s': %s", scan_type, str(e))
        log_notification("Error", f"Quant Scan ({scan_type}) failed with a fatal error: {str(e)}")
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    run_daily_quant_scan(["AAPL", "MSFT", "NVDA"], scan_type='test')