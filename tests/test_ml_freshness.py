"""
tests/test_ml_freshness.py — AGE-BOUNDED ML SCORES

Daily ML Inference serves from the newest quant_signals row of every ticker. A score carries
quant_signals.ml_scored_at, and:
  - ml_fresh_cutoff_date()/ml_fresh_since() count ML_FEATURE_MAX_AGE_TRADING_DAYS trading days, not calendar days
  - inference_cross_section() keeps feature rows inside that limit and on dates that meet the coverage threshold
  - the readers that carry the latest non-NULL score forward (Portfolio/Watchlist rows, Stock Detail) hide a
    score once it is older than the cutoff, and still show one when a newer row has no score yet
  - the readers of the latest row's own score (Dividend Harvest and GARP reports, AI prompt, alert metadata,
    Screener) hide a score once it is older than the cutoff
  - the ML Quantile Price Band readers do the same with quant_signals.quantile_scored_at, on the latest row
    (Portfolio/Watchlist rows, Stock Detail) and carried forward (get_latest_quantile_bands)
  - the universe indicator refresh reads Parquet only and does not sleep
"""

import sqlite3
import sys
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import ai_engine
from db import database as _db
from db import helpers as db_helpers
import page_data_signal_rows
import page_data_stock
import scheduler_jobs
from intraday_orchestrator import IntradayOrchestrator
from ml_features import inference_cross_section
from reports_engine import get_dividend_harvest_setups, get_garp_tenbaggers

pytestmark = [pytest.mark.restore_tables("market_pulse_cache", "quant_scan_states", "scheduler_run_log", "system_notifications", "yahoo_api_call_log", "yahoo_api_stats"), pytest.mark.usefixtures("offline_yahoo")]

TICKERS = ("MLFRESH_1", "MLFRESH_2", "MLFRESH_3")


def _conn():
    conn = sqlite3.connect(_db.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _utc(days_ago: float) -> str:
    return (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d %H:%M:%S")


@pytest.fixture(autouse=True)
def _clean_rows():
    yield
    conn = _conn()
    for table in ("quant_signals", "stock_signals", "market_universe", "ticker_metadata"):
        conn.executemany(f"DELETE FROM {table} WHERE ticker = ?", [(t,) for t in TICKERS])
    conn.commit()
    conn.close()


def _seed(conn, ticker, older_scored_at, latest_score=None, latest_scored_at=None):
    conn.execute("INSERT OR REPLACE INTO stock_signals (ticker, last_updated) VALUES (?, ?)", (ticker, _utc(0)))
    conn.execute(
        "INSERT OR REPLACE INTO market_universe (ticker, company_name, is_freetrade) VALUES (?, ?, 1)", (ticker, ticker),
    )
    conn.execute(
        "INSERT OR REPLACE INTO quant_signals (ticker, date, ml_confidence_score, ml_scored_at) VALUES (?, ?, ?, ?)",
        (ticker, "2026-10-08", 61.0, older_scored_at),
    )
    conn.execute(
        "INSERT OR REPLACE INTO quant_signals (ticker, date, ml_confidence_score, ml_scored_at) VALUES (?, ?, ?, ?)",
        (ticker, "2026-10-09", latest_score, latest_scored_at),
    )
    conn.commit()


class TestCutoffCountsTradingDays:
    def test_a_monday_cutoff_is_the_previous_wednesday(self):
        assert db_helpers.ml_fresh_cutoff_date(date(2026, 11, 9)) == date(2026, 11, 4)

    def test_a_saturday_cutoff_skips_the_weekend(self):
        assert db_helpers.ml_fresh_cutoff_date(date(2026, 11, 7)) == date(2026, 11, 3)

    def test_ml_fresh_since_is_midnight_of_the_cutoff_date(self):
        with patch("db.helpers.datetime") as clock:
            clock.now.return_value = datetime(2026, 11, 9, 1, 30, tzinfo=timezone.utc)
            assert db_helpers.ml_fresh_since() == "2026-11-04 00:00:00"


class TestInferenceCrossSection:
    @staticmethod
    def _frame(spec):
        rows = [(f"T{d}_{i}", d) for d, n in spec.items() for i in range(n)]
        return pd.DataFrame(rows, columns=["ticker", "date"])

    def test_keeps_only_fresh_dates_that_meet_the_threshold(self):
        df = self._frame({"2026-11-06": 40, "2026-11-05": 5, "2026-10-01": 50})
        usable, summary = inference_cross_section(df, date(2026, 11, 9), 30)
        assert set(usable["date"]) == {"2026-11-06"}
        assert len(usable) == 40
        assert (summary["too_old"], summary["thin_dates"]) == (50, 5)
        assert (summary["min_date"], summary["max_date"]) == ("2026-11-06", "2026-11-06")
        assert summary["date_counts"] == {"2026-11-06": 40, "2026-11-05": 5}

    def test_a_date_at_the_cutoff_is_still_fresh_and_the_day_before_is_not(self):
        df = self._frame({"2026-11-04": 30, "2026-11-03": 30})
        usable, _ = inference_cross_section(df, date(2026, 11, 9), 30)
        assert set(usable["date"]) == {"2026-11-04"}

    def test_nothing_usable_reports_no_date_range(self):
        usable, summary = inference_cross_section(self._frame({"2026-10-01": 50}), date(2026, 11, 9), 30)
        assert usable.empty
        assert (summary["min_date"], summary["max_date"]) == (None, None)


class TestReadersBoundMlScoreAge:
    def test_portfolio_watchlist_rows_show_a_recent_score_and_hide_an_old_or_unstamped_one(self):
        conn = _conn()
        _seed(conn, "MLFRESH_1", older_scored_at=_utc(1))
        _seed(conn, "MLFRESH_2", older_scored_at=_utc(30))
        _seed(conn, "MLFRESH_3", older_scored_at=None)
        conn.close()

        rows, _ = page_data_signal_rows.fetch_watchlist_signal_rows("SPY", list(TICKERS))

        scores = {r["ticker"]: r["ml_confidence_score"] for r in rows}
        assert scores == {"MLFRESH_1": pytest.approx(61.0), "MLFRESH_2": None, "MLFRESH_3": None}

    def test_a_newer_row_without_a_score_does_not_hide_a_fresh_older_score(self):
        conn = _conn()
        _seed(conn, "MLFRESH_1", older_scored_at=_utc(0.5), latest_score=None)
        conn.close()
        rows, _ = page_data_signal_rows.fetch_watchlist_signal_rows("SPY", ["MLFRESH_1"])
        assert rows[0]["ml_confidence_score"] == pytest.approx(61.0)

    def test_stock_detail_shows_a_recent_score_and_hides_an_old_one(self):
        conn = _conn()
        _seed(conn, "MLFRESH_1", older_scored_at=_utc(1))
        _seed(conn, "MLFRESH_2", older_scored_at=_utc(30))
        conn.close()

        fresh, _ = page_data_stock.load_stock_row("MLFRESH_1")
        old, _ = page_data_stock.load_stock_row("MLFRESH_2")

        assert fresh["ml_confidence_score"] == pytest.approx(61.0)
        assert old["ml_confidence_score"] is None


def _seed_latest_row(ticker, scored_at, quantile_scored_at=None):
    conn = _conn()
    conn.execute(
        "INSERT OR REPLACE INTO stock_signals (ticker, company_name, current_price, currency, last_updated, "
        "dividend_yield, ex_dividend_date, composite_score, quote_type, peter_lynch_peg, revenue_growth, roe, forward_pe) "
        "VALUES (?, ?, 100.0, 'USD', datetime('now'), 0.05, '2026-09-01', 100, 'EQUITY', 0.5, 0.3, 0.3, 20)",
        (ticker, ticker),
    )
    conn.execute(
        "INSERT OR REPLACE INTO market_universe (ticker, company_name, is_freetrade, is_index) VALUES (?, ?, 1, 1)",
        (ticker, ticker),
    )
    conn.execute("INSERT OR REPLACE INTO ticker_metadata (ticker, market_cap) VALUES (?, 5e9)", (ticker,))
    conn.execute(
        "INSERT OR REPLACE INTO quant_signals (ticker, date, close_price, ml_confidence_score, ml_scored_at, "
        "price_q10, price_q90, quantile_scored_at) VALUES (?, '2099-01-01', 100.0, 61.0, ?, 90.0, 130.0, ?)",
        (ticker, scored_at, quantile_scored_at),
    )
    conn.commit()
    conn.close()


class TestLatestRowReadersBoundMlScoreAge:
    def test_dividend_harvest_report_shows_a_recent_score_and_hides_an_old_one(self):
        _seed_latest_row("MLFRESH_1", _utc(1))
        _seed_latest_row("MLFRESH_2", _utc(30))
        _seed_latest_row("MLFRESH_3", None)

        rows = get_dividend_harvest_setups(min_yield=0.0, min_score=0)

        scores = {r["ticker"]: r["ml_confidence_score"] for r in rows if r["ticker"] in TICKERS}
        assert scores == {"MLFRESH_1": pytest.approx(61.0), "MLFRESH_2": None, "MLFRESH_3": None}

    def test_garp_report_shows_a_recent_score_and_hides_an_old_one(self):
        _seed_latest_row("MLFRESH_1", _utc(1))
        _seed_latest_row("MLFRESH_2", _utc(30))
        _seed_latest_row("MLFRESH_3", None)

        rows = get_garp_tenbaggers()

        scores = {r["ticker"]: r["ml_confidence_score"] for r in rows if r["ticker"] in TICKERS}
        assert scores == {"MLFRESH_1": pytest.approx(61.0), "MLFRESH_2": None, "MLFRESH_3": None}

    def test_screener_shows_a_recent_score_and_hides_an_old_one(self, client):
        _seed_latest_row("MLFRESH_1", _utc(1))
        _seed_latest_row("MLFRESH_2", _utc(30))
        _seed_latest_row("MLFRESH_3", None)

        rows = client.get("/api/screener-data").json()["data"]

        scores = {r["ticker"]: r["ml_confidence_score"] for r in rows if r["ticker"] in TICKERS}
        assert scores == {"MLFRESH_1": pytest.approx(61.0), "MLFRESH_2": None, "MLFRESH_3": None}

    def test_alert_metadata_shows_a_recent_score_and_hides_an_old_one(self):
        _seed_latest_row("MLFRESH_1", _utc(1))
        _seed_latest_row("MLFRESH_2", _utc(30))
        _seed_latest_row("MLFRESH_3", None)

        meta = IntradayOrchestrator().get_asset_metadata(list(TICKERS))

        assert meta["MLFRESH_1"]["ml_confidence_score"] == pytest.approx(61.0)
        assert meta["MLFRESH_2"]["ml_confidence_score"] is None
        assert meta["MLFRESH_3"]["ml_confidence_score"] is None

    def test_ai_prompt_shows_a_recent_score_and_hides_an_old_one(self):
        _seed_latest_row("MLFRESH_1", _utc(1))
        _seed_latest_row("MLFRESH_2", _utc(30))
        engine = ai_engine.AIPromptEngine()

        fresh = engine.generate_prompt("MLFRESH_1", "Quantamental Deep-Dive")
        old = engine.generate_prompt("MLFRESH_2", "Quantamental Deep-Dive")

        assert "ML Confidence Score (>3% return in 5d): 61.0%" in fresh
        assert "ML Confidence Score (>3% return in 5d): N/A" in old


class TestQuantileBandsBoundAge:
    @staticmethod
    def _bands(row):
        return row["price_q10"], row["price_q90"]

    def test_portfolio_watchlist_rows_show_a_recent_band_and_hide_an_old_or_unstamped_one(self):
        _seed_latest_row("MLFRESH_1", _utc(1), _utc(1))
        _seed_latest_row("MLFRESH_2", _utc(1), _utc(30))
        _seed_latest_row("MLFRESH_3", _utc(1), None)

        rows, _ = page_data_signal_rows.fetch_watchlist_signal_rows("SPY", list(TICKERS))

        bands = {r["ticker"]: self._bands(r) for r in rows}
        assert bands == {"MLFRESH_1": (90.0, 130.0), "MLFRESH_2": (None, None), "MLFRESH_3": (None, None)}

    def test_stock_detail_shows_a_recent_band_and_hides_an_old_one(self):
        _seed_latest_row("MLFRESH_1", _utc(1), _utc(1))
        _seed_latest_row("MLFRESH_2", _utc(1), _utc(30))

        fresh, _ = page_data_stock.load_stock_row("MLFRESH_1")
        old, _ = page_data_stock.load_stock_row("MLFRESH_2")

        assert self._bands(fresh) == (90.0, 130.0)
        assert self._bands(old) == (None, None)

    def test_a_band_does_not_depend_on_the_score_stamp(self):
        _seed_latest_row("MLFRESH_1", _utc(30), _utc(1))

        rows, _ = page_data_signal_rows.fetch_watchlist_signal_rows("SPY", ["MLFRESH_1"])

        assert (rows[0]["ml_confidence_score"], self._bands(rows[0])) == (None, (90.0, 130.0))

    def test_latest_bands_return_a_recent_band_and_skip_an_old_or_unstamped_one(self):
        _seed_latest_row("MLFRESH_1", _utc(1), _utc(1))
        _seed_latest_row("MLFRESH_2", _utc(1), _utc(30))
        _seed_latest_row("MLFRESH_3", _utc(1), None)

        rows = db_helpers.get_latest_quantile_bands(list(TICKERS))

        assert [(r["ticker"], r["price_q10"], r["price_q90"]) for r in rows] == [("MLFRESH_1", 90.0, 130.0)]

    def test_a_newer_row_without_a_band_does_not_hide_a_fresh_older_band(self):
        _seed_latest_row("MLFRESH_1", _utc(1), _utc(0.5))
        conn = _conn()
        conn.execute("INSERT INTO quant_signals (ticker, date, close_price) VALUES ('MLFRESH_1', '2099-01-02', 101.0)")
        conn.commit()
        conn.close()

        rows = db_helpers.get_latest_quantile_bands(["MLFRESH_1"])

        assert [(r["date"], r["price_q10"]) for r in rows] == [("2099-01-01", 90.0)]

    def test_an_old_band_is_not_resurrected_by_an_older_fresh_looking_row(self):
        _seed_latest_row("MLFRESH_1", _utc(1), _utc(30))

        assert db_helpers.get_latest_quantile_bands(["MLFRESH_1"]) == []


class TestUniverseRefreshIsReadOnly:
    @staticmethod
    def _ohlcv(n=210):
        idx = pd.date_range("2024-01-01", periods=n, freq="B")
        price = np.linspace(100.0, 120.0, n)
        return pd.DataFrame(
            {"Open": price * 0.99, "High": price * 1.01, "Low": price * 0.98, "Close": price,
             "Volume": np.full(n, 1_000_000, dtype=float)}, index=idx,
        )

    def test_reads_parquet_only_and_never_sleeps(self):
        from quant_engine import run_daily_quant_scan
        with patch("quant_engine.load_or_fetch_daily_history", return_value=self._ohlcv()) as load, \
             patch("quant_engine.time") as clock:
            run_daily_quant_scan(["MLFRESH_1"], scan_type="read_only_test", read_only=True)
        load.assert_called_once_with("MLFRESH_1", read_only=True)
        clock.sleep.assert_not_called()

    def test_a_ticker_without_history_is_skipped_quietly(self):
        from quant_engine import run_daily_quant_scan
        with patch("quant_engine.load_or_fetch_daily_history", return_value=None), \
             patch("quant_engine.time"):
            run_daily_quant_scan(["MLFRESH_2"], scan_type="read_only_missing_test", read_only=True)
        conn = _conn()
        try:
            assert conn.execute("SELECT 1 FROM quant_signals WHERE ticker = 'MLFRESH_2'").fetchone() is None
        finally:
            conn.close()


class TestInferenceRunnersRefreshTheUniverseFirst:
    def test_scheduled_run_refreshes_then_scores_then_warns(self):
        calls = []
        with patch("scheduler_jobs.get_ml_inference_tickers", return_value=["AAPL"]), \
             patch("scheduler_jobs.run_daily_quant_scan", side_effect=lambda *a, **k: calls.append(("scan", a, k))), \
             patch("scheduler_jobs.update_daily_ml_predictions", side_effect=lambda t: calls.append("score") or ["AAPL"]), \
             patch("scheduler_jobs.alert_unscored_holdings", side_effect=lambda u: calls.append(("alert", u))), \
             patch("scheduler_jobs.score_quantile_predictions"), \
             patch("predicted_movers_engine.backfill_actual_outcomes", return_value=0), \
             patch("predicted_movers_engine.log_predictions", return_value=0), \
             patch("scheduler_jobs.log_sched_notification"):
            scheduler_jobs.run_ml_inference()
        assert calls == [("scan", (["AAPL"],), {"scan_type": "universe_refresh", "read_only": True}), "score", ("alert", ["AAPL"])]

    def test_manual_run_does_the_same(self):
        import api.routes_triggers
        calls = []
        with patch("api.routes_triggers.get_ml_inference_tickers", return_value=["AAPL"]), \
             patch("api.routes_triggers.run_daily_quant_scan", side_effect=lambda *a, **k: calls.append("scan")), \
             patch("api.routes_triggers.update_daily_ml_predictions", side_effect=lambda t: calls.append("score") or []), \
             patch("api.routes_triggers.alert_unscored_holdings", side_effect=lambda u: calls.append("alert")), \
             patch("api.routes_triggers.score_quantile_predictions", side_effect=lambda t: calls.append("bands")):
            api.routes_triggers.bg_execute_ml_inference()
        assert calls == ["scan", "score", "alert", "bands"]
