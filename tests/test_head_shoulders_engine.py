"""
tests/test_head_shoulders_engine.py — Head & Shoulders Pattern Detector Tests
"""

import sys
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
from indicators import compute_rsi, compute_volume_sma
from head_shoulders_engine import (
    HeadShouldersEngine,
    _detect_and_build,
    _latest_candidate_extrema,
    _rw_top,
    _rw_bottom,
    _volume_confirms,
    _rsi_divergence,
    fill_pattern_outcomes,
    phase_label,
)

_CFG = {
    "SCHEDULING": {"HEAD_SHOULDERS": {}},
    "NOTIFICATIONS": {"HEAD_SHOULDERS_ALERTS": {}},
}


def _make_regular_df(confirmed: bool = False) -> pd.DataFrame:
    """Regular (topping) H&S: prior uptrend -> l_shoulder(25,110) -> l_armpit(35,95) ->
    head(50,120) -> r_armpit(60,96) -> r_shoulder(75,108) -> optional breakdown."""
    seg1 = np.linspace(90, 110, 26)
    seg2 = np.linspace(110, 95, 11)[1:]
    seg3 = np.linspace(95, 120, 16)[1:]
    seg4 = np.linspace(120, 96, 11)[1:]
    seg5 = np.linspace(96, 108, 16)[1:]
    parts = [seg1, seg2, seg3, seg4, seg5]
    if confirmed:
        parts.append(np.linspace(108, 90, 11)[1:])
    prices = np.concatenate(parts)
    volume = np.linspace(2_000_000, 800_000, len(prices))
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    })


def _make_inverse_df(confirmed: bool = False) -> pd.DataFrame:
    """Inverse (bottoming) H&S: prior downtrend -> l_shoulder(25,90) -> l_armpit(35,105) ->
    head(50,80) -> r_armpit(60,104) -> r_shoulder(75,92) -> optional breakout."""
    seg1 = np.linspace(110, 90, 26)
    seg2 = np.linspace(90, 105, 11)[1:]
    seg3 = np.linspace(105, 80, 16)[1:]
    seg4 = np.linspace(80, 104, 11)[1:]
    seg5 = np.linspace(104, 92, 16)[1:]
    parts = [seg1, seg2, seg3, seg4, seg5]
    if confirmed:
        parts.append(np.linspace(92, 110, 11)[1:])
    prices = np.concatenate(parts)
    volume = np.linspace(2_000_000, 800_000, len(prices))
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    })


def _make_no_trend_df() -> pd.DataFrame:
    """Same H&S geometry but with no prior directional move — sideways oscillation before the
    left shoulder — must be rejected by the prior-trend gate. l_shoulder is still a valid local
    top (spikes just above the oscillation band), but the 20-bar lookback shows no real trend."""
    seg0 = 108.0 + np.sin(np.linspace(0, 4 * np.pi, 25)) * 0.5
    l_shoulder_bump = np.array([110.0])
    seg2 = np.linspace(110, 95, 11)[1:]
    seg3 = np.linspace(95, 120, 16)[1:]
    seg4 = np.linspace(120, 96, 11)[1:]
    seg5 = np.linspace(96, 108, 16)[1:]
    prices = np.concatenate([seg0, l_shoulder_bump, seg2, seg3, seg4, seg5])
    volume = np.full(len(prices), 1_000_000.0)
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    })


def _make_unbalanced_df() -> pd.DataFrame:
    """H&S geometry where the right shoulder/armpit sit far below the left side's midpoint —
    badly unbalanced, must be rejected by the balance rule."""
    seg1 = np.linspace(90, 110, 26)
    seg2 = np.linspace(110, 95, 11)[1:]
    seg3 = np.linspace(95, 120, 16)[1:]
    seg4 = np.linspace(120, 40, 11)[1:]  # right armpit collapses to 40, not 96
    seg5 = np.linspace(40, 45, 16)[1:]   # right shoulder barely recovers to 45
    prices = np.concatenate([seg1, seg2, seg3, seg4, seg5])
    volume = np.full(len(prices), 1_000_000.0)
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5,
        "Close": prices, "Volume": volume,
    })


def _with_datetime_index(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df.index = pd.date_range("2024-01-01", periods=len(df), freq="B")
    return df


def _detect(df: pd.DataFrame, inverted: bool, prior_trend_min_pct: float = 8.0, volume_confirm_multiplier: float = 1.5):
    close = df["Close"].to_numpy()
    volume = df["Volume"].to_numpy()
    rsi_series = compute_rsi(df["Close"])
    vol_sma = compute_volume_sma(df["Volume"])
    return _detect_and_build(close, volume, rsi_series, vol_sma, inverted, prior_trend_min_pct, volume_confirm_multiplier)


class TestRwExtrema:
    def test_rw_top_detects_peak(self):
        data = np.concatenate([np.linspace(0, 10, 10), np.linspace(10, 0, 10)])
        assert _rw_top(data, 14, 5) is True

    def test_rw_bottom_detects_trough(self):
        data = np.concatenate([np.linspace(10, 0, 10), np.linspace(0, 10, 10)])
        assert _rw_bottom(data, 14, 5) is True

    def test_rw_top_false_on_monotonic_series(self):
        data = np.linspace(0, 10, 20)
        assert not any(_rw_top(data, i, 5) for i in range(len(data)))

    def test_too_early_index_returns_false(self):
        data = np.linspace(0, 10, 20)
        assert _rw_top(data, 3, 5) is False
        assert _rw_bottom(data, 3, 5) is False


class TestLatestCandidateExtrema:
    def test_finds_regular_pattern_points(self):
        df = _make_regular_df(confirmed=False)
        extrema = _latest_candidate_extrema(df["Close"].to_numpy(), 5, inverted=False)
        assert extrema == [25, 35, 50, 60]

    def test_finds_inverse_pattern_points(self):
        df = _make_inverse_df(confirmed=False)
        extrema = _latest_candidate_extrema(df["Close"].to_numpy(), 5, inverted=True)
        assert extrema == [25, 35, 50, 60]

    def test_returns_none_with_too_few_extrema(self):
        data = np.linspace(0, 10, 20)
        assert _latest_candidate_extrema(data, 5, inverted=False) is None


class TestDetectAndBuildRegular:
    def test_forming_phase_when_above_neckline(self):
        result = _detect(_make_regular_df(confirmed=False), inverted=False)
        assert result is not None
        assert result["pattern_type"] == "regular"
        assert result["phase"] == "FORMING"
        assert result["breakout_idx"] is None

    def test_confirmed_phase_when_below_neckline(self):
        result = _detect(_make_regular_df(confirmed=True), inverted=False)
        assert result is not None
        assert result["phase"] == "CONFIRMED"
        assert result["breakout_idx"] is not None
        assert result["breakout_price"] is not None

    def test_measured_target_below_neckline_for_regular(self):
        result = _detect(_make_regular_df(confirmed=True), inverted=False)
        assert result["measured_target"] < result["neck_value"]

    def test_head_taller_than_shoulders(self):
        result = _detect(_make_regular_df(confirmed=False), inverted=False)
        assert result["head_price"] > result["l_shoulder_price"]
        assert result["head_price"] > result["r_shoulder_price"]

    def test_rejects_when_no_prior_trend(self):
        result = _detect(_make_no_trend_df(), inverted=False)
        assert result is None

    def test_rejects_when_unbalanced(self):
        result = _detect(_make_unbalanced_df(), inverted=False)
        assert result is None

    def test_pattern_r2_present_and_bounded(self):
        result = _detect(_make_regular_df(confirmed=False), inverted=False)
        assert result["pattern_r2"] is not None
        assert result["pattern_r2"] <= 1.0


class TestDetectAndBuildInverse:
    def test_forming_phase_when_below_neckline(self):
        result = _detect(_make_inverse_df(confirmed=False), inverted=True)
        assert result is not None
        assert result["pattern_type"] == "inverse"
        assert result["phase"] == "FORMING"

    def test_confirmed_phase_when_above_neckline(self):
        result = _detect(_make_inverse_df(confirmed=True), inverted=True)
        assert result is not None
        assert result["phase"] == "CONFIRMED"

    def test_measured_target_above_neckline_for_inverse(self):
        result = _detect(_make_inverse_df(confirmed=True), inverted=True)
        assert result["measured_target"] > result["neck_value"]

    def test_head_lower_than_shoulders(self):
        result = _detect(_make_inverse_df(confirmed=False), inverted=True)
        assert result["head_price"] < result["l_shoulder_price"]
        assert result["head_price"] < result["r_shoulder_price"]

    def test_regular_shape_not_detected_as_inverse(self):
        result = _detect(_make_regular_df(confirmed=False), inverted=True)
        assert result is None


class TestVolumeConfirms:
    def test_declining_volume_confirms_forming(self):
        volume = np.array([100.0] * 60 + [50.0] * 20)
        vol_sma = pd.Series(np.full(80, 90.0))
        assert _volume_confirms(volume, vol_sma, l_shoulder=5, r_shoulder=70, today_idx=79, confirmed=False, multiplier=1.5) is True

    def test_rising_volume_does_not_confirm_forming(self):
        volume = np.array([50.0] * 60 + [100.0] * 20)
        vol_sma = pd.Series(np.full(80, 90.0))
        assert _volume_confirms(volume, vol_sma, l_shoulder=5, r_shoulder=70, today_idx=79, confirmed=False, multiplier=1.5) is False

    def test_confirmed_requires_breakout_surge(self):
        volume = np.array([100.0] * 60 + [50.0] * 19 + [500.0])
        vol_sma = pd.Series(np.full(80, 90.0))
        assert _volume_confirms(volume, vol_sma, l_shoulder=5, r_shoulder=70, today_idx=79, confirmed=True, multiplier=1.5) is True

    def test_confirmed_without_surge_fails(self):
        volume = np.array([100.0] * 60 + [50.0] * 20)
        vol_sma = pd.Series(np.full(80, 90.0))
        assert _volume_confirms(volume, vol_sma, l_shoulder=5, r_shoulder=70, today_idx=79, confirmed=True, multiplier=1.5) is False


class TestRsiDivergence:
    def test_bearish_divergence_for_regular(self):
        rsi = pd.Series([70.0] * 100)
        rsi.iloc[10] = 60.0
        rsi.iloc[50] = 55.0
        assert _rsi_divergence(rsi, l_shoulder=10, head=50, inverted=False) is True

    def test_no_divergence_for_regular(self):
        rsi = pd.Series([70.0] * 100)
        rsi.iloc[10] = 50.0
        rsi.iloc[50] = 60.0
        assert _rsi_divergence(rsi, l_shoulder=10, head=50, inverted=False) is False

    def test_bullish_divergence_for_inverse(self):
        rsi = pd.Series([30.0] * 100)
        rsi.iloc[10] = 30.0
        rsi.iloc[50] = 35.0
        assert _rsi_divergence(rsi, l_shoulder=10, head=50, inverted=True) is True

    def test_nan_returns_false(self):
        rsi = pd.Series([np.nan] * 100)
        assert _rsi_divergence(rsi, l_shoulder=10, head=50, inverted=False) is False


class TestAnalyseTicker:
    def setup_method(self):
        self.engine = HeadShouldersEngine(_CFG)

    def test_returns_none_for_insufficient_data(self):
        df = _with_datetime_index(_make_regular_df(confirmed=False).head(30))
        assert self.engine._analyse_ticker("TEST", df) is None

    def test_result_has_required_keys(self):
        result = self.engine._analyse_ticker("FAKE", _with_datetime_index(_make_regular_df(confirmed=False)))
        assert result is not None
        for key in (
            "ticker", "pattern_type", "phase", "l_shoulder_date", "l_shoulder_price",
            "l_armpit_date", "head_date", "r_armpit_date", "r_shoulder_date",
            "neck_slope", "measured_target", "volume_confirms", "rsi_divergence",
            "pattern_r2", "prior_trend_pct", "scan_ts",
        ):
            assert key in result, f"Missing key in result: {key}"

    def test_ticker_and_pattern_type_preserved(self):
        result = self.engine._analyse_ticker("NVDA", _with_datetime_index(_make_regular_df(confirmed=True)))
        assert result["ticker"] == "NVDA"
        assert result["pattern_type"] == "regular"
        assert result["phase"] == "CONFIRMED"

    def test_dates_are_strings_not_indices(self):
        result = self.engine._analyse_ticker("FAKE", _with_datetime_index(_make_regular_df(confirmed=False)))
        assert isinstance(result["l_shoulder_date"], str)
        assert "l_shoulder_idx" not in result


class TestSaveResults:
    def setup_method(self):
        self.engine = HeadShouldersEngine(_CFG)

    @staticmethod
    def _row(ticker: str, phase: str = "CONFIRMED", pattern_type: str = "regular") -> dict:
        return {
            "ticker": ticker, "pattern_type": pattern_type, "phase": phase,
            "l_shoulder_date": "2026-01-01", "l_shoulder_price": 110.0,
            "l_armpit_date": "2026-01-10", "l_armpit_price": 95.0,
            "head_date": "2026-01-20", "head_price": 120.0,
            "r_armpit_date": "2026-01-30", "r_armpit_price": 96.0,
            "r_shoulder_date": "2026-02-05", "r_shoulder_price": 108.0,
            "neck_slope": 0.04, "neck_value": 97.0,
            "breakout_date": "2026-02-10" if phase == "CONFIRMED" else None,
            "breakout_price": 90.0 if phase == "CONFIRMED" else None,
            "measured_target": 74.0, "volume_confirms": True, "rsi_divergence": True,
            "pattern_r2": 0.85, "prior_trend_pct": 12.0, "close_price": 90.0,
            "scan_ts": "2026-02-10 22:20:00",
        }

    def test_row_readable_after_save(self):
        self.engine._save_results([self._row("HSTST1")])
        conn = db.get_connection()
        try:
            saved = conn.execute("SELECT * FROM head_shoulders_results WHERE ticker = 'HSTST1'").fetchone()
        finally:
            conn.execute("DELETE FROM head_shoulders_results WHERE ticker = 'HSTST1'")
            conn.execute("DELETE FROM head_shoulders_history WHERE ticker = 'HSTST1'")
            conn.commit()
            conn.close()
        assert saved is not None
        assert saved["phase"] == "CONFIRMED"
        assert abs(saved["measured_target"] - 74.0) < 1e-6

    def test_upsert_overwrites_existing_row(self):
        self.engine._save_results([self._row("HSTST2", phase="FORMING")])
        self.engine._save_results([self._row("HSTST2", phase="CONFIRMED")])
        conn = db.get_connection()
        try:
            rows = conn.execute("SELECT phase FROM head_shoulders_results WHERE ticker = 'HSTST2'").fetchall()
        finally:
            conn.execute("DELETE FROM head_shoulders_results WHERE ticker = 'HSTST2'")
            conn.execute("DELETE FROM head_shoulders_history WHERE ticker = 'HSTST2'")
            conn.commit()
            conn.close()
        assert len(rows) == 1
        assert rows[0]["phase"] == "CONFIRMED"

    def test_save_results_populates_history(self):
        self.engine._save_results([self._row("HSTST3")])
        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT measured_target, volume_confirms, rsi_divergence, pattern_r2, prior_trend_pct "
                "FROM head_shoulders_history WHERE ticker = 'HSTST3'"
            ).fetchone()
        finally:
            conn.execute("DELETE FROM head_shoulders_results WHERE ticker = 'HSTST3'")
            conn.execute("DELETE FROM head_shoulders_history WHERE ticker = 'HSTST3'")
            conn.commit()
            conn.close()
        assert row is not None
        assert abs(row["measured_target"] - 74.0) < 1e-6
        assert row["volume_confirms"] == 1
        assert row["rsi_divergence"] == 1


class TestGetTickerList:
    def test_tbill_ticker_excluded_from_portfolio_scope(self):
        engine = HeadShouldersEngine(_CFG)
        engine.monitor_portfolio = True
        holdings = {"AAPL": {}, "TBILL-606": {}}
        with patch("accounts_engine.get_combined_holdings", return_value=holdings):
            tickers = engine._get_ticker_list()
        assert "AAPL" in tickers
        assert not any(t.startswith("TBILL-") for t in tickers)

    def test_watchlist_excluded_by_default(self):
        engine = HeadShouldersEngine(_CFG)
        engine.monitor_portfolio = False
        assert engine.monitor_watchlist is False
        with patch("database.get_watchlist_tickers", return_value=["NVDA"]) as mock_wl:
            tickers = engine._get_ticker_list()
        mock_wl.assert_not_called()
        assert "NVDA" not in tickers

    def test_watchlist_included_when_enabled(self):
        engine = HeadShouldersEngine(_CFG)
        engine.monitor_portfolio = False
        engine.monitor_watchlist = True
        with patch("database.get_watchlist_tickers", return_value=["nvda"]):
            tickers = engine._get_ticker_list()
        assert "NVDA" in tickers


class TestPhaseLabel:
    def test_regular_forming(self):
        assert phase_label("regular", "FORMING") == "Head & Shoulders (Forming)"

    def test_regular_confirmed(self):
        assert phase_label("regular", "CONFIRMED") == "Head & Shoulders (Confirmed)"

    def test_inverse_confirmed(self):
        assert phase_label("inverse", "CONFIRMED") == "Inverse Head & Shoulders (Confirmed)"

    def test_none_pattern_falls_back_to_phase(self):
        assert phase_label(None, "FORMING") == "FORMING"


class TestHeadShouldersHistoryDB:
    def test_head_shoulders_history_table_created(self):
        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='head_shoulders_history'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None

    def test_log_head_shoulders_pattern_insert_and_ignore(self):
        first = db.log_head_shoulders_pattern("HSLOG1", "regular", "CONFIRMED", "2020-01-01", 100.0, "2020-01-01 10:00:00")
        second = db.log_head_shoulders_pattern("HSLOG1", "regular", "CONFIRMED", "2020-01-01", 100.0, "2020-01-01 10:00:00")
        conn = db.get_connection()
        try:
            rows = conn.execute(
                "SELECT phase FROM head_shoulders_history WHERE ticker='HSLOG1' AND scan_date='2020-01-01'"
            ).fetchall()
        finally:
            conn.execute("DELETE FROM head_shoulders_history WHERE ticker='HSLOG1'")
            conn.commit()
            conn.close()
        assert first is True
        assert second is False
        assert len(rows) == 1

    def test_regular_and_inverse_can_coexist_same_day(self):
        db.log_head_shoulders_pattern("HSLOG2", "regular", "CONFIRMED", "2020-01-01", 100.0, "2020-01-01 10:00:00")
        db.log_head_shoulders_pattern("HSLOG2", "inverse", "CONFIRMED", "2020-01-01", 100.0, "2020-01-01 10:00:00")
        conn = db.get_connection()
        try:
            rows = conn.execute(
                "SELECT pattern_type FROM head_shoulders_history WHERE ticker='HSLOG2' AND scan_date='2020-01-01'"
            ).fetchall()
        finally:
            conn.execute("DELETE FROM head_shoulders_history WHERE ticker='HSLOG2'")
            conn.commit()
            conn.close()
        assert len(rows) == 2

    def test_get_unresolved_head_shoulders_patterns_filters(self):
        db.log_head_shoulders_pattern("HSFILT1", "regular", "CONFIRMED", "2019-01-01", 50.0, "2019-01-01 10:00:00")
        db.log_head_shoulders_pattern("HSFILT2", "regular", "FORMING", "2019-01-01", 50.0, "2019-01-01 10:00:00")
        rows = db.get_unresolved_head_shoulders_patterns("2020-01-01", "2020-01-01")
        tickers = [r["ticker"] for r in rows]
        assert "HSFILT1" in tickers
        assert "HSFILT2" not in tickers

    def test_batch_update_head_shoulders_actuals(self):
        db.log_head_shoulders_pattern("HSUPD1", "regular", "CONFIRMED", "2019-02-01", 60.0, "2019-02-01 09:00:00")
        conn = db.get_connection()
        try:
            row_id = conn.execute("SELECT id FROM head_shoulders_history WHERE ticker='HSUPD1'").fetchone()["id"]
        finally:
            conn.close()
        db.batch_update_head_shoulders_actuals([(row_id, 14, 55.0, "2019-02-15", 1)])
        conn = db.get_connection()
        try:
            updated = dict(conn.execute(
                "SELECT actual_price_14d, actual_date_14d, direction_correct_14d FROM head_shoulders_history WHERE id=?",
                (row_id,),
            ).fetchone())
        finally:
            conn.execute("DELETE FROM head_shoulders_history WHERE ticker='HSUPD1'")
            conn.commit()
            conn.close()
        assert updated["actual_price_14d"] == 55.0
        assert updated["direction_correct_14d"] == 1

    def test_get_head_shoulders_accuracy_aggregates(self):
        conn = db.get_connection()
        try:
            conn.execute(
                """INSERT OR IGNORE INTO head_shoulders_history
                   (ticker, pattern_type, phase, scan_date, scan_ts, close_price, direction_correct_14d)
                   VALUES (?,?,?,?,?,?,?)""",
                ("HSAGG1", "regular", "CONFIRMED", "2018-01-01", "2018-01-01 10:00:00", 100.0, 1),
            )
            conn.execute(
                """INSERT OR IGNORE INTO head_shoulders_history
                   (ticker, pattern_type, phase, scan_date, scan_ts, close_price, direction_correct_14d)
                   VALUES (?,?,?,?,?,?,?)""",
                ("HSAGG2", "regular", "CONFIRMED", "2018-01-02", "2018-01-02 10:00:00", 100.0, 0),
            )
            conn.commit()
        finally:
            conn.close()
        result = db.get_head_shoulders_accuracy()
        reg_row = next((r for r in result["patterns"] if r["pattern_type"] == "regular"), None)
        assert reg_row is not None
        assert reg_row["resolved_14d"] >= 2
        assert reg_row["accuracy_14d"] == 50.0


class TestFillPatternOutcomes:
    @staticmethod
    def _cleanup():
        conn = db.get_connection()
        try:
            conn.execute("DELETE FROM head_shoulders_history WHERE ticker='HSFATST'")
            conn.commit()
        finally:
            conn.close()

    def test_returns_zero_when_no_pending_rows(self):
        assert fill_pattern_outcomes() == 0

    def test_back_fills_14d_outcome_from_parquet(self, tmp_path):
        from datetime import date, timedelta
        scan_date = (date.today() - timedelta(days=20)).strftime("%Y-%m-%d")
        db.log_head_shoulders_pattern("HSFATST", "regular", "CONFIRMED", scan_date, 100.0, f"{scan_date} 10:00:00")
        try:
            n = 30
            idx = pd.date_range(scan_date, periods=n, freq="D")
            prices = np.linspace(100.0, 90.0, n)  # declining — regular H&S "down" prediction correct
            df = pd.DataFrame({"Close": prices, "Open": prices, "High": prices + 1, "Low": prices - 1, "Volume": np.ones(n)}, index=idx)
            (tmp_path / "HSFATST.parquet").parent.mkdir(parents=True, exist_ok=True)
            df.to_parquet(tmp_path / "HSFATST.parquet", engine="pyarrow")

            with patch("head_shoulders_engine.HISTORICAL_DIR", tmp_path):
                count = fill_pattern_outcomes()

            conn = db.get_connection()
            try:
                row = dict(conn.execute(
                    "SELECT * FROM head_shoulders_history WHERE ticker='HSFATST' AND scan_date=?", (scan_date,)
                ).fetchone())
            finally:
                conn.close()
            assert count >= 1
            assert row.get("actual_price_14d") is not None
            assert row.get("direction_correct_14d") == 1
        finally:
            self._cleanup()


class TestRunHeadShouldersJobMarketGating:
    @staticmethod
    def _row(ticker: str, phase: str = "CONFIRMED", pattern_type: str = "regular") -> dict:
        return {
            "ticker": ticker, "pattern_type": pattern_type, "phase": phase,
            "l_shoulder_date": "2026-01-01", "l_shoulder_price": 110.0,
            "l_armpit_date": "2026-01-10", "l_armpit_price": 95.0,
            "head_date": "2026-01-20", "head_price": 120.0,
            "r_armpit_date": "2026-01-30", "r_armpit_price": 96.0,
            "r_shoulder_date": "2026-02-05", "r_shoulder_price": 108.0,
            "neck_slope": 0.04, "neck_value": 97.0,
            "breakout_date": "2026-02-10", "breakout_price": 90.0,
            "measured_target": 74.0, "volume_confirms": True, "rsi_divergence": True,
            "pattern_r2": 0.85, "prior_trend_pct": 12.0, "close_price": 90.0,
            "scan_ts": "2026-02-10 22:20:00",
        }

    def test_suppresses_alert_when_ticker_exchange_closed(self):
        import scheduler_jobs

        conn = db.get_connection()
        try:
            conn.execute("DELETE FROM alert_state WHERE engine = 'HeadShouldersDetector'")
            conn.commit()
        finally:
            conn.close()

        with patch(
            "head_shoulders_engine.HeadShouldersEngine.run_scan",
            return_value=[self._row("AAPL")],
        ), patch("scheduler_jobs.is_quote_settled", return_value=False) as mock_open, \
           patch("scheduler_jobs.notify") as mock_notify:
            scheduler_jobs.run_head_shoulders_job()

        mock_open.assert_called()
        mock_notify.assert_not_called()

    def test_fires_alert_when_ticker_exchange_open(self):
        import scheduler_jobs

        conn = db.get_connection()
        try:
            conn.execute("DELETE FROM alert_state WHERE engine = 'HeadShouldersDetector'")
            conn.commit()
        finally:
            conn.close()

        with patch(
            "head_shoulders_engine.HeadShouldersEngine.run_scan",
            return_value=[self._row("AAPL")],
        ), patch("scheduler_jobs.is_quote_settled", return_value=True), \
           patch("scheduler_jobs.notify", return_value=True) as mock_notify:
            scheduler_jobs.run_head_shoulders_job()

        mock_notify.assert_called_once()
