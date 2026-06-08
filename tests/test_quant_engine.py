"""
tests/test_quant_engine.py

Unit tests for run_daily_quant_scan() business logic in quant_engine.py.
All Yahoo Finance calls are mocked; uses the real in-memory SQLite DB from conftest.
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database


def _fake_ohlcv(n: int = 210) -> pd.DataFrame:
    """Return a deterministic OHLCV DataFrame with n rows and a DatetimeIndex."""
    idx = pd.date_range("2024-01-01", periods=n, freq="B")
    price = np.linspace(100.0, 120.0, n)
    df = pd.DataFrame(
        {
            "Open":   price * 0.99,
            "High":   price * 1.01,
            "Low":    price * 0.98,
            "Close":  price,
            "Volume": np.full(n, 1_000_000, dtype=float),
        },
        index=idx,
    )
    return df


def _today() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _get_state(scan_type: str = "test"):
    conn = database.get_connection()
    row = conn.execute(
        "SELECT status, last_processed_ticker FROM quant_scan_states WHERE scan_date = ? AND scan_type = ?",
        (_today(), scan_type),
    ).fetchone()
    conn.close()
    return row


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestEmptyTickerList:

    def test_empty_list_returns_without_error(self):
        from quant_engine import run_daily_quant_scan
        run_daily_quant_scan([], scan_type="empty_test")

    def test_empty_list_creates_no_scan_state(self):
        from quant_engine import run_daily_quant_scan
        run_daily_quant_scan([], scan_type="empty_test2")
        conn = database.get_connection()
        row = conn.execute(
            "SELECT 1 FROM quant_scan_states WHERE scan_type = ?", ("empty_test2",)
        ).fetchone()
        conn.close()
        assert row is None


class TestCompletedScanBypass:

    def test_completed_scan_is_skipped(self):
        """A scan already marked COMPLETED today must not re-process any tickers."""
        scan_type = "bypass_test"
        today = _today()

        conn = database.get_connection()
        conn.execute(
            "INSERT INTO quant_scan_states (scan_date, scan_type, last_processed_ticker, status) VALUES (?, ?, ?, ?)",
            (today, scan_type, "AAPL", "COMPLETED"),
        )
        conn.commit()
        conn.close()

        from quant_engine import run_daily_quant_scan
        with patch("quant_engine.yahoo_engine") as mock_ye, \
             patch("quant_engine.time"):
            run_daily_quant_scan(["AAPL", "MSFT"], scan_type=scan_type)
            mock_ye.get_price_history.assert_not_called()


class TestNewScanCreatesState:

    def test_new_scan_inserts_in_progress_row(self):
        """First call on a fresh day creates a quant_scan_states row with IN_PROGRESS."""
        scan_type = "newstate_test"
        fake_data = {"AAPL": _fake_ohlcv()}

        from quant_engine import run_daily_quant_scan
        with patch("quant_engine.yahoo_engine") as mock_ye, \
             patch("quant_engine.time"):
            mock_ye.get_price_history.return_value = fake_data
            run_daily_quant_scan(["AAPL"], scan_type=scan_type)

        state = _get_state(scan_type)
        assert state is not None

    def test_completed_scan_marks_completed(self):
        """After a successful scan the state row must be COMPLETED."""
        scan_type = "complete_test"
        fake_data = {"AAPL": _fake_ohlcv()}

        from quant_engine import run_daily_quant_scan
        with patch("quant_engine.yahoo_engine") as mock_ye, \
             patch("quant_engine.time"):
            mock_ye.get_price_history.return_value = fake_data
            run_daily_quant_scan(["AAPL"], scan_type=scan_type)

        state = _get_state(scan_type)
        assert state["status"] == "COMPLETED"

    def test_scan_writes_quant_signal_row(self):
        """A valid ticker must produce a row in quant_signals after a successful scan."""
        scan_type = "signal_test"
        fake_data = {"AAPL": _fake_ohlcv()}

        from quant_engine import run_daily_quant_scan
        with patch("quant_engine.yahoo_engine") as mock_ye, \
             patch("quant_engine.time"):
            mock_ye.get_price_history.return_value = fake_data
            run_daily_quant_scan(["AAPL"], scan_type=scan_type)

        conn = database.get_connection()
        row = conn.execute(
            "SELECT close_price FROM quant_signals WHERE ticker = 'AAPL' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["close_price"] > 0


class TestResumability:

    def test_in_progress_resumes_from_next_ticker(self):
        """An IN_PROGRESS state for 'AAPL' in a [AAPL, MSFT, NVDA] list must skip AAPL."""
        scan_type = "resume_test"
        today = _today()

        conn = database.get_connection()
        conn.execute(
            "INSERT INTO quant_scan_states (scan_date, scan_type, last_processed_ticker, status) VALUES (?, ?, ?, ?)",
            (today, scan_type, "AAPL", "IN_PROGRESS"),
        )
        conn.commit()
        conn.close()

        fake_data = {
            "MSFT": _fake_ohlcv(),
            "NVDA": _fake_ohlcv(),
        }

        from quant_engine import run_daily_quant_scan
        with patch("quant_engine.yahoo_engine") as mock_ye, \
             patch("quant_engine.time"):
            mock_ye.get_price_history.side_effect = lambda tickers, **_: {t: fake_data.get(t, pd.DataFrame()) for t in tickers}
            run_daily_quant_scan(["AAPL", "MSFT", "NVDA"], scan_type=scan_type)

        calls = [call.args[0] for call in mock_ye.get_price_history.call_args_list]
        fetched = [t for sublist in calls for t in sublist]
        assert "AAPL" not in fetched, "AAPL was already processed; must be skipped on resume"
        assert "MSFT" in fetched
        assert "NVDA" in fetched

    def test_insufficient_data_ticker_skipped_without_error(self):
        """A ticker with fewer than 200 rows of history must be silently skipped."""
        scan_type = "short_data_test"
        short_df = _fake_ohlcv(n=50)  # too few rows

        from quant_engine import run_daily_quant_scan
        with patch("quant_engine.yahoo_engine") as mock_ye, \
             patch("quant_engine.time"):
            mock_ye.get_price_history.return_value = {"TINY": short_df}
            run_daily_quant_scan(["TINY"], scan_type=scan_type)

        conn = database.get_connection()
        row = conn.execute(
            "SELECT 1 FROM quant_signals WHERE ticker = 'TINY'"
        ).fetchone()
        conn.close()
        assert row is None
