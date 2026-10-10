"""Tests for sentiment_engine pure-logic helpers."""
import numpy as np
import pandas as pd
import pytest
from datetime import date
from unittest.mock import patch

import sentiment_engine
from sentiment_engine import (
    generate_ftse_gbp_figure,
    generate_sentiment_figure,
    generate_uk_yield_equity_figure,
    generate_vix_spy_figure,
    generate_yield_equity_figure,
    fetch_parquet_data,
    fetch_stock_data,
    _bucket_fear_greed,
    get_latest_fear_greed,
)


# ---------------------------------------------------------------------------
# fetch_parquet_data — tests via a real temp Parquet file
# ---------------------------------------------------------------------------


class TestFetchParquetData:
    def test_missing_file_returns_empty(self, tmp_path):
        from config import HISTORICAL_DIR
        with patch("sentiment_engine.HISTORICAL_DIR", tmp_path):
            result = fetch_parquet_data("NONEXISTENT.parquet", "2025-01-01")
        assert result.empty

    def test_correct_column_rename(self, tmp_path):
        df = pd.DataFrame({"Close": [100.0, 200.0]}, index=pd.to_datetime(["2025-03-01", "2025-04-01"]))
        (tmp_path / "FTSE_BASELINE.parquet").write_bytes(df.to_parquet())
        with patch("sentiment_engine.HISTORICAL_DIR", tmp_path):
            result = fetch_parquet_data("FTSE_BASELINE.parquet", "2025-01-01")
        assert "FTSE_Close" in result.columns
        assert "Close" not in result.columns

    def test_filters_by_start_date(self, tmp_path):
        df = pd.DataFrame(
            {"Close": [1.0, 2.0, 3.0]},
            index=pd.to_datetime(["2025-01-01", "2025-06-01", "2025-12-01"])
        )
        (tmp_path / "FTSE_BASELINE.parquet").write_bytes(df.to_parquet())
        with patch("sentiment_engine.HISTORICAL_DIR", tmp_path):
            result = fetch_parquet_data("FTSE_BASELINE.parquet", "2025-06-01")
        # Only rows on or after start_date survive
        assert len(result) == 2
        assert all(idx >= date(2025, 6, 1) for idx in result.index)

    def test_prefix_extracted_from_filename(self, tmp_path):
        df = pd.DataFrame({"Close": [1.5]}, index=pd.to_datetime(["2025-01-01"]))
        (tmp_path / "GBPUSD_BASELINE.parquet").write_bytes(df.to_parquet())
        with patch("sentiment_engine.HISTORICAL_DIR", tmp_path):
            result = fetch_parquet_data("GBPUSD_BASELINE.parquet", "2024-01-01")
        assert "GBPUSD_Close" in result.columns


# ---------------------------------------------------------------------------
# fetch_stock_data — via yahoo_engine mock
# ---------------------------------------------------------------------------

class TestFetchStockData:
    def _make_spy_df(self, dates, closes):
        df = pd.DataFrame({"Close": closes}, index=pd.to_datetime(dates))
        df.index.name = "Date"
        return df

    def test_returns_empty_when_yahoo_returns_empty(self):
        with patch("sentiment_engine.yahoo_engine") as mock_ye:
            mock_ye.get_price_history.return_value = {"SPY": pd.DataFrame()}
            result = fetch_stock_data("SPY", "2025-01-01")
        assert result.empty

    def test_returns_empty_when_ticker_absent(self):
        with patch("sentiment_engine.yahoo_engine") as mock_ye:
            mock_ye.get_price_history.return_value = {}
            result = fetch_stock_data("SPY", "2025-01-01")
        assert result.empty

    def test_column_renamed_to_ticker_close(self):
        df = self._make_spy_df(["2025-06-01", "2025-06-02"], [500.0, 510.0])
        with patch("sentiment_engine.yahoo_engine") as mock_ye:
            mock_ye.get_price_history.return_value = {"SPY": df}
            result = fetch_stock_data("SPY", "2025-01-01")
        assert "SPY_Close" in result.columns
        assert "Close" not in result.columns

    def test_filters_rows_before_start_date(self):
        df = self._make_spy_df(
            ["2025-01-01", "2025-06-01", "2025-12-01"],
            [400.0, 500.0, 600.0],
        )
        with patch("sentiment_engine.yahoo_engine") as mock_ye:
            mock_ye.get_price_history.return_value = {"SPY": df}
            result = fetch_stock_data("SPY", "2025-06-01")
        assert len(result) == 2
        assert all(idx >= date(2025, 6, 1) for idx in result.index)


# ---------------------------------------------------------------------------
# _bucket_fear_greed — CNN's published boundaries
# ---------------------------------------------------------------------------

class TestBucketFearGreed:
    def test_extreme_fear_below_25(self):
        assert _bucket_fear_greed(0) == "Extreme Fear"
        assert _bucket_fear_greed(24.9) == "Extreme Fear"

    def test_fear_25_to_44(self):
        assert _bucket_fear_greed(25) == "Fear"
        assert _bucket_fear_greed(44.9) == "Fear"

    def test_neutral_45_to_55(self):
        assert _bucket_fear_greed(45) == "Neutral"
        assert _bucket_fear_greed(55.9) == "Neutral"

    def test_greed_56_to_75(self):
        assert _bucket_fear_greed(56) == "Greed"
        assert _bucket_fear_greed(75.9) == "Greed"

    def test_extreme_greed_76_and_above(self):
        assert _bucket_fear_greed(76) == "Extreme Greed"
        assert _bucket_fear_greed(100) == "Extreme Greed"


# ---------------------------------------------------------------------------
# get_latest_fear_greed — isolated cache, no coupling to the heavy chart cache
# ---------------------------------------------------------------------------

class TestGetLatestFearGreed:
    def test_returns_none_values_before_first_refresh(self):
        with patch.object(sentiment_engine, "_MACRO_HTML_CACHE", {
            "fear_greed_value": None, "fear_greed_label": None, "fear_greed_as_of": None,
        }), patch("sentiment_engine._check_and_trigger_async_refresh"):
            result = get_latest_fear_greed()
        assert result == {"value": None, "label": None, "as_of": None}

    def test_returns_cached_value_after_refresh(self):
        with patch.object(sentiment_engine, "_MACRO_HTML_CACHE", {
            "fear_greed_value": 62.0, "fear_greed_label": "Greed", "fear_greed_as_of": "2026-06-30",
        }), patch("sentiment_engine._check_and_trigger_async_refresh"):
            result = get_latest_fear_greed()
        assert result == {"value": 62.0, "label": "Greed", "as_of": "2026-06-30"}

    def test_does_not_touch_the_heavy_chart_html_cache_keys(self):
        cache = {
            "sentiment_html": "<div>existing chart</div>",
            "fear_greed_value": 40.0, "fear_greed_label": "Fear", "fear_greed_as_of": "2026-06-30",
        }
        with patch.object(sentiment_engine, "_MACRO_HTML_CACHE", cache), \
             patch("sentiment_engine._check_and_trigger_async_refresh"):
            get_latest_fear_greed()
        assert cache["sentiment_html"] == "<div>existing chart</div>"


# ---------------------------------------------------------------------------
# Chart layout — every sentiment_engine chart follows AGENTS.md rule 18
# ---------------------------------------------------------------------------

def _frame(**columns):
    idx = pd.date_range("2026-04-01", periods=30, freq="B")
    return pd.DataFrame({name: np.linspace(*bounds, 30) for name, bounds in columns.items()}, index=idx)


def _assert_rule_18_layout(layout):
    assert layout.title.x == 0.5 and layout.title.xanchor == "center"
    assert layout.legend.orientation == "h"
    assert layout.legend.yanchor == "top" and layout.legend.y == -0.15
    assert layout.legend.xanchor == "center" and layout.legend.x == 0.5
    assert layout.yaxis.automargin is True and layout.yaxis2.automargin is True
    assert layout.margin.b >= 60
    assert layout.height >= 400


class TestChartLayoutConvention:
    def test_fear_greed_figure(self):
        df = _frame(SPY_Close=(500, 520), Fear_Greed_Index=(20, 80))
        _assert_rule_18_layout(generate_sentiment_figure(df).layout)

    def test_vix_spy_figure(self):
        df = _frame(SPY_Close=(500, 520), VIX_Close=(15, 25))
        with patch("sentiment_engine.get_vix_spy_data", return_value=df):
            fig = generate_vix_spy_figure()
        _assert_rule_18_layout(fig.layout)

    @pytest.mark.parametrize("builder, columns", [
        (generate_yield_equity_figure, dict(SPY_Close=(500, 520), **{"^TYX_Close": (4.5, 5.0)})),
        (generate_uk_yield_equity_figure, dict(FTSE_Close=(8000, 8200), UK_Close=(4.2, 4.6))),
        (generate_ftse_gbp_figure, dict(FTSE_Close=(8000, 8200), GBPUSD_Close=(1.25, 1.30))),
    ])
    def test_merged_frame_figures(self, builder, columns):
        _assert_rule_18_layout(builder(_frame(**columns)).layout)


class TestAsyncChartCruncherWorker:
    def test_worker_caches_all_three_shared_builder_charts_with_rule_18_layout(self, monkeypatch):
        frames = {
            "SPY": _frame(SPY_Close=(500, 520)),
            "^TYX": _frame(**{"^TYX_Close": (4.5, 5.0)}),
            "FTSE_BASELINE.parquet": _frame(FTSE_Close=(8000, 8200)),
            "UK_GILT_BASELINE.parquet": _frame(UK_Close=(4.2, 4.6)),
            "GBPUSD_BASELINE.parquet": _frame(GBPUSD_Close=(1.25, 1.30)),
        }
        cache = {}
        monkeypatch.setattr(sentiment_engine, "_MACRO_HTML_CACHE", cache)
        monkeypatch.setattr(sentiment_engine, "_IS_REFRESHING", True)
        monkeypatch.setattr(sentiment_engine, "_LAST_CACHE_TIME", 0.0)
        monkeypatch.setattr(sentiment_engine, "get_sentiment_data", lambda: None)
        monkeypatch.setattr(sentiment_engine, "generate_vix_spy_figure", lambda: None)
        monkeypatch.setattr(sentiment_engine, "fetch_stock_data", lambda ticker, start: frames[ticker])
        monkeypatch.setattr(sentiment_engine, "fetch_parquet_data", lambda name, start: frames[name])

        sentiment_engine._async_chart_cruncher_worker()

        for key in ("yield_equity_html", "uk_yield_equity_html", "ftse_gbp_html"):
            assert '"y":-0.15' in cache[key].replace(" ", "")
            assert '"height":420' in cache[key].replace(" ", "")
