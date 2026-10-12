# GUI name: "Strategy Backtester". Pure allocation rules; the simulation loop lives in strategy_backtest_engine.py.

from dataclasses import dataclass
from typing import Callable, Dict, Optional, Tuple

import numpy as np
import pandas as pd

from portfolio_optimizer_engine import TRADING_DAYS, long_only_allocations

SCHEDULE_INITIAL_ONLY = "initial_only"
SCHEDULE_CADENCE = "cadence"
SCHEDULE_EVERY_SESSION = "every_session"

Decision = Tuple[Optional[np.ndarray], Optional[str]]


@dataclass
class DecisionContext:
    prices: pd.DataFrame
    weights_now: np.ndarray
    first: bool
    config: dict
    state: dict


@dataclass(frozen=True)
class Strategy:
    id: str
    label: str
    description: str
    steps: Tuple[str, ...]
    schedule: str
    decide: Callable[[DecisionContext], Decision]
    warmup: Callable[[dict], int] = lambda config: 0
    needs_current_weights: bool = False


def _equal(n: int) -> np.ndarray:
    return np.full(n, 1.0 / n)


def _label(ctx: DecisionContext, scheduled: str) -> str:
    return "Initial allocation" if ctx.first else scheduled


def _window_returns(ctx: DecisionContext) -> pd.DataFrame:
    return ctx.prices.iloc[-(ctx.config["lookback"] + 1):].pct_change().dropna()


def _buy_hold_equal_weight(ctx: DecisionContext) -> Decision:
    return _equal(ctx.prices.shape[1]), "Initial equal-weight purchase"


def _rebalanced_equal_weight(ctx: DecisionContext) -> Decision:
    return _equal(ctx.prices.shape[1]), _label(ctx, "Scheduled rebalance to equal weight")


def _current_weights(ctx: DecisionContext) -> Decision:
    target = pd.Series(ctx.config["current_weights"], dtype=float).reindex(ctx.prices.columns).fillna(0.0)
    if target.sum() <= 0:
        return None, "None of the selected tickers is held"
    return (target / target.sum()).to_numpy(), _label(ctx, "Scheduled rebalance to today's portfolio weights")


def _tolerance_band(ctx: DecisionContext) -> Decision:
    target = _equal(ctx.prices.shape[1])
    if ctx.first:
        return target, "Initial allocation"
    drift = float(np.abs(ctx.weights_now - target).max())
    band = ctx.config["band_pp"] / 100.0
    if drift <= band:
        return None, None
    return target, f"Largest drift {drift * 100:.1f} pp exceeded the {ctx.config['band_pp']:g} pp band"


def _inverse_volatility(ctx: DecisionContext) -> Decision:
    vol = _window_returns(ctx).std().to_numpy()
    if not np.all(np.isfinite(vol)) or np.any(vol <= 0):
        return None, "A ticker had zero or undefined volatility over the lookback"
    inverse = 1.0 / vol
    return inverse / inverse.sum(), _label(ctx, f"Scheduled rebalance by 1 / volatility over {ctx.config['lookback']} sessions")


def _rolling_optimizer(ctx: DecisionContext, key: str, name: str) -> Decision:
    returns = _window_returns(ctx)
    mu = returns.mean().to_numpy() * TRADING_DAYS
    cov = returns.cov().to_numpy() * TRADING_DAYS
    budget = 1.0 - ctx.config["cash_reserve"]
    solved = long_only_allocations(mu, cov, ctx.config["risk_free_rate"], ctx.config["max_weight"], budget)
    weights = solved[key]
    if weights is None:
        return None, f"{name} unavailable: {solved['warnings'][-1]}"
    return weights, _label(ctx, f"Rolling {name} over {ctx.config['lookback']} sessions")


def _rolling_min_variance(ctx: DecisionContext) -> Decision:
    return _rolling_optimizer(ctx, "w_mv", "Steadiest Mix")


def _rolling_max_sharpe(ctx: DecisionContext) -> Decision:
    return _rolling_optimizer(ctx, "w_ms", "Best Reward-for-Risk Mix")


def _lookback(config: dict) -> int:
    return config["lookback"]


EXPLANATION_PLACEHOLDERS = frozenset({
    "n", "each_pct", "period", "lookback", "band_pp", "max_weight_pct", "cash_reserve_pct",
})

_DAY_ONE_EQUAL = "On day one, put 1/{n} ({each_pct}%) of the money into each of the {n} tickers."
_NEXT_CLOSE = (
    "Execute at the next close. The engine decides on a completed close and trades the next session, "
    "and trading costs are charged on every buy and sell."
)
_ROLLING_START = "All strategies start together, on the first day {lookback} sessions of history exist."


STRATEGIES: Dict[str, Strategy] = {s.id: s for s in (
    Strategy(
        "buy_hold_ew", "Buy-and-Hold Equal Weight",
        "Splits the money equally on day one and never trades again, so the weights drift.",
        (
            _DAY_ONE_EQUAL,
            "Never trade again. Winners grow and losers shrink, so the weights drift away from equal over time.",
            "The one purchase is decided on a completed close and executed at the next close; trading costs are charged once.",
        ),
        SCHEDULE_INITIAL_ONLY, _buy_hold_equal_weight,
    ),
    Strategy(
        "rebalanced_ew", "Rebalanced Equal Weight",
        "Restores an equal split on every rebalance date.",
        (
            _DAY_ONE_EQUAL,
            "On the last trading day of each {period}, compare actual weights to {each_pct}%. "
            "Sell what has drifted above it and buy what has drifted below.",
            _NEXT_CLOSE,
        ),
        SCHEDULE_CADENCE, _rebalanced_equal_weight,
    ),
    Strategy(
        "current_weights", "Current Portfolio Weights (Rebalanced)",
        "Applies your allocation of today to the whole past period, restored on every rebalance date.",
        (
            "Take the weights of your holdings today, renormalised across the {n} selected tickers, as the fixed target. "
            "A ticker you do not hold gets 0%. The same target is used for the whole past period.",
            "Buy that mix on day one. On the last trading day of each {period}, sell what has drifted above its target "
            "and buy what has drifted below.",
            _NEXT_CLOSE,
        ),
        SCHEDULE_CADENCE, _current_weights, needs_current_weights=True,
    ),
    Strategy(
        "inverse_vol", "Inverse-Volatility Weighting",
        "Gives calmer tickers a bigger share (1 divided by recent volatility), restored on every rebalance date.",
        (
            "On the last trading day of each {period}, measure each ticker's daily volatility over the previous {lookback} sessions.",
            "Give each ticker a share proportional to 1 / volatility: calmer tickers get more money, jumpier ones less, "
            "and the shares add up to 100%. A ticker with zero or unknown volatility skips that decision and the holdings are kept.",
            _NEXT_CLOSE + " " + _ROLLING_START,
        ),
        SCHEDULE_CADENCE, _inverse_volatility, warmup=_lookback,
    ),
    Strategy(
        "tolerance_band", "Tolerance-Band Rebalancing",
        "Starts equal weight and trades back to equal weight only when a holding drifts further than the band; checked every session.",
        (
            _DAY_ONE_EQUAL,
            "After every close, find the holding furthest from {each_pct}%. If it is more than {band_pp} percentage points away, "
            "trade every holding back to {each_pct}%; otherwise do nothing. The Rebalance Cadence setting does not apply.",
            _NEXT_CLOSE,
        ),
        SCHEDULE_EVERY_SESSION, _tolerance_band,
    ),
    Strategy(
        "min_variance", "Rolling Steadiest Mix (Min-Variance)",
        "Long-only mix with the least historical bumpiness, re-solved on every rebalance date from only the trailing lookback window.",
        (
            "On the last trading day of each {period}, take the last {lookback} sessions of returns for the {n} tickers. "
            "Nothing after that day is used.",
            "Solve for the long-only mix with the least bumpiness (lowest variance), with no ticker above {max_weight_pct}% "
            "and {cash_reserve_pct}% kept in cash. This is the same solver as the Portfolio Optimizer's Steadiest Mix.",
            _NEXT_CLOSE + " If the solver finds no valid mix, the holdings are kept and the decision is logged. " + _ROLLING_START,
        ),
        SCHEDULE_CADENCE, _rolling_min_variance, warmup=_lookback,
    ),
    Strategy(
        "max_sharpe", "Rolling Best Reward-for-Risk Mix (Max-Sharpe)",
        "Long-only mix with the best historical return for its bumpiness, re-solved on every rebalance date from only the trailing lookback window.",
        (
            "On the last trading day of each {period}, take the last {lookback} sessions of returns for the {n} tickers. "
            "Nothing after that day is used.",
            "Solve for the long-only mix with the best return for its bumpiness (highest Sharpe ratio, using the risk-free rate), "
            "with no ticker above {max_weight_pct}% and {cash_reserve_pct}% kept in cash. "
            "This is the same solver as the Portfolio Optimizer's Best Reward-for-Risk Mix.",
            _NEXT_CLOSE + " If no allowed mix works, the holdings are kept and the decision is logged. " + _ROLLING_START,
        ),
        SCHEDULE_CADENCE, _rolling_max_sharpe, warmup=_lookback,
    ),
)}


def snapshot_target(strategy_id: str, returns: pd.DataFrame, config: dict) -> Decision:
    """Today's target mix of one strategy from a returns matrix, through the strategy's own decision rule."""
    prices = pd.concat([
        pd.DataFrame(1.0, index=[0], columns=returns.columns),
        (1.0 + returns.reset_index(drop=True)).cumprod().set_axis(range(1, len(returns) + 1)),
    ])
    ctx = DecisionContext(
        prices=prices, weights_now=np.zeros(prices.shape[1]), first=True,
        config={**config, "lookback": len(returns)}, state={},
    )
    return STRATEGIES[strategy_id].decide(ctx)
