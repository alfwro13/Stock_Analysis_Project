import sqlite3, pandas as pd
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)

# How many distinct dates have scores, and how many tickers per date?
diag = pd.read_sql_query("""
    SELECT date, COUNT(*) AS n_tickers
    FROM quant_signals
    WHERE ml_confidence_score IS NOT NULL
    GROUP BY date
    ORDER BY date DESC
    LIMIT 10
""", conn)
print("=== TICKER COUNT PER SCORING DATE (top 10 most recent) ===")
print(diag.to_string(index=False))

# Show today's max date explicitly
maxd = pd.read_sql_query("""
    SELECT MAX(date) AS max_date FROM quant_signals WHERE ml_confidence_score IS NOT NULL
""", conn).iloc[0]['max_date']
print(f"\nMAX(date) returned: {maxd}")

conn.close()