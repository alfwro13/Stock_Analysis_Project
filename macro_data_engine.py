# macro_data_engine.py
import os
import sqlite3
import logging
import requests
import io
import pandas as pd
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Optional, List, Dict
from config import load_config

# Configure module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - MACRO_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DB_PATH = "data/analysis.db"

# Standard headers to bypass WAF challenges
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/csv'
}

# The modern ONS Taxonomy Dictionary linking Tickers to exact JSON data paths
ONS_TAXONOMY: Dict[str, str] = {
    "D7G7": "/economy/inflationandpriceindices/timeseries/d7g7/mm23/data",
    "BCJD": "/employmentandlabourmarket/peoplenotinwork/outofworkbenefits/timeseries/bcjd/unem/data"
}

def get_connection() -> sqlite3.Connection:
    """Returns a native SQLite connection."""
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    return sqlite3.connect(DB_PATH)

def setup_database() -> None:
    """Ensures the macro_indicators table is structured idempotently."""
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS macro_indicators (
            date TEXT PRIMARY KEY,
            us_m2 REAL,
            us_jobless_claims REAL,
            us_high_yield_spread REAL,
            us_yield_curve REAL,
            uk_m4 REAL,
            uk_corporate_spread REAL,
            uk_cpi_inflation REAL,
            uk_claimant_count REAL
        )
    ''')
    conn.commit()
    conn.close()

def get_retry_session() -> requests.Session:
    """Constructs a robust requests Session with exponential backoff retries."""
    session = requests.Session()
    retry_strategy = Retry(
        total=5,
        backoff_factor=1.5,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"]
    )
    adapter = HTTPAdapter(max_retries=retry_strategy, pool_connections=10, pool_maxsize=10)
    session.mount("https://", adapter)
    session.mount("http://", adapter)
    session.headers.update(HEADERS)
    return session

def fetch_fred_api(session: requests.Session, series_id: str, start_date: datetime, end_date: datetime, api_key: str) -> pd.DataFrame:
    """Fetches US Macro and Credit data using the Official FRED REST API."""
    cosd = start_date.strftime('%Y-%m-%d')
    coed = end_date.strftime('%Y-%m-%d')
    
    url = "https://api.stlouisfed.org/fred/series/observations"
    params = {
        "series_id": series_id,
        "api_key": api_key,
        "file_type": "json",
        "observation_start": cosd,
        "observation_end": coed
    }
    
    try:
        response = session.get(url, params=params, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        if 'observations' not in data or not data['observations']:
            return pd.DataFrame()
            
        df = pd.DataFrame(data['observations'])
        df['value'] = pd.to_numeric(df['value'].replace('.', pd.NA), errors='coerce')
        df['date'] = pd.to_datetime(df['date'], errors='coerce')
        
        df.dropna(subset=['date'], inplace=True)
        df.set_index('date', inplace=True)
        df.rename(columns={'value': series_id}, inplace=True)
        
        return df[[series_id]]
        
    except Exception as e:
        logger.error(f"Failed to fetch FRED {series_id}: {e}")
        return pd.DataFrame()

def fetch_boe_data(session: requests.Session, series_code: str, start_date: datetime, end_date: datetime) -> pd.DataFrame:
    """Fetches Broad Money M4 from Bank of England CSV interface."""
    fmt_start = start_date.strftime("%d/%b/%Y")
    fmt_end = end_date.strftime("%d/%b/%Y")
    
    url = (
        f"https://www.bankofengland.co.uk/boeapps/database/_iadb-FromShowColumns.asp?"
        f"csv.x=yes&Datefrom={fmt_start}&Dateto={fmt_end}&SeriesCodes={series_code}"
        f"&CSVF=TN&UsingCodes=Y&VPD=Y&VFD=N"
    )
    
    try:
        response = session.get(url, timeout=30)
        response.raise_for_status()
        
        if "<html" in response.text.lower():
            logger.error(f"BoE returned HTML instead of CSV for {series_code}.")
            return pd.DataFrame()

        df = pd.read_csv(io.StringIO(response.text))
        
        if df.empty or 'DATE' not in df.columns:
            return pd.DataFrame()
            
        df['DATE'] = pd.to_datetime(df['DATE'], errors='coerce')
        df.dropna(subset=['DATE'], inplace=True)
        df.set_index('DATE', inplace=True)
        
        # Defensive renaming mechanism
        df.rename(columns={col: series_code for col in df.columns if series_code in col}, inplace=True)
        if series_code in df.columns:
            return df[[series_code]]
        else:
            val_col = [c for c in df.columns if c != 'DATE'][0]
            df.rename(columns={val_col: series_code}, inplace=True)
            return df[[series_code]]
        
    except Exception as e:
        logger.error(f"Failed to fetch BoE {series_code}: {e}")
        return pd.DataFrame()

def fetch_ons_taxonomy_data(session: requests.Session, series_id: str, start_date: datetime) -> pd.DataFrame:
    """Fetches high-frequency UK Real Economy indicators via ONS Taxonomy paths."""
    taxonomy_path = ONS_TAXONOMY.get(series_id)
    if not taxonomy_path:
        return pd.DataFrame()

    url = f"https://www.ons.gov.uk{taxonomy_path}"
    
    try:
        response = session.get(url, timeout=15)
        response.raise_for_status()
        data = response.json()
        
        if 'months' not in data or not data['months']:
            return pd.DataFrame()
            
        observations = data['months']
        records = []
        
        for obs in observations:
            raw_date = obs.get('date')
            val = obs.get('value')
            if raw_date and val:
                try:
                    dt = pd.to_datetime(raw_date, format='%Y %b') + pd.offsets.MonthEnd(1)
                    if dt >= start_date:
                        records.append({'DATE': dt, series_id: float(val)})
                except ValueError:
                    continue
                    
        df = pd.DataFrame(records)
        if df.empty:
            return pd.DataFrame()
            
        df.set_index('DATE', inplace=True)
        df.sort_index(inplace=True)
        return df[[series_id]]
        
    except Exception as e:
        logger.error(f"Failed to fetch ONS {series_id}: {e}")
        return pd.DataFrame()

def update_macro_indicators() -> None:
    """
    Master pipeline: Aggregates FRED, BoE, and ONS data. Aligns daily, weekly, 
    and monthly structural indices using a forward-fill mechanism, then bulk upserts history.
    """
    config = load_config()
    fred_api_key = config.get("FRED_API_KEY")
    if not fred_api_key:
        logger.error("FRED_API_KEY is not configured in settings. Aborting FRED API fetch.")

    setup_database()
    
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=730) 
    session = get_retry_session()
    
    dfs = []

    if fred_api_key:
        logger.info("Fetching FRED Institutional Data (2-Year History)...")
        # Added T10Y2Y to map the Yield Curve
        fred_tickers = ['WM2NS', 'ICSA', 'BAMLH0A0HYM2', 'BAMLC0A0CM', 'T10Y2Y']
        for ticker in fred_tickers:
            df = fetch_fred_api(session, ticker, start_dt, end_dt, fred_api_key)
            if not df.empty:
                dfs.append(df)
            
    logger.info("Fetching Bank of England IADB Data (2-Year History)...")
    df_boe = fetch_boe_data(session, 'LPMVWNM', start_dt, end_dt)
    if not df_boe.empty:
        dfs.append(df_boe)
        
    logger.info("Fetching UK ONS Taxonomy Data (2-Year History)...")
    for ticker in ONS_TAXONOMY.keys():
        df = fetch_ons_taxonomy_data(session, ticker, start_dt)
        if not df.empty:
            dfs.append(df)

    if not dfs:
        logger.error("All data sources returned empty. Engine execution halted.")
        return

    merged_df = pd.concat(dfs, axis=1, sort=False)
    merged_df.sort_index(inplace=True)
    merged_df.ffill(inplace=True)
    merged_df.bfill(inplace=True) 
    
    records = []
    for dt, row in merged_df.iterrows():
        records.append((
            dt.strftime("%Y-%m-%d"),
            float(row['WM2NS']) if 'WM2NS' in row and pd.notna(row['WM2NS']) else None,
            float(row['ICSA']) if 'ICSA' in row and pd.notna(row['ICSA']) else None,
            float(row['BAMLH0A0HYM2']) if 'BAMLH0A0HYM2' in row and pd.notna(row['BAMLH0A0HYM2']) else None,
            float(row['T10Y2Y']) if 'T10Y2Y' in row and pd.notna(row['T10Y2Y']) else None,
            float(row['LPMVWNM']) if 'LPMVWNM' in row and pd.notna(row['LPMVWNM']) else None,
            float(row['BAMLC0A0CM']) if 'BAMLC0A0CM' in row and pd.notna(row['BAMLC0A0CM']) else None,
            float(row['D7G7']) if 'D7G7' in row and pd.notna(row['D7G7']) else None,
            float(row['BCJD']) if 'BCJD' in row and pd.notna(row['BCJD']) else None
        ))
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.executemany('''
            INSERT OR REPLACE INTO macro_indicators (
                date, us_m2, us_jobless_claims, us_high_yield_spread, us_yield_curve,
                uk_m4, uk_corporate_spread, uk_cpi_inflation, uk_claimant_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', records)
        conn.commit()
        logger.info(f"Successfully bulk-upserted {cursor.rowcount} Macro Regime historical days for AI Training.")
    except sqlite3.Error as e:
        logger.error(f"Database bulk insertion failed: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    logger.info("Initializing Master Macro Data Engine...")
    update_macro_indicators()
    logger.info("Macro Data Engine Execution Complete.")