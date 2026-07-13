"""
tests/test_macro_allocator_engine.py — MACRO ALLOCATOR ENGINE

Covers:
 - get_ideal_allocation  : midpoint calculation, unknown-regime fallback
 - score_portfolio_alignment : cosine similarity correctness, edge cases
 - get_rebalance_deltas  : sign and magnitude correctness
 - get_regime_history    : returns list; empty on fresh DB
 - get_macro_allocation_data : no-data path, status field
"""

import pytest
import database as _db_module
from macro_allocator_engine import (
    get_ideal_allocation,
    score_portfolio_alignment,
    get_rebalance_deltas,
    get_regime_history,
    get_macro_allocation_data,
    _get_portfolio_asset_class_weights,
)

pytestmark = pytest.mark.regime


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _seed_regime_label(label: str, date: str = "2025-06-01") -> None:
    conn = _db_module.get_connection()
    try:
        conn.execute("DELETE FROM macro_regimes WHERE date=?", (date,))
        conn.execute(
            "INSERT INTO macro_regimes "
            "(date, tnx_close, us_threat_level, uk_threat_level, regime_label) "
            "VALUES (?, 4.2, 'GREEN', 'GREEN', ?)",
            (date, label),
        )
        conn.commit()
    finally:
        conn.close()


def _clear_regime_history() -> None:
    conn = _db_module.get_connection()
    try:
        conn.execute("DELETE FROM macro_regimes")
        conn.commit()
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# 1. get_ideal_allocation
# ──────────────────────────────────────────────────────────────────────────────

class TestGetIdealAllocation:

    def test_risk_on_returns_midpoints(self):
        result = get_ideal_allocation("Risk-On")
        # Risk-On equities range [65, 80] → midpoint 72.5
        assert result["equities"] == pytest.approx(72.5, abs=0.1)

    def test_contraction_returns_midpoints(self):
        result = get_ideal_allocation("Contraction")
        # Contraction bonds range [40, 55] → midpoint 47.5
        assert result["bonds"] == pytest.approx(47.5, abs=0.1)

    def test_all_five_regimes_have_four_asset_classes(self):
        for label in ("Risk-On", "Late Cycle", "Stagflation", "Contraction", "Recovery"):
            result = get_ideal_allocation(label)
            assert set(result.keys()) == {"equities", "bonds", "commodities", "cash"}, \
                f"Missing asset classes for {label}"

    def test_unknown_regime_returns_fallback(self):
        result = get_ideal_allocation("Unknown Regime XYZ")
        # Must not raise; returns a non-empty dict with the four keys
        assert set(result.keys()) == {"equities", "bonds", "commodities", "cash"}
        assert result["equities"] > 0


# ──────────────────────────────────────────────────────────────────────────────
# 2. score_portfolio_alignment
# ──────────────────────────────────────────────────────────────────────────────

class TestScorePortfolioAlignment:

    def test_identical_allocation_scores_100(self):
        weights = {"equities": 65.0, "bonds": 20.0, "commodities": 5.0, "cash": 10.0}
        assert score_portfolio_alignment(weights, weights) == 100

    def test_opposite_allocation_scores_low(self):
        ideal   = {"equities": 70.0, "bonds": 20.0, "commodities": 5.0, "cash":  5.0}
        current = {"equities":  0.0, "bonds":  0.0, "commodities": 0.0, "cash": 100.0}
        score = score_portfolio_alignment(current, ideal)
        assert score < 50

    def test_score_is_bounded_0_to_100(self):
        ideal   = {"equities": 60.0, "bonds": 20.0, "commodities": 10.0, "cash": 10.0}
        current = {"equities": 40.0, "bonds": 30.0, "commodities": 15.0, "cash": 15.0}
        score = score_portfolio_alignment(current, ideal)
        assert 0 <= score <= 100

    def test_zero_current_allocation_returns_0(self):
        ideal   = {"equities": 70.0, "bonds": 20.0, "commodities": 5.0, "cash": 5.0}
        current = {"equities":  0.0, "bonds":  0.0, "commodities": 0.0, "cash": 0.0}
        assert score_portfolio_alignment(current, ideal) == 0

    def test_close_allocation_scores_high(self):
        ideal   = {"equities": 70.0, "bonds": 20.0, "commodities": 5.0, "cash":  5.0}
        current = {"equities": 68.0, "bonds": 22.0, "commodities": 5.0, "cash":  5.0}
        assert score_portfolio_alignment(current, ideal) >= 99


# ──────────────────────────────────────────────────────────────────────────────
# 3. get_rebalance_deltas
# ──────────────────────────────────────────────────────────────────────────────

class TestGetRebalanceDeltas:

    def test_underweight_equity_gives_positive_delta(self):
        ideal   = {"equities": 70.0, "bonds": 20.0, "commodities": 5.0, "cash": 5.0}
        current = {"equities": 50.0, "bonds": 30.0, "commodities": 5.0, "cash": 15.0}
        deltas = get_rebalance_deltas(current, ideal)
        assert deltas["equities"] > 0   # need to add equities
        assert deltas["bonds"] < 0      # need to reduce bonds

    def test_perfect_match_produces_zero_deltas(self):
        weights = {"equities": 65.0, "bonds": 20.0, "commodities": 5.0, "cash": 10.0}
        deltas = get_rebalance_deltas(weights, weights)
        for v in deltas.values():
            assert abs(v) < 0.01

    def test_delta_keys_match_ideal_keys(self):
        ideal   = {"equities": 70.0, "bonds": 20.0, "commodities": 5.0, "cash": 5.0}
        current = {"equities": 60.0, "bonds": 25.0, "commodities": 5.0, "cash": 10.0}
        deltas = get_rebalance_deltas(current, ideal)
        assert set(deltas.keys()) == set(ideal.keys())


# ──────────────────────────────────────────────────────────────────────────────
# 4. get_regime_history
# ──────────────────────────────────────────────────────────────────────────────

class TestGetRegimeHistory:

    def test_returns_list(self):
        result = get_regime_history(days=90)
        assert isinstance(result, list)

    def test_empty_on_fresh_db(self):
        _clear_regime_history()
        assert get_regime_history(days=90) == []

    def test_returns_expected_fields(self):
        _seed_regime_label("Risk-On", "2025-06-01")
        history = get_regime_history(days=90)
        assert len(history) >= 1
        assert "date" in history[0]
        assert "regime_label" in history[0]

    def test_rows_with_null_label_excluded(self):
        """Rows without a regime_label (before first classify run) must not appear."""
        conn = _db_module.get_connection()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO macro_regimes "
                "(date, tnx_close, us_threat_level, uk_threat_level) "
                "VALUES ('2025-05-01', 4.0, 'GREEN', 'GREEN')"
            )
            conn.commit()
        finally:
            conn.close()
        history = get_regime_history(days=90)
        dates = [r["date"] for r in history]
        assert "2025-05-01" not in dates


# ──────────────────────────────────────────────────────────────────────────────
# 5. get_macro_allocation_data — top-level aggregator
# ──────────────────────────────────────────────────────────────────────────────

class TestGetMacroAllocationData:

    def test_no_data_returns_no_data_status(self):
        _clear_regime_history()
        conn = _db_module.get_connection()
        try:
            conn.execute("DELETE FROM macro_indicators")
            conn.commit()
        finally:
            conn.close()
        result = get_macro_allocation_data()
        assert result["status"] == "no_data"

    def test_with_seeded_regime_returns_ok_status(self):
        _seed_regime_label("Recovery", "2025-06-10")
        result = get_macro_allocation_data()
        assert result["status"] == "ok"

    def test_ideal_allocation_present_in_response(self):
        _seed_regime_label("Risk-On", "2025-06-10")
        result = get_macro_allocation_data()
        assert "ideal_allocation" in result
        assert set(result["ideal_allocation"].keys()) == {"equities", "bonds", "commodities", "cash"}

    def test_regime_history_present_in_response(self):
        _seed_regime_label("Contraction", "2025-06-10")
        result = get_macro_allocation_data()
        assert "regime_history" in result
        assert isinstance(result["regime_history"], list)

    def test_no_ghostfolio_and_no_builtin_holdings_returns_null_alignment(self):
        """Without Ghostfolio and with no built-in Trading holdings either, current_allocation
        and alignment_score must be None (the genuine "nothing to compute" case)."""
        from unittest.mock import patch
        _seed_regime_label("Late Cycle", "2025-06-10")
        with patch("macro_allocator_engine.GHOSTFOLIO_URL", ""), \
             patch("macro_allocator_engine.GHOSTFOLIO_TOKEN", ""), \
             patch("macro_allocator_engine._builtin_account_holdings", return_value=[]):
            result = get_macro_allocation_data()
        assert result["current_allocation"] is None
        assert result["alignment_score"] is None


class TestGetPortfolioAssetClassWeightsBuiltinFallback:
    """Without Ghostfolio configured, Alignment Score must fall back to built-in Trading account
    holdings instead of permanently showing a Ghostfolio-configuration message (AGENTS.md rule 14:
    built-in Accounts is the primary portfolio source, Ghostfolio is opt-in only)."""

    @staticmethod
    def _seed_stock_signal(ticker, price, currency="GBP"):
        conn = _db_module.get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency) VALUES (?, ?, ?)",
            (ticker, price, currency),
        )
        conn.commit()
        conn.close()

    @staticmethod
    def _seed_asset_profile(ticker, sector, country, quote_type="EQUITY"):
        conn = _db_module.get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO asset_profiles (ticker, sector, country, quote_type) VALUES (?, ?, ?, ?)",
            (ticker, sector, country, quote_type),
        )
        conn.commit()
        conn.close()

    def test_falls_back_to_builtin_holdings_when_ghostfolio_unconfigured(self):
        from unittest.mock import patch
        from database import create_account, add_transaction

        ticker = "MACROALLOC_T1"
        self._seed_stock_signal(ticker, 100.0, "GBP")
        self._seed_asset_profile(ticker, "Technology", "United States", "EQUITY")
        aid = create_account("MacroAllocFallbackAcc", "GBP")
        add_transaction(aid, "Buy", "2026-01-05", ticker=ticker, currency="GBP",
                         quantity=10, unit_price=80, exchange_rate=1.0)

        _seed_regime_label("Risk-On", "2025-06-10")
        with patch("macro_allocator_engine.GHOSTFOLIO_URL", ""), \
             patch("macro_allocator_engine.GHOSTFOLIO_TOKEN", ""):
            result = get_macro_allocation_data()

        assert result["current_allocation"] is not None
        assert result["alignment_score"] is not None
        assert result["current_allocation"]["equities"] > 0
        assert "portfolio_note" not in result

    def test_treasury_bill_holding_counts_as_cash_not_equities(self):
        """Isolated from the shared session DB via a mocked holdings list — _builtin_account_holdings(None)
        has no per-account scoping, so a DB-seeded version of this test would pick up Trading holdings
        left behind by other tests in the same session."""
        from unittest.mock import patch

        cash_holding = [{
            "symbol": "TBILL-999", "asset_class": "CASH", "asset_sub_class": "", "value": 1000.0,
        }]
        with patch("macro_allocator_engine.GHOSTFOLIO_URL", ""), \
             patch("macro_allocator_engine.GHOSTFOLIO_TOKEN", ""), \
             patch("macro_allocator_engine._builtin_account_holdings", return_value=cash_holding):
            weights, error = _get_portfolio_asset_class_weights()

        assert error is None
        assert weights["equities"] == 0.0
        assert weights["cash"] == 100.0
