"""
Equivalence test for the indicators.py refactor (audit item 3.8).

For every indicator formerly computed inline, verify that the output of the
canonical indicators.py function is numerically identical to the old inline
expression, evaluated on the same sample DataFrame.

Run with:  python -m pytest tests/test_indicators_equivalence.py -v
       or: python tests/test_indicators_equivalence.py
"""
import sys
from pathlib import Path

# Make the project root importable when the file is run directly
sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
import pytest
import ta

from indicators import (
    compute_rsi,
    compute_macd,
    compute_smas,
    compute_atr,
    compute_volume_sma,
    compute_volume_surge,
    compute_bullish_cross,
    compute_volume_profile,
    compute_keltner_channel,
)

# ── Synthetic deterministic OHLCV fixture (300 rows, no network needed) ───────

_RNG = np.random.default_rng(42)

def _make_ohlcv(n: int = 300) -> pd.DataFrame:
    close  = 100 + np.cumsum(_RNG.normal(0, 1, n))
    high   = close + _RNG.uniform(0.1, 2.0, n)
    low    = close - _RNG.uniform(0.1, 2.0, n)
    volume = _RNG.integers(500_000, 5_000_000, n).astype(float)
    idx    = pd.date_range("2022-01-01", periods=n, freq="B")
    return pd.DataFrame(
        {"Close": close, "High": high, "Low": low, "Volume": volume}, index=idx
    )


@pytest.fixture(scope="module")
def df() -> pd.DataFrame:
    return _make_ohlcv()


# ── RSI ────────────────────────────────────────────────────────────────────────

def test_rsi_equals_ta_library(df):
    expected = ta.momentum.RSIIndicator(close=df["Close"], window=14).rsi()
    result   = compute_rsi(df["Close"])
    pd.testing.assert_series_equal(result, expected, check_names=False)


# ── MACD ───────────────────────────────────────────────────────────────────────

def test_macd_equals_ta_library(df):
    ind = ta.trend.MACD(close=df["Close"])
    macd_exp, sig_exp, hist_exp = ind.macd(), ind.macd_signal(), ind.macd_diff()
    macd_got, sig_got, hist_got = compute_macd(df["Close"])
    pd.testing.assert_series_equal(macd_got, macd_exp, check_names=False)
    pd.testing.assert_series_equal(sig_got,  sig_exp,  check_names=False)
    pd.testing.assert_series_equal(hist_got, hist_exp, check_names=False)


# ── SMA-50 / SMA-200 ───────────────────────────────────────────────────────────

def test_smas_equal_ta_library(df):
    smas = compute_smas(df["Close"], [50, 200])
    for w in (50, 200):
        expected = ta.trend.SMAIndicator(close=df["Close"], window=w).sma_indicator()
        pd.testing.assert_series_equal(smas[w], expected, check_names=False)


# ── ATR ────────────────────────────────────────────────────────────────────────

def test_atr_equals_ta_library(df):
    expected = ta.volatility.AverageTrueRange(
        high=df["High"], low=df["Low"], close=df["Close"], window=14
    ).average_true_range()
    result = compute_atr(df["High"], df["Low"], df["Close"])
    pd.testing.assert_series_equal(result, expected, check_names=False)


# ── vol_sma_20: canonical (raw pandas) vs old ta.SMAIndicator path ─────────────

def test_volume_sma_canonical_equals_pandas_rolling(df):
    """Both definitions should produce the same SMA-20 on volume."""
    old_ta_path     = ta.trend.SMAIndicator(close=df["Volume"], window=20).sma_indicator()
    old_pandas_path = df["Volume"].rolling(window=20).mean()
    canonical       = compute_volume_sma(df["Volume"])

    pd.testing.assert_series_equal(canonical, old_pandas_path, check_names=False,
                                   rtol=1e-10, atol=1e-10)
    pd.testing.assert_series_equal(canonical, old_ta_path,     check_names=False,
                                   rtol=1e-10, atol=1e-10)


# ── volume_surge ───────────────────────────────────────────────────────────────

def test_volume_surge_matches_old_vectorized_expression(df):
    vol_sma = compute_volume_sma(df["Volume"])
    expected = (df["Volume"] > (vol_sma * 1.5)).astype(int)
    result   = compute_volume_surge(df["Volume"], vol_sma)
    pd.testing.assert_series_equal(result, expected, check_names=False)


# ── bullish_cross ──────────────────────────────────────────────────────────────

def test_bullish_cross_matches_old_vectorized_expression(df):
    macd, signal, _ = compute_macd(df["Close"])
    expected = (
        (macd > signal) & (macd.shift(1) <= signal.shift(1))
    ).astype(int)
    result = compute_bullish_cross(macd, signal)
    pd.testing.assert_series_equal(result, expected, check_names=False)


# ── compute_volume_profile ─────────────────────────────────────────────────────

def test_volume_profile_returns_all_keys(df):
    result = compute_volume_profile(df)
    for key in ("poc", "val", "vah", "hvns", "lvns", "entry_zone", "exit_zone"):
        assert key in result

def test_volume_profile_poc_within_price_range(df):
    result = compute_volume_profile(df)
    assert result["poc"] is not None
    assert float(df["Low"].min()) <= result["poc"] <= float(df["High"].max())

def test_volume_profile_val_leq_poc_leq_vah(df):
    result = compute_volume_profile(df)
    assert result["val"] <= result["poc"] <= result["vah"]

def test_volume_profile_entry_zone_below_current_price(df):
    result = compute_volume_profile(df)
    if result["entry_zone"] is not None:
        assert result["entry_zone"] < float(df["Close"].iloc[-1])

def test_volume_profile_exit_zone_above_current_price(df):
    result = compute_volume_profile(df)
    if result["exit_zone"] is not None:
        assert result["exit_zone"] > float(df["Close"].iloc[-1])

def test_volume_profile_insufficient_data_returns_empty():
    tiny = _make_ohlcv(n=10)
    result = compute_volume_profile(tiny)
    assert result["poc"] is None
    assert result["hvns"] == []

def test_volume_profile_zero_volume_returns_empty(df):
    df_zero = df.copy()
    df_zero["Volume"] = 0.0
    result = compute_volume_profile(df_zero)
    assert result["poc"] is None

def test_volume_profile_flat_price_range_returns_empty(df):
    df_flat = df.copy()
    df_flat["High"] = df_flat["Low"] = df_flat["Close"] = 100.0
    result = compute_volume_profile(df_flat)
    assert result["poc"] is None


# ── compute_keltner_channel ────────────────────────────────────────────────────

def test_keltner_returns_all_keys(df):
    result = compute_keltner_channel(df["High"], df["Low"], df["Close"])
    for key in ("ema_21", "upper_2", "upper_3", "lower_2", "lower_3", "z_score"):
        assert key in result

def test_keltner_bands_symmetric_around_ema(df):
    r = compute_keltner_channel(df["High"], df["Low"], df["Close"])
    assert r["ema_21"] is not None
    ema = r["ema_21"]
    assert abs((ema - r["lower_2"]) - (r["upper_2"] - ema)) < 1e-9
    assert abs((ema - r["lower_3"]) - (r["upper_3"] - ema)) < 1e-9

def test_keltner_upper3_wider_than_upper2(df):
    r = compute_keltner_channel(df["High"], df["Low"], df["Close"])
    assert r["upper_3"] > r["upper_2"]
    assert r["lower_3"] < r["lower_2"]

def test_keltner_insufficient_data_returns_all_none():
    tiny = _make_ohlcv(n=5)
    r = compute_keltner_channel(tiny["High"], tiny["Low"], tiny["Close"])
    assert all(v is None for v in r.values())

def test_keltner_flat_close_z_score_is_none():
    flat = _make_ohlcv(n=50)
    flat["Close"] = flat["High"] = flat["Low"] = 100.0
    r = compute_keltner_channel(flat["High"], flat["Low"], flat["Close"])
    assert r["z_score"] is None

def test_keltner_z_score_positive_above_ema(df):
    r = compute_keltner_channel(df["High"], df["Low"], df["Close"])
    last_close = float(df["Close"].iloc[-1])
    if r["ema_21"] is not None and r["z_score"] is not None:
        expected_sign = 1 if last_close > r["ema_21"] else -1 if last_close < r["ema_21"] else 0
        assert (r["z_score"] > 0) == (expected_sign > 0) or r["z_score"] == 0.0


# ── quant_engine.py scalar extraction: squeeze() is a no-op on a plain Series ──

def test_squeeze_on_series_is_noop(df):
    """quant_engine.py calls .squeeze() before passing close_s to indicators."""
    close_squeezed = df["Close"].squeeze()
    pd.testing.assert_series_equal(
        compute_rsi(close_squeezed),
        compute_rsi(df["Close"]),
        check_names=False,
    )


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
