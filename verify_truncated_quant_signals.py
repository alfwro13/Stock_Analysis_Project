import sqlite3
import pandas as pd
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)

df = pd.read_sql_query("""
    SELECT ticker, date, close_price
    FROM quant_signals
    WHERE close_price IS NOT NULL
    ORDER BY ticker, date ASC
""", conn)

conn.close()

# Replicate the exact target construction from train_global_ml_model()
df = df.sort_values(['ticker', 'date'])
df['next_close']   = df.groupby('ticker')['close_price'].shift(-1)
df['future_close'] = df.groupby('ticker')['close_price'].shift(-5)

df = df.dropna(subset=['next_close', 'future_close'])

df['target'] = (
    (df['future_close'] - df['next_close']) / df['next_close'] > 0.03
).astype(int)

print(f"Total rows:     {len(df):,}")
print(f"Positive (1):   {(df['target'] == 1).sum():,}")
print(f"Negative (0):   {(df['target'] == 0).sum():,}")
print()
print(df['target'].value_counts(normalize=True).round(3))