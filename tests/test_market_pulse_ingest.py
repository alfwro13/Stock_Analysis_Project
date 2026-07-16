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


def _today_ts() -> pd.Timestamp:
    """Real UTC 'today' — utils.is_daily_bar_still_forming() requires the daily bar's own date
    to equal actual today, so fixtures anchored to the last business day would fail this
    invariant on a Saturday/Sunday test run (Friday < today)."""
    return pd.Timestamp(datetime.now(timezone.utc).date())


def _flat_daily_df(prices: list) -> pd.DataFrame:
    """Non-MultiIndex daily DataFrame (single-ticker download path)."""
    dates = pd.date_range(end=_today_ts(), periods=len(prices), freq="D")
    return pd.DataFrame(
        {"Close": prices, "High": [p * 1.01 for p in prices],
         "Low": [p * 0.99 for p in prices], "Open": prices, "Volume": [0] * len(prices)},
        index=dates,
    )


def _flat_live_df(price: float) -> pd.DataFrame:
    """Non-MultiIndex 2m live DataFrame (single-ticker download path)."""
    # Anchor to the same day as _flat_daily_df so the last_daily_date >= live_date comparison
    # in market_pulse.py is always True, matching the intraday path the tests exercise.
    ref = _today_ts() + pd.Timedelta(hours=12)
    return pd.DataFrame(
        {"Close": [price], "High": [price * 1.005], "Low": [price * 0.995],
         "Open": [price], "Volume": [1000]},
        index=[ref],
    )


@pytest.fixture(autouse=True)
def _default_quote_snapshot():
    """get_quote_snapshot defaults to unavailable for every test in this file, so pre-existing
    tests keep exercising the same daily/live diffing fallback path they always have — tests
    of the new Yahoo-quote-snapshot primary path override this with their own inner patch."""
    with patch("market_pulse.yahoo_engine.get_quote_snapshot", return_value=None):
        yield


def _pulse_patches(ticker, daily_df, live_df):
    """Return a pair of patch context managers for yahoo_engine inside market_pulse."""
    daily_rv = {ticker: daily_df} if not daily_df.empty else {}
    live_rv  = {ticker: live_df}  if not live_df.empty  else {}
    return (
        patch("market_pulse.yahoo_engine.get_price_history", return_value=daily_rv),
        patch("market_pulse.yahoo_engine.get_intraday",      return_value=live_rv),
    )


def _snapshot(regular_price, regular_change=None, regular_change_pct=None, market_state=None,
              regular_previous_close=None, pre_price=None, pre_change=None, pre_change_pct=None,
              post_price=None, post_change=None, post_change_pct=None):
    """Builds a yahoo_engine.get_quote_snapshot()-shaped dict for tests."""
    return {
        "market_state": market_state,
        "regular_price": regular_price,
        "regular_change": regular_change,
        "regular_change_pct": regular_change_pct,
        "regular_previous_close": regular_previous_close,
        "pre_market_price": pre_price,
        "pre_market_change": pre_change,
        "pre_market_change_pct": pre_change_pct,
        "post_market_price": post_price,
        "post_market_change": post_change,
        "post_market_change_pct": post_change_pct,
    }


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

    def test_skips_yahoo_intraday_call_for_mutual_fund(self):
        """The actual log spam comes from yfinance's own internal logger inside
        get_intraday(), which fires before our code ever sees an exception — the only
        fix is to never make the doomed call for a ticker that never has 5m bars."""
        daily = _flat_daily_df([100.0, 102.0])
        with patch("market_pulse.yahoo_engine.get_price_history", return_value={MUTUAL_FUND: daily}), \
             patch("market_pulse.get_mutual_fund_tickers", return_value={MUTUAL_FUND}), \
             patch("market_pulse.yahoo_engine.get_intraday") as mock_intraday:
            _mp.fetch_and_save_pulse([MUTUAL_FUND])

        mock_intraday.assert_not_called()

    def test_intraday_call_excludes_mutual_fund_from_mixed_batch(self):
        """A portfolio poll mixing a mutual fund with a normal ticker must still fetch
        intraday for the normal ticker, just with the mutual fund filtered out."""
        daily = _flat_daily_df([100.0, 102.0])
        with patch("market_pulse.yahoo_engine.get_price_history",
                    return_value={MUTUAL_FUND: daily, NORMAL_TICKER: daily}), \
             patch("market_pulse.get_mutual_fund_tickers", return_value={MUTUAL_FUND}), \
             patch("market_pulse.yahoo_engine.get_intraday", return_value={}) as mock_intraday:
            _mp.fetch_and_save_pulse([MUTUAL_FUND, NORMAL_TICKER])

        mock_intraday.assert_called_once_with([NORMAL_TICKER], period="2d", interval="2m", prepost=True)
        _clear_cache(NORMAL_TICKER)


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
        """Change is live_price - daily[-2] when daily[-1] >= today and the exchange is
        confirmed still open — exchange state is mocked so this doesn't depend on the real
        wall-clock/weekend at test-run time (see TestExchangeClosedStillFormingGate)."""
        daily = _flat_daily_df([98.0, 100.0])
        live = _flat_live_df(101.0)   # change = 101 - 98 = +3.0 vs daily[-2]
        p1, p2 = _pulse_patches(NORMAL_TICKER, daily, live)
        with p1, p2, patch("market_pulse.is_exchange_open", return_value=True):
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


class TestExchangeClosedStillFormingGate:
    """Regression (2026-07-15): fetch_and_save_pulse() is reachable at arbitrary times of day
    (age-based staleness refreshes, on-demand single-ticker fetch, HA refresh-now) rather than
    only while its ticker's exchange is confirmed open, so it must thread a real exchange-open
    signal into is_daily_bar_still_forming() the same way data_engine._drop_in_progress_last_bar()
    already does — otherwise a same-UTC-day post-close refresh looks identical to a genuine
    mid-session one and prev_close is wrongly taken from daily[-2] instead of the already-final
    daily[-1]."""

    def teardown_method(self):
        _clear_cache(NORMAL_TICKER)

    def test_keeps_last_daily_close_as_prev_close_when_exchange_confirmed_closed(self):
        daily = _flat_daily_df([98.0, 100.0])
        live = _flat_live_df(101.0)
        p1, p2 = _pulse_patches(NORMAL_TICKER, daily, live)
        with p1, p2, patch("market_pulse.is_exchange_open", return_value=False):
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        row = _read_cache(NORMAL_TICKER)
        # Exchange confirmed closed -> daily[-1]=100.0 is already the final close, not still forming.
        assert row["change_pts"] == pytest.approx(1.0, abs=0.01)

    def test_uses_prev_daily_close_when_exchange_confirmed_open(self):
        daily = _flat_daily_df([98.0, 100.0])
        live = _flat_live_df(101.0)
        p1, p2 = _pulse_patches(NORMAL_TICKER, daily, live)
        with p1, p2, patch("market_pulse.is_exchange_open", return_value=True):
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        row = _read_cache(NORMAL_TICKER)
        assert row["change_pts"] == pytest.approx(3.0, abs=0.01)


class TestQuoteSnapshotSessionTagging:
    """fetch_and_save_pulse() must source price/change_pts/change_pct purely from Yahoo's own
    regularMarketPrice/regularMarketChange* fields when its quote snapshot is available — never
    an extended-hours tick — and populate the separate extended_* columns only when Yahoo's own
    marketState says a pre/post session is genuinely active. This is the core regression
    coverage for never mixing closing price with pre/post-market data in the same column."""

    def teardown_method(self):
        _clear_cache(NORMAL_TICKER, "^GSPC")

    def test_regular_session_writes_pure_regular_price_no_extended(self):
        daily = _flat_daily_df([100.0, 100.5])
        live = _flat_live_df(101.0)
        snap = _snapshot(101.0, regular_change=0.5, regular_change_pct=0.5, market_state="REGULAR")
        p1, p2 = _pulse_patches(NORMAL_TICKER, daily, live)
        with p1, p2, patch("market_pulse.yahoo_engine.get_quote_snapshot", return_value=snap):
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        row = _read_cache(NORMAL_TICKER)
        assert row["price"] == pytest.approx(101.0)
        assert row["change_pct"] == pytest.approx(0.5)
        assert row["market_state"] == "REGULAR"
        assert row["extended_price"] is None
        assert row["extended_session"] is None

    def test_pre_market_tick_never_contaminates_regular_price_column(self):
        daily = _flat_daily_df([100.0, 100.5])
        live = _flat_live_df(101.5)  # would have been misread as "the price" before this fix
        snap = _snapshot(
            100.5, regular_change=0.0, regular_change_pct=0.0, market_state="PRE",
            pre_price=101.5, pre_change=1.0, pre_change_pct=1.0,
        )
        p1, p2 = _pulse_patches(NORMAL_TICKER, daily, live)
        with p1, p2, patch("market_pulse.yahoo_engine.get_quote_snapshot", return_value=snap):
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        row = _read_cache(NORMAL_TICKER)
        assert row["price"] == pytest.approx(100.5), "Pre-market tick leaked into the regular price column"
        assert row["change_pct"] == pytest.approx(0.0)
        assert row["extended_price"] == pytest.approx(101.5)
        assert row["extended_change_pct"] == pytest.approx(1.0)
        assert row["extended_session"] == "pre"

    def test_post_market_tick_never_contaminates_regular_price_column(self):
        daily = _flat_daily_df([100.0, 102.0])
        live = _flat_live_df(101.0)
        snap = _snapshot(
            102.0, regular_change=2.0, regular_change_pct=2.0, market_state="POST",
            post_price=101.0, post_change=-1.0, post_change_pct=-0.98,
        )
        p1, p2 = _pulse_patches(NORMAL_TICKER, daily, live)
        with p1, p2, patch("market_pulse.yahoo_engine.get_quote_snapshot", return_value=snap):
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        row = _read_cache(NORMAL_TICKER)
        assert row["price"] == pytest.approx(102.0), "After-hours tick leaked into the regular price column"
        assert row["change_pct"] == pytest.approx(2.0)
        assert row["extended_price"] == pytest.approx(101.0)
        assert row["extended_session"] == "post"

    def test_closed_state_clears_extended_even_if_stale_fields_present(self):
        """Yahoo can carry a stale postMarketPrice into a CLOSED payload — must never surface it."""
        daily = _flat_daily_df([100.0, 100.5])
        live = _flat_live_df(101.0)
        snap = _snapshot(
            100.5, regular_change=0.5, regular_change_pct=0.5, market_state="CLOSED",
            post_price=99.0, post_change=-1.5, post_change_pct=-1.5,
        )
        p1, p2 = _pulse_patches(NORMAL_TICKER, daily, live)
        with p1, p2, patch("market_pulse.yahoo_engine.get_quote_snapshot", return_value=snap):
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        row = _read_cache(NORMAL_TICKER)
        assert row["extended_price"] is None
        assert row["extended_session"] is None

    def test_market_state_written_for_any_ticker_not_just_proxies(self):
        daily = _flat_daily_df([100.0, 100.5])
        live = _flat_live_df(101.0)
        snap = _snapshot(101.0, regular_change=1.0, regular_change_pct=1.0, market_state="REGULAR")
        p1, p2 = _pulse_patches(NORMAL_TICKER, daily, live)
        with p1, p2, patch("market_pulse.yahoo_engine.get_quote_snapshot", return_value=snap):
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        row = _read_cache(NORMAL_TICKER)
        assert row["market_state"] == "REGULAR"

    def test_price_refresh_preserves_previously_written_market_state_on_snapshot_failure(self):
        """Regression: a transient quote-snapshot failure on a later refresh must not wipe a
        previously-known market_state back to NULL."""
        daily = _flat_daily_df([100.0, 100.5])
        live = _flat_live_df(101.0)
        snap = _snapshot(101.0, regular_change=1.0, regular_change_pct=1.0, market_state="REGULAR")
        p1, p2 = _pulse_patches("^GSPC", daily, live)
        with p1, p2, patch("market_pulse.yahoo_engine.get_quote_snapshot", return_value=snap):
            _mp.fetch_and_save_pulse(["^GSPC"])

        # Second refresh: the quote snapshot itself is unavailable this time.
        daily2 = _flat_daily_df([100.5, 102.0])
        live2 = _flat_live_df(102.5)
        p3, p4 = _pulse_patches("^GSPC", daily2, live2)
        with p3, p4, patch("market_pulse.yahoo_engine.get_quote_snapshot", return_value=None):
            _mp.fetch_and_save_pulse(["^GSPC"])

        row = _read_cache("^GSPC")
        assert row["price"] == pytest.approx(102.5)
        assert row["market_state"] == "REGULAR"


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


class TestStaleDailyHistoryFallback:
    """Yahoo's chart-history endpoint can silently truncate the requested window for some
    symbols (e.g. ^KS200 returning only 2 rows dated days before the live feed) — fetch_and_save_pulse
    must fall back to the quoteSummary endpoint's own previousClose rather than computing change_pct
    against a stale/wrong daily row."""

    TICKER = "_STALE_DAILY_TEST"

    def teardown_method(self):
        _clear_cache(self.TICKER)

    def _stale_daily_and_live(self):
        stale_daily = pd.DataFrame(
            {"Close": [1219.62, 1299.30], "High": [1304.45, 1308.19], "Low": [1213.66, 1176.04],
             "Open": [1269.77, 1235.20], "Volume": [181300000, 156700000]},
            index=pd.to_datetime(["2026-07-02", "2026-07-03"]),
        )
        live = pd.DataFrame(
            {"Close": [1158.37], "High": [1162.11], "Low": [1155.00], "Open": [1160.00], "Volume": [1000]},
            index=pd.to_datetime(["2026-07-08 06:30:00"]),
        )
        return stale_daily, live

    def test_falls_back_to_info_previous_close_when_daily_history_is_stale(self):
        stale_daily, live = self._stale_daily_and_live()
        p1, p2 = _pulse_patches(self.TICKER, stale_daily, live)
        with p1, p2, patch("market_pulse.yahoo_engine.get_ticker_info", return_value={"regularMarketPreviousClose": 1225.57}):
            _mp.fetch_and_save_pulse([self.TICKER])

        row = _read_cache(self.TICKER)
        assert row["price"] == pytest.approx(1158.37)
        assert row["change_pct"] == pytest.approx(-5.483, abs=0.01)

    def test_does_not_fall_back_when_daily_history_is_fresh(self):
        """Normal case (daily and live feeds within a few days of each other) must not call get_ticker_info."""
        daily = _flat_daily_df([100.0, 100.5])
        live = _flat_live_df(101.0)
        p1, p2 = _pulse_patches(self.TICKER, daily, live)
        with p1, p2, patch("market_pulse.yahoo_engine.get_ticker_info") as mock_info:
            _mp.fetch_and_save_pulse([self.TICKER])
        mock_info.assert_not_called()

    def test_missing_info_previous_close_does_not_crash(self):
        """If the .info fallback also can't help, the ticker must still be written (no crash)."""
        stale_daily, live = self._stale_daily_and_live()
        p1, p2 = _pulse_patches(self.TICKER, stale_daily, live)
        with p1, p2, patch("market_pulse.yahoo_engine.get_ticker_info", return_value=None):
            _mp.fetch_and_save_pulse([self.TICKER])

        row = _read_cache(self.TICKER)
        assert row["price"] == pytest.approx(1158.37)

    def test_falls_back_when_daily_feed_has_caught_up_but_still_has_a_gap(self):
        """Regression: even once Yahoo's daily feed re-includes "today" as its last row, the row
        actually used as prev_close (the still-forming branch's t_daily[-2]) can still be several
        sessions further back if rows were dropped out of the middle of the window — checking
        only the feed's own last date isn't enough. Found 2026-07-09 on ^KS200: the first fix
        (comparing only last dates) stopped catching this once Yahoo's feed "caught up"."""
        today_bday = _today_ts()
        old_bday = today_bday - pd.Timedelta(days=8)
        daily = pd.DataFrame(
            {"Close": [1299.30, 1171.43], "High": [1308.19, 1175.0], "Low": [1176.04, 1168.0],
             "Open": [1235.20, 1170.0], "Volume": [156700000, 150000000]},
            index=[old_bday, today_bday],
        )
        live = pd.DataFrame(
            {"Close": [1171.43], "High": [1171.87], "Low": [1170.0], "Open": [1171.0], "Volume": [1000]},
            index=[today_bday + pd.Timedelta(hours=6)],
        )
        p1, p2 = _pulse_patches(self.TICKER, daily, live)
        with p1, p2, \
             patch("market_pulse.is_exchange_open", return_value=True), \
             patch("market_pulse.yahoo_engine.get_ticker_info", return_value={"regularMarketPreviousClose": 1158.37}):
            _mp.fetch_and_save_pulse([self.TICKER])

        row = _read_cache(self.TICKER)
        assert row["price"] == pytest.approx(1171.43)
        assert row["change_pct"] == pytest.approx(1.127, abs=0.01)


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


def _read_sparkline(ticker: str):
    conn = _conn()
    rows = conn.execute(
        "SELECT ts, price FROM market_pulse_sparkline WHERE ticker = ? ORDER BY ts", (ticker,)
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _clear_sparkline(*tickers):
    conn = _conn()
    for t in tickers:
        conn.execute("DELETE FROM market_pulse_sparkline WHERE ticker = ?", (t,))
    conn.commit()
    conn.close()


def _multi_point_live_df(prices: list) -> pd.DataFrame:
    ref = _today_ts() + pd.Timedelta(hours=9)
    dates = [ref + pd.Timedelta(minutes=2 * i) for i in range(len(prices))]
    return pd.DataFrame(
        {"Close": prices, "High": [p * 1.005 for p in prices], "Low": [p * 0.995 for p in prices],
         "Open": prices, "Volume": [1000] * len(prices)},
        index=dates,
    )


class TestSparklineWrite:
    """fetch_and_save_pulse() must persist today's intraday points for the Markets page mini
    chart, and leave prior points untouched (not wipe them) when there's no fresh intraday data."""

    def teardown_method(self):
        _clear_cache(NORMAL_TICKER)
        _clear_sparkline(NORMAL_TICKER)

    def test_writes_sparkline_points_from_live_intraday_data(self):
        daily = _flat_daily_df([100.0, 100.5])
        live = _multi_point_live_df([100.5, 100.7, 100.9])
        p1, p2 = _pulse_patches(NORMAL_TICKER, daily, live)
        with p1, p2:
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        points = _read_sparkline(NORMAL_TICKER)
        assert len(points) == 3
        assert points[-1]["price"] == pytest.approx(100.9)

    def test_full_replace_on_each_fetch_cycle(self):
        daily = _flat_daily_df([100.0, 100.5])
        live1 = _multi_point_live_df([100.5, 100.7])
        p1, p2 = _pulse_patches(NORMAL_TICKER, daily, live1)
        with p1, p2:
            _mp.fetch_and_save_pulse([NORMAL_TICKER])
        assert len(_read_sparkline(NORMAL_TICKER)) == 2

        live2 = _multi_point_live_df([101.0, 101.2, 101.4])
        p3, p4 = _pulse_patches(NORMAL_TICKER, daily, live2)
        with p3, p4:
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        points = _read_sparkline(NORMAL_TICKER)
        assert len(points) == 3
        assert points[0]["price"] == pytest.approx(101.0)

    def test_empty_intraday_data_leaves_prior_sparkline_untouched(self):
        """Market closed this cycle (t_live empty) — the last session's line must persist."""
        daily = _flat_daily_df([100.0, 100.5])
        live = _multi_point_live_df([100.5, 100.7])
        p1, p2 = _pulse_patches(NORMAL_TICKER, daily, live)
        with p1, p2:
            _mp.fetch_and_save_pulse([NORMAL_TICKER])
        assert len(_read_sparkline(NORMAL_TICKER)) == 2

        with patch("market_pulse.yahoo_engine.get_price_history", return_value={NORMAL_TICKER: daily}), \
             patch("market_pulse.yahoo_engine.get_intraday", return_value={}):
            _mp.fetch_and_save_pulse([NORMAL_TICKER])

        points = _read_sparkline(NORMAL_TICKER)
        assert len(points) == 2


class TestGetIntradayPoints:
    def teardown_method(self):
        _clear_sparkline(NORMAL_TICKER)

    def test_returns_points_oldest_first(self):
        conn = _conn()
        conn.execute("INSERT INTO market_pulse_sparkline (ticker, ts, price) VALUES (?, ?, ?)", (NORMAL_TICKER, 200.0, 10.0))
        conn.execute("INSERT INTO market_pulse_sparkline (ticker, ts, price) VALUES (?, ?, ?)", (NORMAL_TICKER, 100.0, 9.0))
        conn.commit()
        conn.close()

        points = _mp.get_intraday_points(NORMAL_TICKER)
        assert points == [[100.0, 9.0], [200.0, 10.0]]

    def test_unknown_ticker_returns_empty_list(self):
        assert _mp.get_intraday_points("_NO_SUCH_TICKER") == []

    def test_respects_max_points_limit(self):
        conn = _conn()
        for i in range(5):
            conn.execute(
                "INSERT INTO market_pulse_sparkline (ticker, ts, price) VALUES (?, ?, ?)",
                (NORMAL_TICKER, float(i), float(i)),
            )
        conn.commit()
        conn.close()

        points = _mp.get_intraday_points(NORMAL_TICKER, max_points=2)
        assert len(points) == 2
        assert points == [[3.0, 3.0], [4.0, 4.0]]


class TestTickerRegistryAccessors:
    """get_index_tickers()/get_pulse_index_tickers() read through market_ticker_registry;
    reload_ticker_registry() must bust both caches after a registry write."""

    def test_get_index_tickers_includes_full_registry(self):
        tickers = _mp.get_index_tickers()
        # A Markets-page-only ticker (not a static Market Pulse tile) must still resolve here.
        assert "GC=F" in tickers
        assert tickers["GC=F"] == "Gold"

    def test_get_pulse_index_tickers_excludes_markets_only_tickers(self):
        tickers = _mp.get_pulse_index_tickers()
        assert "GC=F" not in tickers
        assert "^GSPC" in tickers

    def test_reload_ticker_registry_picks_up_new_row(self):
        import db_helpers
        db_helpers.upsert_ticker_registry_row(
            ticker="_TST_REG_TICK", display_name="Test Reg Tick", region="Europe",
            asset_type="Index", exchange="LSE", currency="GBP",
        )
        try:
            # Cache was already warm from earlier tests in this session — must not see it yet.
            assert "_TST_REG_TICK" not in _mp.get_index_tickers()
            _mp.reload_ticker_registry()
            assert "_TST_REG_TICK" in _mp.get_index_tickers()
        finally:
            conn = _conn()
            conn.execute("DELETE FROM market_ticker_registry WHERE ticker = '_TST_REG_TICK'")
            conn.commit()
            conn.close()
            _mp.reload_ticker_registry()
