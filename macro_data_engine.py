import logging
import os
import requests
import io
import sqlite3
import pandas as pd
from datetime import datetime, timedelta
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry
from typing import Dict

from database import get_connection, init_db
import time_engine

logger = logging.getLogger(__name__)

# GUI name: "Macroeconomic Automation Schedulers (Data)". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.

# Standard headers to bypass WAF challenges
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json, text/csv'
}

ONS_TAXONOMY: Dict[str, str] = {
    "D7G7": "/economy/inflationandpriceindices/timeseries/d7g7/mm23/data",
    "BCJD": "/employmentandlabourmarket/peoplenotinwork/outofworkbenefits/timeseries/bcjd/unem/data"
}

def get_uk_cpi_yoy_series() -> pd.Series:
    """Single source of truth for UK CPI YoY% — reused by the Market Sentiment page's chart
    (uk_cpi_inflation vs FTSE 100) and the Pension account's CPI+target benchmark overlay."""
    conn = None
    try:
        conn = get_connection()
        df = pd.read_sql_query("SELECT date, uk_cpi_inflation FROM macro_indicators", conn)
    finally:
        if conn:
            conn.close()
    if df.empty:
        return pd.Series(dtype=float)
    df['date'] = pd.to_datetime(df['date'])
    df.set_index('date', inplace=True)
    return df['uk_cpi_inflation'].dropna().sort_index()


def get_retry_session() -> requests.Session:
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
        
        # Daily market metrics (Credit Spreads/Yield Curves) are instant. 
        # Structural economic data (M2/Claims) lags by ~30 days.
        lag_days = 0 if series_id in ['BAMLH0A0HYM2', 'BAMLHE00EHYIOAS', 'T10Y2Y', 'DFII10'] else 30
        df['publication_date'] = df['date'] + pd.DateOffset(days=lag_days)
        
        df.dropna(subset=['publication_date'], inplace=True)
        df.set_index('publication_date', inplace=True)
        df.rename(columns={'value': series_id}, inplace=True)
        
        return df[[series_id]]
        
    except Exception as e:
        logger.error(f"Failed to fetch FRED {series_id}: {e}")
        return pd.DataFrame()

def fetch_boe_data(session: requests.Session, series_code: str, start_date: datetime, end_date: datetime, lag_days: int = 30) -> pd.DataFrame:
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

        df['PUBLICATION_DATE'] = df['DATE'] + pd.DateOffset(days=lag_days)

        df.dropna(subset=['PUBLICATION_DATE'], inplace=True)
        df.set_index('PUBLICATION_DATE', inplace=True)
        
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
                    # End-of-month + 30-day publication lag avoids using data before it was publicly available.
                    dt = pd.to_datetime(raw_date, format='%Y %b') + pd.offsets.MonthEnd(1) + pd.DateOffset(days=30)
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
    fred_api_key = os.environ.get("FRED_API_KEY", "")
    if not fred_api_key:
        logger.error("FRED_API_KEY is not configured in settings. Aborting FRED API fetch.")

    end_dt = time_engine.now_local().replace(tzinfo=None)
    start_dt = end_dt - timedelta(days=730) 
    session = get_retry_session()
    
    dfs = []

    if fred_api_key:
        logger.info("Fetching FRED Institutional Data (2-Year History)...")
        
        # No direct UK corporate spread exists on FRED; Euro HY OAS (BAMLHE00EHYIOAS) is used as a
        # UK credit-stress proxy — highly correlated and shares the existing >3.0% circuit-breaker threshold.
        fred_tickers = ['WM2NS', 'ICSA', 'BAMLH0A0HYM2', 'BAMLHE00EHYIOAS', 'T10Y2Y', 'FEDFUNDS', 'DFII10']
        for ticker in fred_tickers:
            df = fetch_fred_api(session, ticker, start_dt, end_dt, fred_api_key)
            if not df.empty:
                dfs.append(df)

        # Fetch 13 extra months: pct_change(12) needs a full year of history before the window start;
        # without it NaN YoY rows leave legacy raw-index values (~313-320) that corrupt the chart.
        cpi_start = start_dt - timedelta(days=395)
        df_cpi_raw = fetch_fred_api(session, 'CPIAUCSL', cpi_start, end_dt, fred_api_key)
        if not df_cpi_raw.empty and 'CPIAUCSL' in df_cpi_raw.columns:
            # fetch_fred_api's index is each observation's date + a flat 30-day publication lag.
            # Resampling on that shifted index directly causes ~5 of every 12 months to collide
            # into the same calendar-month bucket (months have different lengths, the shift doesn't),
            # silently dropping them and desyncing pct_change(12) from a true 12-calendar-month span.
            # Undo the shift to resample on the true observation months, then reapply it to the
            # resulting month-end labels (matching fetch_ons_taxonomy_data's own MonthEnd+30d convention).
            true_dated = df_cpi_raw.copy()
            true_dated.index = true_dated.index - pd.DateOffset(days=30)
            monthly_cpi = true_dated['CPIAUCSL'].resample('ME').last().dropna()
            cpi_yoy = (monthly_cpi.pct_change(periods=12) * 100).dropna()
            cpi_yoy.index = cpi_yoy.index + pd.DateOffset(days=30)
            cpi_yoy_window = cpi_yoy[cpi_yoy.index >= pd.Timestamp(start_dt.date())]
            if not cpi_yoy_window.empty:
                dfs.append(cpi_yoy_window.rename('CPIAUCSL').to_frame())

    logger.info("Fetching Bank of England IADB Data (2-Year History)...")
    df_boe = fetch_boe_data(session, 'LPMAUYN', start_dt, end_dt)
    if not df_boe.empty:
        dfs.append(df_boe)

    df_boe_rate = fetch_boe_data(session, 'IUDBEDR', start_dt, end_dt, lag_days=0)
    if not df_boe_rate.empty:
        dfs.append(df_boe_rate)
        
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

    # No .bfill(): forward-fill only preserves point-in-time knowledge; back-fill would reintroduce lookahead bias.

    records = []
    for dt, row in merged_df.iterrows():
        records.append((
            dt.strftime("%Y-%m-%d"),
            float(row['WM2NS']) if 'WM2NS' in row and pd.notna(row['WM2NS']) else None,
            float(row['ICSA']) if 'ICSA' in row and pd.notna(row['ICSA']) else None,
            float(row['BAMLH0A0HYM2']) if 'BAMLH0A0HYM2' in row and pd.notna(row['BAMLH0A0HYM2']) else None,
            float(row['T10Y2Y']) if 'T10Y2Y' in row and pd.notna(row['T10Y2Y']) else None,
            # LPMAUYN is amounts outstanding in sterling millions; /1000 matches uk_m4's existing billions scale (us_m2's WM2NS is already billions).
            float(row['LPMAUYN']) / 1000 if 'LPMAUYN' in row and pd.notna(row['LPMAUYN']) else None,
            float(row['BAMLHE00EHYIOAS']) if 'BAMLHE00EHYIOAS' in row and pd.notna(row['BAMLHE00EHYIOAS']) else None,
            float(row['D7G7']) if 'D7G7' in row and pd.notna(row['D7G7']) else None,
            float(row['BCJD']) if 'BCJD' in row and pd.notna(row['BCJD']) else None,
            float(row['CPIAUCSL']) if 'CPIAUCSL' in row and pd.notna(row['CPIAUCSL']) else None,
            float(row['FEDFUNDS']) if 'FEDFUNDS' in row and pd.notna(row['FEDFUNDS']) else None,
            float(row['DFII10']) if 'DFII10' in row and pd.notna(row['DFII10']) else None,
            float(row['IUDBEDR']) if 'IUDBEDR' in row and pd.notna(row['IUDBEDR']) else None,
        ))

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        # INSERT OR IGNORE: preserves the point-in-time value recorded on each date; late revisions must not overwrite historical training rows.
        cursor.executemany('''
            INSERT OR IGNORE INTO macro_indicators (
                date, us_m2, us_jobless_claims, us_high_yield_spread, us_yield_curve,
                uk_m4, uk_corporate_spread, uk_cpi_inflation, uk_claimant_count,
                us_cpi_inflation, us_fed_funds_rate, us_real_yield_10y, uk_base_rate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', records)
        conn.commit()
        logger.info(f"Successfully bulk-inserted up to {cursor.rowcount} new Macro Regime historical days (ignoring existing to preserve PIT).")

        # CPI YoY never exceeds 20% in modern history; values > 20 are legacy raw-index artefacts, nullify them.
        cursor.execute("UPDATE macro_indicators SET us_cpi_inflation=NULL WHERE us_cpi_inflation > 20")

        # Patch all rows in the current window with correctly computed YoY% values.
        if 'CPIAUCSL' in merged_df.columns:
            cpi_patch = [
                (float(row['CPIAUCSL']) if pd.notna(row['CPIAUCSL']) else None, dt.strftime("%Y-%m-%d"))
                for dt, row in merged_df.iterrows()
            ]
            cursor.executemany(
                "UPDATE macro_indicators SET us_cpi_inflation=? WHERE date=?",
                [(v, d) for v, d in cpi_patch if v is not None],
            )
        conn.commit()
    except sqlite3.Error as e:
        logger.error(f"Database bulk insertion failed: {e}")
        if conn:
            conn.rollback()
    finally:
        if conn:
            conn.close()

if __name__ == "__main__":
    init_db()
    logger.info("Initializing Master Macro Data Engine...")
    update_macro_indicators()
    logger.info("Macro Data Engine Execution Complete.")