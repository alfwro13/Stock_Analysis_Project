import sqlite3
import pandas as pd
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)
df = pd.read_sql_query("""
    SELECT ml_confidence_score
    FROM quant_signals
    WHERE ml_confidence_score IS NOT NULL
""", conn)
conn.close()

print("=== DISTRIBUTION SUMMARY ===")
print(df['ml_confidence_score'].describe().round(2))

print("\n=== SCORE BANDS ===")
bands = pd.cut(
    df['ml_confidence_score'],
    bins=[0, 25, 35, 45, 55, 65, 100],
    labels=['0-25 (Bearish)', '25-35 (Cautious)', '35-45 (Neutral)',
            '45-55 (Bullish)', '55-65 (Strong)', '65-100 (High Conviction)']
)
print(bands.value_counts().sort_index())

print("\n=== TOP 10 HIGHEST CONVICTION SETUPS TODAY ===")
conn = sqlite3.connect(DB_PATH)
top10 = pd.read_sql_query("""
    SELECT qs.ticker, qs.date, qs.close_price,
           qs.ml_confidence_score,
           qs.rsi_14, qs.mom_3m, qs.mom_6m,
           qs.rel_strength_20d,
           ss.trailing_pe, ss.roe, ss.profit_margin,
           tm.sector
    FROM quant_signals qs
    LEFT JOIN stock_signals ss   ON qs.ticker = ss.ticker
    LEFT JOIN ticker_metadata tm ON qs.ticker = tm.ticker
    WHERE qs.ml_confidence_score IS NOT NULL
      AND qs.date = (
          SELECT MAX(date) FROM quant_signals
          WHERE rel_strength_5d IS NOT NULL
      )
    ORDER BY qs.ml_confidence_score DESC
    LIMIT 10
""", conn)
conn.close()
print(top10.to_string(index=False))

print("\n=== PROGRESS SCORECARD ===")
print(f"Count scored:  {len(df)}")
print(f"Mean:          {df['ml_confidence_score'].mean():.2f}")
print(f"Std:           {df['ml_confidence_score'].std():.2f}")
print(f"Max:           {df['ml_confidence_score'].max():.2f}")