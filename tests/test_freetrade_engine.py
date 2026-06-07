"""
tests/test_freetrade_engine.py  ── FREETRADE ENGINE

Covers resolve_ticker() for all 5 routing branches:
  - MUTUAL_FUND_EXCHANGE + ISIN in cache → returns cached value
  - MUTUAL_FUND_EXCHANGE + ISIN not in cache, HTTP success → resolves via Yahoo
  - MUTUAL_FUND_EXCHANGE + no ISIN / HTTP failure → appends .L fallback
  - US MIC → dots replaced with hyphens, uppercased
  - Unsupported MIC → (None, False)
  - Supported non-US MIC → appends .L
  - Symbol trailing dot stripped (e.g. RR. → RR.L not RR..L)
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from freetrade_engine import resolve_ticker

FT_CONFIG = {
    "US_MICS": ["XNAS", "XNYS", "BATS"],
    "EXCHANGES": {
        "XLON": {"ui_name": "London Stock Exchange"},
        "MUTUAL_FUND_EXCHANGE": {"ui_name": "Mutual Funds"},
    },
}


class TestMutualFundExchange:

    def test_isin_in_cache_returns_cached_symbol(self):
        cache = {"GB00B3X7QG63": "VWRL.L"}
        ticker, mapped = resolve_ticker("VWRL", "GB00B3X7QG63", "MUTUAL_FUND_EXCHANGE", cache, FT_CONFIG)
        assert ticker == "VWRL.L"
        assert mapped is True

    def test_isin_not_in_cache_http_success_resolves_symbol(self):
        cache = {}
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"quotes": [{"symbol": "VANEA.L"}]}

        with patch("freetrade_engine.requests.get", return_value=mock_response), \
             patch("freetrade_engine.time.sleep"):
            ticker, mapped = resolve_ticker("VANEA", "IE00B3RBWM25", "MUTUAL_FUND_EXCHANGE", cache, FT_CONFIG)

        assert ticker == "VANEA.L"
        assert mapped is True
        assert cache.get("IE00B3RBWM25") == "VANEA.L"

    def test_isin_not_in_cache_http_failure_falls_back_to_dot_l(self):
        cache = {}
        with patch("freetrade_engine.requests.get", side_effect=Exception("timeout")), \
             patch("freetrade_engine.time.sleep"):
            ticker, mapped = resolve_ticker("ACWI", "IE00B6R52259", "MUTUAL_FUND_EXCHANGE", cache, FT_CONFIG)

        assert ticker == "ACWI.L"
        assert mapped is True

    def test_nan_isin_falls_back_to_dot_l(self):
        import pandas as pd
        cache = {}
        ticker, mapped = resolve_ticker("HSBA", float("nan"), "MUTUAL_FUND_EXCHANGE", cache, FT_CONFIG)
        assert ticker == "HSBA.L"
        assert mapped is True

    def test_empty_isin_string_falls_back_to_dot_l(self):
        cache = {}
        ticker, mapped = resolve_ticker("HSBA", "   ", "MUTUAL_FUND_EXCHANGE", cache, FT_CONFIG)
        assert ticker == "HSBA.L"
        assert mapped is True


class TestUsMics:

    def test_xnas_ticker_returned_uppercased(self):
        ticker, mapped = resolve_ticker("aapl", None, "XNAS", {}, FT_CONFIG)
        assert ticker == "AAPL"
        assert mapped is True

    def test_dot_replaced_with_hyphen_for_us(self):
        """BRK.B must become BRK-B, not BRK.B."""
        ticker, mapped = resolve_ticker("BRK.B", None, "XNYS", {}, FT_CONFIG)
        assert ticker == "BRK-B"
        assert mapped is True

    def test_bats_mic_treated_as_us(self):
        ticker, mapped = resolve_ticker("SPY", None, "BATS", {}, FT_CONFIG)
        assert ticker == "SPY"
        assert mapped is True


class TestUnsupportedMic:

    def test_xetr_not_in_exchanges_returns_none_false(self):
        ticker, mapped = resolve_ticker("SAP", None, "XETR", {}, FT_CONFIG)
        assert ticker is None
        assert mapped is False

    def test_xpar_not_in_exchanges_returns_none_false(self):
        ticker, mapped = resolve_ticker("AIR", None, "XPAR", {}, FT_CONFIG)
        assert ticker is None
        assert mapped is False

    def test_unknown_mic_returns_none_false(self):
        ticker, mapped = resolve_ticker("XYZ", None, "UNKN", {}, FT_CONFIG)
        assert ticker is None
        assert mapped is False


class TestLondonEquities:

    def test_xlon_appends_dot_l(self):
        ticker, mapped = resolve_ticker("SHEL", None, "XLON", {}, FT_CONFIG)
        assert ticker == "SHEL.L"
        assert mapped is True

    def test_trailing_dot_stripped_before_appending_l(self):
        """Freetrade exports RR. and BP. — must not become RR..L."""
        ticker, mapped = resolve_ticker("RR.", None, "XLON", {}, FT_CONFIG)
        assert ticker == "RR.L"
        assert mapped is True

    def test_whitespace_stripped_from_symbol(self):
        ticker, mapped = resolve_ticker("  BP  ", None, "XLON", {}, FT_CONFIG)
        assert ticker == "BP.L"
        assert mapped is True
