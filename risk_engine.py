import logging
import time
import random
from datetime import datetime, timezone
import numpy as np
import pandas as pd
from typing import Optional

from database import get_connection
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

def calculate_tail_risk(ticker: str, target_date: Optional[str] = None) -> None:
    """Historical Simulation VaR/CVaR at 95% CI; uses empirical percentiles to avoid the normal-distribution assumption (returns are leptokurtic)."""
    try:
        _result = yahoo_engine.get_price_history([ticker], period="2y", interval="1d")
        df = _result.get(ticker, pd.DataFrame())

        if df.empty or len(df) < 50:
            logger.warning("Insufficient historical data to calculate tail risk for %s.", ticker)
            return

        df = df.dropna(subset=['Close'])

        # log returns preferred over simple returns for statistical modelling
        log_returns = np.log(df['Close'] / df['Close'].shift(1)).dropna().values

        if len(log_returns) < 50:
            logger.warning("Insufficient valid log returns to calculate tail risk for %s.", ticker)
            return

        # leptokurtic returns: use empirical 5th-percentile rather than normal-distribution VaR
        alpha = 0.05
        empirical_var_95_threshold = np.percentile(log_returns, alpha * 100)
        var_95 = float(1 - np.exp(empirical_var_95_threshold)) if empirical_var_95_threshold < 0 else 0.0

        tail_returns = log_returns[log_returns <= empirical_var_95_threshold]
        if len(tail_returns) > 0:
            cvar_95 = float(1 - np.exp(np.mean(tail_returns)))
        else:
            cvar_95 = var_95

        # Prevent negative risk metrics in extreme outlier cases (e.g., asset only went straight up)
        var_95 = max(var_95, 0.0)
        cvar_95 = max(cvar_95, 0.0)

        # UPDATE only — avoids orphan rows for tickers not yet in quant_signals
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            if target_date:
                query = """
                    UPDATE quant_signals
                    SET var_95 = ?, cvar_95 = ?
                    WHERE ticker = ? AND date = ?
                """
                cursor.execute(query, (var_95, cvar_95, ticker, target_date))
            else:
                query = """
                    UPDATE quant_signals
                    SET var_95 = ?, cvar_95 = ?
                    WHERE ticker = ? AND date = (SELECT MAX(date) FROM quant_signals WHERE ticker = ?)
                """
                cursor.execute(query, (var_95, cvar_95, ticker, ticker))
            conn.commit()
        finally:
            if conn:
                conn.close()

        logger.info("[%s] VaR(95%%): %.2f%%, CVaR(95%%): %.2f%%", ticker, var_95 * 100, cvar_95 * 100)

    except Exception as e:
        logger.error("Fatal error calculating tail risk for %s: %s", ticker, e)

def update_all_tail_risks(tickers: list, scan_type: str = None) -> None:
    if not tickers:
        logger.warning("Ticker list is empty. Aborting tail risk scan.")
        return

    process_tickers = tickers
    today_str = None

    if scan_type:
        today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')
        resume_from = None

        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "SELECT last_processed_ticker, status FROM quant_scan_states WHERE scan_date = ? AND scan_type = ?",
                (today_str, scan_type),
            )
            row = cursor.fetchone()
            if row:
                if row['status'] == 'COMPLETED':
                    logger.info("Tail risk scan '%s' for %s already completed. Skipping.", scan_type, today_str)
                    return
                if row['status'] == 'IN_PROGRESS' and row['last_processed_ticker'] in tickers:
                    resume_from = row['last_processed_ticker']
                    logger.info("Resuming tail risk scan '%s' from after %s.", scan_type, resume_from)
                else:
                    cursor.execute(
                        "UPDATE quant_scan_states SET last_processed_ticker = '', status = 'IN_PROGRESS'"
                        " WHERE scan_date = ? AND scan_type = ?",
                        (today_str, scan_type),
                    )
            else:
                cursor.execute(
                    "INSERT INTO quant_scan_states (scan_date, scan_type, last_processed_ticker, status)"
                    " VALUES (?, ?, ?, ?)",
                    (today_str, scan_type, '', 'IN_PROGRESS'),
                )
            conn.commit()
        finally:
            if conn:
                conn.close()

        if resume_from:
            idx = tickers.index(resume_from)
            process_tickers = tickers[idx + 1:]
        else:
            process_tickers = tickers

    logger.info("Initiating Tail Risk (VaR) Scan for %s assets...", len(process_tickers))
    for ticker in process_tickers:
        calculate_tail_risk(ticker)
        time.sleep(random.uniform(0.5, 1.5))
        if scan_type:
            conn = None
            try:
                conn = get_connection()
                cursor = conn.cursor()
                cursor.execute(
                    "UPDATE quant_scan_states SET last_processed_ticker = ? WHERE scan_date = ? AND scan_type = ?",
                    (ticker, today_str, scan_type),
                )
                conn.commit()
            finally:
                if conn:
                    conn.close()

    if scan_type:
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "UPDATE quant_scan_states SET status = 'COMPLETED' WHERE scan_date = ? AND scan_type = ?",
                (today_str, scan_type),
            )
            conn.commit()
        finally:
            if conn:
                conn.close()

    logger.info("Tail Risk Scan completed successfully.")

if __name__ == "__main__":
    calculate_tail_risk("SPY")
