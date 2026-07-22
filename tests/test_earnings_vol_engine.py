# Guards against re-introducing a live yahoo_engine.get_ticker_info/get_earnings_dates
# call per ticker for date detection — next_earnings_date must come from stock_signals.
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from earnings_vol_engine import (
    _get_past_earnings_events,
    backfill_earnings_drift_outcomes,
    get_earnings_drift_accuracy_summary,
    get_historical_earnings_drift,
    get_historical_earnings_move,
    log_near_earnings_predictions,
    run_earnings_vol_scan,
)


@pytest.fixture(autouse=True)
def _no_real_sleep():
    """run_earnings_vol_scan() sleeps 2.5-5s between tickers to stay under Yahoo's rate limits
    in production — no reason to actually wait that long across dozens of test invocations."""
    with patch("earnings_vol_engine.time.sleep"):
        yield


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "earnings_vol_test.db"
    conn = sqlite3.connect(str(path))
    conn.executescript("""
        CREATE TABLE stock_signals (
            ticker TEXT PRIMARY KEY,
            quote_type TEXT,
            company_name TEXT,
            next_earnings_date TEXT
        );
        CREATE TABLE asset_profiles (
            ticker TEXT PRIMARY KEY,
            quote_type TEXT
        );
        CREATE TABLE earnings_volatility (
            ticker TEXT PRIMARY KEY,
            next_earnings_date TEXT,
            implied_move_pct REAL,
            historical_avg_move_pct REAL,
            edge_score REAL,
            options_volume INTEGER,
            last_updated TEXT,
            drift_avg_pct_1d REAL,
            drift_up_count_1d INTEGER,
            drift_sample_size_1d INTEGER,
            drift_avg_pct_5d REAL,
            drift_up_count_5d INTEGER,
            drift_sample_size_5d INTEGER,
            drift_avg_pct_20d REAL,
            drift_up_count_20d INTEGER,
            drift_sample_size_20d INTEGER
        );
        CREATE TABLE earnings_drift_predictions (
            id                     INTEGER PRIMARY KEY AUTOINCREMENT,
            ticker                 TEXT NOT NULL,
            earnings_date          TEXT NOT NULL,
            predicted_ts           TEXT NOT NULL,
            pre_earnings_close     REAL NOT NULL,
            sample_size            INTEGER,
            predicted_pct_1d       REAL,
            target_date_1d         TEXT,
            actual_price_1d        REAL,
            actual_date_1d         TEXT,
            direction_correct_1d   INTEGER,
            predicted_pct_5d       REAL,
            target_date_5d         TEXT,
            actual_price_5d        REAL,
            actual_date_5d         TEXT,
            direction_correct_5d   INTEGER,
            predicted_pct_20d      REAL,
            target_date_20d        TEXT,
            actual_price_20d       REAL,
            actual_date_20d        TEXT,
            direction_correct_20d  INTEGER,
            UNIQUE(ticker, earnings_date)
        );
        CREATE TABLE quant_signals (
            ticker TEXT,
            date TEXT,
            close_price REAL,
            price_q10 REAL,
            price_q90 REAL
        );
        CREATE TABLE system_notifications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            message_type TEXT,
            message_text TEXT,
            is_read BOOLEAN DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'sent'
        );
    """)
    conn.commit()
    conn.close()
    return path


def _fake_hist_linear(start="2023-12-01", end="2024-03-01"):
    """Business-day price series where Close increases by exactly 1.0 per row, so
    offsets relative to any anchor position are exactly predictable."""
    idx = pd.bdate_range(start=start, end=end)
    closes = 100.0 + np.arange(len(idx))
    return pd.DataFrame({"Close": closes}, index=idx)


def _fake_earnings_dates(dates):
    idx = pd.DatetimeIndex(dates)
    return pd.DataFrame({"eventName": ["Earnings"] * len(idx)}, index=idx)


def _seed_prediction(db_path, ticker, earnings_date, pre_close,
                      predicted_pct_1d, target_1d,
                      predicted_pct_5d=None, target_5d=None,
                      predicted_pct_20d=None, target_20d=None):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        """INSERT INTO earnings_drift_predictions
           (ticker, earnings_date, predicted_ts, pre_earnings_close, sample_size,
            predicted_pct_1d, target_date_1d, predicted_pct_5d, target_date_5d,
            predicted_pct_20d, target_date_20d)
           VALUES (?, ?, '2024-01-01 00:00:00', ?, 4, ?, ?, ?, ?, ?, ?)""",
        (ticker, earnings_date, pre_close, predicted_pct_1d, target_1d,
         predicted_pct_5d, target_5d, predicted_pct_20d, target_20d),
    )
    conn.commit()
    row_id = conn.execute("SELECT id FROM earnings_drift_predictions WHERE ticker=?", (ticker,)).fetchone()[0]
    conn.close()
    return row_id


def _seed_quant_signal_row(db_path, ticker, dt, close):
    conn = sqlite3.connect(str(db_path))
    conn.execute("INSERT INTO quant_signals (ticker, date, close_price) VALUES (?, ?, ?)", (ticker, dt, close))
    conn.commit()
    conn.close()


def _get_conn(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _seed(db_path, ticker, next_earnings_date):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO stock_signals (ticker, quote_type, next_earnings_date) VALUES (?, 'EQUITY', ?)",
        (ticker, next_earnings_date),
    )
    conn.commit()
    conn.close()


def _read_all(db_path, sql, *params):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def _fake_hist(n=40):
    idx = pd.date_range(end=date.today(), periods=n, freq="D")
    rng = np.random.default_rng(42)
    prices = 100 + np.cumsum(rng.normal(0, 1, n))
    return pd.DataFrame({"Close": prices}, index=idx)


def _run(db_path, tickers):
    with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
         patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)):
        return run_earnings_vol_scan(tickers)


class TestCachedEarningsDateFilter:

    def test_outside_window_skipped_with_no_yahoo_calls(self, db_path):
        far_date = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        _seed(db_path, "MSFT", far_date)
        with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("earnings_vol_engine.yahoo_engine.get_ticker_info") as mock_info, \
             patch("earnings_vol_engine.yahoo_engine.get_earnings_dates") as mock_dates, \
             patch("earnings_vol_engine.yahoo_engine.get_options_expirations") as mock_exp:
            run_earnings_vol_scan(["MSFT"])
        mock_info.assert_not_called()
        mock_dates.assert_not_called()
        mock_exp.assert_not_called()
        assert len(_read_all(db_path, "SELECT * FROM earnings_volatility")) == 0

    def test_past_earnings_date_skipped(self, db_path):
        past_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        _seed(db_path, "GOOG", past_date)
        _run(db_path, ["GOOG"])
        assert len(_read_all(db_path, "SELECT * FROM earnings_volatility")) == 0

    def test_unknown_date_skipped(self, db_path):
        _seed(db_path, "TSLA", "Unknown")
        with patch("earnings_vol_engine.yahoo_engine.get_ticker_info") as mock_info:
            _run(db_path, ["TSLA"])
        mock_info.assert_not_called()
        assert len(_read_all(db_path, "SELECT * FROM earnings_volatility")) == 0

    def test_missing_row_skipped(self, db_path):
        _run(db_path, ["NOPE"])
        assert len(_read_all(db_path, "SELECT * FROM earnings_volatility")) == 0

    def test_malformed_date_skipped_gracefully(self, db_path):
        _seed(db_path, "BADD", "not-a-date")
        _run(db_path, ["BADD"])
        assert len(_read_all(db_path, "SELECT * FROM earnings_volatility")) == 0

    def test_in_window_never_calls_get_ticker_info(self, db_path):
        in_window = (date.today() + timedelta(days=5)).strftime("%Y-%m-%d")
        _seed(db_path, "AAPL", in_window)
        with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("earnings_vol_engine.yahoo_engine.get_ticker_info") as mock_info, \
             patch("earnings_vol_engine.yahoo_engine.get_earnings_dates", return_value=None), \
             patch("earnings_vol_engine.load_or_fetch_daily_history", return_value=_fake_hist()), \
             patch("earnings_vol_engine.yahoo_engine.get_options_expirations", return_value=None):
            run_earnings_vol_scan(["AAPL"])
        mock_info.assert_not_called()

    def test_fetch_failure_returns_ticker_in_failed_list(self, db_path):
        in_window = (date.today() + timedelta(days=5)).strftime("%Y-%m-%d")
        _seed(db_path, "AAPL", in_window)
        with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("earnings_vol_engine.yahoo_engine.get_earnings_dates", return_value=None), \
             patch("earnings_vol_engine.load_or_fetch_daily_history", return_value=_fake_hist()), \
             patch("earnings_vol_engine.yahoo_engine.get_options_expirations", return_value=None):
            failed = run_earnings_vol_scan(["AAPL"])
        assert failed == ["AAPL"]

    def test_outside_window_ticker_not_in_failed_list(self, db_path):
        far_date = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        _seed(db_path, "MSFT", far_date)
        failed = _run(db_path, ["MSFT"])
        assert failed == []

    def test_successful_scan_returns_empty_failed_list(self, db_path):
        in_window_date = date.today() + timedelta(days=3)
        e_date_str = in_window_date.strftime("%Y-%m-%d")
        expiry_str = (in_window_date + timedelta(days=2)).strftime("%Y-%m-%d")
        _seed(db_path, "OK1", e_date_str)
        fake_drift = {
            1: {"avg_pct": 1.0, "avg_abs_pct": 1.0, "up_count": 2, "sample_size": 4},
            5: {"avg_pct": 1.0, "avg_abs_pct": 1.0, "up_count": 2, "sample_size": 4},
            20: {"avg_pct": 1.0, "avg_abs_pct": 1.0, "up_count": 2, "sample_size": 4},
        }
        with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("earnings_vol_engine.load_or_fetch_daily_history", return_value=_fake_hist()), \
             patch("earnings_vol_engine.get_historical_earnings_drift", return_value=fake_drift), \
             patch("earnings_vol_engine.get_implied_straddle_move", return_value=(None, 0, None)):
            failed = run_earnings_vol_scan(["OK1"])
        assert failed == []

    def test_full_scan_writes_edge_row_using_cached_date(self, db_path):
        in_window_date = date.today() + timedelta(days=3)
        e_date_str = in_window_date.strftime("%Y-%m-%d")
        expiry_str = (in_window_date + timedelta(days=2)).strftime("%Y-%m-%d")
        _seed(db_path, "NVDA", e_date_str)
        fake_drift = {
            1: {"avg_pct": 9.2, "avg_abs_pct": 9.2, "up_count": 3, "sample_size": 4},
            5: {"avg_pct": 3.0, "avg_abs_pct": 5.0, "up_count": 2, "sample_size": 4},
            20: {"avg_pct": -1.5, "avg_abs_pct": 6.0, "up_count": 1, "sample_size": 3},
        }

        with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("earnings_vol_engine.yahoo_engine.get_ticker_info") as mock_info, \
             patch("earnings_vol_engine.load_or_fetch_daily_history", return_value=_fake_hist()), \
             patch("earnings_vol_engine.get_historical_earnings_drift", return_value=fake_drift), \
             patch("earnings_vol_engine.get_implied_straddle_move", return_value=(6.5, 500, expiry_str)):
            run_earnings_vol_scan(["NVDA"])

        mock_info.assert_not_called()
        rows = _read_all(db_path, "SELECT * FROM earnings_volatility")
        assert len(rows) == 1
        assert rows[0]["ticker"] == "NVDA"
        assert rows[0]["next_earnings_date"] == e_date_str
        assert rows[0]["implied_move_pct"] is not None
        assert rows[0]["edge_score"] is not None
        assert rows[0]["drift_avg_pct_1d"] == 9.2
        assert rows[0]["drift_up_count_1d"] == 3
        assert rows[0]["drift_sample_size_1d"] == 4
        assert rows[0]["drift_avg_pct_20d"] == -1.5

    def test_illiquid_options_still_writes_row_with_null_edge_score(self, db_path):
        in_window_date = date.today() + timedelta(days=3)
        e_date_str = in_window_date.strftime("%Y-%m-%d")
        _seed(db_path, "ILLQ", e_date_str)
        fake_drift = {
            1: {"avg_pct": 4.5, "avg_abs_pct": 4.5, "up_count": 3, "sample_size": 4},
            5: {"avg_pct": None, "avg_abs_pct": None, "up_count": 0, "sample_size": 0},
            20: {"avg_pct": None, "avg_abs_pct": None, "up_count": 0, "sample_size": 0},
        }

        with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("earnings_vol_engine.load_or_fetch_daily_history", return_value=_fake_hist()), \
             patch("earnings_vol_engine.get_historical_earnings_drift", return_value=fake_drift), \
             patch("earnings_vol_engine.get_implied_straddle_move", return_value=(None, 0, None)):
            run_earnings_vol_scan(["ILLQ"])

        rows = _read_all(db_path, "SELECT * FROM earnings_volatility")
        assert len(rows) == 1
        assert rows[0]["implied_move_pct"] is None
        assert rows[0]["edge_score"] is None
        assert rows[0]["options_volume"] is None
        assert rows[0]["historical_avg_move_pct"] == 4.5
        assert rows[0]["drift_avg_pct_1d"] == 4.5
        assert rows[0]["drift_up_count_1d"] == 3


class TestGetPastEarningsEvents:
    def test_offsets_relative_to_pre_close_are_exact(self):
        hist = _fake_hist_linear()
        dates = _fake_earnings_dates(["2024-01-10"])
        with patch("earnings_vol_engine.yahoo_engine.get_earnings_dates", return_value=dates), \
             patch("earnings_vol_engine.load_or_fetch_daily_history", return_value=hist):
            events = _get_past_earnings_events("TEST", offsets=[-5, 0, 1, 5, 20])
        assert len(events) == 1
        e = events[0]
        # Close increases by exactly 1.0 per business day, so offsets relative to the
        # pre-earnings close are exactly predictable (offset>=1 skips the ambiguous
        # earnings-day bar, matching the legacy pre/post 2-session-window design).
        assert e["closes"][0] - e["pre_close"] == pytest.approx(0.0)
        assert e["closes"][1] - e["pre_close"] == pytest.approx(2.0)
        assert e["closes"][5] - e["pre_close"] == pytest.approx(6.0)
        assert e["closes"][20] - e["pre_close"] == pytest.approx(21.0)
        assert e["closes"][-5] - e["pre_close"] == pytest.approx(-5.0)

    def test_no_earnings_dates_returns_empty(self):
        with patch("earnings_vol_engine.yahoo_engine.get_earnings_dates", return_value=None):
            assert _get_past_earnings_events("TEST", offsets=[1]) == []


class TestGetHistoricalEarningsMoveUnchanged:
    """Regression guard: the public unsigned-average behavior of get_historical_earnings_move
    must be unchanged after being refactored onto _get_past_earnings_events."""

    def test_matches_manual_pre_post_close_calculation(self):
        hist = _fake_hist_linear()
        dates = _fake_earnings_dates(["2024-01-10"])
        with patch("earnings_vol_engine.yahoo_engine.get_earnings_dates", return_value=dates), \
             patch("earnings_vol_engine.load_or_fetch_daily_history", return_value=hist):
            events = _get_past_earnings_events("TEST", offsets=[1])
            move = get_historical_earnings_move("TEST")
        pre_close = events[0]["pre_close"]
        post_close = events[0]["closes"][1]
        expected = abs((post_close - pre_close) / pre_close) * 100.0
        assert move == pytest.approx(expected)

    def test_no_history_returns_none(self):
        with patch("earnings_vol_engine.yahoo_engine.get_earnings_dates", return_value=None):
            assert get_historical_earnings_move("TEST") is None


class TestGetHistoricalEarningsDrift:
    def test_single_event_signed_stats(self):
        hist = _fake_hist_linear()
        dates = _fake_earnings_dates(["2024-01-10"])
        with patch("earnings_vol_engine.yahoo_engine.get_earnings_dates", return_value=dates), \
             patch("earnings_vol_engine.load_or_fetch_daily_history", return_value=hist):
            drift = get_historical_earnings_drift("TEST", horizons=(1, 5, 20))
        assert drift[1]["sample_size"] == 1
        assert drift[1]["up_count"] == 1  # linear series is monotonically increasing
        assert drift[1]["avg_pct"] > 0
        assert drift[20]["sample_size"] == 1
        assert drift[20]["avg_pct"] > drift[1]["avg_pct"]

    def test_too_recent_event_reduces_only_the_unavailable_horizon(self):
        hist = _fake_hist_linear(start="2023-12-01", end="2024-02-01")
        recent_date = hist.index[-3].strftime("%Y-%m-%d")  # only 2 trading days of history after it
        dates = _fake_earnings_dates([recent_date])
        with patch("earnings_vol_engine.yahoo_engine.get_earnings_dates", return_value=dates), \
             patch("earnings_vol_engine.load_or_fetch_daily_history", return_value=hist):
            drift = get_historical_earnings_drift("TEST", horizons=(1, 5, 20))
        assert drift[1]["sample_size"] == 1
        assert drift[20]["sample_size"] == 0
        assert drift[20]["avg_pct"] is None

    def test_no_history_returns_all_none(self):
        with patch("earnings_vol_engine.yahoo_engine.get_earnings_dates", return_value=None):
            drift = get_historical_earnings_drift("TEST", horizons=(1, 5, 20))
        for h in (1, 5, 20):
            assert drift[h] == {"avg_pct": None, "avg_abs_pct": None, "up_count": 0, "sample_size": 0}


class TestLogNearEarningsPredictions:
    _drift = {
        1: {"avg_pct": 2.0, "avg_abs_pct": 2.0, "up_count": 3, "sample_size": 4},
        5: {"avg_pct": 3.0, "avg_abs_pct": 3.0, "up_count": 3, "sample_size": 4},
        20: {"avg_pct": 4.0, "avg_abs_pct": 4.0, "up_count": 3, "sample_size": 4},
    }

    def test_logs_row_for_ticker_with_earnings_within_window(self, db_path):
        e_date = (date.today() + timedelta(days=2)).strftime("%Y-%m-%d")
        _seed(db_path, "LOGX", e_date)
        with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("earnings_vol_engine.load_or_fetch_daily_history", return_value=_fake_hist()), \
             patch("earnings_vol_engine.get_historical_earnings_drift", return_value=self._drift):
            logged = log_near_earnings_predictions(["LOGX"])
        assert logged == 1
        rows = _read_all(db_path, "SELECT * FROM earnings_drift_predictions WHERE ticker='LOGX'")
        assert len(rows) == 1
        assert rows[0]["predicted_pct_1d"] == 2.0
        assert rows[0]["predicted_pct_20d"] == 4.0
        assert rows[0]["sample_size"] == 4

    def test_outside_window_ticker_not_logged(self, db_path):
        far_date = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        _seed(db_path, "LOGFAR", far_date)
        with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)):
            logged = log_near_earnings_predictions(["LOGFAR"])
        assert logged == 0

    def test_second_run_refreshes_baseline_while_unresolved(self, db_path):
        e_date = (date.today() + timedelta(days=2)).strftime("%Y-%m-%d")
        _seed(db_path, "LOGREF", e_date)
        hist1 = _fake_hist()
        with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("earnings_vol_engine.load_or_fetch_daily_history", return_value=hist1), \
             patch("earnings_vol_engine.get_historical_earnings_drift", return_value=self._drift):
            log_near_earnings_predictions(["LOGREF"])
        first_close = _read_all(db_path, "SELECT pre_earnings_close FROM earnings_drift_predictions WHERE ticker='LOGREF'")[0]["pre_earnings_close"]

        hist2 = hist1.copy()
        hist2["Close"] = hist2["Close"] + 500.0
        with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("earnings_vol_engine.load_or_fetch_daily_history", return_value=hist2), \
             patch("earnings_vol_engine.get_historical_earnings_drift", return_value=self._drift):
            logged2 = log_near_earnings_predictions(["LOGREF"])

        assert logged2 == 1
        rows = _read_all(db_path, "SELECT * FROM earnings_drift_predictions WHERE ticker='LOGREF'")
        assert len(rows) == 1
        assert rows[0]["pre_earnings_close"] != first_close

    def test_resolved_row_not_clobbered_by_later_run(self, db_path):
        e_date = (date.today() + timedelta(days=2)).strftime("%Y-%m-%d")
        _seed(db_path, "LOGRES", e_date)
        with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("earnings_vol_engine.load_or_fetch_daily_history", return_value=_fake_hist()), \
             patch("earnings_vol_engine.get_historical_earnings_drift", return_value=self._drift):
            log_near_earnings_predictions(["LOGRES"])

        conn = sqlite3.connect(str(db_path))
        conn.execute("UPDATE earnings_drift_predictions SET direction_correct_1d=1, pre_earnings_close=999.0 WHERE ticker='LOGRES'")
        conn.commit()
        conn.close()

        hist2 = _fake_hist().copy()
        hist2["Close"] = hist2["Close"] + 500.0
        with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("earnings_vol_engine.load_or_fetch_daily_history", return_value=hist2), \
             patch("earnings_vol_engine.get_historical_earnings_drift", return_value=self._drift):
            log_near_earnings_predictions(["LOGRES"])

        row = _read_all(db_path, "SELECT pre_earnings_close FROM earnings_drift_predictions WHERE ticker='LOGRES'")[0]
        assert row["pre_earnings_close"] == 999.0


class TestBackfillEarningsDriftOutcomes:
    def test_returns_zero_when_no_pending_rows(self, db_path):
        with patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)):
            assert backfill_earnings_drift_outcomes() == 0

    def test_resolves_1d_and_5d_independently_while_20d_pending(self, db_path):
        past = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")
        d1 = (date.today() - timedelta(days=9)).strftime("%Y-%m-%d")
        d5 = (date.today() - timedelta(days=5)).strftime("%Y-%m-%d")
        future20 = (date.today() + timedelta(days=20)).strftime("%Y-%m-%d")
        row_id = _seed_prediction(db_path, "BFX", past, 100.0, 2.0, d1, 3.0, d5, 4.0, future20)
        _seed_quant_signal_row(db_path, "BFX", d1, 103.0)
        _seed_quant_signal_row(db_path, "BFX", d5, 108.0)

        with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)):
            resolved = backfill_earnings_drift_outcomes()

        assert resolved == 2
        row = _read_all(db_path, "SELECT * FROM earnings_drift_predictions WHERE id=?", row_id)[0]
        assert row["direction_correct_1d"] == 1
        assert row["direction_correct_5d"] == 1
        assert row["direction_correct_20d"] is None
        assert row["actual_price_1d"] == 103.0

    def test_direction_correct_and_incorrect(self, db_path):
        past = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")
        d1 = (date.today() - timedelta(days=9)).strftime("%Y-%m-%d")
        row_id = _seed_prediction(db_path, "BFY", past, 100.0, 2.0, d1)
        _seed_quant_signal_row(db_path, "BFY", d1, 90.0)  # predicted up, actual down

        with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)):
            backfill_earnings_drift_outcomes()

        row = _read_all(db_path, "SELECT direction_correct_1d FROM earnings_drift_predictions WHERE id=?", row_id)[0]
        assert row["direction_correct_1d"] == 0

    def test_catch_up_scans_multiple_unresolved_rows_not_only_newest(self, db_path):
        past = (date.today() - timedelta(days=30)).strftime("%Y-%m-%d")
        d1a = (date.today() - timedelta(days=29)).strftime("%Y-%m-%d")
        d1b = (date.today() - timedelta(days=9)).strftime("%Y-%m-%d")
        _seed_prediction(db_path, "BFZ1", past, 100.0, 2.0, d1a)
        _seed_prediction(db_path, "BFZ2", past, 100.0, 2.0, d1b)
        _seed_quant_signal_row(db_path, "BFZ1", d1a, 105.0)
        _seed_quant_signal_row(db_path, "BFZ2", d1b, 105.0)

        with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)):
            resolved = backfill_earnings_drift_outcomes()

        assert resolved == 2

    def test_leaves_rows_before_target_date_unresolved(self, db_path):
        past = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")
        future = "2099-12-31"
        row_id = _seed_prediction(db_path, "BFPEND", past, 100.0, 2.0, future)

        with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)):
            resolved = backfill_earnings_drift_outcomes()

        assert resolved == 0
        row = _read_all(db_path, "SELECT direction_correct_1d FROM earnings_drift_predictions WHERE id=?", row_id)[0]
        assert row["direction_correct_1d"] is None


class TestGetEarningsDriftAccuracySummary:
    def test_empty_table_returns_shape(self, db_path):
        with patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)):
            data = get_earnings_drift_accuracy_summary()
        assert data["by_ticker"] == []
        assert data["overall"]["total"] == 0
        assert data["overall"]["accuracy_1d"] is None

    def test_company_name_enrichment(self, db_path):
        past = (date.today() - timedelta(days=10)).strftime("%Y-%m-%d")
        d1 = (date.today() - timedelta(days=9)).strftime("%Y-%m-%d")
        _seed_prediction(db_path, "ACCX", past, 100.0, 2.0, d1)
        _seed_quant_signal_row(db_path, "ACCX", d1, 105.0)
        with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)):
            backfill_earnings_drift_outcomes()
            with patch("db_helpers.get_company_names", return_value={"ACCX": "Test Co"}):
                data = get_earnings_drift_accuracy_summary()
        assert len(data["by_ticker"]) == 1
        assert data["by_ticker"][0]["ticker"] == "ACCX"
        assert data["by_ticker"][0]["company_name"] == "Test Co"
        assert data["by_ticker"][0]["resolved_1d"] == 1
        assert data["by_ticker"][0]["accuracy_1d"] == 100.0
