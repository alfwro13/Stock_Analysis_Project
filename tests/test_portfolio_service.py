"""
tests/test_portfolio_service.py

Unit tests for portfolio_service.py FX rate helpers.
No network calls — yahoo_engine is patched.
"""

import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ---------------------------------------------------------------------------
# get_rate_to_base
# ---------------------------------------------------------------------------

class TestGetRateToBase:
    """Tests for the Native→Base conversion helper."""

    def _call(self, stock_currency: str, base: str = "GBP", live_rate: float | None = 1.25):
        with patch("portfolio_service.BASE_CURRENCY", base):
            if live_rate is not None:
                with patch("portfolio_service.yahoo_engine") as mock_yf:
                    mock_yf.get_fx_rate.return_value = live_rate
                    from portfolio_service import get_rate_to_base
                    return get_rate_to_base(stock_currency)
            else:
                with patch("portfolio_service.yahoo_engine") as mock_yf:
                    mock_yf.get_fx_rate.return_value = None
                    from portfolio_service import get_rate_to_base
                    return get_rate_to_base(stock_currency)

    def test_same_currency_as_base_returns_1(self):
        result = self._call("GBP", base="GBP")
        assert result == 1.0

    def test_gbp_pence_to_gbp_base_returns_0_01(self):
        result = self._call("GBp", base="GBP")
        assert result == pytest.approx(0.01)

    def test_empty_currency_returns_1(self):
        result = self._call("", base="GBP")
        assert result == 1.0

    def test_none_currency_returns_1(self):
        with patch("portfolio_service.BASE_CURRENCY", "GBP"):
            with patch("portfolio_service.yahoo_engine") as mock_yf:
                mock_yf.get_fx_rate.return_value = 1.25
                from portfolio_service import get_rate_to_base
                result = get_rate_to_base(None)
        assert result == 1.0

    def test_gbp_pence_to_non_gbp_base_converts_via_gbp(self):
        # GBp → USD = 0.01 * GBPUSD rate (previously returned 1.0 — bug)
        with patch("portfolio_service.BASE_CURRENCY", "USD"):
            with patch("portfolio_service.yahoo_engine") as mock_yf:
                mock_yf.get_fx_rate.return_value = 1.27
                from portfolio_service import get_rate_to_base
                result = get_rate_to_base("GBp")
        assert result == pytest.approx(0.01 * 1.27)
        mock_yf.get_fx_rate.assert_called_once_with("GBPUSD=X")

    def test_foreign_currency_calls_fx_rate(self):
        with patch("portfolio_service.BASE_CURRENCY", "GBP"):
            with patch("portfolio_service.yahoo_engine") as mock_yf:
                mock_yf.get_fx_rate.return_value = 0.79
                from portfolio_service import get_rate_to_base
                result = get_rate_to_base("USD")
        assert result == pytest.approx(0.79)

    def test_yahoo_returns_none_falls_back_to_stale(self):
        import portfolio_service
        portfolio_service._last_known_rates["EURGBP=X"] = 0.86
        with patch("portfolio_service.BASE_CURRENCY", "GBP"):
            with patch("portfolio_service.yahoo_engine") as mock_yf:
                mock_yf.get_fx_rate.return_value = None
                from portfolio_service import get_rate_to_base
                result = get_rate_to_base("EUR")
        assert result == pytest.approx(0.86)

    def test_yahoo_none_no_stale_returns_1_0_fallback(self):
        import portfolio_service
        # Remove any stale entry that might have been written by other tests
        portfolio_service._last_known_rates.pop("SEKCURRENCY=X", None)
        portfolio_service._last_known_rates.pop("SEKGBP=X", None)
        with patch("portfolio_service.BASE_CURRENCY", "GBP"):
            with patch("portfolio_service.yahoo_engine") as mock_yf:
                mock_yf.get_fx_rate.return_value = None
                from portfolio_service import get_rate_to_base
                result = get_rate_to_base("SEK")
        assert result == 1.0


# ---------------------------------------------------------------------------
# get_rate_from_base
# ---------------------------------------------------------------------------

class TestGetRateFromBase:
    """Tests for the Base→Native conversion helper."""

    def test_same_currency_as_base_returns_1(self):
        with patch("portfolio_service.BASE_CURRENCY", "GBP"):
            with patch("portfolio_service.yahoo_engine") as mock_yf:
                mock_yf.get_fx_rate.return_value = 1.0
                from portfolio_service import get_rate_from_base
                assert get_rate_from_base("GBP") == 1.0

    def test_gbp_pence_returns_1(self):
        with patch("portfolio_service.BASE_CURRENCY", "GBP"):
            with patch("portfolio_service.yahoo_engine") as mock_yf:
                mock_yf.get_fx_rate.return_value = 1.0
                from portfolio_service import get_rate_from_base
                assert get_rate_from_base("GBp") == 1.0

    def test_foreign_currency_calls_inverse_pair(self):
        with patch("portfolio_service.BASE_CURRENCY", "GBP"):
            with patch("portfolio_service.yahoo_engine") as mock_yf:
                mock_yf.get_fx_rate.return_value = 1.27
                from portfolio_service import get_rate_from_base
                result = get_rate_from_base("USD")
        assert result == pytest.approx(1.27)
        mock_yf.get_fx_rate.assert_called_once_with("GBPUSD=X")

    def test_empty_currency_returns_1(self):
        with patch("portfolio_service.BASE_CURRENCY", "GBP"):
            with patch("portfolio_service.yahoo_engine") as mock_yf:
                mock_yf.get_fx_rate.return_value = 1.0
                from portfolio_service import get_rate_from_base
                assert get_rate_from_base("") == 1.0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
