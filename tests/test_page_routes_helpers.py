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

from page_routes import enrich_macro_events, _build_rss_base_url, _parse_cb_nlp_message


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


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
