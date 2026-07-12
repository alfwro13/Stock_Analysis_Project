"""
tests/test_earnings_engine.py — EARNINGS ENGINE

Covers:
  run_earnings_alert() — config loading, portfolio loading, date filter logic,
                         "once" vs "daily" alert types, DB writes, return values
"""

import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from earnings_engine import run_earnings_alert


def _combined(tickers):
    return {t: {"ticker": t} for t in tickers}


@pytest.fixture
def db_path(tmp_path):
    path = tmp_path / "earnings_test.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE stock_signals (
            ticker              TEXT PRIMARY KEY,
            company_name        TEXT,
            next_earnings_date  TEXT
        );
        CREATE TABLE system_notifications (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            message_type TEXT,
            message_text TEXT
        );
    """)
    conn.commit()
    conn.close()
    return path


def _get_conn(db_path):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    return conn


def _read_all(db_path, sql, *params):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def _seed_signal(db_path, ticker, name, earnings_date_str):
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT OR REPLACE INTO stock_signals (ticker, company_name, next_earnings_date) VALUES (?, ?, ?)",
        (ticker, name, earnings_date_str),
    )
    conn.commit()
    conn.close()


def _base_cfg(alert_type="daily", days_ahead=7):
    return {
        "NEXTCLOUD_URL": "https://nc.example.com",
        "CONVERSATION_TOKEN": "tok",
        "BOT_USERNAME": "bot",
        "APP_PASSWORD": "pass",
        "NOTIFICATIONS": {
            "EARNINGS_ALERTS": {
                "DAYS_AHEAD": days_ahead,
                "ALERT_TYPE": alert_type,
            }
        },
    }


class TestRunEarningsAlertGuards:

    def test_empty_portfolio_returns_early(self):
        cfg = _base_cfg()
        with patch("earnings_engine.load_config", return_value=cfg), \
             patch("accounts_engine.get_combined_holdings", return_value={}):
            ok, msg = run_earnings_alert()
        assert ok is True
        assert "no valid" in msg.lower()

    def test_all_0p_tickers_returns_early(self):
        cfg = _base_cfg()
        with patch("earnings_engine.load_config", return_value=cfg), \
             patch("accounts_engine.get_combined_holdings", return_value=_combined(["0P0000ABC"])):
            ok, msg = run_earnings_alert()
        assert ok is True
        assert "no valid" in msg.lower()

    def test_fatal_config_error_returns_false(self):
        with patch("earnings_engine.load_config", side_effect=RuntimeError("boom")):
            ok, msg = run_earnings_alert()
        assert ok is False
        assert "crash" in msg.lower()

    def test_holdings_engine_error_returns_false(self):
        cfg = _base_cfg()
        with patch("earnings_engine.load_config", return_value=cfg), \
             patch("accounts_engine.get_combined_holdings", side_effect=RuntimeError("db gone")):
            ok, msg = run_earnings_alert()
        assert ok is False
        assert "crash" in msg.lower()


class TestDailyAlertType:

    def _run(self, db_path, tickers, earnings_date_str, days_ahead=7):
        cfg = _base_cfg(alert_type="daily", days_ahead=days_ahead)
        with patch("earnings_engine.load_config", return_value=cfg), \
             patch("accounts_engine.get_combined_holdings", return_value=_combined(tickers)), \
             patch("earnings_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("notification_engine.load_config", return_value=cfg), \
             patch("notification_engine.nextcloud_talk.send_text_message", return_value=True):
            return run_earnings_alert()

    def test_today_earnings_fires(self, db_path):
        today_str = date.today().strftime("%Y-%m-%d")
        _seed_signal(db_path, "AAPL", "Apple", today_str)
        ok, msg = self._run(db_path, ["AAPL"], today_str)
        assert ok is True
        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 1
        assert "TODAY" in rows[0]["message_text"]

    def test_tomorrow_earnings_fires(self, db_path):
        tomorrow_str = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        _seed_signal(db_path, "MSFT", "Microsoft", tomorrow_str)
        ok, msg = self._run(db_path, ["MSFT"], tomorrow_str)
        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 1
        assert "TOMORROW" in rows[0]["message_text"]

    def test_in_n_days_fires_within_window(self, db_path):
        five_days = (date.today() + timedelta(days=5)).strftime("%Y-%m-%d")
        _seed_signal(db_path, "NVDA", "Nvidia", five_days)
        ok, msg = self._run(db_path, ["NVDA"], five_days, days_ahead=7)
        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 1
        assert "in 5 days" in rows[0]["message_text"]

    def test_outside_window_does_not_fire(self, db_path):
        far_date = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        _seed_signal(db_path, "TSLA", "Tesla", far_date)
        ok, msg = self._run(db_path, ["TSLA"], far_date, days_ahead=7)
        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 0

    def test_past_earnings_does_not_fire(self, db_path):
        past_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        _seed_signal(db_path, "GOOG", "Alphabet", past_date)
        ok, msg = self._run(db_path, ["GOOG"], past_date)
        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 0

    def test_unknown_earnings_date_skipped(self, db_path):
        _seed_signal(db_path, "XYZ", "Unknown Corp", "Unknown")
        ok, msg = self._run(db_path, ["XYZ"], "Unknown")
        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 0

    def test_notification_row_has_correct_type(self, db_path):
        today_str = date.today().strftime("%Y-%m-%d")
        _seed_signal(db_path, "AAPL", "Apple", today_str)
        self._run(db_path, ["AAPL"], today_str)
        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert rows[0]["message_type"] == "Earnings"


class TestOnceAlertType:

    def _run(self, db_path, tickers, days_ahead=7):
        cfg = _base_cfg(alert_type="once", days_ahead=days_ahead)
        with patch("earnings_engine.load_config", return_value=cfg), \
             patch("accounts_engine.get_combined_holdings", return_value=_combined(tickers)), \
             patch("earnings_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("notification_engine.load_config", return_value=cfg), \
             patch("notification_engine.nextcloud_talk.send_text_message", return_value=True):
            return run_earnings_alert()

    def test_fires_exactly_on_days_ahead_day(self, db_path):
        exact_date = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")
        _seed_signal(db_path, "AAPL", "Apple", exact_date)
        ok, msg = self._run(db_path, ["AAPL"], days_ahead=7)
        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 1

    def test_does_not_fire_on_other_days_within_window(self, db_path):
        three_days = (date.today() + timedelta(days=3)).strftime("%Y-%m-%d")
        _seed_signal(db_path, "AAPL", "Apple", three_days)
        ok, msg = self._run(db_path, ["AAPL"], days_ahead=7)
        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 0


class TestReturnValues:

    def test_return_message_includes_alert_count(self, db_path):
        today_str = date.today().strftime("%Y-%m-%d")
        _seed_signal(db_path, "AAPL", "Apple", today_str)
        cfg = _base_cfg()
        with patch("earnings_engine.load_config", return_value=cfg), \
             patch("accounts_engine.get_combined_holdings", return_value=_combined(["AAPL"])), \
             patch("earnings_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("notification_engine.load_config", return_value=cfg), \
             patch("notification_engine.nextcloud_talk.send_text_message", return_value=True):
            ok, msg = run_earnings_alert()
        assert ok is True
        assert "1" in msg

    def test_nextcloud_failure_still_writes_notification(self, db_path):
        today_str = date.today().strftime("%Y-%m-%d")
        _seed_signal(db_path, "AAPL", "Apple", today_str)
        cfg = _base_cfg()
        with patch("earnings_engine.load_config", return_value=cfg), \
             patch("accounts_engine.get_combined_holdings", return_value=_combined(["AAPL"])), \
             patch("earnings_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("notification_engine.load_config", return_value=cfg), \
             patch("notification_engine.nextcloud_talk.send_text_message", return_value=False):
            ok, msg = run_earnings_alert()
        assert ok is True
        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 1
