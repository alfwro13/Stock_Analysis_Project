import sqlite3, pandas as pd
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)

# Filter to ONLY the latest scoring date — the previous query
# was reading historical scores accumulated over multiple runs
df = pd.read_sql_query("""
    SELECT ml_confidence_score
    FROM quant_signals
    WHERE ml_confidence_score IS NOT NULL
      AND date = (SELECT MAX(date) FROM quant_signals WHERE ml_confidence_score IS NOT NULL)
""", conn)
conn.close()

print(f"=== TODAY'S SCORES ONLY (count={len(df)}) ===")
print(df['ml_confidence_score'].describe().round(2))

print("\n=== SCORE BANDS ===")
print(pd.cut(df['ml_confidence_score'],
    bins=[0,25,35,45,55,65,100],
    labels=['0-25','25-35','35-45','45-55','55-65','65-100']
).value_counts().sort_index())