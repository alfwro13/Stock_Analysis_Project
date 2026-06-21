"""
tests/test_page_routes_helpers.py

Pure-function tests for page_routes helpers that contain business logic.
No network calls, no DB access, no template rendering.
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from page_routes import _build_rss_base_url
from page_routes_macro import enrich_macro_events, _parse_cb_nlp_message
from page_helpers import _fmt_currency, _fmt_volume, _utc_str_to_local, calculate_pnl, _build_position_sizing_context


# ---------------------------------------------------------------------------
# enrich_macro_events
# ---------------------------------------------------------------------------

def _make_event(name: str, forecast=None, previous=None, currency="USD") -> dict:
    return {
        "event_name": name,
        "event_date": "2026-06-08 09:00:00",
        "forecast_val": forecast,
        "previous_val": previous,
        "currency": currency,
    }


class TestEnrichMacroEvents:
    def test_no_events_returns_empty_list(self):
        assert enrich_macro_events([]) == []

    def test_display_date_always_set(self):
        events = enrich_macro_events([_make_event("Unknown Event X")])
        assert "display_date" in events[0]

    def test_unmatched_event_has_null_context(self):
        events = enrich_macro_events([_make_event("Some Obscure Report XYZ")])
        assert events[0]["context"] is None
        assert events[0]["insight"] == ""

    def test_cpi_matched_and_marked_inverse(self):
        evt = _make_event("US CPI Report", forecast=3.2, previous=3.0)
        result = enrich_macro_events([evt])[0]
        assert result["context"] is not None
        assert "CPI" in result["context"] or "inflation" in result["context"].lower()

    def test_inverse_polarity_delta_negative_is_cooling(self):
        # CPI dropping is Dovish
        evt = _make_event("US CPI Report", forecast=2.8, previous=3.0)
        result = enrich_macro_events([evt])[0]
        assert "Cooling" in result["insight"] or "Dovish" in result["insight"]

    def test_inverse_polarity_delta_positive_is_hawkish(self):
        # CPI rising is Hawkish
        evt = _make_event("US CPI Report", forecast=3.5, previous=3.0)
        result = enrich_macro_events([evt])[0]
        assert "Hot" in result["insight"] or "Hawkish" in result["insight"]

    def test_inverse_polarity_zero_delta_is_unchanged(self):
        evt = _make_event("US CPI Report", forecast=3.0, previous=3.0)
        result = enrich_macro_events([evt])[0]
        assert "unchanged" in result["insight"].lower()

    def test_direct_polarity_delta_positive_is_bullish(self):
        # GDP rising is Bullish
        evt = _make_event("US GDP Q1", forecast=2.5, previous=2.0)
        result = enrich_macro_events([evt])[0]
        assert "Expanding" in result["insight"] or "Bullish" in result["insight"]

    def test_direct_polarity_delta_negative_is_bearish(self):
        evt = _make_event("US GDP Q1", forecast=1.5, previous=2.0)
        result = enrich_macro_events([evt])[0]
        assert "Slowing" in result["insight"] or "Bearish" in result["insight"]

    def test_threshold_polarity_pmi_above_50_is_expansion(self):
        evt = _make_event("Manufacturing PMI", forecast=52.0, previous=50.5)
        result = enrich_macro_events([evt])[0]
        assert "Expansion" in result["insight"]

    def test_threshold_polarity_pmi_below_50_is_contraction(self):
        evt = _make_event("Manufacturing PMI", forecast=48.5, previous=50.0)
        result = enrich_macro_events([evt])[0]
        assert "Contraction" in result["insight"]

    def test_missing_forecast_produces_empty_insight(self):
        evt = _make_event("US CPI Report", forecast=None, previous=3.0)
        result = enrich_macro_events([evt])[0]
        assert result["insight"] == ""

    def test_missing_previous_produces_empty_insight(self):
        evt = _make_event("US CPI Report", forecast=3.0, previous=None)
        result = enrich_macro_events([evt])[0]
        assert result["insight"] == ""

    def test_non_numeric_values_produce_empty_insight(self):
        evt = _make_event("US CPI Report", forecast="TBD", previous="n/a")
        result = enrich_macro_events([evt])[0]
        assert result["insight"] == ""

    def test_multiple_events_all_enriched(self):
        events = [
            _make_event("US CPI Report", forecast=3.2, previous=3.0),
            _make_event("Manufacturing PMI", forecast=51.0, previous=50.0),
        ]
        results = enrich_macro_events(events)
        assert len(results) == 2
        assert results[0]["context"] is not None
        assert results[1]["context"] is not None


# ---------------------------------------------------------------------------
# _build_rss_base_url
# ---------------------------------------------------------------------------

class TestBuildRssBaseUrl:
    def test_localhost_no_port_appends_port(self):
        result = _build_rss_base_url("http://localhost", 8090)
        assert result == "http://localhost:8090"

    def test_ip_address_no_port_appends_port(self):
        result = _build_rss_base_url("http://192.168.1.100", 8090)
        assert result == "http://192.168.1.100:8090"

    def test_domain_name_no_port_does_not_append_port(self):
        result = _build_rss_base_url("https://dashboard.example.com", 8090)
        assert result == "https://dashboard.example.com"

    def test_url_with_explicit_port_unchanged(self):
        result = _build_rss_base_url("http://localhost:9000", 8090)
        assert result == "http://localhost:9000"

    def test_trailing_slash_stripped(self):
        result = _build_rss_base_url("http://localhost/", 8090)
        assert result.endswith(":8090") and not result.endswith("/")

    def test_ip_v4_public_appends_port(self):
        result = _build_rss_base_url("http://10.0.0.5", 8090)
        assert ":8090" in result


# ---------------------------------------------------------------------------
# _parse_cb_nlp_message
# ---------------------------------------------------------------------------

HAWKISH_MSG = (
    "**Event:** ECB Rate Decision (EUR)\n"
    "**Calculated Tone:** HAWKISH\n"
    "**Expected Equity Impact:** Negative — rate hike expected\n"
    "**Analyzed FinBERT Score:** -0.82"
)

DOVISH_MSG = (
    "**Event:** Fed Press Conference (USD)\n"
    "**Calculated Tone:** DOVISH\n"
    "**Expected Equity Impact:** Positive — pivot signalled\n"
    "**Analyzed FinBERT Score:** +0.71"
)

NEUTRAL_MSG = (
    "**Event:** BoE Minutes (GBP)\n"
    "**Calculated Tone:** NEUTRAL\n"
    "**Expected Equity Impact:** Mixed\n"
    "**Analyzed FinBERT Score:** +0.05"
)

INCOMPLETE_MSG = "Some message with no tone field at all."


class TestParseCbNlpMessage:
    def test_hawkish_returns_red_class(self):
        result = _parse_cb_nlp_message(HAWKISH_MSG, "2026-06-08 09:00:00")
        assert result is not None
        assert result["css_class"] == "risk-summary-red"
        assert result["header_class"] == "red"

    def test_dovish_returns_green_class(self):
        result = _parse_cb_nlp_message(DOVISH_MSG, "2026-06-08 09:00:00")
        assert result is not None
        assert result["css_class"] == "risk-summary-green"
        assert result["header_class"] == "green"

    def test_neutral_returns_yellow_class(self):
        result = _parse_cb_nlp_message(NEUTRAL_MSG, "2026-06-08 09:00:00")
        assert result is not None
        assert result["css_class"] == "risk-summary-yellow"
        assert result["header_class"] == "yellow"

    def test_missing_tone_returns_none(self):
        result = _parse_cb_nlp_message(INCOMPLETE_MSG, "2026-06-08 09:00:00")
        assert result is None

    def test_event_name_parsed_correctly(self):
        result = _parse_cb_nlp_message(HAWKISH_MSG, "2026-06-08 09:00:00")
        assert result["event_name"] == "ECB Rate Decision"

    def test_currency_parsed_correctly(self):
        result = _parse_cb_nlp_message(HAWKISH_MSG, "2026-06-08 09:00:00")
        assert result["currency"] == "EUR"

    def test_timestamp_preserved(self):
        ts = "2026-06-08 09:30:00"
        result = _parse_cb_nlp_message(DOVISH_MSG, ts)
        assert result["timestamp"] == ts


# ---------------------------------------------------------------------------
# _fmt_currency
# ---------------------------------------------------------------------------

class TestFmtCurrency:
    def test_none_returns_none(self):
        assert _fmt_currency(None) is None

    def test_trillion(self):
        assert _fmt_currency(2.5e12) == "$2.50T"

    def test_billion(self):
        assert _fmt_currency(1.23e9) == "$1.23B"

    def test_million(self):
        assert _fmt_currency(5.6e6) == "$5.6M"

    def test_small_value(self):
        assert _fmt_currency(1234) == "$1,234"

    def test_negative_billion(self):
        result = _fmt_currency(-3e9)
        assert result == "-$3.00B"

    def test_zero(self):
        assert _fmt_currency(0) == "$0"


# ---------------------------------------------------------------------------
# _fmt_volume
# ---------------------------------------------------------------------------

class TestFmtVolume:
    def test_none_returns_none(self):
        assert _fmt_volume(None) is None

    def test_billions(self):
        assert _fmt_volume(2e9) == "2.0B"

    def test_millions(self):
        assert _fmt_volume(3.5e6) == "3.5M"

    def test_thousands(self):
        assert _fmt_volume(4000) == "4K"

    def test_small_value(self):
        assert _fmt_volume(42) == "42"

    def test_exact_billion_boundary(self):
        result = _fmt_volume(1e9)
        assert "B" in result

    def test_exact_million_boundary(self):
        result = _fmt_volume(1e6)
        assert "M" in result


# ---------------------------------------------------------------------------
# _utc_str_to_local
# ---------------------------------------------------------------------------

class TestUtcStrToLocal:
    def test_valid_datetime_string_returns_formatted(self):
        result = _utc_str_to_local("2026-06-08 14:30:00")
        assert result  # non-empty; actual format depends on USER_TIMEZONE

    def test_valid_hhmm_string_parsed(self):
        result = _utc_str_to_local("2026-06-08 14:30")
        assert result

    def test_invalid_string_returned_unchanged(self):
        assert _utc_str_to_local("not-a-date") == "not-a-date"

    def test_empty_string_returned_unchanged(self):
        assert _utc_str_to_local("") == ""


# ---------------------------------------------------------------------------
# calculate_pnl
# ---------------------------------------------------------------------------

class TestCalculatePnl:
    def test_zero_shares_returns_none(self):
        assert calculate_pnl(0, 10.0, 1.0, 100.0) is None

    def test_negative_shares_returns_none(self):
        assert calculate_pnl(-5, 10.0, 1.0, 100.0) is None

    def test_basic_profit(self):
        # 10 shares, buy at £10, current at £12, no FX, no pence conversion
        result = calculate_pnl(10, 10.0, 1.0, 12.0)
        assert result is not None
        assert result["pnl"] == pytest.approx(20.0)
        assert result["pnl_pct"] == pytest.approx(20.0)

    def test_basic_loss(self):
        result = calculate_pnl(10, 10.0, 1.0, 8.0)
        assert result["pnl"] == pytest.approx(-20.0)
        assert result["pnl_pct"] == pytest.approx(-20.0)

    def test_fx_exchange_rate_applied_to_buy_price(self):
        # buy_price_base=10 in USD, exchange_rate=0.8 → bp_adj=8 GBP
        result = calculate_pnl(1, 10.0, 0.8, 9.0)
        assert result["buy_price"] == pytest.approx(8.0)
        assert result["pnl"] == pytest.approx(1.0)

    def test_price_in_pence_converts_buy_price_to_pence(self):
        # buy_price_base=10 GBP, exchange_rate=1.0, current_price=1050p
        # bp_adj becomes 1000p; pnl = 1*(1050-1000) = 50p
        result = calculate_pnl(1, 10.0, 1.0, 1050.0, price_in_pence=True)
        assert result["buy_price"] == pytest.approx(1000.0)
        assert result["pnl"] == pytest.approx(50.0)
        assert result["pnl_pct"] == pytest.approx(5.0)

    def test_zero_cost_basis_pnl_pct_is_zero(self):
        result = calculate_pnl(1, 0.0, 1.0, 100.0)
        assert result["pnl_pct"] == 0

    def test_result_keys_present(self):
        result = calculate_pnl(5, 10.0, 1.0, 15.0)
        assert set(result.keys()) == {"shares", "buy_price", "current_value", "pnl", "pnl_pct"}

    def test_shares_rounded_to_4dp(self):
        result = calculate_pnl(1.23456789, 10.0, 1.0, 10.0)
        assert result["shares"] == 1.2346


# ---------------------------------------------------------------------------
# _build_position_sizing_context
# ---------------------------------------------------------------------------

class TestBuildPositionSizingContext:
    def _make_row(self, currency):
        """Return a dict-like object that behaves like an sqlite3.Row."""
        class _Row(dict):
            def keys(self):
                return super().keys()
        return _Row({"currency": currency})

    def test_returns_required_keys(self):
        with patch("page_helpers.get_position_sizing_config", return_value={}), \
             patch("page_helpers.get_rate_to_base", return_value=1.25):
            result = _build_position_sizing_context({"BASE_CURRENCY": "GBP"}, [])
        assert set(result.keys()) == {"config", "fx_rates", "base_currency"}

    def test_base_currency_always_has_rate_1(self):
        with patch("page_helpers.get_position_sizing_config", return_value={}), \
             patch("page_helpers.get_rate_to_base", return_value=1.25):
            result = _build_position_sizing_context({"BASE_CURRENCY": "GBP"}, [])
        assert result["fx_rates"]["GBP"] == 1.0

    def test_foreign_currency_from_rows_included(self):
        rows = [self._make_row("USD")]
        with patch("page_helpers.get_position_sizing_config", return_value={}), \
             patch("page_helpers.get_rate_to_base", return_value=0.79):
            result = _build_position_sizing_context({"BASE_CURRENCY": "GBP"}, rows)
        assert "USD" in result["fx_rates"]
        assert result["fx_rates"]["USD"] == pytest.approx(0.79)

    def test_failed_fx_lookup_does_not_raise(self):
        rows = [self._make_row("EUR")]
        with patch("page_helpers.get_position_sizing_config", return_value={}), \
             patch("page_helpers.get_rate_to_base", side_effect=Exception("network")):
            result = _build_position_sizing_context({"BASE_CURRENCY": "GBP"}, rows)
        assert result["fx_rates"]["GBP"] == 1.0
        assert "EUR" not in result["fx_rates"]

    def test_none_rate_excluded(self):
        rows = [self._make_row("JPY")]
        with patch("page_helpers.get_position_sizing_config", return_value={}), \
             patch("page_helpers.get_rate_to_base", return_value=None):
            result = _build_position_sizing_context({"BASE_CURRENCY": "GBP"}, rows)
        assert "JPY" not in result["fx_rates"]


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
