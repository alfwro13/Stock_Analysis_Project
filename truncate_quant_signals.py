import sqlite3
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

cursor.execute("DELETE FROM quant_signals;")
cursor.execute("""
    UPDATE stock_signals
    SET ml_confidence = NULL,
        score_method  = 'HARDCODED'
    WHERE ml_confidence IS NOT NULL;
""")

# Commit and close the transaction BEFORE calling VACUUM.
# SQLite requires VACUUM to run in autocommit mode (no open transaction).
conn.commit()
conn.close()

# Re-open a fresh connection with no active transaction, then VACUUM.
conn = sqlite3.connect(DB_PATH, isolation_level=None)
conn.execute("VACUUM;")
conn.close()

print("Done. quant_signals cleared, stock_signals ML columns reset, disk space reclaimed.")