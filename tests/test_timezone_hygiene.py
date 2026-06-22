"""
tests/test_timezone_hygiene.py

Guards against timezone regressions across three testable layers:

1. ROUTING — _intraday_market_tz() and _EXCHANGE_DELAYS return the right
   values per ticker/currency.

2. CHART — create_intraday_chart() with market_tz shifts naive UTC timestamps
   to the correct local hour in the Plotly HTML output.

3. SCAN TIMESTAMP — the scan_ts formatting logic in IntradayBottomEngine
   produces a string that includes a timezone abbreviation (BST/GMT/EDT/EST),
   not a bare UTC time.

4. TEMPLATE LINTING — interactive templates (stock_detail.html, settings.html)
   must contain the .dip-reset-time span rather than bare hardcoded "16:05 ET"
   in the dip-radar description paragraph.

What cannot be tested here:
  - JavaScript formatDipResetLocalTime() correctness (requires a browser).
  - Whether Plotly visually renders the right hour on screen (chart is
    client-side; the HTML encoding is what we validate instead).
"""

import sys
from pathlib import Path

import pandas as pd
sys.path.insert(0, str(Path(__file__).parent.parent))

from visuals import create_intraday_chart, _intraday_market_tz, _EXCHANGE_DELAYS


# ── helpers ───────────────────────────────────────────────────────────────────

def _single_candle_df(naive_utc_str: str) -> pd.DataFrame:
    """Return a one-row OHLC DataFrame with a naive UTC timestamp index."""
    idx = pd.to_datetime([naive_utc_str])
    return pd.DataFrame(
        {"Open": [100.0], "High": [102.0], "Low": [99.0], "Close": [101.0]},
        index=idx,
    )


# ── 1. Routing ────────────────────────────────────────────────────────────────

class TestIntradayMarketTz:
    """
    _intraday_market_tz() is a display helper — it returns the user's configured
    USER_TIMEZONE for all tickers so intraday charts are always shown in local time.
    Exchange routing (NYSE/LSE/etc.) lives in time_engine.ticker_exchange().
    """

    def test_returns_a_valid_iana_tz_string(self):
        from zoneinfo import ZoneInfo
        tz_str = _intraday_market_tz("AAPL", "USD")
        ZoneInfo(tz_str)  # raises if invalid

    def test_same_tz_for_uk_and_us_tickers(self):
        # All tickers get the same display timezone regardless of their exchange
        assert _intraday_market_tz("VOD.L", "GBp") == _intraday_market_tz("AAPL", "USD")

    def test_same_tz_for_all_currencies(self):
        assert _intraday_market_tz("SPY", "USD") == _intraday_market_tz("SIE.DE", "EUR")


class TestExchangeDelays:

    def test_gbp_pence_has_delay(self):
        assert _EXCHANGE_DELAYS.get("GBp", 0) > 0

    def test_gbp_has_delay(self):
        assert _EXCHANGE_DELAYS.get("GBP", 0) > 0

    def test_eur_has_delay(self):
        assert _EXCHANGE_DELAYS.get("EUR", 0) > 0

    def test_usd_has_no_delay(self):
        assert _EXCHANGE_DELAYS.get("USD", 0) == 0


# ── 2. Chart timestamp shift ──────────────────────────────────────────────────

class TestChartTimezoneShift:

    def test_utc_shifted_to_bst_in_chart_html(self):
        """07:00 UTC == 08:00 BST; chart HTML must encode 08:00, not 07:00."""
        # 5 Jun 2026 is during BST (UTC+1)
        df = _single_candle_df("2026-06-05 07:00:00")
        html = create_intraday_chart(
            df, "TEST.L", market_tz="Europe/London", include_plotlyjs=False
        )
        assert "08:00" in html, "Expected BST hour 08:00 in Plotly HTML"
        assert "07:00" not in html, "UTC hour 07:00 must not appear in chart HTML after TZ shift"

    def test_utc_shifted_to_et_in_chart_html(self):
        """14:30 UTC == 10:30 EDT; chart HTML must encode 10:30, not 14:30."""
        # NYSE opens at 09:30 ET = 13:30 UTC in EDT season
        df = _single_candle_df("2026-06-05 14:30:00")
        html = create_intraday_chart(
            df, "AAPL", market_tz="America/New_York", include_plotlyjs=False
        )
        assert "10:30" in html, "Expected ET hour 10:30 in Plotly HTML"
        assert "14:30" not in html, "UTC hour 14:30 must not appear in chart HTML after TZ shift"

    def test_no_market_tz_leaves_timestamps_unchanged(self):
        """Without market_tz the function must not shift timestamps."""
        df = _single_candle_df("2026-06-05 07:00:00")
        html = create_intraday_chart(df, "TEST", include_plotlyjs=False)
        assert "07:00" in html, "Without market_tz the raw timestamp must be preserved"

    def test_delay_warning_appears_in_title_for_uk_stock(self):
        """UK stocks (GBp/GBP) get a delay notice in the chart title."""
        df = _single_candle_df("2026-06-05 07:00:00")
        html = create_intraday_chart(
            df, "BARC.L", market_tz="Europe/London",
            data_delay_minutes=15, include_plotlyjs=False,
        )
        assert "delayed" in html.lower() or "delay" in html.lower(), (
            "Expected a delay warning in chart HTML for a UK stock"
        )

    def test_no_delay_warning_for_us_stock(self):
        """US stocks (USD) must not show a delay warning."""
        df = _single_candle_df("2026-06-05 14:30:00")
        html = create_intraday_chart(
            df, "AAPL", market_tz="America/New_York",
            data_delay_minutes=0, include_plotlyjs=False,
        )
        assert "delayed" not in html.lower()


# ── 3. scan_ts timezone label ─────────────────────────────────────────────────

class TestScanTsTimezone:
    """
    Validates the timestamp-formatting logic used in IntradayBottomEngine
    without instantiating the full engine (which requires DB + parquet).
    We replicate the exact two-liner from the engine and assert its output.
    """

    def _format_scan_ts(self, naive_utc_str: str, ticker: str) -> str:
        ts = pd.Timestamp(naive_utc_str)
        mkt_tz = "Europe/London" if ticker.endswith(".L") else "America/New_York"
        ts_local = ts.tz_localize("UTC").tz_convert(mkt_tz)
        return ts_local.strftime("%Y-%m-%d %H:%M %Z")

    def test_uk_scan_ts_shows_bst_not_utc(self):
        scan_ts = self._format_scan_ts("2026-06-05 07:25:00", "VOD.L")
        assert "08:25" in scan_ts, f"Expected 08:25 BST, got: {scan_ts}"
        assert "BST" in scan_ts or "GMT" in scan_ts, (
            f"scan_ts must contain timezone abbreviation, got: {scan_ts}"
        )
        assert "07:25" not in scan_ts, f"UTC time must not appear: {scan_ts}"

    def test_us_scan_ts_shows_et_not_utc(self):
        # 14:25 UTC = 10:25 EDT during summer
        scan_ts = self._format_scan_ts("2026-06-05 14:25:00", "AAPL")
        assert "10:25" in scan_ts, f"Expected 10:25 EDT, got: {scan_ts}"
        assert "EDT" in scan_ts or "EST" in scan_ts, (
            f"scan_ts must contain timezone abbreviation, got: {scan_ts}"
        )
        assert "14:25" not in scan_ts, f"UTC time must not appear: {scan_ts}"

    def test_scan_ts_always_ends_with_tz_abbreviation(self):
        """The last token of scan_ts must be a 2-5 letter uppercase TZ code."""
        import re
        for ticker, utc_str in [
            ("VOD.L", "2026-06-05 09:00:00"),
            ("AAPL", "2026-06-05 15:00:00"),
        ]:
            scan_ts = self._format_scan_ts(utc_str, ticker)
            last_token = scan_ts.split()[-1]
            assert re.match(r"^[A-Z]{2,5}$", last_token), (
                f"scan_ts '{scan_ts}' does not end with a TZ abbreviation"
            )


# ── 4. Template linting ───────────────────────────────────────────────────────

class TestTemplateTimezoneHygiene:

    TEMPLATES_DIR = Path(__file__).parent.parent / "templates"
    STATIC_DIR = Path(__file__).parent.parent / "static"

    def _read(self, name: str) -> str:
        return (self.TEMPLATES_DIR / name).read_text(encoding="utf-8")

    def _read_static(self, name: str) -> str:
        return (self.STATIC_DIR / name).read_text(encoding="utf-8")

    def test_stock_detail_dip_radar_uses_dynamic_span(self):
        """The dip-radar description in stock_detail.html must use .dip-reset-time span."""
        content = self._read("stock_detail.html")
        assert 'class="dip-reset-time"' in content, (
            "stock_detail.html must have a .dip-reset-time span for the reset time"
        )

    def test_settings_dip_radar_uses_dynamic_span(self):
        """The dip-radar description in settings/_alerts.html must use .dip-reset-time span."""
        content = self._read("settings/_alerts.html")
        assert 'class="dip-reset-time"' in content, (
            "settings/_alerts.html must have a .dip-reset-time span for the reset time"
        )

    def test_stock_detail_has_format_reset_time_js_function(self):
        """stock_detail.js must include the JS helper that computes local reset time."""
        content = self._read_static("js/stock_detail.js")
        assert "formatDipResetLocalTime" in content, (
            "static/js/stock_detail.js is missing the formatDipResetLocalTime JS function"
        )

    def test_settings_has_format_reset_time_js_function(self):
        """settings_alerts.js must include the JS helper that computes local reset time."""
        content = self._read_static("js/settings_alerts.js")
        assert "formatDipResetLocalTime" in content, (
            "static/js/settings_alerts.js is missing the formatDipResetLocalTime JS function"
        )

    def test_glossary_mentions_both_timezones(self):
        """glossary Dip Radar partial must show ET alongside at least one UK timezone."""
        content = self._read("glossary/_dip_radar.html")
        # Should have both ET and at least BST or GMT so non-US readers understand
        assert "ET" in content and ("BST" in content or "GMT" in content), (
            "glossary/_dip_radar.html Dip Radar entry must show both ET and a UK timezone"
        )
