import sqlite3, pandas as pd
from config import DB_PATH
from ai_prediction_engine import update_daily_ml_predictions

# 1. Wipe scores for today so we start clean
conn = sqlite3.connect(DB_PATH)
conn.execute("""
    UPDATE quant_signals
    SET ml_confidence_score = NULL
    WHERE date = (SELECT MAX(date) FROM quant_signals WHERE ml_confidence_score IS NOT NULL)
""")
conn.commit()
conn.close()
print("Cleared all scores for today's date.")

# 2. Re-run inference using the current production model
conn = sqlite3.connect(DB_PATH)
all_tickers = pd.read_sql_query(
    "SELECT DISTINCT ticker FROM quant_signals", conn
)['ticker'].tolist()
conn.close()
print(f"Running inference on {len(all_tickers)} unique tickers from the DB...")
update_daily_ml_predictions(all_tickers)

# 3. Read the fresh scores — guaranteed to come from the current model only
conn = sqlite3.connect(DB_PATH)
df = pd.read_sql_query("""
    SELECT ml_confidence_score
    FROM quant_signals
    WHERE ml_confidence_score IS NOT NULL
      AND date = (SELECT MAX(date) FROM quant_signals WHERE ml_confidence_score IS NOT NULL)
""", conn)
conn.close()

print(f"\n=== CLEAN HOLDOUT MODEL — TODAY'S SCORES (count={len(df)}) ===")
print(df['ml_confidence_score'].describe().round(2))

print("\n=== SCORE BANDS ===")
print(pd.cut(df['ml_confidence_score'],
    bins=[0,25,35,45,55,65,100],
    labels=['0-25','25-35','35-45','45-55','55-65','65-100']
).value_counts().sort_index())