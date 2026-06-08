"""
tests/test_news_feed_engine.py — unit tests for news_feed_engine pure functions

Covers:
  _make_article_id()        — UUID present vs absent, SHA-256 hash determinism
  _extract_published_at()   — millisecond timestamp, second timestamp, ISO string, missing
  _build_ticker_source_map()— portfolio/watchlist union, 'both' label, ignored filter
"""
import sys
import json
import tempfile
import os
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
from news_feed_engine import (
    _make_article_id,
    _extract_published_at,
    _build_ticker_source_map,
)


# ── _make_article_id ──────────────────────────────────────────────────────────

class TestMakeArticleId:
    def test_uses_uuid_when_present(self):
        item = {"uuid": "my-uuid-123"}
        assert _make_article_id(item, "AAPL", 1000.0) == "my-uuid-123"

    def test_uses_id_when_no_uuid(self):
        item = {"id": "item-id-456"}
        assert _make_article_id(item, "AAPL", 1000.0) == "item-id-456"

    def test_falls_back_to_sha256_hash(self):
        item = {"content": {"title": "Breaking News"}}
        result = _make_article_id(item, "AAPL", 1717840000.0)
        assert len(result) == 64  # SHA-256 hex digest

    def test_sha256_is_deterministic(self):
        item = {"content": {"title": "Same Headline"}}
        r1 = _make_article_id(item, "TSLA", 1717840000.0)
        r2 = _make_article_id(item, "TSLA", 1717840000.0)
        assert r1 == r2

    def test_different_inputs_produce_different_hashes(self):
        item_a = {"content": {"title": "Headline A"}}
        item_b = {"content": {"title": "Headline B"}}
        assert _make_article_id(item_a, "AAPL", 1000.0) != _make_article_id(item_b, "AAPL", 1000.0)


# ── _extract_published_at ─────────────────────────────────────────────────────

class TestExtractPublishedAt:
    def test_second_timestamp_returned_as_float(self):
        item = {"providerPublishTime": 1717840000}
        result = _extract_published_at(item)
        assert result == 1717840000.0

    def test_millisecond_timestamp_converted_to_seconds(self):
        item = {"providerPublishTime": 1717840000000}
        result = _extract_published_at(item)
        assert abs(result - 1717840000.0) < 1.0

    def test_iso_string_in_content_parsed(self):
        item = {"content": {"pubDate": "2024-06-08T12:00:00Z"}}
        result = _extract_published_at(item)
        assert result > 1_000_000_000

    def test_missing_timestamp_returns_zero(self):
        result = _extract_published_at({})
        assert result == 0.0

    def test_unparseable_string_returns_zero(self):
        item = {"content": {"pubDate": "not-a-date"}}
        result = _extract_published_at(item)
        assert result == 0.0


# ── _build_ticker_source_map ──────────────────────────────────────────────────

class TestBuildTickerSourceMap:
    def _write_portfolio(self, path, tickers):
        data = {t: {"ticker": t} for t in tickers}
        with open(path, "w") as f:
            json.dump(data, f)

    def _write_watchlist(self, path, tickers):
        with open(path, "w") as f:
            json.dump({"watchlist": tickers}, f)

    def _run(self, portfolio_tickers, watchlist_tickers, ignored=None):
        with tempfile.TemporaryDirectory() as tmpdir:
            p_path = os.path.join(tmpdir, "portfolio.json")
            w_path = os.path.join(tmpdir, "watchlist.json")
            self._write_portfolio(p_path, portfolio_tickers)
            self._write_watchlist(w_path, watchlist_tickers)
            cfg = {"IGNORED_TICKERS": ignored or []}
            with (
                patch("news_feed_engine.PORTFOLIO_PATH", p_path),
                patch("news_feed_engine.WATCHLIST_PATH", w_path),
                patch("news_feed_engine.load_config", return_value=cfg),
            ):
                return _build_ticker_source_map()

    def test_portfolio_only_ticker_labelled_portfolio(self):
        result = self._run(["AAPL"], [])
        assert result.get("AAPL") == "portfolio"

    def test_watchlist_only_ticker_labelled_watchlist(self):
        result = self._run([], ["TSLA"])
        assert result.get("TSLA") == "watchlist"

    def test_ticker_in_both_labelled_both(self):
        result = self._run(["MSFT"], ["MSFT"])
        assert result.get("MSFT") == "both"

    def test_ignored_ticker_excluded(self):
        result = self._run(["AAPL", "IGNORED"], [], ignored=["IGNORED"])
        assert "IGNORED" not in result
        assert "AAPL" in result

    def test_empty_portfolio_and_watchlist_returns_empty(self):
        result = self._run([], [])
        assert result == {}
