# clean_db.py
import sqlite3

conn = sqlite3.connect("data/analysis.db")
cursor = conn.cursor()

print("Cleaning corrupted Freetrade tickers...")

# Delete all Freetrade assets to prepare for a clean sync
cursor.execute("DELETE FROM market_universe WHERE is_freetrade = 1")
total = cursor.rowcount

# Aggressively target the malformed suffixes stuck in the quant tables 
bad_patterns = ["%D.DE", "%P.PA", "%B.BR", "%M.MI", "%A.AS", "%L.L"]
for table in ['quant_signals', 'stock_signals']:
    for pattern in bad_patterns:
        cursor.execute(f"DELETE FROM {table} WHERE ticker LIKE ?", (pattern,))
        total += cursor.rowcount

conn.commit()
conn.close()
print(f"Success! Purged {total} corrupted rows from the database.")