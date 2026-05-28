# test_market_pulse.py
import sqlite3
import tempfile
import threading
import time
import unittest
from pathlib import Path
from typing import Any, Dict
from unittest.mock import MagicMock, patch

import pandas as pd

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SCHEMA_MARKET_PULSE = """
    CREATE TABLE IF NOT EXISTS market_pulse_cache (
        ticker      TEXT PRIMARY KEY,
        name        TEXT,
        price       REAL,
        change_pts  REAL,
        change_pct  REAL,
        is_positive BOOLEAN,
        last_updated REAL
    )
"""

_SCHEMA_QUANT_SIGNALS = """
    CREATE TABLE IF NOT EXISTS quant_signals (
        ticker          TEXT,
        date            TEXT,
        close_price     REAL,
        sentiment_score REAL,
        PRIMARY KEY (ticker, date)
    )
"""


def _make_db(path: str) -> None:
    conn = sqlite3.connect(path)
    conn.execute(_SCHEMA_MARKET_PULSE)
    conn.execute(_SCHEMA_QUANT_SIGNALS)
    conn.commit()
    conn.close()


def _real_conn(path: str) -> sqlite3.Connection:
    conn = sqlite3.connect(path, timeout=5.0)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_pulse(path: str, rows: list) -> None:
    conn = sqlite3.connect(path)
    conn.executemany(
        "INSERT OR REPLACE INTO market_pulse_cache "
        "(ticker, name, price, change_pts, change_pct, is_positive, last_updated) "
        "VALUES (?,?,?,?,?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def _seed_signals(path: str, rows: list) -> None:
    conn = sqlite3.connect(path)
    conn.executemany(
        "INSERT OR REPLACE INTO quant_signals (ticker, date, sentiment_score) VALUES (?,?,?)",
        rows,
    )
    conn.commit()
    conn.close()


def _read_pulse(path: str, ticker: str) -> Dict[str, Any]:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        "SELECT * FROM market_pulse_cache WHERE ticker = ?", (ticker,)
    ).fetchone()
    conn.close()
    return dict(row) if row else {}


def _make_flat_df(prices: list) -> pd.DataFrame:
    """Single-ticker flat DataFrame with a DatetimeIndex and Close column."""
    dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=len(prices), freq="D")
    return pd.DataFrame({"Close": prices}, index=dates)


def _make_all_nan_df() -> pd.DataFrame:
    """Structurally valid DataFrame whose Close column is all-NaN.

    Passes the 'if not df.empty' and MultiIndex checks, but yields an empty
    DataFrame after dropna(subset=['Close']), triggering the last_updated=0 path.
    """
    dates = pd.date_range(end=pd.Timestamp.today().normalize(), periods=2, freq="D")
    return pd.DataFrame({"Close": [float("nan"), float("nan")]}, index=dates)


def _make_multi_df(ticker_prices: Dict[str, list]) -> pd.DataFrame:
    """Multi-ticker MultiIndex DataFrame as yfinance returns for >1 ticker."""
    frames = {}
    for ticker, prices in ticker_prices.items():
        dates = pd.date_range(
            end=pd.Timestamp.today().normalize(), periods=len(prices), freq="D"
        )
        frames[ticker] = pd.DataFrame({"Close": prices}, index=dates)
    return pd.concat(frames, axis=1)


# ---------------------------------------------------------------------------
# Base class: spins up a fresh temp DB per test and patches get_connection
# ---------------------------------------------------------------------------

class _PulseTestBase(unittest.TestCase):
    def setUp(self):
        self._db_fd, self._db_path = tempfile.mkstemp(suffix=".db")
        _make_db(self._db_path)
        self._conn_patcher = patch(
            "market_pulse.get_connection",
            side_effect=lambda: _real_conn(self._db_path),
        )
        self._conn_patcher.start()

        self._config_patcher = patch(
            "market_pulse.load_config",
            return_value={"IGNORED_TICKERS": [], "UI_PREFERENCES": {"REFRESH_RATE": 60}},
        )
        self._config_patcher.start()

        # Reset the fetch lock so tests are independent
        import market_pulse
        market_pulse._FETCH_LOCK = threading.Lock()

    def tearDown(self):
        self._conn_patcher.stop()
        self._config_patcher.stop()
        import os
        os.close(self._db_fd)
        os.unlink(self._db_path)


# ===========================================================================
# get_all_cached_pulse
# ===========================================================================

class TestGetAllCachedPulse(_PulseTestBase):

    def test_empty_db_returns_empty_dict(self):
        from market_pulse import get_all_cached_pulse
        result = get_all_cached_pulse()
        self.assertEqual(result, {})

    def test_returns_all_rows_keyed_by_ticker(self):
        _seed_pulse(self._db_path, [
            ("^FTSE", "UK FTSE 100", 8000.0, 50.0, 0.63, 1, time.time()),
            ("^GSPC", "US S&P 500",  5200.0, -10.0, -0.19, 0, time.time()),
        ])
        from market_pulse import get_all_cached_pulse
        result = get_all_cached_pulse()
        self.assertIn("^FTSE", result)
        self.assertIn("^GSPC", result)
        self.assertEqual(len(result), 2)

    def test_fresh_row_is_not_stale(self):
        _seed_pulse(self._db_path, [("^FTSE", "UK FTSE 100", 8000.0, 0.0, 0.0, 1, time.time())])
        from market_pulse import get_all_cached_pulse
        result = get_all_cached_pulse()
        self.assertFalse(result["^FTSE"]["is_stale"])

    def test_old_row_is_stale(self):
        stale_ts = time.time() - 9999
        _seed_pulse(self._db_path, [("^FTSE", "UK FTSE 100", 8000.0, 0.0, 0.0, 1, stale_ts)])
        from market_pulse import get_all_cached_pulse
        result = get_all_cached_pulse()
        self.assertTrue(result["^FTSE"]["is_stale"])

    def test_last_updated_zero_is_always_stale(self):
        _seed_pulse(self._db_path, [("^FTSE", "UK FTSE 100", 0.0, 0.0, 0.0, 1, 0)])
        from market_pulse import get_all_cached_pulse
        result = get_all_cached_pulse()
        self.assertTrue(result["^FTSE"]["is_stale"])

    def test_is_positive_cast_to_bool(self):
        _seed_pulse(self._db_path, [
            ("^FTSE", "UK FTSE 100", 8000.0, 10.0, 0.1, 1, time.time()),
            ("^GSPC", "US S&P 500",  5200.0, -5.0, -0.1, 0, time.time()),
        ])
        from market_pulse import get_all_cached_pulse
        result = get_all_cached_pulse()
        self.assertIs(type(result["^FTSE"]["is_positive"]), bool)
        self.assertTrue(result["^FTSE"]["is_positive"])
        self.assertFalse(result["^GSPC"]["is_positive"])

    def test_row_shape(self):
        _seed_pulse(self._db_path, [("^FTSE", "UK FTSE 100", 8000.0, 10.0, 0.12, 1, time.time())])
        from market_pulse import get_all_cached_pulse
        row = get_all_cached_pulse()["^FTSE"]
        for key in ("ticker", "name", "price", "change_pts", "change_pct", "is_positive", "is_stale"):
            self.assertIn(key, row, msg=f"missing key: {key}")

    def test_connection_closed_on_db_error(self):
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.execute.side_effect = sqlite3.OperationalError("boom")
        with patch("market_pulse.get_connection", return_value=mock_conn):
            from market_pulse import get_all_cached_pulse
            with self.assertRaises(sqlite3.OperationalError):
                get_all_cached_pulse()
        mock_conn.close.assert_called_once()


# ===========================================================================
# get_cached_pulse_from_db
# ===========================================================================

class TestGetCachedPulseFromDb(_PulseTestBase):

    def test_none_input_defaults_to_empty_list(self):
        from market_pulse import get_cached_pulse_from_db
        result = get_cached_pulse_from_db(None, 60)
        self.assertIn("indexes", result)
        self.assertIn("assets", result)

    def test_ticker_normalization_lowercase(self):
        """A lowercase ticker from the watchlist must match the DB and index dedup."""
        _seed_pulse(self._db_path, [("^FTSE", "UK FTSE 100", 8000.0, 0.0, 0.0, 1, time.time())])
        from market_pulse import get_cached_pulse_from_db
        # '^ftse' should be normalised to '^FTSE' and deduplicated against INDEX_TICKERS
        result = get_cached_pulse_from_db(["^ftse"], 60)
        tickers = [r["ticker"] for r in result["indexes"]]
        self.assertEqual(tickers.count("^FTSE"), 1)

    def test_ticker_normalization_whitespace(self):
        from market_pulse import get_cached_pulse_from_db
        result = get_cached_pulse_from_db(["  aapl  "], 60)
        asset_tickers = [r["ticker"] for r in result["assets"]]
        self.assertIn("AAPL", asset_tickers)
        self.assertNotIn("  aapl  ", asset_tickers)

    def test_duplicate_watchlist_entry_renders_once(self):
        from market_pulse import get_cached_pulse_from_db
        result = get_cached_pulse_from_db(["AAPL", "AAPL", "aapl"], 60)
        asset_tickers = [r["ticker"] for r in result["assets"]]
        self.assertEqual(asset_tickers.count("AAPL"), 1)

    def test_ignored_ticker_excluded(self):
        with patch(
            "market_pulse.load_config",
            return_value={"IGNORED_TICKERS": ["TSLA"], "UI_PREFERENCES": {"REFRESH_RATE": 60}},
        ):
            from market_pulse import get_cached_pulse_from_db
            result = get_cached_pulse_from_db(["TSLA", "AAPL"], 60)
        asset_tickers = [r["ticker"] for r in result["assets"]]
        self.assertNotIn("TSLA", asset_tickers)
        self.assertIn("AAPL", asset_tickers)

    def test_ignored_ticker_normalised_before_comparison(self):
        with patch(
            "market_pulse.load_config",
            return_value={"IGNORED_TICKERS": ["tsla "], "UI_PREFERENCES": {"REFRESH_RATE": 60}},
        ):
            from market_pulse import get_cached_pulse_from_db
            result = get_cached_pulse_from_db(["TSLA"], 60)
        asset_tickers = [r["ticker"] for r in result["assets"]]
        self.assertNotIn("TSLA", asset_tickers)

    def test_index_tickers_land_in_indexes(self):
        from market_pulse import get_cached_pulse_from_db
        result = get_cached_pulse_from_db([], 60)
        index_tickers = {r["ticker"] for r in result["indexes"]}
        self.assertIn("^FTSE", index_tickers)
        self.assertIn("^GSPC", index_tickers)

    def test_asset_tickers_land_in_assets(self):
        from market_pulse import get_cached_pulse_from_db
        result = get_cached_pulse_from_db(["AAPL", "NVDA"], 60)
        asset_tickers = {r["ticker"] for r in result["assets"]}
        self.assertIn("AAPL", asset_tickers)
        self.assertIn("NVDA", asset_tickers)

    def test_missing_from_db_defaults_to_zero_price_and_stale(self):
        from market_pulse import get_cached_pulse_from_db
        result = get_cached_pulse_from_db(["AAPL"], 60)
        aapl = next(r for r in result["assets"] if r["ticker"] == "AAPL")
        self.assertEqual(aapl["price"], 0.0)
        self.assertTrue(aapl["is_stale"])

    def test_fresh_db_row_not_stale(self):
        _seed_pulse(self._db_path, [("AAPL", "Apple", 190.0, 1.5, 0.8, 1, time.time())])
        from market_pulse import get_cached_pulse_from_db
        result = get_cached_pulse_from_db(["AAPL"], 60)
        aapl = next(r for r in result["assets"] if r["ticker"] == "AAPL")
        self.assertFalse(aapl["is_stale"])

    def test_old_db_row_is_stale(self):
        _seed_pulse(self._db_path, [("AAPL", "Apple", 190.0, 1.5, 0.8, 1, time.time() - 9999)])
        from market_pulse import get_cached_pulse_from_db
        result = get_cached_pulse_from_db(["AAPL"], 60)
        aapl = next(r for r in result["assets"] if r["ticker"] == "AAPL")
        self.assertTrue(aapl["is_stale"])

    def test_sentiment_score_merged(self):
        _seed_pulse(self._db_path, [("AAPL", "Apple", 190.0, 1.0, 0.5, 1, time.time())])
        _seed_signals(self._db_path, [("AAPL", "2024-01-15", 0.82)])
        from market_pulse import get_cached_pulse_from_db
        result = get_cached_pulse_from_db(["AAPL"], 60)
        aapl = next(r for r in result["assets"] if r["ticker"] == "AAPL")
        self.assertAlmostEqual(aapl["sentiment_score"], 0.82)

    def test_sentiment_score_none_when_absent(self):
        _seed_pulse(self._db_path, [("AAPL", "Apple", 190.0, 1.0, 0.5, 1, time.time())])
        from market_pulse import get_cached_pulse_from_db
        result = get_cached_pulse_from_db(["AAPL"], 60)
        aapl = next(r for r in result["assets"] if r["ticker"] == "AAPL")
        self.assertIsNone(aapl["sentiment_score"])

    def test_result_shape(self):
        from market_pulse import get_cached_pulse_from_db
        result = get_cached_pulse_from_db(["AAPL"], 60)
        for bucket in ("indexes", "assets"):
            for row in result[bucket]:
                for key in ("ticker", "name", "price", "change_pts", "change_pct",
                            "is_positive", "is_stale", "sentiment_score"):
                    self.assertIn(key, row, msg=f"[{bucket}] missing key: {key}")

    def test_connection_closed_on_db_error(self):
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.execute.side_effect = sqlite3.OperationalError("boom")
        with patch("market_pulse.get_connection", return_value=mock_conn):
            from market_pulse import get_cached_pulse_from_db
            with self.assertRaises(sqlite3.OperationalError):
                get_cached_pulse_from_db(["AAPL"], 60)
        mock_conn.close.assert_called_once()


# ===========================================================================
# fetch_and_save_pulse
# ===========================================================================

class TestFetchAndSavePulse(_PulseTestBase):

    def _no_yf(self):
        """Patch yfinance to return structurally valid all-NaN DataFrames (no network).

        Using _make_all_nan_df() rather than pd.DataFrame() means the per-ticker loop
        reaches the proper empty-after-dropna branch instead of raising a KeyError on
        the missing Close column, which would produce spurious [ERROR] log output.
        """
        return patch("market_pulse.yf.download", return_value=_make_all_nan_df())

    def _gilt_mock(self, live_yield=None):
        m = MagicMock()
        m.return_value.fetch_live_ft_yield.return_value = live_yield
        return patch("market_pulse.GiltDataService", m)

    # --- lock behaviour ---

    def test_second_call_skipped_while_lock_held(self):
        import market_pulse
        market_pulse._FETCH_LOCK.acquire()
        try:
            with self._no_yf() as mock_dl:
                market_pulse.fetch_and_save_pulse(["^FTSE"])
            mock_dl.assert_not_called()
        finally:
            market_pulse._FETCH_LOCK.release()

    def test_lock_released_after_normal_completion(self):
        import market_pulse
        with self._no_yf(), self._gilt_mock():
            market_pulse.fetch_and_save_pulse([])
        self.assertFalse(market_pulse._FETCH_LOCK.locked())

    def test_lock_released_after_exception(self):
        import market_pulse
        with patch("market_pulse.get_connection", side_effect=RuntimeError("db down")):
            with self._no_yf():
                with self.assertLogs("market_pulse", level="ERROR"):
                    market_pulse.fetch_and_save_pulse(["^FTSE"])
        self.assertFalse(market_pulse._FETCH_LOCK.locked())

    # --- UK10YG routing ---

    def test_gilt_ticker_stripped_from_yfinance_payload(self):
        import market_pulse
        captured = []

        def fake_download(tickers, **kwargs):
            captured.append(list(tickers))
            return _make_all_nan_df()

        with patch("market_pulse.yf.download", side_effect=fake_download):
            with self._gilt_mock(live_yield=4.5):
                market_pulse.fetch_and_save_pulse(["^FTSE", "UK10YG"])

        for call_args in captured:
            self.assertNotIn("UK10YG", call_args)

    # --- empty yfinance data writes last_updated = 0 ---

    def test_empty_data_stamps_last_updated_zero_on_existing_row(self):
        _seed_pulse(self._db_path, [("^FTSE", "UK FTSE 100", 8000.0, 0.0, 0.0, 1, time.time())])
        import market_pulse
        nan_df = _make_all_nan_df()
        with patch("market_pulse.yf.download", return_value=nan_df):
            market_pulse.fetch_and_save_pulse(["^FTSE"])
        row = _read_pulse(self._db_path, "^FTSE")
        self.assertEqual(row["last_updated"], 0)

    def test_empty_data_inserts_new_row_with_last_updated_zero(self):
        import market_pulse
        nan_df = _make_all_nan_df()
        with patch("market_pulse.yf.download", return_value=nan_df):
            market_pulse.fetch_and_save_pulse(["^FTSE"])
        row = _read_pulse(self._db_path, "^FTSE")
        self.assertIsNotNone(row)
        self.assertEqual(row["last_updated"], 0)
        self.assertEqual(row["price"], 0.0)

    # --- successful fetch writes correct values ---

    def test_single_ticker_successful_fetch(self):
        import market_pulse
        daily = _make_flat_df([100.0, 102.0, 104.0])
        live  = _make_flat_df([105.0])

        call_count = [0]
        def fake_download(tickers, **kwargs):
            call_count[0] += 1
            return daily if call_count[0] == 1 else live

        with patch("market_pulse.yf.download", side_effect=fake_download):
            market_pulse.fetch_and_save_pulse(["^FTSE"])

        row = _read_pulse(self._db_path, "^FTSE")
        self.assertAlmostEqual(row["price"], 105.0)
        # daily[-1] date == live date, so prev_close = daily.iloc[-2] = 102.0
        self.assertAlmostEqual(row["change_pts"], 105.0 - 102.0, places=4)
        self.assertGreater(row["last_updated"], 0)

    def test_successful_fetch_sets_is_positive(self):
        import market_pulse
        daily = _make_flat_df([100.0, 100.0])
        live_up   = _make_flat_df([101.0])
        live_down = _make_flat_df([99.0])

        calls = [0]
        def fake_up(tickers, **kwargs):
            calls[0] += 1
            return daily if calls[0] == 1 else live_up

        with patch("market_pulse.yf.download", side_effect=fake_up):
            market_pulse.fetch_and_save_pulse(["^FTSE"])
        self.assertEqual(_read_pulse(self._db_path, "^FTSE")["is_positive"], 1)

        market_pulse._FETCH_LOCK = threading.Lock()
        calls[0] = 0

        def fake_down(tickers, **kwargs):
            calls[0] += 1
            return daily if calls[0] == 1 else live_down

        with patch("market_pulse.yf.download", side_effect=fake_down):
            market_pulse.fetch_and_save_pulse(["^FTSE"])
        self.assertEqual(_read_pulse(self._db_path, "^FTSE")["is_positive"], 0)

    # --- gilt path ---

    def test_gilt_live_yield_written_to_db(self):
        import market_pulse
        with self._no_yf(), self._gilt_mock(live_yield=4.25):
            market_pulse.fetch_and_save_pulse(["UK10YG"])
        row = _read_pulse(self._db_path, "UK10YG")
        self.assertAlmostEqual(row["price"], 4.25)
        self.assertGreater(row["last_updated"], 0)

    def test_gilt_failure_stamps_last_updated_zero(self):
        _seed_pulse(self._db_path, [("UK10YG", "UK 10Y Gilt", 4.1, 0.0, 0.0, 1, time.time())])
        import market_pulse
        with self._no_yf(), self._gilt_mock(live_yield=None):
            with patch("market_pulse.HISTORICAL_DIR", Path(tempfile.mkdtemp())):
                market_pulse.fetch_and_save_pulse(["UK10YG"])
        row = _read_pulse(self._db_path, "UK10YG")
        self.assertEqual(row["last_updated"], 0)

    def test_gilt_parquet_fallback_used_when_live_none(self):
        import market_pulse
        tmp_dir = Path(tempfile.mkdtemp())
        parquet_path = tmp_dir / "UK_GILT_BASELINE.parquet"
        df = _make_flat_df([4.1, 4.2])
        df.to_parquet(parquet_path)

        with self._no_yf(), self._gilt_mock(live_yield=None):
            with patch("market_pulse.HISTORICAL_DIR", tmp_dir):
                market_pulse.fetch_and_save_pulse(["UK10YG"])

        row = _read_pulse(self._db_path, "UK10YG")
        # Fallback should use the last Close value (4.2), not stamp 0
        self.assertAlmostEqual(row["price"], 4.2)
        self.assertGreater(row["last_updated"], 0)

    # --- connection hygiene ---

    def test_connection_closed_after_successful_run(self):
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = MagicMock()
        with patch("market_pulse.get_connection", return_value=mock_conn):
            with self._no_yf():
                import market_pulse
                market_pulse.fetch_and_save_pulse([])
        mock_conn.close.assert_called_once()

    def test_connection_closed_after_exception_in_loop(self):
        import market_pulse
        daily = _make_flat_df([100.0, 102.0])
        live  = _make_flat_df([103.0])

        calls = [0]
        def fake_download(tickers, **kwargs):
            calls[0] += 1
            return daily if calls[0] == 1 else live

        # Corrupt one ticker so the per-ticker try/except fires, but the outer run still completes
        bad_daily = MagicMock()
        bad_daily.empty = False
        bad_daily.columns = MagicMock(spec=pd.MultiIndex)
        bad_daily.columns.__class__ = pd.MultiIndex

        mock_conn = MagicMock()
        mock_conn.cursor.return_value = MagicMock()
        mock_conn.commit.side_effect = sqlite3.OperationalError("locked")

        with patch("market_pulse.get_connection", return_value=mock_conn):
            with self._no_yf():
                with self.assertLogs("market_pulse", level="ERROR"):
                    market_pulse.fetch_and_save_pulse(["^FTSE"])

        mock_conn.close.assert_called_once()


# ===========================================================================

if __name__ == "__main__":
    import logging
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
    )
    unittest.main(verbosity=2)
