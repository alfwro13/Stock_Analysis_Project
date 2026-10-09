"""
tests/test_ml_backfill_engine.py — ML Historical Backfill unit tests

Covers:
  • run_historical_backfill resume logic:
      - resumes from next ticker after last_processed_ticker
      - new run creates IN_PROGRESS state row
      - completed run marks state COMPLETED
  • rel_strength survives a one-day SPY lag
  • rebuild_quant_history rewrites only rows from the repair date forward
"""

import sys
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db_module


# ---------------------------------------------------------------------------
# run_historical_backfill resume logic
# ---------------------------------------------------------------------------

def _today():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def _wipe_backfill_rows():
    conn = None
    try:
        conn = _db_module.get_connection()
        conn.execute("DELETE FROM quant_scan_states WHERE scan_type = 'ml_backfill'")
        conn.execute(
            "DELETE FROM quant_signals WHERE ticker IN ('ZZBACKFILLRS', 'ZZREBUILDQH', 'ZZREBUILDSHORT', 'ZZREBUILDCOPY')"
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


@pytest.fixture(autouse=True)
def clean_backfill_state():
    """Wipe the backfill scan state and this module's quant_signals rows before and after each test."""
    _wipe_backfill_rows()
    yield
    _wipe_backfill_rows()


def _run_backfill_mocked(tickers):
    """Run run_historical_backfill with all external I/O mocked out. Returns list of tickers fetched."""
    from ml_backfill_engine import run_historical_backfill
    fetched = []

    def _fake_get_price(ticker):
        fetched.append(ticker)
        return None

    with (
        patch("ml_backfill_engine.sync_ticker_metadata"),
        patch("ml_backfill_engine.download_spy_benchmark", return_value=None),
        patch("ml_backfill_engine.load_or_fetch_daily_history") as mock_fetch,
        patch("ml_backfill_engine.time"),
    ):
        mock_fetch.side_effect = _fake_get_price
        run_historical_backfill(tickers)

    return fetched


class TestMLBackfillResume:

    def test_fresh_run_creates_in_progress_state(self):
        _run_backfill_mocked(["AAPL", "MSFT"])
        conn = None
        try:
            conn = _db_module.get_connection()
            row = conn.execute(
                "SELECT status FROM quant_scan_states WHERE scan_type = 'ml_backfill'"
            ).fetchone()
        finally:
            if conn:
                conn.close()
        assert row is not None

    def test_completed_run_marks_state_completed(self):
        _run_backfill_mocked(["AAPL"])
        conn = None
        try:
            conn = _db_module.get_connection()
            row = conn.execute(
                "SELECT status FROM quant_scan_states WHERE scan_type = 'ml_backfill'"
            ).fetchone()
        finally:
            if conn:
                conn.close()
        assert row["status"] == "COMPLETED"

    def test_resume_skips_already_processed_tickers(self):
        """Seed IN_PROGRESS with last_processed_ticker='MSFT'; only NVDA must be fetched."""
        conn = None
        try:
            conn = _db_module.get_connection()
            conn.execute(
                "INSERT INTO quant_scan_states (scan_date, scan_type, last_processed_ticker, status) "
                "VALUES (?, 'ml_backfill', 'MSFT', 'IN_PROGRESS')",
                (_today(),),
            )
            conn.commit()
        finally:
            if conn:
                conn.close()

        fetched = _run_backfill_mocked(["AAPL", "MSFT", "NVDA"])
        assert "AAPL" not in fetched, "AAPL already processed — must be skipped"
        assert "MSFT" not in fetched, "MSFT already processed — must be skipped"
        assert "NVDA" in fetched

    def test_resume_cross_day_finds_previous_in_progress(self):
        """An IN_PROGRESS row from yesterday must still be found and resumed."""
        conn = None
        try:
            conn = _db_module.get_connection()
            conn.execute(
                "INSERT INTO quant_scan_states (scan_date, scan_type, last_processed_ticker, status) "
                "VALUES ('2026-01-01', 'ml_backfill', 'AAPL', 'IN_PROGRESS')",
            )
            conn.commit()
        finally:
            if conn:
                conn.close()

        fetched = _run_backfill_mocked(["AAPL", "MSFT"])
        assert "AAPL" not in fetched
        assert "MSFT" in fetched

    def test_empty_ticker_list_returns_without_state(self):
        from ml_backfill_engine import run_historical_backfill
        run_historical_backfill([])
        conn = None
        try:
            conn = _db_module.get_connection()
            row = conn.execute(
                "SELECT 1 FROM quant_scan_states WHERE scan_type = 'ml_backfill'"
            ).fetchone()
        finally:
            if conn:
                conn.close()
        assert row is None


def _fake_ohlcv(n: int = 260, start: str = "2024-01-01") -> pd.DataFrame:
    """Deterministic OHLCV DataFrame with n business-day rows, long enough to clear the
    252-row SMA-200/momentum warm-up window inside run_historical_backfill."""
    idx = pd.date_range(start, periods=n, freq="B")
    price = np.linspace(100.0, 130.0, n)
    return pd.DataFrame(
        {
            "Open":   price * 0.99,
            "High":   price * 1.01,
            "Low":    price * 0.98,
            "Close":  price,
            "Volume": np.full(n, 1_000_000, dtype=float),
        },
        index=idx,
    )


class TestRelStrengthSurvivesSpyLag:
    """SPY's cached history lagging a ticker's freshly-fetched history by a day used to blank
    rel_strength_5d/20d for that whole row via a blanket dropna() — the exact regression that
    silently broke ML Quantile Bands / Set Targets suggestions in production for over a week
    (found 2026-07-10)."""

    def test_rel_strength_written_despite_one_day_spy_lag(self):
        # A ticker unique to this test file — "MU" is also used by test_quant_engine.py's tests,
        # and a bare "ORDER BY date DESC LIMIT 1" (no date filter) would pick up whichever test's
        # row is newest, silently reading the wrong test's data.
        ticker_df = _fake_ohlcv(n=260)
        spy_df = ticker_df.iloc[:-1]  # SPY missing the newest date

        def _fake_fetch(ticker):
            return spy_df if ticker == "SPY" else ticker_df

        from ml_backfill_engine import run_historical_backfill
        with (
            patch("ml_backfill_engine.sync_ticker_metadata"),
            patch("ml_backfill_engine.load_or_fetch_daily_history", side_effect=_fake_fetch),
            patch("ml_backfill_engine.time"),
        ):
            run_historical_backfill(["ZZBACKFILLRS"])

        conn = None
        try:
            conn = _db_module.get_connection()
            row = conn.execute(
                "SELECT rel_strength_5d, rel_strength_20d FROM quant_signals "
                "WHERE ticker = 'ZZBACKFILLRS' ORDER BY date DESC LIMIT 1"
            ).fetchone()
        finally:
            if conn:
                conn.close()
        assert row is not None
        assert row["rel_strength_5d"] is not None
        assert row["rel_strength_20d"] is not None


class TestRebuildQuantHistory:
    """A repaired bar shifts every rolling indicator after it; rebuild_quant_history re-derives
    the stored technical columns from the repair date forward and leaves earlier rows alone."""

    @staticmethod
    def _spy(df):
        spy = df[["Close"]].copy()
        spy["spy_ret_5d"] = spy["Close"].pct_change(5)
        spy["spy_ret_20d"] = spy["Close"].pct_change(20)
        return spy

    @staticmethod
    def _rows(ticker):
        conn = None
        try:
            conn = _db_module.get_connection()
            return {
                r["date"]: r for r in conn.execute(
                    "SELECT date, close_price, sma_50, rsi_14, composite_score FROM quant_signals WHERE ticker = ?",
                    (ticker,),
                ).fetchall()
            }
        finally:
            if conn:
                conn.close()

    def test_rebuilds_only_rows_from_repair_date_and_keeps_stamped_composites(self):
        from ml_backfill_engine import rebuild_quant_history

        ticker = "ZZREBUILDQH"
        df = _fake_ohlcv(n=320)
        df["Close"] = df["Close"] + np.sin(np.arange(320)) * 2.0
        df["High"] = df["Close"] * 1.01
        df["Low"] = df["Close"] * 0.98
        dates = [d.strftime("%Y-%m-%d") for d in df.index]
        with patch("ml_backfill_engine.download_spy_benchmark", return_value=self._spy(df)):
            assert rebuild_quant_history(ticker, df, dates[0]) == 320 - 252
            conn = _db_module.get_connection()
            try:
                conn.execute("UPDATE quant_signals SET composite_score = 77 WHERE ticker = ? AND date = ?",
                             (ticker, dates[-1]))
                conn.commit()
            finally:
                conn.close()
            before = self._rows(ticker)

            repaired = df.copy()
            repaired.iloc[300, repaired.columns.get_loc("Close")] *= 1.4
            repaired.iloc[300, repaired.columns.get_loc("High")] = repaired.iloc[300]["Close"] * 1.01
            written = rebuild_quant_history(ticker, repaired, dates[300])
        after = self._rows(ticker)

        assert written == 320 - 300
        assert after[dates[299]]["sma_50"] == before[dates[299]]["sma_50"]
        assert after[dates[299]]["rsi_14"] == before[dates[299]]["rsi_14"]
        assert after[dates[300]]["close_price"] == pytest.approx(float(repaired.iloc[300]["Close"]))
        assert after[dates[-1]]["sma_50"] != before[dates[-1]]["sma_50"]
        assert after[dates[-1]]["composite_score"] == 77

    def test_returns_zero_when_history_is_too_short(self):
        from ml_backfill_engine import rebuild_quant_history

        df = _fake_ohlcv(n=100)
        with patch("ml_backfill_engine.download_spy_benchmark", return_value=self._spy(df)):
            assert rebuild_quant_history("ZZREBUILDSHORT", df, "2024-01-01") == 0
        assert self._rows("ZZREBUILDSHORT") == {}

    def test_does_not_mutate_the_callers_frame(self):
        from ml_backfill_engine import rebuild_quant_history

        df = _fake_ohlcv(n=320)
        columns = list(df.columns)
        with patch("ml_backfill_engine.download_spy_benchmark", return_value=self._spy(df)):
            rebuild_quant_history("ZZREBUILDCOPY", df, "2024-01-01")
        assert list(df.columns) == columns
        assert len(df) == 320
