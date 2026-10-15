"""
tests/test_huggingface_engine.py  ── HUGGINGFACE ENGINE UNIT TESTS

All tests are fully offline — no real model loading, no network I/O, no DB.

Coverage:
  TestGetFinbertAnalyzer   — singleton guarantee, local-cache-first path,
                             online fallback, HF_TOKEN injection, failure → None
  TestScoreText            — positive / negative / neutral label mapping,
                             text truncation at NLP_TEXT_TRUNCATE_CHARS
  TestFetchAndScoreNews    — no headlines -> None, unavailable fetch/scoring -> error,
                             age cutoff, NLP_NEWS_FETCH_LIMIT cap, blank-item skipping,
                             nested-content vs flat payloads
  TestUpdateAllSentiment   — macro ticker injection, early-exit on no analyzer,
                             UPDATE path, upsert path (rowcount == 0), NULL write for no
                             headlines, untouched row when unavailable, degraded-scan alert
  TestRunCentralBankNlpAlert — unsupported currency, no news, keyword filter,
                               HAWKISH / DOVISH / NEUTRAL tone classification,
                               Nextcloud dispatch + DB logging on success
"""

import os
import re
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, call, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import huggingface_engine
from huggingface_engine import (
    SentimentUnavailableError,
    _get_finbert_analyzer,
    _score_text,
    fetch_and_score_news,
    run_central_bank_nlp_alert,
    update_all_sentiment,
)
from constants import (
    NLP_CB_TONE_THRESHOLD, NLP_FINBERT_REVISION, NLP_NEWS_FETCH_LIMIT, NLP_TEXT_TRUNCATE_CHARS,
)

MAX_AGE_DAYS = 3


# ─── fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def reset_singleton():
    """Reset the FinBERT singleton before every test to guarantee isolation."""
    huggingface_engine._FINBERT_ANALYZER = None
    yield
    huggingface_engine._FINBERT_ANALYZER = None


def _mock_analyzer(label: str = "positive", score: float = 0.9) -> MagicMock:
    """Return a callable mock that behaves like a HuggingFace pipeline."""
    analyzer = MagicMock()
    analyzer.return_value = [{"label": label, "score": score}]
    return analyzer


def _pub_date(days_ago: float = 0.1) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")


def _news_item(title: str = "Markets rally", summary: str = "Stocks up",
               publisher: str = "Reuters", days_ago: float = 0.1) -> dict:
    """Minimal Yahoo Finance news item in the nested-content format."""
    return {"content": {"title": title, "summary": summary, "pubDate": _pub_date(days_ago),
                        "provider": {"displayName": publisher}}}


# ─── TestGetFinbertAnalyzer ──────────────────────────────────────────────────

class TestGetFinbertAnalyzer:
    def test_returns_singleton_on_repeated_calls(self):
        mock_pipe = MagicMock()
        with patch("huggingface_engine.pipeline", return_value=mock_pipe) as p:
            first = _get_finbert_analyzer()
            second = _get_finbert_analyzer()
        assert first is second
        assert p.call_count == 1  # pipeline() called exactly once

    def test_prefers_local_cache(self):
        mock_pipe = MagicMock()
        with patch("huggingface_engine.pipeline", return_value=mock_pipe) as p:
            _get_finbert_analyzer()
        first_call_kwargs = p.call_args_list[0][1]
        assert first_call_kwargs.get("local_files_only") is True

    def test_falls_back_to_online_when_cache_missing(self):
        mock_pipe = MagicMock()
        def _side_effect(*args, **kwargs):
            if kwargs.get("local_files_only"):
                raise OSError("cache miss")
            return mock_pipe

        with patch("huggingface_engine.pipeline", side_effect=_side_effect) as p:
            result = _get_finbert_analyzer()

        assert result is mock_pipe
        assert p.call_count == 2
        # Second call must NOT have local_files_only
        second_call_kwargs = p.call_args_list[1][1]
        assert "local_files_only" not in second_call_kwargs

    def test_both_pipeline_calls_pin_the_reviewed_revision(self):
        def _side_effect(*args, **kwargs):
            if kwargs.get("local_files_only"):
                raise OSError("cache miss")
            return MagicMock()

        with patch("huggingface_engine.pipeline", side_effect=_side_effect) as p:
            _get_finbert_analyzer()

        assert p.call_count == 2
        revisions = {c[1].get("revision") for c in p.call_args_list}
        assert revisions == {NLP_FINBERT_REVISION}
        assert re.fullmatch(r"[0-9a-f]{40}", NLP_FINBERT_REVISION)

    def test_both_pipeline_calls_require_safetensors(self):
        def _side_effect(*args, **kwargs):
            if kwargs.get("local_files_only"):
                raise OSError("cache miss")
            return MagicMock()

        with patch("huggingface_engine.pipeline", side_effect=_side_effect) as p:
            _get_finbert_analyzer()

        assert p.call_count == 2
        assert all(c[1].get("model_kwargs") == {"use_safetensors": True} for c in p.call_args_list)

    def test_returns_none_when_pipeline_raises(self):
        with patch("huggingface_engine.pipeline", side_effect=RuntimeError("OOM")):
            result = _get_finbert_analyzer()
        assert result is None

    def test_injects_hf_token_when_set(self, monkeypatch):
        monkeypatch.setenv("HF_TOKEN", "hf_testtoken123")
        with patch("huggingface_engine.pipeline", return_value=MagicMock()):
            _get_finbert_analyzer()
        assert os.environ.get("HF_TOKEN") == "hf_testtoken123"

    def test_skips_hf_token_injection_when_not_set(self, monkeypatch):
        monkeypatch.delenv("HF_TOKEN", raising=False)
        with patch("huggingface_engine.pipeline", return_value=MagicMock()):
            _get_finbert_analyzer()
        # Should not crash and HF_TOKEN should remain absent
        assert os.environ.get("HF_TOKEN", "") == ""


# ─── TestScoreText ───────────────────────────────────────────────────────────

class TestScoreText:
    def test_positive_label_returns_positive_prob(self):
        analyzer = _mock_analyzer("positive", 0.85)
        assert _score_text(analyzer, "Great earnings!") == pytest.approx(0.85)

    def test_negative_label_returns_negative_prob(self):
        analyzer = _mock_analyzer("negative", 0.75)
        assert _score_text(analyzer, "Stocks crash hard") == pytest.approx(-0.75)

    def test_neutral_label_returns_zero(self):
        analyzer = _mock_analyzer("neutral", 0.6)
        assert _score_text(analyzer, "Markets flat today") == pytest.approx(0.0)

    def test_text_is_truncated_to_constant(self):
        analyzer = _mock_analyzer("positive", 0.9)
        long_text = "x" * (NLP_TEXT_TRUNCATE_CHARS + 500)
        _score_text(analyzer, long_text)
        actual_text = analyzer.call_args[0][0]
        assert len(actual_text) == NLP_TEXT_TRUNCATE_CHARS

    def test_short_text_is_not_truncated(self):
        analyzer = _mock_analyzer("positive", 0.9)
        short = "Short headline"
        _score_text(analyzer, short)
        assert analyzer.call_args[0][0] == short


# ─── TestFetchAndScoreNews ───────────────────────────────────────────────────

class TestFetchAndScoreNews:
    def test_returns_none_for_empty_news_list(self):
        analyzer = _mock_analyzer("positive", 0.9)
        with patch("huggingface_engine.yahoo_engine") as mock_yf:
            mock_yf.get_news.return_value = []
            result = fetch_and_score_news("AAPL", analyzer, MAX_AGE_DAYS)
        assert result is None
        analyzer.assert_not_called()

    def test_raises_when_news_fetch_failed(self):
        analyzer = _mock_analyzer("positive", 0.9)
        with patch("huggingface_engine.yahoo_engine") as mock_yf:
            mock_yf.get_news.return_value = None
            with pytest.raises(SentimentUnavailableError):
                fetch_and_score_news("AAPL", analyzer, MAX_AGE_DAYS)

    def test_averages_scores_across_items(self):
        # Two items: positive 0.8 and negative 0.4 → average = (0.8 + -0.4) / 2 = 0.2
        call_count = [0]
        def _side_effect(text, **kwargs):
            call_count[0] += 1
            if call_count[0] == 1:
                return [{"label": "positive", "score": 0.8}]
            return [{"label": "negative", "score": 0.4}]

        analyzer = MagicMock(side_effect=_side_effect)
        news = [_news_item(f"Headline {i}") for i in range(2)]
        with patch("huggingface_engine.yahoo_engine") as mock_yf:
            mock_yf.get_news.return_value = news
            result = fetch_and_score_news("AAPL", analyzer, MAX_AGE_DAYS)
        assert result == pytest.approx(0.2)

    def test_headlines_older_than_the_age_limit_are_ignored(self):
        analyzer = _mock_analyzer("positive", 0.9)
        news = [_news_item("Old", days_ago=MAX_AGE_DAYS + 1), _news_item("Fresh", days_ago=MAX_AGE_DAYS - 1)]
        with patch("huggingface_engine.yahoo_engine") as mock_yf:
            mock_yf.get_news.return_value = news
            result = fetch_and_score_news("AAPL", analyzer, MAX_AGE_DAYS)
        assert result == pytest.approx(0.9)
        assert analyzer.call_count == 1

    def test_returns_none_when_every_headline_is_stale(self):
        analyzer = _mock_analyzer("positive", 0.9)
        news = [_news_item(days_ago=MAX_AGE_DAYS + 5)]
        with patch("huggingface_engine.yahoo_engine") as mock_yf:
            mock_yf.get_news.return_value = news
            result = fetch_and_score_news("NWG.L", analyzer, MAX_AGE_DAYS)
        assert result is None
        analyzer.assert_not_called()

    def test_headline_without_a_timestamp_is_not_counted(self):
        analyzer = _mock_analyzer("positive", 0.9)
        undated = {"content": {"title": "Undated", "summary": "x", "provider": {"displayName": "AP"}}}
        with patch("huggingface_engine.yahoo_engine") as mock_yf:
            mock_yf.get_news.return_value = [undated]
            assert fetch_and_score_news("AAPL", analyzer, MAX_AGE_DAYS) is None

    def test_respects_nlp_news_fetch_limit(self):
        analyzer = _mock_analyzer("positive", 0.5)
        news = [_news_item(f"Headline {i}") for i in range(NLP_NEWS_FETCH_LIMIT + 10)]
        with patch("huggingface_engine.yahoo_engine") as mock_yf:
            mock_yf.get_news.return_value = news
            fetch_and_score_news("AAPL", analyzer, MAX_AGE_DAYS)
        assert analyzer.call_count == NLP_NEWS_FETCH_LIMIT

    def test_skips_blank_items(self):
        analyzer = _mock_analyzer("positive", 0.9)
        blank = {"content": {"title": "", "summary": "", "pubDate": _pub_date(), "provider": {"displayName": ""}}}
        with patch("huggingface_engine.yahoo_engine") as mock_yf:
            mock_yf.get_news.return_value = [blank]
            result = fetch_and_score_news("AAPL", analyzer, MAX_AGE_DAYS)
        assert result is None
        analyzer.assert_not_called()

    def test_handles_flat_payload_without_content_key(self):
        analyzer = _mock_analyzer("positive", 0.7)
        flat_item = {"title": "Flat headline", "summary": "Flat summary", "publisher": "BBC",
                     "providerPublishTime": int(datetime.now(timezone.utc).timestamp()) - 3600}
        with patch("huggingface_engine.yahoo_engine") as mock_yf:
            mock_yf.get_news.return_value = [flat_item]
            result = fetch_and_score_news("AAPL", analyzer, MAX_AGE_DAYS)
        assert result == pytest.approx(0.7)

    def test_raises_when_every_fresh_headline_fails_to_score(self):
        analyzer = MagicMock(side_effect=RuntimeError("model error"))
        news = [_news_item()]
        with patch("huggingface_engine.yahoo_engine") as mock_yf:
            mock_yf.get_news.return_value = news
            with pytest.raises(SentimentUnavailableError):
                fetch_and_score_news("AAPL", analyzer, MAX_AGE_DAYS)


# ─── TestUpdateAllSentiment ──────────────────────────────────────────────────

MACRO_TICKERS = {"^GSPC", "^NDX", "^FTSE", "^FTMC", "GBPUSD=X", "DX-Y.NYB"}


class TestUpdateAllSentiment:
    def _make_cursor(self, rowcount: int = 1) -> MagicMock:
        cursor = MagicMock()
        cursor.rowcount = rowcount
        return cursor

    def _make_conn(self, rowcount: int = 1):
        cursor = self._make_cursor(rowcount)
        conn = MagicMock()
        conn.cursor.return_value = cursor
        return conn, cursor

    def _run(self, tickers, fetch, rowcount: int = 1):
        conn, cursor = self._make_conn(rowcount)
        with patch("huggingface_engine._get_finbert_analyzer", return_value=_mock_analyzer()), \
             patch("huggingface_engine.fetch_and_score_news", side_effect=fetch), \
             patch("huggingface_engine.get_connection", return_value=conn), \
             patch("huggingface_engine.notify") as mock_notify, \
             patch("huggingface_engine.time") as mock_time:
            mock_time.sleep = MagicMock()
            update_all_sentiment(tickers)
        return conn, cursor, mock_notify

    def test_aborts_when_analyzer_unavailable(self):
        with patch("huggingface_engine._get_finbert_analyzer", return_value=None), \
             patch("huggingface_engine.get_connection") as mock_conn:
            update_all_sentiment(["AAPL"])
        mock_conn.assert_not_called()

    def test_always_includes_macro_tickers(self):
        _, cursor, _ = self._run([], lambda *a: 0.0)

        # UPDATE params are (score, scored_at, ticker, ticker)
        updated_tickers = {c.args[1][2] for c in cursor.execute.call_args_list if c.args[1:]}
        assert MACRO_TICKERS.issubset(updated_tickers)

    def test_update_path_when_rowcount_positive(self):
        _, cursor, _ = self._run(["AAPL"], lambda *a: 0.5, rowcount=1)

        sql_calls = [c.args[0].strip().upper()[:6] for c in cursor.execute.call_args_list]
        assert all(s == "UPDATE" for s in sql_calls)

    def test_score_is_written_with_a_scored_at_timestamp(self):
        _, cursor, _ = self._run(["AAPL"], lambda *a: 0.5)

        score, scored_at, ticker, _ = next(
            c.args[1] for c in cursor.execute.call_args_list if c.args[1][2] == "AAPL"
        )
        assert score == 0.5
        assert datetime.strptime(scored_at, "%Y-%m-%d %H:%M:%S")

    def test_upsert_fires_when_update_matches_no_rows(self):
        _, cursor, _ = self._run(["NEW_TICKER"], lambda *a: 0.5, rowcount=0)

        sql_calls = [c.args[0].strip().upper()[:6] for c in cursor.execute.call_args_list]
        assert "UPDATE" in sql_calls
        assert "INSERT" in sql_calls

    def test_no_headlines_clears_the_score_without_inserting(self):
        _, cursor, _ = self._run(["VWRL.L"], lambda *a: None, rowcount=0)

        params = [c.args[1] for c in cursor.execute.call_args_list if c.args[1][2] == "VWRL.L"]
        assert params == [(None, None, "VWRL.L", "VWRL.L")]
        assert all(c.args[0].strip().upper()[:6] != "INSERT" for c in cursor.execute.call_args_list)

    def test_unavailable_news_leaves_the_stored_score_untouched(self):
        def _fetch(ticker, *a):
            if ticker == "AAPL":
                raise SentimentUnavailableError(ticker)
            return 0.1

        _, cursor, _ = self._run(["AAPL", "MSFT"], _fetch)

        written = {c.args[1][2] for c in cursor.execute.call_args_list}
        assert "AAPL" not in written
        assert "MSFT" in written

    def test_connection_always_closed(self):
        conn, _, _ = self._run(["AAPL"], lambda *a: 0.0)
        conn.close.assert_called_once()

    def test_degraded_scan_alerts_once_and_rearms_after_a_healthy_scan(self):
        huggingface_engine._scan_degraded_alerted = False
        _, _, first = self._run(["AAPL", "MSFT"], lambda *a: None)
        _, _, repeat = self._run(["AAPL", "MSFT"], lambda *a: None)
        _, _, healthy = self._run(["AAPL", "MSFT"], lambda *a: 0.3)
        _, _, again = self._run(["AAPL", "MSFT"], lambda *a: None)

        assert first.call_count == 1
        assert first.call_args.args[0] == "sentiment_scan_degraded"
        assert repeat.call_count == 0
        assert healthy.call_count == 0
        assert again.call_count == 1
        huggingface_engine._scan_degraded_alerted = False

    def test_healthy_scan_does_not_alert(self):
        huggingface_engine._scan_degraded_alerted = False
        _, _, mock_notify = self._run(["AAPL", "MSFT"], lambda *a: 0.3)
        mock_notify.assert_not_called()


# ─── TestRunCentralBankNlpAlert ──────────────────────────────────────────────

def _cb_news(title: str, summary: str) -> dict:
    return {"content": {"title": title, "summary": summary}}


class TestRunCentralBankNlpAlert:
    def _base_patches(self, news: list, avg_score: float = 0.0):
        """Context manager stack for the common happy-path mock wiring."""
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor

        return (
            patch("huggingface_engine._get_finbert_analyzer", return_value=_mock_analyzer()),
            patch("huggingface_engine.yahoo_engine") ,
            patch("huggingface_engine._score_text", return_value=avg_score),
            patch("notification_engine.nextcloud_talk.send_text_message"),
            patch("huggingface_engine.get_connection", return_value=mock_conn),
            patch("huggingface_engine.load_config", return_value={}),
        )

    def test_returns_false_for_unsupported_currency(self):
        with patch("huggingface_engine._get_finbert_analyzer", return_value=_mock_analyzer()), \
             patch("huggingface_engine.load_config", return_value={}):
            result = run_central_bank_nlp_alert("ECB Meeting", "EUR")
        assert result is False

    def test_returns_false_when_no_news(self):
        with patch("huggingface_engine._get_finbert_analyzer", return_value=_mock_analyzer()), \
             patch("huggingface_engine.yahoo_engine") as mock_yf, \
             patch("huggingface_engine.load_config", return_value={}):
            mock_yf.get_news.return_value = []
            result = run_central_bank_nlp_alert("FOMC Decision", "USD")
        assert result is False

    def test_returns_false_when_analyzer_unavailable(self):
        with patch("huggingface_engine._get_finbert_analyzer", return_value=None), \
             patch("huggingface_engine.load_config", return_value={}):
            result = run_central_bank_nlp_alert("FOMC Decision", "USD")
        assert result is False

    def test_filters_out_irrelevant_headlines(self):
        irrelevant = [_cb_news("Taylor Swift tour dates", "Pop star announces shows")]
        with patch("huggingface_engine._get_finbert_analyzer", return_value=_mock_analyzer()), \
             patch("huggingface_engine.yahoo_engine") as mock_yf, \
             patch("huggingface_engine.load_config", return_value={}):
            mock_yf.get_news.return_value = irrelevant
            result = run_central_bank_nlp_alert("FOMC Decision", "USD")
        assert result is False

    def test_accepts_headline_containing_rate(self):
        news = [_cb_news("Fed holds interest rate steady", "Federal Reserve keeps rate")]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = MagicMock()
        with patch("huggingface_engine._get_finbert_analyzer", return_value=_mock_analyzer()), \
             patch("huggingface_engine.yahoo_engine") as mock_yf, \
             patch("huggingface_engine._score_text", return_value=0.0), \
             patch("notification_engine.nextcloud_talk.send_text_message"), \
             patch("huggingface_engine.get_connection", return_value=mock_conn), \
             patch("huggingface_engine.load_config", return_value={}):
            mock_yf.get_news.return_value = news
            result = run_central_bank_nlp_alert("FOMC Decision", "USD")
        assert result is True

    def test_classifies_hawkish_below_negative_threshold(self):
        hawkish_score = -(NLP_CB_TONE_THRESHOLD + 0.1)
        news = [_cb_news("Fed hikes rates aggressively", "inflation fight")]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = MagicMock()
        dispatched = []
        with patch("huggingface_engine._get_finbert_analyzer", return_value=_mock_analyzer()), \
             patch("huggingface_engine.yahoo_engine") as mock_yf, \
             patch("huggingface_engine._score_text", return_value=hawkish_score), \
             patch("notification_engine.nextcloud_talk.send_text_message",
                   side_effect=lambda msg, cfg: dispatched.append(msg)), \
             patch("huggingface_engine.get_connection", return_value=mock_conn), \
             patch("huggingface_engine.load_config", return_value={}):
            mock_yf.get_news.return_value = news
            result = run_central_bank_nlp_alert("FOMC Decision", "USD")
        assert result is True
        assert "HAWKISH" in dispatched[0]

    def test_classifies_dovish_above_positive_threshold(self):
        dovish_score = NLP_CB_TONE_THRESHOLD + 0.1
        news = [_cb_news("Fed signals rate cuts ahead", "easing inflation")]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = MagicMock()
        dispatched = []
        with patch("huggingface_engine._get_finbert_analyzer", return_value=_mock_analyzer()), \
             patch("huggingface_engine.yahoo_engine") as mock_yf, \
             patch("huggingface_engine._score_text", return_value=dovish_score), \
             patch("notification_engine.nextcloud_talk.send_text_message",
                   side_effect=lambda msg, cfg: dispatched.append(msg)), \
             patch("huggingface_engine.get_connection", return_value=mock_conn), \
             patch("huggingface_engine.load_config", return_value={}):
            mock_yf.get_news.return_value = news
            result = run_central_bank_nlp_alert("FOMC Decision", "USD")
        assert result is True
        assert "DOVISH" in dispatched[0]

    def test_classifies_neutral_within_threshold(self):
        neutral_score = NLP_CB_TONE_THRESHOLD * 0.5
        news = [_cb_news("Fed unchanged on rate policy", "no change to inflation stance")]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = MagicMock()
        dispatched = []
        with patch("huggingface_engine._get_finbert_analyzer", return_value=_mock_analyzer()), \
             patch("huggingface_engine.yahoo_engine") as mock_yf, \
             patch("huggingface_engine._score_text", return_value=neutral_score), \
             patch("notification_engine.nextcloud_talk.send_text_message",
                   side_effect=lambda msg, cfg: dispatched.append(msg)), \
             patch("huggingface_engine.get_connection", return_value=mock_conn), \
             patch("huggingface_engine.load_config", return_value={}):
            mock_yf.get_news.return_value = news
            result = run_central_bank_nlp_alert("FOMC Decision", "USD")
        assert result is True
        assert "NEUTRAL" in dispatched[0]

    def test_dispatches_to_nextcloud_and_logs_to_db(self):
        import database as _db
        news = [_cb_news("Bank of England rate decision", "BoE holds rate")]
        conn = _db.get_connection()
        conn.execute("DELETE FROM system_notifications WHERE message_type = 'Macro NLP'")
        conn.commit()
        conn.close()
        with patch("huggingface_engine._get_finbert_analyzer", return_value=_mock_analyzer()), \
             patch("huggingface_engine.yahoo_engine") as mock_yf, \
             patch("huggingface_engine._score_text", return_value=0.0), \
             patch("notification_engine.nextcloud_talk.send_text_message", return_value=True) as mock_send, \
             patch("notification_engine.load_config", return_value={}), \
             patch("huggingface_engine.load_config", return_value={}):
            mock_yf.get_news.return_value = news
            result = run_central_bank_nlp_alert("BoE Decision", "GBP")

        assert result is True
        # Nextcloud channel attempted (cb_nlp_alert default routing has Nextcloud on).
        mock_send.assert_called_once()
        # In-app channel wrote a 'Macro NLP' row to the database via the unified router.
        conn = _db.get_connection()
        rows = conn.execute(
            "SELECT message_type FROM system_notifications WHERE message_type = 'Macro NLP'"
        ).fetchall()
        conn.close()
        assert rows, "notify() must write a 'Macro NLP' in-app row through the router."

    def test_usd_uses_treasury_proxy(self):
        news = [_cb_news("Rate hike expected by Fed", "Federal Reserve decision")]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = MagicMock()
        with patch("huggingface_engine._get_finbert_analyzer", return_value=_mock_analyzer()), \
             patch("huggingface_engine.yahoo_engine") as mock_yf, \
             patch("huggingface_engine._score_text", return_value=0.0), \
             patch("notification_engine.nextcloud_talk.send_text_message"), \
             patch("huggingface_engine.get_connection", return_value=mock_conn), \
             patch("huggingface_engine.load_config", return_value={}):
            mock_yf.get_news.return_value = news
            run_central_bank_nlp_alert("FOMC Decision", "USD")
        mock_yf.get_news.assert_called_once_with("^TNX")

    def test_gbp_uses_ftse_proxy(self):
        news = [_cb_news("Bank of England rate hold", "BoE inflation target")]
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = MagicMock()
        with patch("huggingface_engine._get_finbert_analyzer", return_value=_mock_analyzer()), \
             patch("huggingface_engine.yahoo_engine") as mock_yf, \
             patch("huggingface_engine._score_text", return_value=0.0), \
             patch("notification_engine.nextcloud_talk.send_text_message"), \
             patch("huggingface_engine.get_connection", return_value=mock_conn), \
             patch("huggingface_engine.load_config", return_value={}):
            mock_yf.get_news.return_value = news
            run_central_bank_nlp_alert("BoE Decision", "GBP")
        mock_yf.get_news.assert_called_once_with("^FTSE")
