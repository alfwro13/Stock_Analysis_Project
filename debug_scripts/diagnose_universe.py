# tools/diagnose_universe.py
"""
Diagnostic script for the Market Universe Pipeline.
Investigates why count_pending_profiles() returns 0 when both Freetrade
and Index Constituents have been synced.

Run from the project root:
    python -m tools.diagnose_universe
"""
import logging
import sys
from pathlib import Path
from typing import List, Tuple

# Allow running from project root
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from database import get_connection
from config import load_config

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - DIAGNOSE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def hr(title: str) -> None:
    """Prints a section divider."""
    print("\n" + "=" * 70)
    print(f"  {title}")
    print("=" * 70)


def section_config() -> None:
    hr("1. CONFIG STATE")
    cfg = load_config()
    ft_only = cfg.get("UI_PREFERENCES", {}).get("FREETRADE_ONLY_MODE", False)
    profiler = cfg.get("SCHEDULING", {}).get("PROFILER_ENGINE", {})
    indices = cfg.get("SCHEDULING", {}).get("SYNC_INDICES", {})
    ft_sync = cfg.get("SCHEDULING", {}).get("FREETRADE_SYNC", {})

    print(f"  FREETRADE_ONLY_MODE        : {ft_only}")
    print(f"  PROFILER_ENGINE.ENABLED    : {profiler.get('ENABLED')}")
    print(f"  PROFILER_ENGINE.BATCH_SIZE : {profiler.get('BATCH_SIZE')}")
    print(f"  SYNC_INDICES.ENABLED       : {indices.get('ENABLED')}")
    print(f"  SYNC_INDICES.INDICES       : {indices.get('INDICES')}")
    print(f"  FREETRADE_SYNC.ENABLED     : {ft_sync.get('ENABLED')}")


def section_universe_counts() -> None:
    hr("2. MARKET UNIVERSE FLAG COUNTS")
    conn = get_connection()
    try:
        cursor = conn.cursor()

        queries: List[Tuple[str, str]] = [
            ("Total rows in market_universe",
             "SELECT COUNT(*) AS c FROM market_universe"),
            ("Rows with is_freetrade = 1",
             "SELECT COUNT(*) AS c FROM market_universe WHERE is_freetrade = 1"),
            ("Rows with is_index = 1",
             "SELECT COUNT(*) AS c FROM market_universe WHERE is_index = 1"),
            ("Rows with BOTH is_index = 1 AND is_freetrade = 1 (the intersection)",
             "SELECT COUNT(*) AS c FROM market_universe WHERE is_index = 1 AND is_freetrade = 1"),
            ("SP500 members tagged",
             "SELECT COUNT(*) AS c FROM market_universe WHERE index_membership LIKE '%SP500%'"),
            ("FTSE100 members tagged",
             "SELECT COUNT(*) AS c FROM market_universe WHERE index_membership LIKE '%FTSE100%'"),
            ("SP500 members ALSO on Freetrade",
             "SELECT COUNT(*) AS c FROM market_universe WHERE index_membership LIKE '%SP500%' AND is_freetrade = 1"),
            ("FTSE100 members ALSO on Freetrade",
             "SELECT COUNT(*) AS c FROM market_universe WHERE index_membership LIKE '%FTSE100%' AND is_freetrade = 1"),
        ]

        for label, sql in queries:
            cursor.execute(sql)
            row = cursor.fetchone()
            count = row['c'] if row else 0
            print(f"  {label:<60} : {count}")
    finally:
        conn.close()


def section_orphans() -> None:
    """Find index constituents that did NOT match any Freetrade row."""
    hr("3. ORPHANED INDEX CONSTITUENTS (is_index=1 but is_freetrade=0)")
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT ticker, company_name, index_membership, exchange
            FROM market_universe
            WHERE is_index = 1 AND (is_freetrade = 0 OR is_freetrade IS NULL)
            ORDER BY index_membership, ticker
            LIMIT 40
        """)
        rows = cursor.fetchall()
        if not rows:
            print("  (none — all index constituents are also Freetrade tradable)")
            return
        print(f"  Showing first 40 of orphaned index tickers:\n")
        print(f"  {'TICKER':<12} {'INDEX':<20} {'EXCHANGE':<12} COMPANY")
        print(f"  {'-' * 12} {'-' * 20} {'-' * 12} {'-' * 30}")
        for r in rows:
            print(f"  {r['ticker']:<12} {(r['index_membership'] or 'N/A'):<20} "
                  f"{(r['exchange'] or 'N/A'):<12} {r['company_name'] or ''}")
    finally:
        conn.close()


def section_ticker_format_probe() -> None:
    """Probe specific high-value tickers to see how they were stored."""
    hr("4. KEY TICKER FORMAT PROBE")
    probes: List[str] = [
        "AAPL", "MSFT", "GOOGL", "AMZN", "TSLA", "NVDA",            # SP500 sanity
        "BRK-B", "BRK.B", "BF-B", "BF.B",                           # SP500 with dots
        "LLOY.L", "BP.L", "BP-.L", "RR.L", "RR-.L",                 # FTSE100 with trailing dots
        "BT-A.L", "BT.A.L", "IMB.L", "HSBA.L"                       # FTSE100 standard
    ]
    conn = get_connection()
    try:
        cursor = conn.cursor()
        print(f"  {'TICKER':<14} {'is_idx':<8} {'is_ft':<8} {'index_membership':<20} EXCHANGE")
        print(f"  {'-' * 14} {'-' * 8} {'-' * 8} {'-' * 20} {'-' * 12}")
        for t in probes:
            cursor.execute("""
                SELECT ticker, is_index, is_freetrade, index_membership, exchange
                FROM market_universe WHERE ticker = ?
            """, (t,))
            row = cursor.fetchone()
            if row:
                print(f"  {row['ticker']:<14} {str(row['is_index']):<8} "
                      f"{str(row['is_freetrade']):<8} "
                      f"{(row['index_membership'] or '-'):<20} "
                      f"{row['exchange'] or '-'}")
            else:
                print(f"  {t:<14} (NOT FOUND in market_universe)")
    finally:
        conn.close()


def section_pending_profiles_simulation() -> None:
    """Replicate the exact query used by count_pending_profiles()."""
    hr("5. PENDING PROFILES QUERY (replicates profile_engine logic)")
    cfg = load_config()
    ft_only = cfg.get("UI_PREFERENCES", {}).get("FREETRADE_ONLY_MODE", False)
    print(f"  FREETRADE_ONLY_MODE active : {ft_only}\n")

    conn = get_connection()
    try:
        cursor = conn.cursor()

        # The exact firewall query
        if ft_only:
            cte_query = """
                WITH AllTickers AS (
                    SELECT ticker FROM market_universe WHERE is_index = 1 AND is_freetrade = 1
                    UNION
                    SELECT ticker FROM stock_signals
                    UNION
                    SELECT ticker FROM quant_signals
                )
                SELECT COUNT(*) AS c FROM AllTickers
            """
            label = "Eligible tickers (firewall ON)"
        else:
            cte_query = """
                WITH AllTickers AS (
                    SELECT ticker FROM market_universe
                    UNION
                    SELECT ticker FROM stock_signals
                    UNION
                    SELECT ticker FROM quant_signals
                )
                SELECT COUNT(*) AS c FROM AllTickers
            """
            label = "Eligible tickers (firewall OFF)"

        cursor.execute(cte_query)
        eligible = cursor.fetchone()['c']
        print(f"  {label:<55} : {eligible}")

        cursor.execute("SELECT COUNT(*) AS c FROM asset_profiles")
        profiled = cursor.fetchone()['c']
        print(f"  {'Rows already in asset_profiles':<55} : {profiled}")

        cursor.execute("SELECT COUNT(*) AS c FROM asset_profiles WHERE last_verified_date < date('now', '-90 days')")
        stale = cursor.fetchone()['c']
        print(f"  {'Profiles older than 90 days (stale)':<55} : {stale}")

        cursor.execute("""
            SELECT COUNT(DISTINCT ticker) AS c FROM market_universe
            WHERE is_index = 1
        """)
        idx_total = cursor.fetchone()['c']
        print(f"  {'Total tickers with is_index = 1':<55} : {idx_total}")

        cursor.execute("""
            SELECT COUNT(DISTINCT ticker) AS c FROM market_universe
            WHERE is_freetrade = 1
        """)
        ft_total = cursor.fetchone()['c']
        print(f"  {'Total tickers with is_freetrade = 1':<55} : {ft_total}")
    finally:
        conn.close()


def section_signals_tables() -> None:
    hr("6. SIGNAL TABLES (portfolio/watchlist contributors)")
    conn = get_connection()
    try:
        cursor = conn.cursor()
        for table in ("stock_signals", "quant_signals"):
            try:
                cursor.execute(f"SELECT COUNT(DISTINCT ticker) AS c FROM {table}")
                row = cursor.fetchone()
                count = row['c'] if row else 0
                print(f"  Distinct tickers in {table:<20} : {count}")
            except Exception as e:
                print(f"  Could not query {table}: {e}")
    finally:
        conn.close()


def main() -> None:
    print("\n" + "#" * 70)
    print("#  MARKET UNIVERSE PIPELINE DIAGNOSTIC")
    print("#" * 70)
    try:
        section_config()
        section_universe_counts()
        section_pending_profiles_simulation()
        section_orphans()
        section_ticker_format_probe()
        section_signals_tables()
        print("\n" + "#" * 70)
        print("#  DIAGNOSTIC COMPLETE")
        print("#" * 70 + "\n")
    except Exception as e:
        logger.error(f"Diagnostic failed: {e}", exc_info=True)


if __name__ == "__main__":
    main()