import time
import sqlite3
import logging
import requests
from typing import List, Tuple, Optional
from pathlib import Path

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - VERIFICATION - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

DB_PATH = Path("data/analysis.db")
API_ENDPOINT = "http://127.0.0.1:8090/api/macro/run-pipeline"

def verify_schema() -> bool:
    """
    Step 6a: Connects to SQLite and checks PRAGMA table_info to guarantee 
    the 'us_cpi_inflation' column exists in 'macro_indicators'.
    """
    logger.info("Checking database schema for 'us_cpi_inflation' column...")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        cursor.execute("PRAGMA table_info(macro_indicators)")
        columns = [info[1] for info in cursor.fetchall()]
        
        if 'us_cpi_inflation' in columns:
            logger.info("✅ Schema Verification Passed: 'us_cpi_inflation' column exists.")
            return True
        else:
            logger.error("❌ Schema Verification Failed: 'us_cpi_inflation' is missing. Migration did not run.")
            return False
    except sqlite3.Error as e:
        logger.error(f"Database error during schema check: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()

def trigger_macro_pipeline() -> bool:
    """
    Step 6b: Triggers the FastAPI macro refresh endpoint.
    """
    logger.info(f"Triggering Macro Data Pipeline via {API_ENDPOINT}...")
    try:
        response = requests.post(API_ENDPOINT, timeout=10)
        if response.status_code == 200:
            logger.info("✅ API Trigger Passed: Background job initiated successfully.")
            return True
        else:
            logger.error(f"❌ API Trigger Failed: HTTP {response.status_code} - {response.text}")
            return False
    except requests.RequestException as e:
        logger.error(f"Failed to reach FastAPI server. Is it running? Error: {e}")
        return False

def verify_cpi_data() -> None:
    """
    Step 6c: Queries the DB for the 5 most recent rows containing US CPI data.
    """
    logger.info("Querying SQLite for newly ingested CPI data...")
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        
        query = """
            SELECT date, us_cpi_inflation, uk_cpi_inflation 
            FROM macro_indicators 
            WHERE us_cpi_inflation IS NOT NULL 
            ORDER BY date DESC 
            LIMIT 5;
        """
        cursor.execute(query)
        rows: List[Tuple[str, float, float]] = cursor.fetchall()
        
        if not rows:
            logger.warning("⚠️ No data found! The background job might still be downloading from FRED/BoE. Wait 30 seconds and run this script again.")
            return

        logger.info("✅ Data Verification Passed! Latest CPI Records:")
        logger.info("-" * 60)
        logger.info(f"{'Date':<15} | {'US CPI (Raw)':<15} | {'UK CPI (Raw)':<15}")
        logger.info("-" * 60)
        
        for row in rows:
            date_str = str(row[0])
            us_cpi = f"{row[1]:.2f}" if row[1] is not None else "N/A"
            uk_cpi = f"{row[2]:.2f}" if row[2] is not None else "N/A"
            logger.info(f"{date_str:<15} | {us_cpi:<15} | {uk_cpi:<15}")
            
        logger.info("-" * 60)
        logger.info("Expect US CPI around 290-320 and UK CPI around 130-140.")
        
    except sqlite3.Error as e:
        logger.error(f"Database error during data verification: {e}")
    finally:
        if 'conn' in locals():
            conn.close()

def main() -> None:
    print("\n" + "="*50)
    print(" 🕵️ QUANTAMENTAL INFLATION VERIFICATION SUITE")
    print("="*50 + "\n")
    
    if not verify_schema():
        return
        
    if trigger_macro_pipeline():
        logger.info("Waiting 15 seconds to allow the Data Engine to fetch from FRED and ONS...")
        time.sleep(15)
        verify_cpi_data()
        
    print("\n" + "="*50)
    print(" NEXT STEP (Step 6d):")
    print(" Open your browser and navigate to the Market Sentiment page.")
    print(" Verify that the new US and UK Inflation charts render correctly ")
    print(" between the Liquidity and Credit modules, showing YoY %.")
    print("="*50 + "\n")

if __name__ == "__main__":
    main()