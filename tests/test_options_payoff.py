"""
tests/test_options_payoff.py

Pure-math tests for calculate_payoff_matrix() in options_engine.py.
No network calls; the function is fully self-contained.

Grid: 500 price points spanning [current_price * 0.70, current_price * 1.30].
GRID_STEP ≈ 0.12 for current_price=100.  Zero-crossing assertions use a
tolerance of 2 × GRID_STEP so they don't depend on exact grid alignment.
"""

from typing import List, Optional, Dict

import numpy as np
import pytest

from options_engine import calculate_payoff_matrix


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CURRENT_PRICE: float = 100.0
GRID_STEP: float = (CURRENT_PRICE * 0.30 * 2) / 499  # ≈ 0.1202


def _prices(result: Dict) -> np.ndarray:
    return np.array(result["prices"])


def _payoffs(result: Dict) -> np.ndarray:
    return np.array(result["payoffs"])


def _zero_crossing_price(result: Dict) -> Optional[float]:
    """Return the price just before the payoff crosses from negative to positive."""
    px = _prices(result)
    py = _payoffs(result)
    idx = np.where(np.diff(np.sign(py)) > 0)[0]
    return float(px[idx[0]]) if len(idx) else None


# ---------------------------------------------------------------------------
# Long call: strike=100, premium=5, qty=1
# ---------------------------------------------------------------------------

class TestLongCall:
    LEG = [{"type": "call", "strike": 100, "premium": 5,
             "position": "long", "quantity": 1}]

    def setup_method(self) -> None:
        self.result = calculate_payoff_matrix(self.LEG, CURRENT_PRICE)
        self.px = _prices(self.result)
        self.py = _payoffs(self.result)

    def test_max_loss_well_below_strike(self) -> None:
        """Below strike the call expires worthless: payoff = (0 - 5) * 1 * 100 = -500."""
        below = self.py[self.px < 95]
        assert len(below) > 0
        np.testing.assert_allclose(below, -500.0, atol=0.1)

    def test_breakeven_near_105(self) -> None:
        """Zero crossing should be within two grid steps of strike + premium = 105."""
        crossing = _zero_crossing_price(self.result)
        assert crossing is not None, "Expected payoff to cross zero near 105"
        assert abs(crossing - 105.0) < GRID_STEP * 2

    def test_payoff_increases_above_strike(self) -> None:
        """Well above strike every grid step should add to profit."""
        high = self.py[self.px > 115]
        assert len(high) > 1
        assert np.all(np.diff(high) > 0)

    def test_payoff_is_500_point_list(self) -> None:
        assert len(self.result["prices"]) == 500
        assert len(self.result["payoffs"]) == 500


# ---------------------------------------------------------------------------
# Long put: strike=100, premium=5, qty=1
# ---------------------------------------------------------------------------

class TestLongPut:
    LEG = [{"type": "put", "strike": 100, "premium": 5,
             "position": "long", "quantity": 1}]

    def setup_method(self) -> None:
        self.result = calculate_payoff_matrix(self.LEG, CURRENT_PRICE)
        self.px = _prices(self.result)
        self.py = _payoffs(self.result)

    def test_max_loss_above_strike(self) -> None:
        """Above strike the put expires worthless: payoff = -500."""
        above = self.py[self.px > 105]
        assert len(above) > 0
        np.testing.assert_allclose(above, -500.0, atol=0.1)

    def test_breakeven_near_95(self) -> None:
        """Zero crossing should be within two grid steps of strike - premium = 95."""
        # Put payoff crosses 0 going negative→positive as price falls, so look
        # for a negative-to-positive crossing from right to left.
        idx = np.where(np.diff(np.sign(self.py)) < 0)[0]
        assert len(idx) > 0, "Expected payoff to cross zero near 95"
        crossing = float(self.px[idx[0]])
        assert abs(crossing - 95.0) < GRID_STEP * 2

    def test_payoff_increases_as_price_falls(self) -> None:
        """Well below strike every step down should add to profit."""
        low = self.py[self.px < 85]
        assert len(low) > 1
        assert np.all(np.diff(low) < 0)  # payoff increases as price decreases


# ---------------------------------------------------------------------------
# Short call: exact negation of long call
# ---------------------------------------------------------------------------

class TestShortCall:
    LEG_LONG = [{"type": "call", "strike": 100, "premium": 5,
                  "position": "long", "quantity": 1}]
    LEG_SHORT = [{"type": "call", "strike": 100, "premium": 5,
                   "position": "short", "quantity": 1}]

    def test_max_profit_below_strike(self) -> None:
        """Short call receives premium: payoff = +500 below strike."""
        result = calculate_payoff_matrix(self.LEG_SHORT, CURRENT_PRICE)
        px = _prices(result)
        py = _payoffs(result)
        below = py[px < 95]
        assert len(below) > 0
        np.testing.assert_allclose(below, 500.0, atol=0.1)

    def test_short_is_exact_negation_of_long(self) -> None:
        """Every price point: short_payoff == -long_payoff to floating-point precision."""
        long_r  = calculate_payoff_matrix(self.LEG_LONG, CURRENT_PRICE)
        short_r = calculate_payoff_matrix(self.LEG_SHORT, CURRENT_PRICE)
        long_py  = _payoffs(long_r)
        short_py = _payoffs(short_r)
        np.testing.assert_allclose(long_py + short_py, 0.0, atol=1e-9)


# ---------------------------------------------------------------------------
# Long straddle: long call + long put, both strike=100, premium=5
# Breakevens at 90 (lower) and 110 (upper).
# Max loss at strike = -(5+5)*100 = -1000.
# ---------------------------------------------------------------------------

class TestLongStraddle:
    LEGS = [
        {"type": "call", "strike": 100, "premium": 5,
         "position": "long", "quantity": 1},
        {"type": "put",  "strike": 100, "premium": 5,
         "position": "long", "quantity": 1},
    ]

    def setup_method(self) -> None:
        self.result = calculate_payoff_matrix(self.LEGS, CURRENT_PRICE)
        self.px = _prices(self.result)
        self.py = _payoffs(self.result)

    def test_max_loss_at_strike(self) -> None:
        """
        At price == strike both legs expire worthless: combined loss = -1000.
        The 500-point grid rarely lands exactly on 100, so we find the closest
        grid point and allow a tolerance proportional to its distance from 100.
        Each unit of price off-strike contributes ≤1 unit of intrinsic per leg,
        so 2 legs × distance × 100 multiplier is the max acceptable deviation.
        """
        closest_idx = int(np.argmin(np.abs(self.px - 100.0)))
        closest_price = float(self.px[closest_idx])
        closest_payoff = float(self.py[closest_idx])
        # Maximum payoff deviation = both legs' combined intrinsic at closest_price
        max_deviation = 2.0 * abs(closest_price - 100.0) * 100.0 + 1.0
        assert abs(closest_payoff - (-1000.0)) <= max_deviation, (
            f"Straddle payoff at {closest_price:.4f} was {closest_payoff:.2f}, "
            f"expected ≈ -1000 within {max_deviation:.1f}"
        )

    def test_profit_above_upper_breakeven(self) -> None:
        """Price > 111 (past upper breakeven + buffer): straddle is profitable."""
        high = self.py[self.px > 111.0]
        assert len(high) > 0
        assert np.all(high > 0), "Straddle should be profitable above 111"

    def test_profit_below_lower_breakeven(self) -> None:
        """Price < 89 (past lower breakeven + buffer): straddle is profitable."""
        low = self.py[self.px < 89.0]
        assert len(low) > 0
        assert np.all(low > 0), "Straddle should be profitable below 89"

    def test_combined_equals_sum_of_individual_legs(self) -> None:
        """Straddle payoff must equal the sum of the two individual single-leg payoffs."""
        call_r = calculate_payoff_matrix([self.LEGS[0]], CURRENT_PRICE)
        put_r  = calculate_payoff_matrix([self.LEGS[1]], CURRENT_PRICE)
        combined = np.array(call_r["payoffs"]) + np.array(put_r["payoffs"])
        np.testing.assert_allclose(_payoffs(self.result), combined, atol=1e-9)
