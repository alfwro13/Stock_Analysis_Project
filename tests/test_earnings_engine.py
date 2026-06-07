"""
tests/test_earnings_engine.py — EARNINGS ENGINE

Covers:
  run_earnings_alert() — config loading, portfolio parsing, date filter logic,
                         "once" vs "daily" alert types, DB writes, return values
"""

import json
import os
import sqlite3
import sys
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import earnings_engine
from earnings_engine import run_earnings_alert


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

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


def _portfolio_json(tickers):
    return {t: {"ticker": t} for t in tickers}


# ──────────────────────────────────────────────────────────────────────────────
# Guard paths
# ──────────────────────────────────────────────────────────────────────────────

class TestRunEarningsAlertGuards:

    def test_missing_portfolio_returns_false(self, tmp_path):
        cfg = _base_cfg()
        with patch("earnings_engine.load_config", return_value=cfg), \
             patch("earnings_engine.PORTFOLIO_PATH", str(tmp_path / "nonexistent.json")):
            ok, msg = run_earnings_alert()
        assert ok is False
        assert "not found" in msg.lower()

    def test_corrupted_portfolio_returns_false(self, tmp_path):
        bad = tmp_path / "portfolio.json"
        bad.write_text("{bad json")
        cfg = _base_cfg()
        with patch("earnings_engine.load_config", return_value=cfg), \
             patch("earnings_engine.PORTFOLIO_PATH", str(bad)):
            ok, msg = run_earnings_alert()
        assert ok is False
        assert "corrupted" in msg.lower()

    def test_empty_portfolio_after_0p_filter_returns_early(self, tmp_path):
        port = tmp_path / "portfolio.json"
        port.write_text(json.dumps({"x": {"ticker": "0P0000ABC"}}))
        cfg = _base_cfg()
        with patch("earnings_engine.load_config", return_value=cfg), \
             patch("earnings_engine.PORTFOLIO_PATH", str(port)):
            ok, msg = run_earnings_alert()
        assert ok is True
        assert "no valid" in msg.lower()

    def test_fatal_config_error_returns_false(self):
        with patch("earnings_engine.load_config", side_effect=RuntimeError("boom")):
            ok, msg = run_earnings_alert()
        assert ok is False
        assert "crash" in msg.lower()


# ──────────────────────────────────────────────────────────────────────────────
# Date-filter logic (daily alert type)
# ──────────────────────────────────────────────────────────────────────────────

class TestDailyAlertType:

    def _run(self, db_path, portfolio_path, earnings_date_str, days_ahead=7):
        cfg = _base_cfg(alert_type="daily", days_ahead=days_ahead)
        with patch("earnings_engine.load_config", return_value=cfg), \
             patch("earnings_engine.PORTFOLIO_PATH", str(portfolio_path)), \
             patch("earnings_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("earnings_engine.send_text_message", return_value=True):
            return run_earnings_alert()

    def test_today_earnings_fires(self, tmp_path, db_path):
        today_str = date.today().strftime("%Y-%m-%d")
        _seed_signal(db_path, "AAPL", "Apple", today_str)
        port = tmp_path / "p.json"
        port.write_text(json.dumps(_portfolio_json(["AAPL"])))

        ok, msg = self._run(db_path, port, today_str)

        assert ok is True
        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 1
        assert "TODAY" in rows[0]["message_text"]

    def test_tomorrow_earnings_fires(self, tmp_path, db_path):
        tomorrow_str = (date.today() + timedelta(days=1)).strftime("%Y-%m-%d")
        _seed_signal(db_path, "MSFT", "Microsoft", tomorrow_str)
        port = tmp_path / "p.json"
        port.write_text(json.dumps(_portfolio_json(["MSFT"])))

        ok, msg = self._run(db_path, port, tomorrow_str)

        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 1
        assert "TOMORROW" in rows[0]["message_text"]

    def test_in_n_days_fires_within_window(self, tmp_path, db_path):
        five_days = (date.today() + timedelta(days=5)).strftime("%Y-%m-%d")
        _seed_signal(db_path, "NVDA", "Nvidia", five_days)
        port = tmp_path / "p.json"
        port.write_text(json.dumps(_portfolio_json(["NVDA"])))

        ok, msg = self._run(db_path, port, five_days, days_ahead=7)

        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 1
        assert "in 5 days" in rows[0]["message_text"]

    def test_outside_window_does_not_fire(self, tmp_path, db_path):
        far_date = (date.today() + timedelta(days=30)).strftime("%Y-%m-%d")
        _seed_signal(db_path, "TSLA", "Tesla", far_date)
        port = tmp_path / "p.json"
        port.write_text(json.dumps(_portfolio_json(["TSLA"])))

        ok, msg = self._run(db_path, port, far_date, days_ahead=7)

        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 0

    def test_past_earnings_does_not_fire(self, tmp_path, db_path):
        past_date = (date.today() - timedelta(days=1)).strftime("%Y-%m-%d")
        _seed_signal(db_path, "GOOG", "Alphabet", past_date)
        port = tmp_path / "p.json"
        port.write_text(json.dumps(_portfolio_json(["GOOG"])))

        ok, msg = self._run(db_path, port, past_date)

        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 0

    def test_unknown_earnings_date_skipped(self, tmp_path, db_path):
        _seed_signal(db_path, "XYZ", "Unknown Corp", "Unknown")
        port = tmp_path / "p.json"
        port.write_text(json.dumps(_portfolio_json(["XYZ"])))

        ok, msg = self._run(db_path, port, "Unknown")

        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 0

    def test_notification_row_has_correct_type(self, tmp_path, db_path):
        today_str = date.today().strftime("%Y-%m-%d")
        _seed_signal(db_path, "AAPL", "Apple", today_str)
        port = tmp_path / "p.json"
        port.write_text(json.dumps(_portfolio_json(["AAPL"])))

        self._run(db_path, port, today_str)

        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert rows[0]["message_type"] == "Earnings"


# ──────────────────────────────────────────────────────────────────────────────
# "once" alert type — fires only on the exact day
# ──────────────────────────────────────────────────────────────────────────────

class TestOnceAlertType:

    def _run(self, db_path, portfolio_path, days_ahead=7):
        cfg = _base_cfg(alert_type="once", days_ahead=days_ahead)
        with patch("earnings_engine.load_config", return_value=cfg), \
             patch("earnings_engine.PORTFOLIO_PATH", str(portfolio_path)), \
             patch("earnings_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("earnings_engine.send_text_message", return_value=True):
            return run_earnings_alert()

    def test_fires_exactly_on_days_ahead_day(self, tmp_path, db_path):
        exact_date = (date.today() + timedelta(days=7)).strftime("%Y-%m-%d")
        _seed_signal(db_path, "AAPL", "Apple", exact_date)
        port = tmp_path / "p.json"
        port.write_text(json.dumps(_portfolio_json(["AAPL"])))

        ok, msg = self._run(db_path, port, days_ahead=7)

        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 1

    def test_does_not_fire_on_other_days_within_window(self, tmp_path, db_path):
        three_days = (date.today() + timedelta(days=3)).strftime("%Y-%m-%d")
        _seed_signal(db_path, "AAPL", "Apple", three_days)
        port = tmp_path / "p.json"
        port.write_text(json.dumps(_portfolio_json(["AAPL"])))

        ok, msg = self._run(db_path, port, days_ahead=7)

        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 0


# ──────────────────────────────────────────────────────────────────────────────
# Return value and alert count
# ──────────────────────────────────────────────────────────────────────────────

class TestReturnValues:

    def test_return_message_includes_alert_count(self, tmp_path, db_path):
        today_str = date.today().strftime("%Y-%m-%d")
        _seed_signal(db_path, "AAPL", "Apple", today_str)
        port = tmp_path / "p.json"
        port.write_text(json.dumps(_portfolio_json(["AAPL"])))

        cfg = _base_cfg()
        with patch("earnings_engine.load_config", return_value=cfg), \
             patch("earnings_engine.PORTFOLIO_PATH", str(port)), \
             patch("earnings_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("earnings_engine.send_text_message", return_value=True):
            ok, msg = run_earnings_alert()

        assert ok is True
        assert "1" in msg

    def test_nextcloud_failure_still_writes_notification(self, tmp_path, db_path):
        today_str = date.today().strftime("%Y-%m-%d")
        _seed_signal(db_path, "AAPL", "Apple", today_str)
        port = tmp_path / "p.json"
        port.write_text(json.dumps(_portfolio_json(["AAPL"])))

        cfg = _base_cfg()
        with patch("earnings_engine.load_config", return_value=cfg), \
             patch("earnings_engine.PORTFOLIO_PATH", str(port)), \
             patch("earnings_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("earnings_engine.send_text_message", return_value=False):
            ok, msg = run_earnings_alert()

        assert ok is True
        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 1
