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


def _seed_stock_signal(ticker: str, price: float, last_updated: str, currency: str = "USD"):
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, last_updated) VALUES (?, ?, ?, ?)",
        (ticker, price, currency, last_updated),
    )
    c.commit()
    c.close()


def _clear(*tickers):
    c = _conn()
    for t in tickers:
        c.execute("DELETE FROM market_pulse_cache WHERE ticker = ?", (t,))
        c.execute("DELETE FROM quant_signals WHERE ticker = ?", (t,))
        c.execute("DELETE FROM stock_signals WHERE ticker = ?", (t,))
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
        """Forces static mode so this test exercises the plain seeded index-ticker set,
        independent of whether MARKET_PULSE_DYNAMIC happens to be on in the real config —
        dynamic mode picks its own live, exchange-state-dependent tile set (see
        TestSelectActivePulseTickers), which is not what this test is about."""
        _seed_pulse(INDEX_TICKER)
        with patch("market_pulse.load_config", return_value={"UI_PREFERENCES": {}}):
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

    def test_falls_back_to_stock_signals_when_cache_stuck(self):
        now = time.time()
        _seed_pulse(ASSET_TICKER, price=297.11, change_pct=35.0, last_updated=now - 7 * 86400)
        _seed_stock_signal(ASSET_TICKER, price=219.05, last_updated=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)))
        with patch("market_pulse.is_trading_session", return_value=False):
            result = _mp.get_cached_pulse_from_db([ASSET_TICKER], refresh_rate=60)
        asset = next(r for r in result["assets"] if r["ticker"] == ASSET_TICKER)
        assert asset["price"] == 219.05
        assert asset["change_pct"] is None

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

    def test_falls_back_to_stock_signals_when_cache_stuck(self):
        """Regression coverage: market_pulse_cache stuck on a week-old price after
        stock_signals refreshed must not surface the stuck price to the Portfolio/Watchlist
        page — see accounts_engine.current_price_map()'s identical gap-check."""
        now = time.time()
        _seed_pulse(ASSET_TICKER, price=297.11, change_pct=35.0, last_updated=now - 7 * 86400)
        _seed_stock_signal(ASSET_TICKER, price=219.05, last_updated=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)))
        result = _mp.get_all_cached_pulse()
        assert result[ASSET_TICKER]["price"] == 219.05
        assert result[ASSET_TICKER]["change_pct"] is None
        assert result[ASSET_TICKER]["change_pts"] is None

    def test_keeps_live_price_when_stock_signals_not_meaningfully_fresher(self):
        now = time.time()
        _seed_pulse(ASSET_TICKER, price=297.11, change_pct=0.6, last_updated=now - 60)
        _seed_stock_signal(ASSET_TICKER, price=219.05, last_updated=time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime(now)))
        result = _mp.get_all_cached_pulse()
        assert result[ASSET_TICKER]["price"] == 297.11
        assert result[ASSET_TICKER]["change_pct"] == 0.6


# ── get_cached_change_pct ───────────────────────────────────────────────────────

class TestGetCachedChangePct:
    def teardown_method(self):
        _clear(ASSET_TICKER)

    def test_returns_cached_change_pct(self):
        _seed_pulse(ASSET_TICKER, change_pct=-9.0)
        assert _mp.get_cached_change_pct(ASSET_TICKER) == -9.0

    def test_unknown_ticker_returns_none(self):
        _clear(ASSET_TICKER)
        assert _mp.get_cached_change_pct(ASSET_TICKER) is None

    def test_survives_market_closed_state(self):
        """The core contract this function exists for: a value cached while a foreign exchange
        was open must still be readable after that exchange has since closed for the day."""
        _seed_pulse(ASSET_TICKER, change_pct=-9.0, last_updated=time.time() - 3600 * 12)
        assert _mp.get_cached_change_pct(ASSET_TICKER) == -9.0


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
        # needs_refresh is gated by is_quote_settled() (per-ticker exchange), not
        # is_trading_session() — see TestNeedsRefreshPerTickerExchange. Both must be mocked so
        # this test is deterministic regardless of the real exchange's wall-clock open state.
        with patch("market_pulse.is_trading_session", return_value=True), \
             patch("market_pulse.is_quote_settled", return_value=True):
            result = _mp.get_cached_pulse_from_db([ASSET_TICKER], refresh_rate=60)
        asset = next(r for r in result["assets"] if r["ticker"] == ASSET_TICKER)
        assert asset["is_stale"] is False
        assert asset["needs_refresh"] is True

    def test_beyond_display_floor_is_stale(self):
        _seed_pulse(ASSET_TICKER, last_updated=time.time() - 301)  # beyond the 5-minute floor
        with patch("market_pulse.is_trading_session", return_value=True), \
             patch("market_pulse.is_quote_settled", return_value=True):
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


# ── needs_refresh gated per-ticker exchange, not one global is_trading_session() ──────────────

class TestNeedsRefreshPerTickerExchange:
    """Regression coverage for the fix: get_cached_pulse_from_db() used to gate every row's
    needs_refresh on one global is_trading_session() (HOME_EXCHANGE heuristic), so an LSE row and
    an NYSE row always agreed on whether to refresh even when only one of their two exchanges was
    actually open/settled. It must now be resolved per ticker via its own exchange."""

    def teardown_method(self):
        _clear("^FTSE", "^GSPC")

    def test_lse_and_nyse_index_rows_gated_independently(self):
        _seed_pulse("^FTSE", last_updated=time.time() - 3600)
        _seed_pulse("^GSPC", last_updated=time.time() - 3600)

        def fake_settled(exchange, include_premarket=False):
            return exchange == "NYSE"

        # Static mode forced (see test_known_index_ticker_lands_in_indexes) so both index rows
        # are guaranteed present regardless of the real config's MARKET_PULSE_DYNAMIC setting.
        with patch("market_pulse.is_quote_settled", side_effect=fake_settled), \
             patch("market_pulse.load_config", return_value={"UI_PREFERENCES": {}}):
            result = _mp.get_cached_pulse_from_db([], refresh_rate=60)
        by_ticker = {r["ticker"]: r for r in result["indexes"]}
        assert by_ticker["^FTSE"]["needs_refresh"] is False
        assert by_ticker["^GSPC"]["needs_refresh"] is True

    def test_equity_asset_exchange_resolved_via_currency_when_not_in_registry(self):
        _clear(ASSET_TICKER)
        _seed_pulse(ASSET_TICKER, last_updated=time.time() - 3600)
        conn = _conn()
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, currency) VALUES (?, ?)",
            (ASSET_TICKER, "GBp"),
        )
        conn.commit()
        conn.close()

        def fake_settled(exchange, include_premarket=False):
            return exchange == "LSE"

        try:
            with patch("market_pulse.is_quote_settled", side_effect=fake_settled):
                result = _mp.get_cached_pulse_from_db([ASSET_TICKER], refresh_rate=60)
            asset = next(r for r in result["assets"] if r["ticker"] == ASSET_TICKER)
            assert asset["needs_refresh"] is True
        finally:
            conn = _conn()
            conn.execute("DELETE FROM stock_signals WHERE ticker = ?", (ASSET_TICKER,))
            conn.commit()
            conn.close()
            _clear(ASSET_TICKER)


# ── resolve_ticker_exchange / is_ticker_quote_settled (shared per-ticker helper) ──────────────

class TestResolveTickerExchange:
    def test_registry_ticker_uses_registry_exchange_not_currency_fallback(self):
        assert _mp.resolve_ticker_exchange("^FTSE", currency="USD") == "LSE"

    def test_future_ticker_shares_its_spot_rows_exchange(self):
        assert _mp.resolve_ticker_exchange("ES=F") == "NYSE"

    def test_unknown_ticker_falls_back_to_currency_resolution(self):
        assert _mp.resolve_ticker_exchange("_NOT_A_REGISTRY_TICKER", currency="EUR") == "XETRA"

    def test_precomputed_map_is_used_when_given(self):
        prebuilt = {"^FTSE": "XETRA"}
        assert _mp.resolve_ticker_exchange("^FTSE", registry_exchange_map=prebuilt) == "XETRA"


class TestIsTickerQuoteSettled:
    def test_delegates_to_is_quote_settled_for_resolved_exchange(self):
        with patch("market_pulse.is_quote_settled", return_value=False) as mock_settled, \
             patch("market_pulse.get_exchange_session_state", return_value="open"):
            assert _mp.is_ticker_quote_settled("^FTSE") is False
        mock_settled.assert_called_once_with("LSE", include_premarket=False)

    def test_active_premarket_session_counts_as_settled_for_ordinary_ticker(self):
        """Regression (2026-07-17): an ordinary (non-future) ticker whose exchange is genuinely
        in pre-market or after-hours must also count as settled, so the extended-hours display
        actually refreshes rather than staying frozen on the last regular-session cache row."""
        with patch("market_pulse.is_quote_settled", return_value=False), \
             patch("market_pulse.get_exchange_session_state", return_value="pre"):
            assert _mp.is_ticker_quote_settled("AAPL") is True

    def test_active_afterhours_session_counts_as_settled_for_ordinary_ticker(self):
        with patch("market_pulse.is_quote_settled", return_value=False), \
             patch("market_pulse.get_exchange_session_state", return_value="post"):
            assert _mp.is_ticker_quote_settled("AAPL") is True

    def test_closed_session_does_not_count_as_settled(self):
        with patch("market_pulse.is_quote_settled", return_value=False), \
             patch("market_pulse.get_exchange_session_state", return_value="closed"):
            assert _mp.is_ticker_quote_settled("AAPL") is False

    def test_future_ticker_honors_premarket_unlike_its_spot_row(self):
        # ES=F shares ^GSPC's NYSE exchange (see TestResolveTickerExchange above), but must
        # gate on include_premarket=True — futures trade near-continuously and are shown
        # specifically during the spot exchange's pre-market window, so gating the future's
        # refresh on the spot's *regular* session settling would mean it can never refresh
        # while it's actually the tile being displayed (found 2026-07-13).
        with patch("market_pulse.is_quote_settled", return_value=True) as mock_settled:
            assert _mp.is_ticker_quote_settled("ES=F") is True
        mock_settled.assert_called_once_with("NYSE", include_premarket=True)

    def test_registry_future_tickers_can_be_precomputed(self):
        prebuilt_exchanges = {"ES=F": "NYSE"}
        prebuilt_futures = set()
        with patch("market_pulse.is_quote_settled", return_value=True) as mock_settled:
            _mp.is_ticker_quote_settled(
                "ES=F", registry_exchange_map=prebuilt_exchanges, registry_future_tickers=prebuilt_futures,
            )
        mock_settled.assert_called_once_with("NYSE", include_premarket=False)


class TestBuildRegistryFutureTickers:
    def test_returns_every_future_ticker_in_registry(self):
        future_tickers = _mp.build_registry_future_tickers()
        assert "ES=F" in future_tickers

    def test_spot_ticker_itself_is_not_included(self):
        assert "^GSPC" not in _mp.build_registry_future_tickers()


# ── registry_tickers_needing_refresh ──────────────────────────────────────────

class TestRegistryTickersNeedingRefresh:
    """Sibling of tickers_needing_refresh() used to warm market_ticker_registry rows (Markets
    page / GET /api/system/market-status) — unlike the bare age-only check, an already-cached
    ticker must also be gated on is_ticker_quote_settled() for its own exchange."""

    def teardown_method(self):
        _clear("^FTSE", "^GSPC", "^KS200")

    def test_empty_input_returns_empty(self):
        assert _mp.registry_tickers_needing_refresh([]) == []

    def test_missing_row_always_included_regardless_of_settlement(self):
        with patch("market_pulse.is_quote_settled", return_value=False):
            assert _mp.registry_tickers_needing_refresh(["^KS200"]) == ["^KS200"]

    def test_fresh_row_not_flagged_even_when_settled(self):
        _seed_pulse("^FTSE", last_updated=time.time())
        with patch("market_pulse.is_quote_settled", return_value=True):
            assert _mp.registry_tickers_needing_refresh(["^FTSE"]) == []

    def test_stale_row_excluded_when_its_exchange_not_settled(self):
        _seed_pulse("^FTSE", last_updated=time.time() - 3600)
        with patch("market_pulse.is_quote_settled", return_value=False):
            assert _mp.registry_tickers_needing_refresh(["^FTSE"]) == []

    def test_stale_row_included_when_its_exchange_settled(self):
        _seed_pulse("^FTSE", last_updated=time.time() - 3600)
        with patch("market_pulse.is_quote_settled", return_value=True):
            assert _mp.registry_tickers_needing_refresh(["^FTSE"]) == ["^FTSE"]

    def test_two_rows_gated_independently_by_own_exchange(self):
        _seed_pulse("^FTSE", last_updated=time.time() - 3600)
        _seed_pulse("^GSPC", last_updated=time.time() - 3600)

        def fake_settled(exchange, include_premarket=False):
            return exchange == "NYSE"

        with patch("market_pulse.is_quote_settled", side_effect=fake_settled):
            assert _mp.registry_tickers_needing_refresh(["^FTSE", "^GSPC"]) == ["^GSPC"]


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
    @pytest.fixture(autouse=True)
    def _not_a_real_holiday(self):
        # These tests exercise the cached-market-state/heuristic logic, not the holiday veto
        # itself (that's covered by the dedicated exchange-calendar tests below, which patch
        # this explicitly) — without this default, every test here would only pass when the
        # suite happens to run on a genuine NYSE/LSE trading day.
        with patch("market_pulse.is_exchange_holiday", return_value=False):
            yield

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

    def test_exchange_calendar_veto_overrides_live_regular_state(self):
        """New canonical-holiday-source behaviour: even if Yahoo's cached marketState is stale
        or wrong and still says REGULAR, a real exchange_calendars holiday must still veto it."""
        _set_market_state("^GSPC", "REGULAR")
        with patch("market_pulse.is_exchange_holiday", return_value=True):
            assert _mp.is_exchange_open("NYSE") is False

    def test_no_veto_on_ordinary_day_still_reads_live_state(self):
        _set_market_state("^GSPC", "REGULAR")
        with patch("market_pulse.is_exchange_holiday", return_value=False):
            assert _mp.is_exchange_open("NYSE") is True

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

    def test_include_premarket_does_not_propagate_for_exchange_without_premarket_window(self):
        # XETRA has no "premarket_open" in exchange_hours.json (only NYSE does), so even the
        # no-cached-row heuristic fallback must not be asked to honor premarket.
        with patch("market_pulse.is_trading_session", return_value=True) as mock_ts:
            assert _mp.is_exchange_open("XETRA", include_premarket=True) is True
            mock_ts.assert_called_once_with("XETRA", include_premarket=False)

    def test_include_premarket_propagates_to_heuristic_fallback_for_nyse(self):
        with patch("market_pulse.is_trading_session", return_value=True) as mock_ts:
            assert _mp.is_exchange_open("NYSE", include_premarket=True) is True
            mock_ts.assert_called_once_with("NYSE", include_premarket=True)

    def test_pre_state_not_honored_for_exchange_without_premarket_window(self):
        # The Markets page bug this fixes: Yahoo returns "PRE" for HKEX/TSE/etc. for the whole
        # gap since the previous close (no genuine extended-hours session modeled for them), so
        # a stale/lingering "PRE" state must not read as open even with include_premarket=True.
        _set_market_state("^HSI", "PRE")
        assert _mp.is_exchange_open("HKEX", include_premarket=True) is False
        _clear("^HSI")


# ── get_exchange_session_state ──────────────────────────────────────────────────

class TestGetExchangeSessionState:
    @pytest.fixture(autouse=True)
    def _not_a_real_holiday(self):
        # Same reasoning as TestIsExchangeOpen — isolate these tests from whatever real
        # calendar day the suite happens to run on; the holiday veto itself is covered by
        # test_exchange_calendar_veto_overrides_live_regular_state below.
        with patch("market_pulse.is_exchange_holiday", return_value=False):
            yield

    def teardown_method(self):
        _clear("^GSPC", "^FTSE", "^HSI")

    def test_regular_state_is_open(self):
        _set_market_state("^GSPC", "REGULAR")
        assert _mp.get_exchange_session_state("NYSE") == "open"

    def test_pre_state_is_pre_for_nyse(self):
        _set_market_state("^GSPC", "PRE")
        assert _mp.get_exchange_session_state("NYSE") == "pre"

    def test_prepre_state_is_pre_for_nyse(self):
        _set_market_state("^GSPC", "PREPRE")
        assert _mp.get_exchange_session_state("NYSE") == "pre"

    def test_pre_state_is_closed_for_exchange_without_premarket_window(self):
        # Same reasoning as is_exchange_open(): Yahoo's "PRE" spans the whole gap since
        # previous close for exchanges with no genuine extended-hours session (only NYSE
        # has one modeled), so it must not read as "pre" there.
        _set_market_state("^HSI", "PRE")
        assert _mp.get_exchange_session_state("HKEX") == "closed"

    def test_post_state_is_post(self):
        _set_market_state("^GSPC", "POST")
        assert _mp.get_exchange_session_state("NYSE") == "post"

    def test_postpost_state_is_post(self):
        _set_market_state("^FTSE", "POSTPOST")
        assert _mp.get_exchange_session_state("LSE") == "post"

    def test_closed_state_is_closed(self):
        _set_market_state("^GSPC", "CLOSED")
        assert _mp.get_exchange_session_state("NYSE") == "closed"

    def test_falls_back_to_heuristic_when_no_cached_row(self):
        with patch("market_pulse.is_trading_session", return_value=True):
            assert _mp.get_exchange_session_state("NYSE") == "open"

    def test_falls_back_to_pre_heuristic_when_cached_market_state_is_null(self):
        _set_market_state("^GSPC", None)
        with patch("market_pulse.is_trading_session", side_effect=lambda ex, include_premarket=False: include_premarket):
            assert _mp.get_exchange_session_state("NYSE") == "pre"

    def test_untracked_exchange_uses_heuristic_directly(self):
        with patch("market_pulse.is_trading_session", return_value=True):
            assert _mp.get_exchange_session_state("XETRA") == "open"

    def test_exchange_calendar_veto_overrides_live_regular_state(self):
        _set_market_state("^GSPC", "REGULAR")
        with patch("market_pulse.is_exchange_holiday", return_value=True):
            assert _mp.get_exchange_session_state("NYSE") == "closed"


# ── is_quote_settled ───────────────────────────────────────────────────────────

class TestIsQuoteSettled:
    """Regression coverage for the bug this fixes: the instant LSE opens, Yahoo's free
    quote feed is still ~15-20 minutes behind, so any engine treating is_exchange_open()
    alone as 'safe to act on this quote' pulls a not-yet-representative price."""

    def test_closed_exchange_is_not_settled(self):
        with patch("market_pulse.is_exchange_open", return_value=False):
            assert _mp.is_quote_settled("LSE") is False

    def test_exchange_with_no_configured_delay_is_settled_as_soon_as_open(self):
        with patch("market_pulse.is_exchange_open", return_value=True):
            assert _mp.is_quote_settled("NYSE") is True

    def test_lse_not_settled_within_delay_window_of_open(self):
        from datetime import time as dtime
        with patch("market_pulse.is_exchange_open", return_value=True), \
             patch("market_pulse.market_window_utc", return_value=(dtime(8, 0), dtime(16, 30))), \
             patch("market_pulse.datetime") as mock_dt:
            mock_dt.now.return_value.time.return_value = dtime(8, 5)
            assert _mp.is_quote_settled("LSE") is False

    def test_lse_settled_once_delay_window_has_passed(self):
        from datetime import time as dtime
        with patch("market_pulse.is_exchange_open", return_value=True), \
             patch("market_pulse.market_window_utc", return_value=(dtime(8, 0), dtime(16, 30))), \
             patch("market_pulse.datetime") as mock_dt:
            mock_dt.now.return_value.time.return_value = dtime(8, 16)
            assert _mp.is_quote_settled("LSE") is True


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


class TestTickersNeedingRefresh:
    """The generalized helper proxy_tickers_needing_refresh() itself now delegates to — used by
    GET /api/system/market-status to warm the full Markets registry, not just the 8 exchange-state
    proxies (added 2026-07-10)."""

    def teardown_method(self):
        _clear("^KS200", "GC=F", "^GSPC")

    def test_empty_input_returns_empty(self):
        assert _mp.tickers_needing_refresh([]) == []

    def test_missing_rows_are_stale(self):
        assert set(_mp.tickers_needing_refresh(["^KS200", "GC=F"])) == {"^KS200", "GC=F"}

    def test_fresh_row_is_not_flagged(self):
        _seed_pulse("^KS200", last_updated=time.time())
        assert _mp.tickers_needing_refresh(["^KS200"]) == []

    def test_stale_row_is_flagged(self):
        _seed_pulse("^KS200", last_updated=time.time() - 3600)
        assert _mp.tickers_needing_refresh(["^KS200"]) == ["^KS200"]

    def test_custom_max_age_respected(self):
        _seed_pulse("^KS200", last_updated=time.time() - 120)
        assert _mp.tickers_needing_refresh(["^KS200"], max_age_seconds=60) == ["^KS200"]
        assert _mp.tickers_needing_refresh(["^KS200"], max_age_seconds=300) == []
