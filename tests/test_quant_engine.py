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
        with patch("quant_engine.load_or_fetch_daily_history") as mock_fetch, \
             patch("quant_engine.time"):
            run_daily_quant_scan(["AAPL", "MSFT"], scan_type=scan_type)
            mock_fetch.assert_not_called()


class TestNewScanCreatesState:

    def test_new_scan_inserts_in_progress_row(self):
        """First call on a fresh day creates a quant_scan_states row with IN_PROGRESS."""
        scan_type = "newstate_test"
        fake_data = {"AAPL": _fake_ohlcv()}

        from quant_engine import run_daily_quant_scan
        with patch("quant_engine.load_or_fetch_daily_history") as mock_fetch, \
             patch("quant_engine.time"):
            mock_fetch.return_value = fake_data["AAPL"]
            run_daily_quant_scan(["AAPL"], scan_type=scan_type)

        state = _get_state(scan_type)
        assert state is not None

    def test_completed_scan_marks_completed(self):
        """After a successful scan the state row must be COMPLETED."""
        scan_type = "complete_test"
        fake_data = {"AAPL": _fake_ohlcv()}

        from quant_engine import run_daily_quant_scan
        with patch("quant_engine.load_or_fetch_daily_history") as mock_fetch, \
             patch("quant_engine.time"):
            mock_fetch.return_value = fake_data["AAPL"]
            run_daily_quant_scan(["AAPL"], scan_type=scan_type)

        state = _get_state(scan_type)
        assert state["status"] == "COMPLETED"

    def test_scan_writes_quant_signal_row(self):
        """A valid ticker must produce a row in quant_signals after a successful scan."""
        scan_type = "signal_test"
        fake_data = {"AAPL": _fake_ohlcv()}

        from quant_engine import run_daily_quant_scan
        with patch("quant_engine.load_or_fetch_daily_history") as mock_fetch, \
             patch("quant_engine.time"):
            mock_fetch.return_value = fake_data["AAPL"]
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
        with patch("quant_engine.load_or_fetch_daily_history") as mock_fetch, \
             patch("quant_engine.time"):
            mock_fetch.side_effect = lambda ticker: fake_data.get(ticker, pd.DataFrame())
            run_daily_quant_scan(["AAPL", "MSFT", "NVDA"], scan_type=scan_type)

        fetched = [call.args[0] for call in mock_fetch.call_args_list]
        assert "AAPL" not in fetched, "AAPL was already processed; must be skipped on resume"
        assert "MSFT" in fetched
        assert "NVDA" in fetched

    def test_insufficient_data_ticker_skipped_without_error(self):
        """A ticker with fewer than 200 rows of history must be silently skipped."""
        scan_type = "short_data_test"
        short_df = _fake_ohlcv(n=50)  # too few rows

        from quant_engine import run_daily_quant_scan
        with patch("quant_engine.load_or_fetch_daily_history") as mock_fetch, \
             patch("quant_engine.time"):
            mock_fetch.return_value = short_df
            run_daily_quant_scan(["TINY"], scan_type=scan_type)

        conn = database.get_connection()
        row = conn.execute(
            "SELECT 1 FROM quant_signals WHERE ticker = 'TINY'"
        ).fetchone()
        conn.close()
        assert row is None


class TestMomentumFieldsPersisted:

    def test_mom_1m_3m_6m_written_for_200_row_ticker(self):
        """mom_1m/3m/6m are written even with 200 rows (12m_skip1m stays None with < 252 rows)."""
        scan_type = "mom_200_test"
        fake_data = {"MU": _fake_ohlcv(n=210)}

        from quant_engine import run_daily_quant_scan
        with patch("quant_engine.load_or_fetch_daily_history") as mock_fetch, \
             patch("quant_engine.time"):
            mock_fetch.return_value = fake_data["MU"]
            run_daily_quant_scan(["MU"], scan_type=scan_type)

        conn = database.get_connection()
        row = conn.execute(
            "SELECT mom_1m, mom_3m, mom_6m, mom_12m_skip1m FROM quant_signals WHERE ticker = 'MU' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["mom_1m"] is not None
        assert row["mom_3m"] is not None
        assert row["mom_6m"] is not None
        assert row["mom_12m_skip1m"] is None

    def test_mom_12m_skip1m_written_for_260_row_ticker(self):
        """All 4 momentum fields including 12M Skip-1M are written when >= 252 rows are available."""
        scan_type = "mom_260_test"
        fake_data = {"AAPL": _fake_ohlcv(n=260)}

        from quant_engine import run_daily_quant_scan
        with patch("quant_engine.load_or_fetch_daily_history") as mock_fetch, \
             patch("quant_engine.time"):
            mock_fetch.return_value = fake_data["AAPL"]
            run_daily_quant_scan(["AAPL"], scan_type=scan_type)

        conn = database.get_connection()
        row = conn.execute(
            "SELECT mom_1m, mom_3m, mom_6m, mom_12m_skip1m FROM quant_signals WHERE ticker = 'AAPL' ORDER BY date DESC LIMIT 1"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["mom_1m"] is not None
        assert row["mom_3m"] is not None
        assert row["mom_6m"] is not None
        assert row["mom_12m_skip1m"] is not None
        assert abs(row["mom_12m_skip1m"]) < 1.0

    def test_quant_scan_preserves_ml_score_on_conflict(self):
        """When a quant_signals row already has ml_confidence_score from the ML backfill,
        the quant scan's ON CONFLICT update must leave that score intact."""
        scan_type = "mom_conflict_test"
        fake_data = {"NVDA": _fake_ohlcv(n=260)}

        conn = database.get_connection()
        conn.execute(
            "INSERT INTO quant_signals (ticker, date, close_price, volume, ml_confidence_score) "
            "VALUES ('NVDA', '2025-01-10', 500.0, 1000000, 0.91)"
        )
        conn.commit()
        conn.close()

        from quant_engine import run_daily_quant_scan
        with patch("quant_engine.load_or_fetch_daily_history") as mock_fetch, \
             patch("quant_engine.time"):
            mock_fetch.return_value = fake_data["NVDA"]
            run_daily_quant_scan(["NVDA"], scan_type=scan_type)

        conn = database.get_connection()
        preserved = conn.execute(
            "SELECT ml_confidence_score FROM quant_signals WHERE ticker = 'NVDA' AND date = '2025-01-10'"
        ).fetchone()
        conn.close()
        assert preserved is not None
        assert preserved["ml_confidence_score"] == pytest.approx(0.91)


class TestRelStrengthAndHistVol:
    """rel_strength_5d/20d and hist_vol_20 used to be written only by the weekly ML backfill
    job, never by this daily scan — leaving them stale for up to a week and breaking any same-date
    feature query (e.g. score_quantile_predictions) that requires all of them non-null at once."""

    def test_hist_vol_20_written_independent_of_spy(self):
        """hist_vol_20 has no SPY dependency and must populate even when SPY data is unavailable."""
        scan_type = "hist_vol_no_spy_test"
        ticker = "ZZHISTVOL"
        fake_data = {ticker: _fake_ohlcv(n=210)}

        from quant_engine import run_daily_quant_scan
        with patch("quant_engine.load_or_fetch_daily_history") as mock_fetch, \
             patch("quant_engine.download_spy_benchmark", return_value=None), \
             patch("quant_engine.time"):
            mock_fetch.return_value = fake_data[ticker]
            run_daily_quant_scan([ticker], scan_type=scan_type)

        conn = database.get_connection()
        row = conn.execute(
            "SELECT hist_vol_20, rel_strength_5d, rel_strength_20d FROM quant_signals "
            "WHERE ticker = ? ORDER BY date DESC LIMIT 1", (ticker,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["hist_vol_20"] is not None
        assert row["rel_strength_5d"] is None
        assert row["rel_strength_20d"] is None

    def test_rel_strength_written_when_spy_available(self):
        """With SPY data present, rel_strength_5d/20d must be written as real floats."""
        scan_type = "rel_strength_test"
        ticker = "ZZRELSTR1"
        ticker_df = _fake_ohlcv(n=210)

        spy_df = ticker_df[["Close"]].copy()
        spy_df["spy_ret_5d"] = spy_df["Close"].pct_change(5)
        spy_df["spy_ret_20d"] = spy_df["Close"].pct_change(20)

        from quant_engine import run_daily_quant_scan
        with patch("quant_engine.load_or_fetch_daily_history") as mock_fetch, \
             patch("quant_engine.download_spy_benchmark", return_value=spy_df), \
             patch("quant_engine.time"):
            mock_fetch.return_value = ticker_df
            run_daily_quant_scan([ticker], scan_type=scan_type)

        conn = database.get_connection()
        row = conn.execute(
            "SELECT rel_strength_5d, rel_strength_20d FROM quant_signals "
            "WHERE ticker = ? ORDER BY date DESC LIMIT 1", (ticker,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["rel_strength_5d"] is not None
        assert row["rel_strength_20d"] is not None

    def test_rel_strength_survives_one_day_spy_lag(self):
        """SPY's cached history lagging the ticker's by one day must not blank rel_strength_5d —
        the exact regression that silently broke ML Quantile Bands for a week in production."""
        scan_type = "rel_strength_lag_test"
        ticker = "ZZRELSTR2"
        ticker_df = _fake_ohlcv(n=210)

        spy_df = ticker_df[["Close"]].copy().iloc[:-1]  # SPY missing the newest date
        spy_df["spy_ret_5d"] = spy_df["Close"].pct_change(5)
        spy_df["spy_ret_20d"] = spy_df["Close"].pct_change(20)

        from quant_engine import run_daily_quant_scan
        with patch("quant_engine.load_or_fetch_daily_history") as mock_fetch, \
             patch("quant_engine.download_spy_benchmark", return_value=spy_df), \
             patch("quant_engine.time"):
            mock_fetch.return_value = ticker_df
            run_daily_quant_scan([ticker], scan_type=scan_type)

        conn = database.get_connection()
        row = conn.execute(
            "SELECT rel_strength_5d, rel_strength_20d FROM quant_signals "
            "WHERE ticker = ? ORDER BY date DESC LIMIT 1", (ticker,)
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["rel_strength_5d"] is not None
        assert row["rel_strength_20d"] is not None
