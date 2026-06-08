import logging
from datetime import datetime
from typing import Dict, Any, Optional

import numpy as np
import pandas as pd

from database import get_connection
from config import HISTORICAL_DIR
from yahoo_engine import yahoo_engine
from constants import REGIME_CRASH_VOL, REGIME_VOLATILE_VOL

logger = logging.getLogger(__name__)

def calculate_market_regime() -> None:
    """Fetches 1y of SPY/VIX/FTSE, computes RiskMetrics EWMA vol, classifies regimes, and persists to market_regimes."""
    logger.info("Initiating daily Dual-Region Market Regime calculation...")

    try:
        ticker_dfs = yahoo_engine.get_price_history(["SPY", "^VIX", "^FTSE"], period="1y", interval="1d")

        if not ticker_dfs or not all(t in ticker_dfs for t in ["SPY", "^VIX", "^FTSE"]):
            logger.error("Critical market indices missing from Yahoo Finance response.")
            return

        spy_data = ticker_dfs["SPY"].dropna(subset=['Close'])
        vix_data = ticker_dfs["^VIX"].dropna(subset=['Close'])
        ftse_data = ticker_dfs["^FTSE"].dropna(subset=['Close'])

        if spy_data.empty or vix_data.empty or ftse_data.empty:
            logger.error("Incomplete data received for core regime tickers.")
            return

        # RiskMetrics EWMA (λ=0.94): minimizes lag vs simple rolling window
        LAMBDA = 0.94

        spy_log_returns = np.log(spy_data['Close'] / spy_data['Close'].shift(1)).dropna()
        spy_var_ewma = (spy_log_returns ** 2).ewm(alpha=(1 - LAMBDA), adjust=False).mean()
        spy_vol_ewma = np.sqrt(spy_var_ewma) * np.sqrt(252) * 100.0

        ftse_log_returns = np.log(ftse_data['Close'] / ftse_data['Close'].shift(1)).dropna()
        ftse_var_ewma = (ftse_log_returns ** 2).ewm(alpha=(1 - LAMBDA), adjust=False).mean()
        ftse_vol_ewma = np.sqrt(ftse_var_ewma) * np.sqrt(252) * 100.0

        latest_date = spy_data.index[-1].strftime('%Y-%m-%d')
        latest_vix = float(vix_data['Close'].iloc[-1])
        latest_spy_vol = float(spy_vol_ewma.iloc[-1])
        latest_ftse_vol = float(ftse_vol_ewma.iloc[-1])

        if pd.isna(latest_spy_vol) or pd.isna(latest_ftse_vol):
            logger.warning("Not enough data to calculate EWMA volatilities.")
            return

        # US: 100% EWMA Realized (VIX is fetched for display purposes only, not blended in)
        us_turbulence = latest_spy_vol
        # UK: 100% EWMA Realized (no robust UK implied vol feed available on Yahoo Finance)
        uk_turbulence = latest_ftse_vol

        us_regime_label = 'Crash' if us_turbulence >= REGIME_CRASH_VOL else \
                          'Volatile' if us_turbulence >= REGIME_VOLATILE_VOL else 'Normal'

        uk_regime_label = 'Crash' if uk_turbulence >= REGIME_CRASH_VOL else \
                          'Volatile' if uk_turbulence >= REGIME_VOLATILE_VOL else 'Normal'

        conn = None
        try:
            conn = get_connection()
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
            logger.info("Regimes recorded | Date: %s | US: %s (%.2f) | UK: %s (%.2f)",
                        latest_date, us_regime_label, us_turbulence, uk_regime_label, uk_turbulence)
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error("Database insertion failed during regime calculation: %s", e)
            raise
        finally:
            if conn:
                conn.close()

    except Exception as e:
        logger.error("Fatal error calculating market regime: %s", e)

def get_latest_regime() -> Optional[Dict[str, Any]]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM market_regimes ORDER BY date DESC LIMIT 1")
        row = cursor.fetchone()
        return dict(row) if row else None
    except Exception as e:
        logger.error("Failed to fetch latest regime: %s", e)
        return None
    finally:
        if conn:
            conn.close()

def calculate_systemic_macro_threat() -> None:
    try:
        # pull 10 days to survive weekends and holidays
        _macro_dfs = yahoo_engine.get_price_history(
            ["^TYX", "^TNX", "DX-Y.NYB", "GBPUSD=X"], period="10d", interval="1d"
        )
        tyx = _macro_dfs.get("^TYX", pd.DataFrame())
        tnx = _macro_dfs.get("^TNX", pd.DataFrame())
        dxy = _macro_dfs.get("DX-Y.NYB", pd.DataFrame())
        gbpusd = _macro_dfs.get("GBPUSD=X", pd.DataFrame())

        if tnx.empty or len(tnx) < 4:
            logger.warning("Insufficient ^TNX data for macro threat evaluation.")
            return

        curr_tnx = float(tnx['Close'].iloc[-1])
        # iloc[-4] is fragile across holidays; use date lookback instead
        target_past = tnx.index[-1] - pd.Timedelta(days=4)
        tnx_past_rows = tnx[tnx.index <= target_past]
        past_tnx = float(tnx_past_rows['Close'].iloc[-1]) if not tnx_past_rows.empty else curr_tnx

        curr_tyx = float(tyx['Close'].iloc[-1]) if not tyx.empty else curr_tnx
        curr_dxy = float(dxy['Close'].iloc[-1]) if not dxy.empty else 0.0
        curr_gbpusd = float(gbpusd['Close'].iloc[-1]) if not gbpusd.empty else 0.0

        uk_gilt_path = HISTORICAL_DIR / "UK_GILT_BASELINE.parquet"
        if uk_gilt_path.exists():
            gilt_df = pd.read_parquet(uk_gilt_path)
            # Strip out weekend padding (Saturday=5, Sunday=6) to align with trading days
            gilt_df = gilt_df[gilt_df.index.dayofweek < 5]
            curr_gilt = float(gilt_df['Close'].iloc[-1]) if len(gilt_df) >= 1 else curr_tnx
            target_past_gilt = gilt_df.index[-1] - pd.Timedelta(days=4)
            gilt_past_rows = gilt_df[gilt_df.index <= target_past_gilt]
            past_gilt = float(gilt_past_rows['Close'].iloc[-1]) if not gilt_past_rows.empty else curr_tnx
        else:
            logger.warning("UK Gilt Baseline Parquet missing. Falling back to TNX equivalence.")
            curr_gilt, past_gilt = curr_tnx, past_tnx

        us_velocity_bps = (curr_tnx - past_tnx) * 100.0
        gilt_velocity_bps = (curr_gilt - past_gilt) * 100.0

        # calibrated to post-2022 rate environment: 30bps/3-day or 4.75% = RED, 15bps or 4.25% = YELLOW
        if us_velocity_bps >= 30.0 or curr_tnx >= 4.75:
            us_threat_level = "RED"
        elif us_velocity_bps >= 15.0 or curr_tnx >= 4.25:
            us_threat_level = "YELLOW"
        else:
            us_threat_level = "GREEN"

        # calibrated to higher UK gilt premiums: 30bps/3-day or 5.0% = RED, 15bps or 4.5% = YELLOW
        if gilt_velocity_bps >= 30.0 or curr_gilt >= 5.0:
            uk_threat_level = "RED"
        elif gilt_velocity_bps >= 15.0 or curr_gilt >= 4.5:
            uk_threat_level = "YELLOW"
        else:
            uk_threat_level = "GREEN"

        latest_date = tnx.index[-1].strftime('%Y-%m-%d')

        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
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
            logger.info("Macro Risk Evaluated | US: %s (Vel: %+.2f bps, Lvl: %.2f%%) | UK: %s (Vel: %+.2f bps, Lvl: %.2f%%)",
                        us_threat_level, us_velocity_bps, curr_tnx, uk_threat_level, gilt_velocity_bps, curr_gilt)
        except Exception as e:
            if conn:
                conn.rollback()
            logger.error("Database insertion failed during macro threat calculation: %s", e)
            raise
        finally:
            if conn:
                conn.close()

    except Exception as e:
        logger.error("Fatal crash inside systemic threat calculator: %s", e)
