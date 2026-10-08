"""
tests/test_strategy_backtest_engine.py — Strategy Backtester simulation engine tests

Every expected number below is small enough to be worked out by hand: tiny price paths,
round capital, and round cost rates.

Covers:
  • Next-close execution: a move already known on the decision day earns nothing
  • Weight drift between rebalances (buy-and-hold keeps quantities, not weights)
  • Cost maths on both sides and budget conservation (initial entry, partial rebalance, liquidation)
  • Cadence decision dates, next-eligible execution across a non-tradable day
  • Tolerance-band triggering, rolling-strategy look-ahead protection, unavailable strategies
  • Insufficient warm-up, deterministic reruns, benchmark alignment and cost treatment
"""

import numpy as np
import pandas as pd
import pytest

from strategy_backtest_engine import (
    InsufficientHistory,
    _execute,
    cadence_indices,
    next_eligible,
    post_trade_equity,
    run_backtest,
)

CONFIG = {
    "cadence": "monthly",
    "lookback": 20,
    "initial_capital": 1000.0,
    "commission_bps": 0.0,
    "spread_bps": 0.0,
    "slippage_bps": 0.0,
    "cash_rate": 0.0,
    "band_pp": 5.0,
    "max_weight": 1.0,
    "cash_reserve": 0.0,
    "risk_free_rate": 0.0,
    "current_weights": {"A": 0.75, "B": 0.25},
}


def _frames(columns, n=60, start="2026-01-05"):
    index = pd.bdate_range(start, periods=n)
    prices = pd.DataFrame({c: np.full(n, 100.0) for c in columns}, index=index)
    real = pd.DataFrame(True, index=index, columns=columns)
    return prices, real


def _run(prices, real, ids, **overrides):
    return run_backtest(prices, real, None, None, ids, {**CONFIG, **overrides})


def _strategy(result, sid):
    assert result["strategies"][sid]["available"], result["strategies"][sid]
    return result["strategies"][sid]


class TestNextCloseExecution:
    def test_move_on_execution_day_earns_nothing(self):
        prices, real = _frames(["A", "B"])
        prices.loc[prices.index[1]:, "A"] = 200.0
        out = _strategy(_run(prices, real, ["buy_hold_ew"]), "buy_hold_ew")
        assert out["equity"].iloc[0] == pytest.approx(1000.0)
        assert out["equity"].iloc[1] == pytest.approx(1000.0)
        assert out["equity"].iloc[-1] == pytest.approx(1000.0)
        assert out["transactions"][0]["date"] == str(prices.index[1].date())

    def test_execution_waits_for_next_eligible_session(self):
        prices, real = _frames(["A", "B"])
        real.iloc[1, 0] = False
        out = _strategy(_run(prices, real, ["buy_hold_ew"]), "buy_hold_ew")
        assert out["transactions"][0]["date"] == str(prices.index[2].date())

    def test_next_eligible_helper(self):
        eligible = np.array([True, False, False, True, True])
        assert next_eligible(eligible, 0) == 3
        assert next_eligible(eligible, 4) is None


class TestDrift:
    def test_buy_and_hold_keeps_quantities_so_weights_drift(self):
        prices, real = _frames(["A", "B"])
        prices.loc[prices.index[5]:, "A"] = 200.0
        out = _strategy(_run(prices, real, ["buy_hold_ew"]), "buy_hold_ew")
        assert out["equity"].iloc[-1] == pytest.approx(1500.0)
        assert out["summary"]["final_weights"]["A"] == pytest.approx(2 / 3)
        assert out["summary"]["final_weights"]["B"] == pytest.approx(1 / 3)
        assert len(out["transactions"]) == 2

    def test_rebalanced_equal_weight_restores_weights_on_cadence(self):
        prices, real = _frames(["A", "B"], n=80)
        prices.loc[prices.index[5]:, "A"] = 200.0
        out = _strategy(_run(prices, real, ["rebalanced_ew"]), "rebalanced_ew")
        assert out["summary"]["final_weights"]["A"] == pytest.approx(0.5)
        assert out["equity"].iloc[-1] == pytest.approx(1500.0)
        assert any(t["side"] == "sell" and t["ticker"] == "A" for t in out["transactions"])


class TestCostsAndBudget:
    def test_initial_entry_cost_comes_out_of_the_budget(self):
        prices, real = _frames(["A", "B"])
        out = _strategy(_run(prices, real, ["buy_hold_ew"], commission_bps=100.0), "buy_hold_ew")
        assert out["equity"].iloc[1] == pytest.approx(990.0990099, rel=1e-8)
        assert out["summary"]["costs_total"] == pytest.approx(9.900990099, rel=1e-8)
        assert out["summary"]["gross_final_value"] == pytest.approx(1000.0)
        assert out["summary"]["cost_commission"] == pytest.approx(out["summary"]["costs_total"])

    def test_partial_rebalance_charges_both_sides(self):
        value = post_trade_equity(np.array([600.0, 400.0]), 1000.0, np.array([0.5, 0.5]), 0.01)
        assert value == pytest.approx(998.0)

    def test_liquidation_charges_the_sale(self):
        value = post_trade_equity(np.array([600.0, 400.0]), 1000.0, np.array([0.0, 0.0]), 0.01)
        assert value == pytest.approx(990.0)

    @pytest.mark.parametrize("rate", [0.0, 0.002, 0.01, 0.05])
    def test_execution_conserves_money_and_never_goes_negative(self, rate):
        rng = np.random.default_rng(7)
        units = rng.uniform(1, 10, 4)
        prices = rng.uniform(50, 150, 4)
        weights = np.array([0.4, 0.3, 0.2, 0.0])
        cash = 25.0
        new_units, new_cash, trade_value, traded, equity = _execute(units, cash, prices, weights, rate)
        assert new_cash >= 0
        assert float((new_units * prices).sum()) + new_cash + rate * traded == pytest.approx(equity)
        assert float((new_units * prices).sum()) == pytest.approx(weights.sum() * (equity - rate * traded))

    def test_cash_weight_is_preserved_not_normalised_away(self):
        prices, real = _frames(["A", "B", "C", "D"], n=80)
        rng = np.random.default_rng(3)
        for col in prices.columns:
            prices[col] = 100 * np.cumprod(1 + rng.normal(0, 0.01, len(prices)))
        out = _strategy(
            _run(prices, real, ["min_variance"], max_weight=0.4, cash_reserve=0.2), "min_variance"
        )
        assert out["cash_fraction"].iloc[-1] == pytest.approx(0.2, abs=0.02)
        assert out["summary"]["final_cash_weight"] == pytest.approx(0.2, abs=0.02)

    def test_cash_earns_the_configured_rate_while_waiting(self):
        prices, real = _frames(["A", "B"])
        out = _strategy(_run(prices, real, ["buy_hold_ew"], cash_rate=0.05), "buy_hold_ew")
        assert out["equity"].iloc[1] == pytest.approx(1000.0 * 1.05 ** (1 / 252))


class TestSchedules:
    def test_cadence_indices_pick_last_session_of_each_period(self):
        index = pd.bdate_range("2026-01-26", "2026-04-10")
        monthly = cadence_indices(index, 0, "monthly")
        assert [index[i].date().isoformat() for i in monthly] == ["2026-01-30", "2026-02-27", "2026-03-31"]
        quarterly = cadence_indices(index, 0, "quarterly")
        assert [index[i].date().isoformat() for i in quarterly] == ["2026-03-31"]

    def test_final_session_is_never_a_decision(self):
        index = pd.bdate_range("2026-01-26", "2026-01-30")
        assert cadence_indices(index, 0, "monthly") == []

    def test_decision_and_execution_timestamps_are_recorded(self):
        prices, real = _frames(["A", "B"])
        result = run_backtest(
            prices, real, None, None, ["buy_hold_ew"], CONFIG,
            close_utc=lambda ts: pd.Timestamp(ts).to_pydatetime().replace(hour=16),
        )
        decision = result["strategies"]["buy_hold_ew"]["decisions"][0]
        assert decision["decided_at_utc"].endswith("16:00:00")
        assert decision["executed_at_utc"].endswith("16:00:00")
        assert decision["status"] == "executed"
        assert decision["decision_date"] < decision["executed_date"]


class TestToleranceBand:
    def test_no_trade_while_drift_stays_inside_the_band(self):
        prices, real = _frames(["A", "B"])
        prices.loc[prices.index[5]:, "A"] = 110.0
        out = _strategy(_run(prices, real, ["tolerance_band"]), "tolerance_band")
        assert len(out["transactions"]) == 2

    def test_trades_back_to_target_when_band_breached(self):
        prices, real = _frames(["A", "B"])
        prices.loc[prices.index[5]:, "A"] = 140.0
        out = _strategy(_run(prices, real, ["tolerance_band"]), "tolerance_band")
        assert len(out["transactions"]) > 2
        assert out["summary"]["final_weights"]["A"] == pytest.approx(0.5)
        reasons = [d["reason"] for d in out["decisions"]]
        assert any("exceeded the 5 pp band" in r for r in reasons)


class TestRollingStrategies:
    def _random_prices(self, seed=11, n=140):
        prices, real = _frames(["A", "B", "C"], n=n)
        rng = np.random.default_rng(seed)
        for col in prices.columns:
            prices[col] = 100 * np.cumprod(1 + rng.normal(0.0004, 0.01 + 0.004 * ord(col) % 3, n))
        return prices, real

    @pytest.mark.parametrize("sid", ["min_variance", "inverse_vol", "max_sharpe"])
    def test_future_prices_never_change_earlier_decisions(self, sid):
        prices, real = self._random_prices()
        cfg = {"lookback": 40, "max_weight": 0.6}
        base = _run(prices, real, [sid], **cfg)["strategies"][sid]["decisions"]
        altered = prices.copy()
        altered.iloc[100:] = altered.iloc[100:] * 3.0
        changed = _run(altered, real, [sid], **cfg)["strategies"][sid]["decisions"]
        cutoff = str(prices.index[99].date())
        early_base = [d for d in base if d["decision_date"] <= cutoff]
        early_changed = [d for d in changed if d["decision_date"] <= cutoff]
        assert early_base and early_base == early_changed

    def test_min_variance_respects_weight_cap_and_sums_to_one(self):
        prices, real = self._random_prices()
        out = _strategy(_run(prices, real, ["min_variance"], lookback=40, max_weight=0.4), "min_variance")
        for decision in out["decisions"]:
            weights = list(decision["target"].values())
            assert sum(weights) == pytest.approx(1.0)
            assert max(weights) <= 0.4 + 1e-6
            assert min(weights) >= -1e-9

    def test_inverse_volatility_gives_calmer_ticker_more_weight(self):
        prices, real = _frames(["CALM", "WILD"], n=80)
        rng = np.random.default_rng(5)
        prices["CALM"] = 100 * np.cumprod(1 + rng.normal(0, 0.002, 80))
        prices["WILD"] = 100 * np.cumprod(1 + rng.normal(0, 0.02, 80))
        out = _strategy(_run(prices, real, ["inverse_vol"], lookback=30), "inverse_vol")
        target = out["decisions"][0]["target"]
        assert target["CALM"] > target["WILD"]
        assert sum(target.values()) == pytest.approx(1.0)

    def test_max_sharpe_unavailable_when_no_mix_beats_the_risk_free_rate(self):
        prices, real = _frames(["A", "B"], n=80)
        rng = np.random.default_rng(2)
        for col in prices.columns:
            prices[col] = 100 * np.cumprod(1 + rng.normal(-0.003, 0.01, 80))
        result = _run(prices, real, ["max_sharpe"], lookback=30)
        assert result["strategies"]["max_sharpe"]["available"] is False
        assert "risk-free" in result["strategies"]["max_sharpe"]["reason"]

    def test_zero_volatility_makes_inverse_vol_unavailable(self):
        prices, real = _frames(["A", "B"], n=80)
        result = _run(prices, real, ["inverse_vol"], lookback=30)
        assert result["strategies"]["inverse_vol"]["available"] is False

    def test_current_weights_strategy_uses_supplied_weights(self):
        prices, real = _frames(["A", "B"])
        out = _strategy(_run(prices, real, ["current_weights"]), "current_weights")
        assert out["decisions"][0]["target"] == pytest.approx({"A": 0.75, "B": 0.25})


class TestHistoryAndDeterminism:
    def test_insufficient_warmup_raises_with_an_explanation(self):
        prices, real = _frames(["A", "B"], n=50)
        with pytest.raises(InsufficientHistory, match="warm-up"):
            _run(prices, real, ["min_variance"], lookback=40)

    def test_common_start_uses_the_longest_warmup_of_the_selected_strategies(self):
        prices, real = _frames(["A", "B"], n=120)
        rng = np.random.default_rng(1)
        for col in prices.columns:
            prices[col] = 100 * np.cumprod(1 + rng.normal(0.0003, 0.01, 120))
        result = _run(prices, real, ["buy_hold_ew", "inverse_vol"], lookback=50)
        assert result["start_index"] == 50
        assert len(result["strategies"]["buy_hold_ew"]["equity"]) == 70

    def test_reruns_are_deterministic(self):
        prices, real = _frames(["A", "B", "C"], n=140)
        rng = np.random.default_rng(9)
        for col in prices.columns:
            prices[col] = 100 * np.cumprod(1 + rng.normal(0.0004, 0.01, 140))
        ids = ["buy_hold_ew", "rebalanced_ew", "min_variance", "max_sharpe", "inverse_vol", "tolerance_band"]
        first = _run(prices, real, ids, lookback=40, max_weight=0.6, commission_bps=10.0)
        second = _run(prices, real, ids, lookback=40, max_weight=0.6, commission_bps=10.0)
        for sid in ids:
            a, b = first["strategies"][sid], second["strategies"][sid]
            if a["available"]:
                pd.testing.assert_series_equal(a["equity"], b["equity"])
                assert a["transactions"] == b["transactions"]


class TestBenchmark:
    def test_benchmark_enters_on_the_same_day_and_pays_the_same_cost(self):
        prices, real = _frames(["A", "B"])
        bench = pd.Series(100.0, index=prices.index)
        bench_real = pd.Series(True, index=prices.index)
        result = run_backtest(
            prices, real, bench, bench_real, ["buy_hold_ew"], {**CONFIG, "commission_bps": 100.0}
        )
        assert result["benchmark"]["equity"].iloc[1] == pytest.approx(1000.0 / 1.01)
        strategy = result["strategies"]["buy_hold_ew"]["equity"]
        assert strategy.index.equals(result["benchmark"]["equity"].index)
        assert result["benchmark"]["equity"].iloc[1] == pytest.approx(strategy.iloc[1], rel=1e-9)

    def test_benchmark_tracks_its_own_price_after_entry(self):
        prices, real = _frames(["A", "B"])
        bench = pd.Series(100.0, index=prices.index)
        bench.iloc[10:] = 120.0
        bench_real = pd.Series(True, index=prices.index)
        result = run_backtest(prices, real, bench, bench_real, ["buy_hold_ew"], CONFIG)
        assert result["benchmark"]["equity"].iloc[-1] == pytest.approx(1200.0)
