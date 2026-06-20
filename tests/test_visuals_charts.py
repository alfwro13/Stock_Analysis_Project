"""
tests/test_visuals_charts.py — Chart functions in visuals.py (non-ETF)

Covers:
 - create_anomaly_score_chart (threshold coloring)
 - create_anomaly_feature_radar (AXES normalization and clipping)
 - create_ai_contagion_performance_chart (empty guard, normalisation)
 - create_ai_contagion_correlation_heatmap (<2 tickers guard, correlation)
 - Smoke tests for macro economic charts (return HTML, empty-data guard)
"""

import pytest
import pandas as pd
import numpy as np
from datetime import datetime, timezone

from visuals import (
    create_anomaly_score_chart,
    create_anomaly_feature_radar,
    create_ai_contagion_performance_chart,
    create_ai_contagion_correlation_heatmap,
    create_us_inflation_chart,
    create_uk_inflation_chart,
    create_us_liquidity_chart,
    create_us_credit_chart,
    create_yield_curve_chart,
)

pytestmark = pytest.mark.visuals


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _anomaly_df(n: int = 30, high_score: bool = False) -> pd.DataFrame:
    idx = pd.date_range("2026-04-01", periods=n, freq="B")
    scores = np.linspace(0.3, 0.9 if high_score else 0.5, n)
    prices = np.linspace(100.0, 110.0, n)
    return pd.DataFrame({"anomaly_score": scores, "close_price": prices}, index=idx)


def _ohlcv_df(n: int = 30) -> pd.DataFrame:
    idx = pd.date_range("2026-04-01", periods=n, freq="B")
    closes = np.linspace(100.0, 110.0, n)
    return pd.DataFrame({
        "Open": closes * 0.99, "High": closes * 1.01,
        "Low": closes * 0.98, "Close": closes,
        "Volume": np.full(n, 1_000_000.0),
    }, index=idx)


# ──────────────────────────────────────────────────────────────────────────────
# create_anomaly_score_chart
# ──────────────────────────────────────────────────────────────────────────────

class TestCreateAnomalyScoreChart:

    def test_returns_html_string(self):
        result = create_anomaly_score_chart(_anomaly_df(), "AAPL")
        assert isinstance(result, str)
        assert "<div" in result

    def test_ticker_appears_in_output(self):
        result = create_anomaly_score_chart(_anomaly_df(), "NVDA")
        assert "NVDA" in result

    def test_high_score_uses_red_marker(self):
        df = _anomaly_df(n=5)
        df["anomaly_score"] = [0.9, 0.9, 0.9, 0.9, 0.9]
        result = create_anomaly_score_chart(df, "X", threshold=0.7)
        assert "ff4d4d" in result

    def test_low_score_uses_green_marker(self):
        df = _anomaly_df(n=5)
        df["anomaly_score"] = [0.1, 0.1, 0.1, 0.1, 0.1]
        result = create_anomaly_score_chart(df, "X", threshold=0.7)
        assert "00ffcc" in result

    def test_custom_threshold_in_output(self):
        result = create_anomaly_score_chart(_anomaly_df(), "X", threshold=0.85)
        assert "0.85" in result


# ──────────────────────────────────────────────────────────────────────────────
# create_anomaly_feature_radar
# ──────────────────────────────────────────────────────────────────────────────

class TestCreateAnomalyFeatureRadar:

    _BASE_FEATURES = {
        "volume_ratio": 1.5,
        "rsi_14": 60.0,
        "daily_return_pct": 2.0,
        "sma50_dist_pct": 5.0,
        "hist_vol_20": 0.3,
        "beta": 1.2,
    }

    def test_returns_html_string(self):
        result = create_anomaly_feature_radar(self._BASE_FEATURES, "AAPL")
        assert isinstance(result, str)
        assert "<div" in result

    def test_ticker_in_output(self):
        result = create_anomaly_feature_radar(self._BASE_FEATURES, "NVDA")
        assert "NVDA" in result

    def test_values_clamped_to_0_1(self):
        extreme = {k: 9999.0 for k in self._BASE_FEATURES}
        result = create_anomaly_feature_radar(extreme, "X")
        assert "1.0" in result

    def test_missing_key_defaults_to_zero(self):
        partial = {"volume_ratio": 1.0}
        result = create_anomaly_feature_radar(partial, "X")
        assert isinstance(result, str)

    def test_none_value_treated_as_zero(self):
        features = {**self._BASE_FEATURES, "rsi_14": None}
        result = create_anomaly_feature_radar(features, "X")
        assert isinstance(result, str)


# ──────────────────────────────────────────────────────────────────────────────
# create_ai_contagion_performance_chart
# ──────────────────────────────────────────────────────────────────────────────

class TestCreateAiContagionPerformanceChart:

    def _ticker_dfs(self, n: int = 30) -> dict:
        idx = pd.date_range("2026-04-01", periods=n, freq="B")
        return {
            "NVDA": pd.DataFrame({"Close": np.linspace(100.0, 120.0, n)}, index=idx),
            "MSFT": pd.DataFrame({"Close": np.linspace(200.0, 190.0, n)}, index=idx),
        }

    def test_returns_html_string(self):
        result = create_ai_contagion_performance_chart(self._ticker_dfs())
        assert isinstance(result, str)
        assert "<div" in result

    def test_empty_dict_returns_no_data_html(self):
        result = create_ai_contagion_performance_chart({})
        assert "No Data" in result

    def test_ticker_with_zero_first_close_skipped(self):
        idx = pd.date_range("2026-04-01", periods=5, freq="B")
        dfs = {"BAD": pd.DataFrame({"Close": [0.0, 1.0, 2.0, 3.0, 4.0]}, index=idx)}
        result = create_ai_contagion_performance_chart(dfs)
        assert isinstance(result, str)

    def test_normalised_base_is_100(self):
        idx = pd.date_range("2026-04-01", periods=5, freq="B")
        dfs = {"NVDA": pd.DataFrame({"Close": [50.0, 55.0, 60.0, 65.0, 70.0]}, index=idx)}
        result = create_ai_contagion_performance_chart(dfs)
        assert "100" in result

    def test_period_label_appears_in_title(self):
        result = create_ai_contagion_performance_chart(self._ticker_dfs(), period_label="Intraday")
        assert "Intraday" in result


# ──────────────────────────────────────────────────────────────────────────────
# create_ai_contagion_correlation_heatmap
# ──────────────────────────────────────────────────────────────────────────────

class TestCreateAiContagionCorrelationHeatmap:

    def _ticker_dfs(self, n: int = 30) -> dict:
        idx = pd.date_range("2026-04-01", periods=n, freq="B")
        rng = np.random.default_rng(42)
        dfs = {}
        for ticker in ["NVDA", "MSFT", "GOOGL"]:
            dfs[ticker] = pd.DataFrame({"Close": 100 + rng.normal(0, 1, n).cumsum()}, index=idx)
        return dfs

    def test_returns_html_string(self):
        result = create_ai_contagion_correlation_heatmap(self._ticker_dfs())
        assert isinstance(result, str)
        assert "<div" in result

    def test_insufficient_tickers_returns_no_data_html(self):
        idx = pd.date_range("2026-04-01", periods=10, freq="B")
        single = {"NVDA": pd.DataFrame({"Close": np.linspace(100, 110, 10)}, index=idx)}
        result = create_ai_contagion_correlation_heatmap(single)
        assert "Insufficient Data" in result

    def test_empty_dict_returns_no_data_html(self):
        result = create_ai_contagion_correlation_heatmap({})
        assert "Insufficient Data" in result

    def test_diagonal_values_are_one(self):
        result = create_ai_contagion_correlation_heatmap(self._ticker_dfs())
        assert "1.00" in result

    def test_tickers_appear_in_output(self):
        result = create_ai_contagion_correlation_heatmap(self._ticker_dfs())
        assert "NVDA" in result
        assert "MSFT" in result


# ──────────────────────────────────────────────────────────────────────────────
# Macro chart smoke tests (thin wrappers — verify HTML output + empty guards)
# ──────────────────────────────────────────────────────────────────────────────

class TestMacroChartSmoke:

    def _spy(self, n: int = 30) -> pd.DataFrame:
        idx = pd.date_range("2026-04-01", periods=n, freq="B")
        return pd.DataFrame({"Close": np.linspace(500.0, 520.0, n)}, index=idx)

    def _series(self, n: int = 24, val: float = 3.5) -> pd.DataFrame:
        idx = pd.date_range("2024-01-01", periods=n, freq="MS")
        return pd.DataFrame({"value": np.full(n, val)}, index=idx)

    def test_us_inflation_returns_html(self):
        result = create_us_inflation_chart(self._spy(), self._series())
        assert isinstance(result, str) and "<div" in result

    def test_us_inflation_empty_dataframes(self):
        result = create_us_inflation_chart(pd.DataFrame(), pd.DataFrame())
        assert isinstance(result, str)

    def test_uk_inflation_returns_html(self):
        result = create_uk_inflation_chart(self._spy(), self._series())
        assert isinstance(result, str) and "<div" in result

    def test_us_liquidity_returns_html(self):
        result = create_us_liquidity_chart(self._spy(), self._series(val=21000.0))
        assert isinstance(result, str) and "<div" in result

    def test_us_credit_returns_html(self):
        result = create_us_credit_chart(self._series(val=4.5))
        assert isinstance(result, str) and "<div" in result

    def test_yield_curve_returns_html(self):
        result = create_yield_curve_chart(self._series(val=-0.5))
        assert isinstance(result, str) and "<div" in result

    def test_yield_curve_empty_dataframe(self):
        result = create_yield_curve_chart(pd.DataFrame())
        assert isinstance(result, str)
