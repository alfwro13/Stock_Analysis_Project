"""
tests/test_lunchtime_briefing.py  ── LUNCHTIME BRIEFING

Tests for the pure rendering functions in lunchtime_briefing.py:

  _render_uk_midsession()    — regime icons, threat icons, pulse row formatting
  _render_us_premarket()     — markdown table for US pre-market snapshot
  _render_intraday_alerts()  — empty state + alert rows from DB
  _render_macro_events()     — event formatting, divergence flag, AI warning flag
"""

import sqlite3
import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from lunchtime_briefing import (
    _render_intraday_alerts,
    _render_macro_events,
    _render_uk_midsession,
    _render_us_premarket,
)


# ──────────────────────────────────────────────────────────────────────────────
# 1. _render_uk_midsession()
# ──────────────────────────────────────────────────────────────────────────────

class TestRenderUkMidsession:

    def _call(self, regime_data=None, macro_regime=None, pulse=None, charts=None):
        return _render_uk_midsession(
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
        out = self._call(regime_data={"uk_regime_label": "Volatile", "uk_turbulence": 2.5,
                                       "us_regime_label": "Normal", "us_turbulence": 1.0})
        assert "🟡" in out

    def test_crash_regime_shows_crash_warning(self):
        out = self._call(
            regime_data={"uk_regime_label": "Crash", "uk_turbulence": 5.0,
                         "us_regime_label": "Normal", "us_turbulence": 1.0},
            macro_regime={},
        )
        assert "Crash conditions" in out

    def test_volatile_combined_shows_volatile_warning(self):
        out = self._call(
            regime_data={"uk_regime_label": "Normal", "uk_turbulence": 1.0,
                         "us_regime_label": "Volatile", "us_turbulence": 3.0},
        )
        assert "Volatile conditions" in out

    def test_threat_level_green_icon(self):
        out = self._call(macro_regime={"uk_threat_level": "GREEN", "uk_yield_velocity": 0.1})
        assert "GREEN" in out

    def test_threat_level_red_icon(self):
        out = self._call(macro_regime={"uk_threat_level": "RED", "uk_yield_velocity": 0.5})
        assert "RED" in out

    def test_gbpusd_formatted_to_4dp(self):
        pulse = {"GBPUSD=X": {"price": 1.26789, "change_pct": -0.12}}
        out = self._call(pulse=pulse)
        assert "1.2679" in out

    def test_missing_pulse_row_shows_dash(self):
        out = self._call(pulse={})
        # All tickers missing: each should show em-dash
        assert "—" in out

    def test_charts_embedded_when_provided(self):
        out = self._call(charts={"ftse": "http://example.com/ftse.png"})
        assert "FTSE 100" in out
        assert "http://example.com/ftse.png" in out


# ──────────────────────────────────────────────────────────────────────────────
# 2. _render_us_premarket()
# ──────────────────────────────────────────────────────────────────────────────

class TestRenderUsPremarket:

    def test_returns_markdown_table_header(self):
        out = _render_us_premarket({})
        assert "| Asset |" in out
        assert "| Price |" in out

    def test_missing_pulse_row_shows_dash(self):
        out = _render_us_premarket({})
        assert "| — |" in out

    def test_known_ticker_price_formatted(self):
        pulse = {"^GSPC": {"price": 5250.75, "change_pct": 0.34}}
        out = _render_us_premarket(pulse)
        assert "5,250.75" in out
        assert "+0.34%" in out

    def test_tnx_formatted_as_percent(self):
        pulse = {"^TNX": {"price": 4.32, "change_pct": -0.05}}
        out = _render_us_premarket(pulse)
        assert "4.32%" in out


# ──────────────────────────────────────────────────────────────────────────────
# 3. _render_intraday_alerts()
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def alert_db():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.execute("""
        CREATE TABLE alert_state (
            engine TEXT, ticker TEXT, last_fired_utc TEXT
        )
    """)
    conn.commit()
    return conn


class TestRenderIntradayAlerts:

    def test_no_alerts_returns_none_message(self, alert_db):
        since = datetime(2026, 6, 7, 6, 0, 0)
        with patch("lunchtime_briefing.get_connection", return_value=alert_db):
            out = _render_intraday_alerts(since)
        assert "No crash or anomaly alerts" in out

    def test_alert_row_appears_in_output(self, alert_db):
        alert_db.execute(
            "INSERT INTO alert_state VALUES (?, ?, ?)",
            ("crash_engine", "AAPL", "2026-06-07T09:15:00"),
        )
        alert_db.commit()
        since = datetime(2026, 6, 7, 6, 0, 0)
        with patch("lunchtime_briefing.get_connection", return_value=alert_db):
            out = _render_intraday_alerts(since)
        assert "AAPL" in out
        assert "crash_engine" in out

    def test_alert_before_since_excluded(self, alert_db):
        alert_db.execute(
            "INSERT INTO alert_state VALUES (?, ?, ?)",
            ("crash_engine", "TSLA", "2026-06-07T04:00:00"),
        )
        alert_db.commit()
        since = datetime(2026, 6, 7, 6, 0, 0)
        with patch("lunchtime_briefing.get_connection", return_value=alert_db):
            out = _render_intraday_alerts(since)
        assert "TSLA" not in out
        assert "No crash or anomaly alerts" in out

    def test_db_error_returns_unavailable_message(self):
        since = datetime(2026, 6, 7, 6, 0, 0)
        bad_conn = sqlite3.connect(":memory:")
        bad_conn.close()
        with patch("lunchtime_briefing.get_connection", return_value=bad_conn):
            out = _render_intraday_alerts(since)
        assert "unavailable" in out.lower()


# ──────────────────────────────────────────────────────────────────────────────
# 4. _render_macro_events()
# ──────────────────────────────────────────────────────────────────────────────

class TestRenderMacroEvents:

    def test_no_events_returns_none_message(self):
        with patch("lunchtime_briefing.fetch_upcoming_macro_events", return_value=[]):
            out = _render_macro_events("2026-06-07")
        assert "No Tier-1" in out

    def test_event_appears_in_output(self):
        events = [{"event_date": "2026-06-08", "event_name": "CPI", "currency": "USD",
                   "previous_val": "3.1%", "forecast_val": "3.1%", "ai_volatility_warning": 0.0}]
        with patch("lunchtime_briefing.fetch_upcoming_macro_events", return_value=events):
            out = _render_macro_events("2026-06-07")
        assert "CPI" in out
        assert "USD" in out

    def test_divergent_forecast_shows_warning_flag(self):
        events = [{"event_date": "2026-06-08", "event_name": "NFP", "currency": "USD",
                   "previous_val": "150", "forecast_val": "200", "ai_volatility_warning": 0.0}]
        with patch("lunchtime_briefing.fetch_upcoming_macro_events", return_value=events):
            out = _render_macro_events("2026-06-07")
        assert "⚠️" in out

    def test_ai_warning_above_threshold_shows_alert_flag(self):
        events = [{"event_date": "2026-06-08", "event_name": "FOMC", "currency": "USD",
                   "previous_val": "5.25", "forecast_val": "5.25", "ai_volatility_warning": 3.5}]
        with patch("lunchtime_briefing.fetch_upcoming_macro_events", return_value=events):
            out = _render_macro_events("2026-06-07")
        assert "AI VOLATILITY WARNING" in out

    def test_matching_forecast_prev_no_warning(self):
        events = [{"event_date": "2026-06-08", "event_name": "GDP", "currency": "GBP",
                   "previous_val": "2.1%", "forecast_val": "2.1%", "ai_volatility_warning": 0.0}]
        with patch("lunchtime_briefing.fetch_upcoming_macro_events", return_value=events):
            out = _render_macro_events("2026-06-07")
        assert "⚠️" not in out
        assert "AI VOLATILITY WARNING" not in out
