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

from earnings_vol_engine import run_earnings_vol_scan


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
            last_updated TEXT
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
        run_earnings_vol_scan(tickers)


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

    def test_full_scan_writes_edge_row_using_cached_date(self, db_path):
        in_window_date = date.today() + timedelta(days=3)
        e_date_str = in_window_date.strftime("%Y-%m-%d")
        expiry_str = (in_window_date + timedelta(days=2)).strftime("%Y-%m-%d")
        _seed(db_path, "NVDA", e_date_str)

        with patch("earnings_vol_engine.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("db_helpers.get_connection", side_effect=lambda: _get_conn(db_path)), \
             patch("earnings_vol_engine.yahoo_engine.get_ticker_info") as mock_info, \
             patch("earnings_vol_engine.load_or_fetch_daily_history", return_value=_fake_hist()), \
             patch("earnings_vol_engine.get_historical_earnings_move", return_value=9.2), \
             patch("earnings_vol_engine.get_implied_straddle_move", return_value=(6.5, 500, expiry_str)):
            run_earnings_vol_scan(["NVDA"])

        mock_info.assert_not_called()
        rows = _read_all(db_path, "SELECT * FROM earnings_volatility")
        assert len(rows) == 1
        assert rows[0]["ticker"] == "NVDA"
        assert rows[0]["next_earnings_date"] == e_date_str
