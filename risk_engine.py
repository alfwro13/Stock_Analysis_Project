# risk_engine.py
import logging
import time
import random
import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats
from typing import Optional

from database import get_connection

logger = logging.getLogger(__name__)

def calculate_tail_risk(ticker: str, target_date: Optional[str] = None) -> None:
    """
    Fetches 2 years of daily historical prices to calculate Historical Simulation VaR
    and Conditional Value at Risk (CVaR / Expected Shortfall) at a 95% confidence interval.
    Historical Simulation is used in preference to Parametric VaR to avoid the dangerous
    assumption of normally distributed returns (financial returns are leptokurtic).
    """
    try:
        # 1. Fetch 2 years (approx 504 trading days) of historical data
        df = yf.download(ticker, period="2y", interval="1d", progress=False, auto_adjust=True)
        
        if df.empty or len(df) < 50:
            logger.warning(f"Insufficient historical data to calculate tail risk for {ticker}.")
            return

        # Handle multi-index columns returned by newer yfinance versions
        if isinstance(df.columns, pd.MultiIndex):
            df.columns = df.columns.get_level_values(0)

        df.dropna(subset=['Close'], inplace=True)
        
        # 2. Calculate daily logarithmic returns
        # Log returns are strictly preferred for statistical modeling over simple percentage returns
        log_returns = np.log(df['Close'] / df['Close'].shift(1)).dropna().values
        
        if len(log_returns) < 50:
            logger.warning(f"Insufficient valid log returns to calculate tail risk for {ticker}.")
            return

        # 3. Calculate Historical Simulation VaR (Empirical) at 95% Confidence
        # Financial returns are leptokurtic (fat tails). Using historical percentiles
        # avoids the dangerous assumption of a normal distribution.
        alpha = 0.05
        empirical_var_95_threshold = np.percentile(log_returns, alpha * 100)        
        # Express VaR as a positive float representing the percentage drop.
        var_95 = float(1 - np.exp(empirical_var_95_threshold)) if empirical_var_95_threshold < 0 else 0.0
        
        # 4. Calculate Historical CVaR (Expected Shortfall) at 95% Confidence
        # Calculates the average of all actual daily returns that were worse than the VaR threshold.
        tail_returns = log_returns[log_returns <= empirical_var_95_threshold]
        
        if len(tail_returns) > 0:
            # Convert average tail log-return to simple loss magnitude
            cvar_95 = float(1 - np.exp(np.mean(tail_returns)))
        else:
            cvar_95 = var_95
            
        # Prevent negative risk metrics in extreme outlier cases (e.g., asset only went straight up)
        var_95 = max(var_95, 0.0)
        cvar_95 = max(cvar_95, 0.0)

        # 5. Database Update (Strictly UPDATE, no INSERT)
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
        conn.close()
        
        # [MATH-13 RESOLVED] Clarify log-return terms in terminal logs
        logger.info(f"[{ticker}] Tail Risk Calculated -> Log-Return VaR(95%): {var_95*100:.2f}%, Log-Return CVaR(95%): {cvar_95*100:.2f}%")
        
    except Exception as e:
        logger.error(f"Fatal error calculating tail risk for {ticker}: {e}")

def update_all_tail_risks(tickers: list) -> None:
    """Iterates through a list of tickers and calculates their VaR/CVaR profiles."""
    if not tickers:
        logger.warning("Ticker list is empty. Aborting tail risk scan.")
        return
        
    logger.info(f"Initiating Tail Risk (VaR) Scan for {len(tickers)} assets...")
    for ticker in tickers:
        calculate_tail_risk(ticker)
        time.sleep(random.uniform(0.5, 1.5))
    logger.info("Tail Risk Scan completed successfully.")

if __name__ == "__main__":
    # Test script standalone
    calculate_tail_risk("SPY")