"""
tests/test_ai_engine.py  ── AI ENGINE UNIT TESTS

Covers:
  • Formatting helpers: _fmt_pct, _fmt_float, _clean_html, _describe_series_trajectory
  • Bug 1: Sector peer top/bottom lists never overlap for small sectors
  • Bug 2: _HTML_TAG_RE regex is a class-level constant (not recompiled per call)
  • Bug 3: _get_options_volatility surfaces last_updated staleness warning
  • Bug 4: volume.tail(5) redundancy removed — _get_technical_indicators still works
  • Technical trajectory: VCP ratio labelling, OBV direction, price/RSI paths
  • generate_prompt: returns None for unknown ticker, returns non-empty string for seeded data
  • generate_prompt: unknown mode falls back gracefully (no exception, logs warning)
"""

import sys
import sqlite3
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from ai_engine import AIPromptEngine


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

ENGINE = AIPromptEngine()

# Tickers used by this test file — unique to avoid colliding with other tests
SECTOR_TICKERS = [f"AI_PEER_{i}" for i in range(8)]  # AI_PEER_0 … AI_PEER_7
OPT_TICKER = "AI_OPT_TEST"
CORE_TICKER = "AI_CORE_TEST"


def _seed_sector_peers(num_peers: int):
    """
    Seed `num_peers` stock_signals + quant_signals rows in the test DB
    so that _get_sector_peer_context can rank them.
    Returns the list of tickers seeded.
    """
    import database as db
    conn = db.get_connection()
    cur = conn.cursor()
    tickers = SECTOR_TICKERS[:num_peers]
    for i, ticker in enumerate(tickers):
        cur.execute(
            """INSERT OR REPLACE INTO stock_signals
               (ticker, company_name, sector, current_price, composite_score,
                overall_signal, currency)
               VALUES (?, ?, 'AI_TEST_SECTOR', 100.0, 50, 'BULLISH', 'USD')""",
            (ticker, f"AI Test Co {i}"),
        )
        cur.execute(
            """INSERT OR REPLACE INTO quant_signals
               (ticker, date, rel_strength_20d)
               VALUES (?, ?, ?)""",
            (ticker, "2026-01-01", float(i) / 10.0),
        )
    conn.commit()
    conn.close()
    return tickers


def _seed_options_row(ticker: str, last_updated: str):
    """Insert one row into earnings_volatility with a controllable last_updated."""
    import database as db
    conn = db.get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO earnings_volatility
           (ticker, next_earnings_date, implied_move_pct, historical_avg_move_pct,
            edge_score, options_volume, last_updated)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (ticker, "2026-09-01", 5.0, 7.5, 2.5, 1000, last_updated),
    )
    conn.commit()
    conn.close()


def _seed_core_stock(ticker: str):
    """Insert minimal stock_signals + quant_signals rows for generate_prompt."""
    import database as db
    conn = db.get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO stock_signals
           (ticker, company_name, sector, current_price, composite_score,
            overall_signal, currency, educational_notes)
           VALUES (?, 'Core Test Co', 'Technology', 100.0, 60, 'STRONG BUY', 'USD',
                   '<p>Test <b>notes</b>.</p>')""",
        (ticker,),
    )
    conn.execute(
        """INSERT OR REPLACE INTO quant_signals
           (ticker, date, ml_confidence_score, var_95, cvar_95, sentiment_score,
            rel_strength_20d, rel_strength_5d, mom_1m, mom_3m, mom_6m,
            mom_12m_skip1m, hist_vol_20, atr_pct)
           VALUES (?, '2026-01-01', 75.0, 0.03, 0.05, 0.5,
                   0.1, 0.05, 0.08, 0.15, 0.2, 0.3, 0.12, 0.015)""",
        (ticker,),
    )
    conn.commit()
    conn.close()


# ─────────────────────────────────────────────────────────────────────────────
# 1. Formatting helpers (pure functions, no DB)
# ─────────────────────────────────────────────────────────────────────────────

class TestFormatters:
    def test_fmt_pct_normal(self):
        assert ENGINE._fmt_pct(0.12) == "12.0%"

    def test_fmt_pct_zero(self):
        assert ENGINE._fmt_pct(0.0) == "0.0%"

    def test_fmt_pct_none(self):
        assert ENGINE._fmt_pct(None) == "N/A"

    def test_fmt_pct_negative(self):
        assert ENGINE._fmt_pct(-0.05) == "-5.0%"

    def test_fmt_float_normal(self):
        assert ENGINE._fmt_float(3.14159) == "3.14"

    def test_fmt_float_none(self):
        assert ENGINE._fmt_float(None) == "N/A"

    def test_fmt_float_decimals(self):
        assert ENGINE._fmt_float(1.23456, decimals=4) == "1.2346"

    def test_clean_html_strips_tags(self):
        assert ENGINE._clean_html("<p>Hello <b>world</b></p>") == "Hello world"

    def test_clean_html_formats_list_items(self):
        result = ENGINE._clean_html("<ul><li>item1</li><li>item2</li></ul>")
        assert "- item1" in result
        assert "- item2" in result

    def test_clean_html_empty(self):
        assert ENGINE._clean_html("") == "No notes available."

    def test_clean_html_none(self):
        assert ENGINE._clean_html(None) == "No notes available."

    def test_clean_html_nbsp(self):
        result = ENGINE._clean_html("Hello&nbsp;World")
        assert "Hello World" in result


# ─────────────────────────────────────────────────────────────────────────────
# 2. _describe_series_trajectory
# ─────────────────────────────────────────────────────────────────────────────

class TestTrajectory:
    def test_rising_direction(self):
        s = pd.Series([10.0, 20.0, 30.0, 40.0, 50.0])
        result = ENGINE._describe_series_trajectory(s)
        assert "rising" in result

    def test_falling_direction(self):
        s = pd.Series([50.0, 40.0, 30.0, 20.0, 10.0])
        result = ENGINE._describe_series_trajectory(s)
        assert "falling" in result

    def test_flat_direction(self):
        s = pd.Series([25.0, 25.0, 25.0, 25.0, 25.0])
        result = ENGINE._describe_series_trajectory(s)
        assert "flat" in result

    def test_empty_series(self):
        assert ENGINE._describe_series_trajectory(pd.Series([], dtype=float)) == "N/A"

    def test_all_nan(self):
        assert ENGINE._describe_series_trajectory(pd.Series([float("nan")] * 5)) == "N/A"

    def test_single_value(self):
        result = ENGINE._describe_series_trajectory(pd.Series([42.5]))
        assert "42.5" in result

    def test_path_uses_arrow_separator(self):
        s = pd.Series([1.0, 2.0, 3.0])
        assert " -> " in ENGINE._describe_series_trajectory(s)

    def test_uses_last_5_points_of_longer_series(self):
        # Series of 10 points; only last 5 should appear in the path.
        s = pd.Series(list(range(10)), dtype=float)
        result = ENGINE._describe_series_trajectory(s)
        assert "5.0" in result   # first of last-5
        assert "9.0" in result   # last of last-5
        assert "0.0" not in result  # earlier values should be absent


# ─────────────────────────────────────────────────────────────────────────────
# BUG 2: _clean_html regex must be a class-level constant
# ─────────────────────────────────────────────────────────────────────────────

class TestRegexClassLevel:
    def test_html_tag_re_is_class_attribute(self):
        """_HTML_TAG_RE must exist on the class so it's compiled exactly once."""
        assert hasattr(AIPromptEngine, "_HTML_TAG_RE"), (
            "_HTML_TAG_RE must be a class-level constant, not compiled inside the method"
        )

    def test_html_tag_re_is_compiled_pattern(self):
        import re
        assert hasattr(AIPromptEngine._HTML_TAG_RE, "sub"), (
            "_HTML_TAG_RE must be a compiled re.Pattern"
        )


# ─────────────────────────────────────────────────────────────────────────────
# BUG 1: Sector peer top/bottom lists must not overlap for small sectors
# ─────────────────────────────────────────────────────────────────────────────

class TestSectorPeerOverlap:
    @pytest.mark.parametrize("num_peers", [2, 3, 4, 5, 6, 8])
    def test_no_overlap_between_top_and_bottom(self, num_peers):
        _seed_sector_peers(num_peers)
        result = ENGINE._get_sector_peer_context(SECTOR_TICKERS[0], "AI_TEST_SECTOR")
        assert isinstance(result, str)
        assert "Strongest" in result
        assert "Weakest" in result

        # Parse tickers from the two lines
        strongest_line = [l for l in result.splitlines() if "Strongest" in l][0]
        weakest_line = [l for l in result.splitlines() if "Weakest" in l][0]

        def _tickers_in(line):
            return {t for t in SECTOR_TICKERS if t in line}

        strongest_tickers = _tickers_in(strongest_line)
        weakest_tickers = _tickers_in(weakest_line)

        overlap = strongest_tickers & weakest_tickers
        assert not overlap, (
            f"With {num_peers} peers, tickers {overlap} appear in both Strongest and Weakest"
        )

    def test_single_peer_returns_insufficient_data(self):
        """Only 1 peer → should return the 'insufficient peer data' message."""
        # Use a separate sector so earlier parametrized seeds don't pollute the count.
        import database as db
        conn = db.get_connection()
        conn.execute(
            """INSERT OR REPLACE INTO stock_signals
               (ticker, company_name, sector, current_price, composite_score,
                overall_signal, currency)
               VALUES ('AI_SOLO', 'Solo Co', 'AI_SINGLE_SECTOR', 100.0, 50, 'BULLISH', 'USD')"""
        )
        conn.execute(
            """INSERT OR REPLACE INTO quant_signals (ticker, date, rel_strength_20d)
               VALUES ('AI_SOLO', '2026-01-01', 0.1)"""
        )
        conn.commit()
        conn.close()
        result = ENGINE._get_sector_peer_context("AI_SOLO", "AI_SINGLE_SECTOR")
        assert "insufficient" in result.lower()

    def test_unknown_sector_returns_unavailable(self):
        result = ENGINE._get_sector_peer_context("AAPL", None)
        assert "unknown" in result.lower()

    def test_rank_and_percentile_present(self):
        _seed_sector_peers(6)
        result = ENGINE._get_sector_peer_context(SECTOR_TICKERS[0], "AI_TEST_SECTOR")
        assert "ranks #" in result
        assert "percentile" in result.lower()


# ─────────────────────────────────────────────────────────────────────────────
# BUG 3: _get_options_volatility must surface staleness
# ─────────────────────────────────────────────────────────────────────────────

class TestOptionsStaleness:
    def test_fresh_data_no_staleness_warning(self):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _seed_options_row(OPT_TICKER + "_FRESH", ts)
        result = ENGINE._get_options_volatility(OPT_TICKER + "_FRESH")
        assert "stale" not in result.lower()

    def test_stale_data_warns(self):
        old_ts = (datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d %H:%M:%S")
        _seed_options_row(OPT_TICKER + "_STALE", old_ts)
        result = ENGINE._get_options_volatility(OPT_TICKER + "_STALE")
        assert "stale" in result.lower() or "days ago" in result.lower(), (
            f"Expected staleness warning in output, got:\n{result}"
        )

    def test_missing_ticker_returns_no_data_message(self):
        result = ENGINE._get_options_volatility("TICKER_THAT_DOES_NOT_EXIST_XYZ")
        assert "no earnings volatility" in result.lower() or "no " in result.lower()

    def test_edge_score_positive_underpriced_verdict(self):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _seed_options_row(OPT_TICKER + "_UNDER", ts)
        result = ENGINE._get_options_volatility(OPT_TICKER + "_UNDER")
        assert "UNDERPRICED" in result

    def test_last_updated_present_in_output(self):
        ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        _seed_options_row(OPT_TICKER + "_TS", ts)
        result = ENGINE._get_options_volatility(OPT_TICKER + "_TS")
        assert "Data as of" in result or "last updated" in result.lower() or ts[:10] in result


# ─────────────────────────────────────────────────────────────────────────────
# BUG 4: volume path — _describe_series_trajectory works without pre-slicing
# ─────────────────────────────────────────────────────────────────────────────

class TestVolumePathNonRedundant:
    def test_volume_path_with_full_series(self):
        """
        Passing the full volume series (not pre-sliced) must still produce
        a valid trajectory string — confirms Bug 4 fix doesn't break output.
        """
        vol = pd.Series([1e6 * (i + 1) for i in range(20)])
        result = ENGINE._describe_series_trajectory(vol, decimals=0)
        assert " -> " in result
        assert "rising" in result or "falling" in result or "flat" in result

    def test_describe_trajectory_tail5_equivalent(self):
        """
        _describe_series_trajectory(series) must give the same result as
        _describe_series_trajectory(series.tail(5)), since it takes .tail(5) internally.
        """
        s = pd.Series(list(range(20)), dtype=float)
        assert ENGINE._describe_series_trajectory(s) == ENGINE._describe_series_trajectory(s.tail(5))


# ─────────────────────────────────────────────────────────────────────────────
# Technical indicator helpers (VCP logic)
# ─────────────────────────────────────────────────────────────────────────────

class TestVCPRatio:
    """
    The Volume Contraction Ratio (VCP) is computed inside _get_technical_indicators.
    We test its labelling boundaries in isolation by calling
    _describe_series_trajectory and comparing inline logic.
    """

    def _make_parquet(self, close_prices, volumes):
        import io
        df = pd.DataFrame({
            "Close": close_prices,
            "Open": close_prices,
            "High": [p * 1.01 for p in close_prices],
            "Low": [p * 0.99 for p in close_prices],
            "Volume": volumes,
        })
        return df

    def test_contracting_volume_label(self, tmp_path):
        close = [100.0] * 50
        # volume.iloc[-25:-5] → indices 25-44 → prior baseline (high)
        # volume.tail(5)      → indices 45-49 → recent (low)
        # ratio = 500k / 1M = 0.5 → CONTRACTING
        volumes = [500_000.0] * 25 + [1_000_000.0] * 20 + [500_000.0] * 5
        df = self._make_parquet(close, volumes)

        parquet_path = tmp_path / "TEST_VCP.parquet"
        df.to_parquet(parquet_path)

        with patch("ai_engine.HISTORICAL_DIR", tmp_path):
            engine = AIPromptEngine()
            result = engine._get_technical_indicators("TEST_VCP")

        assert "CONTRACTING" in result["volume_contraction_ratio"], (
            f"Expected CONTRACTING label, got: {result['volume_contraction_ratio']}"
        )

    def test_expanding_volume_label(self, tmp_path):
        close = [100.0] * 50
        # volume.iloc[-25:-5] → indices 25-44 → prior baseline (low)
        # volume.tail(5)      → indices 45-49 → recent (high)
        # ratio = 1M / 500k = 2.0 → EXPANDING
        volumes = [500_000.0] * 25 + [500_000.0] * 20 + [1_000_000.0] * 5
        df = self._make_parquet(close, volumes)

        parquet_path = tmp_path / "TEST_VCP2.parquet"
        df.to_parquet(parquet_path)

        with patch("ai_engine.HISTORICAL_DIR", tmp_path):
            engine = AIPromptEngine()
            result = engine._get_technical_indicators("TEST_VCP2")

        assert "EXPANDING" in result["volume_contraction_ratio"]

    def test_stable_volume_label(self, tmp_path):
        close = [100.0] * 50
        # Ratio near 1.0 → stable
        volumes = [1_000_000.0] * 50
        df = self._make_parquet(close, volumes)

        parquet_path = tmp_path / "TEST_VCP3.parquet"
        df.to_parquet(parquet_path)

        with patch("ai_engine.HISTORICAL_DIR", tmp_path):
            engine = AIPromptEngine()
            result = engine._get_technical_indicators("TEST_VCP3")

        assert "stable" in result["volume_contraction_ratio"]

    def test_insufficient_history_returns_na(self, tmp_path):
        """Fewer than 30 rows → all metrics should remain N/A."""
        df = pd.DataFrame({"Close": [100.0] * 20, "Volume": [1e6] * 20})
        parquet_path = tmp_path / "TEST_SHORT.parquet"
        df.to_parquet(parquet_path)

        with patch("ai_engine.HISTORICAL_DIR", tmp_path):
            engine = AIPromptEngine()
            result = engine._get_technical_indicators("TEST_SHORT")

        assert result["rsi_path"] == "N/A"
        assert result["volume_contraction_ratio"] == "N/A"


class TestADXAndBollingerBands:
    """Feature: ADX trend strength and Bollinger Band position."""

    def _make_df(self, closes, volumes=None):
        if volumes is None:
            volumes = [1_000_000.0] * len(closes)
        return pd.DataFrame({
            "Close": closes,
            "Open": closes,
            "High": [c * 1.01 for c in closes],
            "Low":  [c * 0.99 for c in closes],
            "Volume": volumes,
        })

    def test_adx_trending_label(self, tmp_path):
        """A strongly trending series should produce an ADX > 25."""
        closes = [float(100 + i * 2) for i in range(60)]  # strong uptrend
        df = self._make_df(closes)
        (tmp_path / "ADX_TREND.parquet").exists() or df.to_parquet(tmp_path / "ADX_TREND.parquet")

        with patch("ai_engine.HISTORICAL_DIR", tmp_path):
            result = AIPromptEngine()._get_technical_indicators("ADX_TREND")

        assert result["adx"] != "N/A", "ADX should be computed for 60-bar series"
        assert "TRENDING" in result["adx"] or "STRONG" in result["adx"], (
            f"Expected TRENDING or STRONG TREND label, got: {result['adx']}"
        )

    def test_adx_absent_without_high_low(self, tmp_path):
        """When the parquet has no High/Low columns, ADX must remain N/A."""
        closes = [float(100 + i) for i in range(60)]
        df = pd.DataFrame({"Close": closes, "Volume": [1e6] * 60})
        df.to_parquet(tmp_path / "ADX_NOHL.parquet")

        with patch("ai_engine.HISTORICAL_DIR", tmp_path):
            result = AIPromptEngine()._get_technical_indicators("ADX_NOHL")

        assert result["adx"] == "N/A"

    def test_bollinger_pband_present(self, tmp_path):
        """BB pband should be populated for a series >= 20 bars."""
        closes = [float(100 + (i % 5)) for i in range(60)]
        df = self._make_df(closes)
        df.to_parquet(tmp_path / "BB_TEST.parquet")

        with patch("ai_engine.HISTORICAL_DIR", tmp_path):
            result = AIPromptEngine()._get_technical_indicators("BB_TEST")

        assert result["bb_pband"] != "N/A", f"bb_pband should be set, got: {result['bb_pband']}"
        assert result["bb_wband"] != "N/A", f"bb_wband should be set, got: {result['bb_wband']}"

    def test_bollinger_above_upper_band_label(self, tmp_path):
        """A parabolic run should push close above the upper band (pband >= 1)."""
        # Steady price then a sudden spike well above band
        closes = [100.0] * 40 + [float(100 + i * 10) for i in range(20)]
        df = self._make_df(closes)
        df.to_parquet(tmp_path / "BB_ABOVE.parquet")

        with patch("ai_engine.HISTORICAL_DIR", tmp_path):
            result = AIPromptEngine()._get_technical_indicators("BB_ABOVE")

        assert "ABOVE" in result["bb_pband"] or "Near upper" in result["bb_pband"], (
            f"Expected upper-band label for parabolic close, got: {result['bb_pband']}"
        )

    def test_prompt_contains_adx_and_bb_lines(self):
        """generate_prompt output must include the ADX and Bollinger Band lines."""
        _seed_core_stock(CORE_TICKER)
        with (
            patch("ai_engine.get_rate_to_base", return_value=1.0),
            patch("ai_engine.get_latest_regime", return_value=None),
            patch("builtins.open", MagicMock(side_effect=FileNotFoundError)),
        ):
            result = ENGINE.generate_prompt(CORE_TICKER, "Quantamental Deep-Dive")
        assert "ADX Trend Strength" in result
        assert "Bollinger Band Position" in result
        assert "Bollinger Band Width" in result


# ─────────────────────────────────────────────────────────────────────────────
# Feature: X-ray portfolio risk data injection
# ─────────────────────────────────────────────────────────────────────────────

XRAY_TICKER = "AI_XRAY_TEST"


def _seed_xray_data(ticker: str):
    import database as db
    conn = db.get_connection()
    conn.execute(
        """INSERT OR REPLACE INTO xray_risk_cache
           (ticker, benchmark, last_updated, beta, annualized_vol)
           VALUES (?, 'SWDA.L', '2026-01-01 10:00:00', 1.25, 0.22)""",
        (ticker,),
    )
    # Minimal correlation matrix with 2 tickers
    import json as _json
    tickers = [ticker, "OTHER_TICKER"]
    matrix = [[1.0, 0.65], [0.65, 1.0]]
    conn.execute(
        """INSERT OR REPLACE INTO xray_correlation_matrix
           (benchmark, last_updated, tickers_json, matrix_json)
           VALUES ('SWDA.L', '2026-01-01 10:00:00', ?, ?)""",
        (_json.dumps(tickers), _json.dumps(matrix)),
    )
    conn.execute(
        """INSERT OR REPLACE INTO xray_dividend_cache
           (ticker, data_source, last_updated, dividend_yield_pct, dividend_in_base_currency)
           VALUES (?, 'ghostfolio', '2026-01-01 10:00:00', 1.5, 45.00)""",
        (ticker,),
    )
    conn.commit()
    conn.close()


class TestXRayContext:
    def test_no_data_returns_unavailable_message(self):
        result = ENGINE._get_xray_context("TICKER_WITH_NO_XRAY_DATA_XYZ")
        assert "not yet computed" in result.lower() or "unavailable" in result.lower()

    def test_beta_present_when_seeded(self):
        _seed_xray_data(XRAY_TICKER)
        result = ENGINE._get_xray_context(XRAY_TICKER)
        assert "Portfolio Beta" in result
        assert "1.250" in result

    def test_annualized_vol_present(self):
        _seed_xray_data(XRAY_TICKER)
        result = ENGINE._get_xray_context(XRAY_TICKER)
        assert "Annualised Vol" in result
        assert "22.0%" in result

    def test_correlations_present(self):
        _seed_xray_data(XRAY_TICKER)
        result = ENGINE._get_xray_context(XRAY_TICKER)
        assert "Top Portfolio Correlations" in result
        assert "OTHER_TICKER" in result

    def test_dividend_yield_present(self):
        _seed_xray_data(XRAY_TICKER)
        result = ENGINE._get_xray_context(XRAY_TICKER)
        assert "Dividend Yield" in result
        assert "1.50%" in result

    def test_prompt_contains_xray_section(self):
        _seed_core_stock(CORE_TICKER)
        with (
            patch("ai_engine.get_rate_to_base", return_value=1.0),
            patch("ai_engine.get_latest_regime", return_value=None),
            patch("builtins.open", MagicMock(side_effect=FileNotFoundError)),
        ):
            result = ENGINE.generate_prompt(CORE_TICKER, "Risk/Reward Audit")
        assert "X-RAY PORTFOLIO RISK METRICS" in result


# ─────────────────────────────────────────────────────────────────────────────
# Feature: Prompt-level TTL cache
# ─────────────────────────────────────────────────────────────────────────────

class TestPromptCache:
    def test_cache_hit_returns_same_object(self):
        """Second call with same ticker/mode on the same date must return the cached string."""
        engine = AIPromptEngine()
        _seed_core_stock(CORE_TICKER)
        with (
            patch("ai_engine.get_rate_to_base", return_value=1.0),
            patch("ai_engine.get_latest_regime", return_value=None),
            patch("builtins.open", MagicMock(side_effect=FileNotFoundError)),
        ):
            first = engine.generate_prompt(CORE_TICKER, "Quantamental Deep-Dive")
            second = engine.generate_prompt(CORE_TICKER, "Quantamental Deep-Dive")
        assert first is second, "Cached result must be the identical object"

    def test_different_mode_is_separate_cache_entry(self):
        engine = AIPromptEngine()
        _seed_core_stock(CORE_TICKER)
        with (
            patch("ai_engine.get_rate_to_base", return_value=1.0),
            patch("ai_engine.get_latest_regime", return_value=None),
            patch("builtins.open", MagicMock(side_effect=FileNotFoundError)),
        ):
            result_a = engine.generate_prompt(CORE_TICKER, "Risk/Reward Audit")
            result_b = engine.generate_prompt(CORE_TICKER, "Quantamental Deep-Dive")
        assert result_a != result_b

    def test_invalidate_cache_clears_entries(self):
        engine = AIPromptEngine()
        _seed_core_stock(CORE_TICKER)
        with (
            patch("ai_engine.get_rate_to_base", return_value=1.0),
            patch("ai_engine.get_latest_regime", return_value=None),
            patch("builtins.open", MagicMock(side_effect=FileNotFoundError)),
        ):
            engine.generate_prompt(CORE_TICKER, "Risk/Reward Audit")
        assert len(engine._prompt_cache) > 0
        engine.invalidate_cache()
        assert len(engine._prompt_cache) == 0

    def test_cache_invalidates_on_new_day(self):
        engine = AIPromptEngine()
        engine._cache_date = "2000-01-01"
        engine._prompt_cache[("FAKE", "mode", "2000-01-01")] = "old_result"
        # _cache_key with today's date should clear the stale cache
        engine._cache_key("FAKE", "mode")
        assert engine._prompt_cache == {}, "Cache should be cleared when date changes"


# ─────────────────────────────────────────────────────────────────────────────
# generate_prompt integration tests
# ─────────────────────────────────────────────────────────────────────────────

class TestGeneratePrompt:
    def test_unknown_ticker_returns_none(self):
        with (
            patch("ai_engine.get_rate_to_base", return_value=1.0),
            patch("ai_engine.get_latest_regime", return_value=None),
        ):
            result = ENGINE.generate_prompt("TICKER_DOES_NOT_EXIST_XYZ", "Quantamental Deep-Dive")
        assert result is None

    @pytest.mark.parametrize("mode", [
        "The Devil's Advocate analysis",
        "Risk/Reward Audit",
        "Quantamental Deep-Dive",
        "Earnings Strategy",
    ])
    def test_known_modes_return_string(self, mode):
        _seed_core_stock(CORE_TICKER)
        with (
            patch("ai_engine.get_rate_to_base", return_value=1.0),
            patch("ai_engine.get_latest_regime", return_value=None),
            patch("ai_engine.PORTFOLIO_PATH") as mock_path,
        ):
            mock_path.__str__ = lambda self: "/nonexistent/portfolio.json"
            mock_open = MagicMock(side_effect=FileNotFoundError)
            with patch("builtins.open", mock_open):
                result = ENGINE.generate_prompt(CORE_TICKER, mode)
        assert result is not None
        assert len(result) > 100

    def test_unknown_mode_returns_generic_prompt(self, caplog):
        _seed_core_stock(CORE_TICKER)
        import logging
        with (
            patch("ai_engine.get_rate_to_base", return_value=1.0),
            patch("ai_engine.get_latest_regime", return_value=None),
            patch("builtins.open", MagicMock(side_effect=FileNotFoundError)),
            caplog.at_level(logging.WARNING, logger="ai_engine"),
        ):
            result = ENGINE.generate_prompt(CORE_TICKER, "UNKNOWN_MODE_XYZ")
        assert result is not None
        assert len(result) > 100
        assert any("UNKNOWN_MODE_XYZ" in r.message for r in caplog.records), (
            "Expected a WARNING log mentioning the unknown mode name"
        )

    def test_prompt_contains_system_metadata_header(self):
        _seed_core_stock(CORE_TICKER)
        with (
            patch("ai_engine.get_rate_to_base", return_value=1.0),
            patch("ai_engine.get_latest_regime", return_value=None),
            patch("builtins.open", MagicMock(side_effect=FileNotFoundError)),
        ):
            result = ENGINE.generate_prompt(CORE_TICKER, "Quantamental Deep-Dive")
        assert "SYSTEM METADATA" in result
        assert "MACRO REGIME" in result
        assert "ASSET DATA" in result

    def test_prompt_contains_ticker_name(self):
        _seed_core_stock(CORE_TICKER)
        with (
            patch("ai_engine.get_rate_to_base", return_value=1.0),
            patch("ai_engine.get_latest_regime", return_value=None),
            patch("builtins.open", MagicMock(side_effect=FileNotFoundError)),
        ):
            result = ENGINE.generate_prompt(CORE_TICKER, "Risk/Reward Audit")
        assert CORE_TICKER in result
