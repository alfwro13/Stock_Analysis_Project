"""
tests/test_position_sizing.py

Pure-math tests for calculate_position_size() in position_sizing.py.
No network calls; the function is self-contained.

Canonical formula (from docstring):
    risk_capital        = account_value * (risk_pct / 100)
    risk_per_share_native = entry_price * atr_pct * stop_multiple
    risk_per_share_base   = risk_per_share_native * fx_rate_to_base
    shares              = floor(risk_capital / risk_per_share_base)

Discrepancy note — spec arithmetic:
    The task brief states "floor(100 / (8 * 1.27)) = floor(9.84) = 10".
    The correct arithmetic is floor(9.842) = 9.  The code produces 9, which
    is the mathematically correct answer.  The FX conversion IS applied in the
    denominator (line 112 of position_sizing.py), so no xfail is needed.
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

from typing import Optional

import pytest

from position_sizing import calculate_position_size


# ---------------------------------------------------------------------------
# Same-currency baseline (fx_rate_to_base = 1.0)
# ---------------------------------------------------------------------------

class TestSameCurrency:
    """account=10000 GBP, entry=100 GBP, atr=4%, stop_multiple=2, risk=1%."""

    KWARGS = dict(
        account_value=10_000.0,
        entry_price=100.0,
        atr_pct=0.04,
        fx_rate_to_base=1.0,
        risk_pct=1.0,
        stop_multiple=2.0,
    )
    # risk_capital = 100, risk_per_share = 100 * 0.04 * 2 * 1.0 = 8, shares = 12

    def test_shares(self) -> None:
        result = calculate_position_size(**self.KWARGS)
        assert result["shares"] == 12

    def test_risk_per_share_native(self) -> None:
        result = calculate_position_size(**self.KWARGS)
        assert abs(result["risk_per_share_native"] - 8.0) < 1e-6

    def test_risk_per_share_base_equals_native_at_fx1(self) -> None:
        result = calculate_position_size(**self.KWARGS)
        assert abs(result["risk_per_share"] - result["risk_per_share_native"]) < 1e-6

    def test_stop_price(self) -> None:
        # stop = entry - entry * atr * multiple = 100 - 100 * 0.04 * 2 = 92
        result = calculate_position_size(**self.KWARGS)
        assert abs(result["stop_price"] - 92.0) < 1e-4

    def test_position_value_base(self) -> None:
        # 12 shares * 100 * 1.0 = 1200
        result = calculate_position_size(**self.KWARGS)
        assert abs(result["position_value"] - 1200.0) < 1e-2

    def test_risk_amount(self) -> None:
        # 12 shares * 8 GBP = 96 (≤ 100 risk_capital due to floor)
        result = calculate_position_size(**self.KWARGS)
        assert abs(result["risk_amount"] - 96.0) < 1e-2


# ---------------------------------------------------------------------------
# Cross-currency (USD stock, GBP account)
# This test encodes the correct formula and is the key regression for audit
# item 1e (FX must appear in the denominator when sizing cross-currency).
# ---------------------------------------------------------------------------

class TestCrossCurrencyFX:
    """Same inputs as above but fx_rate_to_base=1.27 (e.g. 1 USD ≈ 1.27 of base)."""

    KWARGS = dict(
        account_value=10_000.0,
        entry_price=100.0,
        atr_pct=0.04,
        fx_rate_to_base=1.27,
        risk_pct=1.0,
        stop_multiple=2.0,
    )
    # risk_capital = 100
    # risk_per_share_base = 100 * 0.04 * 2 * 1.27 = 10.16
    # shares = floor(100 / 10.16) = floor(9.842) = 9
    # NOTE: the task brief states "floor(9.84) = 10" — that is a typo.
    # floor(9.842) = 9 is correct.  The code produces 9 (FX is applied).

    def test_shares_fx_applied(self) -> None:
        """FX in denominator reduces shares vs same-currency case (12 → 9)."""
        result = calculate_position_size(**self.KWARGS)
        assert result["shares"] == 9

    def test_shares_fewer_than_same_currency(self) -> None:
        """Cross-currency position must be smaller than same-currency baseline."""
        same_ccy = calculate_position_size(
            account_value=10_000.0, entry_price=100.0, atr_pct=0.04,
            fx_rate_to_base=1.0, risk_pct=1.0, stop_multiple=2.0,
        )
        cross_ccy = calculate_position_size(**self.KWARGS)
        assert cross_ccy["shares"] < same_ccy["shares"]

    def test_risk_per_share_base_includes_fx(self) -> None:
        result = calculate_position_size(**self.KWARGS)
        expected = round(100.0 * 0.04 * 2.0 * 1.27, 4)
        assert abs(result["risk_per_share"] - expected) < 1e-3


# ---------------------------------------------------------------------------
# Degenerate / null inputs
# ---------------------------------------------------------------------------

class TestNullAndEdgeCases:

    def test_none_atr_returns_all_nulls(self) -> None:
        result = calculate_position_size(
            account_value=10_000.0, entry_price=100.0, atr_pct=None,
        )
        assert all(v is None for v in result.values())

    def test_zero_entry_price_returns_all_nulls(self) -> None:
        result = calculate_position_size(
            account_value=10_000.0, entry_price=0.0, atr_pct=0.04,
        )
        assert all(v is None for v in result.values())

    def test_zero_atr_returns_all_nulls(self) -> None:
        result = calculate_position_size(
            account_value=10_000.0, entry_price=100.0, atr_pct=0.0,
        )
        assert all(v is None for v in result.values())

    def test_zero_account_returns_all_nulls(self) -> None:
        result = calculate_position_size(
            account_value=0.0, entry_price=100.0, atr_pct=0.04,
        )
        assert all(v is None for v in result.values())

    def test_no_exception_on_zero_inputs(self) -> None:
        """Function must never raise; it should return a safe null dict."""
        for kwargs in [
            dict(account_value=0.0,     entry_price=100.0, atr_pct=0.04),
            dict(account_value=10_000.0, entry_price=0.0,  atr_pct=0.04),
            dict(account_value=10_000.0, entry_price=100.0, atr_pct=None),
            dict(account_value=10_000.0, entry_price=100.0, atr_pct=0.0),
        ]:
            result = calculate_position_size(**kwargs)  # must not raise
            assert isinstance(result, dict)

    def test_return_keys_always_present(self) -> None:
        """All documented keys must be present regardless of validity."""
        expected_keys = {
            "shares", "position_value", "stop_price",
            "risk_amount", "risk_per_share", "risk_per_share_native",
        }
        result = calculate_position_size(
            account_value=10_000.0, entry_price=100.0, atr_pct=None,
        )
        assert set(result.keys()) == expected_keys


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
