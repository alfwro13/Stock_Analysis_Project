"""Tests for time_engine public API."""
import json
import pytest
from datetime import datetime, time as dtime, timezone
from unittest.mock import patch

import time_engine
from time_engine import (
    ticker_exchange,
    ticker_exchange_from_suffix,
    ticker_exchange_or_none,
    market_window_utc,
    is_market_open,
    is_trading_session,
    is_exchange_holiday,
    reset_cron_trigger_params,
    exchange_tz,
    to_local,
    fmt_time,
    fmt_datetime,
    fmt_reset_time,
    EXCHANGE_HOURS,
    _load_exchange_registry,
    reload_exchange_registry,
    _BUILTIN_EXCHANGE_HOURS,
)


# ---------------------------------------------------------------------------
# Helpers — fake datetime for DST pinning
# ---------------------------------------------------------------------------

def _fake_datetime(fixed_utc: datetime):
    """Return a datetime subclass whose .now() always returns fixed_utc."""
    class _Fake(datetime):
        @classmethod
        def now(cls, tz=None):
            return fixed_utc.astimezone(tz) if tz else fixed_utc
        @classmethod
        def combine(cls, *a, **kw):
            return datetime.combine(*a, **kw)
    return _Fake


_SUMMER_UTC = datetime(2026, 7, 15, 12, 0, 0, tzinfo=timezone.utc)  # EDT (UTC-4)
_WINTER_UTC = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)  # EST (UTC-5)


# ---------------------------------------------------------------------------
# ticker_exchange
# ---------------------------------------------------------------------------

class TestTickerExchange:
    def test_dot_l_suffix_returns_lse(self):
        assert ticker_exchange("SMGB.L") == "LSE"

    def test_gbp_currency_returns_lse(self):
        assert ticker_exchange("TLW", currency="GBP") == "LSE"

    def test_gbp_pence_currency_returns_lse(self):
        assert ticker_exchange("BATS", currency="GBp") == "LSE"

    def test_dot_de_suffix_returns_xetra(self):
        assert ticker_exchange("SAP.DE") == "XETRA"

    def test_eur_currency_returns_xetra(self):
        assert ticker_exchange("ADS", currency="EUR") == "XETRA"

    def test_dot_t_suffix_returns_tse(self):
        assert ticker_exchange("7203.T") == "TSE"

    def test_usd_currency_returns_nyse(self):
        assert ticker_exchange("AAPL", currency="USD") == "NYSE"

    def test_ambiguous_falls_back_to_home_exchange(self):
        with patch("time_engine._load_config", return_value={"HOME_EXCHANGE": "LSE"}):
            assert ticker_exchange("UNKNOWN") == "LSE"

    def test_ambiguous_uses_fallback_when_no_home_exchange(self):
        with patch("time_engine._load_config", return_value={}):
            assert ticker_exchange("UNKNOWN") == "NYSE"  # _FALLBACK_EXCHANGE


class TestTickerExchangeFromSuffix:
    """Suffix-only variant — never falls back to HOME_EXCHANGE; used where crediting an
    unrecognised ticker to the operator's home exchange would be wrong (e.g. ETF constituent
    exchange inference in etf_predictor_engine.py)."""

    def test_dot_l_suffix_returns_lse(self):
        assert ticker_exchange_from_suffix("SMGB.L") == "LSE"

    def test_krx_suffix_returns_krx(self):
        assert ticker_exchange_from_suffix("005930.KS") == "KRX"

    def test_plain_ticker_defaults_to_nyse(self):
        assert ticker_exchange_from_suffix("AAPL") == "NYSE"

    def test_unrecognised_suffix_defaults_to_nyse(self):
        assert ticker_exchange_from_suffix("XYZ.UNKNOWN") == "NYSE"


class TestTickerExchangeOrNone:
    """Suffix-only, null-preserving variant — used by markets_engine.resolve_benchmark_for_holdings
    so an unrecognised suffix is skipped rather than silently misattributed to NYSE."""

    def test_dot_l_suffix_returns_lse(self):
        assert ticker_exchange_or_none("SMGB.L") == "LSE"

    def test_krx_suffix_returns_krx(self):
        assert ticker_exchange_or_none("005930.KS") == "KRX"

    def test_unrecognised_suffix_returns_none(self):
        assert ticker_exchange_or_none("XYZ.UNKNOWN") is None

    def test_plain_ticker_with_no_dot_returns_none(self):
        # Deliberately does NOT default plain tickers to NYSE — the caller (markets_engine)
        # applies that convention itself so it isn't silently baked into this primitive.
        assert ticker_exchange_or_none("AAPL") is None


# ---------------------------------------------------------------------------
# market_window_utc — invariants and DST correctness
# ---------------------------------------------------------------------------

class TestMarketWindowUtc:
    def test_open_before_close_for_all_exchanges(self):
        for exchange in EXCHANGE_HOURS:
            open_utc, close_utc = market_window_utc(exchange)
            assert open_utc < close_utc, f"{exchange}: open >= close"

    def test_nyse_summer_open_is_1330_utc(self):
        with patch("time_engine.datetime", _fake_datetime(_SUMMER_UTC)):
            open_utc, _ = market_window_utc("NYSE")
        assert open_utc == dtime(13, 30)

    def test_nyse_summer_close_is_2000_utc(self):
        with patch("time_engine.datetime", _fake_datetime(_SUMMER_UTC)):
            _, close_utc = market_window_utc("NYSE")
        assert close_utc == dtime(20, 0)

    def test_nyse_winter_open_is_1430_utc(self):
        with patch("time_engine.datetime", _fake_datetime(_WINTER_UTC)):
            open_utc, _ = market_window_utc("NYSE")
        assert open_utc == dtime(14, 30)

    def test_nyse_premarket_open_is_earlier_than_regular(self):
        open_regular, _ = market_window_utc("NYSE", include_premarket=False)
        open_premarket, _ = market_window_utc("NYSE", include_premarket=True)
        assert open_premarket < open_regular

    def test_lse_no_premarket_key_so_include_premarket_has_no_effect(self):
        open_regular, _ = market_window_utc("LSE", include_premarket=False)
        open_premarket, _ = market_window_utc("LSE", include_premarket=True)
        assert open_regular == open_premarket

    def test_unknown_exchange_falls_back_to_nyse(self):
        open_utc, close_utc = market_window_utc("INVALID_EXCHANGE")
        nyse_open, nyse_close = market_window_utc("NYSE")
        assert open_utc == nyse_open
        assert close_utc == nyse_close


# ---------------------------------------------------------------------------
# is_market_open
# ---------------------------------------------------------------------------

class TestIsMarketOpen:
    def test_returns_true_during_nyse_session(self):
        # 17:00 UTC in summer = 13:00 EDT — mid NYSE session
        mid_session = datetime(2026, 7, 15, 17, 0, 0, tzinfo=timezone.utc)
        with patch("time_engine.datetime", _fake_datetime(mid_session)):
            assert is_market_open("NYSE") is True

    def test_returns_false_after_nyse_close(self):
        # 22:00 UTC in summer = 18:00 EDT — after NYSE close at 20:00 UTC
        after_close = datetime(2026, 7, 15, 22, 0, 0, tzinfo=timezone.utc)
        with patch("time_engine.datetime", _fake_datetime(after_close)):
            assert is_market_open("NYSE") is False

    def test_returns_false_before_nyse_open(self):
        # 10:00 UTC in summer = 06:00 EDT — before NYSE open at 13:30 UTC
        pre_open = datetime(2026, 7, 15, 10, 0, 0, tzinfo=timezone.utc)
        with patch("time_engine.datetime", _fake_datetime(pre_open)):
            assert is_market_open("NYSE") is False

    def test_returns_bool(self):
        assert isinstance(is_market_open("NYSE"), bool)


# ---------------------------------------------------------------------------
# is_exchange_holiday
# ---------------------------------------------------------------------------

class TestIsExchangeHoliday:
    def teardown_method(self):
        # Each test may populate the module-level calendar cache with a mocked entry.
        time_engine._calendar_cache.pop("BOGUS_EXCHANGE", None)
        time_engine._uncovered_calendar_warned.discard("BOGUS_EXCHANGE")

    def test_new_years_day_is_a_holiday(self):
        # 2026-01-01 is a Thursday (weekday), NYSE closed for New Year's Day
        new_years = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
        with patch("time_engine.datetime", _fake_datetime(new_years)):
            assert is_exchange_holiday("NYSE") is True

    def test_july_4th_observed_friday_is_a_holiday(self):
        # 2026-07-04 falls on a Saturday; NYSE observes it Friday 2026-07-03 (AGENTS.md example)
        observed = datetime(2026, 7, 3, 15, 0, 0, tzinfo=timezone.utc)
        with patch("time_engine.datetime", _fake_datetime(observed)):
            assert is_exchange_holiday("NYSE") is True

    def test_ordinary_weekday_is_not_a_holiday(self):
        with patch("time_engine.datetime", _fake_datetime(_SUMMER_UTC)):
            assert is_exchange_holiday("NYSE") is False

    def test_lse_holiday_uses_its_own_calendar(self):
        # 2026-01-01 is also a UK bank holiday (New Year's Day)
        new_years = datetime(2026, 1, 1, 11, 0, 0, tzinfo=timezone.utc)
        with patch("time_engine.datetime", _fake_datetime(new_years)):
            assert is_exchange_holiday("LSE") is True

    def test_unmapped_exchange_fails_open_and_warns_once(self, caplog):
        with caplog.at_level("WARNING", logger="time_engine"):
            assert is_exchange_holiday("BOGUS_EXCHANGE") is False
            assert is_exchange_holiday("BOGUS_EXCHANGE") is False
        warnings = [r for r in caplog.records if "BOGUS_EXCHANGE" in r.getMessage()]
        assert len(warnings) == 1

    def test_none_exchange_uses_home_exchange_config(self):
        with patch("time_engine._load_config", return_value={"HOME_EXCHANGE": "NYSE"}):
            with patch("time_engine.datetime", _fake_datetime(_SUMMER_UTC)):
                assert is_exchange_holiday(None) is False


class TestIsMarketOpenHolidayVeto:
    def test_holiday_overrides_otherwise_open_hours(self):
        # 15:00 UTC on 2026-01-01 (Thursday) = 10:00 EST, mid NYSE session hours were it a
        # trading day — but it's New Year's Day, so is_market_open must still return False.
        new_years_mid_session = datetime(2026, 1, 1, 15, 0, 0, tzinfo=timezone.utc)
        with patch("time_engine.datetime", _fake_datetime(new_years_mid_session)):
            assert is_market_open("NYSE") is False


# ---------------------------------------------------------------------------
# reset_cron_trigger_params
# ---------------------------------------------------------------------------

class TestResetCronTriggerParams:
    def test_nyse_fires_at_1605_et(self):
        params = reset_cron_trigger_params("NYSE")
        # NYSE close 16:00 ET + 5 min = 16:05 ET
        assert params["hour"] == 16
        assert params["minute"] == 5

    def test_lse_fires_at_1635_london(self):
        params = reset_cron_trigger_params("LSE")
        # LSE close 16:30 London + 5 min = 16:35 London
        assert params["hour"] == 16
        assert params["minute"] == 35

    def test_timezone_is_exchange_tz(self):
        assert reset_cron_trigger_params("NYSE")["timezone"] == "America/New_York"
        assert reset_cron_trigger_params("LSE")["timezone"] == "Europe/London"

    def test_day_of_week_is_weekdays(self):
        assert reset_cron_trigger_params("NYSE")["day_of_week"] == "mon-fri"

    def test_none_exchange_uses_home_exchange_config(self):
        with patch("time_engine._load_config", return_value={"HOME_EXCHANGE": "LSE"}):
            params = reset_cron_trigger_params(None)
        assert params["timezone"] == "Europe/London"


# ---------------------------------------------------------------------------
# _load_exchange_registry — self-healing fallback
# ---------------------------------------------------------------------------

class TestLoadExchangeRegistry:

    def _reset_cache(self):
        """Force next _load_exchange_registry() call to re-read from disk."""
        orig = time_engine._registry_cache
        time_engine._registry_cache = None
        return orig

    def test_missing_file_falls_back_to_builtin(self, tmp_path):
        with patch("time_engine._EXCHANGE_HOURS_PATH", str(tmp_path / "nonexistent.json")):
            saved = self._reset_cache()
            try:
                result = _load_exchange_registry()
            finally:
                time_engine._registry_cache = saved
        assert "NYSE" in result
        assert result == _BUILTIN_EXCHANGE_HOURS

    def test_malformed_json_falls_back_to_builtin(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text("NOT JSON {{{")
        with patch("time_engine._EXCHANGE_HOURS_PATH", str(bad)):
            saved = self._reset_cache()
            try:
                result = _load_exchange_registry()
            finally:
                time_engine._registry_cache = saved
        assert result == _BUILTIN_EXCHANGE_HOURS

    def test_valid_json_file_is_loaded(self, tmp_path):
        custom = {"CUSTOM_X": {"open": "10:00", "close": "18:00", "tz": "UTC"}}
        p = tmp_path / "exchange_hours.json"
        p.write_text(json.dumps(custom))
        with patch("time_engine._EXCHANGE_HOURS_PATH", str(p)):
            saved = self._reset_cache()
            try:
                result = _load_exchange_registry()
            finally:
                time_engine._registry_cache = saved
        assert "CUSTOM_X" in result

    def test_reload_exchange_registry_invalidates_suffix_cache(self, tmp_path):
        custom = {
            "CUSTOM_EXCH": {
                "open": "09:00", "close": "17:00", "tz": "UTC",
                "currency": "XXX", "suffixes": [".XX"],
            }
        }
        p = tmp_path / "exchange_hours.json"
        p.write_text(json.dumps(custom))
        # Reload so the new registry and suffix map are active
        with patch("time_engine._EXCHANGE_HOURS_PATH", str(p)):
            reload_exchange_registry()
            result = ticker_exchange("TICKER.XX")
        # Reload back to real file for subsequent tests
        reload_exchange_registry()
        assert result == "CUSTOM_EXCH"


# ---------------------------------------------------------------------------
# ticker_exchange — multi-part suffix matching
# ---------------------------------------------------------------------------

class TestTickerExchangeMultiSuffix:

    def test_four_char_suffix_matched(self):
        # Inject a 4-char suffix directly into the suffix map for isolation
        fake_map = {".TWOO": "CUSTOM_EX", **{k: v for k, v in time_engine._SUFFIX_TO_EXCHANGE.items()}}
        with patch("time_engine._suffix_cache", fake_map):
            assert ticker_exchange("TICKER.TWOO") == "CUSTOM_EX"

    def test_two_char_suffix_still_works(self):
        # .DE is 2 chars — must still resolve via new length-priority loop
        assert ticker_exchange("SAP.DE") == "XETRA"

    def test_unknown_suffix_falls_through_to_currency(self):
        # .ZZ not in registry → should fall through to currency USD → NYSE
        assert ticker_exchange("FOO.ZZ", currency="USD") == "NYSE"


# ---------------------------------------------------------------------------
# is_trading_session
# ---------------------------------------------------------------------------

class TestIsTradingSession:
    def test_returns_false_on_saturday(self):
        # 2026-06-13 is a Saturday; 15:00 UTC is within NYSE summer hours
        saturday_mid_session = datetime(2026, 6, 13, 15, 0, 0, tzinfo=timezone.utc)
        with patch("time_engine.datetime", _fake_datetime(saturday_mid_session)):
            assert is_trading_session("NYSE") is False

    def test_returns_false_on_sunday(self):
        sunday_mid_session = datetime(2026, 6, 14, 15, 0, 0, tzinfo=timezone.utc)
        with patch("time_engine.datetime", _fake_datetime(sunday_mid_session)):
            assert is_trading_session("NYSE") is False

    def test_returns_true_weekday_during_session(self):
        # 2026-06-11 is a Thursday; 17:00 UTC is mid-NYSE-session in summer
        thursday_mid = datetime(2026, 6, 11, 17, 0, 0, tzinfo=timezone.utc)
        with patch("time_engine.datetime", _fake_datetime(thursday_mid)):
            assert is_trading_session("NYSE") is True

    def test_returns_false_weekday_outside_session(self):
        # 2026-06-11 Thursday; 10:00 UTC is before NYSE opens in summer
        thursday_pre = datetime(2026, 6, 11, 10, 0, 0, tzinfo=timezone.utc)
        with patch("time_engine.datetime", _fake_datetime(thursday_pre)):
            assert is_trading_session("NYSE") is False

    def test_returns_bool(self):
        assert isinstance(is_trading_session("NYSE"), bool)


# ---------------------------------------------------------------------------
# exchange_tz
# ---------------------------------------------------------------------------

class TestExchangeTz:
    def test_nyse_returns_america_new_york(self):
        assert exchange_tz("NYSE") == "America/New_York"

    def test_lse_returns_europe_london(self):
        assert exchange_tz("LSE") == "Europe/London"

    def test_xetra_returns_europe_berlin(self):
        assert exchange_tz("XETRA") == "Europe/Berlin"

    def test_tse_returns_asia_tokyo(self):
        assert exchange_tz("TSE") == "Asia/Tokyo"

    def test_unknown_exchange_falls_back_to_nyse_tz(self):
        # Unrecognised exchange → fallback is NYSE
        assert exchange_tz("BOGUS") == "America/New_York"


# ---------------------------------------------------------------------------
# to_local / fmt_time / fmt_datetime
# ---------------------------------------------------------------------------

_UTC_NOON = datetime(2026, 1, 15, 12, 0, 0, tzinfo=timezone.utc)  # 12:00 UTC, winter


class TestToLocal:
    def test_aware_utc_converted_to_user_tz(self):
        _UTC_CONFIG = {"USER_TIMEZONE": "Europe/London"}
        with patch("time_engine._load_config", return_value=_UTC_CONFIG):
            local = to_local(_UTC_NOON)
        # Europe/London in January is UTC+0, so hour should be 12
        assert local.hour == 12
        assert local.tzinfo is not None

    def test_naive_datetime_treated_as_utc(self):
        naive = datetime(2026, 1, 15, 12, 0, 0)
        _UTC_CONFIG = {"USER_TIMEZONE": "America/New_York"}
        with patch("time_engine._load_config", return_value=_UTC_CONFIG):
            local = to_local(naive)
        # EST is UTC-5; noon UTC → 07:00 EST in January
        assert local.hour == 7

    def test_returns_aware_datetime(self):
        _UTC_CONFIG = {"USER_TIMEZONE": "Europe/London"}
        with patch("time_engine._load_config", return_value=_UTC_CONFIG):
            local = to_local(_UTC_NOON)
        assert local.tzinfo is not None


class TestFmtTime:
    def test_returns_string_with_time_and_tz_abbreviation(self):
        _UTC_CONFIG = {"USER_TIMEZONE": "Europe/London"}
        with patch("time_engine._load_config", return_value=_UTC_CONFIG):
            result = fmt_time(_UTC_NOON)
        # Should look like "12:00 GMT" in January
        assert ":" in result
        assert len(result) > 4

    def test_format_pattern_hhmm_tz(self):
        _UTC_CONFIG = {"USER_TIMEZONE": "Europe/London"}
        with patch("time_engine._load_config", return_value=_UTC_CONFIG):
            result = fmt_time(_UTC_NOON)
        parts = result.split(" ")
        assert len(parts) == 2
        assert ":" in parts[0]


class TestFmtDatetime:
    def test_includes_date_time_and_tz(self):
        _UTC_CONFIG = {"USER_TIMEZONE": "Europe/London"}
        with patch("time_engine._load_config", return_value=_UTC_CONFIG):
            result = fmt_datetime(_UTC_NOON)
        # Format: "2026-01-15 12:00 GMT"
        assert result.startswith("2026-01-15")
        assert "12:00" in result

    def test_returns_string(self):
        _UTC_CONFIG = {"USER_TIMEZONE": "Europe/London"}
        with patch("time_engine._load_config", return_value=_UTC_CONFIG):
            result = fmt_datetime(_UTC_NOON)
        assert isinstance(result, str)


# ---------------------------------------------------------------------------
# fmt_reset_time
# ---------------------------------------------------------------------------

class TestFmtResetTime:
    def test_returns_string(self):
        _UTC_CONFIG = {"USER_TIMEZONE": "Europe/London", "HOME_EXCHANGE": "NYSE"}
        with patch("time_engine._load_config", return_value=_UTC_CONFIG):
            result = fmt_reset_time("NYSE")
        assert isinstance(result, str)

    def test_contains_colon(self):
        _UTC_CONFIG = {"USER_TIMEZONE": "Europe/London", "HOME_EXCHANGE": "NYSE"}
        with patch("time_engine._load_config", return_value=_UTC_CONFIG):
            result = fmt_reset_time("NYSE")
        assert ":" in result

    def test_lse_reset_is_after_close(self):
        # LSE closes at 16:30; reset fires at 16:35 Europe/London
        # In UTC+0 (winter) this should be "16:35 GMT"
        _UTC_CONFIG = {"USER_TIMEZONE": "Europe/London", "HOME_EXCHANGE": "LSE"}
        with patch("time_engine._load_config", return_value=_UTC_CONFIG):
            result = fmt_reset_time("LSE")
        assert "16:35" in result
