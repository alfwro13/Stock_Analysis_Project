"""
tests/test_market_pulse_read.py — MARKET PULSE READ FUNCTIONS

Covers get_cached_pulse_from_db() and get_all_cached_pulse():
  - index/asset split is determined by INDEX_TICKERS membership
  - de-duplication: same ticker in portfolio and watchlist appears once in assets
  - staleness flag: is_stale when age > refresh_rate
  - sentinel row for missing cache entry (price=0, is_stale=True)
  - IGNORED_TICKERS filtering
  - get_all_cached_pulse staleness flag via refresh rate
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
        _seed_pulse(ASSET_TICKER, last_updated=time.time() - 120)  # 2 min ago
        result = _mp.get_cached_pulse_from_db([ASSET_TICKER], refresh_rate=60)
        asset = next(r for r in result["assets"] if r["ticker"] == ASSET_TICKER)
        assert asset["is_stale"] is True

    def test_missing_cache_entry_produces_stale_sentinel(self):
        _clear(ASSET_TICKER)
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
        with patch("market_pulse.load_config", return_value=config_patch):
            result = _mp.get_all_cached_pulse()
        assert result[ASSET_TICKER]["is_stale"] is True

    def test_empty_db_returns_empty_dict(self):
        _clear(ASSET_TICKER)
        result = _mp.get_all_cached_pulse()
        assert ASSET_TICKER not in result
