import numpy as np
import pytest
from unittest.mock import patch
from monte_carlo_engine import run_simulation, _classify_asset_class, _load_corr_and_vols


HORIZON = 10
BASE_ARGS = dict(
    portfolio_value=50_000.0,
    monthly_contribution=0.0,
    horizon_years=HORIZON,
    target_wealth=100_000.0,
    drift_overrides={},
    inflation_pct=2.5,
    seed=42,
)


def test_output_shape():
    result = run_simulation(**BASE_ARGS)
    for key in ("p5", "p25", "p50", "p75", "p95"):
        assert len(result["percentiles"][key]) == HORIZON + 1
        assert len(result["percentiles_real"][key]) == HORIZON + 1


def test_percentile_ordering():
    result = run_simulation(**BASE_ARGS)
    p = result["percentiles"]
    final = {k: p[k][-1] for k in ("p5", "p25", "p50", "p75", "p95")}
    assert final["p5"] < final["p25"] < final["p50"] < final["p75"] < final["p95"]


def test_probability_of_success_bounds():
    result = run_simulation(**BASE_ARGS)
    prob = result["probability_of_success"]
    assert prob is not None
    assert 0.0 <= prob <= 1.0


def test_real_wealth_le_nominal():
    result = run_simulation(**BASE_ARGS)
    p_nom = result["percentiles"]["p50"]
    p_real = result["percentiles_real"]["p50"]
    for t in range(1, HORIZON + 1):
        assert p_real[t] <= p_nom[t], f"real > nominal at year {t}"


def test_contributions_increase_median():
    no_contrib = run_simulation(**BASE_ARGS)
    with_contrib = run_simulation(**{**BASE_ARGS, "monthly_contribution": 500.0})
    assert with_contrib["median_final"] > no_contrib["median_final"]


def test_zero_target_wealth_returns_none_probability():
    result = run_simulation(**{**BASE_ARGS, "target_wealth": 0.0})
    assert result["probability_of_success"] is None


def test_year_zero_equals_portfolio_value():
    result = run_simulation(**BASE_ARGS)
    assert result["percentiles"]["p50"][0] == pytest.approx(50_000.0, rel=1e-3)


class TestClassifyAssetClass:
    def test_fixed_income_keyword(self):
        assert _classify_asset_class("FIXED_INCOME", "USD") == "Bond/Fixed Income"

    def test_bond_keyword(self):
        assert _classify_asset_class("Bond ETF", "USD") == "Bond/Fixed Income"

    def test_equity_gbp_is_uk(self):
        assert _classify_asset_class("Equity", "GBP") == "UK Equity"

    def test_equity_usd_is_global(self):
        assert _classify_asset_class("Equity", "USD") == "Global Equity ETF"

    def test_unknown_class_is_global(self):
        assert _classify_asset_class("", "USD") == "Global Equity ETF"

    def test_none_asset_class_handled(self):
        # None → empty string; no "EQUITY" substring → falls through to global default
        assert _classify_asset_class(None, "GBP") == "Global Equity ETF"

    def test_fixed_income_takes_priority_over_gbp(self):
        # A GBP-denominated bond fund should be Bond/Fixed Income, not UK Equity
        assert _classify_asset_class("FIXED_INCOME", "GBP") == "Bond/Fixed Income"


class TestMultiAssetPath:
    """Exercises the correlated-GBM path when xray holdings are available."""

    _HOLDINGS = [
        {"symbol": "VWRL", "weight": 0.6, "asset_class": "EQUITY", "currency": "USD"},
        {"symbol": "IGLT", "weight": 0.4, "asset_class": "FIXED_INCOME", "currency": "GBP"},
    ]

    def _fake_load(self, benchmark="SPY"):
        corr = {"tickers": ["VWRL", "IGLT"], "matrix": np.eye(2)}
        vols = {"VWRL": 0.18, "IGLT": 0.06}
        return corr, vols

    def test_multi_asset_output_shape(self):
        with patch("xray_engine.assemble_xray_report", return_value={"holdings": self._HOLDINGS}):
            with patch("monte_carlo_engine._load_corr_and_vols", side_effect=self._fake_load):
                result = run_simulation(
                    portfolio_value=50_000.0,
                    monthly_contribution=0.0,
                    horizon_years=5,
                    target_wealth=80_000.0,
                    drift_overrides={},
                    inflation_pct=2.5,
                    seed=42,
                )
        assert result["status"] == "success"
        for key in ("p5", "p25", "p50", "p75", "p95"):
            assert len(result["percentiles"][key]) == 6

    def test_multi_asset_prob_success_in_range(self):
        with patch("xray_engine.assemble_xray_report", return_value={"holdings": self._HOLDINGS}):
            with patch("monte_carlo_engine._load_corr_and_vols", side_effect=self._fake_load):
                result = run_simulation(
                    portfolio_value=50_000.0,
                    monthly_contribution=0.0,
                    horizon_years=5,
                    target_wealth=80_000.0,
                    drift_overrides={},
                    inflation_pct=2.5,
                    seed=42,
                )
        assert 0.0 <= result["probability_of_success"] <= 1.0

    def test_cholesky_fallback_on_non_psd_matrix(self):
        bad_corr = {"tickers": ["VWRL"], "matrix": np.array([[-1.0]])}
        holdings = [{"symbol": "VWRL", "weight": 1.0, "asset_class": "EQUITY", "currency": "USD"}]
        with patch("xray_engine.assemble_xray_report", return_value={"holdings": holdings}):
            with patch("monte_carlo_engine._load_corr_and_vols", return_value=(bad_corr, {"VWRL": 0.20})):
                result = run_simulation(
                    portfolio_value=10_000.0,
                    monthly_contribution=0.0,
                    horizon_years=3,
                    target_wealth=0.0,
                    drift_overrides={},
                    inflation_pct=2.5,
                    seed=1,
                )
        assert result["status"] == "success"
        assert result["probability_of_success"] is None  # target_wealth=0

    def test_missing_ticker_vol_uses_default(self):
        """Ticker absent from vol_map falls back to DEFAULT_VOL (0.20)."""
        corr = {"tickers": ["VWRL"], "matrix": np.eye(1)}
        holdings = [{"symbol": "VWRL", "weight": 1.0, "asset_class": "EQUITY", "currency": "USD"}]
        with patch("xray_engine.assemble_xray_report", return_value={"holdings": holdings}):
            with patch("monte_carlo_engine._load_corr_and_vols", return_value=(corr, {})):
                result = run_simulation(
                    portfolio_value=10_000.0,
                    monthly_contribution=0.0,
                    horizon_years=2,
                    target_wealth=0.0,
                    drift_overrides={},
                    inflation_pct=2.5,
                    seed=7,
                )
        assert result["status"] == "success"
