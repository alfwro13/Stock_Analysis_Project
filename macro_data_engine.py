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
        df.rename(columns={col: series_code for col in df.columns if series_code in col}, inplace=True)
        
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
                    # Roll ONS "YYYY MMM" format to Month End for database alignment
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
    and monthly structural indices using a forward-fill mechanism, then upserts.
    """
    # Load configuration dynamically to extract API Keys
    config = load_config()
    fred_api_key = config.get("FRED_API_KEY")
    if not fred_api_key:
        logger.error("FRED_API_KEY is not configured in settings. Aborting FRED API fetch.")
        # We will continue the execution to fetch BoE and ONS even if FRED fails

    setup_database()
    
    end_dt = datetime.now()
    start_dt = end_dt - timedelta(days=90) # Trailing 90 days to capture quarterly/monthly lags
    session = get_retry_session()
    
    dfs = []

    # 1. Fetch FRED Data (US + UK Corporate Credit)
    if fred_api_key:
        logger.info("Fetching FRED Institutional Data...")
        # WM2NS: US M2, ICSA: US Jobless Claims, BAMLH0A0HYM2: US HY Spread, BAMLC0A0CM: UK Corporate Spread
        fred_tickers = ['WM2NS', 'ICSA', 'BAMLH0A0HYM2', 'BAMLC0A0CM']
        
        for ticker in fred_tickers:
            df = fetch_fred_api(session, ticker, start_dt, end_dt, fred_api_key)
            if not df.empty:
                dfs.append(df)
            
    # 2. Fetch Bank of England (UK Broad Money)
    logger.info("Fetching Bank of England IADB Data...")
    df_boe = fetch_boe_data(session, 'LPMVWNM', start_dt, end_dt)
    if not df_boe.empty:
        dfs.append(df_boe)
        
    # 3. Fetch ONS Data (UK Real Economy)
    logger.info("Fetching UK ONS Taxonomy Data...")
    for ticker in ONS_TAXONOMY.keys():
        df = fetch_ons_taxonomy_data(session, ticker, start_dt)
        if not df.empty:
            dfs.append(df)

    if not dfs:
        logger.error("All data sources returned empty. Engine execution halted.")
        return

    # Merge, sort by date, and forward fill across differing frequencies
    merged_df = pd.concat(dfs, axis=1, sort=False)
    merged_df.sort_index(inplace=True)
    merged_df.ffill(inplace=True)
    
    # Extract the most recent macro state
    latest_state = merged_df.iloc[-1]
    snapshot_date = end_dt.strftime("%Y-%m-%d")
    
    def safe_get(ticker: str) -> Optional[float]:
        if ticker in latest_state and pd.notna(latest_state[ticker]):
            return float(latest_state[ticker])
        return None
    
    payload = (
        snapshot_date,
        safe_get('WM2NS'),
        safe_get('ICSA'),
        safe_get('BAMLH0A0HYM2'),
        safe_get('LPMVWNM'),
        safe_get('BAMLC0A0CM'),
        safe_get('D7G7'),
        safe_get('BCJD')
    )
    
    conn = get_connection()
    cursor = conn.cursor()
    
    try:
        cursor.execute('''
            INSERT OR REPLACE INTO macro_indicators (
                date, us_m2, us_jobless_claims, us_high_yield_spread, 
                uk_m4, uk_corporate_spread, uk_cpi_inflation, uk_claimant_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ''', payload)
        conn.commit()
        logger.info(f"Successfully upserted Macro Regime Snapshot for {snapshot_date}")
    except sqlite3.Error as e:
        logger.error(f"Database insertion failed: {e}")
        conn.rollback()
    finally:
        conn.close()

if __name__ == "__main__":
    logger.info("Initializing Master Macro Data Engine...")
    update_macro_indicators()
    logger.info("Macro Data Engine Execution Complete.")