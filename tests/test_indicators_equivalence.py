"""
Equivalence test for the indicators.py refactor (audit item 3.8).

For every indicator formerly computed inline, verify that the output of the
canonical indicators.py function is numerically identical to the old inline
expression, evaluated on the same sample DataFrame.

Run with:  python -m pytest tests/test_indicators_equivalence.py -v
"""
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


# ── quant_engine.py scalar extraction: squeeze() is a no-op on a plain Series ──

def test_squeeze_on_series_is_noop(df):
    """quant_engine.py calls .squeeze() before passing close_s to indicators."""
    close_squeezed = df["Close"].squeeze()
    pd.testing.assert_series_equal(
        compute_rsi(close_squeezed),
        compute_rsi(df["Close"]),
        check_names=False,
    )
