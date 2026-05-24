import sqlite3
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# Pull the earliest rows for NVDA — pre-split on adjusted series
# should show prices ~$100 range, NOT ~$800-900 (unadjusted pre-split price)
cursor.execute("""
    SELECT date, close_price, sma_50, sma_200
    FROM quant_signals
    WHERE ticker = 'NVDA'
    ORDER BY date ASC
    LIMIT 5;
""")

for row in cursor.fetchall():
    print(row)

conn.close()