import sqlite3
import pandas as pd
from config import DB_PATH

conn = sqlite3.connect(DB_PATH)

df = pd.read_sql_query("""
    SELECT ticker, trailing_pe, price_to_book, profit_margin,
           roe, revenue_growth, debt_to_equity
    FROM stock_signals
    WHERE trailing_pe IS NOT NULL
       OR roe IS NOT NULL
""", conn)
conn.close()

print("=== ROW COUNT ===")
print(f"Total tickers with fundamentals: {len(df)}")

print("\n=== RAW VALUE DISTRIBUTIONS ===")
cols = ['trailing_pe', 'price_to_book', 'profit_margin',
        'roe', 'revenue_growth', 'debt_to_equity']

for col in cols:
    s = df[col].dropna()
    print(f"\n{col} (n={len(s):,})")
    print(f"  min={s.min():.4f}  mean={s.mean():.4f}  "
          f"max={s.max():.4f}  std={s.std():.4f}")
    print(f"  p5={s.quantile(0.05):.4f}  p25={s.quantile(0.25):.4f}  "
          f"p50={s.median():.4f}  p75={s.quantile(0.75):.4f}  "
          f"p95={s.quantile(0.95):.4f}")

print("\n=== WINSORIZATION IMPACT ===")
bounds = {
    'trailing_pe':    (0.0,  100.0),
    'price_to_book':  (0.0,  20.0),
    'profit_margin':  (-1.0, 1.0),
    'roe':            (-1.0, 1.5),
    'revenue_growth': (-1.0, 3.0),
    'debt_to_equity': (0.0,  500.0),
}

for col, (lo, hi) in bounds.items():
    s = df[col].dropna()
    n_below = (s < lo).sum()
    n_above = (s > hi).sum()
    pct = (n_below + n_above) / len(s) * 100 if len(s) > 0 else 0
    print(f"{col:20s}  clipped_below={n_below:4d}  "
          f"clipped_above={n_above:4d}  "
          f"total_clipped={pct:.1f}%")

print("\n=== SCALE CHECK — KNOWN REFERENCE VALUES ===")
known = ['AAPL', 'MSFT', 'NVDA', 'STX', 'JPM']
ref = df[df['ticker'].isin(known)][
    ['ticker', 'trailing_pe', 'price_to_book',
     'profit_margin', 'roe', 'debt_to_equity']
].set_index('ticker')
print(ref.to_string())
print("\nExpected scales:")
print("  trailing_pe:   25–150 (raw ratio)  e.g. AAPL ~32, NVDA ~40")
print("  price_to_book: 1–60   (raw ratio)  e.g. AAPL ~50, MSFT ~12")
print("  profit_margin: 0.10–0.60 (decimal) e.g. AAPL 0.26, NVDA 0.56")
print("  roe:           0.10–20  (decimal)  e.g. AAPL 1.60, NVDA 1.20")
print("  debt_to_equity:0–300   (pct-like)  e.g. AAPL ~150, MSFT ~30")