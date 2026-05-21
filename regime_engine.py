# regime_engine.py
import logging
from datetime import datetime
from typing import Dict, Any, Optional

import numpy as np
import pandas as pd
import yfinance as yf

from database import get_connection
from config import HISTORICAL_DIR

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - REGIME_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def initialize_regime_table() -> None:
    """Ensures the dual-region market_regimes table exists before insertion."""
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS market_regimes (
                date TEXT PRIMARY KEY,
                vix_close REAL,
                spy_volatility REAL,
                us_turbulence REAL,
                us_regime_label TEXT,
                ftse_volatility REAL,
                uk_turbulence REAL,
                uk_regime_label TEXT
            )
        ''')
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to initialize market_regimes table: {e}")
    finally:
        conn.close()

def calculate_market_regime() -> None:
    """
    Downloads 1 year of SPY, VIX, and FTSE data.
    Calculates 10-day EWMA annualized historical volatility to minimize lag.
    Creates independent Composite Turbulence Indices for US and UK markets.
    Persists the data natively to SQLite with strict connection safety.
    """
    logger.info("Initiating daily Dual-Region Market Regime calculation...")
    initialize_regime_table()
    
    try:
        # 1. Fetch exactly 1 year of historical market data
        tickers = ["SPY", "^VIX", "^FTSE"]
        df = yf.download(tickers, period="1y", interval="1d", group_by='ticker', auto_adjust=True, progress=False)
        
        if df.empty:
            logger.error("Failed to fetch market data from Yahoo Finance: DataFrame is empty.")
            return

        # Safely handle yfinance MultiIndex structures
        available_tickers = df.columns.get_level_values(0).unique() if isinstance(df.columns, pd.MultiIndex) else df.columns
        
        if 'SPY' not in available_tickers or '^VIX' not in available_tickers or '^FTSE' not in available_tickers:
            logger.error("Critical market indices missing from Yahoo Finance response.")
            return

        # Extract and clean sub-DataFrames
        spy_data = df['SPY'].dropna(subset=['Close'])
        vix_data = df['^VIX'].dropna(subset=['Close'])
        ftse_data = df['^FTSE'].dropna(subset=['Close'])
        
        if spy_data.empty or vix_data.empty or ftse_data.empty:
            logger.error("Incomplete data received for core regime tickers.")
            return

        # 2. Calculate Realized Volatility (RiskMetrics EWMA, Lambda = 0.94)
        LAMBDA = 0.94
        
        # US Realized Volatility (SPY)
        spy_log_returns = np.log(spy_data['Close'] / spy_data['Close'].shift(1)).dropna()
        spy_returns_sq = spy_log_returns ** 2
        spy_var_ewma = spy_returns_sq.ewm(alpha=(1 - LAMBDA), adjust=False).mean()
        spy_vol_ewma = np.sqrt(spy_var_ewma) * np.sqrt(252) * 100.0

        # UK Realized Volatility (FTSE)
        ftse_log_returns = np.log(ftse_data['Close'] / ftse_data['Close'].shift(1)).dropna()
        ftse_returns_sq = ftse_log_returns ** 2
        ftse_var_ewma = ftse_returns_sq.ewm(alpha=(1 - LAMBDA), adjust=False).mean()
        ftse_vol_ewma = np.sqrt(ftse_var_ewma) * np.sqrt(252) * 100.0

        # Extract latest metrics
        latest_date = spy_data.index[-1].strftime('%Y-%m-%d')
        latest_vix = float(vix_data['Close'].iloc[-1])
        latest_spy_vol = float(spy_vol_ewma.iloc[-1])
        latest_ftse_vol = float(ftse_vol_ewma.iloc[-1])
        
        if pd.isna(latest_spy_vol) or pd.isna(latest_ftse_vol):
            logger.warning("Not enough data to calculate EWMA volatilities.")
            return

        # 3. Calculate Independent Composite Turbulence Indices
        # US: 70% Implied (Forward) / 30% EWMA Realized (Fast-Backward)
        us_turbulence = (latest_vix * 0.7) + (latest_spy_vol * 0.3)
        # UK: 100% EWMA Realized (due to lack of robust UK implied vol data on YF)
        uk_turbulence = latest_ftse_vol
        
        # 4. Classify Market Regimes
        us_regime_label = 'Crash' if us_turbulence >= 30.0 else \
                          'Volatile' if us_turbulence >= 20.0 else 'Normal'
                          
        uk_regime_label = 'Crash' if uk_turbulence >= 30.0 else \
                          'Volatile' if uk_turbulence >= 20.0 else 'Normal'

        # 5. Persist to Database (Strict Context Handling)
        conn = get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute('''
                INSERT OR REPLACE INTO market_regimes 
                (date, vix_close, spy_volatility, us_turbulence, us_regime_label, 
                 ftse_volatility, uk_turbulence, uk_regime_label)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                latest_date, 
                round(latest_vix, 2), 
                round(latest_spy_vol, 2), 
                round(us_turbulence, 2), 
                us_regime_label,
                round(latest_ftse_vol, 2),
                round(uk_turbulence, 2),
                uk_regime_label
            ))
            conn.commit()
            logger.info(f"Regimes recorded | Date: {latest_date} | US: {us_regime_label} ({us_turbulence:.2f}) | UK: {uk_regime_label} ({uk_turbulence:.2f})")
        except Exception as e:
            conn.rollback()
            logger.error(f"Database insertion failed during regime calculation: {e}")
            raise
        finally:
            conn.close()
            
    except Exception as e:
        logger.error(f"Fatal error calculating market regime: {e}")

def get_latest_regime() -> Optional[Dict[str, Any]]:
    """
    Queries the database for the most recent market regime classification.
    Returns a dictionary with the regime details or None if unavailable.
    """
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM market_regimes ORDER BY date DESC LIMIT 1")
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error(f"Failed to fetch latest regime: {e}")
        return None
    finally:
        conn.close()

def calculate_systemic_macro_threat() -> None:
    """Calculates yield rate of change in basis points (US & UK) and logs granular systemic compression risk to SQLite."""
    try:
        # Pull 10 days to survive weekends and holidays.
        tyx = yf.Ticker("^TYX").history(period="10d")
        tnx = yf.Ticker("^TNX").history(period="10d")
        dxy = yf.Ticker("DX-Y.NYB").history(period="10d")
        gbpusd = yf.Ticker("GBPUSD=X").history(period="10d")
        
        if tnx.empty or len(tnx) < 4:
            logger.warning("Insufficient ^TNX data for macro threat evaluation.")
            return
            
        # Current and Past (3 trading days ago) values
        curr_tnx = float(tnx['Close'].iloc[-1])
        past_tnx = float(tnx['Close'].iloc[-4])
        
        # FIX ISSUE-M02: Fetch and assign 30Y Treasury Data safely
        curr_tyx = float(tyx['Close'].iloc[-1]) if not tyx.empty else curr_tnx

        curr_dxy = float(dxy['Close'].iloc[-1]) if not dxy.empty else 0.0
        curr_gbpusd = float(gbpusd['Close'].iloc[-1]) if not gbpusd.empty else 0.0
        
        # Read the UK Gilt data from our custom FT.com parquet scraper
        uk_gilt_path = HISTORICAL_DIR / "UK_GILT_BASELINE.parquet"
        if uk_gilt_path.exists():
            gilt_df = pd.read_parquet(uk_gilt_path)
            
            # Strip out weekend padding (Saturday=5, Sunday=6) to align with trading days
            gilt_df = gilt_df[gilt_df.index.dayofweek < 5]
            
            curr_gilt = float(gilt_df['Close'].iloc[-1]) if len(gilt_df) >= 1 else curr_tnx
            past_gilt = float(gilt_df['Close'].iloc[-4]) if len(gilt_df) >= 4 else past_tnx
        else:
            logger.warning("UK Gilt Baseline Parquet missing. Falling back to TNX equivalence.")
            curr_gilt, past_gilt = curr_tnx, past_tnx
            
        # Calculate yield velocity in basis points (bps)
        us_velocity_bps = (curr_tnx - past_tnx) * 100.0
        gilt_velocity_bps = (curr_gilt - past_gilt) * 100.0
        
        # US Institutional Rule Classification
        # Velocity in bps, Absolute Level in Percentage Points
        if us_velocity_bps >= 30.0 or curr_tnx >= 5.0:
            us_threat_level = "RED"
        elif us_velocity_bps >= 15.0:
            us_threat_level = "YELLOW"
        else:
            us_threat_level = "GREEN"

        # UK Institutional Rule Classification
        # Calibrated absolute threshold to 6.0% reflecting historically higher Gilt risk premiums
        if gilt_velocity_bps >= 30.0 or curr_gilt >= 6.0:
            uk_threat_level = "RED"
        elif gilt_velocity_bps >= 15.0:
            uk_threat_level = "YELLOW"
        else:
            uk_threat_level = "GREEN"
            
        latest_date = tnx.index[-1].strftime('%Y-%m-%d')
        
        # Native upsert into macro ledger with strict connection handling
        conn = get_connection()
        try:
            cursor = conn.cursor()
            
            # Explicitly ensure table exists before insert
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS macro_regimes (
                    date TEXT PRIMARY KEY,
                    tyx_close REAL,
                    tnx_close REAL,
                    dxy_close REAL,
                    uk_gilt_close REAL,
                    gbpusd_close REAL,
                    us_yield_velocity REAL,
                    us_threat_level TEXT,
                    uk_yield_velocity REAL,
                    uk_threat_level TEXT
                )
            ''')
            
            cursor.execute('''
                INSERT OR REPLACE INTO macro_regimes 
                (date, tyx_close, tnx_close, dxy_close, uk_gilt_close, gbpusd_close, 
                 us_yield_velocity, us_threat_level, uk_yield_velocity, uk_threat_level)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (
                latest_date, 
                round(curr_tyx, 3), 
                round(curr_tnx, 3), 
                round(curr_dxy, 3), 
                round(curr_gilt, 3), 
                round(curr_gbpusd, 4), 
                round(us_velocity_bps, 2), 
                us_threat_level, 
                round(gilt_velocity_bps, 2), 
                uk_threat_level
            ))
            conn.commit()
            logger.info(f"Macro Risk Evaluated | US: {us_threat_level} (Vel: {us_velocity_bps:+.2f} bps, Lvl: {curr_tnx:.2f}%) | UK: {uk_threat_level} (Vel: {gilt_velocity_bps:+.2f} bps, Lvl: {curr_gilt:.2f}%)")
        except Exception as e:
            conn.rollback()
            logger.error(f"Database insertion failed during macro threat calculation: {e}")
            raise
        finally:
            conn.close()
            
    except Exception as e:
        logger.error(f"Fatal crash inside systemic threat calculator: {e}")