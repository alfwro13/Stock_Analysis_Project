"""Tests for sentiment_engine pure-logic helpers."""
import pandas as pd
from datetime import date
from unittest.mock import patch

from sentiment_engine import fetch_parquet_data, fetch_stock_data


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
