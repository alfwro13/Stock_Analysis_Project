"""
tests/test_market_pulse_ingest.py  ── MARKET PULSE INGEST LOGIC

Tests for the fetch_and_save_pulse() ingest path in market_pulse.py.

The existing API-level tests mock fetch_and_save_pulse entirely, so the internal
logic has had zero coverage. This file fills that gap, with particular focus on
the daily-only instrument path (e.g. mutual funds with a 0P prefix) that caused
a permanent refetch storm: yfinance returns empty 2m data for these tickers, the
old code treated that as a failure and set last_updated=0, keeping is_stale=True
forever and triggering a yfinance call on every frontend poll.

yf.download is patched throughout so these tests run offline.
"""

import sys
import sqlite3
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db
import market_pulse as _mp


# ── helpers ───────────────────────────────────────────────────────────────────

MUTUAL_FUND = "0P00018XAR.L"
NORMAL_TICKER = "_PULSE_TEST_STK"


def _conn():
    conn = sqlite3.connect(_db.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _read_cache(ticker: str):
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM market_pulse_cache WHERE ticker = ?", (ticker,)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _seed_cache(ticker, price, last_updated):
    conn = _conn()
    conn.execute(
        "INSERT OR REPLACE INTO market_pulse_cache "
        "(ticker, name, price, change_pts, change_pct, is_positive, last_updated) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ticker, ticker, price, 0.0, 0.0, 1, last_updated),
    )
    conn.commit()
    conn.close()


def _clear_cache(*tickers):
    conn = _conn()
    for t in tickers:
        conn.execute("DELETE FROM market_pulse_cache WHERE ticker = ?", (t,))
    conn.commit()
    conn.close()


def _last_bday() -> pd.Timestamp:
    """Returns today at midnight if it is a weekday, or rolls back to Friday if weekend.

    pd.bdate_range(end=<weekend-date>, periods=N) silently returns N-1 dates in
    current pandas — the non-business end date is excluded. Rolling end to the most
    recent business day keeps the index length exactly equal to len(prices) regardless
    of whether the test runs on a weekday or weekend.
    """
    return pd.offsets.BusinessDay().rollback(pd.Timestamp.now().normalize())


def _flat_daily_df(prices: list) -> pd.DataFrame:
    """Non-MultiIndex daily DataFrame (single-ticker download path)."""
    dates = pd.bdate_range(end=_last_bday(), periods=len(prices))
    return pd.DataFrame(
        {"Close": prices, "High": [p * 1.01 for p in prices],
         "Low": [p * 0.99 for p in prices], "Open": prices, "Volume": [0] * len(prices)},
        index=dates,
    )


def _flat_live_df(price: float) -> pd.DataFrame:
    """Non-MultiIndex 2m live DataFrame (single-ticker download path)."""
    # Anchor to the same business-day reference as _flat_daily_df so the
    # last_daily_date >= live_date comparison in market_pulse.py is always True,
    # matching the intraday path that the tests are designed to exercise.
    ref = _last_bday() + pd.Timedelta(hours=12)
    return pd.DataFrame(
        {"Close": [price], "High": [price * 1.005], "Low": [price * 0.995],
         "Open": [price], "Volume": [1000]},
        index=[ref],
    )


def _pulse_patches(ticker, daily_df, live_df):
    """Return a pair of patch context managers for yahoo_engine inside market_pulse."""
    daily_rv = {ticker: daily_df} if not daily_df.empty else {}
    live_rv  = {ticker: live_df}  if not live_df.empty  else {}
    return (
        patch("market_pulse.yahoo_engine.get_price_history", return_value=daily_rv),
        patch("market_pulse.yahoo_engine.get_intraday",      return_value=live_rv),
    )


# ── daily-only path (the regression) ─────────────────────────────────────────

class TestDailyOnlyInstrument:
    """
    Mutual funds / daily-priced instruments: yf.download interval='2m' is always
    empty, but interval='1d' returns valid daily NAV data. fetch_and_save_pulse must
    write a correct cache entry using the daily data rather than falling through to
    the stale-placeholder path.
    """

    def teardown_method(self):
        _clear_cache(MUTUAL_FUND)

    def test_writes_cache_entry_from_daily_data(self):
        """Core regression: daily-only ticker must produce a valid cache row."""
        daily = _flat_daily_df([100.0, 102.5])
        p1, p2 = _pulse_patches(MUTUAL_FUND, daily, pd.DataFrame())
        with p1, p2:
            _mp.fetch_and_save_pulse([MUTUAL_FUND])

        row = _read_cache(MUTUAL_FUND)
        assert row is not None, "No cache row written for daily-only ticker"
        assert row["price"] == pytest.approx(102.5)

    def test_change_calculated_from_previous_daily_close(self):
        """Day-over-day change must use daily[-2] as prev_close, not live data."""
        daily = _flat_daily_df([100.0, 103.0])   # +3.0 pts, +3.0%
        p1, p2 = _pulse_patches(MUTUAL_FUND, daily, pd.DataFrame())
        with p1, p2:
            _mp.fetch_and_save_pulse([MUTUAL_FUND])

        row = _read_cache(MUTUAL_FUND)
        assert row["change_pts"] == pytest.approx(3.0, abs=0.01)
        assert row["change_pct"] == pytest.approx(3.0, abs=0.01)
        assert row["is_positive"] == 1

    def test_negative_change_sets_is_positive_false(self):
        daily = _flat_daily_df([105.0, 102.0])   # -3.0 pts
        p1, p2 = _pulse_patches(MUTUAL_FUND, daily, pd.DataFrame())
        with p1, p2:
            _mp.fetch_and_save_pulse([MUTUAL_FUND])

        row = _read_cache(MUTUAL_FUND)
        assert row["change_pts"] == pytest.approx(-3.0, abs=0.01)
        assert row["is_positive"] == 0

    def test_single_row_daily_has_zero_change(self):
        """Only one day of history: prev_close falls back to current, so change = 0."""
        daily = _flat_daily_df([100.0])
        p1, p2 = _pulse_patches(MUTUAL_FUND, daily, pd.DataFrame())
        with p1, p2:
            _mp.fetch_and_save_pulse([MUTUAL_FUND])

        row = _read_cache(MUTUAL_FUND)
        assert row is not None
        assert row["price"] == pytest.approx(100.0)
        assert row["change_pts"] == pytest.approx(0.0, abs=0.001)

    def test_last_updated_is_recent_not_zero(self):
        """The critical invariant: last_updated must NOT be 0 after a successful daily fetch.
        A value of 0 keeps is_stale=True forever and causes an infinite refetch storm."""
        daily = _flat_daily_df([100.0, 102.0])

        before = datetime.now().timestamp() - 5
        p1, p2 = _pulse_patches(MUTUAL_FUND, daily, pd.DataFrame())
        with p1, p2:
            _mp.fetch_and_save_pulse([MUTUAL_FUND])

        row = _read_cache(MUTUAL_FUND)
        assert row["last_updated"] > before, (
            "last_updated was not stamped with current time — ticker will stay "
            "permanently stale and trigger an infinite refetch storm"
        )

    def test_does_not_log_error_for_empty_live_data(self):
        """A mutual fund with no 2m ticks must not log an error — it is expected."""
        daily = _flat_daily_df([100.0, 102.0])
        p1, p2 = _pulse_patches(MUTUAL_FUND, daily, pd.DataFrame())
        with p1, p2, patch("market_pulse.logger.error") as mock_err:
            _mp.fetch_and_save_pulse([MUTUAL_FUND])

        mock_err.assert_not_called()


# ── empty daily path ──────────────────────────────────────────────────────────

class TestEmptyDailyPath:
    """When daily data is also unavailable (true failure / genuine delisting)."""

    def teardown_method(self):
        _clear_cache(NORMAL_TICKER)

    def test_new_ticker_seeds_stale_placeholder(self):
        p1, p2 = _pulse_patches(NORMAL_TICKER, pd.DataFrame(), pd.DataFrame())
        with p1, p2:
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        row = _read_cache(NORMAL_TICKER)
        assert row is not None
        assert row["price"] == 0.0
        assert row["last_updated"] == 0

    def test_existing_price_is_marked_fresh_on_transient_outage(self):
        """A ticker with a known good price that temporarily returns no data
        should be stamped with current time, not dropped into the storm."""
        _seed_cache(NORMAL_TICKER, price=150.0, last_updated=0)

        before = datetime.now().timestamp() - 5
        p1, p2 = _pulse_patches(NORMAL_TICKER, pd.DataFrame(), pd.DataFrame())
        with p1, p2:
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        row = _read_cache(NORMAL_TICKER)
        assert row["last_updated"] > before, "Established ticker was not marked fresh on transient outage"
        assert row["price"] == pytest.approx(150.0)  # price retained

    def test_existing_zero_price_stays_stale_to_retry(self):
        """A ticker that was seeded as a placeholder (price=0) and still has no data
        should stay stale so the next poll retries it."""
        _seed_cache(NORMAL_TICKER, price=0.0, last_updated=0)
        p1, p2 = _pulse_patches(NORMAL_TICKER, pd.DataFrame(), pd.DataFrame())
        with p1, p2:
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        row = _read_cache(NORMAL_TICKER)
        assert row["last_updated"] == 0


# ── normal intraday path ──────────────────────────────────────────────────────

class TestNormalIntradayPath:
    """Standard equities with both daily and live 2m data available."""

    def teardown_method(self):
        _clear_cache(NORMAL_TICKER)

    def test_normal_ticker_uses_live_price(self):
        daily = _flat_daily_df([100.0, 100.5])
        live = _flat_live_df(101.75)
        p1, p2 = _pulse_patches(NORMAL_TICKER, daily, live)
        with p1, p2:
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        row = _read_cache(NORMAL_TICKER)
        assert row is not None
        assert row["price"] == pytest.approx(101.75)

    def test_normal_ticker_change_vs_prev_daily_close(self):
        """Change is live_price - daily[-2] when daily[-1] >= today."""
        daily = _flat_daily_df([98.0, 100.0])
        live = _flat_live_df(101.0)   # change = 101 - 98 = +3.0 vs daily[-2]
        p1, p2 = _pulse_patches(NORMAL_TICKER, daily, live)
        with p1, p2:
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        row = _read_cache(NORMAL_TICKER)
        # daily[-1] date >= live date → prev_close = daily[-2] = 98.0
        assert row["change_pts"] == pytest.approx(3.0, abs=0.01)

    def test_normal_ticker_last_updated_is_recent(self):
        daily = _flat_daily_df([100.0, 101.0])
        live = _flat_live_df(101.5)

        before = datetime.now().timestamp() - 5
        p1, p2 = _pulse_patches(NORMAL_TICKER, daily, live)
        with p1, p2:
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        row = _read_cache(NORMAL_TICKER)
        assert row["last_updated"] > before
