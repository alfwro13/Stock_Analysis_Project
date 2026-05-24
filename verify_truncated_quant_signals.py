import sqlite3
import pandas as pd
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
df = pd.read_sql_query("""
    SELECT date,
           close_price,
           LAG(close_price, -5) OVER (PARTITION BY ticker ORDER BY date) AS future_close,
           LAG(close_price, -1) OVER (PARTITION BY ticker ORDER BY date) AS next_close
    FROM quant_signals
    WHERE close_price IS NOT NULL
""", conn)
conn.close()

df = df.dropna()
df['target'] = ((df['future_close'] - df['next_close']) / df['next_close'] > 0.03).astype(int)
print(df['target'].value_counts(normalize=True).round(3))