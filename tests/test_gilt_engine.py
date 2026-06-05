"""
tests/test_gilt_engine.py  ── GILT DATA SERVICE

Covers every function in GiltDataService with a focus on:
 - API contract tests (endpoint URLs, required params, required columns)
 - Regex pattern coverage for each FT.com scraping pattern
 - Graceful degradation when either source fails
 - Weekend fill and deduplication logic

External sources under contract test:
  BoE  GET https://www.bankofengland.co.uk/boeapps/database/_iadb-FromShowColumns.asp
       Required params: SeriesCodes=IUDMNPY, CSVF=TN
       Required response columns: DATE, IUDMNPY

  FT   GET https://markets.ft.com/data/bonds/tearsheet/summary
       Required param: s=UK10YG
"""

import io
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pandas as pd
import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

from gilt_engine import GiltDataService


# ──────────────────────────────────────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────────────────────────────────────

def _mock_resp(status_code: int, text: str = "", content: bytes = b"") -> MagicMock:
    m = MagicMock()
    m.status_code = status_code
    m.text = text
    m.content = content or text.encode()
    m.raise_for_status = MagicMock()
    if status_code >= 400:
        m.raise_for_status.side_effect = requests.exceptions.HTTPError(f"HTTP {status_code}")
    return m


BOE_CSV_VALID = (
    "DATE,IUDMNPY\n"
    "2024-01-02,3.80\n"
    "2024-01-03,3.82\n"
    "2024-01-04,3.79\n"
)

BOE_CSV_MISSING_COLUMN = "DATE,SOME_OTHER_SERIES\n2024-01-02,3.80\n"
BOE_CSV_ALL_NAN = "DATE,IUDMNPY\n2024-01-02,N/A\n2024-01-03,N/A\n"

FT_HTML_PATTERN1 = (
    'Yield<span class="mod-ui-data-list__value">4.25%</span>'
)
FT_HTML_PATTERN4 = "yield: 4.10"
FT_HTML_FALLBACK = 'class="mod-ui-data-list__value">4.30'
FT_HTML_OUT_OF_RANGE = 'class="mod-ui-data-list__value">99.99'
FT_HTML_NO_MATCH = "<html>nothing useful here</html>"


def _make_service(tmp_path: Path) -> GiltDataService:
    svc = GiltDataService()
    svc.parquet_path = tmp_path / "UK_GILT_BASELINE.parquet"
    return svc


# ──────────────────────────────────────────────────────────────────────────────
# 1. _get_with_retry()
# ──────────────────────────────────────────────────────────────────────────────

class TestGetWithRetry:

    def test_success_on_first_attempt(self):
        """Returns the response immediately on first success."""
        svc = GiltDataService()
        mock_resp = _mock_resp(200, "ok")
        with patch("requests.get", return_value=mock_resp) as mock_get:
            result = svc._get_with_retry("http://example.com", timeout=5)
        assert result is mock_resp
        assert mock_get.call_count == 1

    def test_retries_on_request_exception_then_succeeds(self):
        """Retries after RequestException and returns on subsequent success."""
        svc = GiltDataService()
        ok_resp = _mock_resp(200, "ok")
        with patch("requests.get", side_effect=[
            requests.exceptions.ConnectionError("refused"),
            ok_resp,
        ]), patch("time.sleep"):
            result = svc._get_with_retry("http://example.com", timeout=5, retries=2)
        assert result is ok_resp

    def test_exhausts_retries_and_raises_request_exception(self):
        """After all retries are exhausted the original RequestException propagates."""
        svc = GiltDataService()
        err = requests.exceptions.Timeout("timed out")
        with patch("requests.get", side_effect=err), patch("time.sleep"):
            with pytest.raises(requests.exceptions.Timeout):
                svc._get_with_retry("http://example.com", timeout=5, retries=2)

    def test_raises_for_status_called(self):
        """raise_for_status() is called on every response."""
        svc = GiltDataService()
        mock_resp = _mock_resp(200, "ok")
        with patch("requests.get", return_value=mock_resp):
            svc._get_with_retry("http://example.com", timeout=5, retries=1)
        mock_resp.raise_for_status.assert_called_once()


# ──────────────────────────────────────────────────────────────────────────────
# 2. fetch_historical_boe()
# ──────────────────────────────────────────────────────────────────────────────

class TestFetchHistoricalBoe:

    def test_contract_endpoint_and_series_code(self):
        """
        CONTRACT: BoE IADB endpoint must be called with SeriesCodes=IUDMNPY.
        If BoE renames the series code or endpoint, this test fails loudly.
        """
        svc = GiltDataService()
        called_urls = []
        called_params = []

        def capture(url, params=None, **kw):
            called_urls.append(url)
            called_params.append(params or {})
            return _mock_resp(200, content=BOE_CSV_VALID.encode())

        with patch.object(svc, "_get_with_retry", side_effect=capture):
            svc.fetch_historical_boe()

        assert len(called_urls) == 1
        assert "bankofengland.co.uk" in called_urls[0], (
            "CONTRACT VIOLATION: BoE endpoint URL changed."
        )
        assert called_params[0].get("SeriesCodes") == "IUDMNPY", (
            "CONTRACT VIOLATION: SeriesCodes param changed or missing."
        )

    def test_happy_path_returns_date_close_columns(self):
        """Valid CSV is parsed to a DataFrame with Date and Close columns."""
        svc = GiltDataService()
        with patch.object(svc, "_get_with_retry",
                          return_value=_mock_resp(200, content=BOE_CSV_VALID.encode())):
            df = svc.fetch_historical_boe()
        assert df is not None
        assert list(df.columns) == ["Date", "Close"]
        assert len(df) == 3
        assert df["Close"].iloc[0] == pytest.approx(3.80)

    def test_empty_csv_returns_none(self):
        """Empty response body → returns None."""
        svc = GiltDataService()
        with patch.object(svc, "_get_with_retry",
                          return_value=_mock_resp(200, content=b"DATE,IUDMNPY\n")):
            result = svc.fetch_historical_boe()
        assert result is None

    def test_missing_iudmnpy_column_returns_none(self):
        """
        CONTRACT: If BoE renames the IUDMNPY series column, the function must
        return None rather than crash or silently produce wrong data.
        """
        svc = GiltDataService()
        with patch.object(svc, "_get_with_retry",
                          return_value=_mock_resp(200, content=BOE_CSV_MISSING_COLUMN.encode())):
            result = svc.fetch_historical_boe()
        assert result is None, (
            "CONTRACT VIOLATION: BoE response missing IUDMNPY column — "
            "series code may have changed."
        )

    def test_all_nan_yields_returns_none(self):
        """If all IUDMNPY values are non-numeric, returns None."""
        svc = GiltDataService()
        with patch.object(svc, "_get_with_retry",
                          return_value=_mock_resp(200, content=BOE_CSV_ALL_NAN.encode())):
            result = svc.fetch_historical_boe()
        assert result is None

    def test_network_failure_returns_none(self):
        """Network error → returns None without propagating."""
        svc = GiltDataService()
        with patch.object(svc, "_get_with_retry",
                          side_effect=requests.exceptions.ConnectionError("refused")):
            result = svc.fetch_historical_boe()
        assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# 3. fetch_live_ft_yield()
# ──────────────────────────────────────────────────────────────────────────────

class TestFetchLiveFtYield:

    def test_contract_endpoint_and_symbol_param(self):
        """
        CONTRACT: FT endpoint must be called with s=UK10YG.
        If FT renames the bond symbol or endpoint, this test fails loudly.
        """
        svc = GiltDataService()
        called_urls = []
        called_params = []

        def capture(url, params=None, **kw):
            called_urls.append(url)
            called_params.append(params or {})
            return _mock_resp(200, text=FT_HTML_PATTERN1)

        with patch.object(svc, "_get_with_retry", side_effect=capture):
            svc.fetch_live_ft_yield()

        assert "markets.ft.com" in called_urls[0], (
            "CONTRACT VIOLATION: FT.com endpoint URL changed."
        )
        assert called_params[0].get("s") == "UK10YG", (
            "CONTRACT VIOLATION: FT bond symbol param changed or missing."
        )

    def test_pattern1_modern_ft_ui(self):
        """Pattern 1: mod-ui-data-list__value span with Yield label."""
        svc = GiltDataService()
        html = 'Yield<span class="mod-ui-data-list__value">4.25%</span>'
        with patch.object(svc, "_get_with_retry", return_value=_mock_resp(200, text=html)):
            result = svc.fetch_live_ft_yield()
        assert result == pytest.approx(4.25)

    def test_pattern4_generalized_fallback(self):
        """Pattern 4: bare 'yield: <value>' text."""
        svc = GiltDataService()
        with patch.object(svc, "_get_with_retry",
                          return_value=_mock_resp(200, text=FT_HTML_PATTERN4)):
            result = svc.fetch_live_ft_yield()
        assert result == pytest.approx(4.10)

    def test_fallback_price_node(self):
        """Price-node fallback: mod-ui-data-list__value without Yield label."""
        svc = GiltDataService()
        with patch.object(svc, "_get_with_retry",
                          return_value=_mock_resp(200, text=FT_HTML_FALLBACK)):
            result = svc.fetch_live_ft_yield()
        assert result == pytest.approx(4.30)

    def test_out_of_range_value_returns_none(self):
        """Values outside 0–25% are rejected as implausible gilt yields."""
        svc = GiltDataService()
        with patch.object(svc, "_get_with_retry",
                          return_value=_mock_resp(200, text=FT_HTML_OUT_OF_RANGE)):
            result = svc.fetch_live_ft_yield()
        assert result is None

    def test_no_pattern_match_returns_none(self):
        """No yield found in HTML → returns None."""
        svc = GiltDataService()
        with patch.object(svc, "_get_with_retry",
                          return_value=_mock_resp(200, text=FT_HTML_NO_MATCH)):
            result = svc.fetch_live_ft_yield()
        assert result is None

    def test_network_failure_returns_none(self):
        """Network error → returns None without propagating."""
        svc = GiltDataService()
        with patch.object(svc, "_get_with_retry",
                          side_effect=requests.exceptions.Timeout("timed out")):
            result = svc.fetch_live_ft_yield()
        assert result is None


# ──────────────────────────────────────────────────────────────────────────────
# 4. sync_gilt_data()
# ──────────────────────────────────────────────────────────────────────────────

def _boe_df():
    """Minimal BoE DataFrame matching fetch_historical_boe() output format."""
    return pd.DataFrame({
        "Date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-04"]),
        "Close": [3.80, 3.82, 3.79],
    })


class TestSyncGiltData:

    def test_historical_failure_returns_false(self, tmp_path):
        """sync_gilt_data() returns False when historical fetch fails."""
        svc = _make_service(tmp_path)
        with patch.object(svc, "fetch_historical_boe", return_value=None):
            result = svc.sync_gilt_data()
        assert result is False

    def test_live_failure_degrades_gracefully(self, tmp_path):
        """
        When live FT fetch fails, sync still succeeds using BoE data alone.
        No live data row is inserted.
        """
        svc = _make_service(tmp_path)
        with patch.object(svc, "fetch_historical_boe", return_value=_boe_df()), \
             patch.object(svc, "fetch_live_ft_yield", return_value=None):
            result = svc.sync_gilt_data()
        assert result is True
        df = pd.read_parquet(svc.parquet_path)
        assert len(df) == 3

    def test_success_writes_parquet(self, tmp_path):
        """Happy path writes a Parquet file and returns True."""
        svc = _make_service(tmp_path)
        with patch.object(svc, "fetch_historical_boe", return_value=_boe_df()), \
             patch.object(svc, "fetch_live_ft_yield", return_value=None):
            result = svc.sync_gilt_data()
        assert result is True
        assert svc.parquet_path.exists()

    def test_weekend_saturday_maps_to_friday(self, tmp_path):
        """
        When sync runs on Saturday, the live yield must be inserted under Friday's date.
        """
        from datetime import date as date_cls

        svc = _make_service(tmp_path)
        saturday_date = date_cls(2024, 1, 6)   # Saturday
        friday        = pd.Timestamp("2024-01-05")

        fake_now = MagicMock()
        fake_now.date.return_value = saturday_date

        with patch.object(svc, "fetch_historical_boe", return_value=_boe_df()), \
             patch.object(svc, "fetch_live_ft_yield", return_value=4.50), \
             patch("gilt_engine.datetime") as mock_dt:
            mock_dt.now.return_value = fake_now
            svc.sync_gilt_data()

        df = pd.read_parquet(svc.parquet_path)
        assert friday in df.index, "Live yield must be stored under Friday when run on Saturday"
        assert df.loc[friday, "Close"] == pytest.approx(4.50)

    def test_deduplication_keeps_last(self, tmp_path):
        """Duplicate index rows are de-duped with keep='last' (live data wins)."""
        boe_with_dup = pd.DataFrame({
            "Date": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-03"]),
            "Close": [3.80, 3.82, 3.99],
        })
        svc = _make_service(tmp_path)
        with patch.object(svc, "fetch_historical_boe", return_value=boe_with_dup), \
             patch.object(svc, "fetch_live_ft_yield", return_value=None):
            svc.sync_gilt_data()

        df = pd.read_parquet(svc.parquet_path)
        jan3 = pd.Timestamp("2024-01-03")
        assert df.loc[jan3, "Close"] == pytest.approx(3.99), (
            "keep='last' must preserve the final duplicate value"
        )
        assert len(df) == 2

    def test_parquet_write_failure_returns_false(self, tmp_path):
        """IO error writing Parquet → returns False without raising."""
        svc = _make_service(tmp_path)
        with patch.object(svc, "fetch_historical_boe", return_value=_boe_df()), \
             patch.object(svc, "fetch_live_ft_yield", return_value=None), \
             patch("pandas.DataFrame.to_parquet", side_effect=IOError("disk full")):
            result = svc.sync_gilt_data()
        assert result is False
