"""
tests/test_fundamentals_helpers.py  ── FUNDAMENTALS HELPERS

Covers calculate_peter_lynch_peg() for all guard paths, PE selection logic,
decimal-to-percentage scaling, and denominator overflow protection.
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from fundamentals_helpers import calculate_peter_lynch_peg


# ──────────────────────────────────────────────────────────────────────────────
# Happy-path: known value
# ──────────────────────────────────────────────────────────────────────────────

class TestPegHappyPath:

    def test_basic_calculation(self):
        """PE=20, growth=20% decimal, no dividend → PEG = 20 / 20 = 1.0."""
        result = calculate_peter_lynch_peg(
            forward_pe=20.0,
            trailing_pe=None,
            earnings_growth=0.20,
            dividend_yield=None,
        )
        assert result == pytest.approx(1.0)

    def test_yield_adjusted_peg(self):
        """PE=15, growth=10%, yield=5% → denominator=15 → PEG = 15/15 = 1.0."""
        result = calculate_peter_lynch_peg(
            forward_pe=15.0,
            trailing_pe=None,
            earnings_growth=0.10,
            dividend_yield=0.05,
        )
        assert result == pytest.approx(1.0)

    def test_zero_dividend_yield_same_as_none(self):
        """Explicit 0.0 dividend_yield must give same result as None."""
        r_zero = calculate_peter_lynch_peg(20.0, None, 0.20, 0.0)
        r_none = calculate_peter_lynch_peg(20.0, None, 0.20, None)
        assert r_zero == pytest.approx(r_none)

    def test_low_peg_below_one(self):
        """Growth stock: PE=10, growth=25% → PEG = 10/25 = 0.40."""
        result = calculate_peter_lynch_peg(10.0, None, 0.25, None)
        assert result == pytest.approx(0.40)

    def test_result_is_always_positive(self):
        """With valid inputs the returned PEG ratio must always be > 0."""
        result = calculate_peter_lynch_peg(30.0, None, 0.15, 0.02)
        assert result is not None and result > 0


# ──────────────────────────────────────────────────────────────────────────────
# PE selection logic
# ──────────────────────────────────────────────────────────────────────────────

class TestPeSelection:

    def test_forward_pe_preferred_over_trailing(self):
        """Forward PE must be used when it is positive, even if trailing is also set."""
        r_forward = calculate_peter_lynch_peg(10.0, 30.0, 0.20, None)
        assert r_forward == pytest.approx(10.0 / 20.0)

    def test_falls_back_to_trailing_when_forward_none(self):
        """When forward_pe is None, trailing_pe must be used."""
        result = calculate_peter_lynch_peg(None, 25.0, 0.25, None)
        assert result == pytest.approx(25.0 / 25.0)

    def test_falls_back_to_trailing_when_forward_zero(self):
        """forward_pe=0 is treated as unavailable; trailing used instead."""
        result = calculate_peter_lynch_peg(0.0, 20.0, 0.20, None)
        assert result == pytest.approx(20.0 / 20.0)

    def test_falls_back_to_trailing_when_forward_negative(self):
        """Negative forward PE (loss-making) → falls back to trailing PE."""
        result = calculate_peter_lynch_peg(-5.0, 18.0, 0.18, None)
        assert result == pytest.approx(18.0 / 18.0)

    def test_both_pe_none_returns_none(self):
        assert calculate_peter_lynch_peg(None, None, 0.20, None) is None

    def test_forward_none_trailing_negative_returns_none(self):
        """Trailing PE negative → loss-making, no valid PEG possible."""
        assert calculate_peter_lynch_peg(None, -10.0, 0.20, None) is None

    def test_forward_none_trailing_zero_returns_none(self):
        assert calculate_peter_lynch_peg(None, 0.0, 0.20, None) is None


# ──────────────────────────────────────────────────────────────────────────────
# Earnings growth guards
# ──────────────────────────────────────────────────────────────────────────────

class TestEarningsGrowthGuards:

    def test_none_earnings_growth_returns_none(self):
        assert calculate_peter_lynch_peg(20.0, None, None, None) is None

    def test_zero_earnings_growth_returns_none(self):
        """Zero growth makes PEG infinite — must return None."""
        assert calculate_peter_lynch_peg(20.0, None, 0.0, None) is None

    def test_negative_earnings_growth_returns_none(self):
        """Loss-making company — Lynch PEG is undefined."""
        assert calculate_peter_lynch_peg(20.0, None, -0.10, None) is None

    def test_tiny_positive_growth_is_accepted(self):
        """Even 0.1% growth is valid — should not be filtered."""
        result = calculate_peter_lynch_peg(20.0, None, 0.001, None)
        assert result is not None and result > 0


# ──────────────────────────────────────────────────────────────────────────────
# Decimal-to-percentage scaling
# ──────────────────────────────────────────────────────────────────────────────

class TestDecimalScaling:

    def test_earnings_growth_scaled_to_percent(self):
        """0.15 earnings_growth must be treated as 15%, not 0.15%."""
        result = calculate_peter_lynch_peg(15.0, None, 0.15, None)
        # denominator = 15.0 (15%), not 0.15
        assert result == pytest.approx(15.0 / 15.0)
        assert result != pytest.approx(15.0 / 0.15), "growth must be scaled ×100"

    def test_dividend_yield_scaled_to_percent(self):
        """0.03 dividend_yield must be treated as 3%, adding 3 percentage points."""
        result_with = calculate_peter_lynch_peg(20.0, None, 0.17, 0.03)
        # denominator = 17 + 3 = 20 → PEG = 1.0
        assert result_with == pytest.approx(1.0)

    def test_large_growth_reduces_peg(self):
        """Higher growth rate must produce lower (better) PEG."""
        low_growth  = calculate_peter_lynch_peg(20.0, None, 0.10, None)
        high_growth = calculate_peter_lynch_peg(20.0, None, 0.40, None)
        assert high_growth < low_growth


# ──────────────────────────────────────────────────────────────────────────────
# Denominator overflow / pathological data
# ──────────────────────────────────────────────────────────────────────────────

class TestDenominatorGuard:

    def test_large_negative_dividend_yield_returns_none(self):
        """
        If yfinance returns a large negative dividend_yield (bad data) that
        swamps positive earnings growth, total_growth_yield <= 0 → must return None.
        """
        result = calculate_peter_lynch_peg(
            forward_pe=20.0,
            trailing_pe=None,
            earnings_growth=0.01,   # eg_scaled = 1.0
            dividend_yield=-0.05,   # div_yield_scaled = -5.0 → total = -4.0
        )
        assert result is None

    def test_negative_dividend_slightly_above_zero_denom_accepted(self):
        """
        Negative dividend_yield that only partially reduces total_growth_yield
        but leaves it positive → result must still be returned.
        """
        # eg_scaled = 20.0, div_yield_scaled = -5.0 → total = 15.0
        result = calculate_peter_lynch_peg(20.0, None, 0.20, -0.05)
        assert result == pytest.approx(20.0 / 15.0)


# ──────────────────────────────────────────────────────────────────────────────
# Parametrized guard matrix
# ──────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize("fwd, trail, growth, div", [
    (None,  None,  0.20,  None),   # no PE at all
    (None,  -5.0,  0.20,  None),   # trailing negative
    (None,  0.0,   0.20,  None),   # trailing zero
    (-1.0,  None,  0.20,  None),   # forward negative, no fallback
    (20.0,  None,  None,  None),   # no growth
    (20.0,  None,  0.0,   None),   # zero growth
    (20.0,  None,  -0.10, None),   # negative growth
    (20.0,  None,  0.01,  -0.05),  # neg dividend swamps growth
])
def test_returns_none_for_invalid_inputs(fwd, trail, growth, div):
    assert calculate_peter_lynch_peg(fwd, trail, growth, div) is None
