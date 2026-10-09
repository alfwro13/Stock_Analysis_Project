"""
tests/test_fx_conversion_helpers.py — strict dated-FX conversion used by the Portfolio Optimizer and Strategy Backtester

Covers:
  • fx_levels_on(): latest close on or before each date, bounded age, NaN (never 1.0 or a live rate) outside it
  • fx_level_on() / cached_fx_close(): the one-date reading and the daily-history loader behind the other dated-FX readers
  • close_in_base() / returns_in_base(): price scaling and return compounding over the same interval
  • BaseCurrencyConverter: base-currency pass-through, pence/pound bucketing, unknown currency, missing or stale FX
"""

from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

from fx_conversion_helpers import (
    FX_MAX_FILL_DAYS,
    BaseCurrencyConverter,
    cached_fx_close,
    close_in_base,
    fx_level_on,
    fx_levels_on,
    returns_in_base,
)


def _series(values, start="2025-03-03"):
    return pd.Series(values, index=pd.bdate_range(start, periods=len(values)))


@pytest.fixture(autouse=True)
def _clear_fx_close_cache():
    import fx_conversion_helpers

    fx_conversion_helpers._fx_close_cache.clear()
    yield
    fx_conversion_helpers._fx_close_cache.clear()


@pytest.fixture(autouse=True)
def _gbp_base():
    with patch("portfolio_service.BASE_CURRENCY", "GBP"):
        yield


class TestFxLevelsOn:
    def test_exact_dates_take_that_days_close(self):
        fx = _series([0.8, 0.81, 0.82])
        assert fx_levels_on(fx.index, fx).tolist() == [0.8, 0.81, 0.82]

    def test_weekend_price_date_uses_the_previous_close(self):
        fx = _series([0.8, 0.81], start="2025-03-07")
        saturday = pd.DatetimeIndex(["2025-03-08"])
        assert fx_levels_on(saturday, fx).tolist() == [0.8]

    def test_dates_before_the_first_rate_are_nan(self):
        fx = _series([0.8], start="2025-03-10")
        assert np.isnan(fx_levels_on(pd.DatetimeIndex(["2025-03-03"]), fx).iloc[0])

    def test_rate_older_than_the_tolerance_is_not_carried(self):
        fx = _series([0.8], start="2025-03-03")
        late = pd.DatetimeIndex([pd.Timestamp("2025-03-03") + pd.Timedelta(days=FX_MAX_FILL_DAYS + 1)])
        assert np.isnan(fx_levels_on(late, fx).iloc[0])

    def test_empty_fx_gives_all_nan(self):
        assert fx_levels_on(pd.bdate_range("2025-03-03", periods=3), pd.Series(dtype=float)).isna().all()

    def test_order_of_the_input_dates_is_preserved(self):
        fx = _series([0.8, 0.9, 1.0])
        dates = pd.DatetimeIndex([fx.index[2], fx.index[0]])
        assert fx_levels_on(dates, fx).tolist() == [1.0, 0.8]


class TestFxLevelOn:
    def test_unsorted_duplicated_timezone_aware_and_intraday_stamped_closes_are_cleaned(self):
        index = pd.DatetimeIndex(["2025-03-05 23:00", "2025-03-03 23:00", "2025-03-03 23:00"]).tz_localize("UTC")
        fx = pd.Series([0.82, 0.80, 0.81], index=index)
        assert fx_level_on("2025-03-04", fx) == 0.81
        assert fx_level_on("2025-03-05", fx) == 0.82


    def test_string_date_takes_the_latest_close_on_or_before(self):
        fx = _series([0.8, 0.81], start="2025-03-07")
        assert fx_level_on("2025-03-08", fx) == 0.8

    def test_unavailable_when_outside_the_tolerance_or_empty(self):
        fx = _series([0.8], start="2025-03-03")
        assert fx_level_on("2025-03-20", fx) is None
        assert fx_level_on("2025-03-04", pd.Series(dtype=float)) is None

    def test_non_positive_close_is_unavailable(self):
        assert fx_level_on("2025-03-03", _series([0.0])) is None


class TestCachedFxClose:
    @staticmethod
    def _history(values=(0.8, 0.81)):
        return pd.DataFrame({"Close": list(values)}, index=pd.bdate_range("2025-03-03", periods=len(values)))

    def test_returns_the_close_column_of_the_daily_history(self):
        with patch("data_engine.daily_history_cache_revision", return_value=None), \
             patch("data_engine.load_or_fetch_daily_history", return_value=self._history()) as load:
            assert cached_fx_close("USDGBP=X", cache_only=True).tolist() == [0.8, 0.81]
        load.assert_called_once_with("USDGBP=X", cache_only=True)

    def test_none_when_there_is_no_history(self):
        with patch("data_engine.daily_history_cache_revision", return_value=None), \
             patch("data_engine.load_or_fetch_daily_history", return_value=None):
            assert cached_fx_close("USDGBP=X") is None

    def test_unchanged_file_revision_is_served_from_memory(self):
        with patch("data_engine.daily_history_cache_revision", return_value=("rev", 1)), \
             patch("data_engine.load_or_fetch_daily_history", return_value=self._history()) as load:
            first = cached_fx_close("USDGBP=X")
            second = cached_fx_close("USDGBP=X")
        assert load.call_count == 1
        assert second.tolist() == first.tolist()

    def test_changed_file_revision_reloads(self):
        revisions = iter([("rev", 1), ("rev", 1), ("rev", 2), ("rev", 2)])
        with patch("data_engine.daily_history_cache_revision", side_effect=lambda *a, **k: next(revisions)), \
             patch("data_engine.load_or_fetch_daily_history", side_effect=[self._history((0.8,)), self._history((0.9,))]) as load:
            assert cached_fx_close("USDGBP=X").tolist() == [0.8]
            assert cached_fx_close("USDGBP=X").tolist() == [0.9]
        assert load.call_count == 2

    def test_a_file_changing_during_the_read_is_not_kept(self):
        revisions = iter([("rev", 1), ("rev", 2)])
        with patch("data_engine.daily_history_cache_revision", side_effect=lambda *a, **k: next(revisions)), \
             patch("data_engine.load_or_fetch_daily_history", return_value=self._history()):
            cached_fx_close("USDGBP=X")
        import fx_conversion_helpers
        assert "USDGBP=X" not in fx_conversion_helpers._fx_close_cache

    def test_cache_is_bounded_and_drops_the_least_recently_used_pair(self):
        import fx_conversion_helpers
        with patch("data_engine.daily_history_cache_revision", return_value=("rev", 1)), \
             patch("data_engine.load_or_fetch_daily_history", return_value=self._history()):
            for n in range(fx_conversion_helpers._FX_CLOSE_CACHE_LIMIT + 1):
                cached_fx_close(f"P{n}=X")
        assert len(fx_conversion_helpers._fx_close_cache) == fx_conversion_helpers._FX_CLOSE_CACHE_LIMIT
        assert "P0=X" not in fx_conversion_helpers._fx_close_cache


class TestConversion:
    def test_close_in_base_scales_each_close(self):
        close = _series([100.0, 110.0])
        assert close_in_base(close, _series([0.8, 0.5])).tolist() == pytest.approx([80.0, 55.0])

    def test_close_in_base_drops_dates_without_a_rate(self):
        close = _series([100.0, 110.0, 120.0])
        fx = _series([0.8], start=close.index[2].strftime("%Y-%m-%d"))
        assert close_in_base(close, fx).index.tolist() == [close.index[2]]

    def test_returns_compound_with_the_fx_move_and_lose_the_first_row(self):
        returns = _series([0.01, 0.02, -0.01])
        fx = _series([0.80, 0.84, 0.84])
        out = returns_in_base(returns, fx)
        assert len(out) == 2
        assert out.iloc[0] == pytest.approx(1.02 * 1.05 - 1)
        assert out.iloc[1] == pytest.approx(0.99 * 1.0 - 1)


class TestBaseCurrencyConverter:
    def _converter(self, buckets, fx_by_pair):
        calls = []

        def load(pair):
            calls.append(pair)
            return fx_by_pair.get(pair)

        return BaseCurrencyConverter(buckets, load), calls

    def test_base_currency_ticker_is_returned_unchanged_without_loading_fx(self):
        converter, calls = self._converter({"AAA": "GBP"}, {})
        close = _series([1.0, 2.0])
        assert converter.prices("AAA", close).equals(close)
        assert calls == [] and converter.issues == {}

    def test_foreign_ticker_loads_its_pair_once(self):
        converter, calls = self._converter({"U1": "USD", "U2": "USD"}, {"USDGBP=X": _series([0.8, 0.8])})
        converter.prices("U1", _series([100.0, 100.0]))
        converter.prices("U2", _series([50.0, 50.0]))
        assert calls == ["USDGBP=X"]
        assert converter.pairs["USDGBP=X"]["sessions"] == 2

    def test_unknown_currency_is_an_issue_not_an_assumption(self):
        converter, _ = self._converter({"X": None}, {})
        assert converter.prices("X", _series([1.0])) is None
        assert "unknown" in converter.issues["X"]

    def test_missing_fx_history_names_the_pair(self):
        converter, _ = self._converter({"E": "EUR"}, {})
        assert converter.returns("E", _series([0.01, 0.02])) is None
        assert "EURGBP=X" in converter.issues["E"]

    def test_fx_with_no_rate_near_the_prices_is_an_issue(self):
        far_fx = _series([0.8], start="2020-01-06")
        converter, _ = self._converter({"U": "USD"}, {"USDGBP=X": far_fx})
        assert converter.prices("U", _series([100.0, 101.0])) is None
        assert "within" in converter.issues["U"]
