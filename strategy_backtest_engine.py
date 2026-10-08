# GUI name: "Strategy Backtester". Pure close-to-close simulation: callers supply aligned prices and an eligibility mask; nothing here fetches or persists.

import math
from datetime import datetime
from typing import Callable, Dict, List, Optional

import numpy as np
import pandas as pd

from performance_analytics_engine import compute_return_metrics
from portfolio_optimizer_engine import TRADING_DAYS
from strategy_backtest_strategies import (
    SCHEDULE_CADENCE,
    SCHEDULE_EVERY_SESSION,
    STRATEGIES,
    DecisionContext,
    Strategy,
)

CADENCES = ("monthly", "quarterly", "annual")
MIN_TEST_SESSIONS = 30
MIN_METRIC_SESSIONS = 30
BENCHMARK_ID = "benchmark"
UTC_FORMAT = "%Y-%m-%d %H:%M:%S"
CASH_TOLERANCE = 1e-9


class InsufficientHistory(ValueError):
    pass


def cost_rate(config: dict) -> float:
    return (config["commission_bps"] + config["spread_bps"] + config["slippage_bps"]) / 10000.0


def cadence_indices(dates: pd.DatetimeIndex, start: int, cadence: str) -> List[int]:
    """Last session of every month/quarter/year strictly after `start`; the final session is never a decision since nothing can execute after it."""
    def key(ts):
        if cadence == "monthly":
            return (ts.year, ts.month)
        if cadence == "quarterly":
            return (ts.year, (ts.month - 1) // 3)
        return (ts.year,)
    return [i for i in range(start + 1, len(dates) - 1) if key(dates[i]) != key(dates[i + 1])]


def next_eligible(eligible: np.ndarray, after: int) -> Optional[int]:
    later = np.flatnonzero(eligible[after + 1:])
    return int(after + 1 + later[0]) if later.size else None


def post_trade_equity(holdings: np.ndarray, equity: float, weights: np.ndarray, rate: float) -> float:
    """Solves V + rate·Σ|wV − h| = E: the equity left after paying costs on the trades needed to reach the target weights."""
    if rate == 0:
        return equity
    lo, hi = 0.0, equity
    for _ in range(200):
        mid = (lo + hi) / 2.0
        if mid + rate * float(np.abs(weights * mid - holdings).sum()) < equity:
            lo = mid
        else:
            hi = mid
    return (lo + hi) / 2.0


def _execute(units: np.ndarray, cash: float, prices: np.ndarray, weights: np.ndarray, rate: float):
    holdings = units * prices
    equity = float(holdings.sum()) + cash
    value = post_trade_equity(holdings, equity, weights, rate)
    trade_value = weights * value - holdings
    traded = float(np.abs(trade_value).sum())
    cost = rate * traded
    new_cash = cash - float(trade_value.sum()) - cost
    if new_cash < -CASH_TOLERANCE * equity:
        raise ArithmeticError(f"Execution left negative cash ({new_cash:.6f}).")
    return units + trade_value / prices, max(new_cash, 0.0), trade_value, traded, equity


def _simulate(
    strategy: Strategy, prices: pd.DataFrame, eligible: np.ndarray, start: int, decisions_at: set,
    config: dict, rate: float, close_utc: Optional[Callable[[pd.Timestamp], datetime]],
) -> Dict:
    dates = prices.index
    px = prices.to_numpy(dtype=float)
    n_dates, n = px.shape
    units = np.zeros(n)
    cash = float(config["initial_capital"])
    growth = (1.0 + config["cash_rate"]) ** (1.0 / TRADING_DAYS)
    horizon = n_dates - start
    equity = np.empty(horizon)
    cash_fraction = np.empty(horizon)
    weights_path = np.empty((horizon, n))
    transactions: List[Dict] = []
    decisions: List[Dict] = []
    executions: List[Dict] = []
    state: dict = {}
    pending: Optional[Dict] = None

    def stamp(idx: int) -> Optional[str]:
        return close_utc(dates[idx]).strftime(UTC_FORMAT) if close_utc else None

    for t in range(start, n_dates):
        if t > start:
            cash *= growth
        if pending is not None and pending["exec_idx"] == t:
            units, cash, trade_value, traded, pre_equity = _execute(units, cash, px[t], pending["weights"], rate)
            executions.append({"idx": t, "traded": traded, "pre_equity": pre_equity})
            for i in np.flatnonzero(np.abs(trade_value) > 1e-6):
                notional = abs(float(trade_value[i]))
                transactions.append({
                    "date": dates[t].strftime("%Y-%m-%d"), "executed_at_utc": stamp(t),
                    "ticker": prices.columns[i], "side": "buy" if trade_value[i] > 0 else "sell",
                    "units": abs(float(trade_value[i])) / px[t][i], "price": float(px[t][i]),
                    "notional": notional,
                    "commission": notional * config["commission_bps"] / 10000.0,
                    "spread": notional * config["spread_bps"] / 10000.0,
                    "slippage": notional * config["slippage_bps"] / 10000.0,
                })
            pending["record"]["status"] = "executed"
            pending["record"]["executed_date"] = dates[t].strftime("%Y-%m-%d")
            pending["record"]["executed_at_utc"] = stamp(t)
            pending = None
        holdings = units * px[t]
        total = float(holdings.sum()) + cash
        equity[t - start] = total
        cash_fraction[t - start] = cash / total
        weights_path[t - start] = holdings / total

        if t in decisions_at:
            ctx = DecisionContext(prices.iloc[: t + 1], weights_path[t - start], t == start, config, state)
            target, reason = strategy.decide(ctx)
            if target is None:
                if t == start:
                    return {"available": False, "reason": reason}
                if reason:
                    decisions.append({
                        "decision_date": dates[t].strftime("%Y-%m-%d"), "decided_at_utc": stamp(t),
                        "status": "held", "reason": reason, "target": None,
                    })
                continue
            exec_idx = next_eligible(eligible, t)
            if exec_idx is None:
                continue
            if pending is not None:
                pending["record"]["status"] = "superseded"
            record = {
                "decision_date": dates[t].strftime("%Y-%m-%d"), "decided_at_utc": stamp(t),
                "status": "pending", "reason": reason,
                "target": {prices.columns[i]: float(target[i]) for i in range(n)},
                "cash_weight": float(1.0 - target.sum()),
            }
            decisions.append(record)
            pending = {"exec_idx": exec_idx, "weights": np.asarray(target, dtype=float), "record": record}

    if pending is not None:
        pending["record"]["status"] = "not_executed"
    index = dates[start:]
    return {
        "available": True,
        "equity": pd.Series(equity, index=index),
        "cash_fraction": pd.Series(cash_fraction, index=index),
        "weights": pd.DataFrame(weights_path, index=index, columns=prices.columns),
        "transactions": transactions, "decisions": decisions, "executions": executions,
    }


def _turnover_per_year(executions: List[Dict], years: float) -> float:
    return sum(e["traded"] / 2.0 / e["pre_equity"] for e in executions[1:]) / years


def _series_metrics(equity: pd.Series, rf: float) -> Dict:
    returns = equity.pct_change().dropna()
    years = max((equity.index[-1] - equity.index[0]).days / 365.25, 1e-9)
    out: Dict = {
        "start_value": float(equity.iloc[0]), "final_value": float(equity.iloc[-1]),
        "total_return": float(equity.iloc[-1] / equity.iloc[0] - 1.0), "years": years,
        "annualized_return": None, "volatility": None, "sharpe_ratio": None, "metrics": None,
    }
    if len(returns) < MIN_METRIC_SESSIONS:
        return out
    ann_return, metrics, _ = compute_return_metrics(returns, rf)
    vol = float(returns.std() * math.sqrt(TRADING_DAYS))
    out.update({
        "annualized_return": ann_return, "volatility": vol, "metrics": metrics,
        "sharpe_ratio": round((ann_return - rf) / vol, 3) if vol > 0 else None,
    })
    return out


def run_backtest(
    prices: pd.DataFrame, real: pd.DataFrame, bench_prices: Optional[pd.Series], bench_real: Optional[pd.Series],
    strategy_ids: List[str], config: dict, close_utc: Optional[Callable[[pd.Timestamp], datetime]] = None,
) -> Dict:
    strategies = [STRATEGIES[s] for s in strategy_ids]
    start = max(s.warmup(config) for s in strategies)
    if len(prices) - start < MIN_TEST_SESSIONS + 1:
        raise InsufficientHistory(
            f"Not enough shared price history: {len(prices)} sessions are available "
            f"({prices.index[0].date()} to {prices.index[-1].date()}); the selected strategies need "
            f"{start} sessions of warm-up plus at least {MIN_TEST_SESSIONS} sessions to test."
        )
    dates = prices.index
    eligible = real.to_numpy(dtype=bool).all(axis=1)
    net_rate = cost_rate(config)
    rf = config["risk_free_rate"]
    results: Dict[str, Dict] = {}

    for strategy in strategies:
        if strategy.schedule == SCHEDULE_EVERY_SESSION:
            decisions_at = set(range(start, len(dates) - 1))
        elif strategy.schedule == SCHEDULE_CADENCE:
            decisions_at = {start, *cadence_indices(dates, start, config["cadence"])}
        else:
            decisions_at = {start}
        net = _simulate(strategy, prices, eligible, start, decisions_at, config, net_rate, close_utc)
        if not net["available"]:
            results[strategy.id] = {"available": False, "reason": net["reason"]}
            continue
        gross = _simulate(strategy, prices, eligible, start, decisions_at, config, 0.0, close_utc)
        summary = _series_metrics(net["equity"], rf)
        summary["gross_total_return"] = float(gross["equity"].iloc[-1] / gross["equity"].iloc[0] - 1.0)
        summary["gross_final_value"] = float(gross["equity"].iloc[-1])
        summary["costs_total"] = sum(t["commission"] + t["spread"] + t["slippage"] for t in net["transactions"])
        summary["cost_commission"] = sum(t["commission"] for t in net["transactions"])
        summary["cost_spread"] = sum(t["spread"] for t in net["transactions"])
        summary["cost_slippage"] = sum(t["slippage"] for t in net["transactions"])
        summary["trade_count"] = len(net["transactions"])
        summary["turnover_per_year"] = _turnover_per_year(net["executions"], summary["years"])
        summary["average_exposure"] = float(1.0 - net["cash_fraction"].mean())
        summary["final_weights"] = {t: float(w) for t, w in net["weights"].iloc[-1].items()}
        summary["final_cash_weight"] = float(net["cash_fraction"].iloc[-1])
        results[strategy.id] = {
            "available": True, "summary": summary, "equity": net["equity"],
            "gross_equity": gross["equity"], "cash_fraction": net["cash_fraction"],
            "weights": net["weights"], "transactions": net["transactions"], "decisions": net["decisions"],
        }

    benchmark = None
    if bench_prices is not None:
        bench_frame = bench_prices.to_frame("benchmark")
        sim = _simulate(
            STRATEGIES["buy_hold_ew"], bench_frame, bench_real.to_numpy(dtype=bool), start, {start},
            config, net_rate, close_utc,
        )
        if sim["available"]:
            benchmark = {"equity": sim["equity"], "summary": _series_metrics(sim["equity"], rf)}

    return {"start_index": start, "dates": dates[start:], "strategies": results, "benchmark": benchmark}
