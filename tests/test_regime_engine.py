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
