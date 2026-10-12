"""
tests/test_sentiment_freshness.py — AGE-BOUNDED SENTIMENT SCORES

Yahoo retired the news endpoint behind Ticker.news on 2026-10-02 and the scan stored its empty answer as a
0.0 score. A score now carries quant_signals.sentiment_scored_at, and:
  - the migration clears the 0.0 rows written during that outage and nothing else
  - sentiment_max_age_days()/sentiment_fresh_since() derive the cutoff from SENTIMENT_ENGINE.MAX_NEWS_AGE_DAYS
  - the four readers that carry the latest non-NULL score forward (Portfolio/Watchlist rows, Stock Detail,
    Market Pulse, Screener) stop showing a score once it is older than the cutoff
  - the three readers of the latest row's score (AI prompt, Crash/Moonshot alert metadata, Index detail) do too
"""

import sqlite3
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db
import ai_engine
import db_helpers
import db_schema
import market_pulse as _mp
import page_data_signal_rows
import page_data_stock
from intraday_orchestrator import IntradayOrchestrator

TICKERS = ("SENTMIG_A", "SENTMIG_B", "SENTMIG_C", "SENTMIG_D", "SENTFRESH_1", "SENTFRESH_2", "^GSPC")


def _conn():
    conn = sqlite3.connect(_db.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _utc(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


def _seed_quant(conn, ticker, date, score, scored_at):
    conn.execute(
        "INSERT OR REPLACE INTO quant_signals (ticker, date, sentiment_score, sentiment_scored_at) VALUES (?, ?, ?, ?)",
        (ticker, date, score, scored_at),
    )


@pytest.fixture(autouse=True)
def _clean_rows():
    yield
    conn = _conn()
    for table in ("quant_signals", "stock_signals", "market_universe"):
        conn.executemany(f"DELETE FROM {table} WHERE ticker = ?", [(t,) for t in TICKERS])
    conn.commit()
    conn.close()


class TestOutageRowMigration:
    def test_clears_only_zero_scores_written_during_the_outage(self):
        conn = _conn()
        _seed_quant(conn, "SENTMIG_A", "2026-10-05", 0.0, None)
        _seed_quant(conn, "SENTMIG_B", "2026-10-05", 0.4, None)
        _seed_quant(conn, "SENTMIG_C", "2026-10-05", 0.0, "2026-10-12 08:12:00")
        _seed_quant(conn, "SENTMIG_D", "2026-09-30", 0.0, None)
        conn.commit()

        db_schema.migrate_db(conn, conn.cursor())
        conn.commit()

        scores = {r["ticker"]: r["sentiment_score"] for r in conn.execute(
            "SELECT ticker, sentiment_score FROM quant_signals WHERE ticker LIKE 'SENTMIG_%'"
        )}
        conn.close()
        assert scores == {"SENTMIG_A": None, "SENTMIG_B": 0.4, "SENTMIG_C": 0.0, "SENTMIG_D": 0.0}


class TestFreshnessHelpers:
    def test_default_age_limit_is_three_days(self):
        with patch("db_helpers.load_config", return_value={}):
            assert db_helpers.sentiment_max_age_days() == 3

    def test_configured_age_limit_drives_the_cutoff(self):
        cfg = {"SCHEDULING": {"SENTIMENT_ENGINE": {"MAX_NEWS_AGE_DAYS": 5}}}
        with patch("db_helpers.load_config", return_value=cfg):
            assert db_helpers.sentiment_max_age_days() == 5
            cutoff = datetime.strptime(db_helpers.sentiment_fresh_since(), "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        expected = datetime.now(timezone.utc) - timedelta(days=5)
        assert abs((cutoff - expected).total_seconds()) < 5


def _seed_stock_with_history(conn, ticker, *, older_scored_at, latest_score=None, latest_scored_at=None):
    conn.execute(
        "INSERT OR REPLACE INTO stock_signals (ticker, company_name, current_price, currency, last_updated) "
        "VALUES (?, ?, 10.0, 'USD', datetime('now'))", (ticker, ticker),
    )
    conn.execute(
        "INSERT OR REPLACE INTO market_universe (ticker, company_name, is_freetrade) VALUES (?, ?, 1)", (ticker, ticker),
    )
    _seed_quant(conn, ticker, "2026-10-08", 0.61, older_scored_at)
    _seed_quant(conn, ticker, "2026-10-09", latest_score, latest_scored_at)
    conn.commit()


class TestReadersBoundScoreAge:
    def test_portfolio_watchlist_rows_show_a_recent_score_and_hide_an_old_one(self):
        conn = _conn()
        _seed_stock_with_history(conn, "SENTFRESH_1", older_scored_at=_utc(1))
        _seed_stock_with_history(conn, "SENTFRESH_2", older_scored_at=_utc(10))
        conn.close()

        rows, _ = page_data_signal_rows.fetch_watchlist_signal_rows("SPY", ["SENTFRESH_1", "SENTFRESH_2"])

        scores = {r["ticker"]: r["sentiment_score"] for r in rows}
        assert scores == {"SENTFRESH_1": pytest.approx(0.61), "SENTFRESH_2": None}

    def test_stock_detail_shows_a_recent_score_and_hides_an_old_one(self):
        conn = _conn()
        _seed_stock_with_history(conn, "SENTFRESH_1", older_scored_at=_utc(1))
        _seed_stock_with_history(conn, "SENTFRESH_2", older_scored_at=_utc(10))
        conn.close()

        fresh, _ = page_data_stock.load_stock_row("SENTFRESH_1")
        old, _ = page_data_stock.load_stock_row("SENTFRESH_2")

        assert fresh["sentiment_score"] == pytest.approx(0.61)
        assert old["sentiment_score"] is None

    def test_market_pulse_shows_a_recent_score_and_hides_an_old_one(self):
        conn = _conn()
        _seed_stock_with_history(conn, "SENTFRESH_1", older_scored_at=_utc(1))
        _seed_stock_with_history(conn, "SENTFRESH_2", older_scored_at=_utc(10))
        conn.close()

        result = _mp.get_cached_pulse_from_db(["SENTFRESH_1", "SENTFRESH_2"], refresh_rate=60)

        scores = {r["ticker"]: r["sentiment_score"] for r in result["assets"]}
        assert scores == {"SENTFRESH_1": pytest.approx(0.61), "SENTFRESH_2": None}

    def test_screener_shows_a_recent_score_and_hides_an_old_one(self, client):
        conn = _conn()
        _seed_stock_with_history(conn, "SENTFRESH_1", older_scored_at=_utc(1))
        _seed_stock_with_history(conn, "SENTFRESH_2", older_scored_at=_utc(10))
        conn.close()

        rows = client.get("/api/screener-data").json()["data"]

        scores = {r["ticker"]: r["sentiment_score"] for r in rows if r["ticker"].startswith("SENTFRESH_")}
        assert scores == {"SENTFRESH_1": pytest.approx(0.61), "SENTFRESH_2": None}

    def test_a_newer_row_without_a_score_does_not_resurrect_an_old_one(self):
        conn = _conn()
        _seed_stock_with_history(conn, "SENTFRESH_2", older_scored_at=_utc(4), latest_score=None)
        conn.close()

        rows, _ = page_data_signal_rows.fetch_watchlist_signal_rows("SPY", ["SENTFRESH_2"])

        assert rows[0]["sentiment_score"] is None


class TestRowLevelReadersBoundScoreAge:
    @staticmethod
    def _seed(ticker, scored_at, score=0.4321):
        conn = _conn()
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, company_name, current_price, currency, last_updated) "
            "VALUES (?, ?, 10.0, 'USD', datetime('now'))", (ticker, ticker),
        )
        _seed_quant(conn, ticker, "2099-01-01", score, scored_at)
        conn.commit()
        conn.close()

    def test_alert_metadata_shows_a_recent_score_and_hides_an_old_one(self):
        self._seed("SENTFRESH_1", _utc(1))
        self._seed("SENTFRESH_2", _utc(10))

        meta = IntradayOrchestrator().get_asset_metadata(["SENTFRESH_1", "SENTFRESH_2"])

        assert meta["SENTFRESH_1"]["sentiment_score"] == pytest.approx(0.4321)
        assert meta["SENTFRESH_2"]["sentiment_score"] is None

    def test_ai_prompt_shows_a_recent_score_and_hides_an_old_one(self):
        self._seed("SENTFRESH_1", _utc(1))
        self._seed("SENTFRESH_2", _utc(10))
        engine = ai_engine.AIPromptEngine()

        fresh = engine.generate_prompt("SENTFRESH_1", "Quantamental Deep-Dive")
        old = engine.generate_prompt("SENTFRESH_2", "Quantamental Deep-Dive")

        assert "VADER Media Sentiment: 0.432" in fresh
        assert "VADER Media Sentiment: N/A" in old

    def test_index_detail_shows_a_recent_score_and_hides_an_old_one(self, client):
        conn = _conn()
        _seed_quant(conn, "^GSPC", "2099-01-01", 0.4321, _utc(1))
        conn.commit()
        conn.close()
        assert "(0.432)" in client.get("/index/^GSPC").text

        conn = _conn()
        _seed_quant(conn, "^GSPC", "2099-01-01", 0.4321, _utc(10))
        conn.commit()
        conn.close()
        assert "(0.432)" not in client.get("/index/^GSPC").text
