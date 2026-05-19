# macro_data_engine.py
import sqlite3
import logging
import requests
import io
import pandas as pd
import pandas_datareader.data as web
from datetime import datetime, timedelta
from typing import Optional

# Configure module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - MACRO_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DB_PATH = "data/analysis.db"

# Bank of England specific series codes for UK M4 and Corporate Spreads
# (Using standardized proxies for the BoE IADB system)
BOE_M4_CODE = "LPMVWNM"  # Broad Money M4
BOE_SPREAD_CODE = "IUMAAH2" # Example proxy for IG spread via BoE

def get_connection() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)

def fetch_boe_data(series_code: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
    """
    Queries the Bank of England IADB CSV endpoint.
    """
    fmt_start = start_date.strftime("%d/%b/%Y")
    fmt_end = end_date.strftime("%d/%b/%Y")
    
    url = (
        f"https://www.bankofengland.co.uk/boeapps/database/_iadb-FromShowColumns.asp?"
        f"csv.x=yes&Datefrom={fmt_start}&Dateto={fmt_end}&SeriesCodes={series_code}"
        f"&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N"
    )
    
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) Institutional-Quant-Engine'
    }
    
    try:
        response = requests.get(url, headers=headers, timeout=15)
        response.raise_for_status()
        df = pd.read_csv(io.StringIO(response.text))
        
        if df.empty or 'DATE' not in df.columns:
            logger.warning(f"BoE returned empty data for {series_code}")
            return pd.DataFrame()
            
        df['DATE'] = pd.to_datetime(df['DATE'])
        df.set_index('DATE', inplace=True)
        # Rename the value column to the series code
        df.rename(columns={col: series_code for col in df.columns if series_code in col}, inplace=True)
        return df[[series_code]]
        
    except Exception as e:
        logger.error(f"Failed to fetch BoE data for {series_code}: {e}")
        return pd.DataFrame()

def update_macro_indicators() -> None:
    """
    Fetches US (FRED) and UK (BoE) macro indicators, aligns their differing
    frequencies (daily, weekly, monthly) using a forward fill over a trailing window,
    and upserts the current regime state into the database.
    """
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=90) # Trailing 90 days to ensure we catch monthly releases
    
    logger.info("Fetching US Structural Indicators from FRED...")
    try:
        # WM2NS: M2 Money Supply, ICSA: Initial Jobless Claims, BAMLH0A0HYM2: US HY Spread
        fred_tickers = ['WM2NS', 'ICSA', 'BAMLH0A0HYM2']
        us_data = web.DataReader(fred_tickers, 'fred', start_dt, end_dt)
    except Exception as e:
        logger.error(f"Failed to fetch FRED data: {e}")
        us_data = pd.DataFrame()

    logger.info("Fetching UK Structural Indicators from Bank of England...")
    uk_m4_data = fetch_boe_data(BOE_M4_CODE, start_dt, end_dt)
    uk_spread_data = fetch_boe_data(BOE_SPREAD_CODE, start_dt, end_dt)
    
    # Consolidate DataFrames
    dfs_to_concat = [us_data]
    if not uk_m4_data.empty: dfs_to_concat.append(uk_m4_data)
    if not uk_spread_data.empty: dfs_to_concat.append(uk_spread_data)
    
    if not dfs_to_concat:
        logger.error("All data sources failed. Aborting macro engine update.")
        return

    # Merge on date index and forward-fill missing values (since releases are asynchronous)
    merged_df = pd.concat(dfs_to_concat, axis=1)
    merged_df.sort_index(inplace=True)
    merged_df.ffill(inplace=True)
    
    # Extract the most recent state vector
    latest_state = merged_df.iloc[-1]
    snapshot_date = end_dt.strftime("%Y-%m-%d")
    
    # Map raw data to database columns
    payload = (
        snapshot_date,
        float(latest_state.get('WM2NS', 0.0)),
        float(latest_state.get('ICSA', 0.0)),
        float(latest_state.get('BAMLH0A0HYM2', 0.0)),
        float(latest_state.get(BOE_M4_CODE, 0.0)) if BOE_M4_CODE in latest_state else None,
        float(latest_state.get(BOE_SPREAD_CODE, 0.0)) if BOE_SPREAD_CODE in latest_state else None
    )
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT OR REPLACE INTO macro_indicators (
                date, us_m2, us_jobless_claims, us_high_yield_spread, uk_m4, uk_corporate_spread
            ) VALUES (?, ?, ?, ?, ?, ?)
        ''', payload)
        conn.commit()
        logger.info(f"Successfully upserted Macro Regime Snapshot for {snapshot_date}")
    except sqlite3.Error as e:
        logger.error(f"Database insertion failed for macro snapshot: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    logger.info("Starting Macroeconomic Structural Data Ingestion...")
    update_macro_indicators()
    logger.info("Macro Data Ingestion Complete.")