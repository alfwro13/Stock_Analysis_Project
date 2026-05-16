import logging
from datetime import datetime
from typing import Dict, Any, Optional

import numpy as np
import pandas as pd
import yfinance as yf

from database import get_connection

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - REGIME_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def initialize_regime_table():
    """Ensures the market_regimes table exists before insertion."""
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_regimes (
                date TEXT PRIMARY KEY,
                vix_close REAL,
                spy_volatility REAL,
                turbulence_index REAL,
                regime_label TEXT
            )
        ''')
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to initialize market_regimes table: {e}")
    finally:
        conn.close()

def calculate_market_regime() -> None:
    """
    Downloads 1 year of SPY and VIX data.
    Calculates the annualized 21-day historical volatility of SPY's log returns.
    Creates a composite Turbulence Index and classifies the market regime.
    Persists the data natively to SQLite.
    """
    logger.info("Initiating daily Market Regime calculation...")
    initialize_regime_table()
    
    try:
        # 1. Fetch exactly 1 year of historical market data
        tickers = ["SPY", "^VIX"]
        df = yf.download(tickers, period="1y", interval="1d", group_by='ticker', auto_adjust=True, progress=False)
        
        if df.empty or 'SPY' not in df.columns or '^VIX' not in df.columns:
            logger.error("Failed to fetch SPY or VIX data from Yahoo Finance.")
            return

        spy_data = df['SPY'].dropna(subset=['Close'])
        vix_data = df['^VIX'].dropna(subset=['Close'])
        
        if spy_data.empty or vix_data.empty:
            logger.error("Incomplete data received for SPY or VIX.")
            return

        # 2. Calculate SPY 21-Day Annualized Historical Volatility
        # Log returns: ln(P_t / P_{t-1})
        spy_log_returns = np.log(spy_data['Close'] / spy_data['Close'].shift(1))
        # Volatility = Standard Deviation of log returns * sqrt(252 trading days) * 100
        spy_vol_21d = spy_log_returns.rolling(window=21).std() * np.sqrt(252) * 100.0

        # Extract latest metrics
        latest_date = spy_data.index[-1].strftime('%Y-%m-%d')
        latest_vix = float(vix_data['Close'].iloc[-1])
        latest_spy_vol = float(spy_vol_21d.iloc[-1])
        
        if pd.isna(latest_spy_vol):
            logger.warning("Not enough data to calculate 21-day rolling volatility.")
            return

        # 3. Calculate Composite Turbulence Index
        # A blended metric weighing implied volatility (VIX) and realized historical volatility (SPY Vol)
        turbulence_index = (latest_vix + latest_spy_vol) / 2.0
        
        # 4. Classify Market Regime
        if turbulence_index >= 30.0 or latest_vix >= 30.0:
            regime_label = 'Crash'
        elif turbulence_index >= 20.0 or latest_vix >= 20.0:
            regime_label = 'Volatile'
        else:
            regime_label = 'Normal'

        # 5. Persist to Database (Idempotent Insert)
        conn = get_connection()
        cursor = conn.cursor()
        
        cursor.execute('''
            INSERT OR REPLACE INTO market_regimes 
            (date, vix_close, spy_volatility, turbulence_index, regime_label)
            VALUES (?, ?, ?, ?, ?)
        ''', (latest_date, round(latest_vix, 2), round(latest_spy_vol, 2), round(turbulence_index, 2), regime_label))
        
        conn.commit()
        conn.close()
        
        logger.info(f"Market Regime recorded for {latest_date}: {regime_label} (Turbulence: {turbulence_index:.2f})")
        
    except Exception as e:
        logger.error(f"Fatal error calculating market regime: {e}")

def get_latest_regime() -> Optional[Dict[str, Any]]:
    """
    Queries the database for the most recent market regime classification.
    Returns a dictionary with the regime details or None if unavailable.
    """
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM market_regimes ORDER BY date DESC LIMIT 1")
        row = cursor.fetchone()
        conn.close()
        
        if row:
            return dict(row)
        return None
    except Exception as e:
        logger.error(f"Failed to fetch latest regime: {e}")
        return None

def calculate_systemic_macro_threat() -> None:
    """Calculates yield rate of change (US) and logs global systemic compression risk to SQLite."""
    try:
        tyx = yf.Ticker("^TYX").history(period="5d")
        tnx = yf.Ticker("^TNX").history(period="5d")
        dxy = yf.Ticker("DX-Y.NYB").history(period="5d")
        gbpusd = yf.Ticker("GBPUSD=X").history(period="5d")
        
        if tyx.empty or len(tyx) < 4:
            return
            
        # Current and Past (3 trading days ago) values
        curr_tyx = float(tyx['Close'].iloc[-1])
        past_tyx = float(tyx['Close'].iloc[-4])
        
        curr_tnx = float(tnx['Close'].iloc[-1]) if not tnx.empty else 0.0
        curr_dxy = float(dxy['Close'].iloc[-1]) if not dxy.empty else 0.0
        curr_gbpusd = float(gbpusd['Close'].iloc[-1]) if not gbpusd.empty else 0.0
        
        # Calculate yield velocity % change over 72 trading hours
        tyx_velocity = ((curr_tyx - past_tyx) / past_tyx) * 100.0 if past_tyx > 0 else 0.0
        
        max_velocity = tyx_velocity
        threat_source = "US 30Y Treasury"
        
        # Institutional Rule Classification
        if max_velocity >= 3.5 or curr_tyx >= 5.0:
            threat_level = "RED"
        elif max_velocity >= 1.5:
            threat_level = "YELLOW"
        else:
            threat_level = "GREEN"
            
        latest_date = tyx.index[-1].strftime('%Y-%m-%d')
        
        conn = get_connection()
        cursor = conn.cursor()
        
        # Native upsert into our dedicated macro ledger
        cursor.execute('''
            INSERT OR REPLACE INTO macro_regimes 
            (date, tyx_close, tnx_close, dxy_close, uk_gilt_close, gbpusd_close, yield_velocity, systemic_threat_level, threat_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (latest_date, round(curr_tyx, 3), round(curr_tnx, 3), round(curr_dxy, 3), 0.0, round(curr_gbpusd, 4), round(max_velocity, 2), threat_level, threat_source))
        
        conn.commit()
        conn.close()
        logger.info(f"Systemic Macro Risk Evaluated: Threat level is {threat_level} (Source: {threat_source}, Velocity: {max_velocity:+.2f}%)")
    except Exception as e:
        logger.error(f"Fatal crash inside systemic threat calculator: {e}")