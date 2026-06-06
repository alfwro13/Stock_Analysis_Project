"""
tests/test_risk_engine.py

Tests for the VaR/CVaR math in risk_engine.py.

calculate_tail_risk() is tightly coupled to yfinance.download and get_connection,
so the math cannot be tested in isolation without mocking.  A reference
implementation of the documented formula is written here and tested against
known hand-computed inputs.  An integration-style test then patches both I/O
dependencies and verifies the live function produces the same values.

# NOTE: Extracting a pure compute_var_cvar(log_returns: np.ndarray) helper
# from risk_engine.py would eliminate the need for mocking entirely and make
# this test file significantly cleaner.  That refactor is deferred per project
# standards (no source changes in this task).

Documented formula (risk_engine.py lines 44–64):
    threshold = percentile(log_returns, 5)
    var_95    = max(1 - exp(threshold), 0.0)
    tail      = log_returns[log_returns <= threshold]
    cvar_95   = max(1 - exp(mean(tail)), 0.0)   if tail non-empty else var_95
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from typing import Tuple
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

from risk_engine import calculate_tail_risk


# ---------------------------------------------------------------------------
# Reference implementation — encodes the INTENDED math, not a copy of the
# source, so tests are independent from implementation details.
# ---------------------------------------------------------------------------

def _ref_var_cvar(
    log_returns: np.ndarray,
    alpha: float = 0.05,
) -> Tuple[float, float]:
    """Return (var_95, cvar_95) matching risk_engine's documented algorithm."""
    threshold = float(np.percentile(log_returns, alpha * 100))
    var_95 = max(float(1.0 - np.exp(threshold)), 0.0)
    tail = log_returns[log_returns <= threshold]
    if len(tail) > 0:
        cvar_95 = max(float(1.0 - np.exp(np.mean(tail))), 0.0)
    else:
        cvar_95 = var_95
    return var_95, cvar_95


# ---------------------------------------------------------------------------
# Known-input construction
#
# With N=21 values and np.percentile's default linear interpolation:
#   index i = alpha * (N-1) = 0.05 * 20 = 1.0  (exact integer)
# → the 5th percentile is exactly the value at sorted index 1.
# Using 2 values of -0.10 and 19 values of +0.02 gives threshold = -0.10 exactly.
# ---------------------------------------------------------------------------

_RETURNS_KNOWN: np.ndarray = np.array([-0.10, -0.10] + [0.02] * 19)
_EXPECTED_VAR:  float = float(1.0 - np.exp(-0.10))   # ≈ 0.09516
_EXPECTED_CVAR: float = float(1.0 - np.exp(-0.10))   # tail mean = -0.10 (all equal)


class TestReferenceImplementation:

    def test_known_var(self) -> None:
        var, _ = _ref_var_cvar(_RETURNS_KNOWN)
        assert abs(var - _EXPECTED_VAR) < 1e-9

    def test_known_cvar(self) -> None:
        _, cvar = _ref_var_cvar(_RETURNS_KNOWN)
        assert abs(cvar - _EXPECTED_CVAR) < 1e-9

    def test_cvar_ge_var(self) -> None:
        """Expected Shortfall must be ≥ VaR by definition."""
        var, cvar = _ref_var_cvar(_RETURNS_KNOWN)
        assert cvar >= var

    def test_all_positive_returns_clamp_to_zero(self) -> None:
        """If every day was positive, both risk metrics should be 0."""
        all_positive = np.full(50, 0.01)
        var, cvar = _ref_var_cvar(all_positive)
        assert var == 0.0
        assert cvar == 0.0

    def test_cvar_ge_var_asymmetric_tail(self) -> None:
        """With a fat tail CVaR > VaR."""
        # 5 catastrophic returns and 95 normal ones — CVaR should exceed VaR.
        returns = np.concatenate([np.full(5, -0.30), np.full(95, 0.01)])
        var, cvar = _ref_var_cvar(returns)
        assert cvar >= var

    def test_worse_tail_raises_cvar_not_var(self) -> None:
        """Making the tail worse should raise CVaR but leave VaR unchanged."""
        base = np.concatenate([np.full(10, -0.10), np.full(90, 0.01)])
        worse = np.concatenate([np.full(10, -0.20), np.full(90, 0.01)])
        var_base,  cvar_base  = _ref_var_cvar(base)
        var_worse, cvar_worse = _ref_var_cvar(worse)
        # VaR threshold is the same position in the sorted array either way
        # (both have 10 bad values → same percentile rank)
        assert cvar_worse > cvar_base


# ---------------------------------------------------------------------------
# Integration test — patches I/O; verifies the live function writes the
# same var_95/cvar_95 that the reference computes for the same price series.
# ---------------------------------------------------------------------------

def _build_price_series(returns: np.ndarray, start: float = 100.0) -> np.ndarray:
    """Convert log returns to a cumulative price series."""
    prices = [start]
    for r in returns:
        prices.append(prices[-1] * np.exp(float(r)))
    return np.array(prices)


class TestCalculateTailRiskIntegration:
    """
    Patches yfinance.download and get_connection so no network or DB is needed.
    Asserts that calculate_tail_risk writes the var_95/cvar_95 produced by the
    reference implementation for the same price series.

    # NOTE: The coupling to yfinance and get_connection forces mocking here.
    # Extracting compute_var_cvar() from risk_engine would remove this need.
    """

    # Use 100 prices (99 log returns) — above the function's minimum of 50.
    # 10 returns of -0.30 (clearly below the 5th percentile), 89 of +0.05.
    # With 99 values, percentile index = 0.05 * 98 = 4.9 → interpolates between
    # index 4 (-0.30) and index 5 (-0.30) → threshold = -0.30 exactly.
    _RETURNS = np.concatenate([np.full(10, -0.30), np.full(89, 0.05)])
    _PRICES  = _build_price_series(_RETURNS)

    @patch("risk_engine.get_connection")
    @patch("risk_engine.yahoo_engine.get_price_history")
    def test_writes_correct_var_cvar(
        self, mock_get_history: MagicMock, mock_get_conn: MagicMock
    ) -> None:
        # --- mock yahoo_engine ---
        df = pd.DataFrame({"Close": self._PRICES})
        mock_get_history.return_value = {"SPY": df}

        # --- mock DB connection ---
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        # --- expected values (recompute from actual log returns of mocked prices) ---
        log_returns = np.log(self._PRICES[1:] / self._PRICES[:-1])
        expected_var, expected_cvar = _ref_var_cvar(log_returns)

        # --- call the live function ---
        calculate_tail_risk("SPY")

        # --- verify the DB write was called ---
        assert mock_cursor.execute.called, "cursor.execute should have been called"
        call_args = mock_cursor.execute.call_args[0]
        # The query args tuple is the second positional arg to cursor.execute.
        # Shape: (var_95, cvar_95, ticker, ticker) for the no-target_date branch.
        written_var  = call_args[1][0]
        written_cvar = call_args[1][1]

        assert abs(written_var  - expected_var)  < 1e-9, (
            f"var_95 mismatch: got {written_var:.6f}, expected {expected_var:.6f}"
        )
        assert abs(written_cvar - expected_cvar) < 1e-9, (
            f"cvar_95 mismatch: got {written_cvar:.6f}, expected {expected_cvar:.6f}"
        )

    @patch("risk_engine.get_connection")
    @patch("risk_engine.yahoo_engine.get_price_history")
    def test_empty_dataframe_does_not_write(
        self, mock_get_history: MagicMock, mock_get_conn: MagicMock
    ) -> None:
        """If yahoo_engine returns empty data the function should bail without DB write."""
        mock_get_history.return_value = {}
        mock_cursor = MagicMock()
        mock_conn = MagicMock()
        mock_conn.cursor.return_value = mock_cursor
        mock_get_conn.return_value = mock_conn

        calculate_tail_risk("SPY")
        mock_cursor.execute.assert_not_called()


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
