# profile_engine.py
import time
import random
import logging
import json
from pathlib import Path
from datetime import datetime
import yfinance as yf
from config import load_config
from database import get_connection
from tools.network_engine import yahoo_connection_boundary
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

BLACKLIST_PATH = Path("data/freetrade_blacklist.json")

def load_blacklist() -> set:
    if BLACKLIST_PATH.exists():
        try:
            with open(BLACKLIST_PATH, 'r') as f:
                return set(json.load(f))
        except Exception:
            pass
    return set()

def save_blacklist(blacklist: set) -> None:
    try:
        BLACKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(BLACKLIST_PATH, 'w') as f:
            json.dump(sorted(list(blacklist)), f, indent=4)
    except Exception as e:
        logger.error(f"Failed to save blacklist: {e}")

def update_single_profile(ticker: str) -> bool:
    """
    Fetches static metadata for a single ticker via yfinance and inserts it into asset_profiles.
    Handles blacklisting and orphan purging automatically.
    Returns True if successful, False if blacklisted or failed.
    """
    blacklist = load_blacklist()
    
    # Skip immediately if it has been blacklisted previously
    if ticker in blacklist:
        logger.info(f"Skipping profile update for {ticker}: Present in blacklist.")
        return False

    conn = get_connection()
    cursor = conn.cursor()
    
    with yahoo_connection_boundary(f"Profile Audit: {ticker}") as session:
        try:
            info = yf.Ticker(ticker, session=session).info
            
            # --- THE AUTOMATED BLACKLIST PURGE ---
            # Softened check: Mutual Funds often have very small info dictionaries. 
            # We only blacklist if we get absolutely no identifying information back from Yahoo.
            has_identity = 'shortName' in info or 'longName' in info or 'symbol' in info or 'regularMarketPrice' in info
            
            if not info or not has_identity:
                logger.warning(f"No valid payload for {ticker}. Permanently blacklisting and purging from database.")
                blacklist.add(ticker)
                save_blacklist(blacklist)
                
                # Ruthlessly delete the orphan from all tables
                cursor.execute("DELETE FROM market_universe WHERE ticker = ?", (ticker,))
                cursor.execute("DELETE FROM asset_profiles WHERE ticker = ?", (ticker,))
                cursor.execute("DELETE FROM stock_signals WHERE ticker = ?", (ticker,))
                cursor.execute("DELETE FROM quant_signals WHERE ticker = ?", (ticker,))
                conn.commit()
                return False
                
            company_name = info.get('shortName') or info.get('longName') or ticker
            sector = info.get('sector', 'Unclassified')
            industry = info.get('industry', 'Unclassified')
            country = info.get('country', 'Unknown')
            exchange = info.get('exchange', 'Unknown')
            currency = info.get('currency', 'USD')
            quote_type = info.get('quoteType', 'EQUITY')
            summary = info.get('longBusinessSummary', 'No business summary available.')
            last_verified = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

            company_name = company_name.replace(" - Common Stock", "").replace(" Common Stock", "").strip()
            
            cursor.execute('''
                INSERT OR REPLACE INTO asset_profiles 
                (ticker, company_name, sector, industry, country, exchange, currency, quote_type, business_summary, last_verified_date)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ''', (ticker, company_name, sector, industry, country, exchange, currency, quote_type, summary, last_verified))
            
            conn.commit()
            return True
            
        except Exception as e:
            logger.error(f"Failed to fetch/save profile for {ticker}: {e}")
            return False
        finally:
            conn.close()

def run_profile_audit(limit: int = 250):
    logger.info(f"Initiating Audit for Central Asset Profiles (Limit: {limit})...")
    conn = get_connection()
    cursor = conn.cursor()

    try:
        config_data = load_config()
        freetrade_only = config_data.get("UI_PREFERENCES", {}).get("FREETRADE_ONLY_MODE", False)

        if freetrade_only:
            # THE FIREWALL: Only audit active portfolio/watchlist assets OR tradable index constituents
            cursor.execute("""
                WITH AllTickers AS (
                    SELECT ticker FROM market_universe WHERE is_index = 1 AND is_freetrade = 1
                    UNION
                    SELECT ticker FROM stock_signals
                    UNION
                    SELECT ticker FROM quant_signals
                )
                SELECT a.ticker 
                FROM AllTickers a
                LEFT JOIN asset_profiles p ON a.ticker = p.ticker
                WHERE p.ticker IS NULL 
                   OR p.last_verified_date < date('now', '-90 days')
                LIMIT ?
            """, (limit,))
        else:
            # LEGACY MODE: Audit everything
            cursor.execute("""
                WITH AllTickers AS (
                    SELECT ticker FROM market_universe
                    UNION
                    SELECT ticker FROM stock_signals
                    UNION
                    SELECT ticker FROM quant_signals
                )
                SELECT a.ticker 
                FROM AllTickers a
                LEFT JOIN asset_profiles p ON a.ticker = p.ticker
                WHERE p.ticker IS NULL 
                   OR p.last_verified_date < date('now', '-90 days')
                LIMIT ?
            """, (limit,))
        
        rows = cursor.fetchall()
        tickers_to_update = [row['ticker'] for row in rows]
        
    except Exception as e:
        logger.error(f"Fatal error fetching target tickers during Asset Profile Audit: {e}")
        return
    finally:
        # Close connection before executing the long-running fetch loop to prevent DB locks
        conn.close()

    if not tickers_to_update:
        logger.info("All asset profiles are up-to-date within the last 90 days. No action needed.")
        return

    logger.info(f"Found {len(tickers_to_update)} profiles requiring initialization or refresh.")
    
    updated_count = 0
    for i, ticker in enumerate(tickers_to_update):
        if i > 0 and i % 50 == 0: 
            logger.info(f"Progress: {i}/{len(tickers_to_update)} fetched...")
            
        success = update_single_profile(ticker)
        if success:
            updated_count += 1
            
        # Respect API rate limits gracefully
        time.sleep(random.uniform(0.5, 1.5))
            
    logger.info(f"Asset Profile Audit complete. Updated {updated_count} static metadata records.")

def get_profiler_queue_breakdown() -> Dict[str, int]:
    """
    Returns a full breakdown of the profiler queue state in a single DB round-trip.

    Keys:
      - eligible_count : tickers in scope (respects FREETRADE_ONLY_MODE firewall)
      - profiled_count : eligible tickers that already have a row in asset_profiles
      - stale_count    : profiled tickers whose last_verified_date is >90 days old
      - pending_count  : eligible tickers needing a profile fetch (missing OR stale)
      - total_profiles : total rows in asset_profiles across the entire DB
      - firewall_active: 1 if FREETRADE_ONLY_MODE is enabled, else 0
    """
    config_data = load_config()
    freetrade_only: bool = config_data.get("UI_PREFERENCES", {}).get("FREETRADE_ONLY_MODE", False)

    # Build the eligibility CTE conditionally to respect the Freetrade Firewall.
    if freetrade_only:
        eligibility_cte = """
            WITH AllTickers AS (
                SELECT ticker FROM market_universe WHERE is_index = 1 AND is_freetrade = 1
                UNION
                SELECT ticker FROM stock_signals
                UNION
                SELECT ticker FROM quant_signals
            )
        """
    else:
        eligibility_cte = """
            WITH AllTickers AS (
                SELECT ticker FROM market_universe
                UNION
                SELECT ticker FROM stock_signals
                UNION
                SELECT ticker FROM quant_signals
            )
        """

    breakdown: Dict[str, int] = {
        "eligible_count": 0,
        "profiled_count": 0,
        "stale_count": 0,
        "pending_count": 0,
        "total_profiles": 0,
        "firewall_active": 1 if freetrade_only else 0,
    }

    conn = get_connection()
    cursor = conn.cursor()
    try:
        # 1. Eligible tickers in scope
        cursor.execute(eligibility_cte + " SELECT COUNT(*) AS c FROM AllTickers")
        row = cursor.fetchone()
        breakdown["eligible_count"] = int(row["c"]) if row else 0

        # 2. Eligible tickers that ARE already profiled (regardless of staleness)
        cursor.execute(
            eligibility_cte + """
                SELECT COUNT(*) AS c
                FROM AllTickers a
                INNER JOIN asset_profiles p ON a.ticker = p.ticker
            """
        )
        row = cursor.fetchone()
        breakdown["profiled_count"] = int(row["c"]) if row else 0

        # 3. Eligible tickers whose existing profile is stale (>90 days)
        cursor.execute(
            eligibility_cte + """
                SELECT COUNT(*) AS c
                FROM AllTickers a
                INNER JOIN asset_profiles p ON a.ticker = p.ticker
                WHERE p.last_verified_date < date('now', '-90 days')
            """
        )
        row = cursor.fetchone()
        breakdown["stale_count"] = int(row["c"]) if row else 0

        # 4. Pending = missing OR stale (matches existing count_pending_profiles logic)
        cursor.execute(
            eligibility_cte + """
                SELECT COUNT(*) AS c
                FROM AllTickers a
                LEFT JOIN asset_profiles p ON a.ticker = p.ticker
                WHERE p.ticker IS NULL
                   OR p.last_verified_date < date('now', '-90 days')
            """
        )
        row = cursor.fetchone()
        breakdown["pending_count"] = int(row["c"]) if row else 0

        # 5. Global asset_profiles row count (debug/transparency value)
        cursor.execute("SELECT COUNT(*) AS c FROM asset_profiles")
        row = cursor.fetchone()
        breakdown["total_profiles"] = int(row["c"]) if row else 0

        return breakdown
    except Exception as e:
        logger.error(f"Error computing profiler queue breakdown: {e}")
        return breakdown
    finally:
        conn.close()

def count_pending_profiles() -> int:
    """
    Counts how many assets currently lack profiles or have stale data (>90 days).
    Respects the Freetrade Firewall UI setting.

    Thin legacy wrapper around get_profiler_queue_breakdown() to preserve the
    existing API contract used by callers that only need the pending integer.
    """
    return get_profiler_queue_breakdown().get("pending_count", 0)


if __name__ == "__main__":
    print("WARNING: Running initial massive data harvest. This will take ~1 to 1.5 hours to respect rate limits.")
    run_profile_audit(limit=5000)