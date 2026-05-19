# macro_data_engine.py
import sqlite3
import logging
import requests
import io
import pandas as pd
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
BOE_M4_CODE = "LPMVWNM"  # Broad Money M4
BOE_SPREAD_CODE = "IUMAAH2" # Proxy for IG spread via BoE

# Standard browser headers to bypass API bot-blocks
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
}

def get_connection() -> sqlite3.Connection:
    return sqlite3.connect(DB_PATH)

def fetch_boe_data(series_code: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
    """Queries the Bank of England IADB CSV endpoint safely."""
    fmt_start = start_date.strftime("%d/%b/%Y")
    fmt_end = end_date.strftime("%d/%b/%Y")
    
    url = (
        f"https://www.bankofengland.co.uk/boeapps/database/_iadb-FromShowColumns.asp?"
        f"csv.x=yes&Datefrom={fmt_start}&Dateto={fmt_end}&SeriesCodes={series_code}"
        f"&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N"
    )
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        
        # Prevent Pandas from crashing if BoE returns an HTML error page
        if "<html" in response.text.lower() or "<!doctype" in response.text.lower():
            logger.warning(f"BoE returned HTML instead of CSV for {series_code}. Code may be invalid.")
            return pd.DataFrame()

        df = pd.read_csv(io.StringIO(response.text))
        
        if df.empty or 'DATE' not in df.columns:
            logger.warning(f"BoE returned empty data for {series_code}")
            return pd.DataFrame()
            
        df['DATE'] = pd.to_datetime(df['DATE'], errors='coerce')
        df.dropna(subset=['DATE'], inplace=True)
        df.set_index('DATE', inplace=True)
        df.rename(columns={col: series_code for col in df.columns if series_code in col}, inplace=True)
        return df[[series_code]]
        
    except Exception as e:
        logger.error(f"Failed to fetch BoE data for {series_code}: {e}")
        return pd.DataFrame()

def fetch_fred_data(series_id: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
    """Fetches data directly from FRED's public CSV export using precise date parameters."""
    cosd = start_date.strftime('%Y-%m-%d')
    coed = end_date.strftime('%Y-%m-%d')
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series_id}&cosd={cosd}&coed={coed}"
    
    try:
        response = requests.get(url, headers=HEADERS, timeout=15)
        response.raise_for_status()
        
        # FRED uses '.' for missing values
        df = pd.read_csv(io.StringIO(response.text), na_values=['.'])
        
        if df.empty or 'DATE' not in df.columns:
            logger.warning(f"FRED returned empty data for {series_id}")
            return pd.DataFrame()
            
        df['DATE'] = pd.to_datetime(df['DATE'], errors='coerce')
        df.dropna(subset=['DATE'], inplace=True)
        df.set_index('DATE', inplace=True)
        
        # Ensure column matches series_id and is numeric
        if series_id in df.columns:
            df[series_id] = pd.to_numeric(df[series_id], errors='coerce')
            return df[[series_id]]
        return pd.DataFrame()
        
    except Exception as e:
        logger.error(f"Failed to fetch FRED data for {series_id}: {e}")
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
    fred_tickers = ['WM2NS', 'ICSA', 'BAMLH0A0HYM2']
    us_dfs = []
    
    for ticker in fred_tickers:
        df_fred = fetch_fred_data(ticker, start_dt, end_dt)
        if not df_fred.empty:
            us_dfs.append(df_fred)
            
    if us_dfs:
        us_data = pd.concat(us_dfs, axis=1)
    else:
        us_data = pd.DataFrame()

    logger.info("Fetching UK Structural Indicators from Bank of England...")
    uk_m4_data = fetch_boe_data(BOE_M4_CODE, start_dt, end_dt)
    uk_spread_data = fetch_boe_data(BOE_SPREAD_CODE, start_dt, end_dt)
    
    # Consolidate DataFrames
    dfs_to_concat = []
    if not us_data.empty: dfs_to_concat.append(us_data)
    if not uk_m4_data.empty: dfs_to_concat.append(uk_m4_data)
    if not uk_spread_data.empty: dfs_to_concat.append(uk_spread_data)
    
    if not dfs_to_concat:
        logger.error("All data sources failed. Aborting macro engine update.")
        return

    # Merge on date index and forward-fill missing values
    merged_df = pd.concat(dfs_to_concat, axis=1)
    merged_df.sort_index(inplace=True)
    merged_df.ffill(inplace=True)
    
    # Extract the most recent state vector
    latest_state = merged_df.iloc[-1]
    snapshot_date = end_dt.strftime("%Y-%m-%d")
    
    # Map raw data to database columns
    payload = (
        snapshot_date,
        float(latest_state.get('WM2NS', 0.0)) if 'WM2NS' in latest_state and pd.notna(latest_state['WM2NS']) else 0.0,
        float(latest_state.get('ICSA', 0.0)) if 'ICSA' in latest_state and pd.notna(latest_state['ICSA']) else 0.0,
        float(latest_state.get('BAMLH0A0HYM2', 0.0)) if 'BAMLH0A0HYM2' in latest_state and pd.notna(latest_state['BAMLH0A0HYM2']) else 0.0,
        float(latest_state.get(BOE_M4_CODE, 0.0)) if BOE_M4_CODE in latest_state and pd.notna(latest_state[BOE_M4_CODE]) else None,
        float(latest_state.get(BOE_SPREAD_CODE, 0.0)) if BOE_SPREAD_CODE in latest_state and pd.notna(latest_state[BOE_SPREAD_CODE]) else None
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