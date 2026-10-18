import tempfile
import numpy as np
import pandas as pd
import pytest
from pathlib import Path
from unittest.mock import patch

import database
from regime_engine import (
    calculate_market_regime,
    get_latest_regime,
    calculate_systemic_macro_threat,
    _classify_regime,
    classify_macro_regime,
    _build_if_features,
    run_price_regime_hmm,
    run_market_stress_if,
    _IF_FEATURE_COLS,
)
from constants import REGIME_CRASH_VOL, REGIME_VOLATILE_VOL, IF_STRESS_MIN_ROWS

pytestmark = pytest.mark.regime


def _make_spy_vix_ftse(spy_daily_std, ftse_daily_std=None, n=252):
    """Build synthetic SPY, VIX, FTSE DataFrames with controllable vol."""
    if ftse_daily_std is None:
        ftse_daily_std = spy_daily_std
    np.random.seed(0)
    dates = pd.date_range("2025-01-01", periods=n, freq="B")
    spy_prices = 100 * np.cumprod(1 + np.random.normal(0, spy_daily_std, n))
    ftse_prices = 7000 * np.cumprod(1 + np.random.normal(0, ftse_daily_std, n))
    spy_df = pd.DataFrame({"Close": spy_prices}, index=dates)
    ftse_df = pd.DataFrame({"Close": ftse_prices}, index=dates)
    vix_df = pd.DataFrame({"Close": np.full(n, 15.0)}, index=dates)
    return spy_df, vix_df, ftse_df


def _make_tnx_df(curr_tnx, past_tnx, n=10):
    """TNX series where iloc[-1]=curr and the date-lookback value = past."""
    dates = pd.date_range("2025-06-02", periods=n, freq="B")
    prices = [past_tnx] * n
    prices[-1] = curr_tnx
    return pd.DataFrame({"Close": prices}, index=dates)


def _run_macro(curr_tnx, past_tnx):
    """Run calculate_systemic_macro_threat with controlled TNX and no gilt file."""
    tnx_df = _make_tnx_df(curr_tnx, past_tnx)
    n = len(tnx_df)
    dates = tnx_df.index
    flat = pd.DataFrame({"Close": [3.5] * n}, index=dates)
    mock_dfs = {"^TYX": flat, "^TNX": tnx_df, "DX-Y.NYB": flat, "GBPUSD=X": flat}

    with (
        patch("regime_engine.yahoo_engine.get_price_history", return_value=mock_dfs),
        patch("regime_engine.HISTORICAL_DIR", new=Path(tempfile.mkdtemp())),
    ):
        calculate_systemic_macro_threat()

    conn = database.get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM macro_regimes ORDER BY date DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


class TestCalculateMarketRegime:

    def setup_method(self):
        conn = database.get_connection()
        try:
            conn.execute("DELETE FROM market_regimes")
            conn.commit()
        finally:
            conn.close()

    def test_high_vol_produces_crash_regime(self):
        spy_df, vix_df, ftse_df = _make_spy_vix_ftse(spy_daily_std=0.03)
        with patch("regime_engine.yahoo_engine.get_price_history",
                   return_value={"SPY": spy_df, "^VIX": vix_df, "^FTSE": ftse_df}), \
             patch("regime_engine.run_price_regime_hmm", return_value={}), \
             patch("regime_engine.run_market_stress_if", return_value={}):
            calculate_market_regime()
        row = get_latest_regime()
        assert row is not None
        assert row["us_regime_label"] == "Crash"
        assert row["spy_volatility"] >= REGIME_CRASH_VOL

    def test_low_vol_produces_normal_regime(self):
        spy_df, vix_df, ftse_df = _make_spy_vix_ftse(spy_daily_std=0.004)
        with patch("regime_engine.yahoo_engine.get_price_history",
                   return_value={"SPY": spy_df, "^VIX": vix_df, "^FTSE": ftse_df}), \
             patch("regime_engine.run_price_regime_hmm", return_value={}), \
             patch("regime_engine.run_market_stress_if", return_value={}):
            calculate_market_regime()
        row = get_latest_regime()
        assert row is not None
        assert row["us_regime_label"] == "Normal"
        assert row["spy_volatility"] < REGIME_VOLATILE_VOL

    def test_row_has_all_expected_keys(self):
        spy_df, vix_df, ftse_df = _make_spy_vix_ftse(spy_daily_std=0.01)
        with patch("regime_engine.yahoo_engine.get_price_history",
                   return_value={"SPY": spy_df, "^VIX": vix_df, "^FTSE": ftse_df}):
            calculate_market_regime()
        row = get_latest_regime()
        assert row is not None
        for key in ("date", "vix_close", "spy_volatility", "us_turbulence",
                    "us_regime_label", "ftse_volatility", "uk_turbulence", "uk_regime_label"):
            assert key in row

    def test_missing_tickers_does_not_raise(self):
        with patch("regime_engine.yahoo_engine.get_price_history", return_value={}):
            calculate_market_regime()  # must not raise

    def test_empty_dataframes_returns_early(self):
        empty = pd.DataFrame()
        with patch("regime_engine.yahoo_engine.get_price_history",
                   return_value={"SPY": empty, "^VIX": empty, "^FTSE": empty}):
            calculate_market_regime()  # must not raise


class TestGetLatestRegime:

    def test_returns_none_when_table_empty(self):
        conn = database.get_connection()
        try:
            conn.execute("DELETE FROM market_regimes")
            conn.commit()
        finally:
            conn.close()
        assert get_latest_regime() is None

    def test_returns_most_recent_row(self):
        conn = database.get_connection()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO market_regimes "
                "(date, vix_close, spy_volatility, us_turbulence, us_regime_label, "
                " ftse_volatility, uk_turbulence, uk_regime_label) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                ("2025-06-01", 18.5, 12.3, 12.3, "Normal", 11.0, 11.0, "Normal")
            )
            conn.commit()
        finally:
            conn.close()

        row = get_latest_regime()
        assert row is not None
        assert row["us_regime_label"] == "Normal"
        assert row["date"] == "2025-06-01"


class TestMacroThreatVelocityRules:

    def test_high_velocity_us_red(self):
        # 35 bps velocity >= 30 threshold (margin avoids float rounding edge)
        row = _run_macro(curr_tnx=4.60, past_tnx=4.25)
        assert row["us_threat_level"] == "RED"

    def test_high_absolute_level_us_red(self):
        # 4.8% >= 4.75 threshold, minimal velocity
        row = _run_macro(curr_tnx=4.80, past_tnx=4.79)
        assert row["us_threat_level"] == "RED"

    def test_medium_velocity_us_yellow(self):
        # 18 bps velocity >= 15 threshold (margin avoids float rounding edge)
        row = _run_macro(curr_tnx=4.00, past_tnx=3.82)
        assert row["us_threat_level"] == "YELLOW"

    def test_medium_absolute_level_us_yellow(self):
        # level >= 4.25, minimal velocity
        row = _run_macro(curr_tnx=4.30, past_tnx=4.29)
        assert row["us_threat_level"] == "YELLOW"

    def test_low_velocity_low_level_us_green(self):
        row = _run_macro(curr_tnx=3.50, past_tnx=3.48)
        assert row["us_threat_level"] == "GREEN"

    def test_db_row_has_expected_keys(self):
        row = _run_macro(curr_tnx=3.50, past_tnx=3.48)
        assert row is not None
        for key in ("date", "tnx_close", "us_threat_level", "uk_threat_level", "us_yield_velocity"):
            assert key in row

    def test_velocity_stored_in_basis_points(self):
        # 20 bps velocity
        row = _run_macro(curr_tnx=4.20, past_tnx=4.00)
        assert row is not None
        assert abs(row["us_yield_velocity"] - 20.0) < 0.5


class TestMacroThreatFailedFetches:
    """A failed fetch is stored as NULL, never as 0.0 or as a substitute taken from another series."""

    _EMPTY = pd.DataFrame()

    def _run(self, empty=(), gilt_closes=None, gilt_dates=None, tnx=(4.30, 4.29), tnx_df=None, gilt_bytes=None):
        tnx_df = _make_tnx_df(*tnx) if tnx_df is None else tnx_df
        flat = pd.DataFrame({"Close": [3.5] * len(tnx_df)}, index=tnx_df.index)
        dfs = {"^TYX": flat, "^TNX": tnx_df, "DX-Y.NYB": flat, "GBPUSD=X": flat}
        for ticker in empty:
            dfs[ticker] = self._EMPTY
        history_dir = Path(tempfile.mkdtemp())
        if gilt_closes is not None:
            index = pd.DatetimeIndex(gilt_dates if gilt_dates is not None else tnx_df.index[-len(gilt_closes):])
            pd.DataFrame({"Close": gilt_closes}, index=index).to_parquet(history_dir / "UK_GILT_BASELINE.parquet")
        if gilt_bytes is not None:
            (history_dir / "UK_GILT_BASELINE.parquet").write_bytes(gilt_bytes)
        with (
            patch("regime_engine.yahoo_engine.get_price_history", return_value=dfs),
            patch("regime_engine.HISTORICAL_DIR", new=history_dir),
        ):
            calculate_systemic_macro_threat()
        return self._row(tnx_df.index[-1].strftime("%Y-%m-%d"))

    @staticmethod
    def _row(date):
        conn = database.get_connection()
        try:
            row = conn.execute("SELECT * FROM macro_regimes WHERE date=?", (date,)).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def test_failed_dollar_sterling_and_30y_fetches_are_null(self, isolated_regime_tables):
        row = self._run(empty=("^TYX", "DX-Y.NYB", "GBPUSD=X"))
        assert row["tyx_close"] is None
        assert row["dxy_close"] is None
        assert row["gbpusd_close"] is None
        assert row["tnx_close"] == 4.3 and row["us_threat_level"] == "YELLOW"

    def test_successful_fetches_are_stored(self, isolated_regime_tables):
        row = self._run()
        assert (row["tyx_close"], row["dxy_close"], row["gbpusd_close"]) == (3.5, 3.5, 3.5)

    def test_missing_gilt_file_leaves_the_uk_side_null_and_the_us_side_computed(self, isolated_regime_tables):
        row = self._run()
        assert row["uk_gilt_close"] is None
        assert row["uk_yield_velocity"] is None
        assert row["uk_threat_level"] is None
        assert row["us_threat_level"] == "YELLOW"

    def test_gilt_file_with_a_value_four_days_back_is_scored(self, isolated_regime_tables):
        row = self._run(gilt_closes=[4.0] * 9 + [4.6])
        assert row["uk_gilt_close"] == 4.6
        assert row["uk_threat_level"] == "RED"
        assert abs(row["uk_yield_velocity"] - 60.0) < 0.5

    def test_gilt_file_without_a_value_four_days_back_leaves_the_uk_side_null(self, isolated_regime_tables):
        row = self._run(gilt_closes=[4.0, 4.6])
        assert row["uk_gilt_close"] is None and row["uk_threat_level"] is None
        assert row["us_threat_level"] == "YELLOW"

    def test_gilt_file_with_only_weekend_rows_leaves_the_uk_side_null(self, isolated_regime_tables):
        row = self._run(gilt_closes=[4.0, 4.6], gilt_dates=["2025-06-07", "2025-06-08"])
        assert row["uk_gilt_close"] is None and row["uk_threat_level"] is None
        assert row["us_threat_level"] == "YELLOW"

    def test_unreadable_gilt_file_leaves_the_uk_side_null_and_still_writes_the_us_row(self, isolated_regime_tables):
        row = self._run(gilt_bytes=b"not a parquet file")
        assert row["uk_gilt_close"] is None and row["uk_yield_velocity"] is None and row["uk_threat_level"] is None
        assert row["us_threat_level"] == "YELLOW" and row["tnx_close"] == 4.3

    def test_gilt_file_without_a_close_column_leaves_the_uk_side_null(self, isolated_regime_tables):
        no_close = Path(tempfile.mkdtemp()) / "no_close.parquet"
        pd.DataFrame({"Open": [4.0] * 10}, index=pd.date_range("2025-06-02", periods=10, freq="B")).to_parquet(no_close)
        row = self._run(gilt_bytes=no_close.read_bytes())
        assert row["uk_gilt_close"] is None and row["uk_threat_level"] is None
        assert row["us_threat_level"] == "YELLOW"

    @staticmethod
    def _tnx_without_a_value_four_days_back():
        return pd.DataFrame({"Close": [4.0, 4.1, 4.2, 4.9]}, index=pd.date_range("2025-06-02", periods=4, freq="D"))

    def test_tnx_without_a_value_four_days_back_leaves_the_us_velocity_and_level_null(self, isolated_regime_tables):
        row = self._run(tnx_df=self._tnx_without_a_value_four_days_back())
        assert row["us_yield_velocity"] is None and row["us_threat_level"] is None
        assert row["tnx_close"] == 4.9
        assert (row["tyx_close"], row["dxy_close"], row["gbpusd_close"]) == (3.5, 3.5, 3.5)

    def test_tnx_without_a_value_four_days_back_still_scores_the_uk_side(self, isolated_regime_tables):
        row = self._run(tnx_df=self._tnx_without_a_value_four_days_back(), gilt_closes=[4.0] * 9 + [4.6], gilt_dates=pd.date_range(end="2025-06-05", periods=10, freq="B"))
        assert row["us_threat_level"] is None
        assert row["uk_threat_level"] == "RED"

    def test_us_rerun_without_history_keeps_the_values_an_earlier_run_stored(self, isolated_regime_tables):
        full_history = pd.DataFrame({"Close": [4.0] * 9 + [4.9]}, index=pd.date_range("2025-05-26", periods=10, freq="D"))
        good = self._run(tnx_df=full_history)
        rerun = self._run(tnx_df=full_history.iloc[-4:])
        assert good["us_threat_level"] == "RED" and rerun["us_threat_level"] == "RED"
        assert rerun["us_yield_velocity"] == good["us_yield_velocity"] is not None

    def test_failed_rerun_keeps_the_good_values_stored_for_the_same_date(self, isolated_regime_tables):
        good = self._run(gilt_closes=[4.0] * 9 + [4.6])
        rerun = self._run(empty=("^TYX", "DX-Y.NYB", "GBPUSD=X"))
        for column in ("tyx_close", "dxy_close", "gbpusd_close", "uk_gilt_close", "uk_yield_velocity", "uk_threat_level"):
            assert rerun[column] == good[column] and rerun[column] is not None

    def test_successful_rerun_replaces_the_stored_values(self, isolated_regime_tables):
        self._run(gilt_closes=[4.0] * 9 + [4.6])
        rerun = self._run(gilt_closes=[4.0] * 10)
        assert rerun["uk_gilt_close"] == 4.0 and rerun["uk_threat_level"] == "GREEN"


class TestClassifyRegime:
    """Unit tests for the pure _classify_regime() function — no DB needed."""

    def test_risk_on_all_clear(self):
        assert _classify_regime(0.50, 2.5, 300, 0, 1.2) == "Risk-On"

    def test_risk_on_hmm_zero_positive_curve(self):
        assert _classify_regime(0.80, 1.5, 250, 0, 2.0) == "Risk-On"

    def test_late_cycle_flattening_curve_elevated_cpi(self):
        assert _classify_regime(0.10, 3.5, 420, 1, 1.1) == "Late Cycle"

    def test_late_cycle_curve_at_upper_boundary(self):
        # 0.20 is the boundary — should still be Late Cycle when CPI > 3
        assert _classify_regime(0.20, 3.2, 400, 0, 1.0) == "Late Cycle"

    def test_stagflation_high_cpi_with_blown_spreads(self):
        assert _classify_regime(0.30, 4.5, 550, 0, 0.5) == "Stagflation"

    def test_stagflation_high_cpi_with_negative_real_yield(self):
        assert _classify_regime(0.50, 4.1, 300, 0, -0.3) == "Stagflation"

    def test_contraction_inverted_and_hmm_recession(self):
        assert _classify_regime(-0.20, 2.5, 450, 2, 1.0) == "Contraction"

    def test_contraction_inverted_blown_spreads_no_hmm(self):
        # Spreads > 600 with inverted curve = Contraction regardless of HMM
        assert _classify_regime(-0.10, 2.0, 650, 0, 1.0) == "Contraction"

    def test_recovery_hmm_choppy_positive_curve(self):
        assert _classify_regime(0.40, 2.0, 380, 1, 1.5) == "Recovery"

    def test_risk_on_when_all_inputs_none(self):
        # Must not raise; defaults to Risk-On
        assert _classify_regime(None, None, None, None, None) == "Risk-On"

    def test_stagflation_requires_both_cpi_and_stress(self):
        # High CPI alone (no spread blow-out, positive real yield) does not trigger Stagflation
        assert _classify_regime(0.50, 4.5, 350, 0, 1.0) == "Risk-On"

    def test_late_cycle_not_triggered_below_cpi_threshold(self):
        # Flattening curve but CPI under 3% → Risk-On, not Late Cycle
        assert _classify_regime(0.10, 2.8, 350, 0, 1.0) == "Risk-On"


class TestClassifyMacroRegime:
    """Integration tests for classify_macro_regime() — verifies DB round-trip.

    Uses a far-future sentinel date '2099-06-01' so it is always the most-recent
    row in both macro_indicators and macro_regimes, regardless of what other tests
    have inserted.
    """

    _DATE = "2099-06-01"

    def _seed(self, us_yield_curve, us_cpi=2.5, us_hy_spread=300.0, us_real_yield=1.2):
        conn = database.get_connection()
        try:
            conn.execute("DELETE FROM macro_indicators WHERE date=?", (self._DATE,))
            conn.execute(
                "INSERT INTO macro_indicators "
                "(date, us_yield_curve, us_cpi_inflation, us_high_yield_spread, us_real_yield_10y) "
                "VALUES (?, ?, ?, ?, ?)",
                (self._DATE, us_yield_curve, us_cpi, us_hy_spread, us_real_yield),
            )
            conn.execute("DELETE FROM macro_regimes WHERE date=?", (self._DATE,))
            conn.execute(
                "INSERT INTO macro_regimes (date, tnx_close, us_threat_level, uk_threat_level) "
                "VALUES (?, 4.2, 'GREEN', 'GREEN')",
                (self._DATE,),
            )
            conn.commit()
        finally:
            conn.close()

    def _read_result(self):
        conn = database.get_connection()
        try:
            row = conn.execute(
                "SELECT regime_label, yield_curve_inverted, days_inverted "
                "FROM macro_regimes WHERE date=?",
                (self._DATE,),
            ).fetchone()
            return dict(row) if row else None
        finally:
            conn.close()

    def test_writes_regime_label_to_db(self):
        self._seed(us_yield_curve=0.50)
        classify_macro_regime()
        result = self._read_result()
        assert result is not None
        assert result["regime_label"] == "Risk-On"

    def test_inverted_curve_sets_flag(self):
        self._seed(us_yield_curve=-0.15)
        classify_macro_regime()
        result = self._read_result()
        assert result is not None
        assert result["yield_curve_inverted"] == 1

    def test_positive_curve_clears_flag(self):
        self._seed(us_yield_curve=0.40)
        classify_macro_regime()
        result = self._read_result()
        assert result is not None
        assert result["yield_curve_inverted"] == 0

    def test_no_macro_indicators_does_not_raise(self):
        conn = database.get_connection()
        try:
            conn.execute("DELETE FROM macro_indicators")
            conn.commit()
        finally:
            conn.close()
        classify_macro_regime()  # must not raise


def _make_if_raw(n=60):
    """Build a minimal merged raw-price DataFrame suitable for _build_if_features."""
    dates = pd.date_range("2024-01-01", periods=n, freq="B")
    np.random.seed(7)
    return pd.DataFrame({
        "vix_close": np.random.uniform(12, 30, n),
        "hyg_close": np.random.uniform(70, 80, n),
        "tnx_close": np.random.uniform(3.5, 5.0, n),
        "spy_close": 400 * np.cumprod(1 + np.random.normal(0.0003, 0.008, n)),
        "spy_volume": np.random.uniform(50e6, 120e6, n),
    }, index=dates)


def _make_spy_df(n=300):
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    np.random.seed(42)
    prices = 100 * np.cumprod(1 + np.random.normal(0.0003, 0.01, n))
    return pd.DataFrame({"Close": prices}, index=dates)


def _make_if_ticker_dfs(n=300):
    """Build mock Yahoo Finance response for _IF_TICKERS."""
    dates = pd.date_range("2023-01-01", periods=n, freq="B")
    np.random.seed(99)
    return {
        "^VIX": pd.DataFrame({"Close": np.random.uniform(12, 30, n)}, index=dates),
        "HYG": pd.DataFrame({"Close": np.random.uniform(70, 80, n)}, index=dates),
        "^TNX": pd.DataFrame({"Close": np.random.uniform(3.5, 5.0, n)}, index=dates),
        "SPY": pd.DataFrame({
            "Close": 400 * np.cumprod(1 + np.random.normal(0.0003, 0.008, n)),
            "Volume": np.random.uniform(50e6, 120e6, n),
        }, index=dates),
    }


class TestBuildIfFeatures:

    def test_output_has_all_feature_cols(self):
        df = _make_if_raw()
        out = _build_if_features(df)
        assert list(out.columns) == _IF_FEATURE_COLS

    def test_vix_level_passthrough(self):
        df = _make_if_raw()
        out = _build_if_features(df)
        pd.testing.assert_series_equal(out["vix_level"], df["vix_close"], check_names=False)

    def test_hyg_return_is_pct_change(self):
        df = _make_if_raw(n=5)
        out = _build_if_features(df)
        expected = df["hyg_close"].pct_change() * 100.0
        pd.testing.assert_series_equal(out["hyg_return"], expected, check_names=False)

    def test_spy_return_is_pct_change(self):
        df = _make_if_raw(n=5)
        out = _build_if_features(df)
        expected = df["spy_close"].pct_change() * 100.0
        pd.testing.assert_series_equal(out["spy_return"], expected, check_names=False)

    def test_tnx_change_is_diff(self):
        df = _make_if_raw(n=5)
        out = _build_if_features(df)
        expected = df["tnx_close"].diff()
        pd.testing.assert_series_equal(out["tnx_change"], expected, check_names=False)

    def test_output_index_matches_input(self):
        df = _make_if_raw(n=40)
        out = _build_if_features(df)
        pd.testing.assert_index_equal(out.index, df.index)


class TestRunPriceRegimeHmm:

    def test_failed_fetch_returns_empty_dict(self):
        with patch("regime_engine.yahoo_engine.get_price_history", return_value={}), \
             patch("regime_engine._HMM_CACHE_PATH", new=Path(tempfile.mkdtemp()) / "spy_hmm.parquet"):
            result = run_price_regime_hmm()
        assert result == {}

    def test_insufficient_data_returns_empty_dict(self):
        # Only 30 rows — below the 60-row minimum
        spy_df = _make_spy_df(n=30)
        with patch("regime_engine.yahoo_engine.get_price_history",
                   return_value={"SPY": spy_df}), \
             patch("regime_engine._HMM_CACHE_PATH",
                   new=Path(tempfile.mkdtemp()) / "spy_hmm.parquet"):
            result = run_price_regime_hmm()
        assert result == {}

    def test_success_returns_expected_keys(self):
        spy_df = _make_spy_df(n=300)
        tmp = Path(tempfile.mkdtemp()) / "spy_hmm.parquet"
        with patch("regime_engine.yahoo_engine.get_price_history",
                   return_value={"SPY": spy_df}), \
             patch("regime_engine._HMM_CACHE_PATH", new=tmp):
            result = run_price_regime_hmm()
        assert set(result.keys()) == {"state", "label", "probability", "previous_state", "previous_label", "date"}

    def test_state_is_valid(self):
        spy_df = _make_spy_df(n=300)
        tmp = Path(tempfile.mkdtemp()) / "spy_hmm.parquet"
        with patch("regime_engine.yahoo_engine.get_price_history",
                   return_value={"SPY": spy_df}), \
             patch("regime_engine._HMM_CACHE_PATH", new=tmp):
            result = run_price_regime_hmm()
        assert result["state"] in (0, 1, 2)
        assert result["label"] in ("Bull", "Chop", "Crash")

    def test_probability_in_unit_interval(self):
        spy_df = _make_spy_df(n=300)
        tmp = Path(tempfile.mkdtemp()) / "spy_hmm.parquet"
        with patch("regime_engine.yahoo_engine.get_price_history",
                   return_value={"SPY": spy_df}), \
             patch("regime_engine._HMM_CACHE_PATH", new=tmp):
            result = run_price_regime_hmm()
        assert 0.0 <= result["probability"] <= 1.0

    def test_writes_to_price_hmm_states_table(self):
        spy_df = _make_spy_df(n=300)
        tmp = Path(tempfile.mkdtemp()) / "spy_hmm.parquet"
        conn = database.get_connection()
        try:
            conn.execute("DELETE FROM price_hmm_states")
            conn.commit()
        finally:
            conn.close()
        with patch("regime_engine.yahoo_engine.get_price_history",
                   return_value={"SPY": spy_df}), \
             patch("regime_engine._HMM_CACHE_PATH", new=tmp):
            run_price_regime_hmm()
        conn = database.get_connection()
        try:
            count = conn.execute("SELECT COUNT(*) FROM price_hmm_states").fetchone()[0]
        finally:
            conn.close()
        assert count > 0


class TestRunMarketStressIf:

    def test_failed_fetch_missing_tickers_returns_empty_dict(self):
        with patch("regime_engine.yahoo_engine.get_price_history", return_value={}), \
             patch("regime_engine._IF_CACHE_PATH", new=Path(tempfile.mkdtemp()) / "if.parquet"):
            result = run_market_stress_if()
        assert result == {}

    def test_insufficient_rows_returns_empty_dict(self):
        # Only 5 rows of data — well below IF_STRESS_MIN_ROWS
        ticker_dfs = _make_if_ticker_dfs(n=5)
        tmp = Path(tempfile.mkdtemp()) / "if.parquet"
        with patch("regime_engine.yahoo_engine.get_price_history", return_value=ticker_dfs), \
             patch("regime_engine._IF_CACHE_PATH", new=tmp), \
             patch("regime_engine._IF_MODEL_PATH", new=tmp.with_suffix(".joblib")):
            result = run_market_stress_if()
        assert result == {}

    def test_success_returns_expected_keys(self):
        ticker_dfs = _make_if_ticker_dfs(n=300)
        tmp_dir = Path(tempfile.mkdtemp())
        with patch("regime_engine.yahoo_engine.get_price_history", return_value=ticker_dfs), \
             patch("regime_engine._IF_CACHE_PATH", new=tmp_dir / "if.parquet"), \
             patch("regime_engine._IF_MODEL_PATH", new=tmp_dir / "if.joblib"):
            result = run_market_stress_if()
        assert set(result.keys()) == {"score", "features", "alert", "date"}

    def test_score_in_unit_interval(self):
        ticker_dfs = _make_if_ticker_dfs(n=300)
        tmp_dir = Path(tempfile.mkdtemp())
        with patch("regime_engine.yahoo_engine.get_price_history", return_value=ticker_dfs), \
             patch("regime_engine._IF_CACHE_PATH", new=tmp_dir / "if.parquet"), \
             patch("regime_engine._IF_MODEL_PATH", new=tmp_dir / "if.joblib"):
            result = run_market_stress_if()
        assert 0.0 <= result["score"] <= 1.0

    def test_features_has_all_if_feature_cols(self):
        ticker_dfs = _make_if_ticker_dfs(n=300)
        tmp_dir = Path(tempfile.mkdtemp())
        with patch("regime_engine.yahoo_engine.get_price_history", return_value=ticker_dfs), \
             patch("regime_engine._IF_CACHE_PATH", new=tmp_dir / "if.parquet"), \
             patch("regime_engine._IF_MODEL_PATH", new=tmp_dir / "if.joblib"):
            result = run_market_stress_if()
        assert set(result["features"].keys()) == set(_IF_FEATURE_COLS)

    def test_alert_is_bool(self):
        ticker_dfs = _make_if_ticker_dfs(n=300)
        tmp_dir = Path(tempfile.mkdtemp())
        with patch("regime_engine.yahoo_engine.get_price_history", return_value=ticker_dfs), \
             patch("regime_engine._IF_CACHE_PATH", new=tmp_dir / "if.parquet"), \
             patch("regime_engine._IF_MODEL_PATH", new=tmp_dir / "if.joblib"):
            result = run_market_stress_if()
        assert isinstance(result["alert"], bool)


@pytest.fixture
def isolated_regime_tables():
    tables = ("market_regimes", "macro_regimes", "treasury_auction_results")
    conn = database.get_connection()
    try:
        saved = {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in tables}
        for t in tables:
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
    finally:
        conn.close()
    try:
        yield
    finally:
        conn = database.get_connection()
        try:
            for t in tables:
                conn.execute(f"DELETE FROM {t}")
                for row in saved[t]:
                    conn.execute(f"INSERT INTO {t} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
            conn.commit()
        finally:
            conn.close()


def _insert_regime_row(date, **columns):
    names = ["date", *columns]
    conn = database.get_connection()
    try:
        conn.execute(
            f"INSERT OR REPLACE INTO market_regimes ({', '.join(names)}) VALUES ({', '.join('?' * len(names))})",
            [date, *columns.values()],
        )
        conn.commit()
    finally:
        conn.close()


class TestCurrentHmmRegime:
    def test_empty_table(self, isolated_regime_tables):
        from regime_engine import current_hmm_regime
        assert current_hmm_regime() == {"current": None, "last_change": None}

    def test_skips_a_newer_row_that_has_no_hmm_state(self, isolated_regime_tables):
        from regime_engine import current_hmm_regime
        _insert_regime_row("2026-05-01", price_hmm_state=1, price_hmm_label="Chop", price_hmm_prob=0.7, us_regime_label="Normal", uk_regime_label="Volatile")
        _insert_regime_row("2026-05-02", market_stress_score=0.3)
        result = current_hmm_regime()
        assert result["current"] == {
            "state": 1, "label": "Chop", "probability": 0.7, "as_of": "2026-05-01",
            "us_regime_label": "Normal", "uk_regime_label": "Volatile",
        }
        assert result["last_change"] is None

    def test_last_change_names_the_first_day_of_the_current_label(self, isolated_regime_tables):
        from regime_engine import current_hmm_regime
        for date, state, label in (("2026-05-01", 0, "Bull"), ("2026-05-02", 0, "Bull"), ("2026-05-03", 2, "Crash"), ("2026-05-04", 2, "Crash")):
            _insert_regime_row(date, price_hmm_state=state, price_hmm_label=label, price_hmm_prob=0.9)
        assert current_hmm_regime()["last_change"] == {"date": "2026-05-03", "from_label": "Bull", "to_label": "Crash"}


class TestMarketStressSnapshot:
    def test_empty_table(self, isolated_regime_tables):
        from regime_engine import market_stress_snapshot
        assert market_stress_snapshot() == {"current": None, "history": []}

    def test_current_and_history_oldest_first(self, isolated_regime_tables):
        from regime_engine import market_stress_snapshot
        _insert_regime_row("2026-05-01", market_stress_score=0.11112, market_stress_features='{"vix_level": 14.0}')
        _insert_regime_row("2026-05-02", market_stress_score=0.55556, market_stress_features='{"vix_level": 21.0}')
        result = market_stress_snapshot()
        assert result["current"] == {"score": 0.5556, "features": {"vix_level": 21.0}, "date": "2026-05-02"}
        assert result["history"] == [{"date": "2026-05-01", "score": 0.1111}, {"date": "2026-05-02", "score": 0.5556}]

    def test_unreadable_features_become_empty(self, isolated_regime_tables):
        from regime_engine import market_stress_snapshot
        _insert_regime_row("2026-05-01", market_stress_score=0.2, market_stress_features="not json")
        assert market_stress_snapshot()["current"]["features"] == {}


class TestMacroConditionsSnapshot:
    HA_FIELDS = {
        "as_of", "us_threat_level", "uk_threat_level", "us_yield_velocity", "uk_yield_velocity",
        "tyx_close", "tnx_close", "uk_gilt_close", "dxy_close", "gbpusd_close", "treasury_auction", "fear_greed",
    }

    def test_keys_are_the_ones_the_route_and_home_assistant_read(self, isolated_regime_tables):
        from regime_engine import macro_conditions_snapshot
        with patch("regime_engine.get_latest_fear_greed", return_value={"value": 50.0, "label": "Neutral", "as_of": "2026-05-01"}):
            result = macro_conditions_snapshot()
        assert set(result) == self.HA_FIELDS
        assert result["as_of"] is None and result["treasury_auction"] == {"healthy": None, "recent": []}
        assert result["fear_greed"]["label"] == "Neutral"

    def test_unavailable_closes_come_through_as_null(self, isolated_regime_tables):
        from regime_engine import macro_conditions_snapshot
        conn = database.get_connection()
        try:
            conn.execute("INSERT INTO macro_regimes (date, tnx_close, us_threat_level, us_yield_velocity) VALUES ('2026-05-01', 4.3, 'YELLOW', 18.5)")
            conn.commit()
        finally:
            conn.close()
        with patch("regime_engine.get_latest_fear_greed", return_value={"value": None, "label": None, "as_of": None}):
            result = macro_conditions_snapshot()
        assert result["tnx_close"] == 4.3 and result["us_threat_level"] == "YELLOW"
        for key in ("tyx_close", "dxy_close", "gbpusd_close", "uk_gilt_close", "uk_yield_velocity", "uk_threat_level"):
            assert result[key] is None

    def test_route_wraps_the_snapshot_with_status_only(self, client, isolated_regime_tables):
        with patch("regime_engine.get_latest_fear_greed", return_value={"value": None, "label": None, "as_of": None}):
            body = client.get("/api/macro-conditions").json()
        assert set(body) == self.HA_FIELDS | {"status"} and body["status"] == "success"


class TestRegimeRoutesWrapTheSnapshots:
    def test_market_regime_current(self, client, isolated_regime_tables):
        _insert_regime_row("2026-05-01", price_hmm_state=0, price_hmm_label="Bull", price_hmm_prob=0.8)
        body = client.get("/api/market-regime/current").json()
        assert set(body) == {"status", "current", "last_change"}
        assert body["current"]["label"] == "Bull" and body["current"]["us_regime_label"] is None

    def test_market_stress(self, client, isolated_regime_tables):
        _insert_regime_row("2026-05-01", market_stress_score=0.4, market_stress_features='{"vix_level": 15.0}')
        body = client.get("/api/market-stress").json()
        assert set(body) == {"status", "current", "history"}
        assert body["current"] == {"score": 0.4, "features": {"vix_level": 15.0}, "date": "2026-05-01"}
        assert body["history"] == [{"date": "2026-05-01", "score": 0.4}]


class TestCrashVetoStressThreshold:
    def test_default_and_configured(self):
        from regime_engine import crash_veto_stress_threshold
        with patch("config.load_config", return_value={}):
            assert crash_veto_stress_threshold() == 0.75
        with patch("config.load_config", return_value={"META_SCORING": {"CRASH_VETO": {"MARKET_STRESS_THRESHOLD": 0.6}}}):
            assert crash_veto_stress_threshold() == 0.6
