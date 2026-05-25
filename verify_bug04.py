import sqlite3, pandas as pd
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
df = pd.read_sql_query("""
    SELECT ml_confidence_score FROM quant_signals
    WHERE ml_confidence_score IS NOT NULL
""", conn)
conn.close()

print(df['ml_confidence_score'].describe().round(2))

# Key check: are the probabilities better spread across [0,1]?
# A well-calibrated model should have scores across 0-100,
# not clustered in a narrow band
print(pd.cut(df['ml_confidence_score'],
    bins=[0,25,35,45,55,65,100],
    labels=['0-25','25-35','35-45','45-55','55-65','65-100']
).value_counts().sort_index())