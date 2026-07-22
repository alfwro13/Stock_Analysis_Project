"""
tests/test_parabolic_stretch_engine.py — Parabolic Stretch (Rubber Band) detection math (the
Pattern Detection "parabolic_stretch" family). Orchestration (ticker scans, DB save/dedup,
scheduler wiring, chart API) is generic across every family and is covered by
tests/test_pattern_detection_engine.py.
"""

import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from indicators import compute_rsi, compute_volume_sma
from parabolic_stretch_engine import (
    _find_latest_stretch,
    phase_label,
    detect,
    FAMILY,
    PATTERN_TYPES,
)

_CONFIG = {"SCHEDULING": {"PATTERN_DETECTION": {}}, "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}}}


def _make_stretch_df(mode: str, seed: int = 3) -> pd.DataFrame:
    """460 bars of noisy sideways prices (enough to warm up a 200-day SMA plus a 252-day
    Z-score window), then a sharp 8-bar move: `mode` selects whether it's an overbought
    ('overbought_forming'/'overbought_confirmed') or oversold ('oversold_forming') stretch,
    or ('none') the price never moves sharply at all."""
    rng = np.random.RandomState(seed)
    base = 100.0 + np.cumsum(rng.normal(0, 0.3, 460))
    if mode == "none":
        prices = np.concatenate([base, rng.normal(0, 0.3, 9).cumsum() + base[-1]])
    elif mode == "overbought_forming":
        spike = base[-1] + np.cumsum(np.full(8, 1.5))
        tail = spike[-1] + rng.normal(0, 0.1, 1)
        prices = np.concatenate([base, spike, tail])
    elif mode == "overbought_confirmed":
        spike = base[-1] + np.cumsum(np.full(8, 1.5))
        reversion = spike[-1] - np.cumsum(np.full(6, 1.2))
        prices = np.concatenate([base, spike, reversion])
    elif mode == "oversold_forming":
        drop = base[-1] - np.cumsum(np.full(8, 1.5))
        tail = drop[-1] + rng.normal(0, 0.1, 1)
        prices = np.concatenate([base, drop, tail])
    else:
        raise ValueError(mode)

    volume = np.full(len(prices), 1_000_000.0)
    idx = pd.date_range("2023-01-01", periods=len(prices), freq="B")
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.2, "Low": prices - 0.2,
        "Close": prices, "Volume": volume,
    }, index=idx)


def _detect(df: pd.DataFrame, config: dict = _CONFIG) -> dict:
    rsi_series = compute_rsi(df["Close"])
    vol_sma = compute_volume_sma(df["Volume"])
    return detect("FAKE", df, rsi_series, vol_sma, config)


class TestFindLatestStretch:
    def test_no_breach_returns_none(self):
        z = np.array([0.5, -0.5, 1.0])
        valid = np.array([True, True, True])
        assert _find_latest_stretch(z, valid, 3.0, 30) is None

    def test_finds_overbought_breach(self):
        z = np.array([0.0, 3.5, 1.0])
        valid = np.array([True, True, True])
        assert _find_latest_stretch(z, valid, 3.0, 30) == (1, "overbought")

    def test_finds_oversold_breach(self):
        z = np.array([0.0, -3.5, 1.0])
        valid = np.array([True, True, True])
        assert _find_latest_stretch(z, valid, 3.0, 30) == (1, "oversold")

    def test_skips_invalid_entries(self):
        z = np.array([4.0, 4.0])
        valid = np.array([False, False])
        assert _find_latest_stretch(z, valid, 3.0, 30) is None


class TestDetectForming:
    def test_forming_overbought(self):
        result = _detect(_make_stretch_df("overbought_forming"))
        assert result is not None
        assert result["pattern_type"] == "parabolic_stretch_overbought"
        assert result["phase"] == "FORMING"
        assert result["breakout_date"] is None

    def test_forming_oversold(self):
        result = _detect(_make_stretch_df("oversold_forming"))
        assert result is not None
        assert result["pattern_type"] == "parabolic_stretch_oversold"
        assert result["phase"] == "FORMING"


class TestDetectConfirmed:
    def test_confirmed_overbought_reverts_toward_mean(self):
        result = _detect(_make_stretch_df("overbought_confirmed"))
        assert result is not None
        assert result["pattern_type"] == "parabolic_stretch_overbought"
        assert result["phase"] == "CONFIRMED"
        assert result["breakout_date"] is not None
        assert result["measured_target"] < result["breakout_price"]

    def test_pattern_r2_is_none(self):
        result = _detect(_make_stretch_df("overbought_confirmed"))
        assert result["pattern_r2"] is None


class TestDetectRejects:
    def test_no_stretch_no_result(self):
        assert _detect(_make_stretch_df("none")) is None

    def test_too_few_bars(self):
        df = _make_stretch_df("overbought_forming").iloc[:400]
        assert _detect(df) is None


class TestFamilyToggles:
    def test_overbought_disabled_suppresses_overbought_stretch(self):
        df = _make_stretch_df("overbought_forming")
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {"PARABOLIC_STRETCH": {"OVERBOUGHT_ENABLED": False}}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
        }
        assert _detect(df, config) is None

    def test_oversold_disabled_suppresses_oversold_stretch(self):
        df = _make_stretch_df("oversold_forming")
        config = {
            "SCHEDULING": {"PATTERN_DETECTION": {"PARABOLIC_STRETCH": {"OVERSOLD_ENABLED": False}}},
            "NOTIFICATIONS": {"PATTERN_DETECTION_ALERTS": {}},
        }
        assert _detect(df, config) is None


class TestPhaseLabel:
    def test_forming(self):
        assert phase_label("parabolic_stretch_overbought", "FORMING") == "Parabolic Stretch (Overbought) (Forming)"

    def test_confirmed(self):
        assert phase_label("parabolic_stretch_oversold", "CONFIRMED") == "Parabolic Stretch (Oversold) (Confirmed)"

    def test_none_pattern_falls_back_to_phase(self):
        assert phase_label(None, "FORMING") == "FORMING"


class TestRegistryContract:
    def test_family_and_pattern_types(self):
        assert FAMILY == "parabolic_stretch"
        assert PATTERN_TYPES == {"parabolic_stretch_overbought": "down", "parabolic_stretch_oversold": "up"}

    def test_lines_carry_path_not_straight_segment(self):
        result = _detect(_make_stretch_df("overbought_confirmed"))
        assert len(result["lines"]) == 1
        assert "path" in result["lines"][0]
        assert len(result["lines"][0]["path"]) >= 2
        assert len(result["points"]) == 2
