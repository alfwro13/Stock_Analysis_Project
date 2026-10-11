import logging
import json
import math
import time
import random
from pathlib import Path
from datetime import datetime, timezone
from config import load_config, HISTORICAL_DIR, FUNDAMENTALS_DIR
from database import get_connection, get_all_account_tickers, get_registry_spot_future_tickers
from yahoo_engine import yahoo_engine
from db_helpers import get_portfolio_watchlist_tickers
from utils import ignored_tickers_set, is_excluded_from_yahoo_fetch, normalize_ticker, safe_ticker_filename
from typing import Optional, List, Dict

logger = logging.getLogger(__name__)

BLACKLIST_PATH = Path("data/freetrade_blacklist.json")
PROFILE_TTL_DAYS = 90
DEFAULT_BATCH_SIZE = 250
EMPTY_ANSWERS_TO_BLACKLIST = 5

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

_PURGED_TABLES = ("market_universe", "asset_profiles", "stock_signals", "quant_signals", "profile_empty_strikes")


def _purge_ticker_rows(cursor, ticker: str) -> int:
    removed = 0
    for table in _PURGED_TABLES:
        cursor.execute(f"DELETE FROM {table} WHERE ticker = ?", (ticker,))
        removed += cursor.rowcount
    return removed


def _protected_tickers() -> set:
    """Held, watched, ever-traded and Markets-registry tickers: valuation history and the Markets page still read their files and rows."""
    protected = set(get_portfolio_watchlist_tickers())
    protected.update(normalize_ticker(t) for t in get_all_account_tickers())
    protected.update(normalize_ticker(t) for t in get_registry_spot_future_tickers())
    return protected


def _delete_ticker_files(ticker: str, protected: set) -> int:
    """Removes the ticker's daily-history parquet and fundamentals dump, which QuantEngine.run_all() would otherwise re-analyse into a fresh stock_signals row every night."""
    safe = safe_ticker_filename(ticker)
    if not safe or normalize_ticker(ticker) in protected:
        return 0
    removed = 0
    for path in (HISTORICAL_DIR / f"{safe}.parquet", FUNDAMENTALS_DIR / f"{safe}.json"):
        try:
            if path.exists():
                path.unlink()
                removed += 1
        except OSError as e:
            logger.warning("Failed to delete %s: %s", path, e)
    return removed


def purge_blacklisted_tickers() -> int:
    """Re-applies the blacklist purge to rows and files other writers re-created; protected tickers are left alone."""
    blacklist = load_blacklist()
    if not blacklist:
        return 0
    protected = _protected_tickers()
    targets = sorted(t for t in blacklist if normalize_ticker(t) not in protected)

    removed = 0
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        for ticker in targets:
            removed += _purge_ticker_rows(cursor, ticker)
        conn.commit()
    finally:
        if conn:
            conn.close()
    for ticker in targets:
        removed += _delete_ticker_files(ticker, protected)
    if removed:
        logger.info("Blacklist purge removed %d leftover rows/files for blacklisted tickers.", removed)
    return removed


def _record_empty_answer(cursor, ticker: str) -> int:
    cursor.execute(
        """
        INSERT INTO profile_empty_strikes (ticker, strikes, last_empty_at) VALUES (?, 1, ?)
        ON CONFLICT(ticker) DO UPDATE SET strikes = strikes + 1, last_empty_at = excluded.last_empty_at
        """,
        (ticker, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")),
    )
    return cursor.execute("SELECT strikes FROM profile_empty_strikes WHERE ticker = ?", (ticker,)).fetchone()["strikes"]


def update_single_profile(ticker: str) -> bool:
    """Fetches yfinance metadata for one ticker, upserts asset_profiles; blacklists/purges after EMPTY_ANSWERS_TO_BLACKLIST consecutive empty answers, never on a failed call."""
    blacklist = load_blacklist()

    if ticker in blacklist:
        logger.info("Skipping profile update for %s: present in blacklist.", ticker)
        return False

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        info = yahoo_engine.get_ticker_info(ticker, raise_errors=True) or {}

        # Softened check: Mutual Funds have small .info dicts; only blacklist when zero identity fields.
        has_identity = 'shortName' in info or 'longName' in info or 'symbol' in info or 'regularMarketPrice' in info

        if not has_identity:
            strikes = _record_empty_answer(cursor, ticker)
            if strikes < EMPTY_ANSWERS_TO_BLACKLIST:
                conn.commit()
                logger.warning(
                    "Empty call strike %d/%d for %s: Yahoo returned no profile data.",
                    strikes, EMPTY_ANSWERS_TO_BLACKLIST, ticker,
                )
                return False

            logger.warning(
                "Empty call strike %d/%d for %s. Permanently blacklisting and purging from database.",
                strikes, EMPTY_ANSWERS_TO_BLACKLIST, ticker,
            )
            blacklist.add(ticker)
            save_blacklist(blacklist)

            _purge_ticker_rows(cursor, ticker)
            conn.commit()
            _delete_ticker_files(ticker, _protected_tickers())
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

        cursor.execute("DELETE FROM profile_empty_strikes WHERE ticker = ?", (ticker,))
        conn.commit()
        return True

    except Exception as e:
        logger.error("Failed to fetch/save profile for %s (not an empty-call strike): %s", ticker, e)
        return False
    finally:
        if conn:
            conn.close()

def profiler_schedule() -> dict:
    cfg = load_config().get("SCHEDULING", {}).get("PROFILER_ENGINE", {})
    return {
        "enabled": bool(cfg.get("ENABLED", False)),
        "days": list(cfg.get("DAYS") or []),
        "batch_size": int(cfg.get("BATCH_SIZE", DEFAULT_BATCH_SIZE)),
    }


def _profile_queue() -> tuple:
    """(rows, excluded_count, freetrade_only); rows are (ticker, last_verified_date|None, is_stale) for fetchable tickers only, so tiles and batch picker share one definition of pending."""
    config_data = load_config()
    freetrade_only: bool = config_data.get("UI_PREFERENCES", {}).get("FREETRADE_ONLY_MODE", False)
    # Freetrade Firewall: only index constituents tradable on Freetrade plus anything already scored
    universe_filter = "WHERE is_index = 1 AND is_freetrade = 1" if freetrade_only else ""
    blacklist = load_blacklist()
    ignored = ignored_tickers_set(config_data)

    conn = None
    try:
        conn = get_connection()
        fetched = conn.execute(f"""
            WITH AllTickers AS (
                SELECT ticker FROM market_universe {universe_filter}
                UNION
                SELECT ticker FROM stock_signals
                UNION
                SELECT ticker FROM quant_signals
            )
            SELECT a.ticker AS ticker,
                   p.last_verified_date AS last_verified_date,
                   COALESCE(p.last_verified_date < date('now', '-{PROFILE_TTL_DAYS} days'), 0) AS is_stale
            FROM AllTickers a
            LEFT JOIN asset_profiles p ON a.ticker = p.ticker
        """).fetchall()
    finally:
        if conn:
            conn.close()

    rows = []
    excluded = 0
    for row in fetched:
        ticker = row["ticker"]
        if ticker in blacklist or is_excluded_from_yahoo_fetch(ticker, ignored):
            excluded += 1
            continue
        rows.append((ticker, row["last_verified_date"], bool(row["is_stale"])))
    return rows, excluded, freetrade_only


def run_profile_audit(limit: int = DEFAULT_BATCH_SIZE):
    logger.info("Initiating Asset Profile Audit (limit: %d)...", limit)
    try:
        purge_blacklisted_tickers()
        rows, _, _ = _profile_queue()
        priority = set(get_portfolio_watchlist_tickers())
    except Exception as e:
        logger.error("Fatal error fetching target tickers during Asset Profile Audit: %s", e)
        return

    pending = [r for r in rows if r[1] is None or r[2]]
    # Portfolio/Watchlist first (rule 3), then never-profiled ('' sorts first), then oldest verification
    pending.sort(key=lambda r: (r[0].upper() not in priority, r[1] or "", r[0]))
    tickers_to_update = [r[0] for r in pending[:limit]]

    if not tickers_to_update:
        logger.info("All asset profiles are up-to-date within the last %d days. No action needed.", PROFILE_TTL_DAYS)
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

def get_profiler_queue_breakdown() -> Dict[str, Optional[int]]:
    """Settings queue tiles over fetchable tickers, plus excluded/24h-refreshed counts and runs/days to clear (None when unscheduled)."""
    schedule = profiler_schedule()
    breakdown: Dict[str, Optional[int]] = {
        "eligible_count": 0,
        "profiled_count": 0,
        "stale_count": 0,
        "pending_count": 0,
        "total_profiles": 0,
        "excluded_count": 0,
        "refreshed_24h_count": 0,
        "batch_size": schedule["batch_size"],
        "runs_to_clear": 0,
        "days_to_clear": None,
        "firewall_active": 0,
    }

    conn = None
    try:
        rows, excluded, freetrade_only = _profile_queue()
        breakdown["firewall_active"] = 1 if freetrade_only else 0
        breakdown["excluded_count"] = excluded
        breakdown["eligible_count"] = len(rows)
        breakdown["profiled_count"] = sum(1 for r in rows if r[1] is not None)
        breakdown["stale_count"] = sum(1 for r in rows if r[1] is not None and r[2])
        breakdown["pending_count"] = sum(1 for r in rows if r[1] is None or r[2])

        conn = get_connection()
        row = conn.execute("SELECT COUNT(*) AS c FROM asset_profiles").fetchone()
        breakdown["total_profiles"] = int(row["c"]) if row else 0
        row = conn.execute(
            "SELECT COUNT(*) AS c FROM asset_profiles WHERE last_verified_date >= datetime('now', '-1 day')"
        ).fetchone()
        breakdown["refreshed_24h_count"] = int(row["c"]) if row else 0

        if schedule["batch_size"] > 0:
            runs = math.ceil(breakdown["pending_count"] / schedule["batch_size"])
            breakdown["runs_to_clear"] = runs
            if schedule["enabled"] and schedule["days"]:
                breakdown["days_to_clear"] = math.ceil(runs * 7 / len(schedule["days"]))
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