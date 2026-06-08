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
)
from constants import REGIME_CRASH_VOL, REGIME_VOLATILE_VOL

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

    def test_high_vol_produces_crash_regime(self):
        spy_df, vix_df, ftse_df = _make_spy_vix_ftse(spy_daily_std=0.03)
        with patch("regime_engine.yahoo_engine.get_price_history",
                   return_value={"SPY": spy_df, "^VIX": vix_df, "^FTSE": ftse_df}):
            calculate_market_regime()
        row = get_latest_regime()
        assert row is not None
        assert row["us_regime_label"] == "Crash"
        assert row["spy_volatility"] >= REGIME_CRASH_VOL

    def test_low_vol_produces_normal_regime(self):
        spy_df, vix_df, ftse_df = _make_spy_vix_ftse(spy_daily_std=0.004)
        with patch("regime_engine.yahoo_engine.get_price_history",
                   return_value={"SPY": spy_df, "^VIX": vix_df, "^FTSE": ftse_df}):
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
