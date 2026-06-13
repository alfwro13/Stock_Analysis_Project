"""
tests/test_insider_engine.py  ── INSIDER ENGINE

Covers:
  send_nextcloud_message()     — Nextcloud Talk POST, auth, failure handling
  get_tickers_from_json()      — portfolio / watchlist JSON parsing
  run_insider_alert()          — config loading, filter logic, alert dispatch,
                                 connection lifecycle, and guard paths
"""

import json
import os
import sys
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch, mock_open

import pandas as pd
import pytest
import requests

sys.path.insert(0, str(Path(__file__).parent.parent))

import insider_engine
from insider_engine import get_tickers_from_json, run_insider_alert


# ──────────────────────────────────────────────────────────────────────────────
# Fixtures
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def db_path(tmp_path):
    """Temp-file SQLite with the schema run_insider_alert needs."""
    path = tmp_path / "insider_test.db"
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE stock_signals (
            ticker          TEXT PRIMARY KEY,
            company_name    TEXT,
            composite_score REAL,
            atr_stop_loss   REAL,
            current_price   REAL
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


def _read(db_path, sql, *params):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    row = conn.execute(sql, params).fetchone()
    conn.close()
    return row


def _read_all(db_path, sql, *params):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    rows = conn.execute(sql, params).fetchall()
    conn.close()
    return rows


def _mock_http_resp(status=200):
    m = MagicMock()
    m.status_code = status
    if status >= 400:
        m.raise_for_status.side_effect = requests.exceptions.HTTPError(f"HTTP {status}")
    else:
        m.raise_for_status.return_value = None
    return m


def _insider_df(days_ago=1, action="Purchase", value=100_000, shares=500):
    """Build a minimal insider_transactions DataFrame matching yfinance output."""
    date = (datetime.now(timezone.utc) - timedelta(days=days_ago)).strftime("%Y-%m-%d")
    return pd.DataFrame([{
        "Start Date": date,
        "Text": action,
        "Value": f"${value:,}",
        "Shares": f"{shares:,}",
        "Insider": "Jane Doe",
        "Position": "CEO",
    }])


# ──────────────────────────────────────────────────────────────────────────────
# 1. get_tickers_from_json()
# ──────────────────────────────────────────────────────────────────────────────

class TestGetTickersFromJson:

    def test_missing_file_returns_empty(self, tmp_path):
        result = get_tickers_from_json(str(tmp_path / "nonexistent.json"))
        assert result == []

    def test_portfolio_tickers_extracted(self, tmp_path):
        data = {
            "AAPL": {"ticker": "AAPL"},
            "MSFT": {"ticker": "MSFT"},
            "no_ticker": {},
        }
        path = tmp_path / "portfolio.json"
        path.write_text(json.dumps(data))
        result = get_tickers_from_json(str(path), is_watchlist=False)
        assert set(result) == {"AAPL", "MSFT"}

    def test_watchlist_tickers_extracted(self, tmp_path):
        data = {"watchlist": ["TSLA", "NVDA"]}
        path = tmp_path / "watchlist.json"
        path.write_text(json.dumps(data))
        result = get_tickers_from_json(str(path), is_watchlist=True)
        assert result == ["TSLA", "NVDA"]

    def test_corrupted_json_returns_empty(self, tmp_path):
        path = tmp_path / "bad.json"
        path.write_text("{not valid json")
        result = get_tickers_from_json(str(path))
        assert result == []

    def test_empty_watchlist_returns_empty(self, tmp_path):
        data = {"watchlist": []}
        path = tmp_path / "watchlist.json"
        path.write_text(json.dumps(data))
        result = get_tickers_from_json(str(path), is_watchlist=True)
        assert result == []


# ──────────────────────────────────────────────────────────────────────────────
# 2. run_insider_alert()
# ──────────────────────────────────────────────────────────────────────────────

class TestRunInsiderAlertGuards:

    def test_both_toggles_disabled_returns_skipped(self):
        cfg = {"NOTIFICATIONS": {"INSIDER_TRADING": {
            "ENABLED_PORTFOLIO": False, "ENABLED_WATCHLIST": False,
        }}}
        with patch("insider_engine.load_config", return_value=cfg):
            ok, msg = run_insider_alert()
        assert ok is True
        assert "skipped" in msg.lower()

    def test_no_valid_tickers_returns_early(self):
        cfg = {"NOTIFICATIONS": {"INSIDER_TRADING": {
            "ENABLED_PORTFOLIO": True, "ENABLED_WATCHLIST": False,
            "MIN_VALUE": 50000, "DAYS_BACK": 7,
        }}}
        with patch("insider_engine.load_config", return_value=cfg), \
             patch("insider_engine.get_tickers_from_json", return_value=[]):
            ok, msg = run_insider_alert()
        assert ok is True
        assert "no valid" in msg.lower()

    def test_0p_tickers_excluded(self, db_path):
        """Freetrade fund tickers starting with 0P must be excluded."""
        cfg = {"NOTIFICATIONS": {"INSIDER_TRADING": {
            "ENABLED_PORTFOLIO": True, "ENABLED_WATCHLIST": False,
            "MIN_VALUE": 50000, "DAYS_BACK": 7,
        }}}
        with patch("insider_engine.load_config", return_value=cfg), \
             patch("insider_engine.get_tickers_from_json", return_value=["0P0000ABC", "0P9999XYZ"]), \
             patch("insider_engine.get_connection", side_effect=lambda: _get_conn(db_path)):
            ok, msg = run_insider_alert()
        assert ok is True
        assert "no valid" in msg.lower()

    def test_fatal_config_error_returns_false(self):
        with patch("insider_engine.load_config", side_effect=RuntimeError("config missing")):
            ok, msg = run_insider_alert()
        assert ok is False
        assert "crash" in msg.lower()


class TestRunInsiderAlertFiltering:

    def _base_cfg(self, min_value=50000, days_back=7):
        return {"NOTIFICATIONS": {"INSIDER_TRADING": {
            "ENABLED_PORTFOLIO": True, "ENABLED_WATCHLIST": False,
            "MIN_VALUE": min_value, "DAYS_BACK": days_back,
        }}}

    def test_old_transaction_skipped(self, db_path):
        """Transaction older than DAYS_BACK must not trigger an alert."""
        cfg = self._base_cfg(days_back=7)
        old_df = _insider_df(days_ago=30, action="Purchase", value=200_000)

        with patch("insider_engine.load_config", return_value=cfg), \
             patch("insider_engine.get_tickers_from_json", return_value=["AAPL"]), \
             patch("insider_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("insider_engine.yahoo_engine.get_insider_transactions", return_value=old_df):
            ok, msg = run_insider_alert()

        assert ok is True
        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 0, "Old transaction must not create a notification"

    def test_sale_transaction_skipped(self, db_path):
        """Insider sale / option exercise must not trigger a buy alert."""
        cfg = self._base_cfg()
        sale_df = _insider_df(days_ago=1, action="Sale", value=500_000)

        with patch("insider_engine.load_config", return_value=cfg), \
             patch("insider_engine.get_tickers_from_json", return_value=["AAPL"]), \
             patch("insider_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("insider_engine.yahoo_engine.get_insider_transactions", return_value=sale_df):
            ok, msg = run_insider_alert()

        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 0

    def test_value_below_min_skipped(self, db_path):
        """Purchase below MIN_VALUE must not trigger an alert."""
        cfg = self._base_cfg(min_value=100_000)
        cheap_df = _insider_df(days_ago=1, action="Purchase", value=10_000)

        with patch("insider_engine.load_config", return_value=cfg), \
             patch("insider_engine.get_tickers_from_json", return_value=["AAPL"]), \
             patch("insider_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("insider_engine.yahoo_engine.get_insider_transactions", return_value=cheap_df):
            ok, msg = run_insider_alert()

        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 0

    def test_qualifying_buy_creates_notification(self, db_path):
        """Recent purchase above MIN_VALUE must write a system_notifications row."""
        cfg = self._base_cfg(min_value=50_000)
        buy_df = _insider_df(days_ago=1, action="Purchase", value=200_000, shares=1000)

        with patch("insider_engine.load_config", return_value=cfg), \
             patch("insider_engine.get_tickers_from_json", return_value=["AAPL"]), \
             patch("insider_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("notification_engine.load_config", return_value=cfg), \
             patch("notification_engine.nextcloud_talk.send_text_message", return_value=True), \
             patch("insider_engine.yahoo_engine.get_insider_transactions", return_value=buy_df):
            ok, msg = run_insider_alert()

        assert ok is True
        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 1
        assert rows[0]["message_type"] == "Insider"
        assert "AAPL" in rows[0]["message_text"]

    def test_alerts_sent_count_in_return_message(self, db_path):
        """Return message must include the number of alerts triggered."""
        cfg = self._base_cfg(min_value=50_000)
        buy_df = _insider_df(days_ago=1, action="Purchase", value=200_000)

        with patch("insider_engine.load_config", return_value=cfg), \
             patch("insider_engine.get_tickers_from_json", return_value=["AAPL"]), \
             patch("insider_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("notification_engine.load_config", return_value=cfg), \
             patch("notification_engine.nextcloud_talk.send_text_message", return_value=True), \
             patch("insider_engine.yahoo_engine.get_insider_transactions", return_value=buy_df):
            ok, msg = run_insider_alert()

        assert "1" in msg

    def test_empty_insider_df_skips_ticker(self, db_path):
        """When yahoo_engine returns an empty DataFrame, no alert fires."""
        cfg = self._base_cfg()

        with patch("insider_engine.load_config", return_value=cfg), \
             patch("insider_engine.get_tickers_from_json", return_value=["AAPL"]), \
             patch("insider_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("insider_engine.yahoo_engine.get_insider_transactions", return_value=pd.DataFrame()):
            ok, _ = run_insider_alert()

        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 0

    def test_none_insider_df_skips_ticker(self, db_path):
        """When yahoo_engine returns None for insider_transactions, no crash and no alert."""
        cfg = self._base_cfg()

        with patch("insider_engine.load_config", return_value=cfg), \
             patch("insider_engine.get_tickers_from_json", return_value=["AAPL"]), \
             patch("insider_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("insider_engine.yahoo_engine.get_insider_transactions", return_value=None):
            ok, _ = run_insider_alert()

        assert ok is True

    def test_yfinance_exception_continues_to_next_ticker(self, db_path):
        """
        REGRESSION: per-ticker exception must not abort the whole run.
        A valid second ticker should still be evaluated.
        """
        cfg = self._base_cfg(min_value=50_000)
        buy_df = _insider_df(days_ago=1, action="Purchase", value=200_000)

        def get_transactions_factory(sym):
            if sym == "BADFEED":
                raise RuntimeError("API down")
            return buy_df

        with patch("insider_engine.load_config", return_value=cfg), \
             patch("insider_engine.get_tickers_from_json", return_value=["BADFEED", "GOOD"]), \
             patch("insider_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("notification_engine.load_config", return_value=cfg), \
             patch("notification_engine.nextcloud_talk.send_text_message", return_value=True), \
             patch("insider_engine.yahoo_engine.get_insider_transactions", side_effect=get_transactions_factory):
            ok, _ = run_insider_alert()

        assert ok is True
        # GOOD ticker must still fire
        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 1

    def test_nextcloud_failure_still_writes_in_app_notification(self, db_path):
        """The in-app row is an independent channel: it is written even if the Nextcloud send fails."""
        cfg = self._base_cfg(min_value=50_000)
        buy_df = _insider_df(days_ago=1, action="Purchase", value=200_000)

        with patch("insider_engine.load_config", return_value=cfg), \
             patch("insider_engine.get_tickers_from_json", return_value=["AAPL"]), \
             patch("insider_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("notification_engine.load_config", return_value=cfg), \
             patch("notification_engine.nextcloud_talk.send_text_message", return_value=False), \
             patch("insider_engine.yahoo_engine.get_insider_transactions", return_value=buy_df):
            ok, msg = run_insider_alert()

        # In-app notification is written regardless of the Nextcloud outcome.
        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 1
        # The count now reflects alerts detected and routed (not Nextcloud delivery).
        assert "1" in msg


class TestRunInsiderAlertQuantamentalAlignment:

    def _base_cfg(self):
        return {"NOTIFICATIONS": {"INSIDER_TRADING": {
            "ENABLED_PORTFOLIO": True, "ENABLED_WATCHLIST": False,
            "MIN_VALUE": 50_000, "DAYS_BACK": 7,
        }}}

    def test_high_score_adds_alignment_banner(self, db_path):
        """When composite_score >= 60, message includes QUANTAMENTAL ALIGNMENT."""
        setup = _get_conn(db_path)
        setup.execute(
            "INSERT INTO stock_signals (ticker, company_name, composite_score, atr_stop_loss, current_price) "
            "VALUES ('AAPL', 'Apple Inc.', 75.0, 100.0, 150.0)"
        )
        setup.commit()
        setup.close()

        buy_df = _insider_df(days_ago=1, action="Purchase", value=200_000)

        with patch("insider_engine.load_config", return_value=self._base_cfg()), \
             patch("insider_engine.get_tickers_from_json", return_value=["AAPL"]), \
             patch("insider_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("notification_engine.load_config", return_value=self._base_cfg()), \
             patch("notification_engine.nextcloud_talk.send_text_message", return_value=True), \
             patch("insider_engine.yahoo_engine.get_insider_transactions", return_value=buy_df):
            run_insider_alert()

        rows = _read_all(db_path, "SELECT message_text FROM system_notifications")
        assert any("QUANTAMENTAL" in r["message_text"] for r in rows)

    def test_low_score_no_alignment_banner(self, db_path):
        """When composite_score < 60 and price is not in dip zone, no banner."""
        setup = _get_conn(db_path)
        setup.execute(
            "INSERT INTO stock_signals (ticker, company_name, composite_score, atr_stop_loss, current_price) "
            "VALUES ('AAPL', 'Apple Inc.', 30.0, 100.0, 200.0)"
        )
        setup.commit()
        setup.close()

        buy_df = _insider_df(days_ago=1, action="Purchase", value=200_000)

        with patch("insider_engine.load_config", return_value=self._base_cfg()), \
             patch("insider_engine.get_tickers_from_json", return_value=["AAPL"]), \
             patch("insider_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("notification_engine.load_config", return_value=self._base_cfg()), \
             patch("notification_engine.nextcloud_talk.send_text_message", return_value=True), \
             patch("insider_engine.yahoo_engine.get_insider_transactions", return_value=buy_df):
            run_insider_alert()

        rows = _read_all(db_path, "SELECT message_text FROM system_notifications")
        assert not any("QUANTAMENTAL" in r["message_text"] for r in rows)

    def test_dip_zone_adds_alignment_banner(self, db_path):
        """Price within 1–15% above ATR stop triggers dip banner even if score < 60."""
        setup = _get_conn(db_path)
        setup.execute(
            "INSERT INTO stock_signals (ticker, company_name, composite_score, atr_stop_loss, current_price) "
            "VALUES ('AAPL', 'Apple Inc.', 30.0, 100.0, 108.0)"
        )
        setup.commit()
        setup.close()

        buy_df = _insider_df(days_ago=1, action="Purchase", value=200_000)

        with patch("insider_engine.load_config", return_value=self._base_cfg()), \
             patch("insider_engine.get_tickers_from_json", return_value=["AAPL"]), \
             patch("insider_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("notification_engine.load_config", return_value=self._base_cfg()), \
             patch("notification_engine.nextcloud_talk.send_text_message", return_value=True), \
             patch("insider_engine.yahoo_engine.get_insider_transactions", return_value=buy_df):
            run_insider_alert()

        rows = _read_all(db_path, "SELECT message_text FROM system_notifications")
        assert any("QUANTAMENTAL" in r["message_text"] for r in rows)


class TestRunInsiderAlertConnectionLifecycle:

    def test_connection_closed_after_success(self):
        """Connection must be closed even when the run completes with no alerts."""
        cfg = {"NOTIFICATIONS": {"INSIDER_TRADING": {
            "ENABLED_PORTFOLIO": True, "ENABLED_WATCHLIST": False,
            "MIN_VALUE": 50_000, "DAYS_BACK": 7,
        }}}
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        with patch("insider_engine.load_config", return_value=cfg), \
             patch("insider_engine.get_tickers_from_json", return_value=["AAPL"]), \
             patch("insider_engine.get_connection", return_value=mock_conn), \
             patch("insider_engine.yahoo_engine.get_insider_transactions", return_value=pd.DataFrame()):
            run_insider_alert()

        mock_conn.close.assert_called_once()

    def test_connection_closed_after_ticker_exception(self):
        """Connection must be closed even when a per-ticker exception occurs."""
        cfg = {"NOTIFICATIONS": {"INSIDER_TRADING": {
            "ENABLED_PORTFOLIO": True, "ENABLED_WATCHLIST": False,
            "MIN_VALUE": 50_000, "DAYS_BACK": 7,
        }}}
        mock_conn = MagicMock()
        mock_cursor = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_cursor.fetchall.return_value = []

        with patch("insider_engine.load_config", return_value=cfg), \
             patch("insider_engine.get_tickers_from_json", return_value=["BOOM"]), \
             patch("insider_engine.get_connection", return_value=mock_conn), \
             patch("insider_engine.yahoo_engine.get_insider_transactions", side_effect=RuntimeError("engine exploded")):
            ok, _ = run_insider_alert()

        assert ok is True
        mock_conn.close.assert_called_once()

    def test_utc_cutoff_date_used(self, db_path):
        """
        REGRESSION: cutoff_date must use UTC (not naive datetime.now()).
        A UTC-aware timestamp must compare correctly with pd.to_datetime(utc=True).
        """
        cfg = {"NOTIFICATIONS": {"INSIDER_TRADING": {
            "ENABLED_PORTFOLIO": True, "ENABLED_WATCHLIST": False,
            "MIN_VALUE": 1, "DAYS_BACK": 7,
        }}}
        # Transaction dated "today" must be included (within 7 days)
        buy_df = _insider_df(days_ago=0, action="Purchase", value=5_000)

        with patch("insider_engine.load_config", return_value=cfg), \
             patch("insider_engine.get_tickers_from_json", return_value=["AAPL"]), \
             patch("insider_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("notification_engine.load_config", return_value=cfg), \
             patch("notification_engine.nextcloud_talk.send_text_message", return_value=True), \
             patch("insider_engine.yahoo_engine.get_insider_transactions", return_value=buy_df):
            ok, _ = run_insider_alert()

        rows = _read_all(db_path, "SELECT * FROM system_notifications")
        assert len(rows) == 1, "UTC-aware cutoff must include today's transaction"
