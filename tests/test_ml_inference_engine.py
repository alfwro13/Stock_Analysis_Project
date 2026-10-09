"""
tests/test_ml_inference_engine.py — Daily ML Inference unit tests

Covers:
  • update_daily_ml_predictions mirrors ml_confidence_score onto stock_signals.ml_confidence
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db_module
from ml_features import LATEST_FEATURES_QUERY


@pytest.fixture(autouse=True)
def _remove_seeded_universe():
    yield
    conn = None
    try:
        conn = _db_module.get_connection()
        conn.execute("DELETE FROM quant_signals WHERE ticker LIKE 'MLU%'")
        conn.execute("DELETE FROM stock_signals WHERE ticker LIKE 'MLU%'")
        conn.commit()
    finally:
        if conn:
            conn.close()


class TestUpdateDailyMlPredictionsSyncsStockSignals:
    """stock_signals.ml_confidence mirrors quant_signals.ml_confidence_score so consumers that
    join off stock_signals (the Regime-Weighted Conviction Score) don't need a second
    quant_signals subselect. Was previously dead — no write site anywhere — until this."""

    @staticmethod
    def _seed_universe(n=30, date_str="2026-01-01"):
        conn = None
        try:
            conn = _db_module.get_connection()
            rng = np.random.default_rng(3)
            for i in range(n):
                ticker = f"MLU{i:02d}"
                conn.execute("INSERT OR REPLACE INTO stock_signals (ticker) VALUES (?)", (ticker,))
                conn.execute(
                    """INSERT OR REPLACE INTO quant_signals
                       (ticker, date, close_price, volume, rsi_14, macd, macd_signal, macd_hist,
                        sma_50, sma_200, volume_surge, bullish_cross,
                        mom_1m, mom_3m, mom_6m, mom_12m_skip1m, atr_pct, hist_vol_20,
                        rel_strength_5d, rel_strength_20d)
                       VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        ticker, date_str,
                        100.0 + rng.normal(0, 5), 1_000_000 + rng.normal(0, 1000),
                        50.0 + rng.normal(0, 5), rng.normal(0, 1), rng.normal(0, 1), rng.normal(0, 1),
                        95.0 + rng.normal(0, 5), 90.0 + rng.normal(0, 5), 0, 0,
                        rng.normal(0, 0.05), rng.normal(0, 0.1), rng.normal(0, 0.15), rng.normal(0, 0.2),
                        0.02 + abs(rng.normal(0, 0.005)), 0.2 + abs(rng.normal(0, 0.05)),
                        rng.normal(0, 0.02), rng.normal(0, 0.03),
                    ),
                )
            conn.commit()
        finally:
            if conn:
                conn.close()
        return [f"MLU{i:02d}" for i in range(n)]

    def test_ml_confidence_synced_to_stock_signals(self, fake_inference_df):
        tickers = self._seed_universe()
        target = tickers[:2]
        fake_df = fake_inference_df(tickers)

        fake_model = MagicMock()
        fake_model.predict_proba.return_value = np.array([[0.2, 0.8]])

        from ml_inference_engine import update_daily_ml_predictions
        with patch("ml_inference_engine.MODEL_PATH") as mock_path, \
             patch("ml_inference_engine.FEATURE_STATS_PATH") as mock_stats_path, \
             patch("ml_inference_engine.joblib.load", return_value=fake_model), \
             patch("ml_inference_engine.pd.read_sql_query", return_value=fake_df):
            mock_path.exists.return_value = True
            mock_stats_path.exists.return_value = False
            update_daily_ml_predictions(target)

        conn = None
        try:
            conn = _db_module.get_connection()
            for ticker in target:
                qs_row = conn.execute(
                    "SELECT ml_confidence_score FROM quant_signals WHERE ticker = ? AND date = '2026-01-01'",
                    (ticker,),
                ).fetchone()
                ss_row = conn.execute(
                    "SELECT ml_confidence FROM stock_signals WHERE ticker = ?", (ticker,)
                ).fetchone()
                assert qs_row["ml_confidence_score"] == 80.0
                assert ss_row["ml_confidence"] == 80.0
        finally:
            conn.close()


def test_latest_features_query_returns_only_each_tickers_newest_complete_row():
    seed = TestUpdateDailyMlPredictionsSyncsStockSignals._seed_universe
    seed(2, "2026-01-01")
    seed(2, "2026-01-05")
    conn = None
    try:
        conn = _db_module.get_connection()
        rows = [r for r in conn.execute(LATEST_FEATURES_QUERY).fetchall() if r["ticker"].startswith("MLU")]
    finally:
        if conn:
            conn.close()
    assert sorted((r["ticker"], r["date"]) for r in rows) == [("MLU00", "2026-01-05"), ("MLU01", "2026-01-05")]
