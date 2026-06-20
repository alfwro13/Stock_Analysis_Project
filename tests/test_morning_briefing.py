"""
tests/test_morning_briefing.py — unit tests for morning_briefing rendering functions

Covers:
  _render_news_section()  — news present, no news, partial tickers
  _render_us_futures()    — price/change formatting, missing data, yield format
  _render_uk_preopen()    — regime icons, threat icons, crash/volatile warnings
  _format_age()           — time string formatting
"""
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from morning_briefing import (
    _format_age,
    _render_news_section,
    _render_us_futures,
    _render_uk_preopen,
)


# ── _format_age ───────────────────────────────────────────────────────────────

class TestFormatAge:
    def test_recent_returns_minutes(self):
        pub = datetime.now(timezone.utc) - timedelta(minutes=30)
        assert "30min ago" in _format_age(pub)

    def test_older_returns_hours(self):
        pub = datetime.now(timezone.utc) - timedelta(hours=3)
        result = _format_age(pub)
        assert "3h ago" in result or "h ago" in result


# ── _render_news_section ──────────────────────────────────────────────────────

class TestRenderNewsSection:
    _ITEM = {
        "title": "Test Headline",
        "summary": "A brief summary of the news.",
        "publisher": "Reuters",
        "age_str": "2h ago",
    }

    def test_news_present_contains_ticker_and_headline(self):
        news = {"AAPL": [self._ITEM]}
        out = _render_news_section(["AAPL"], news, {"AAPL": "Apple Inc."}, "Last 12h")
        assert "AAPL" in out
        assert "Test Headline" in out
        assert "Reuters" in out

    def test_no_news_at_all_shows_empty_message(self):
        out = _render_news_section(["AAPL"], {"AAPL": []}, {"AAPL": "Apple"}, "Last 12h")
        assert "No recent news" in out

    def test_ticker_without_news_listed_at_bottom(self):
        news = {"AAPL": [self._ITEM], "TSLA": []}
        out = _render_news_section(["AAPL", "TSLA"], news, {}, "Last 12h")
        assert "TSLA" in out
        assert "No overnight news found for" in out

    def test_summary_included_when_present(self):
        news = {"AAPL": [self._ITEM]}
        out = _render_news_section(["AAPL"], news, {}, "Last 12h")
        assert "A brief summary" in out

    def test_window_desc_in_output(self):
        out = _render_news_section([], {}, {}, "Custom Window")
        assert "Custom Window" in out

    def test_summary_truncation_indicator_when_max_length(self):
        long_item = {**self._ITEM, "summary": "x" * 250}
        news = {"AAPL": [long_item]}
        out = _render_news_section(["AAPL"], news, {}, "Last 12h")
        assert "…" in out


# ── _render_us_futures ────────────────────────────────────────────────────────

class TestRenderUsFutures:
    def _pulse(self, **kwargs):
        return kwargs

    def test_table_header_present(self):
        out = _render_us_futures({})
        assert "Asset" in out and "Price" in out and "Change" in out

    def test_missing_ticker_shows_dashes(self):
        out = _render_us_futures({})
        assert "— | —" in out or "—" in out

    def test_price_and_change_formatted(self):
        pulse = {"^GSPC": {"price": 5200.5, "change_pct": -0.75}}
        out = _render_us_futures(pulse)
        assert "5,200.50" in out
        assert "-0.75%" in out

    def test_tnx_shown_as_yield_percent(self):
        pulse = {"^TNX": {"price": 4.32, "change_pct": 0.03}}
        out = _render_us_futures(pulse)
        assert "4.32%" in out


# ── _render_uk_preopen ────────────────────────────────────────────────────────

class TestRenderUkPreopen:
    def _call(self, regime_data=None, macro_regime=None, pulse=None, charts=None):
        return _render_uk_preopen(
            pulse=pulse or {},
            regime_data=regime_data or {},
            macro_regime=macro_regime or {},
            charts=charts,
        )

    def test_unknown_regime_when_no_data(self):
        out = self._call()
        assert "Unknown" in out

    def test_normal_regime_shows_green_icon(self):
        out = self._call(regime_data={"uk_regime_label": "Normal", "uk_turbulence": 1.0})
        assert "🟢" in out

    def test_volatile_regime_shows_yellow_icon(self):
        out = self._call(regime_data={"uk_regime_label": "Volatile", "uk_turbulence": 2.0})
        assert "🟡" in out

    def test_crash_regime_shows_crash_warning(self):
        out = self._call(regime_data={"uk_regime_label": "Crash", "uk_turbulence": 6.0})
        assert "🔴" in out
        assert "Crash regime" in out

    def test_volatile_regime_shows_caution_message(self):
        out = self._call(regime_data={"uk_regime_label": "Volatile", "uk_turbulence": 3.0})
        assert "Volatile regime" in out

    def test_green_threat_level(self):
        out = self._call(macro_regime={"uk_threat_level": "GREEN"})
        assert "GREEN" in out

    def test_red_threat_level_shows_red_icon(self):
        out = self._call(macro_regime={"uk_threat_level": "RED"})
        assert "🔴" in out

    def test_gbpusd_formatted_4dp(self):
        pulse = {"GBPUSD=X": {"price": 1.2743, "change_pct": 0.12}}
        out = self._call(pulse=pulse)
        assert "1.2743" in out

    def test_charts_embedded_when_provided(self):
        charts = {"ftse": "/static/briefing_charts/ftse_2026-06-08.png"}
        out = self._call(charts=charts)
        assert "![FTSE 100]" in out

    def test_no_charts_when_not_provided(self):
        out = self._call(charts=None)
        assert "![FTSE 100]" not in out
