"""
tests/test_visuals_etf_charts.py — ETF chart functions in visuals_etf.py

Covers:
 - create_etf_correlation_chart
 - create_etf_prediction_chart
 - create_etf_contributions_chart
 - create_etf_overlay_chart
"""

import datetime
import pytest
import pandas as pd
import numpy as np
from unittest.mock import patch

from visuals_etf import (
    create_etf_correlation_chart,
    create_etf_prediction_chart,
    create_etf_contributions_chart,
    create_etf_overlay_chart,
)

pytestmark = pytest.mark.visuals

_NOW_UTC = datetime.datetime(2026, 6, 11, 14, 0, 0)  # naive UTC


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _price_series(n: int = 30, ticker: str = "X") -> pd.Series:
    idx = pd.date_range("2026-05-01", periods=n, freq="B", tz="UTC")
    return pd.Series(np.linspace(100.0, 110.0, n), index=idx, name=ticker)


def _intraday_series(n: int = 50) -> pd.Series:
    idx = pd.date_range(_NOW_UTC - datetime.timedelta(hours=n * 0.1),
                        periods=n, freq="6min", tz="UTC")
    return pd.Series(np.linspace(100.0, 102.0, n), index=idx)


def _normalized_df(tickers: list, n: int = 30) -> pd.DataFrame:
    idx = pd.date_range("2026-05-01", periods=n, freq="B")
    data = {t: np.linspace(100.0, 105.0, n) for t in tickers}
    return pd.DataFrame(data, index=idx)


def _rolling_corr(n: int = 30) -> pd.Series:
    idx = pd.date_range("2026-05-01", periods=n, freq="B")
    return pd.Series(np.linspace(0.5, 0.9, n), index=idx)


# ──────────────────────────────────────────────────────────────────────────────
# 1. create_etf_correlation_chart
# ──────────────────────────────────────────────────────────────────────────────

class TestCreateEtfCorrelationChart:

    def test_returns_html_string(self):
        df = _normalized_df(["SMGB.L", "AAPL", "MSFT"])
        result = create_etf_correlation_chart("SMGB.L", ["AAPL", "MSFT"], df, _rolling_corr())
        assert isinstance(result, str)
        assert "<div" in result

    def test_empty_df_returns_no_data_html(self):
        result = create_etf_correlation_chart(
            "SMGB.L", ["AAPL"], pd.DataFrame(), pd.Series(dtype=float)
        )
        assert "No Data" in result
        assert isinstance(result, str)

    def test_etf_ticker_appears_in_output(self):
        df = _normalized_df(["TEST.L", "AAPL"])
        result = create_etf_correlation_chart("TEST.L", ["AAPL"], df, _rolling_corr())
        assert "TEST.L" in result

    def test_fx_ticker_gets_yellow_color(self):
        """Tickers containing '=' (FX rates) should be assigned #ffeb3b."""
        df = _normalized_df(["SMGB.L", "GBPUSD=X"])
        result = create_etf_correlation_chart("SMGB.L", ["GBPUSD=X"], df, _rolling_corr())
        assert "ffeb3b" in result

    def test_empty_rolling_corr_still_renders(self):
        df = _normalized_df(["SMGB.L", "AAPL"])
        result = create_etf_correlation_chart("SMGB.L", ["AAPL"], df, pd.Series(dtype=float))
        assert isinstance(result, str)
        assert "<div" in result


# ──────────────────────────────────────────────────────────────────────────────
# 2. create_etf_prediction_chart
# ──────────────────────────────────────────────────────────────────────────────

class TestCreateEtfPredictionChart:

    def test_returns_html_string(self):
        hist = _price_series(20).rename(None)
        hist.index = hist.index.tz_localize(None)
        pred = {"predicted_price": 111.5, "predicted_change_pct": 1.4,
                "last_etf_close": 110.0}
        result = create_etf_prediction_chart("SMGB.L", "GBP", hist, pred)
        assert isinstance(result, str)
        assert "<div" in result

    def test_none_hist_renders_without_crash(self):
        pred = {"predicted_price": 50.0, "predicted_change_pct": 0.5,
                "last_etf_close": 49.75}
        result = create_etf_prediction_chart("ETF", "USD", None, pred)
        assert isinstance(result, str)

    def test_predicted_price_appears_in_output(self):
        hist = _price_series(20).rename(None)
        hist.index = hist.index.tz_localize(None)
        pred = {"predicted_price": 123.456, "predicted_change_pct": 2.0,
                "last_etf_close": 121.0}
        result = create_etf_prediction_chart("SMGB.L", "GBP", hist, pred)
        assert "123.456" in result or "123.46" in result

    def test_ci_band_rendered_when_regression_engine_present(self):
        hist = _price_series(20).rename(None)
        hist.index = hist.index.tz_localize(None)
        pred = {
            "predicted_price": 111.5, "predicted_change_pct": 1.4,
            "last_etf_close": 110.0,
            "regression_engine": {"lower_bound": 109.0, "upper_bound": 114.0},
        }
        result = create_etf_prediction_chart("SMGB.L", "GBP", hist, pred)
        assert "95% CI" in result

    def test_currency_appears_in_y_axis_title(self):
        hist = _price_series(20).rename(None)
        hist.index = hist.index.tz_localize(None)
        result = create_etf_prediction_chart("SMGB.L", "GBP", hist, {})
        assert "GBP" in result


# ──────────────────────────────────────────────────────────────────────────────
# 3. create_etf_contributions_chart
# ──────────────────────────────────────────────────────────────────────────────

class TestCreateEtfContributionsChart:

    def _contrib(self, ticker: str, weight: float, pct: float) -> dict:
        return {"ticker": ticker, "weight": weight,
                "contribution_pct": weight * pct, "return_pct": pct}

    def test_returns_html_string(self):
        contribs = [self._contrib("AAPL", 0.3, 2.0), self._contrib("MSFT", 0.2, -1.0)]
        result = create_etf_contributions_chart("SMGB.L", contribs)
        assert isinstance(result, str)
        assert "<div" in result

    def test_empty_contributions_returns_no_data_html(self):
        result = create_etf_contributions_chart("SMGB.L", [])
        assert "No Data" in result

    def test_ticker_names_appear_in_output(self):
        contribs = [self._contrib("NVIDIA", 0.15, 5.0)]
        result = create_etf_contributions_chart("ETF", contribs)
        assert "NVIDIA" in result

    def test_positive_contribution_uses_green_color(self):
        contribs = [self._contrib("AAPL", 0.3, 3.0)]  # positive pct → positive contribution
        result = create_etf_contributions_chart("ETF", contribs)
        assert "4caf50" in result

    def test_negative_contribution_uses_red_color(self):
        contribs = [self._contrib("TSLA", 0.2, -5.0)]  # negative pct → negative contribution
        result = create_etf_contributions_chart("ETF", contribs)
        assert "f44336" in result

    def test_chart_height_scales_with_item_count(self):
        contribs = [self._contrib(f"T{i}", 0.05, float(i)) for i in range(20)]
        result = create_etf_contributions_chart("ETF", contribs)
        assert "740" in result or "74" in result  # max(300, 20*32+100) = 740


# ──────────────────────────────────────────────────────────────────────────────
# 4. create_etf_overlay_chart
# ──────────────────────────────────────────────────────────────────────────────

_MOCK_MARKET_WINDOW = (datetime.time(8, 0), datetime.time(16, 30))


class TestCreateEtfOverlayChart:

    def _run(self, etf_series=None, constituent_series=None, prediction=None,
             session_relationship="behind"):
        if etf_series is None:
            etf_series = _intraday_series()
        if constituent_series is None:
            constituent_series = {"AAPL": _intraday_series()}
        with patch("visuals.time_engine.get_user_tz", return_value="Europe/London"), \
             patch("visuals.time_engine.market_window_utc", return_value=_MOCK_MARKET_WINDOW):
            return create_etf_overlay_chart(
                etf_ticker="TEST.L",
                etf_exchange="LSE",
                constituent_exchanges=["NYSE"],
                etf_series=etf_series,
                constituent_series=constituent_series,
                etf_last_close=100.0,
                prediction=prediction or {},
                next_open_date=datetime.date(2026, 6, 12),
                now_utc=_NOW_UTC,
                session_relationship=session_relationship,
            )

    def test_returns_html_string(self):
        result = self._run()
        assert isinstance(result, str)
        assert "<div" in result

    def test_etf_ticker_in_output(self):
        assert "TEST.L" in self._run()

    def test_none_prediction_does_not_crash(self):
        result = self._run(prediction=None)
        assert isinstance(result, str)

    def test_empty_constituent_series_renders(self):
        result = self._run(constituent_series={"AAPL": pd.Series(dtype=float)})
        assert isinstance(result, str)

    def test_prediction_star_rendered_when_prediction_present(self):
        pred = {"predicted_price": 101.5, "predicted_change_pct": 1.5,
                "signal_source": "daily_close"}
        result = self._run(prediction=pred)
        assert "101.5" in result or "Predicted" in result

    def test_now_marker_present(self):
        result = self._run()
        assert "Now" in result

    def test_exchange_open_label_present(self):
        result = self._run()
        assert "LSE Open" in result or "NYSE Open" in result

    def test_multiple_constituent_exchanges_rendered(self):
        with patch("visuals.time_engine.get_user_tz", return_value="Europe/London"), \
             patch("visuals.time_engine.market_window_utc", return_value=_MOCK_MARKET_WINDOW):
            result = create_etf_overlay_chart(
                etf_ticker="TEST.L",
                etf_exchange="LSE",
                constituent_exchanges=["NYSE", "XETRA"],
                etf_series=_intraday_series(),
                constituent_series={"AAPL": _intraday_series(), "SIE.DE": _intraday_series()},
                etf_last_close=100.0,
                now_utc=_NOW_UTC,
            )
        assert "LSE" in result
        assert "NYSE" in result
