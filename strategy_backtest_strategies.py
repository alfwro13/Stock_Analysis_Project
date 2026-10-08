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
    target = pd.Series(ctx.config["current_weights"]).reindex(ctx.prices.columns).fillna(0.0)
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


STRATEGIES: Dict[str, Strategy] = {s.id: s for s in (
    Strategy(
        "buy_hold_ew", "Buy-and-Hold Equal Weight",
        "Splits the money equally on day one and never trades again, so the weights drift.",
        SCHEDULE_INITIAL_ONLY, _buy_hold_equal_weight,
    ),
    Strategy(
        "rebalanced_ew", "Rebalanced Equal Weight",
        "Restores an equal split on every rebalance date.",
        SCHEDULE_CADENCE, _rebalanced_equal_weight,
    ),
    Strategy(
        "current_weights", "Current Portfolio Weights (Rebalanced)",
        "Applies your allocation of today to the whole past period, restored on every rebalance date.",
        SCHEDULE_CADENCE, _current_weights, needs_current_weights=True,
    ),
    Strategy(
        "inverse_vol", "Inverse-Volatility Weighting",
        "Gives calmer tickers a bigger share (1 divided by recent volatility), restored on every rebalance date.",
        SCHEDULE_CADENCE, _inverse_volatility, warmup=_lookback,
    ),
    Strategy(
        "tolerance_band", "Tolerance-Band Rebalancing",
        "Starts equal weight and trades back to equal weight only when a holding drifts further than the band; checked every session.",
        SCHEDULE_EVERY_SESSION, _tolerance_band,
    ),
    Strategy(
        "min_variance", "Rolling Steadiest Mix (Min-Variance)",
        "Long-only mix with the least historical bumpiness, re-solved on every rebalance date from only the trailing lookback window.",
        SCHEDULE_CADENCE, _rolling_min_variance, warmup=_lookback,
    ),
    Strategy(
        "max_sharpe", "Rolling Best Reward-for-Risk Mix (Max-Sharpe)",
        "Long-only mix with the best historical return for its bumpiness, re-solved on every rebalance date from only the trailing lookback window.",
        SCHEDULE_CADENCE, _rolling_max_sharpe, warmup=_lookback,
    ),
)}
