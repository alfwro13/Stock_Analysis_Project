"""
tests/test_ai_sentiment_engine.py — AISentimentPromptEngine unit tests

Covers:
  • _fmt_events: empty list returns "no events" placeholder; populated list includes event names
  • generate_us_prompt: raises ValueError for unknown mode; returns non-empty string for all 4
    US modes; result is cached on second call
  • generate_uk_prompt: raises ValueError for unknown mode; returns non-empty string for all 5
    UK modes; "UK vs US Comparison" and "UK Investor in US Exposure" include both data blocks
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db_module
from ai_sentiment_engine import AISentimentPromptEngine, _ALLOWED_US_MODES, _ALLOWED_UK_MODES


# ── seed helpers ──────────────────────────────────────────────────────────────

def _seed(conn):
    conn.execute(
        """INSERT OR REPLACE INTO market_regimes
           (date, us_regime_label, us_turbulence, uk_regime_label, uk_turbulence,
            ai_hmm_state, vix_close, spy_volatility, ftse_volatility,
            price_hmm_state, price_hmm_label, price_hmm_prob)
           VALUES ('2026-06-20','Low Vol',0.4,'Low Vol',0.3,1,14.5,10.2,9.8,0,'Bull',0.88)"""
    )
    conn.execute(
        """INSERT OR REPLACE INTO macro_indicators
           (date, us_cpi_inflation, us_yield_curve, us_high_yield_spread, us_m2,
            uk_cpi_inflation, uk_corporate_spread, uk_m4)
           VALUES ('2026-06-20', 3.2, -0.5, 3.1, 21000.0, 2.8, 1.5, 3100.0)"""
    )
    conn.execute(
        """INSERT OR REPLACE INTO macro_regimes
           (date, tnx_close, tyx_close, dxy_close, us_threat_level, us_yield_velocity,
            yield_curve_inverted, days_inverted, uk_gilt_close, gbpusd_close,
            uk_threat_level, uk_yield_velocity)
           VALUES ('2026-06-20', 4.5, 4.8, 104.2, 'GREEN', 0.05, 0, 0, 4.1, 1.27, 'GREEN', 0.02)"""
    )
    conn.execute(
        """INSERT OR REPLACE INTO ai_contagion_snapshots
           (scan_ts, leader_count, etf_count, alert_fired, payload_json)
           VALUES ('2026-06-20 12:00:00', 0, 0, 0, '{"tickers":[],"severity_score":0.0}')"""
    )
    conn.commit()


# ── _fmt_events ───────────────────────────────────────────────────────────────

class TestFmtEvents:
    def setup_method(self):
        self.engine = AISentimentPromptEngine()

    def test_empty_list_returns_placeholder(self):
        result = self.engine._fmt_events([])
        assert "No events found" in result

    def test_populated_list_contains_event_name(self):
        events = [{"event_name": "CPI", "event_date": "2026-06-21",
                   "forecast_val": 3.1, "previous_val": 3.3,
                   "ai_consensus_miss_prob": 0.42, "ai_volatility_warning": None}]
        result = self.engine._fmt_events(events)
        assert "CPI" in result
        assert "42.0%" in result

    def test_none_miss_prob_formats_as_na(self):
        events = [{"event_name": "NFP", "event_date": "2026-06-21",
                   "forecast_val": None, "previous_val": None,
                   "ai_consensus_miss_prob": None, "ai_volatility_warning": None}]
        result = self.engine._fmt_events(events)
        assert "N/A" in result

    def test_multiple_events_all_appear(self):
        events = [
            {"event_name": "CPI", "event_date": "2026-06-21", "forecast_val": 3.1,
             "previous_val": 3.3, "ai_consensus_miss_prob": 0.4, "ai_volatility_warning": None},
            {"event_name": "NFP", "event_date": "2026-06-22", "forecast_val": 180,
             "previous_val": 175, "ai_consensus_miss_prob": 0.3, "ai_volatility_warning": None},
        ]
        result = self.engine._fmt_events(events)
        assert "CPI" in result
        assert "NFP" in result


# ── generate_us_prompt ────────────────────────────────────────────────────────

class TestGenerateUsPrompt:
    def setup_method(self):
        self.engine = AISentimentPromptEngine()
        conn = _db_module.get_connection()
        _seed(conn)
        conn.close()

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            self.engine.generate_us_prompt("Not A Real Mode")

    @pytest.mark.parametrize("mode", list(_ALLOWED_US_MODES))
    def test_all_us_modes_return_nonempty_string(self, mode):
        result = self.engine.generate_us_prompt(mode)
        assert isinstance(result, str)
        assert len(result) > 50

    def test_us_data_block_header_present(self):
        result = self.engine.generate_us_prompt("US Market Health Check")
        assert "US MARKET DATA" in result

    def test_result_is_cached(self):
        mode = "Recession Radar"
        r1 = self.engine.generate_us_prompt(mode)
        r2 = self.engine.generate_us_prompt(mode)
        assert r1 is r2


# ── generate_uk_prompt ────────────────────────────────────────────────────────

class TestGenerateUkPrompt:
    def setup_method(self):
        self.engine = AISentimentPromptEngine()
        conn = _db_module.get_connection()
        _seed(conn)
        conn.close()

    def test_invalid_mode_raises(self):
        with pytest.raises(ValueError):
            self.engine.generate_uk_prompt("Not A Real Mode")

    @pytest.mark.parametrize("mode", list(_ALLOWED_UK_MODES))
    def test_all_uk_modes_return_nonempty_string(self, mode):
        result = self.engine.generate_uk_prompt(mode)
        assert isinstance(result, str)
        assert len(result) > 50

    def test_uk_data_block_header_present(self):
        result = self.engine.generate_uk_prompt("UK Market Health Check")
        assert "UK MARKET DATA" in result

    def test_comparison_modes_include_us_block(self):
        for mode in ("UK vs US Comparison", "UK Investor in US Exposure"):
            result = self.engine.generate_uk_prompt(mode)
            assert "US MARKET DATA" in result
            assert "UK MARKET DATA" in result

    def test_non_comparison_modes_exclude_us_block(self):
        result = self.engine.generate_uk_prompt("UK Market Health Check")
        assert "US MARKET DATA" not in result

    def test_result_is_cached(self):
        mode = "Pound & Gilt Impact"
        r1 = self.engine.generate_uk_prompt(mode)
        r2 = self.engine.generate_uk_prompt(mode)
        assert r1 is r2
