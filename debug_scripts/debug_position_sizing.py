#!/usr/bin/env python3
"""
debug_position_sizing.py

Diagnostic script to audit the `atr_pct` data pipeline for the Position Sizing feature.
Issues NO writes. Performs SELECT queries only.
"""

import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = Path("data/analysis.db")

def run_diagnostics():
    print("\n" + "="*60)
    print(" 🔎 POSITION SIZING PIPELINE DIAGNOSTIC")
    print("="*60)

    if not DB_PATH.exists():
        print(f"[FAIL] Database not found at {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # 1. Schema Check
    print("\n[1] Checking Schema...")
    cursor.execute("PRAGMA table_info(quant_signals)")
    columns = [col[1] for col in cursor.fetchall()]
    if 'atr_pct' not in columns:
        print(" ❌ 'atr_pct' column is MISSING from quant_signals!")
    else:
        print(" ✅ 'atr_pct' column exists in quant_signals.")

    # 2. Latest Snapshot Check
    print("\n[2] Checking Latest Quant Signals (Are recent days NULL?)...")
    query_recent = """
        SELECT ticker, date, close_price, atr_pct
        FROM quant_signals
        ORDER BY date DESC
        LIMIT 10
    """
    df_recent = pd.read_sql_query(query_recent, conn)
    if not df_recent.empty:
        print(df_recent.to_string(index=False))
    else:
        print(" ⚠️ No data found in quant_signals.")

    # 3. Overall NULL Distribution
    print("\n[3] Overall atr_pct Population Stats...")
    cursor.execute("SELECT COUNT(*) FROM quant_signals WHERE atr_pct IS NULL")
    null_count = cursor.fetchone()[0]

    cursor.execute("SELECT COUNT(*) FROM quant_signals WHERE atr_pct IS NOT NULL")
    not_null_count = cursor.fetchone()[0]

    print(f" -> Rows with atr_pct = NULL     : {null_count:,}")
    print(f" -> Rows with atr_pct populated  : {not_null_count:,}")

    # 4. Route Query Simulation (What does Jinja actually receive?)
    print("\n[4] Simulating page_routes.py frontend payload (Sample: AAPL or highest vol stock)...")
    route_query = """
        SELECT s.ticker, q.date, q.atr_pct, s.current_price
        FROM stock_signals s
        LEFT JOIN quant_signals q ON s.ticker = q.ticker
            AND q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = s.ticker)
        WHERE s.ticker IN ('AAPL', 'SPY') OR s.ticker IS NOT NULL
        LIMIT 2
    """
    df_route = pd.read_sql_query(route_query, conn)
    if not df_route.empty:
        print(df_route.to_string(index=False))
        val = df_route['atr_pct'].iloc[0]
        print(f"\n⚠️ Value passed to Jinja (stock.atr_pct) for {df_route['ticker'].iloc[0]}: {repr(val)}")
    else:
        print(" ⚠️ No joined records found.")

    conn.close()
    print("\n" + "="*60 + "\n")

if __name__ == "__main__":
    run_diagnostics()