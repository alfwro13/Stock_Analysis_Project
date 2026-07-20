"""
tests/test_indicators_volatility.py — compute_true_range / compute_bollinger_bands /
compute_keltner_channel_series (indicators.py), added for the Volatility Squeeze and NR4/NR7
Narrow Range Pattern Detection families.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators import compute_true_range, compute_bollinger_bands, compute_keltner_channel_series


def _make_ohlcv(n: int = 100, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0, 1, n))
    high = close + rng.uniform(0.1, 2.0, n)
    low = close - rng.uniform(0.1, 2.0, n)
    idx = pd.date_range("2022-01-01", periods=n, freq="B")
    return pd.DataFrame({"Close": close, "High": high, "Low": low}, index=idx)


class TestComputeTrueRange:
    def test_matches_wilders_definition_bar_by_bar(self):
        df = _make_ohlcv()
        tr = compute_true_range(df["High"], df["Low"], df["Close"])
        prev_close = df["Close"].shift(1)
        expected = pd.concat([
            df["High"] - df["Low"],
            (df["High"] - prev_close).abs(),
            (df["Low"] - prev_close).abs(),
        ], axis=1).max(axis=1)
        pd.testing.assert_series_equal(tr, expected, check_names=False)

    def test_first_bar_falls_back_to_high_minus_low(self):
        """No prior close on the first bar -> both gap terms are NaN, so max(skipna=True)
        falls back to the plain high-low range for that one bar."""
        df = _make_ohlcv()
        tr = compute_true_range(df["High"], df["Low"], df["Close"])
        assert tr.iloc[0] == pytest.approx(float(df["High"].iloc[0] - df["Low"].iloc[0]))

    def test_gap_up_inflates_range_beyond_high_minus_low(self):
        high = pd.Series([10.0, 10.5])
        low = pd.Series([9.0, 10.0])
        close = pd.Series([9.5, 10.2])
        tr = compute_true_range(high, low, close)
        # Bar 1: H-L=0.5, but gapped up from prior close 9.5 -> |10.5-9.5|=1.0 dominates.
        assert tr.iloc[1] == pytest.approx(1.0)


class TestComputeBollingerBands:
    def test_upper_above_mid_above_lower(self):
        df = _make_ohlcv()
        bb = compute_bollinger_bands(df["Close"], window=20, num_std=2.0)
        valid = bb["mid"].notna()
        assert (bb["upper"][valid] >= bb["mid"][valid]).all()
        assert (bb["mid"][valid] >= bb["lower"][valid]).all()

    def test_wider_std_widens_bands(self):
        df = _make_ohlcv()
        narrow = compute_bollinger_bands(df["Close"], window=20, num_std=1.0)
        wide = compute_bollinger_bands(df["Close"], window=20, num_std=3.0)
        valid = narrow["upper"].notna()
        assert (wide["upper"][valid] >= narrow["upper"][valid]).all()
        assert (wide["lower"][valid] <= narrow["lower"][valid]).all()

    def test_flat_price_collapses_bands_to_mid(self):
        flat = pd.Series([100.0] * 30)
        bb = compute_bollinger_bands(flat, window=20, num_std=2.0)
        valid = bb["mid"].notna()
        assert (bb["upper"][valid] == bb["mid"][valid]).all()
        assert (bb["lower"][valid] == bb["mid"][valid]).all()


class TestComputeKeltnerChannelSeries:
    def test_upper_above_mid_above_lower(self):
        df = _make_ohlcv()
        kc = compute_keltner_channel_series(df["High"], df["Low"], df["Close"], ema_period=20, atr_window=20, multiplier=1.5)
        valid = kc["mid"].notna() & kc["upper"].notna()
        assert (kc["upper"][valid] >= kc["mid"][valid]).all()
        assert (kc["mid"][valid] >= kc["lower"][valid]).all()

    def test_higher_multiplier_widens_bands(self):
        df = _make_ohlcv()
        narrow = compute_keltner_channel_series(df["High"], df["Low"], df["Close"], multiplier=1.0)
        wide = compute_keltner_channel_series(df["High"], df["Low"], df["Close"], multiplier=3.0)
        valid = narrow["upper"].notna()
        assert (wide["upper"][valid] >= narrow["upper"][valid]).all()
        assert (wide["lower"][valid] <= narrow["lower"][valid]).all()

    def test_mid_is_ema_of_close(self):
        df = _make_ohlcv()
        kc = compute_keltner_channel_series(df["High"], df["Low"], df["Close"], ema_period=20)
        # The ta library requires a full window of history before emitting a value
        # (min_periods=window), unlike a bare .ewm() call which starts from bar 0.
        expected_ema = df["Close"].ewm(span=20, min_periods=20, adjust=False).mean()
        pd.testing.assert_series_equal(kc["mid"], expected_ema, check_names=False)
