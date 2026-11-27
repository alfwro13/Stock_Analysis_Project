"""
Unit tests for news.news_feed_engine pure functions.

Covers:
  _make_article_id()        — UUID present vs absent, SHA-256 hash determinism
  _build_ticker_source_map()— portfolio/watchlist union, 'both' label, ignored filter
  _fetch_full_text()        — vetted fetch only: https, public address, 2 redirects, 2 MB, no TLS-off retry
  _score_unscoredrows()     — sentiment is computed before the write transaction opens
  outbound_fetch_helpers.fetch_body() — an unusable declared charset falls back to UTF-8
"""
import io
import logging
import time
from unittest.mock import patch, MagicMock

import pytest
import requests

pytestmark = pytest.mark.restore_tables("data_migrations", "news_articles")

from db import database
from db import schema as _db_schema
from db import connection as _conn
import news.news_feed_engine
import outbound_fetch_helpers
from news.news_feed_engine import (
    _make_article_id,
    _build_ticker_source_map,
    _fetch_full_text,
    mentions_ticker,
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
            patch("accounts.accounts_engine.get_combined_holdings", return_value=combined),
            patch("news.news_feed_engine.get_watchlist_tickers", return_value=watchlist_tickers),
            patch("news.news_feed_engine.load_config", return_value=cfg),
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
        with patch("news.news_feed_engine.fetch_body", return_value="<html>x</html>") as fetch, \
             patch("trafilatura.extract", return_value="Article text") as extract, \
             patch("trafilatura.fetch_url", side_effect=AssertionError("trafilatura must not download")):
            assert _fetch_full_text("https://publisher.example/a") == "Article text"
        assert fetch.call_args.kwargs == {"max_redirects": news.news_feed_engine._ARTICLE_MAX_REDIRECTS}
        assert extract.call_args.args == ("<html>x</html>",)

    def test_article_fetch_allows_two_redirects_and_two_megabytes(self):
        assert news.news_feed_engine._ARTICLE_MAX_REDIRECTS == 2
        assert outbound_fetch_helpers.DEFAULT_MAX_BODY_BYTES == 2 * 1024 * 1024

    @pytest.mark.parametrize("url", ["", "N/A", None])
    def test_missing_url_returns_none_without_fetching(self, url):
        with patch("news.news_feed_engine.fetch_body") as fetch:
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

    @pytest.mark.parametrize("body", ["", "   \n\t"])
    def test_empty_body_skips_trafilatura_and_logs_no_errors(self, body, caplog):
        with patch("news.news_feed_engine.fetch_body", return_value=body), \
             patch("trafilatura.extract", side_effect=AssertionError("must not parse an empty body")), \
             caplog.at_level(logging.DEBUG):
            assert _fetch_full_text("https://publisher.example/a") is None
        assert not [r for r in caplog.records if r.name.startswith("trafilatura")]

    def test_empty_body_with_real_trafilatura_logs_no_errors(self, caplog):
        with patch("news.news_feed_engine.fetch_body", return_value=""), caplog.at_level(logging.DEBUG):
            assert _fetch_full_text("https://publisher.example/a") is None
        assert not [r for r in caplog.records if r.name.startswith("trafilatura")]

    def test_fetch_failure_returns_none(self):
        with patch("news.news_feed_engine.fetch_body", side_effect=Exception("TLS handshake failed")):
            assert _fetch_full_text("https://publisher.example/a") is None


class TestFetchBodyCharset:
    TEXT = "<p>Café shares rose 3%</p>"

    def _fetch(self, content_type, body):
        response = requests.Response()
        response.status_code = 200
        response.headers["Content-Type"] = content_type
        response.encoding = requests.utils.get_encoding_from_headers(response.headers)
        response.raw = io.BytesIO(body)
        with patch.object(outbound_fetch_helpers, "_resolve", return_value=["93.184.216.34"]), \
             patch("requests.Session.get", return_value=response):
            return outbound_fetch_helpers.fetch_body("https://publisher.example/a", {})

    @pytest.mark.parametrize("charset", ["utf-8mb4", "x-unknown", "base64", "rot13", "idna", "undefined"])
    def test_a_charset_that_is_no_text_codec_is_read_as_utf8(self, charset):
        assert self._fetch(f"text/html; charset={charset}", self.TEXT.encode("utf-8")) == self.TEXT

    def test_undecodable_bytes_are_replaced_in_the_fallback(self):
        assert self._fetch("text/html; charset=utf-8mb4", b"a\xffb") == "a\ufffdb"

    def test_a_declared_text_codec_is_honoured(self):
        assert self._fetch("text/html; charset=windows-1252", self.TEXT.encode("windows-1252")) == self.TEXT

    def test_without_a_declared_charset_the_page_is_utf8(self):
        assert self._fetch("text/html", self.TEXT.encode("utf-8")) == self.TEXT


# ── _score_unscoredrows ───────────────────────────────────────────────────────

class TestScoreUnscoredRows:
    ARTICLES = [f"_LOCKTEST_{i}" for i in range(3)]

    def setup_method(self):
        conn = database.get_connection()
        try:
            for article_id in self.ARTICLES:
                conn.execute(
                    "INSERT INTO news_articles (article_id, ticker, source_list, headline, summary, published_at, fetched_at) "
                    "VALUES (?, 'LOCKTEST', 'portfolio', ?, 'summary', ?, ?)",
                    (article_id, f"Headline {article_id}", int(time.time()), int(time.time())),
                )
            conn.commit()
        finally:
            conn.close()

    def teardown_method(self):
        conn = database.get_connection()
        try:
            conn.execute("DELETE FROM news_articles WHERE ticker = 'LOCKTEST'")
            conn.commit()
        finally:
            conn.close()

    def test_no_write_transaction_is_open_while_an_article_is_scored(self):
        held_while_scoring = []

        def score_text(analyzer, text):
            held_while_scoring.append(
                [e["caller"] for e in list(_conn._open_writes.values()) if e["caller"].startswith("news_feed_engine.py")]
            )
            return 0.5

        conn = database.get_connection()
        try:
            with patch("huggingface_engine._get_finbert_analyzer", return_value=object()), \
                 patch("huggingface_engine._score_text", side_effect=score_text):
                news.news_feed_engine._score_unscoredrows(conn)
            rows = conn.execute(
                "SELECT sentiment_score, sentiment_label FROM news_articles WHERE ticker = 'LOCKTEST'"
            ).fetchall()
        finally:
            conn.close()

        assert held_while_scoring == [[]] * len(self.ARTICLES)
        assert [(r["sentiment_score"], r["sentiment_label"]) for r in rows] == [(0.5, "positive")] * len(self.ARTICLES)

    def test_an_article_whose_scoring_fails_stays_unscored(self):
        def score_text(analyzer, text):
            if "_LOCKTEST_1" in text:
                raise RuntimeError("model failure")
            return -0.5

        conn = database.get_connection()
        try:
            with patch("huggingface_engine._get_finbert_analyzer", return_value=object()), \
                 patch("huggingface_engine._score_text", side_effect=score_text):
                news.news_feed_engine._score_unscoredrows(conn)
            rows = conn.execute(
                "SELECT article_id, sentiment_score FROM news_articles WHERE ticker = 'LOCKTEST' ORDER BY article_id"
            ).fetchall()
        finally:
            conn.close()

        assert [(r["article_id"], r["sentiment_score"]) for r in rows] == [
            ("_LOCKTEST_0", -0.5), ("_LOCKTEST_1", None), ("_LOCKTEST_2", -0.5),
        ]


class TestMentionsTicker:
    def test_symbol_as_a_whole_word(self):
        assert mentions_ticker("NVDA", "NVIDIA Corporation", "Why NVDA fell today", "")
        assert mentions_ticker("BE", "Bloom Energy Corporation", "Bloom Energy (NYSE:BE) pitches fuel cells", "")

    def test_symbol_inside_another_word_does_not_count(self):
        assert not mentions_ticker("AMD", "Advanced Micro Devices, Inc.", "Cinema DAMDEN opens", "")

    def test_short_symbol_is_not_matched_on_its_own(self):
        assert not mentions_ticker("ET", "Energy Transfer LP", "Results due 8 a.m. ET", "")
        assert mentions_ticker("ET", "Energy Transfer LP", "Energy Transfer raises payout", "")

    def test_company_name_without_its_corporate_suffix_is_case_insensitive(self):
        assert mentions_ticker("GOOGL", "Alphabet Inc.", "Microsoft Is Worth More Than ALPHABET", "")
        assert mentions_ticker("TXN", "Texas Instruments Incorporated", "", "texas instruments guided lower")
        assert mentions_ticker("SCHW", "Charles Schwab Corporation (The", "Charles Schwab results", "")
        assert mentions_ticker("NSRGY", "Nestle S.A.", "Nestle cuts outlook", "")

    def test_exchange_suffix_is_dropped_from_the_symbol(self):
        assert mentions_ticker("PRY.MI", "PRYSMIAN", "Cable maker PRY rallies", "")

    def test_an_article_naming_neither_is_false(self):
        assert not mentions_ticker("META", "Meta Platforms, Inc.", "Wall Street Thinks Oklo Is a Buy", "Nearly every analyst is bullish on Oklo stock.")
        assert not mentions_ticker("PFE", "Pfizer Inc.", "Trump gas price plan", None)

    def test_missing_company_name_still_matches_the_symbol(self):
        assert mentions_ticker("AAPL", None, "AAPL gains", "")
        assert not mentions_ticker("AAPL", None, "Apple gains", "")


class TestMentionsTickerCurrencyAmounts:
    NOK = "Nokia Oyj"

    @pytest.mark.parametrize("headline", [
        "Norsk Hydro reaffirms 2030 EBITDA target of up to 49B NOK",
        "Hydro cuts 2026 capex outlook by NOK 1 bln, targets NOK 3 bln in savin",
        "NHY: Integrated strengths underpin selective growth and NOK 43–46 bill",
    ])
    def test_a_currency_amount_is_not_a_mention(self, headline):
        assert not mentions_ticker("NOK", self.NOK, headline, "")

    @pytest.mark.parametrize("headline", [
        "Why NOK jumped today",
        "NOK 5% higher on 5G deal",
    ])
    def test_a_real_symbol_mention_still_counts(self, headline):
        assert mentions_ticker("NOK", self.NOK, headline, "")

    def test_an_amount_and_a_real_mention_in_the_same_text_counts(self):
        assert mentions_ticker("NOK", self.NOK, "Hydro targets 49B NOK; NOK shares rally", "")


def _repeat_rows(conn, ticker, headlines, summary, publisher="trefis.com", score=-0.9):
    for index, headline in enumerate(headlines):
        conn.execute(
            "INSERT INTO news_articles (article_id, ticker, source_list, headline, summary, publisher, published_at, fetched_at, sentiment_score, sentiment_label) "
            "VALUES (?, ?, 'portfolio', ?, ?, ?, ?, ?, ?, 'negative')",
            (f"{ticker}-{index}", ticker, headline, summary, publisher, int(time.time()), int(time.time()), score),
        )


class TestRepeatedSummaries:
    TEASER = "Today's big story: a site-wide teaser"

    def teardown_method(self):
        conn = database.get_connection()
        try:
            conn.execute("DELETE FROM news_articles WHERE ticker LIKE 'ZZREP%'")
            conn.commit()
        finally:
            conn.close()

    def test_a_summary_under_several_different_headlines_is_repeated_but_one_article_on_many_tickers_is_not(self):
        from db.helpers import get_repeated_news_summaries
        conn = database.get_connection()
        try:
            _repeat_rows(conn, "ZZREP1", ["One", "Two", "Three"], self.TEASER)
            for ticker in ("ZZREP2", "ZZREP3", "ZZREP4"):
                _repeat_rows(conn, ticker, ["Same article"], "A real summary")
            conn.commit()
            repeated = get_repeated_news_summaries(conn)
        finally:
            conn.close()
        assert ("trefis.com", self.TEASER) in repeated and ("trefis.com", "A real summary") not in repeated

    def test_article_summary_drops_only_a_repeated_one(self):
        from db.helpers import article_summary
        repeated = frozenset({("trefis.com", self.TEASER)})
        assert article_summary("Trefis.com", self.TEASER, repeated) is None
        assert article_summary("trefis.com", "Specific", repeated) == "Specific"
        assert article_summary(None, self.TEASER, repeated) == self.TEASER

    def test_scoring_uses_the_headline_alone_for_a_repeated_summary(self):
        conn = database.get_connection()
        try:
            _repeat_rows(conn, "ZZREP1", ["One", "Two", "Three"], self.TEASER, score=None)
            conn.execute("UPDATE news_articles SET sentiment_score = NULL, sentiment_label = NULL WHERE ticker = 'ZZREP1'")
            conn.commit()
            seen = []
            with patch("huggingface_engine._get_finbert_analyzer", return_value=object()), \
                 patch("huggingface_engine._score_text", side_effect=lambda analyzer, text: seen.append(text) or 0.0):
                news.news_feed_engine._score_unscoredrows(conn)
        finally:
            conn.close()
        assert sorted(seen) == ["One", "Three", "Two"]

    def test_migration_queues_stored_scores_of_a_repeated_summary_once(self):
        name = "news_articles_repeated_summary_unscored"
        conn = database.get_connection()
        try:
            conn.execute("DELETE FROM data_migrations WHERE name = ?", (name,))
            _repeat_rows(conn, "ZZREP1", ["One", "Two", "Three"], self.TEASER)
            _repeat_rows(conn, "ZZREP2", ["Solo"], "A real summary", score=0.4)
            conn.commit()
            _db_schema.migrate_db(conn, conn.cursor())
            rows = {r["ticker"]: r["sentiment_score"] for r in conn.execute("SELECT ticker, sentiment_score FROM news_articles WHERE ticker LIKE 'ZZREP%'")}
            assert rows == {"ZZREP1": None, "ZZREP2": 0.4}
            assert conn.execute("SELECT COUNT(*) FROM data_migrations WHERE name = ?", (name,)).fetchone()[0] == 1

            conn.execute("UPDATE news_articles SET sentiment_score = 0.7 WHERE ticker = 'ZZREP1'")
            conn.commit()
            _db_schema.migrate_db(conn, conn.cursor())
            assert conn.execute("SELECT MIN(sentiment_score) FROM news_articles WHERE ticker = 'ZZREP1'").fetchone()[0] == 0.7
        finally:
            conn.close()


class TestFeedTextMigration:
    _NAME = "news_articles_feed_text_repaired"

    def _insert(self, conn, article_id, summary, publisher, url):
        conn.execute(
            "INSERT INTO news_articles (article_id, ticker, company_name, source_list, headline, summary, url, publisher, published_at, is_premium, fetched_at) "
            "VALUES (?, 'ZZMIG', 'Mig Co', 'portfolio', 'h', ?, ?, ?, 1, 0, 1)",
            (article_id, summary, url, publisher),
        )

    def test_repairs_summary_and_empty_publisher_once_and_keeps_a_real_publisher(self):
        conn = database.get_connection()
        try:
            conn.execute("DELETE FROM data_migrations WHERE name = ?", (self._NAME,))
            self._insert(conn, "zzmig-1", "Key TakeawaysAkamai bought about $2. 6 million.", "", "https://www.tikr.com/blog/a")
            self._insert(conn, "zzmig-2", None, "24/7 Wall St.", "https://247wallst.com/b")
            self._insert(conn, "zzmig-3", "Clean text.", "", "")
            conn.commit()
            _db_schema.migrate_db(conn, conn.cursor())

            def stored(article_id):
                row = conn.execute("SELECT summary, publisher FROM news_articles WHERE article_id = ?", (article_id,)).fetchone()
                return row["summary"], row["publisher"]

            assert stored("zzmig-1") == ("Key Takeaways Akamai bought about $2.6 million.", "tikr.com")
            assert stored("zzmig-2") == (None, "24/7 Wall St.")
            assert stored("zzmig-3") == ("Clean text.", "")
            assert conn.execute("SELECT COUNT(*) FROM data_migrations WHERE name = ?", (self._NAME,)).fetchone()[0] == 1

            conn.execute("UPDATE news_articles SET summary = 'Edited $1. 5 after' WHERE article_id = 'zzmig-3'")
            conn.commit()
            _db_schema.migrate_db(conn, conn.cursor())
            assert stored("zzmig-3")[0] == "Edited $1. 5 after"
        finally:
            conn.close()
