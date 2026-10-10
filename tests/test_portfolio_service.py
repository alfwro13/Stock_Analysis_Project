import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


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
                    mock_yf.get_cached_fx_rate.return_value = {"rate": None}
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

    def test_yahoo_returns_none_prefers_persisted_quote(self):
        with patch("portfolio_service.BASE_CURRENCY", "GBP"):
            with patch("portfolio_service.yahoo_engine") as mock_yf:
                mock_yf.get_fx_rate.return_value = None
                mock_yf.get_cached_fx_rate.return_value = {"rate": 0.91}
                from portfolio_service import get_rate_to_base
                result = get_rate_to_base("CHF")
        assert result == pytest.approx(0.91)
        mock_yf.get_cached_fx_rate.assert_called_once_with("CHFGBP=X", refresh=False)

    def test_no_live_or_usable_persisted_quote_is_unavailable_not_1_0(self):
        with patch("portfolio_service.BASE_CURRENCY", "GBP"):
            with patch("portfolio_service.yahoo_engine") as mock_yf:
                mock_yf.get_fx_rate.return_value = None
                mock_yf.get_cached_fx_rate.return_value = {"rate": None}
                from portfolio_service import get_rate_to_base
                result = get_rate_to_base("SEK")
        assert result is None

    def test_a_previously_seen_rate_is_not_remembered_in_process(self):
        with patch("portfolio_service.BASE_CURRENCY", "GBP"):
            with patch("portfolio_service.yahoo_engine") as mock_yf:
                from portfolio_service import get_rate_to_base
                mock_yf.get_fx_rate.return_value = 0.86
                assert get_rate_to_base("EUR") == pytest.approx(0.86)
                mock_yf.get_fx_rate.return_value = None
                mock_yf.get_cached_fx_rate.return_value = {"rate": None}
                assert get_rate_to_base("EUR") is None

    def test_pence_with_non_gbp_base_is_unavailable_when_gbp_rate_is(self):
        with patch("portfolio_service.BASE_CURRENCY", "USD"):
            with patch("portfolio_service.yahoo_engine") as mock_yf:
                mock_yf.get_fx_rate.return_value = None
                mock_yf.get_cached_fx_rate.return_value = {"rate": None}
                from portfolio_service import get_rate_to_base
                assert get_rate_to_base("GBp") is None


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


def test_navigation_missing_fx_returns_none_and_preserves_pence():
    import portfolio_service

    with patch.object(portfolio_service.yahoo_engine, "get_cached_fx_rate", return_value={"rate": None}), patch.object(portfolio_service.yahoo_engine, "get_fx_rate", side_effect=AssertionError("navigation network")):
        assert portfolio_service.get_rate_to_base("USD", cache_only=True) is None
        assert portfolio_service.get_rate_from_base("USD", cache_only=True) is None
        assert portfolio_service.get_rate_to_base("GBp", cache_only=True) == 0.01
        with patch.object(portfolio_service, "BASE_CURRENCY", "USD"):
            assert portfolio_service.get_rate_to_base("GBp", cache_only=True) is None
            assert portfolio_service.get_rate_from_base("GBP", cache_only=True) is None
            assert portfolio_service.get_rate_from_base("GBp", cache_only=True) is None


def test_explicit_fx_refresh_reports_failure(_block_explicit_fx_refresh):
    import portfolio_service

    with patch.object(portfolio_service.yahoo_engine, "get_fx_rate", return_value=None):
        with pytest.raises(RuntimeError, match="last-good cached data retained"):
            _block_explicit_fx_refresh(["USD"])


def test_explicit_fx_refresh_attempts_every_pair_before_reporting(_block_explicit_fx_refresh):
    import portfolio_service

    with patch.object(portfolio_service, "BASE_CURRENCY", "GBP"), \
         patch.object(portfolio_service.yahoo_engine, "get_fx_rate", side_effect=lambda pair, force: None if pair == "EURGBP=X" else 0.8) as fetch:
        with pytest.raises(RuntimeError, match="EURGBP=X"):
            _block_explicit_fx_refresh(["EUR", "USD"])
    assert [call.args[0] for call in fetch.call_args_list] == ["EURGBP=X", "USDGBP=X"]


@pytest.mark.parametrize("currency", ["GBP", "GBp"])
def test_cached_from_non_gbp_base_uses_actual_gbp_pair(currency):
    import portfolio_service

    with patch.object(portfolio_service, "BASE_CURRENCY", "USD"), patch.object(portfolio_service.yahoo_engine, "get_cached_fx_rate", return_value={"rate": 0.8}) as quote:
        assert portfolio_service.get_rate_from_base(currency, cache_only=True) == 0.8
    quote.assert_called_once_with("USDGBP=X")


def test_fx_status_includes_fresh_quotes_only_when_requested(monkeypatch):
    import portfolio_service
    import time_engine
    from datetime import datetime, timezone

    quote = {"pair": "USDGBP=X", "rate": 0.75, "updated_at": 1791024000,
             "stale": False, "available": True}
    calls = []
    def read(pair, *, refresh):
        calls.append((pair, refresh))
        return dict(quote)
    monkeypatch.setattr(portfolio_service.yahoo_engine, "get_cached_fx_rate", read)
    assert portfolio_service.get_fx_cache_status(["USD", "USD", "GBP", "GBp"]) == []
    statuses = portfolio_service.get_fx_cache_status(["USD", "GBP"], include_fresh=True)
    assert statuses[0]["updated_display"] == time_engine.fmt_datetime(datetime.fromtimestamp(quote["updated_at"], timezone.utc))
    assert calls == [("USDGBP=X", False), ("USDGBP=X", False)]


@pytest.mark.parametrize("currency,from_base,expected", [
    ("USD", False, "USDGBP=X"),
    ("USD", True, "GBPUSD=X"),
    ("GBp", False, None),
    ("GBX", False, None),
    ("GBP", True, None),
    ("", False, None),
    (None, True, None),
])
def test_fx_pair_buckets_pence_with_pounds(currency, from_base, expected):
    import portfolio_service
    with patch("portfolio_service.BASE_CURRENCY", "GBP"):
        assert portfolio_service.fx_pair(currency, from_base=from_base) == expected


def test_fx_pair_pence_against_non_gbp_base_uses_gbp_pair():
    import portfolio_service
    with patch("portfolio_service.BASE_CURRENCY", "USD"):
        assert portfolio_service.fx_pair("GBX") == "GBPUSD=X"
        assert portfolio_service.fx_pair("GBp", from_base=True) == "USDGBP=X"


class TestPersistedFxReads:
    def _rate(self, quote, live=1.5):
        from portfolio_service import get_rate_to_base, persisted_fx_reads
        with patch("portfolio_service.BASE_CURRENCY", "GBP"), patch("portfolio_service.yahoo_engine") as mock_yf:
            mock_yf.get_cached_fx_rate.return_value = quote
            mock_yf.get_fx_rate.return_value = live
            with persisted_fx_reads():
                result = get_rate_to_base("USD")
        return result, mock_yf

    def test_usable_persisted_quote_is_served_without_awaiting_yahoo(self):
        result, mock_yf = self._rate({"rate": 1.1, "updated_at": 1.0})
        assert result == pytest.approx(1.1)
        mock_yf.get_fx_rate.assert_not_called()
        assert mock_yf.get_cached_fx_rate.call_args.kwargs["max_age"] == 3600

    def test_no_quote_within_bound_awaits_live_fetch(self):
        result, mock_yf = self._rate({"rate": None, "updated_at": None})
        assert result == pytest.approx(1.5)
        mock_yf.get_fx_rate.assert_called_once_with("USDGBP=X")

    def test_context_is_restored_afterwards(self):
        from portfolio_service import get_rate_to_base
        self._rate({"rate": 1.1, "updated_at": 1.0})
        with patch("portfolio_service.BASE_CURRENCY", "GBP"), patch("portfolio_service.yahoo_engine") as mock_yf:
            mock_yf.get_fx_rate.return_value = 1.5
            assert get_rate_to_base("USD") == pytest.approx(1.5)
            mock_yf.get_cached_fx_rate.assert_not_called()
