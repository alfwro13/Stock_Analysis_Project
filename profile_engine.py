import logging
import json
import time
import random
from pathlib import Path
from datetime import datetime, timezone
from config import load_config
from database import get_connection
from yahoo_engine import yahoo_engine
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

BLACKLIST_PATH = Path("data/freetrade_blacklist.json")

def load_blacklist() -> set:
    if BLACKLIST_PATH.exists():
        try:
            with open(BLACKLIST_PATH, 'r') as f:
                return set(json.load(f))
        except Exception:
            logger.warning("Failed to read profile blacklist from %s", BLACKLIST_PATH, exc_info=True)
    return set()

def save_blacklist(blacklist: set) -> None:
    try:
        BLACKLIST_PATH.parent.mkdir(parents=True, exist_ok=True)
        with open(BLACKLIST_PATH, 'w') as f:
            json.dump(sorted(list(blacklist)), f, indent=4)
    except Exception as e:
        logger.error("Failed to save blacklist: %s", e)

def update_single_profile(ticker: str) -> bool:
    """Fetches yfinance metadata for one ticker, upserts asset_profiles; blacklists/purges on empty payload."""
    blacklist = load_blacklist()

    if ticker in blacklist:
        logger.info("Skipping profile update for %s: present in blacklist.", ticker)
        return False

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        info = yahoo_engine.get_ticker_info(ticker) or {}

        # Softened check: Mutual Funds have small .info dicts; only blacklist when zero identity fields.
        has_identity = 'shortName' in info or 'longName' in info or 'symbol' in info or 'regularMarketPrice' in info

        if not info or not has_identity:
            logger.warning("No valid payload for %s. Permanently blacklisting and purging from database.", ticker)
            blacklist.add(ticker)
            save_blacklist(blacklist)

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
        last_verified = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        company_name = company_name.replace(" - Common Stock", "").replace(" Common Stock", "").strip()

        cursor.execute('''
            INSERT OR REPLACE INTO asset_profiles
            (ticker, company_name, sector, industry, country, exchange, currency, quote_type, business_summary, last_verified_date)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (ticker, company_name, sector, industry, country, exchange, currency, quote_type, summary, last_verified))

        conn.commit()
        return True

    except Exception as e:
        logger.error("Failed to fetch/save profile for %s: %s", ticker, e)
        return False
    finally:
        if conn:
            conn.close()

def run_profile_audit(limit: int = 250):
    logger.info("Initiating Asset Profile Audit (limit: %d)...", limit)
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        config_data = load_config()
        freetrade_only = config_data.get("UI_PREFERENCES", {}).get("FREETRADE_ONLY_MODE", False)

        if freetrade_only:
            # Freetrade Firewall: only audit index constituents and portfolio/watchlist assets
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
        logger.error("Fatal error fetching target tickers during Asset Profile Audit: %s", e)
        return
    finally:
        # Close before the per-ticker fetch loop to prevent long-held DB locks
        if conn:
            conn.close()

    if not tickers_to_update:
        logger.info("All asset profiles are up-to-date within the last 90 days. No action needed.")
        return

    logger.info("Found %d profiles requiring initialization or refresh.", len(tickers_to_update))

    updated_count = 0
    for i, ticker in enumerate(tickers_to_update):
        if i > 0 and i % 50 == 0:
            logger.info("Progress: %d/%d fetched...", i, len(tickers_to_update))

        success = update_single_profile(ticker)
        if success:
            updated_count += 1

        time.sleep(random.uniform(0.5, 1.5))

    logger.info("Asset Profile Audit complete. Updated %d static metadata records.", updated_count)

def get_profiler_queue_breakdown() -> Dict[str, int]:
    """Returns profiler queue dict: eligible_count, profiled_count, stale_count, pending_count, total_profiles, firewall_active."""
    config_data = load_config()
    freetrade_only: bool = config_data.get("UI_PREFERENCES", {}).get("FREETRADE_ONLY_MODE", False)

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

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute(eligibility_cte + " SELECT COUNT(*) AS c FROM AllTickers")
        row = cursor.fetchone()
        breakdown["eligible_count"] = int(row["c"]) if row else 0

        cursor.execute(
            eligibility_cte + """
                SELECT COUNT(*) AS c
                FROM AllTickers a
                INNER JOIN asset_profiles p ON a.ticker = p.ticker
            """
        )
        row = cursor.fetchone()
        breakdown["profiled_count"] = int(row["c"]) if row else 0

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

        cursor.execute("SELECT COUNT(*) AS c FROM asset_profiles")
        row = cursor.fetchone()
        breakdown["total_profiles"] = int(row["c"]) if row else 0

        return breakdown
    except Exception as e:
        logger.error("Error computing profiler queue breakdown: %s", e)
        return breakdown
    finally:
        if conn:
            conn.close()

def count_pending_profiles() -> int:
    """Thin wrapper returning just the pending_count integer from get_profiler_queue_breakdown()."""
    return get_profiler_queue_breakdown().get("pending_count", 0)


if __name__ == "__main__":
    print("WARNING: Running initial massive data harvest. This will take ~1 to 1.5 hours to respect rate limits.")
    run_profile_audit(limit=5000)