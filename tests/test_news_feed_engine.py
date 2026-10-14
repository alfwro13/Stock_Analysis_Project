"""
tests/test_news_feed_engine.py — unit tests for news_feed_engine pure functions

Covers:
  _make_article_id()        — UUID present vs absent, SHA-256 hash determinism
  _build_ticker_source_map()— portfolio/watchlist union, 'both' label, ignored filter
  _fetch_full_text()        — vetted fetch only: https, public address, 2 redirects, 2 MB, no TLS-off retry
"""
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
import news_feed_engine
import outbound_fetch_helpers
from news_feed_engine import (
    _make_article_id,
    _build_ticker_source_map,
    _fetch_full_text,
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


# ── _build_ticker_source_map ──────────────────────────────────────────────────

class TestBuildTickerSourceMap:
    def _run(self, portfolio_tickers, watchlist_tickers, ignored=None):
        combined = {t: {"ticker": t} for t in portfolio_tickers}
        cfg = {"IGNORED_TICKERS": ignored or []}
        with (
            patch("accounts_engine.get_combined_holdings", return_value=combined),
            patch("news_feed_engine.get_watchlist_tickers", return_value=watchlist_tickers),
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


class TestFetchFullText:
    def test_extracts_text_from_the_vetted_body(self):
        with patch("news_feed_engine.fetch_body", return_value="<html>x</html>") as fetch, \
             patch("trafilatura.extract", return_value="Article text") as extract, \
             patch("trafilatura.fetch_url", side_effect=AssertionError("trafilatura must not download")):
            assert _fetch_full_text("https://publisher.example/a") == "Article text"
        assert fetch.call_args.kwargs == {"max_redirects": news_feed_engine._ARTICLE_MAX_REDIRECTS}
        assert extract.call_args.args == ("<html>x</html>",)

    def test_article_fetch_allows_two_redirects_and_two_megabytes(self):
        assert news_feed_engine._ARTICLE_MAX_REDIRECTS == 2
        assert outbound_fetch_helpers.DEFAULT_MAX_BODY_BYTES == 2 * 1024 * 1024

    @pytest.mark.parametrize("url", ["", "N/A", None])
    def test_missing_url_returns_none_without_fetching(self, url):
        with patch("news_feed_engine.fetch_body") as fetch:
            assert _fetch_full_text(url) is None
        fetch.assert_not_called()

    def test_http_url_is_refused_before_any_lookup(self):
        with patch.object(outbound_fetch_helpers, "_resolve", side_effect=AssertionError("must not resolve")):
            assert _fetch_full_text("http://publisher.example/a") is None

    @pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.5", "192.168.1.71", "169.254.169.254", "::1"])
    def test_non_public_address_is_never_connected_to(self, address):
        with patch.object(outbound_fetch_helpers, "_resolve", return_value=[address]), \
             patch.object(outbound_fetch_helpers, "_get_pinned") as pinned:
            assert _fetch_full_text("https://publisher.example/a") is None
        pinned.assert_not_called()

    def test_fetch_failure_returns_none(self):
        with patch("news_feed_engine.fetch_body", side_effect=Exception("TLS handshake failed")):
            assert _fetch_full_text("https://publisher.example/a") is None
