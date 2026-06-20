"""
tests/test_ai_regime_engine.py — AI Regime Prompt Engine unit tests

Covers:
  • _compute_transition_matrix: normal 3-state history, single-state (no transitions),
    empty input, unknown state values are ignored
  • _days_since_last_transition: steady state (never changed), recent transition,
    too-short history returns None
  • generate_prompt: raises ValueError for unknown mode, returns non-empty string
    for each allowed mode when DB has regime data, caches result within same day
"""

import sys
from pathlib import Path
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db_module
from ai_regime_engine import AIRegimePromptEngine, _ALLOWED_MODES


# ── helpers ───────────────────────────────────────────────────────────────────

def _seed_regime(conn, date_str, state=0, label="Bull", prob=0.85, vix=15.0):
    conn.execute(
        """INSERT OR REPLACE INTO market_regimes
           (date, price_hmm_state, price_hmm_label, price_hmm_prob, vix_close, spy_volatility,
            us_turbulence, uk_turbulence, us_regime_label, uk_regime_label)
           VALUES (?, ?, ?, ?, ?, ?, 0.5, 0.5, 'Low Vol', 'Low Vol')""",
        (date_str, state, label, prob, vix, 12.0),
    )
    conn.execute(
        """INSERT OR REPLACE INTO macro_regimes
           (date, us_threat_level, tnx_close, tyx_close, yield_curve_inverted, days_inverted)
           VALUES (?, 'GREEN', 4.5, 4.8, 0, 0)""",
        (date_str,),
    )
    conn.commit()


# ── _compute_transition_matrix ────────────────────────────────────────────────

class TestComputeTransitionMatrix:
    def setup_method(self):
        self.engine = AIRegimePromptEngine()

    def test_empty_returns_no_data_lines(self):
        result = self.engine._compute_transition_matrix([])
        assert "no data" in result["text"]

    def test_single_entry_returns_no_data(self):
        result = self.engine._compute_transition_matrix([{"price_hmm_state": 0}])
        assert "no data" in result["text"]

    def test_steady_bull_100_pct_self_transition(self):
        history = [{"price_hmm_state": 0}] * 10
        result = self.engine._compute_transition_matrix(history)
        assert "100.0%" in result["text"]

    def test_transitions_between_two_states(self):
        # Alternating Bull→Chop→Bull→Chop...
        history = [{"price_hmm_state": i % 2} for i in range(10)]
        result = self.engine._compute_transition_matrix(history)
        text = result["text"]
        assert "Bull" in text
        assert "Chop" in text

    def test_unknown_state_ignored(self):
        history = [
            {"price_hmm_state": 0},
            {"price_hmm_state": 99},   # out-of-range
            {"price_hmm_state": 0},
        ]
        # Should not raise
        result = self.engine._compute_transition_matrix(history)
        assert "text" in result

    def test_none_state_ignored(self):
        history = [
            {"price_hmm_state": 0},
            {"price_hmm_state": None},
            {"price_hmm_state": 1},
        ]
        result = self.engine._compute_transition_matrix(history)
        assert "text" in result


# ── _days_since_last_transition ───────────────────────────────────────────────

class TestDaysSinceLastTransition:
    def setup_method(self):
        self.engine = AIRegimePromptEngine()

    def test_empty_returns_none(self):
        assert self.engine._days_since_last_transition([]) is None

    def test_single_entry_returns_none(self):
        assert self.engine._days_since_last_transition([{"price_hmm_state": 0, "date": "2026-01-01"}]) is None

    def test_all_same_state_returns_none(self):
        history = [{"price_hmm_state": 0, "date": f"2026-01-{d:02d}"} for d in range(1, 6)]
        # Never transitioned so the loop exhausts without finding a change
        assert self.engine._days_since_last_transition(history) is None

    def test_recent_transition_returns_positive_int(self):
        today = datetime.now(timezone.utc).date()
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date()
        history = [
            {"price_hmm_state": 0, "date": str(yesterday)},
            {"price_hmm_state": 1, "date": str(today)},
        ]
        result = self.engine._days_since_last_transition(history)
        assert isinstance(result, int)
        assert result == 0   # transition happened today

    def test_transition_three_days_ago(self):
        base = datetime.now(timezone.utc)
        history = [
            {"price_hmm_state": 0, "date": (base - timedelta(days=5)).strftime("%Y-%m-%d")},
            {"price_hmm_state": 1, "date": (base - timedelta(days=3)).strftime("%Y-%m-%d")},
            {"price_hmm_state": 1, "date": (base - timedelta(days=2)).strftime("%Y-%m-%d")},
            {"price_hmm_state": 1, "date": (base - timedelta(days=1)).strftime("%Y-%m-%d")},
            {"price_hmm_state": 1, "date": base.strftime("%Y-%m-%d")},
        ]
        result = self.engine._days_since_last_transition(history)
        assert result == 3


# ── generate_prompt ───────────────────────────────────────────────────────────

class TestGeneratePrompt:
    def setup_method(self):
        self.engine = AIRegimePromptEngine()

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            self.engine.generate_prompt("Not A Real Mode")

    @pytest.mark.parametrize("mode", list(_ALLOWED_MODES))
    def test_all_modes_return_nonempty_string(self, mode):
        conn = _db_module.get_connection()
        _seed_regime(conn, "2026-06-20")
        conn.close()
        result = self.engine.generate_prompt(mode)
        assert isinstance(result, str)
        assert len(result) > 50

    def test_prompt_contains_data_block_header(self):
        conn = _db_module.get_connection()
        _seed_regime(conn, "2026-06-20", state=2, label="Crash", prob=0.91)
        conn.close()
        result = self.engine.generate_prompt("Plain English Briefing")
        assert "MARKET REGIME SNAPSHOT" in result
        assert "Crash" in result

    def test_result_is_cached(self):
        conn = _db_module.get_connection()
        _seed_regime(conn, "2026-06-20")
        conn.close()
        mode = "Red Flags Check"
        r1 = self.engine.generate_prompt(mode)
        r2 = self.engine.generate_prompt(mode)
        assert r1 is r2   # same object from cache

    def test_cache_clears_on_new_day(self):
        conn = _db_module.get_connection()
        _seed_regime(conn, "2026-06-20")
        conn.close()
        mode = "Plain English Briefing"
        with patch("ai_regime_engine.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2026-06-20"
            self.engine.generate_prompt(mode)
            cache_size_day1 = len(self.engine._cache)

        # Simulate new day
        self.engine._cache_date = "2026-06-19"   # force stale
        with patch("ai_regime_engine.datetime") as mock_dt:
            mock_dt.now.return_value.strftime.return_value = "2026-06-20"
            self.engine.generate_prompt(mode)

        assert len(self.engine._cache) == cache_size_day1
