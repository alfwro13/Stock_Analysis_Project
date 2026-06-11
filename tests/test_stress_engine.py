"""
tests/test_stress_engine.py — STRESS ENGINE

Covers:
 - _primary_sector       : sector selection from holdings data
 - _get_betas            : DB look-up via xray_risk_cache
 - run_stress_test       : guard paths, core calculation, custom scenario, data warnings
"""

import pytest
from unittest.mock import MagicMock, patch

import database as _db_module
from stress_engine import _primary_sector, _get_betas, run_stress_test, SCENARIOS

pytestmark = pytest.mark.stress


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _make_holding(symbol: str, value: float, sectors=None, name: str = "Co", weight: float = 0.1):
    return {
        "symbol": symbol,
        "name": name,
        "value": value,
        "weight": weight,
        "sectors": sectors or [],
        "asset_class": "Equity",
        "asset_sub_class": "",
    }


def _mock_client(holdings, total_value=10_000.0, is_configured=True, auth_ok=True):
    client = MagicMock()
    client.is_configured = is_configured
    client.authenticate.return_value = auth_ok
    client.get_holdings.return_value = (holdings, total_value)
    return client


def _seed_beta(ticker: str, beta: float) -> None:
    conn = _db_module.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO xray_risk_cache "
            "(ticker, benchmark, last_updated, beta, annualized_vol) VALUES (?, 'SPY', '2026-01-01', ?, 0.2)",
            (ticker, beta),
        )
        conn.commit()
    finally:
        conn.close()


def _clear_betas() -> None:
    conn = _db_module.get_connection()
    try:
        conn.execute("DELETE FROM xray_risk_cache")
        conn.commit()
    finally:
        conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# 1. _primary_sector
# ──────────────────────────────────────────────────────────────────────────────

class TestPrimarySector:

    def test_returns_highest_weight_sector(self):
        holding = _make_holding("X", 100, sectors=[
            {"name": "Technology", "weight": 0.8},
            {"name": "Healthcare", "weight": 0.2},
        ])
        assert _primary_sector(holding) == "Technology"

    def test_returns_unknown_for_empty_sectors(self):
        holding = _make_holding("X", 100, sectors=[])
        assert _primary_sector(holding) == "Unknown"

    def test_returns_unknown_when_sectors_key_missing(self):
        assert _primary_sector({"symbol": "X", "value": 100}) == "Unknown"

    def test_handles_missing_weight_field(self):
        holding = _make_holding("X", 100, sectors=[
            {"name": "Energy"},
            {"name": "Technology", "weight": 0.5},
        ])
        assert _primary_sector(holding) == "Technology"

    def test_returns_unknown_when_name_missing(self):
        holding = _make_holding("X", 100, sectors=[{"weight": 1.0}])
        assert _primary_sector(holding) == "Unknown"


# ──────────────────────────────────────────────────────────────────────────────
# 2. _get_betas
# ──────────────────────────────────────────────────────────────────────────────

class TestGetBetas:

    def setup_method(self):
        _clear_betas()

    def test_empty_tickers_returns_empty_dict(self):
        conn = _db_module.get_connection()
        try:
            assert _get_betas([], conn) == {}
        finally:
            conn.close()

    def test_returns_correct_beta_for_known_ticker(self):
        _seed_beta("AAPL", 1.25)
        conn = _db_module.get_connection()
        try:
            result = _get_betas(["AAPL"], conn)
            assert result["AAPL"] == pytest.approx(1.25, abs=0.001)
        finally:
            conn.close()

    def test_unknown_ticker_absent_from_result(self):
        conn = _db_module.get_connection()
        try:
            result = _get_betas(["UNKNOWN"], conn)
            assert "UNKNOWN" not in result
        finally:
            conn.close()

    def test_multiple_tickers_resolved(self):
        _seed_beta("MSFT", 0.9)
        _seed_beta("TSLA", 2.1)
        conn = _db_module.get_connection()
        try:
            result = _get_betas(["MSFT", "TSLA"], conn)
            assert result["MSFT"] == pytest.approx(0.9, abs=0.001)
            assert result["TSLA"] == pytest.approx(2.1, abs=0.001)
        finally:
            conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# 3. run_stress_test — guard paths
# ──────────────────────────────────────────────────────────────────────────────

_CONFIG = {
    "GHOSTFOLIO_ACCOUNTS": {"active": ["acc1"]},
    "BASE_CURRENCY": "GBP",
}


class TestRunStressTestGuards:

    def test_unknown_scenario_raises_value_error(self):
        with pytest.raises(ValueError, match="Unknown scenario"):
            run_stress_test("all", "nonexistent_scenario")

    def test_custom_scenario_without_drop_raises_value_error(self):
        with pytest.raises(ValueError, match="custom_drop"):
            run_stress_test("all", "custom")

    def test_no_active_accounts_raises_runtime_error(self):
        empty_config = {"GHOSTFOLIO_ACCOUNTS": {"active": []}, "BASE_CURRENCY": "GBP"}
        client = _mock_client([])
        with patch("stress_engine.load_config", return_value=empty_config), \
             patch("stress_engine.GhostfolioXRayClient", return_value=client):
            with pytest.raises(RuntimeError, match="No active Ghostfolio accounts"):
                run_stress_test("all", "covid_2020")

    def test_ghostfolio_not_configured_raises_runtime_error(self):
        client = _mock_client([], is_configured=False)
        with patch("stress_engine.load_config", return_value=_CONFIG), \
             patch("stress_engine.GhostfolioXRayClient", return_value=client):
            with pytest.raises(RuntimeError, match="Ghostfolio is not configured"):
                run_stress_test("all", "covid_2020")

    def test_auth_failure_raises_runtime_error(self):
        client = _mock_client([], auth_ok=False)
        with patch("stress_engine.load_config", return_value=_CONFIG), \
             patch("stress_engine.GhostfolioXRayClient", return_value=client):
            with pytest.raises(RuntimeError, match="authentication failed"):
                run_stress_test("all", "covid_2020")

    def test_no_holdings_raises_runtime_error(self):
        client = _mock_client([], total_value=0.0)
        with patch("stress_engine.load_config", return_value=_CONFIG), \
             patch("stress_engine.GhostfolioXRayClient", return_value=client):
            with pytest.raises(RuntimeError, match="No holdings returned"):
                run_stress_test("all", "covid_2020")


# ──────────────────────────────────────────────────────────────────────────────
# 4. run_stress_test — core calculation
# ──────────────────────────────────────────────────────────────────────────────

class TestRunStressTestCalculation:

    def setup_method(self):
        _clear_betas()

    def _run(self, holdings, total_value=10_000.0, scenario="covid_2020", custom_drop=None):
        client = _mock_client(holdings, total_value)
        with patch("stress_engine.load_config", return_value=_CONFIG), \
             patch("stress_engine.GhostfolioXRayClient", return_value=client):
            return run_stress_test("all", scenario, custom_drop=custom_drop)

    def test_result_has_required_keys(self):
        h = [_make_holding("AAPL", 10_000.0, sectors=[{"name": "Technology", "weight": 1.0}])]
        result = self._run(h)
        for key in ("scenario", "holdings", "sector_impact", "estimated_loss",
                    "estimated_loss_pct", "portfolio_value", "data_warnings", "generated_at"):
            assert key in result, f"Missing key: {key}"

    def test_drop_formula_market_x_beta_x_sector_mult(self):
        # covid_2020 market_drop = -0.34; Technology sector_mult = 0.7
        # beta=1.0 → expected drop = -0.34 × 1.0 × 0.7 = -0.238
        _seed_beta("AAPL", 1.0)
        h = [_make_holding("AAPL", 1000.0, sectors=[{"name": "Technology", "weight": 1.0}])]
        result = self._run(h)
        row = result["holdings"][0]
        assert row["estimated_drop_pct"] == pytest.approx(-23.8, abs=0.1)

    def test_high_beta_amplifies_drop(self):
        # covid_2020 market_drop=-0.34, Energy sector_mult=1.9, beta=2.0
        # expected = -0.34 × 2.0 × 1.9 = -1.292, clamped to -0.95
        _seed_beta("XOM", 2.0)
        h = [_make_holding("XOM", 1000.0, sectors=[{"name": "Energy", "weight": 1.0}])]
        result = self._run(h)
        row = result["holdings"][0]
        assert row["estimated_drop_pct"] == pytest.approx(-95.0, abs=0.1)

    def test_drop_clamped_at_minus_95_pct(self):
        # Any extreme calculation must not go below -95%
        _seed_beta("EXTREME", 5.0)
        h = [_make_holding("EXTREME", 1000.0, sectors=[{"name": "Energy", "weight": 1.0}])]
        result = self._run(h)
        row = result["holdings"][0]
        assert row["estimated_drop_pct"] >= -95.0

    def test_missing_beta_defaults_to_1_and_triggers_warning(self):
        # No beta seeded → defaults to 1.0 and appears in data_warnings
        h = [_make_holding("NOBETA", 1000.0)]
        result = self._run(h)
        assert len(result["data_warnings"]) == 1
        assert "NOBETA" in result["data_warnings"][0]
        assert "β=1.0" in result["data_warnings"][0]
        row = result["holdings"][0]
        assert row["beta"] == pytest.approx(1.0, abs=0.001)

    def test_estimated_loss_is_sum_of_holding_losses(self):
        _seed_beta("A", 1.0)
        _seed_beta("B", 1.0)
        h = [
            _make_holding("A", 6000.0, sectors=[{"name": "Healthcare", "weight": 1.0}]),
            _make_holding("B", 4000.0, sectors=[{"name": "Healthcare", "weight": 1.0}]),
        ]
        result = self._run(h, total_value=10_000.0)
        total = sum(r["estimated_loss"] for r in result["holdings"])
        assert result["estimated_loss"] == pytest.approx(total, abs=0.01)

    def test_custom_scenario_uses_provided_drop(self):
        _seed_beta("AAPL", 1.0)
        h = [_make_holding("AAPL", 1000.0)]
        result = self._run(h, scenario="custom", custom_drop=-0.20)
        # No sector multiplier for custom → drop = -0.20 × 1.0 × 1.0 = -20%
        row = result["holdings"][0]
        assert row["estimated_drop_pct"] == pytest.approx(-20.0, abs=0.1)

    def test_custom_scenario_name_includes_percentage(self):
        h = [_make_holding("AAPL", 1000.0)]
        result = self._run(h, scenario="custom", custom_drop=-0.30)
        assert "-30.0%" in result["scenario"]["name"]

    def test_account_id_all_uses_active_ids(self):
        h = [_make_holding("AAPL", 1000.0)]
        client = _mock_client(h, total_value=1000.0)
        with patch("stress_engine.load_config", return_value=_CONFIG), \
             patch("stress_engine.GhostfolioXRayClient", return_value=client):
            run_stress_test("all", "gfc_2008")
        client.get_holdings.assert_called_once_with(["acc1"])

    def test_sector_impact_groups_by_sector(self):
        _seed_beta("A", 1.0)
        _seed_beta("B", 1.0)
        h = [
            _make_holding("A", 5000.0, sectors=[{"name": "Technology", "weight": 1.0}], weight=0.5),
            _make_holding("B", 5000.0, sectors=[{"name": "Technology", "weight": 1.0}], weight=0.5),
        ]
        result = self._run(h)
        sectors = [s["sector"] for s in result["sector_impact"]]
        assert sectors.count("Technology") == 1

    def test_holdings_sorted_ascending_by_estimated_loss(self):
        _seed_beta("A", 1.0)
        _seed_beta("B", 0.5)
        h = [
            _make_holding("A", 5000.0, sectors=[{"name": "Energy", "weight": 1.0}], weight=0.5),
            _make_holding("B", 5000.0, sectors=[{"name": "Consumer Defensive", "weight": 1.0}], weight=0.5),
        ]
        result = self._run(h)
        losses = [r["estimated_loss"] for r in result["holdings"]]
        assert losses == sorted(losses)
