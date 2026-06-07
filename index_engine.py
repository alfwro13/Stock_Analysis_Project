from io import StringIO
import logging
import requests
import pandas as pd
from datetime import datetime, timezone
from typing import List, Dict, Optional, Callable

from config import load_config
from database import get_connection, log_notification

logger = logging.getLogger(__name__)

# To add a new index: add an entry here; no other code changes needed.
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
        "col_sector": "FTSE industry classification benchmark sector", # Updated Wikipedia Header
        # LSE tickers require the '.L' suffix for Yahoo Finance
        "ticker_formatter": lambda t: str(t).strip().replace('.', '-') + ".L"
    }
}


def fetch_index_constituents(index_key: str) -> List[Dict[str, str]]:
    """Scrape Wikipedia constituents; fuzzy column matching survives Wikipedia header renames."""
    if index_key not in INDEX_REGISTRY:
        logger.error(f"Index '{index_key}' is not defined in the Registry.")
        return []

    config = INDEX_REGISTRY[index_key]
    logger.info(f"Fetching constituents for {index_key} from {config['url']}...")

    try:
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/91.0.4472.124 Safari/537.36"
        }
        response = requests.get(config["url"], headers=headers, timeout=15)
        response.raise_for_status()

        tables = pd.read_html(StringIO(response.text), match=config["match_text"])
        if not tables:
            logger.error(f"No valid HTML tables found for {index_key} matching '{config['match_text']}'.")
            return []

        df = tables[0]
        
        # Case-insensitive subset match: tolerates Wikipedia's frequent column renames.
        col_map = {}
        target_cols = [config["col_ticker"], config["col_company"], config["col_sector"]]
        
        for target in target_cols:
            target_lower = target.lower()
            matched_col = next((c for c in df.columns if target_lower in str(c).lower()), None)
            
            if not matched_col:
                logger.error(f"Required column '{target}' missing from scraped table for {index_key}. Available columns: {list(df.columns)}")
                return []
            col_map[target] = matched_col

        formatter: Callable = config["ticker_formatter"]

        records = []
        for _, row in df.iterrows():
            raw_ticker = row[col_map[config["col_ticker"]]]
            if pd.isna(raw_ticker) or not str(raw_ticker).strip():
                continue
            ticker = formatter(raw_ticker)
                
            records.append({
                "ticker": ticker,
                "company_name": str(row[col_map[config["col_company"]]]),
                "sector": str(row[col_map[config["col_sector"]]]),
                "index_membership": index_key
            })

        logger.info(f"Successfully scraped {len(records)} constituents for {index_key}.")
        return records

    except requests.exceptions.RequestException:
        logger.exception(f"Network error while scraping {index_key}.")
        return []
    except Exception:
        logger.exception(f"Data extraction failed for {index_key}.")
        return []


def upsert_index_assets(records: List[Dict[str, str]]) -> bool:
    """Upsert records into market_universe; omits is_freetrade from UPDATE to preserve broker flags."""
    if not records:
        return False

    current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    upsert_data = [
        (
            r["ticker"], r["company_name"], r["sector"],
            r["index_membership"], current_time
        )
        for r in records
    ]

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

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

    except Exception:
        logger.exception("Failed to upsert index records to database.")
        return False
    finally:
        if conn is not None:
            conn.close()


def sync_all_indices() -> None:
    """Scheduler entry point: reads active indices from config and runs the scrape pipeline."""
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
    sync_all_indices()