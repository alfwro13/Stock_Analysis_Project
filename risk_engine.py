import logging
import numpy as np
import pandas as pd
import yfinance as yf
from scipy import stats
from typing import Optional

from database import get_connection

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - RISK_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def calculate_tail_risk(ticker: str, target_date: Optional[str] = None) -> None:
    """
    Fetches 1 year of daily historical prices to calculate Parametric Value at Risk (VaR)
    and Conditional Value at Risk (CVaR) at a 95% confidence interval.
    Updates the existing row in the quant_signals SQLite table.
    """
    try:
        # 1. Fetch 1 year (approx 252 trading days) of historical data
        df = yf.download(ticker, period="1y", interval="1d", progress=False, auto_adjust=True)
        
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

        # 3. Calculate Parametric VaR at 95% Confidence
        alpha = 0.05  # 95% confidence level
        mu = np.mean(log_returns)
        sigma = np.std(log_returns)
        
        # ppf(0.05) gives the z-score for the 5th percentile (approx -1.645)
        var_95_threshold = mu + sigma * stats.norm.ppf(alpha)
        
        # Express VaR as a positive float representing the percentage drop
        var_95 = float(-var_95_threshold) if var_95_threshold < 0 else 0.0
        
        # 4. Calculate CVaR (Expected Shortfall) at 95% Confidence
        # First, attempt to calculate the empirical CVaR (average of returns worse than VaR)
        tail_returns = log_returns[log_returns <= var_95_threshold]
        
        if len(tail_returns) > 0:
            cvar_95 = float(-np.mean(tail_returns))
        else:
            # Fallback to parametric CVaR if no historical data points breached the threshold
            # Formula: mu - sigma * (PDF(z) / alpha)
            z_score = stats.norm.ppf(alpha)
            cvar_95 = float(-(mu - sigma * (stats.norm.pdf(z_score) / alpha)))
            
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
        
        logger.info(f"[{ticker}] Tail Risk Calculated -> VaR(95%): {var_95*100:.2f}%, CVaR(95%): {cvar_95*100:.2f}%")
        
    except Exception as e:
        logger.error(f"Fatal error calculating tail risk for {ticker}: {e}")

if __name__ == "__main__":
    # Test script standalone
    calculate_tail_risk("SPY")