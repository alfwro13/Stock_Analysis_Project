import sqlite3
import pandas as pd
from pathlib import Path

DB_PATH = Path("data/analysis.db")

def run_waterfall_diagnostics():
    print("\n" + "="*50)
    print(" 🕵️ QUALITY COMPOUNDERS WATERFALL DIAGNOSTIC")
    print("="*50)
    
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Total Universe Base
    cursor.execute("SELECT COUNT(*) FROM stock_signals")
    total = cursor.fetchone()[0]
    print(f"Total assets in stock_signals: {total}")
    
    # 2. Quote Type Filter
    cursor.execute("SELECT COUNT(*) FROM stock_signals WHERE quote_type = 'EQUITY'")
    equities = cursor.fetchone()[0]
    print(f"Total EQUITIES: {equities}")

    # 3. Individual Filter Pass Rates
    filters = {
        "ROE > 15%": "roe > 0.15",
        "Debt/Equity < 1.0": "debt_to_equity < 1.0",
        "Profit Margin > 10%": "profit_margin > 0.10",
        "Revenue Growth > 5%": "revenue_growth > 0.05",
        "Current Ratio > 1.5": "current_ratio > 1.5",
        "Quant Score >= 60": "composite_score >= 60",
        "P/E < 35": "trailing_pe < 35",
        "P/E > 10": "trailing_pe > 10"
    }

    print("\n--- Individual Filter Pass Rates ---")
    for name, condition in filters.items():
        query = f"SELECT COUNT(*) FROM stock_signals WHERE quote_type = 'EQUITY' AND {condition}"
        cursor.execute(query)
        passed = cursor.fetchone()[0]
        print(f"{name:<20}: {passed:<5} stocks passed")

    # 4. Waterfall Cumulative Survival
    print("\n--- Cumulative Waterfall Survival ---")
    cumulative_query = "SELECT COUNT(*) FROM stock_signals WHERE quote_type = 'EQUITY'"
    for name, condition in filters.items():
        cumulative_query += f" AND {condition}"
        cursor.execute(cumulative_query)
        survivors = cursor.fetchone()[0]
        print(f"After + {name:<18}: {survivors} survivors")

    # 5. Benchmark Deep Dive (Check raw data for MSFT or AAPL)
    print("\n--- Benchmark Raw Data Inspection (MSFT) ---")
    benchmark_query = """
        SELECT ticker, roe, profit_margin, debt_to_equity, revenue_growth, 
               current_ratio, composite_score, trailing_pe 
        FROM stock_signals 
        WHERE ticker = 'MSFT'
    """
    df_msft = pd.read_sql_query(benchmark_query, conn)
    if not df_msft.empty:
        print(df_msft.to_string(index=False))
    else:
        print("MSFT not found in database. Is it in your tracked universe?")

    conn.close()
    print("="*50 + "\n")

if __name__ == "__main__":
    run_waterfall_diagnostics()