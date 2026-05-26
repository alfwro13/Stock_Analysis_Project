### `index_engine.py`
from io import StringIO
import logging
import requests
import pandas as pd
from datetime import datetime
from typing import List, Dict, Optional, Callable

from config import load_config
from database import get_connection, log_notification

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - INDEX_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- REGISTRY PATTERN FOR EXTENSIBILITY ---
# To add a new index, simply create a new dictionary entry here.
INDEX_REGISTRY: Dict[str, Dict] = {
    "SP500": {
        "url": "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
        "match_text": "Symbol",  # Keyword to reliably find the right HTML table
        "col_ticker": "Symbol",
        "col_company": "Security",
        "col_sector": "GICS Sector",
        # S&P tickers sometimes use '.' instead of '-' (e.g., BRK.B -> BRK-B) for Yahoo Finance
        "ticker_formatter": lambda t: str(t).strip().replace('.', '-')
    },
    "FTSE100": {
        "url": "https://en.wikipedia.org/wiki/FTSE_100_Index",
        "match_text": "Ticker",
        "col_ticker": "Ticker",
        "col_company": "Company",
        "col_sector": "Sector",
        # LSE tickers require the '.L' suffix for Yahoo Finance
        "ticker_formatter": lambda t: str(t).strip().replace('.', '-') + ".L"
    }
}


def fetch_index_constituents(index_key: str) -> List[Dict[str, str]]:
    """
    Natively scrapes Wikipedia for index constituents using pandas.
    Employs a custom User-Agent to prevent 403 Forbidden blocks.
    """
    if index_key not in INDEX_REGISTRY:
        logger.error(f"Index '{index_key}' is not defined in the Registry.")
        return []

    config = INDEX_REGISTRY[index_key]
    logger.info(f"Fetching constituents for {index_key} from {config['url']}...")

    try:
        # Wikipedia explicitly requires a descriptive User-Agent
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(config["url"], headers=headers, timeout=15)
        response.raise_for_status()

        # Parse HTML tables matching our specific keyword
        tables = pd.read_html(response.text, match=config["match_text"])
        if not tables:
            logger.error(f"No valid HTML tables found for {index_key} matching '{config['match_text']}'.")
            return []

        df = tables[0]
        
        # Verify required columns exist
        required_cols = [config["col_ticker"], config["col_company"], config["col_sector"]]
        for col in required_cols:
            if col not in df.columns:
                logger.error(f"Required column '{col}' missing from scraped table for {index_key}.")
                return []

        formatter: Callable = config["ticker_formatter"]
        
        records = []
        for _, row in df.iterrows():
            ticker = formatter(row[config["col_ticker"]])
            if not ticker or pd.isna(ticker):
                continue
                
            records.append({
                "ticker": ticker,
                "company_name": str(row[config["col_company"]]),
                "sector": str(row[config["col_sector"]]),
                "index_membership": index_key
            })

        logger.info(f"Successfully scraped {len(records)} constituents for {index_key}.")
        return records

    except requests.exceptions.RequestException as e:
        logger.error(f"Network error while scraping {index_key}: {e}")
        return []
    except Exception as e:
        logger.error(f"Data extraction failed for {index_key}: {e}")
        return []


def upsert_index_assets(records: List[Dict[str, str]]) -> bool:
    """
    Executes a surgical INSERT OR UPDATE operation.
    CRITICAL: This query explicitly omits the 'is_freetrade' column from the UPDATE block.
    This establishes the Freetrade Firewall, preventing us from overwriting broker integrations.
    It elegantly concatenates index memberships (e.g., AAPL becomes 'SP500,NASDAQ100').
    """
    if not records:
        return False

    current_time = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    upsert_data = [
        (
            r["ticker"], r["company_name"], r["sector"], 
            r["index_membership"], current_time
        )
        for r in records
    ]

    try:
        conn = get_connection()
        cursor = conn.cursor()

        # The Freetrade Firewall Query
        query = '''
            INSERT INTO market_universe (
                ticker, company_name, sector, is_index, index_membership, last_updated
            ) VALUES (?, ?, ?, 1, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                company_name = excluded.company_name,
                sector = excluded.sector,
                is_index = 1,
                last_updated = excluded.last_updated,
                index_membership = CASE 
                    WHEN index_membership IS NULL OR index_membership = '' THEN excluded.index_membership
                    WHEN instr(index_membership, excluded.index_membership) > 0 THEN index_membership 
                    ELSE index_membership || ',' || excluded.index_membership 
                END
        '''

        cursor.executemany(query, upsert_data)
        conn.commit()
        return True

    except Exception as e:
        logger.error(f"Failed to upsert index records to database: {e}")
        return False
    finally:
        if 'conn' in locals():
            conn.close()


def sync_all_indices() -> None:
    """
    Orchestration point triggered by the `scheduler_engine.py` background job.
    Reads user preferences and triggers the scraping pipeline.
    """
    config_data = load_config()
    index_cfg = config_data.get("SCHEDULING", {}).get("SYNC_INDICES", {})
    
    active_indices = index_cfg.get("INDICES", [])
    if not active_indices:
        logger.warning("Index Scraper triggered, but no indices are selected in settings.")
        return

    total_scraped = 0
    
    for index_key in active_indices:
        records = fetch_index_constituents(index_key)
        if records:
            if upsert_index_assets(records):
                total_scraped += len(records)
                logger.info(f"Database upsert complete for {index_key}.")

    if total_scraped > 0:
        log_notification("Success", f"Index Scraper successfully injected/updated {total_scraped} high-quality constituents into the Market Universe.")
    else:
        log_notification("Warning", "Index Scraper executed, but no constituents were found or updated.")


if __name__ == "__main__":
    # Allows you to test the scraper instantly via terminal: `python index_engine.py`
    sync_all_indices()