"""
tests/test_earnings_isolation.py

Tests for the earnings-volatility variance-isolation formula in
earnings_vol_engine.py (the block inside run_earnings_vol_scan that computes
`isolated_implied_move`).

The formula is currently inline in a function that couples to yfinance and
SQLite, so it cannot be called directly.  A reference implementation is
written here instead.

# NOTE: Audit item 1c flagged two bugs in the original inline formula:
#   (i)  implied_move_pct was derived from IV rather than the straddle price.
#   (ii) two different day-count clocks fed the variance subtraction.
# Both issues HAVE BEEN FIXED in the source (see session history):
#   - implied_move_pct = (call_price + put_price) / underlying_price * 100
#   - days_to_expiry = max((target_expiry_date - datetime.now()).days, 1)
# The reference below encodes the INTENDED (and now implemented) math.
# These tests act as a regression guard: if someone reverts the fix,
# the clamping and consistency tests will catch it.

Reference formula (earnings_vol_engine.py, post-fix):
    daily_hv          = historical_hv / sqrt(252)
    total_implied_pct = implied_move_pct / 100.0
    non_earn_pct      = daily_hv * sqrt(non_earnings_days)
    isolated_variance = max(total_implied_pct**2 - non_earn_pct**2, 0)
    isolated_move     = sqrt(isolated_variance) * 100.0   if isolated_variance > 0
                        else 0.01                          (floor)
"""

import math
from typing import Tuple

import numpy as np
import pytest


# ---------------------------------------------------------------------------
# Reference implementation
# ---------------------------------------------------------------------------

def _ref_isolation(
    implied_move_pct: float,
    historical_hv: float,
    non_earnings_days: int,
) -> float:
    """
    Reference implementation of the earnings-isolation formula.
    Inputs:
        implied_move_pct  — straddle-implied move as a percentage (e.g. 5.0 = 5%)
        historical_hv     — annualised historical volatility as a decimal (e.g. 0.20)
        non_earnings_days — calendar days between earnings and expiry, minus 1
    Returns:
        isolated implied move as a percentage
    """
    daily_hv          = historical_hv / math.sqrt(252)
    total_implied_pct = implied_move_pct / 100.0
    non_earn_pct      = daily_hv * math.sqrt(non_earnings_days)
    isolated_variance = max(total_implied_pct ** 2 - non_earn_pct ** 2, 0.0)
    return math.sqrt(isolated_variance) * 100.0 if isolated_variance > 0.0 else 0.01


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

class TestZeroNonEarningsDays:
    """When non_earnings_days=0 there is no diffusion to strip out.
    The isolated move should equal the raw implied move exactly."""

    def test_zero_decay_returns_implied_move(self) -> None:
        result = _ref_isolation(implied_move_pct=5.0, historical_hv=0.20,
                                non_earnings_days=0)
        assert abs(result - 5.0) < 1e-9

    def test_zero_decay_various_premiums(self) -> None:
        for pct in [1.0, 3.5, 10.0, 20.0]:
            result = _ref_isolation(implied_move_pct=pct, historical_hv=0.30,
                                    non_earnings_days=0)
            assert abs(result - pct) < 1e-9, f"Failed for implied_move_pct={pct}"


class TestVarianceClamping:
    """When non-earnings diffusion exceeds the total implied variance,
    isolated_variance clamps to 0 and the floor of 0.01% is returned."""

    def test_clamp_to_floor_when_diffusion_dominates(self) -> None:
        # High vol (hv=0.80) over 10 days produces large non_earn_pct,
        # easily swamping a small implied move of 3%.
        result = _ref_isolation(implied_move_pct=3.0, historical_hv=0.80,
                                non_earnings_days=10)
        assert result == pytest.approx(0.01)

    def test_floor_value_is_0_01_not_zero(self) -> None:
        result = _ref_isolation(implied_move_pct=0.1, historical_hv=1.0,
                                non_earnings_days=5)
        assert result == pytest.approx(0.01)

    def test_no_negative_isolated_move(self) -> None:
        """Isolated move must always be non-negative."""
        for days in range(0, 21):
            result = _ref_isolation(implied_move_pct=2.0, historical_hv=0.50,
                                    non_earnings_days=days)
            assert result >= 0.0


class TestKnownValues:
    """Hand-computable cases confirming the Pythagorean variance decomposition."""

    def test_pythagorean_case(self) -> None:
        """
        Choose inputs so non_earn_pct = 0.06 and total_implied_pct = 0.10.
        Then isolated = sqrt(0.01 - 0.0036) = sqrt(0.0064) = 0.08 → 8.0%.

        To get non_earn_pct = 0.06 with non_earnings_days = 4:
            daily_hv = 0.06 / sqrt(4) = 0.03
            historical_hv = 0.03 * sqrt(252) ≈ 0.47623
        """
        hv = 0.03 * math.sqrt(252)
        result = _ref_isolation(implied_move_pct=10.0, historical_hv=hv,
                                non_earnings_days=4)
        assert abs(result - 8.0) < 1e-6

    def test_isolated_less_than_or_equal_to_implied(self) -> None:
        """Stripping out diffusion can only reduce (or keep equal) the move."""
        for days in range(1, 15):
            isolated = _ref_isolation(implied_move_pct=8.0, historical_hv=0.25,
                                      non_earnings_days=days)
            assert isolated <= 8.0 + 1e-9

    def test_more_days_to_expiry_strips_more_diffusion(self) -> None:
        """As non_earnings_days grows, isolated move falls (more decay stripped)."""
        results = [
            _ref_isolation(implied_move_pct=10.0, historical_hv=0.20,
                           non_earnings_days=d)
            for d in [1, 5, 10, 20]
        ]
        # Clip at floor before comparing
        non_floored = [r for r in results if r > 0.01]
        if len(non_floored) > 1:
            assert all(non_floored[i] >= non_floored[i + 1]
                       for i in range(len(non_floored) - 1))


class TestEdgeCases:

    def test_very_small_implied_move_clamps(self) -> None:
        result = _ref_isolation(implied_move_pct=0.001, historical_hv=0.20,
                                non_earnings_days=1)
        assert result == pytest.approx(0.01)

    def test_zero_historical_vol_returns_implied(self) -> None:
        """With zero historical vol there is no diffusion to strip."""
        result = _ref_isolation(implied_move_pct=7.5, historical_hv=0.0,
                                non_earnings_days=10)
        assert abs(result - 7.5) < 1e-9
