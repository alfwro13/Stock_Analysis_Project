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
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).parent.parent))

from typing import Optional

import pytest

import database as _db_module
from position_sizing import calculate_position_size, passes_risk_reward_gate, passes_risk_reward_gate_batch


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


# ---------------------------------------------------------------------------
# passes_risk_reward_gate() / passes_risk_reward_gate_batch()
# (Buy-Signal Confluence Pipeline Part D — Recommendation Risk/Reward Gate)
# ---------------------------------------------------------------------------

_RR_CFG = {"POSITION_SIZING": {"ACCOUNT_VALUE": 10000.0, "RISK_PCT": 1.0, "STOP_MULTIPLE": 2.0,
                                "MIN_RISK_REWARD": 1.5}}


def _seed_current_price(ticker: str, price: float) -> None:
    conn = _db_module.get_connection()
    conn.execute(
        "INSERT INTO stock_signals (ticker, current_price) VALUES (?, ?) "
        "ON CONFLICT(ticker) DO UPDATE SET current_price=excluded.current_price",
        (ticker, price),
    )
    conn.commit()
    conn.close()


def _seed_atr_pct(ticker: str, date_: str, atr_pct: float) -> None:
    conn = _db_module.get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO quant_signals (ticker, date, atr_pct) VALUES (?, ?, ?)",
        (ticker, date_, atr_pct),
    )
    conn.commit()
    conn.close()


def _seed_confirmed_pattern(ticker: str, family: str, pattern_type: str, measured_target: float) -> None:
    conn = _db_module.get_connection()
    conn.execute(
        """INSERT INTO pattern_detection_results (ticker, pattern_family, pattern_type, phase, measured_target, scan_ts)
           VALUES (?, ?, ?, 'CONFIRMED', ?, '2026-01-01 00:00:00')
           ON CONFLICT(ticker, pattern_family) DO UPDATE SET
               pattern_type=excluded.pattern_type, phase=excluded.phase, measured_target=excluded.measured_target""",
        (ticker, family, pattern_type, measured_target),
    )
    conn.commit()
    conn.close()


def _seed_quantile_q90(ticker: str, date_: str, price_q90: float) -> None:
    conn = _db_module.get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO quant_signals (ticker, date, price_q10, price_q90) VALUES (?, ?, ?, ?)",
        (ticker, date_, price_q90 - 1, price_q90),
    )
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _clean_rr_gate_tables():
    conn = _db_module.get_connection()
    conn.execute("DELETE FROM stock_signals WHERE ticker LIKE 'RRG%'")
    conn.execute("DELETE FROM quant_signals WHERE ticker LIKE 'RRG%'")
    conn.execute("DELETE FROM pattern_detection_results WHERE ticker LIKE 'RRG%'")
    conn.commit()
    conn.close()
    yield
    conn = _db_module.get_connection()
    conn.execute("DELETE FROM stock_signals WHERE ticker LIKE 'RRG%'")
    conn.execute("DELETE FROM quant_signals WHERE ticker LIKE 'RRG%'")
    conn.execute("DELETE FROM pattern_detection_results WHERE ticker LIKE 'RRG%'")
    conn.commit()
    conn.close()


class TestPassesRiskRewardGate:
    def test_missing_current_price_returns_none(self):
        _seed_atr_pct("RRG1", "2026-01-01", 0.04)
        with patch("config.load_config", return_value=_RR_CFG):
            assert passes_risk_reward_gate("RRG1") is None

    def test_missing_atr_pct_returns_none(self):
        _seed_current_price("RRG2", 100.0)
        with patch("config.load_config", return_value=_RR_CFG):
            assert passes_risk_reward_gate("RRG2") is None

    def test_no_take_profit_source_returns_none(self):
        _seed_current_price("RRG3", 100.0)
        _seed_atr_pct("RRG3", "2026-01-01", 0.04)
        with patch("config.load_config", return_value=_RR_CFG):
            assert passes_risk_reward_gate("RRG3") is None

    def test_bullish_pattern_target_used_and_passes(self):
        # entry=100, atr=0.04, stop_multiple=2 -> stop=92, risk=8
        # measured_target=120 -> reward=20, R:R=2.5 >= 1.5
        _seed_current_price("RRG4", 100.0)
        _seed_atr_pct("RRG4", "2026-01-01", 0.04)
        _seed_confirmed_pattern("RRG4", "flag", "bull_flag", 120.0)
        with patch("config.load_config", return_value=_RR_CFG):
            result = passes_risk_reward_gate("RRG4")
        assert result["take_profit_source"] == "pattern"
        assert result["take_profit"] == 120.0
        assert result["risk_reward"] == pytest.approx(2.5)
        assert result["passes"] is True

    def test_bearish_pattern_ignored_falls_back_to_quantile(self):
        _seed_current_price("RRG5", 100.0)
        _seed_atr_pct("RRG5", "2026-01-01", 0.04)
        _seed_confirmed_pattern("RRG5", "flag", "bear_flag", 80.0)
        _seed_quantile_q90("RRG5", "2026-01-02", 110.0)
        with patch("config.load_config", return_value=_RR_CFG):
            result = passes_risk_reward_gate("RRG5")
        assert result["take_profit_source"] == "quantile_q90"
        assert result["take_profit"] == 110.0

    def test_multiple_bullish_patterns_uses_highest_target(self):
        _seed_current_price("RRG6", 100.0)
        _seed_atr_pct("RRG6", "2026-01-01", 0.04)
        _seed_confirmed_pattern("RRG6", "flag", "bull_flag", 115.0)
        _seed_confirmed_pattern("RRG6", "triangle", "ascending", 130.0)
        with patch("config.load_config", return_value=_RR_CFG):
            result = passes_risk_reward_gate("RRG6")
        assert result["take_profit"] == 130.0

    def test_fails_when_below_min_risk_reward(self):
        # entry=100, stop=92, risk=8; target=104 -> reward=4, R:R=0.5 < 1.5
        _seed_current_price("RRG7", 100.0)
        _seed_atr_pct("RRG7", "2026-01-01", 0.04)
        _seed_quantile_q90("RRG7", "2026-01-02", 104.0)
        with patch("config.load_config", return_value=_RR_CFG):
            result = passes_risk_reward_gate("RRG7")
        assert result["passes"] is False
        assert result["risk_reward"] == pytest.approx(0.5)

    def test_min_rr_override_takes_precedence_over_config(self):
        _seed_current_price("RRG8", 100.0)
        _seed_atr_pct("RRG8", "2026-01-01", 0.04)
        _seed_quantile_q90("RRG8", "2026-01-02", 120.0)
        with patch("config.load_config", return_value=_RR_CFG):
            result = passes_risk_reward_gate("RRG8", min_rr=3.0)
        assert result["min_risk_reward"] == 3.0
        assert result["passes"] is False  # R:R=2.5 < 3.0

    def test_batch_matches_single_ticker_result(self):
        _seed_current_price("RRG9", 100.0)
        _seed_atr_pct("RRG9", "2026-01-01", 0.04)
        _seed_quantile_q90("RRG9", "2026-01-02", 120.0)
        with patch("config.load_config", return_value=_RR_CFG):
            batch_result = passes_risk_reward_gate_batch(["RRG9"])
            single_result = passes_risk_reward_gate("RRG9")
        assert batch_result["RRG9"] == single_result

    def test_empty_ticker_list(self):
        assert passes_risk_reward_gate_batch([]) == {}

    def test_unknown_ticker_returns_none(self):
        with patch("config.load_config", return_value=_RR_CFG):
            assert passes_risk_reward_gate("ZZZZRRG") is None


if __name__ == "__main__":
    import pytest
    raise SystemExit(pytest.main([__file__, "-v"]))
