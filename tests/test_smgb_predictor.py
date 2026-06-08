"""Tests for smgb_predictor pure functions."""
import pytest
import pandas as pd
import numpy as np
from datetime import date, datetime, time
from unittest.mock import patch

from smgb_predictor import (
    compute_holdings_prediction,
    filter_post_uk_close,
    filter_pre_uk_open,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_daily_df(tickers_closes: dict, days: int = 5) -> pd.DataFrame:
    """Build a minimal daily DataFrame with sequential dates."""
    idx = pd.date_range("2026-01-01", periods=days, freq="D")
    data = {}
    for t, values in tickers_closes.items():
        if isinstance(values, (int, float)):
            data[t] = [values] * days
        else:
            data[t] = list(values) + [values[-1]] * max(0, days - len(values))
    return pd.DataFrame(data, index=idx)


def _make_intraday_df(timestamps: list, closes: list) -> pd.DataFrame:
    """Build a minimal intraday DataFrame with a naive UTC DatetimeIndex."""
    idx = pd.DatetimeIndex(timestamps)
    return pd.DataFrame({"Close": closes}, index=idx)


# ---------------------------------------------------------------------------
# compute_holdings_prediction
# ---------------------------------------------------------------------------

class TestComputeHoldingsPrediction:
    def _holdings(self, tickers):
        n = len(tickers)
        return [{"ticker": t, "weight": 1.0 / n} for t in tickers]

    def test_basic_no_fx_change(self):
        """Single holding, 10% gain, no FX change → predicted price = last_close × 1.10."""
        tickers = ["A", "B", "C"]
        df = _make_daily_df({t: [100.0, 110.0] for t in tickers}, days=2)
        result = compute_holdings_prediction(df, self._holdings(tickers), fx_rate=1.25, smgb_last_close_gbx=100.0)
        assert result is not None
        assert abs(result["predicted_change_pct"] - 10.0) < 0.01

    def test_uses_intraday_returns_when_provided(self):
        """When intraday_returns supplied, those are used instead of daily closes."""
        tickers = ["A", "B", "C"]
        df = _make_daily_df({t: [100.0, 100.0] for t in tickers}, days=2)  # 0% daily
        intraday = {t: 0.05 for t in tickers}  # 5% via intraday
        result = compute_holdings_prediction(df, self._holdings(tickers), fx_rate=1.25, smgb_last_close_gbx=100.0, intraday_returns=intraday)
        assert result is not None
        assert abs(result["predicted_change_pct"] - 5.0) < 0.01

    def test_returns_none_when_fewer_than_3_holdings_available(self):
        """Returns None when less than 3 holdings have price data."""
        holdings = [{"ticker": t, "weight": 0.5} for t in ["A", "B"]]
        df = _make_daily_df({"A": [100.0, 105.0], "B": [100.0, 105.0]}, days=2)
        result = compute_holdings_prediction(df, holdings, fx_rate=1.25, smgb_last_close_gbx=100.0)
        assert result is None

    def test_fx_weakening_gbp_raises_predicted_price(self):
        """GBPUSD falling (GBP weakens) → USD assets worth more in GBX → price rises."""
        tickers = ["A", "B", "C"]
        # 0% equity change, but GBP weakened 5% (fx_rate fell from 1.25 to 1.1875)
        df = _make_daily_df({"GBPUSD=X": [1.25, 1.1875], **{t: [100.0, 100.0] for t in tickers}}, days=2)
        result = compute_holdings_prediction(df, self._holdings(tickers), fx_rate=1.1875, smgb_last_close_gbx=100.0)
        assert result is not None
        assert result["fx_adjustment_pct"] > 0

    def test_no_fx_series_in_df_gives_zero_adjustment(self):
        """When no GBPUSD=X column exists in df, fx_adjustment = 0."""
        tickers = ["A", "B", "C"]
        df = _make_daily_df({t: [100.0, 110.0] for t in tickers}, days=2)
        result = compute_holdings_prediction(df, self._holdings(tickers), fx_rate=1.25, smgb_last_close_gbx=100.0)
        assert result is not None
        assert result["fx_adjustment_pct"] == 0.0

    def test_contributions_sorted_by_abs_descending(self):
        """Contributions are sorted by absolute contribution size, largest first."""
        holdings = [
            {"ticker": "A", "weight": 0.5},
            {"ticker": "B", "weight": 0.3},
            {"ticker": "C", "weight": 0.2},
        ]
        df = _make_daily_df({"A": [100.0, 100.0], "B": [100.0, 120.0], "C": [100.0, 105.0]}, days=2)
        result = compute_holdings_prediction(df, holdings, fx_rate=1.25, smgb_last_close_gbx=100.0)
        assert result is not None
        contribs = result["contributions"]
        sizes = [abs(c["contribution_pct"]) for c in contribs]
        assert sizes == sorted(sizes, reverse=True)

    def test_result_has_expected_keys(self):
        tickers = ["A", "B", "C"]
        df = _make_daily_df({t: [100.0, 110.0] for t in tickers}, days=2)
        result = compute_holdings_prediction(df, self._holdings(tickers), fx_rate=1.25, smgb_last_close_gbx=100.0)
        assert result is not None
        for k in ("predicted_price", "predicted_change_pct", "contributions", "fx_adjustment_pct", "n_holdings_used"):
            assert k in result

    def test_predicted_price_matches_last_close_times_return(self):
        """predicted_price = smgb_last_close × (1 + total_return)."""
        tickers = ["A", "B", "C"]
        df = _make_daily_df({t: [100.0, 100.0] for t in tickers}, days=2)
        intraday = {t: 0.10 for t in tickers}  # uniform 10%
        result = compute_holdings_prediction(df, self._holdings(tickers), fx_rate=1.25, smgb_last_close_gbx=200.0, intraday_returns=intraday)
        assert result is not None
        assert abs(result["predicted_price"] - 220.0) < 0.05


# ---------------------------------------------------------------------------
# filter_post_uk_close / filter_pre_uk_open
# ---------------------------------------------------------------------------

_LSE_OPEN = time(8, 0)    # 08:00 UTC (BST summer)
_LSE_CLOSE = time(16, 30)  # 16:30 UTC
_NYSE_PM = time(7, 30)     # 07:30 UTC — NYSE pre-market opens before LSE


class TestFilterPostUkClose:
    def _patch(self):
        return patch("smgb_predictor.time_engine.market_window_utc", return_value=(_LSE_OPEN, _LSE_CLOSE))

    def test_returns_bars_at_or_after_close(self):
        idx = pd.DatetimeIndex([
            datetime(2026, 1, 5, 16, 0),   # before close
            datetime(2026, 1, 5, 16, 30),  # at close
            datetime(2026, 1, 5, 17, 0),   # after close
        ])
        df = pd.DataFrame({"Close": [1.0, 2.0, 3.0]}, index=idx)
        with self._patch():
            result = filter_post_uk_close(df, ref_date=date(2026, 1, 5))
        assert list(result["Close"]) == [2.0, 3.0]

    def test_empty_df_returns_empty(self):
        df = pd.DataFrame({"Close": []})
        with self._patch():
            result = filter_post_uk_close(df, ref_date=date(2026, 1, 5))
        assert result.empty


class TestFilterPreUkOpen:
    def _patch(self):
        def _market_window(exchange, include_premarket=False):
            if include_premarket:
                return (_NYSE_PM, _LSE_CLOSE)
            return (_LSE_OPEN, _LSE_CLOSE)
        return patch("smgb_predictor.time_engine.market_window_utc", side_effect=_market_window)

    def test_returns_bars_in_premarket_window(self):
        idx = pd.DatetimeIndex([
            datetime(2026, 1, 5, 8, 0),    # at LSE open — excluded (not < 08:00)
            datetime(2026, 1, 5, 7, 45),   # in premarket window (>= 07:30 and < 08:00)
            datetime(2026, 1, 5, 7, 0),    # before NYSE premarket start — excluded
        ])
        df = pd.DataFrame({"Close": [1.0, 2.0, 3.0]}, index=idx)
        with self._patch():
            result = filter_pre_uk_open(df, ref_date=date(2026, 1, 5))
        assert list(result["Close"]) == [2.0]

    def test_empty_df_returns_empty(self):
        df = pd.DataFrame({"Close": []})
        with self._patch():
            result = filter_pre_uk_open(df, ref_date=date(2026, 1, 5))
        assert result.empty
