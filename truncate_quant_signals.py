import sqlite3
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
cursor = conn.cursor()

# 1. Wipe the entire quant_signals table (all historical feature rows)
cursor.execute("DELETE FROM quant_signals;")

# 2. Reset ML inference columns in stock_signals so no stale
#    confidence scores survive from the old unadjusted model
cursor.execute("""
    UPDATE stock_signals
    SET ml_confidence = NULL,
        score_method  = 'HARDCODED'
    WHERE ml_confidence IS NOT NULL;
""")

# 3. Reclaim the disk space SQLite would otherwise keep reserved
cursor.execute("VACUUM;")

conn.commit()
conn.close()
print("Done. quant_signals cleared, stock_signals ML columns reset.")