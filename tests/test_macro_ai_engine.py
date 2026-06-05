"""
tests/test_macro_ai_engine.py  ── MACRO AI ENGINE

Tests focused on the parts of MacroAIEngine that are deterministic and
do not require a trained ML model:

  _extract_numeric()          — pure string→float conversion
  _remap_hmm_states()         — state index permutation
  _log_training_score()       — DB write
  _ensure_training_log_table()— DB schema
  training guards             — early-return paths when data is insufficient
  run_macro_inference()       — no-events and untrained-model paths
"""

import sys
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from macro_ai_engine import MacroAIEngine


# ──────────────────────────────────────────────────────────────────────────────
# Fixture: in-memory engine (no real DB I/O, no ML libraries called)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def engine():
    """MacroAIEngine backed by an in-memory SQLite — no network, no disk."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    # Minimal schema so _ensure_training_log_table and queries don't error
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS market_regimes (
            date TEXT PRIMARY KEY, vix_close REAL, ai_hmm_state INTEGER
        );
        CREATE TABLE IF NOT EXISTS macro_indicators (
            date TEXT PRIMARY KEY,
            us_m2 REAL, us_jobless_claims REAL,
            us_high_yield_spread REAL, us_yield_curve REAL
        );
        CREATE TABLE IF NOT EXISTS macro_calendar (
            event_id INTEGER PRIMARY KEY,
            event_date TEXT,
            forecast_val TEXT,
            previous_val TEXT,
            actual_val TEXT,
            post_event_spy_gap REAL,
            is_event_passed INTEGER DEFAULT 0,
            ai_volatility_warning REAL,
            ai_consensus_miss_prob REAL
        );
    """)
    conn.commit()

    with patch("macro_ai_engine.get_connection", return_value=conn):
        eng = MacroAIEngine()
    eng.conn = conn  # ensure fixture connection is used
    yield eng
    conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# 1. _extract_numeric()
# ──────────────────────────────────────────────────────────────────────────────

class TestExtractNumeric:

    @pytest.mark.parametrize("val, expected", [
        ("5.0%",    5.0),
        ("-1.2",   -1.2),
        ("1,234.5", 1234.5),
        ("1.2e3",   1200.0),
        ("$3.50",   3.50),
        ("2.5K",    2.5),
        ("0",       0.0),
        (".75",     0.75),
    ])
    def test_valid_inputs(self, engine, val, expected):
        assert engine._extract_numeric(val) == pytest.approx(expected)

    @pytest.mark.parametrize("val", [None, "", "   ", "N/A", "TBD"])
    def test_invalid_inputs_return_nan(self, engine, val):
        import pandas as pd
        result = engine._extract_numeric(val)
        assert pd.isna(result), f"Expected NaN for {val!r}, got {result}"

    def test_nan_scalar_returns_nan(self, engine):
        import pandas as pd
        result = engine._extract_numeric(float('nan'))
        assert pd.isna(result)


# ──────────────────────────────────────────────────────────────────────────────
# 2. _remap_hmm_states()
# ──────────────────────────────────────────────────────────────────────────────

class TestRemapHmmStates:

    def test_identity_when_no_order_set(self, engine):
        """Falls back to identity mapping when hmm_state_order is None."""
        engine.hmm_state_order = None
        raw = np.array([0, 1, 2, 0, 2])
        result = engine._remap_hmm_states(raw)
        np.testing.assert_array_equal(result, raw)

    def test_remaps_correctly(self, engine):
        """
        If raw state 2 is expansion (lowest spread) and raw state 0 is recession,
        argsort gives order [2, 1, 0] → canonical 0 maps to raw 2, canonical 2 maps to raw 0.
        """
        # Simulate: argsort of means column gives [2, 1, 0]
        # i.e. raw state 2 has lowest spread (→ canonical 0 = expansion)
        engine.hmm_state_order = np.array([2, 1, 0])
        raw = np.array([0, 1, 2])
        result = engine._remap_hmm_states(raw)
        # inv[hmm_state_order] = [0,1,2]  →  inv[2]=0, inv[1]=1, inv[0]=2
        # result[0] = inv[0] = 2  (raw 0 = recession → canonical 2)
        # result[1] = inv[1] = 1  (raw 1 = neutral   → canonical 1)
        # result[2] = inv[2] = 0  (raw 2 = expansion → canonical 0)
        np.testing.assert_array_equal(result, [2, 1, 0])

    def test_identity_order_is_no_op(self, engine):
        """If hmm_state_order is identity [0,1,2], remapping is a no-op."""
        engine.hmm_state_order = np.array([0, 1, 2])
        raw = np.array([0, 1, 2, 1, 0])
        result = engine._remap_hmm_states(raw)
        np.testing.assert_array_equal(result, raw)


# ──────────────────────────────────────────────────────────────────────────────
# 3. _ensure_training_log_table() + _log_training_score()
# ──────────────────────────────────────────────────────────────────────────────

class TestTrainingLog:

    def test_table_created(self, engine):
        """model_training_log table must exist after __init__."""
        row = engine.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='model_training_log'"
        ).fetchone()
        assert row is not None

    def test_log_training_score_inserts_row(self, engine):
        """_log_training_score() writes one row per call."""
        engine._log_training_score("test_model", 50, 0.85, 0.03, "accuracy")
        row = engine.conn.execute(
            "SELECT * FROM model_training_log WHERE model_name='test_model'"
        ).fetchone()
        assert row is not None
        assert row["n_samples"] == 50
        assert row["cv_score_mean"] == pytest.approx(0.85)
        assert row["cv_score_std"] == pytest.approx(0.03)
        assert row["score_metric"] == "accuracy"

    def test_log_training_score_none_std(self, engine):
        """cv_score_std may be None (HMM uses log-likelihood, no std)."""
        engine._log_training_score("hmm_regime", 100, -1.23, None, "log_likelihood_per_sample")
        row = engine.conn.execute(
            "SELECT cv_score_std FROM model_training_log WHERE model_name='hmm_regime'"
        ).fetchone()
        assert row is not None
        assert row["cv_score_std"] is None

    def test_log_training_score_db_error_does_not_raise(self, engine):
        """DB write failure must be swallowed, not propagated."""
        engine.conn.close()  # provoke OperationalError on next write
        engine._log_training_score("x", 1, 0.5, 0.1, "acc")  # must not raise


# ──────────────────────────────────────────────────────────────────────────────
# 4. Training guard — insufficient data early returns
# ──────────────────────────────────────────────────────────────────────────────

class TestTrainingGuards:

    def test_hmm_skips_with_no_data(self, engine):
        """train_regime_clustering() returns without error when macro_indicators is empty."""
        engine.train_regime_clustering()  # empty DB — must not raise
        assert engine.hmm_model is None  # model stays untrained

    def test_rf_skips_with_no_data(self, engine):
        """train_consensus_miss_probability() returns without error when macro_calendar is empty."""
        engine.train_consensus_miss_probability()
        assert engine.rf_model is None

    def test_xgb_skips_with_no_data(self, engine):
        """train_volatility_magnitude() returns without error when no SPY gap rows exist."""
        engine.train_volatility_magnitude()
        assert engine.xgb_model is None


# ──────────────────────────────────────────────────────────────────────────────
# 5. run_macro_inference() guard paths
# ──────────────────────────────────────────────────────────────────────────────

class TestRunMacroInference:

    def test_no_events_returns_early(self, engine):
        """run_macro_inference() exits cleanly when no upcoming events exist."""
        engine.run_macro_inference("2099-01-01")  # far-future date, no events in DB

    def test_untrained_xgb_skips_inference(self, engine):
        """
        When xgb_model is None but events exist, inference is skipped gracefully.
        No rows should be updated.
        """
        engine.conn.execute(
            "INSERT INTO macro_calendar (event_id, event_date, forecast_val, previous_val, is_event_passed) "
            "VALUES (1, '2026-06-05', '4.5', '4.2', 0)"
        )
        engine.conn.commit()
        engine.xgb_model = None
        engine.run_macro_inference("2026-06-05")
        row = engine.conn.execute(
            "SELECT ai_volatility_warning FROM macro_calendar WHERE event_id=1"
        ).fetchone()
        assert row["ai_volatility_warning"] is None
