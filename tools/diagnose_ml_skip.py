#!/usr/bin/env python3
"""Diagnose why update_daily_ml_predictions writes zero scores for universe tickers."""
import logging
import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

logging.basicConfig(level=logging.DEBUG, format='%(levelname)s [%(name)s] %(message)s')
logger = logging.getLogger(__name__)

from database import get_connection
from ai_prediction_engine import update_daily_ml_predictions

# Pick one universe-only ticker (UNIVERSE_FUNDAMENTALS) and one overlap (HARDCODED)
conn = get_connection(); conn.row_factory = sqlite3.Row
cur = conn.cursor()
cur.execute("""
    SELECT s.ticker, s.score_method
    FROM stock_signals s
    INNER JOIN market_universe m ON s.ticker = m.ticker
    WHERE m.is_index = 1
    ORDER BY s.score_method DESC, s.ticker
    LIMIT 10
""")
samples = cur.fetchall()
print("\n=== SAMPLE TICKERS ===")
for r in samples:
    print(f"  {r['ticker']:<10} {r['score_method']}")

# Pick one of each score_method
uf_ticker = next((r['ticker'] for r in samples if r['score_method'] == 'UNIVERSE_FUNDAMENTALS'), None)
hc_ticker = next((r['ticker'] for r in samples if r['score_method'] == 'HARDCODED'), None)
print(f"\nTesting: UF={uf_ticker} HC={hc_ticker}")

# Check pre-state on quant_signals for these tickers (latest row)
def show_state(label, ticker):
    if not ticker: return
    cur.execute("""
        SELECT date, ml_confidence_score, rsi_14, macd, sma_50, sma_200, var_95, sentiment_score, close_price
        FROM quant_signals
        WHERE ticker = ?
        ORDER BY date DESC LIMIT 1
    """, (ticker,))
    row = cur.fetchone()
    print(f"\n[{label}] {ticker} latest quant_signals row:")
    if row:
        for k in row.keys():
            print(f"    {k}: {row[k]}")
    else:
        print("    NO ROW")

show_state("BEFORE-UF", uf_ticker)
show_state("BEFORE-HC", hc_ticker)

# Run inference on JUST these two tickers
test_tickers = [t for t in [uf_ticker, hc_ticker] if t]
print(f"\n=== CALLING update_daily_ml_predictions({test_tickers}) ===")
try:
    update_daily_ml_predictions(test_tickers)
    print("[OK] returned without exception")
except Exception as e:
    print(f"[FAIL] raised: {type(e).__name__}: {e}")
    import traceback; traceback.print_exc()

# Check post-state
show_state("AFTER-UF", uf_ticker)
show_state("AFTER-HC", hc_ticker)

# Check what features the inference function expects
print("\n=== INSPECTING update_daily_ml_predictions SOURCE ===")
import inspect
src = inspect.getsource(update_daily_ml_predictions)
print(src[:3000])

conn.close()