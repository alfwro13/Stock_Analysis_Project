"""
tests/test_market_pulse_read.py — MARKET PULSE READ FUNCTIONS

Covers get_cached_pulse_from_db() and get_all_cached_pulse():
  - index/asset split is determined by INDEX_TICKERS membership
  - de-duplication: same ticker in portfolio and watchlist appears once in assets
  - staleness flag: is_stale only when age > max(refresh_rate * 2, 300s floor) AND market is open
  - sentinel row for missing cache entry (price=0, is_stale=True when market open)
  - IGNORED_TICKERS filtering
  - needs_refresh flag: True when age > refresh_rate AND market is open
  - closed-market: is_stale=False, needs_refresh=False even for old data
"""

import sys
import time
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db
import market_pulse as _mp


# ── helpers ───────────────────────────────────────────────────────────────────

INDEX_TICKER = "^GSPC"    # always in INDEX_TICKERS
ASSET_TICKER = "_MPR_TEST_ASSET"


def _conn():
    import sqlite3
    conn = sqlite3.connect(_db.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_pulse(ticker: str, price: float = 100.0, change_pts: float = 1.0,
                change_pct: float = 1.0, is_positive: int = 1,
                last_updated: float | None = None):
    if last_updated is None:
        last_updated = time.time()
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO market_pulse_cache "
        "(ticker, name, price, change_pts, change_pct, is_positive, last_updated) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ticker, f"Name_{ticker}", price, change_pts, change_pct, is_positive, last_updated),
    )
    c.commit()
    c.close()


def _seed_quant_signal(ticker: str, score: float, date: str = "2026-06-07"):
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO quant_signals "
        "(ticker, date, sentiment_score) VALUES (?, ?, ?)",
        (ticker, date, score),
    )
    c.commit()
    c.close()


def _clear(*tickers):
    c = _conn()
    for t in tickers:
        c.execute("DELETE FROM market_pulse_cache WHERE ticker = ?", (t,))
        c.execute("DELETE FROM quant_signals WHERE ticker = ?", (t,))
    c.commit()
    c.close()


# ── _select_active_pulse_tickers ────────────────────────────────────────────────

class TestSelectActivePulseTickers:
    LEGACY_TEN = {"^FTSE", "^FTMC", "GBPUSD=X", "BZ=F", "UK10YG",
                  "^GSPC", "^NDX", "^TYX", "^TNX", "DX-Y.NYB"}

    def test_static_mode_returns_pulse_tile_set(self):
        result = _mp._select_active_pulse_tickers({"UI_PREFERENCES": {"MARKET_PULSE_DYNAMIC": False}})
        assert set(result.keys()) == self.LEGACY_TEN

    def test_static_mode_respects_desktop_count_cap(self):
        result = _mp._select_active_pulse_tickers({
            "UI_PREFERENCES": {"MARKET_PULSE_DYNAMIC": False, "MARKET_PULSE_DESKTOP_COUNT": 3}
        })
        assert len(result) == 3

    def test_dynamic_mode_delegates_to_markets_engine(self):
        with patch("markets_engine.select_pulse_tickers", return_value={"desktop": ["^GSPC", "GC=F"], "mobile": ["^GSPC"]}) as mock_select:
            result = _mp._select_active_pulse_tickers({
                "UI_PREFERENCES": {"MARKET_PULSE_DYNAMIC": True, "MARKET_PULSE_DESKTOP_COUNT": 2, "MARKET_PULSE_MOBILE_COUNT": 1}
            })
        mock_select.assert_called_once_with(dynamic=True, desktop_count=2, mobile_count=1)
        assert set(result.keys()) == {"^GSPC", "GC=F"}

    def test_dynamic_mode_falls_back_to_static_on_error(self):
        with patch("markets_engine.select_pulse_tickers", side_effect=RuntimeError("boom")):
            result = _mp._select_active_pulse_tickers({"UI_PREFERENCES": {"MARKET_PULSE_DYNAMIC": True}})
        assert set(result.keys()) == self.LEGACY_TEN

    def test_default_config_missing_ui_preferences_uses_static(self):
        result = _mp._select_active_pulse_tickers({})
        assert set(result.keys()) == self.LEGACY_TEN


# ── get_cached_pulse_from_db ──────────────────────────────────────────────────

class TestGetCachedPulseFromDb:
    def teardown_method(self):
        _clear(ASSET_TICKER, INDEX_TICKER)

    def test_known_index_ticker_lands_in_indexes(self):
        _seed_pulse(INDEX_TICKER)
        result = _mp.get_cached_pulse_from_db([], refresh_rate=60)
        index_tickers = [r["ticker"] for r in result["indexes"]]
        assert INDEX_TICKER in index_tickers

    def test_custom_asset_ticker_lands_in_assets(self):
        _seed_pulse(ASSET_TICKER)
        result = _mp.get_cached_pulse_from_db([ASSET_TICKER], refresh_rate=60)
        asset_tickers = [r["ticker"] for r in result["assets"]]
        assert ASSET_TICKER in asset_tickers

    def test_custom_asset_not_in_indexes(self):
        _seed_pulse(ASSET_TICKER)
        result = _mp.get_cached_pulse_from_db([ASSET_TICKER], refresh_rate=60)
        index_tickers = [r["ticker"] for r in result["indexes"]]
        assert ASSET_TICKER not in index_tickers

    def test_is_stale_false_when_fresh(self):
        _seed_pulse(ASSET_TICKER, last_updated=time.time())
        result = _mp.get_cached_pulse_from_db([ASSET_TICKER], refresh_rate=60)
        asset = next(r for r in result["assets"] if r["ticker"] == ASSET_TICKER)
        assert asset["is_stale"] is False

    def test_is_stale_true_when_old(self):
        _seed_pulse(ASSET_TICKER, last_updated=time.time() - 301)  # beyond the 5-minute floor
        with patch("market_pulse.is_trading_session", return_value=True):
            result = _mp.get_cached_pulse_from_db([ASSET_TICKER], refresh_rate=60)
        asset = next(r for r in result["assets"] if r["ticker"] == ASSET_TICKER)
        assert asset["is_stale"] is True

    def test_missing_cache_entry_produces_stale_sentinel(self):
        _clear(ASSET_TICKER)
        with patch("market_pulse.is_trading_session", return_value=True):
            result = _mp.get_cached_pulse_from_db([ASSET_TICKER], refresh_rate=60)
        asset = next((r for r in result["assets"] if r["ticker"] == ASSET_TICKER), None)
        assert asset is not None
        assert asset["price"] == 0.0
        assert asset["is_stale"] is True

    def test_deduplication_same_ticker_twice(self):
        _seed_pulse(ASSET_TICKER)
        # Pass the same ticker twice — should appear only once in assets
        result = _mp.get_cached_pulse_from_db([ASSET_TICKER, ASSET_TICKER], refresh_rate=60)
        asset_tickers = [r["ticker"] for r in result["assets"]]
        assert asset_tickers.count(ASSET_TICKER) == 1

    def test_sentiment_score_merged_when_present(self):
        _seed_pulse(ASSET_TICKER)
        _seed_quant_signal(ASSET_TICKER, score=0.72)
        result = _mp.get_cached_pulse_from_db([ASSET_TICKER], refresh_rate=60)
        asset = next(r for r in result["assets"] if r["ticker"] == ASSET_TICKER)
        assert asset["sentiment_score"] == pytest.approx(0.72, abs=0.001)

    def test_sentiment_score_none_when_absent(self):
        _seed_pulse(ASSET_TICKER)
        result = _mp.get_cached_pulse_from_db([ASSET_TICKER], refresh_rate=60)
        asset = next(r for r in result["assets"] if r["ticker"] == ASSET_TICKER)
        assert asset["sentiment_score"] is None

    def test_ignored_ticker_excluded_from_assets(self):
        _seed_pulse(ASSET_TICKER)
        config_patch = {"IGNORED_TICKERS": [ASSET_TICKER], "UI_PREFERENCES": {}}
        with patch("market_pulse.load_config", return_value=config_patch):
            result = _mp.get_cached_pulse_from_db([ASSET_TICKER], refresh_rate=60)
        asset_tickers = [r["ticker"] for r in result["assets"]]
        assert ASSET_TICKER not in asset_tickers

    def test_empty_ticker_list_returns_index_only(self):
        result = _mp.get_cached_pulse_from_db([], refresh_rate=60)
        assert "indexes" in result
        assert "assets" in result
        assert result["assets"] == []

    def test_none_ticker_list_handled_gracefully(self):
        result = _mp.get_cached_pulse_from_db(None, refresh_rate=60)
        assert "indexes" in result
        assert "assets" in result


# ── get_all_cached_pulse ──────────────────────────────────────────────────────

class TestGetAllCachedPulse:
    def teardown_method(self):
        _clear(ASSET_TICKER)

    def test_returns_dict_keyed_by_ticker(self):
        _seed_pulse(ASSET_TICKER)
        result = _mp.get_all_cached_pulse()
        assert isinstance(result, dict)
        assert ASSET_TICKER in result

    def test_is_stale_false_when_fresh(self):
        _seed_pulse(ASSET_TICKER, last_updated=time.time())
        config_patch = {"UI_PREFERENCES": {"REFRESH_RATE": 60}}
        with patch("market_pulse.load_config", return_value=config_patch):
            result = _mp.get_all_cached_pulse()
        assert result[ASSET_TICKER]["is_stale"] is False

    def test_is_stale_true_when_old(self):
        _seed_pulse(ASSET_TICKER, last_updated=time.time() - 3600)
        config_patch = {"UI_PREFERENCES": {"REFRESH_RATE": 60}}
        with patch("market_pulse.load_config", return_value=config_patch), \
             patch("market_pulse.is_trading_session", return_value=True):
            result = _mp.get_all_cached_pulse()
        assert result[ASSET_TICKER]["is_stale"] is True

    def test_empty_db_returns_empty_dict(self):
        _clear(ASSET_TICKER)
        result = _mp.get_all_cached_pulse()
        assert ASSET_TICKER not in result


# ── closed-market / needs_refresh behaviour ───────────────────────────────────

class TestClosedMarketStaleness:
    """When is_trading_session() is False, cached data should never go grey."""

    def teardown_method(self):
        _clear(ASSET_TICKER)

    def test_old_data_not_stale_when_market_closed(self):
        _seed_pulse(ASSET_TICKER, last_updated=time.time() - 3600)
        with patch("market_pulse.is_trading_session", return_value=False):
            result = _mp.get_cached_pulse_from_db([ASSET_TICKER], refresh_rate=60)
        asset = next(r for r in result["assets"] if r["ticker"] == ASSET_TICKER)
        assert asset["is_stale"] is False

    def test_needs_refresh_false_when_market_closed(self):
        _seed_pulse(ASSET_TICKER, last_updated=time.time() - 3600)
        with patch("market_pulse.is_trading_session", return_value=False):
            result = _mp.get_cached_pulse_from_db([ASSET_TICKER], refresh_rate=60)
        asset = next(r for r in result["assets"] if r["ticker"] == ASSET_TICKER)
        assert asset["needs_refresh"] is False

    def test_within_refresh_rate_not_stale_and_no_refresh(self):
        _seed_pulse(ASSET_TICKER, last_updated=time.time() - 30)
        with patch("market_pulse.is_trading_session", return_value=True):
            result = _mp.get_cached_pulse_from_db([ASSET_TICKER], refresh_rate=60)
        asset = next(r for r in result["assets"] if r["ticker"] == ASSET_TICKER)
        assert asset["is_stale"] is False
        assert asset["needs_refresh"] is False

    def test_between_refresh_rate_and_display_floor_needs_refresh_but_not_stale(self):
        _seed_pulse(ASSET_TICKER, last_updated=time.time() - 130)  # 130s > 60s but < 300s floor
        with patch("market_pulse.is_trading_session", return_value=True):
            result = _mp.get_cached_pulse_from_db([ASSET_TICKER], refresh_rate=60)
        asset = next(r for r in result["assets"] if r["ticker"] == ASSET_TICKER)
        assert asset["is_stale"] is False
        assert asset["needs_refresh"] is True

    def test_beyond_display_floor_is_stale(self):
        _seed_pulse(ASSET_TICKER, last_updated=time.time() - 301)  # beyond the 5-minute floor
        with patch("market_pulse.is_trading_session", return_value=True):
            result = _mp.get_cached_pulse_from_db([ASSET_TICKER], refresh_rate=60)
        asset = next(r for r in result["assets"] if r["ticker"] == ASSET_TICKER)
        assert asset["is_stale"] is True
        assert asset["needs_refresh"] is True

    def test_missing_ticker_always_needs_refresh_even_when_market_closed(self):
        """A ticker with no cache row must always be fetched; daily NAV data is available
        outside trading hours (e.g. mutual funds), so the missing check must be session-agnostic."""
        _clear(ASSET_TICKER)
        with patch("market_pulse.is_trading_session", return_value=False):
            result = _mp.get_cached_pulse_from_db([ASSET_TICKER], refresh_rate=60)
        asset = next((r for r in result["assets"] if r["ticker"] == ASSET_TICKER), None)
        assert asset is not None
        assert asset["needs_refresh"] is True
        assert asset["is_stale"] is True

    def test_get_all_cached_pulse_not_stale_when_market_closed(self):
        _seed_pulse(ASSET_TICKER, last_updated=time.time() - 3600)
        config_patch = {"UI_PREFERENCES": {"REFRESH_RATE": 60}}
        with patch("market_pulse.load_config", return_value=config_patch), \
             patch("market_pulse.is_trading_session", return_value=False):
            result = _mp.get_all_cached_pulse()
        assert result[ASSET_TICKER]["is_stale"] is False


# ── is_price_fresh (shared helper) ────────────────────────────────────────────

class TestIsPriceFresh:
    def test_no_data_is_never_fresh(self):
        with patch("market_pulse.is_trading_session", return_value=False):
            assert _mp.is_price_fresh(0, 0.0, 60) is False

    def test_fresh_when_market_closed_regardless_of_age(self):
        with patch("market_pulse.is_trading_session", return_value=False):
            assert _mp.is_price_fresh(time.time() - 3600, 100.0, 60) is True

    def test_fresh_when_market_open_and_within_window(self):
        with patch("market_pulse.is_trading_session", return_value=True):
            assert _mp.is_price_fresh(time.time() - 30, 100.0, 60) is True

    def test_fresh_within_the_5_minute_floor_even_beyond_2x_refresh_rate(self):
        """Regression test: a 130s-old price at refresh_rate=60 (2x = 120s) must still count
        as fresh — the display floor is 5 minutes, comfortably wider than the ~10-minute
        background scan cadence, to avoid flashing stale/fresh every poll cycle."""
        with patch("market_pulse.is_trading_session", return_value=True):
            assert _mp.is_price_fresh(time.time() - 130, 100.0, 60) is True

    def test_stale_when_market_open_and_beyond_5_minute_floor(self):
        with patch("market_pulse.is_trading_session", return_value=True):
            assert _mp.is_price_fresh(time.time() - 301, 100.0, 60) is False

    def test_stale_when_market_open_and_beyond_2x_refresh_rate_larger_than_floor(self):
        """When 2x refresh_rate exceeds the 5-minute floor (a large configured refresh_rate),
        the larger of the two still governs."""
        with patch("market_pulse.is_trading_session", return_value=True):
            assert _mp.is_price_fresh(time.time() - 700, 100.0, 400) is True
            assert _mp.is_price_fresh(time.time() - 900, 100.0, 400) is False


# ── is_exchange_open ──────────────────────────────────────────────────────────

def _set_market_state(ticker: str, state: str | None):
    c = _conn()
    c.execute(
        "INSERT INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, "
        "is_positive, last_updated, market_state) VALUES (?, ?, 0, 0, 0, 1, 0, ?) "
        "ON CONFLICT(ticker) DO UPDATE SET market_state = excluded.market_state",
        (ticker, f"Name_{ticker}", state),
    )
    c.commit()
    c.close()


class TestIsExchangeOpen:
    def teardown_method(self):
        _clear("^GSPC", "^FTSE")

    def test_regular_state_is_open(self):
        _set_market_state("^GSPC", "REGULAR")
        assert _mp.is_exchange_open("NYSE") is True

    def test_closed_state_is_not_open(self):
        _set_market_state("^GSPC", "CLOSED")
        assert _mp.is_exchange_open("NYSE") is False

    def test_holiday_regression_ignores_naive_weekday_hours_heuristic(self):
        """The exact bug this feature fixes: a normal Friday during NYSE hours (the naive
        heuristic would say open) but Yahoo's live marketState says the exchange is actually
        closed for a holiday — is_exchange_open must trust the live state, not the calendar."""
        _set_market_state("^GSPC", "CLOSED")
        with patch("market_pulse.is_trading_session", return_value=True):
            assert _mp.is_exchange_open("NYSE") is False

    def test_postpost_state_is_not_open(self):
        _set_market_state("^FTSE", "POSTPOST")
        assert _mp.is_exchange_open("LSE") is False

    def test_falls_back_to_heuristic_when_no_cached_row(self):
        with patch("market_pulse.is_trading_session", return_value=True) as mock_ts:
            assert _mp.is_exchange_open("NYSE") is True
            mock_ts.assert_called_once_with("NYSE", include_premarket=False)

    def test_falls_back_to_heuristic_when_cached_market_state_is_null(self):
        _set_market_state("^GSPC", None)
        with patch("market_pulse.is_trading_session", return_value=False) as mock_ts:
            assert _mp.is_exchange_open("NYSE") is False
            mock_ts.assert_called_once_with("NYSE", include_premarket=False)

    def test_untracked_exchange_uses_heuristic_directly(self):
        with patch("market_pulse.is_trading_session", return_value=True) as mock_ts:
            assert _mp.is_exchange_open("XETRA") is True
            mock_ts.assert_called_once_with("XETRA", include_premarket=False)

    def test_pre_state_not_open_by_default(self):
        _set_market_state("^GSPC", "PRE")
        assert _mp.is_exchange_open("NYSE") is False

    def test_pre_state_is_open_with_include_premarket(self):
        _set_market_state("^GSPC", "PRE")
        assert _mp.is_exchange_open("NYSE", include_premarket=True) is True

    def test_prepre_state_is_open_with_include_premarket(self):
        _set_market_state("^GSPC", "PREPRE")
        assert _mp.is_exchange_open("NYSE", include_premarket=True) is True

    def test_regular_state_still_open_with_include_premarket(self):
        _set_market_state("^GSPC", "REGULAR")
        assert _mp.is_exchange_open("NYSE", include_premarket=True) is True

    def test_closed_state_still_closed_with_include_premarket(self):
        _set_market_state("^GSPC", "CLOSED")
        assert _mp.is_exchange_open("NYSE", include_premarket=True) is False

    def test_include_premarket_propagates_to_heuristic_fallback(self):
        with patch("market_pulse.is_trading_session", return_value=True) as mock_ts:
            assert _mp.is_exchange_open("XETRA", include_premarket=True) is True
            mock_ts.assert_called_once_with("XETRA", include_premarket=True)


# ── proxy_tickers_needing_refresh ─────────────────────────────────────────────

class TestProxyTickersNeedingRefresh:
    """Regression coverage: without this self-refresh, a caller that only ever polls
    GET /api/system/market-status (e.g. Home Assistant, with no browser dashboard open to
    drive /api/market-pulse's own JS polling) would never populate market_state at all, and
    is_exchange_open() would fall back to the naive heuristic forever."""

    ALL_PROXIES = {"^GSPC", "^FTSE", "^GDAXI", "^N225", "^HSI", "000001.SS", "^AXJO", "^FCHI"}

    def teardown_method(self):
        _clear(*self.ALL_PROXIES)

    def test_all_proxies_stale_when_no_cache_rows_exist(self):
        assert set(_mp.proxy_tickers_needing_refresh()) == self.ALL_PROXIES

    def test_fresh_row_is_not_flagged(self):
        for ticker in self.ALL_PROXIES:
            _seed_pulse(ticker, last_updated=time.time())
        assert _mp.proxy_tickers_needing_refresh() == []

    def test_stale_row_is_flagged(self):
        for ticker in self.ALL_PROXIES:
            _seed_pulse(ticker, last_updated=time.time())
        _seed_pulse("^GSPC", last_updated=time.time() - 3600)
        assert _mp.proxy_tickers_needing_refresh() == ["^GSPC"]

    def test_custom_max_age_respected(self):
        for ticker in self.ALL_PROXIES:
            _seed_pulse(ticker, last_updated=time.time() - 120)
        assert set(_mp.proxy_tickers_needing_refresh(max_age_seconds=60)) == self.ALL_PROXIES
        assert _mp.proxy_tickers_needing_refresh(max_age_seconds=300) == []
