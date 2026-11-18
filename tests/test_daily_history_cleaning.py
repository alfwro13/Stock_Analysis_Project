"""Unit-flip rescale and listing-break trim of the daily-history writer, and the one-time cleanup of stored bars.

Series shapes follow bars seen on prod: Yahoo quoting a pence ETF's flat zero-volume bars in pounds (UIGB.L, JRDZ.L, GGOV.L),
and a reissued symbol (BCE.L) whose earlier company's bars precede a 1,000x jump.
"""
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

import daily_history_writer as writer
from db import schema as db_schema
from db.database import get_connection
from price_repair_engine import clean_stored_bar_artifacts

pytestmark = pytest.mark.restore_tables("quant_signals", "data_migrations")


def _bars(closes, volume=0.0, start="2026-01-05"):
    closes = np.asarray(closes, float)
    index = pd.bdate_range(start, periods=len(closes))
    volumes = np.broadcast_to(np.asarray(volume, float), closes.shape)
    return pd.DataFrame(
        {"Open": closes, "High": closes, "Low": closes, "Close": closes, "Volume": volumes}, index=index,
    )


def _pence(n, base=2300.0):
    return [base + i for i in range(n)]


class TestFixUnitFlips:
    def test_rescales_a_flipped_run_between_two_correct_runs(self):
        df = _bars(_pence(30) + [23.3, 23.4, 23.5] + _pence(30, 2330.0))
        fixed = writer.fix_unit_flips("UIGB.L", df)
        assert fixed["Close"].between(2300, 2400).all()
        assert fixed["Close"].iloc[30:33].tolist() == pytest.approx([2330.0, 2340.0, 2350.0])
        assert df["Close"].iloc[30] == pytest.approx(23.3)

    def test_scales_open_high_low_with_close_and_leaves_volume(self):
        df = _bars(_pence(30) + [23.3] + _pence(30, 2330.0), volume=7.0)
        df.loc[df.index[30], ["Open", "High", "Low"]] = [23.2, 23.5, 23.1]
        fixed = writer.fix_unit_flips("UIGB.L", df)
        assert fixed.iloc[30][["Open", "High", "Low", "Close"]].tolist() == pytest.approx([2320.0, 2350.0, 2310.0, 2330.0])
        assert (fixed["Volume"] == 7.0).all()

    def test_pound_runs_are_scaled_up_when_pence_is_the_unit_of_the_latest_run(self):
        df = _bars([23.0 + i / 10 for i in range(30)] + _pence(5) + [23.3] * 3 + _pence(25))
        fixed = writer.fix_unit_flips("LIVE.L", df)
        assert fixed["Close"].between(2200, 2600).all()
        assert fixed["Close"].iloc[0] == pytest.approx(2300.0)

    def test_trailing_short_run_of_a_series_that_flipped_both_ways_follows_the_majority(self):
        df = _bars(_pence(20) + [23.0] * 3 + _pence(20, 2320.0) + [23.5, 23.6])
        fixed = writer.fix_unit_flips("JRDZ.L", df)
        assert fixed["Close"].iloc[-2:].tolist() == pytest.approx([2350.0, 2360.0])
        assert fixed["Close"].between(2200, 2400).all()

    def test_history_follows_the_unit_of_a_long_latest_run(self):
        df = _bars([11.0 + i / 100 for i in range(100)] + _pence(100, 1100.0))
        fixed = writer.fix_unit_flips("ICLA.L", df)
        assert fixed["Close"].iloc[0] == pytest.approx(1100.0)
        assert fixed["Close"].iloc[-1] == pytest.approx(1199.0)

    def test_a_short_final_run_after_a_one_way_step_is_left_alone(self):
        df = _bars(_pence(60) + [23.3] * 3)
        assert writer.fix_unit_flips("NEW.L", df) is df

    def test_only_lse_tickers_are_touched(self):
        df = _bars(_pence(30) + [23.3] + _pence(30, 2330.0))
        assert writer.fix_unit_flips("AAPL", df) is df
        assert writer.fix_unit_flips("^FTSE", df) is df

    def test_steps_outside_the_flip_range_are_left_alone(self):
        for ratio in (50.0, 562.0, 1 / 562.0):
            df = _bars(_pence(30, 20.0) + [49.0 * ratio] * 30)
            assert writer.fix_unit_flips("3BSR.L", df) is df

    def test_short_and_degenerate_frames_pass_through(self):
        two_bars = _bars([2300.0, 23.0])
        assert writer.fix_unit_flips("UIGB.L", two_bars) is two_bars
        df = _bars([2300.0, 0.0, 2310.0, 2320.0])
        assert writer.fix_unit_flips("UIGB.L", df) is df


class TestTrimListingBreak:
    def _listing(self, zero_bars=10):
        old_company = _bars([0.004] * 30, volume=1_000_000.0)
        stale = _bars([0.004] * zero_bars, volume=0.0, start="2026-02-16")
        listed = _bars([4.15 + i / 100 for i in range(20)], volume=1_300_000.0, start="2026-03-02")
        return pd.concat([old_company, stale, listed]), len(old_company) + zero_bars

    def test_drops_bars_before_a_jump_that_follows_stale_zero_volume_prints(self):
        df, first = self._listing()
        trimmed = writer.trim_listing_break(df)
        assert len(trimmed) == len(df) - first
        assert trimmed["Close"].iloc[0] == pytest.approx(4.15)

    def test_a_jump_after_only_a_few_zero_volume_bars_is_kept(self):
        df, _ = self._listing(zero_bars=writer.LISTING_BREAK_MIN_ZERO_VOLUME_BARS - 1)
        assert writer.trim_listing_break(df) is df

    def test_a_jump_inside_the_unit_flip_range_is_not_a_listing_break(self):
        df = pd.concat([_bars([40.0] * 10, volume=0.0), _bars([4000.0] * 10, volume=500.0, start="2026-02-02")])
        assert writer.trim_listing_break(df) is df

    def test_stale_prints_without_a_jump_are_kept(self):
        df = _bars([0.0024] * 40, volume=0.0)
        assert writer.trim_listing_break(df) is df

    def test_the_last_break_wins(self):
        first, _ = self._listing()
        second = _bars([900.0 + i for i in range(10)], volume=0.0, start="2026-04-06")
        third = _bars([0.9 + i / 100 for i in range(10)], volume=800.0, start="2026-04-20")
        trimmed = writer.trim_listing_break(pd.concat([first, second, third]))
        assert trimmed["Close"].iloc[0] == pytest.approx(0.9)

    def test_fx_frames_with_no_volume_are_never_trimmed(self):
        df = _bars(list(np.linspace(1.2, 1.3, 40)), volume=0.0)
        assert writer.trim_listing_break(df) is df


class TestWriterPaths:
    def test_prepare_daily_history_runs_both_cleaning_steps(self):
        df, _ = TestTrimListingBreak()._listing()
        flipped = _bars(_pence(30) + [23.3, 23.4] + _pence(30, 2330.0), start="2026-05-04")
        assert writer.prepare_daily_history("UIGB.L", flipped, None)["Close"].between(2300, 2400).all()
        prepared = writer.prepare_daily_history("BCE.L", df, None)
        assert prepared["Close"].iloc[0] == pytest.approx(4.15)

    def test_a_stored_history_merged_with_a_short_answer_does_not_bring_the_old_company_back(self, tmp_path):
        df, first = TestTrimListingBreak()._listing()
        path = tmp_path / "BCE.L.parquet"
        df.to_parquet(path)
        answer = df.iloc[-5:]
        written = writer.write_daily_history(answer, path)
        assert len(written) == len(df) - first
        assert len(pd.read_parquet(path)) == len(df) - first


REBUILT_DATE = str(pd.bdate_range("2026-01-05", periods=320)[300].date())


def _seed_quant_row(ticker, date=REBUILT_DATE):
    conn = None
    try:
        conn = get_connection()
        conn.execute("INSERT OR REPLACE INTO quant_signals (ticker, date, close_price) VALUES (?, ?, ?)", (ticker, date, 1.0))
        conn.commit()
    finally:
        if conn:
            conn.close()


def _quant_rows(ticker):
    conn = None
    try:
        conn = get_connection()
        return [tuple(r) for r in conn.execute("SELECT date, close_price FROM quant_signals WHERE ticker = ? ORDER BY date", (ticker,))]
    finally:
        if conn:
            conn.close()


def _long_series(n=320, base=2300.0):
    rng = np.random.default_rng(7)
    return list(base * np.exp(np.cumsum(rng.normal(0, 0.004, n))))


class TestStoredBarCleanup:
    def _folder(self, tmp_path):
        spy = _bars([500.0 + i / 4 for i in range(320)], volume=1_000_000.0)
        spy.to_parquet(tmp_path / "SPY.parquet")
        closes = _long_series()
        flipped = list(closes)
        for i in range(100, 104):
            flipped[i] = closes[i] / 100
        _bars(flipped, volume=0.0).to_parquet(tmp_path / "UIGB.L.parquet")
        listing, _ = TestTrimListingBreak()._listing()
        listing.to_parquet(tmp_path / "BCE.L.parquet")
        _bars(flipped, volume=0.0).to_parquet(tmp_path / "SP500_BASELINE.parquet")
        pd.DataFrame({"Close": [1.0, 2.0, 3.0]}).to_parquet(tmp_path / "SPY_hmm.parquet")
        _bars(flipped, volume=10.0).to_parquet(tmp_path / "AAPL.parquet")
        return closes

    def test_rewrites_flipped_and_listing_files_and_leaves_the_rest(self, tmp_path):
        closes = self._folder(tmp_path)
        before_baseline = pd.read_parquet(tmp_path / "SP500_BASELINE.parquet")
        before_us = pd.read_parquet(tmp_path / "AAPL.parquet")
        for ticker in ("UIGB.L", "BCE.L"):
            _seed_quant_row(ticker)

        assert clean_stored_bar_artifacts(tmp_path) == 2

        fixed = pd.read_parquet(tmp_path / "UIGB.L.parquet")
        assert fixed["Close"].tolist() == pytest.approx(closes)
        assert len(pd.read_parquet(tmp_path / "BCE.L.parquet")) == 20
        pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "SP500_BASELINE.parquet"), before_baseline)
        pd.testing.assert_frame_equal(pd.read_parquet(tmp_path / "AAPL.parquet"), before_us)

    def test_rebuilds_quant_rows_of_a_changed_ticker_and_clears_a_trimmed_one(self, tmp_path):
        closes = self._folder(tmp_path)
        for ticker in ("UIGB.L", "BCE.L"):
            _seed_quant_row(ticker)

        clean_stored_bar_artifacts(tmp_path)

        by_date = {str(d.date()): c for d, c in zip(pd.bdate_range("2026-01-05", periods=len(closes)), closes)}
        rebuilt = _quant_rows("UIGB.L")
        assert len(rebuilt) > 50
        assert all(close == pytest.approx(by_date[date]) for date, close in rebuilt)
        assert _quant_rows("BCE.L") == []

    def test_a_changed_ticker_without_quant_rows_gets_none(self, tmp_path):
        self._folder(tmp_path)
        clean_stored_bar_artifacts(tmp_path)
        assert _quant_rows("UIGB.L") == []

    def test_without_a_spy_file_the_bars_are_still_fixed_and_nothing_is_fetched(self, tmp_path):
        self._folder(tmp_path)
        (tmp_path / "SPY.parquet").unlink()
        _seed_quant_row("UIGB.L")
        with patch("ml_backfill_engine.download_spy_benchmark") as download:
            assert clean_stored_bar_artifacts(tmp_path) == 2
        download.assert_not_called()
        assert _quant_rows("UIGB.L") == [(REBUILT_DATE, 1.0)]

    def test_a_second_pass_changes_nothing(self, tmp_path):
        self._folder(tmp_path)
        clean_stored_bar_artifacts(tmp_path)
        assert clean_stored_bar_artifacts(tmp_path) == 0

    def test_a_missing_folder_is_a_no_op(self, tmp_path):
        assert clean_stored_bar_artifacts(tmp_path / "absent") == 0


class TestMigrationRunsOnce:
    def test_migrate_db_cleans_stored_bars_once(self, tmp_path):
        conn = get_connection()
        try:
            conn.execute("DELETE FROM data_migrations WHERE name = 'stored_bar_artifacts'")
            conn.commit()
            flipped = _long_series()
            flipped[50] = flipped[50] / 100
            _bars(flipped).to_parquet(tmp_path / "UIGB.L.parquet")
            with patch("db.schema.HISTORICAL_DIR", tmp_path):
                db_schema.migrate_db(conn, conn.cursor())
                assert pd.read_parquet(tmp_path / "UIGB.L.parquet")["Close"].tolist() == pytest.approx(_long_series())
                assert conn.execute("SELECT COUNT(*) FROM data_migrations WHERE name = 'stored_bar_artifacts'").fetchone()[0] == 1

                _bars(flipped).to_parquet(tmp_path / "UIGB.L.parquet")
                db_schema.migrate_db(conn, conn.cursor())
                assert pd.read_parquet(tmp_path / "UIGB.L.parquet")["Close"].iloc[50] == pytest.approx(flipped[50])
        finally:
            conn.close()

    def test_a_failing_cleanup_leaves_no_marker_so_the_next_start_retries(self, tmp_path):
        conn = get_connection()
        try:
            conn.execute("DELETE FROM data_migrations WHERE name = 'stored_bar_artifacts'")
            conn.commit()
            with patch("db.schema.HISTORICAL_DIR", tmp_path), patch("price_repair_engine.clean_stored_bar_artifacts", side_effect=RuntimeError("boom")):
                db_schema.migrate_db(conn, conn.cursor())
            assert conn.execute("SELECT COUNT(*) FROM data_migrations WHERE name = 'stored_bar_artifacts'").fetchone()[0] == 0
        finally:
            conn.close()
