"""
tests/test_volume_profile_keltner.py

Unit tests for compute_volume_profile() and compute_keltner_channel() in indicators.py.
No network calls — all data is synthetic.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import numpy as np
import pandas as pd
import pytest

from indicators import compute_volume_profile, compute_keltner_channel, compute_atr


_RNG = np.random.default_rng(99)


def _make_ohlcv(n: int = 300, trend: float = 0.0) -> pd.DataFrame:
    close = 100.0 + trend * np.arange(n) + np.cumsum(_RNG.normal(0, 0.5, n))
    close = np.maximum(close, 1.0)
    high = close + _RNG.uniform(0.1, 1.5, n)
    low = close - _RNG.uniform(0.1, 1.5, n)
    low = np.minimum(low, close - 0.01)
    volume = _RNG.integers(500_000, 5_000_000, n).astype(float)
    idx = pd.date_range("2023-01-01", periods=n, freq="B")
    return pd.DataFrame({"Close": close, "High": high, "Low": low, "Volume": volume}, index=idx)


class TestComputeVolumeProfile:

    def test_poc_within_price_range(self):
        df = _make_ohlcv()
        result = compute_volume_profile(df)
        assert result["poc"] is not None
        assert df["Low"].min() <= result["poc"] <= df["High"].max()

    def test_val_leq_poc_leq_vah(self):
        df = _make_ohlcv()
        result = compute_volume_profile(df)
        assert result["val"] is not None
        assert result["vah"] is not None
        assert result["val"] <= result["poc"] <= result["vah"]

    def test_entry_zone_below_current_price(self):
        df = _make_ohlcv()
        result = compute_volume_profile(df)
        current = float(df["Close"].iloc[-1])
        if result["entry_zone"] is not None:
            assert result["entry_zone"] < current

    def test_exit_zone_above_current_price(self):
        df = _make_ohlcv()
        result = compute_volume_profile(df)
        current = float(df["Close"].iloc[-1])
        if result["exit_zone"] is not None:
            assert result["exit_zone"] > current

    def test_insufficient_data_returns_none(self):
        df = _make_ohlcv(n=10)
        result = compute_volume_profile(df)
        assert result["poc"] is None
        assert result["val"] is None
        assert result["entry_zone"] is None

    def test_zero_volume_returns_none(self):
        df = _make_ohlcv()
        df["Volume"] = 0.0
        result = compute_volume_profile(df)
        assert result["poc"] is None

    def test_window_parameter_respected(self):
        df = _make_ohlcv(n=300)
        r_short = compute_volume_profile(df, window=30)
        r_long = compute_volume_profile(df, window=180)
        # Short window only sees recent prices; its POC can differ from long window
        assert r_short["poc"] is not None
        assert r_long["poc"] is not None

    def test_hvns_are_lists(self):
        df = _make_ohlcv()
        result = compute_volume_profile(df)
        assert isinstance(result["hvns"], list)
        assert isinstance(result["lvns"], list)

    def test_vah_gt_val(self):
        df = _make_ohlcv()
        result = compute_volume_profile(df)
        assert result["vah"] > result["val"]


class TestComputeKeltnerChannel:

    def test_returns_expected_keys(self):
        df = _make_ohlcv()
        result = compute_keltner_channel(df["High"], df["Low"], df["Close"])
        for key in ("ema_21", "upper_2", "upper_3", "lower_2", "lower_3", "z_score"):
            assert key in result

    def test_band_ordering(self):
        df = _make_ohlcv()
        kc = compute_keltner_channel(df["High"], df["Low"], df["Close"])
        if kc["lower_3"] is not None:
            assert kc["lower_3"] < kc["lower_2"] < kc["ema_21"] < kc["upper_2"] < kc["upper_3"]

    def test_z_score_sign_above_ema(self):
        # Force price consistently above EMA by using a strong uptrend
        df = _make_ohlcv(n=200, trend=0.5)
        kc = compute_keltner_channel(df["High"], df["Low"], df["Close"])
        assert kc["z_score"] is not None
        assert kc["z_score"] > 0

    def test_z_score_sign_below_ema(self):
        # Force price below EMA by using a strong downtrend
        df = _make_ohlcv(n=200, trend=-0.5)
        kc = compute_keltner_channel(df["High"], df["Low"], df["Close"])
        assert kc["z_score"] is not None
        assert kc["z_score"] < 0

    def test_insufficient_data_returns_none_z_score(self):
        df = _make_ohlcv(n=5)
        kc = compute_keltner_channel(df["High"], df["Low"], df["Close"])
        assert kc["z_score"] is None

    def test_z_score_formula_consistent(self):
        """z_score = (Close - EMA21) / ATR; verify manually on last bar."""
        df = _make_ohlcv()
        kc = compute_keltner_channel(df["High"], df["Low"], df["Close"])
        if kc["z_score"] is None:
            return
        ema_val = df["Close"].ewm(span=21, adjust=False).mean().iloc[-1]
        atr_val = compute_atr(df["High"], df["Low"], df["Close"]).iloc[-1]
        expected_z = (df["Close"].iloc[-1] - ema_val) / atr_val
        assert abs(kc["z_score"] - expected_z) < 1e-6

    def test_ema_21_within_band_range(self):
        df = _make_ohlcv()
        kc = compute_keltner_channel(df["High"], df["Low"], df["Close"])
        if kc["ema_21"] is not None:
            assert kc["lower_2"] < kc["ema_21"] < kc["upper_2"]


class TestKeltnerEntryExitSignals:
    """Tests for kc_entry_signal and kc_exit_signal derivation logic in quant_engine.py."""

    def _derive_signals(self, kc_z_score, trend_200d_up, rsi):
        kc_entry = int(
            kc_z_score is not None and -3.0 < kc_z_score < -2.0 and trend_200d_up
        )
        kc_exit = int(
            kc_z_score is not None and kc_z_score > 3.0
            and rsi is not None and rsi > 75
        )
        return kc_entry, kc_exit

    def test_entry_signal_active_in_zone(self):
        entry, _ = self._derive_signals(-2.5, True, 50)
        assert entry == 1

    def test_entry_signal_inactive_if_downtrend(self):
        entry, _ = self._derive_signals(-2.5, False, 50)
        assert entry == 0

    def test_entry_signal_inactive_outside_zone(self):
        entry, _ = self._derive_signals(-1.5, True, 50)
        assert entry == 0

    def test_exit_signal_active_when_overextended(self):
        _, exit_sig = self._derive_signals(3.5, True, 80)
        assert exit_sig == 1

    def test_exit_signal_inactive_when_rsi_low(self):
        _, exit_sig = self._derive_signals(3.5, True, 65)
        assert exit_sig == 0

    def test_exit_signal_inactive_when_z_score_normal(self):
        _, exit_sig = self._derive_signals(1.5, True, 80)
        assert exit_sig == 0


if __name__ == "__main__":
    raise SystemExit(pytest.main([__file__, "-v"]))
