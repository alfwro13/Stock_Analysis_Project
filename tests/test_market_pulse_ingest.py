import sys
import sqlite3
from pathlib import Path
from unittest.mock import patch
from datetime import datetime, timedelta, timezone

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db
import market_pulse as _mp


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
    """Most recent business day; bdate_range excludes non-business end dates, keeping index length deterministic."""
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


class TestDailyOnlyInstrument:
    """Mutual funds: empty 2m data but valid 1d NAV — fetch_and_save_pulse must write a correct cache entry."""

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
        """last_updated=0 after a successful fetch keeps is_stale=True forever and triggers a refetch storm."""
        daily = _flat_daily_df([100.0, 102.0])

        before = datetime.now(timezone.utc).timestamp() - 5
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


class TestEmptyDailyPath:
    """When daily data is also unavailable (true failure / genuine delisting)."""

    def teardown_method(self):
        _clear_cache(NORMAL_TICKER)
        conn = _conn()
        conn.execute("DELETE FROM alert_state WHERE engine = 'stale_price' AND ticker = ?", (NORMAL_TICKER,))
        conn.commit()
        conn.close()

    def test_new_ticker_seeds_stale_placeholder(self):
        p1, p2 = _pulse_patches(NORMAL_TICKER, pd.DataFrame(), pd.DataFrame())
        with p1, p2, patch("market_pulse.yahoo_engine.get_single_ticker_history", return_value=None):
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        row = _read_cache(NORMAL_TICKER)
        assert row is not None
        assert row["price"] == 0.0
        assert row["last_updated"] == 0

    def test_existing_price_is_marked_fresh_on_transient_outage(self):
        """Known-good price with temporary data outage must be stamped fresh, not stale."""
        _seed_cache(NORMAL_TICKER, price=150.0, last_updated=0)

        before = datetime.now(timezone.utc).timestamp() - 5
        p1, p2 = _pulse_patches(NORMAL_TICKER, pd.DataFrame(), pd.DataFrame())
        with p1, p2, patch("market_pulse.yahoo_engine.get_single_ticker_history", return_value=None):
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        row = _read_cache(NORMAL_TICKER)
        assert row["last_updated"] > before, "Established ticker was not marked fresh on transient outage"
        assert row["price"] == pytest.approx(150.0)

    def test_existing_zero_price_stays_stale_to_retry(self):
        """Zero-price placeholder with no incoming data must stay stale so the next poll retries."""
        _seed_cache(NORMAL_TICKER, price=0.0, last_updated=0)
        p1, p2 = _pulse_patches(NORMAL_TICKER, pd.DataFrame(), pd.DataFrame())
        with p1, p2, patch("market_pulse.yahoo_engine.get_single_ticker_history", return_value=None):
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        row = _read_cache(NORMAL_TICKER)
        assert row["last_updated"] == 0

    def test_persistently_failing_ticker_during_market_hours_fires_notification(self):
        """Regression test for the LCJP.L scenario: a held ticker whose fetch has been failing
        for well over the alert threshold, during its own market's trading hours, must notify
        once — not sit silently stale forever."""
        import time as _time
        _seed_cache(NORMAL_TICKER, price=150.0, last_updated=_time.time() - 3600)  # 1 hour stale

        p1, p2 = _pulse_patches(NORMAL_TICKER, pd.DataFrame(), pd.DataFrame())
        with p1, p2, \
             patch("market_pulse.yahoo_engine.get_single_ticker_history", return_value=None), \
             patch("market_pulse.ticker_exchange", return_value="LSE"), \
             patch("market_pulse.is_trading_session", return_value=True), \
             patch("market_pulse.notification_engine.notify") as mock_notify:
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        mock_notify.assert_called_once()
        assert mock_notify.call_args[0][0] == "stale_price_alert"
        assert NORMAL_TICKER in mock_notify.call_args[0][2]

    def test_persistently_failing_ticker_does_not_notify_twice_same_day(self):
        import time as _time
        _seed_cache(NORMAL_TICKER, price=150.0, last_updated=_time.time() - 3600)

        p1, p2 = _pulse_patches(NORMAL_TICKER, pd.DataFrame(), pd.DataFrame())
        with p1, p2, \
             patch("market_pulse.yahoo_engine.get_single_ticker_history", return_value=None), \
             patch("market_pulse.ticker_exchange", return_value="LSE"), \
             patch("market_pulse.is_trading_session", return_value=True), \
             patch("market_pulse.notification_engine.notify") as mock_notify:
            _mp.fetch_and_save_pulse([NORMAL_TICKER])
            # The first call bumped last_updated to "now" (transient-outage leniency), so
            # re-seed an old timestamp to simulate a second failed attempt later the same day.
            _seed_cache(NORMAL_TICKER, price=150.0, last_updated=_time.time() - 3600)
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        mock_notify.assert_called_once()

    def test_no_notification_when_market_closed(self):
        import time as _time
        _seed_cache(NORMAL_TICKER, price=150.0, last_updated=_time.time() - 3600)

        p1, p2 = _pulse_patches(NORMAL_TICKER, pd.DataFrame(), pd.DataFrame())
        with p1, p2, \
             patch("market_pulse.yahoo_engine.get_single_ticker_history", return_value=None), \
             patch("market_pulse.ticker_exchange", return_value="LSE"), \
             patch("market_pulse.is_trading_session", return_value=False), \
             patch("market_pulse.notification_engine.notify") as mock_notify:
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        mock_notify.assert_not_called()

    def test_no_notification_when_within_threshold(self):
        import time as _time
        _seed_cache(NORMAL_TICKER, price=150.0, last_updated=_time.time() - 60)  # only 1 min stale

        p1, p2 = _pulse_patches(NORMAL_TICKER, pd.DataFrame(), pd.DataFrame())
        with p1, p2, \
             patch("market_pulse.yahoo_engine.get_single_ticker_history", return_value=None), \
             patch("market_pulse.ticker_exchange", return_value="LSE"), \
             patch("market_pulse.is_trading_session", return_value=True), \
             patch("market_pulse.notification_engine.notify") as mock_notify:
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        mock_notify.assert_not_called()


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

        before = datetime.now(timezone.utc).timestamp() - 5
        p1, p2 = _pulse_patches(NORMAL_TICKER, daily, live)
        with p1, p2:
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        row = _read_cache(NORMAL_TICKER)
        assert row["last_updated"] > before


class TestFallbackSingleHistory:
    """Mutual fund real-world path: get_price_history empty, get_single_ticker_history fallback must compute correct change."""

    def teardown_method(self):
        _clear_cache(MUTUAL_FUND)

    def test_fallback_writes_correct_change_when_daily_download_fails(self):
        """get_price_history empty + get_single_ticker_history returns data → change computed."""
        fallback_df = _flat_daily_df([100.0, 103.0])
        with (
            patch("market_pulse.yahoo_engine.get_price_history", return_value={}),
            patch("market_pulse.yahoo_engine.get_intraday", return_value={}),
            patch("market_pulse.yahoo_engine.get_single_ticker_history", return_value=fallback_df),
        ):
            _mp.fetch_and_save_pulse([MUTUAL_FUND])

        row = _read_cache(MUTUAL_FUND)
        assert row is not None
        assert row["price"] == pytest.approx(103.0)
        assert row["change_pts"] == pytest.approx(3.0, abs=0.01)
        assert row["change_pct"] == pytest.approx(3.0, abs=0.01)
        assert row["is_positive"] == 1

    def test_fallback_stamps_last_updated(self):
        """Fallback must stamp last_updated so the ticker is not stuck permanently stale."""
        fallback_df = _flat_daily_df([100.0, 102.0])
        before = datetime.now(timezone.utc).timestamp() - 5
        with (
            patch("market_pulse.yahoo_engine.get_price_history", return_value={}),
            patch("market_pulse.yahoo_engine.get_intraday", return_value={}),
            patch("market_pulse.yahoo_engine.get_single_ticker_history", return_value=fallback_df),
        ):
            _mp.fetch_and_save_pulse([MUTUAL_FUND])

        row = _read_cache(MUTUAL_FUND)
        assert row["last_updated"] > before

    def test_fallback_negative_change(self):
        fallback_df = _flat_daily_df([105.0, 102.0])
        with (
            patch("market_pulse.yahoo_engine.get_price_history", return_value={}),
            patch("market_pulse.yahoo_engine.get_intraday", return_value={}),
            patch("market_pulse.yahoo_engine.get_single_ticker_history", return_value=fallback_df),
        ):
            _mp.fetch_and_save_pulse([MUTUAL_FUND])

        row = _read_cache(MUTUAL_FUND)
        assert row["change_pts"] == pytest.approx(-3.0, abs=0.01)
        assert row["is_positive"] == 0

    def test_fallback_not_invoked_for_index_tickers(self):
        """Index tickers must skip the single-ticker fallback entirely."""
        index_ticker = "^FTSE"
        _clear_cache(index_ticker)
        with (
            patch("market_pulse.yahoo_engine.get_price_history", return_value={}),
            patch("market_pulse.yahoo_engine.get_intraday", return_value={}),
            patch("market_pulse.yahoo_engine.get_single_ticker_history") as mock_single,
        ):
            _mp.fetch_and_save_pulse([index_ticker])

        mock_single.assert_not_called()
        _clear_cache(index_ticker)


class TestUpsertLivePrice:
    """upsert_live_price(): shares a price another engine already fetched, without a new Yahoo call."""

    TICKER = "_PULSE_UPSERT_TEST"

    def setup_method(self):
        _clear_cache(self.TICKER)

    def teardown_method(self):
        _clear_cache(self.TICKER)

    def test_writes_correct_row_for_a_gain(self):
        _mp.upsert_live_price(self.TICKER, "Test Co", 110.0, 100.0)
        row = _read_cache(self.TICKER)
        assert row["price"] == 110.0
        assert row["change_pts"] == pytest.approx(10.0)
        assert row["change_pct"] == pytest.approx(10.0)
        assert row["is_positive"] == 1
        assert row["name"] == "Test Co"

    def test_writes_correct_row_for_a_loss(self):
        _mp.upsert_live_price(self.TICKER, "Test Co", 90.0, 100.0)
        row = _read_cache(self.TICKER)
        assert row["change_pts"] == pytest.approx(-10.0)
        assert row["change_pct"] == pytest.approx(-10.0)
        assert row["is_positive"] == 0

    def test_none_price_is_a_noop(self):
        _mp.upsert_live_price(self.TICKER, "Test Co", None, 100.0)
        assert _read_cache(self.TICKER) is None

    def test_zero_prev_close_is_a_noop(self):
        _mp.upsert_live_price(self.TICKER, "Test Co", 100.0, 0.0)
        assert _read_cache(self.TICKER) is None

    def test_none_prev_close_is_a_noop(self):
        _mp.upsert_live_price(self.TICKER, "Test Co", 100.0, None)
        assert _read_cache(self.TICKER) is None

    def test_second_call_updates_price_but_preserves_original_name(self):
        _mp.upsert_live_price(self.TICKER, "Original Name", 100.0, 90.0)
        _mp.upsert_live_price(self.TICKER, self.TICKER, 105.0, 90.0)
        row = _read_cache(self.TICKER)
        assert row["name"] == "Original Name"
        assert row["price"] == 105.0

    def test_accepts_an_existing_open_connection_without_closing_it(self):
        conn = _conn()
        _mp.upsert_live_price(self.TICKER, "Test Co", 110.0, 100.0, conn=conn)
        _mp.upsert_live_price(self.TICKER, "Test Co", 120.0, 100.0, conn=conn)
        row = conn.execute(
            "SELECT * FROM market_pulse_cache WHERE ticker = ?", (self.TICKER,)
        ).fetchone()
        assert row["price"] == 120.0
        conn.close()
