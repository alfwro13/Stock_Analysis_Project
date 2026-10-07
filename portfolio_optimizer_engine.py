import logging
import math
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd
from scipy.optimize import minimize

from config import load_config
from database import get_connection
from db_accounts import get_watchlist_tickers
from xray_engine import (
    fetch_close_returns_from_parquet,
    get_scope_returns_matrix,
    resolve_scope_holdings,
)

logger = logging.getLogger(__name__)

TRADING_DAYS = 252
RIDGE = 1e-8
SOLVER_TOL = 1e-6
ZERO_VARIANCE = 1e-12
FRONTIER_POINTS = 25
MODE_UNCONSTRAINED = "unconstrained"
MODE_LONG_ONLY = "long_only"
MIN_OVERLAP_DAYS = 30
MIN_TICKERS_WARNING = "Need at least 2 tickers to optimize a portfolio."
NOT_ENOUGH_DATA_WARNING = (
    "Not enough overlapping cached return history for this candidate set yet — need at least "
    "30 overlapping trading days across the selected tickers."
)


def _ticker_names(tickers: List[str]) -> Dict[str, str]:
    if not tickers:
        return {}
    conn = None
    try:
        conn = get_connection()
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker, company_name FROM asset_profiles WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
        return {row["ticker"]: row["company_name"] for row in rows if row["company_name"]}
    except Exception as e:
        logger.error("Portfolio Optimizer asset_profiles name lookup failed: %s", e)
        return {}
    finally:
        if conn:
            conn.close()


def list_candidates(account_id: str) -> Dict:
    """Held tickers (pre-checked) + full Watchlist ticker list (opt-in) for the checklist UI."""
    try:
        holdings, _ = resolve_scope_holdings(account_id)
    except RuntimeError as e:
        return {"status": "error", "message": str(e)}

    held = [h for h in holdings if h.get("weight", 0) > 0]
    held_symbols = {h["symbol"] for h in held}
    watchlist_symbols = [t for t in get_watchlist_tickers() if t not in held_symbols]
    names = _ticker_names(watchlist_symbols)

    candidates = [
        {
            "symbol": h["symbol"],
            "name": h.get("name") or h["symbol"],
            "current_weight": round(h["weight"], 4),
            "held": True,
        }
        for h in held
    ] + [
        {"symbol": t, "name": names.get(t, t), "current_weight": 0.0, "held": False}
        for t in watchlist_symbols
    ]
    return {"status": "success", "account_id": account_id, "candidates": candidates}


def _drop_short_history(returns: pd.DataFrame) -> Tuple[pd.DataFrame, List[Tuple[str, int]]]:
    """Removes the fewest tickers needed for the rest to share MIN_OVERLAP_DAYS, shortest history first."""
    removed: List[Tuple[str, int]] = []
    while returns.shape[1] >= 2 and len(returns.dropna(how="any")) < MIN_OVERLAP_DAYS:
        ticker, days = min(returns.notna().sum().items(), key=lambda kv: (kv[1], kv[0]))
        removed.append((ticker, int(days)))
        returns = returns.drop(columns=ticker)
    return returns, removed


def _returns_matrix_for_candidates(
    tickers: List[str],
) -> Tuple[Optional[pd.DataFrame], List[str], List[Tuple[str, int]]]:
    """Parquet fallback because the nightly X-ray precompute never caches a never-held (Watchlist-only) ticker."""
    cached_df, warnings = get_scope_returns_matrix(tickers, include_benchmark=False)
    cached_symbols = set(cached_df.columns) if cached_df is not None else set()
    missing = [t for t in tickers if t not in cached_symbols]

    if not missing:
        return cached_df, warnings, []

    fallback_prices = fetch_close_returns_from_parquet(missing)
    if cached_df is None and fallback_prices.empty:
        return None, warnings, []
    if cached_df is None:
        combined = fallback_prices
    elif fallback_prices.empty:
        combined = cached_df
    else:
        combined = cached_df.join(fallback_prices, how="left")

    combined, removed = _drop_short_history(combined)
    combined = combined.dropna(how="any")
    if len(combined) < MIN_OVERLAP_DAYS or combined.shape[1] < 2:
        return None, warnings, removed
    return combined, warnings, removed


def _short_history_warning(removed: List[Tuple[str, int]]) -> str:
    return (
        "Removed " + ", ".join(f"{t} ({days} days)" for t, days in removed)
        + f" — not enough overlapping cached return history with the other selected tickers "
        f"(need at least {MIN_OVERLAP_DAYS} overlapping trading days)."
    )


def _closed_form_weights(mu: np.ndarray, cov: np.ndarray, rf: float) -> Dict:
    """Unconstrained weights can be negative; callers must surface that, never clip it."""
    n = len(mu)
    ones = np.ones(n)
    cov_reg = cov + RIDGE * np.eye(n)
    warnings: List[str] = []
    try:
        inv_cov = np.linalg.inv(cov_reg)
    except np.linalg.LinAlgError:
        logger.warning("Portfolio Optimizer: covariance matrix singular, using pseudo-inverse")
        inv_cov = np.linalg.pinv(cov_reg)
        warnings.append(
            "Covariance matrix was singular or near-singular (e.g. duplicate/highly correlated "
            "tickers, or more candidate tickers than overlapping trading days) — weights were "
            "computed via a pseudo-inverse fallback and may be unstable."
        )

    raw_mv = inv_cov @ ones
    w_mv = raw_mv / (ones @ raw_mv)

    raw_ms = inv_cov @ (mu - rf)
    denom_ms = ones @ raw_ms
    w_ms = None
    if abs(denom_ms) > 1e-8:
        w_ms = raw_ms / denom_ms
        if denom_ms < 0:
            warnings.append(
                "Expected returns for this candidate set are below the risk-free rate on "
                "average — interpret the Max-Sharpe allocation with caution."
            )
    else:
        warnings.append(
            "Max-Sharpe allocation could not be computed (near-zero excess-return spread "
            "across candidates)."
        )

    return {"w_mv": w_mv, "w_ms": w_ms, "warnings": warnings}


def _risk_return_point(w: np.ndarray, mu: np.ndarray, cov: np.ndarray, cash_return: float = 0.0) -> Dict:
    return {
        "return": round(float(w @ mu) + cash_return, 4),
        "volatility": round(max(float(w @ cov @ w), 0.0) ** 0.5, 4),
    }


def _efficient_frontier(w_mv: np.ndarray, w_ms: np.ndarray, mu: np.ndarray, cov: np.ndarray) -> Dict:
    """Two-fund separation: blending any two frontier portfolios traces the curve without re-optimizing."""
    return {
        "points": [
            _risk_return_point(w_mv + t * (w_ms - w_mv), mu, cov)
            for t in np.linspace(-0.5, 1.5, FRONTIER_POINTS)
        ],
        "min_variance": _risk_return_point(w_mv, mu, cov),
        "max_sharpe": _risk_return_point(w_ms, mu, cov),
    }


def _validated_solution(res, cap: float, budget: float) -> Optional[np.ndarray]:
    """Rejects a failed or constraint-violating result instead of repairing it; only sub-tolerance noise is trimmed."""
    if not res.success:
        return None
    w = np.asarray(res.x, dtype=float)
    if (
        not np.all(np.isfinite(w))
        or abs(w.sum() - budget) > SOLVER_TOL
        or w.min() < -SOLVER_TOL
        or w.max() > cap + SOLVER_TOL
    ):
        return None
    return np.clip(w, 0.0, cap)


def _budget_constraint(n: int, budget: float) -> Dict:
    return {"type": "eq", "fun": lambda w: w.sum() - budget, "jac": lambda w: np.ones(n)}


def _long_only_min_variance(
    cov: np.ndarray, cap: float, budget: float,
    mu: Optional[np.ndarray] = None, target_return: Optional[float] = None,
    x0: Optional[np.ndarray] = None,
) -> Optional[np.ndarray]:
    n = cov.shape[0]
    constraints = [_budget_constraint(n, budget)]
    if target_return is not None:
        constraints.append({"type": "eq", "fun": lambda w: w @ mu - target_return, "jac": lambda w: mu})
    res = minimize(
        lambda w: w @ cov @ w,
        np.full(n, budget / n) if x0 is None else x0,
        jac=lambda w: 2 * cov @ w,
        method="SLSQP",
        bounds=[(0.0, cap)] * n,
        constraints=constraints,
        options={"maxiter": 1000, "ftol": 1e-12},
    )
    w = _validated_solution(res, cap, budget)
    if w is not None and target_return is not None and abs(w @ mu - target_return) > SOLVER_TOL:
        return None
    return w


def _max_return_weights(mu: np.ndarray, cap: float, budget: float) -> np.ndarray:
    """Highest-expected-return mix under the bounds: fill the best tickers to the cap in turn."""
    w = np.zeros(len(mu))
    remaining = budget
    for i in np.argsort(-mu, kind="stable"):
        w[i] = min(cap, remaining)
        remaining -= w[i]
        if remaining <= 0:
            break
    return w


def _long_only_max_sharpe(
    mu: np.ndarray, cov: np.ndarray, rf: float, cap: float, budget: float
) -> Tuple[Optional[np.ndarray], Optional[str]]:
    """Starts from the max-excess mix: the ratio is quasi-concave where wᵀ(μ − rf) > 0, so SLSQP's local optimum is global."""
    excess = mu - rf
    x0 = _max_return_weights(mu, cap, budget)
    if float(x0 @ excess) <= ZERO_VARIANCE:
        return None, (
            "No long-only mix of these tickers has a historical return above the risk-free rate, "
            "so the Best Reward-for-Risk Mix is unavailable."
        )

    def neg_sharpe(w):
        return -float(w @ excess) / max(float(w @ cov @ w), ZERO_VARIANCE) ** 0.5

    def neg_sharpe_grad(w):
        var = max(float(w @ cov @ w), ZERO_VARIANCE)
        return -(excess / var ** 0.5 - float(w @ excess) * (cov @ w) / var ** 1.5)

    res = minimize(
        neg_sharpe, x0, jac=neg_sharpe_grad, method="SLSQP", bounds=[(0.0, cap)] * len(mu),
        constraints=[_budget_constraint(len(mu), budget)],
        options={"maxiter": 1000, "ftol": 1e-12},
    )
    w = _validated_solution(res, cap, budget)
    if w is None:
        return None, "The Best Reward-for-Risk Mix solver did not converge for this candidate set."
    if float(w @ cov @ w) <= ZERO_VARIANCE:
        return None, (
            "The Best Reward-for-Risk Mix has zero historical volatility for this candidate set, so "
            "its reward-for-risk ratio is undefined."
        )
    return w, None


def _long_only_frontier(
    mu: np.ndarray, cov: np.ndarray, w_mv: np.ndarray, cap: float, budget: float, cash_return: float
) -> List[Dict]:
    """Per-target min-variance solves, because the unconstrained two-fund blend can breach the bounds."""
    low = float(w_mv @ mu)
    high = float(_max_return_weights(mu, cap, budget) @ mu)
    if high - low <= SOLVER_TOL:
        return [_risk_return_point(w_mv, mu, cov, cash_return)]
    points = []
    x0 = w_mv
    for target in np.linspace(low, high, FRONTIER_POINTS):
        w = _long_only_min_variance(cov, cap, budget, mu=mu, target_return=float(target), x0=x0)
        if w is None:
            continue
        points.append(_risk_return_point(w, mu, cov, cash_return))
        x0 = w
    return points


def _pct(fraction: float) -> str:
    return f"{fraction * 100:g}%"


def _cap_infeasible_warning(n: int, cap: float, cash_reserve: float) -> str:
    budget = 1.0 - cash_reserve
    min_cap = math.ceil(round(budget / n * 1000, 9)) / 1000
    return (
        f"A {_pct(cap)} Weight Cap can't be met with {n} tickers: {n} × {_pct(cap)} = "
        f"{_pct(round(n * cap, 6))}, less than the {_pct(round(budget, 6))} to be invested"
        + (f" after the {_pct(cash_reserve)} Cash Reserve" if cash_reserve > 0 else "")
        + f". Raise the Weight Cap to at least {_pct(min_cap)}"
        + (", tick more tickers, or raise the Cash Reserve." if cash_reserve > 0 else " or tick more tickers.")
    )


def optimize_portfolio(
    account_id: str,
    include_tickers: Optional[List[str]] = None,
    mode: str = MODE_UNCONSTRAINED,
    max_weight: float = 1.0,
    cash_reserve: float = 0.0,
) -> Dict:
    """Pure computation, no DB writes; Long-Only cap/cash arguments are ignored in unconstrained mode."""
    long_only = mode == MODE_LONG_ONLY
    budget = 1.0 - cash_reserve if long_only else 1.0
    settings = {
        "mode": mode,
        "max_weight": max_weight if long_only else None,
        "cash_reserve": cash_reserve if long_only else None,
    }

    def _no_result(warnings: List[str], window: Optional[Dict] = None) -> Dict:
        return {
            "status": "success", "account_id": account_id, **settings, "weights": None,
            "risk_free_rate": None, "efficient_frontier": None, "estimation_window": window,
            "data_warnings": warnings,
        }

    try:
        holdings, _ = resolve_scope_holdings(account_id)
    except RuntimeError as e:
        logger.warning("Portfolio Optimizer failed for account_id=%s: %s", account_id, e)
        return {"status": "error", "message": str(e)}

    held = {h["symbol"]: h for h in holdings if h.get("weight", 0) > 0}
    candidate_tickers = list(include_tickers) if include_tickers else list(held.keys())
    candidate_tickers = list(dict.fromkeys(t for t in candidate_tickers if t))

    if len(candidate_tickers) < 2:
        return _no_result([MIN_TICKERS_WARNING])

    returns_df, data_warnings, short_history = _returns_matrix_for_candidates(candidate_tickers)
    data_warnings = list(data_warnings)
    if short_history:
        data_warnings.append(_short_history_warning(short_history))
    if returns_df is None or returns_df.shape[1] < 2:
        data_warnings.append(NOT_ENOUGH_DATA_WARNING)
        return _no_result(data_warnings)

    resolved_tickers = list(returns_df.columns)
    dropped = sorted(set(candidate_tickers) - set(resolved_tickers) - {t for t, _ in short_history})
    if dropped:
        data_warnings.append(
            f"{len(dropped)} candidate ticker(s) excluded — no aligned return history: "
            + ", ".join(dropped[:5])
            + (f" and {len(dropped) - 5} more" if len(dropped) > 5 else "")
        )

    overlapping_days = len(returns_df)
    estimation_window = {
        "start": pd.Timestamp(returns_df.index.min()).strftime("%Y-%m-%d"),
        "end": pd.Timestamp(returns_df.index.max()).strftime("%Y-%m-%d"),
        "trading_days": overlapping_days,
    }
    n = len(resolved_tickers)
    if n > overlapping_days / 3:
        data_warnings.append(
            f"{n} candidate tickers vs. only {overlapping_days} overlapping trading days — the "
            "covariance estimate is thin relative to the number of tickers and weights may be "
            "unstable. Consider selecting fewer candidates."
        )

    if long_only and n * max_weight < budget - SOLVER_TOL:
        data_warnings.append(_cap_infeasible_warning(n, max_weight, cash_reserve))
        return _no_result(data_warnings, estimation_window)

    mu = returns_df.mean(axis=0).to_numpy() * TRADING_DAYS
    cov = returns_df.cov().to_numpy() * TRADING_DAYS
    rf = float(load_config().get("RISK_FREE_RATE", 0.045))

    if long_only:
        if np.linalg.cond(cov) > 1e10:
            data_warnings.append(
                "Covariance matrix is singular or near-singular (e.g. duplicate, highly correlated "
                "or flat-priced tickers) — several different mixes can be almost equally good, so "
                "treat individual weights loosely."
            )
        w_mv = _long_only_min_variance(cov, max_weight, budget)
        if w_mv is None:
            data_warnings.append("The Steadiest Mix solver did not converge for this candidate set.")
            return _no_result(data_warnings, estimation_window)
        w_ms, ms_warning = _long_only_max_sharpe(mu, cov, rf, max_weight, budget)
        if ms_warning:
            data_warnings.append(ms_warning)
    else:
        result = _closed_form_weights(mu, cov, rf)
        data_warnings.extend(result["warnings"])
        w_mv, w_ms = result["w_mv"], result["w_ms"]
    w_ew = np.full(n, budget / n)

    names = _ticker_names([t for t in resolved_tickers if t not in held])
    for ticker in resolved_tickers:
        if ticker in held:
            names[ticker] = held[ticker].get("name") or ticker

    any_short = False
    weights_out = []
    for i, ticker in enumerate(resolved_tickers):
        current_weight = held.get(ticker, {}).get("weight", 0.0)
        mv = float(w_mv[i])
        ms = float(w_ms[i]) if w_ms is not None else None
        is_short = mv < 0 or (ms is not None and ms < 0)
        any_short = any_short or is_short
        weights_out.append({
            "symbol": ticker,
            "name": names.get(ticker, ticker),
            "current_weight": round(current_weight, 4),
            "suggested_weight_mv": round(mv, 4),
            "suggested_weight_ms": round(ms, 4) if ms is not None else None,
            "suggested_weight_ew": round(float(w_ew[i]), 4),
            "is_new_addition": current_weight == 0.0,
            "is_short": is_short,
        })

    if any_short:
        data_warnings.append(
            "One or more holdings have a negative suggested weight in the closed-form solution "
            "— this reflects the unconstrained math, not a shorting recommendation. This app has "
            "no order execution and cannot act on it."
        )

    current_w = np.array([held.get(t, {}).get("weight", 0.0) for t in resolved_tickers])
    current_point = None
    current_sum = float(current_w.sum())
    if current_sum > 1e-9:
        if abs(current_sum - 1.0) > 0.01:
            data_warnings.append(
                "Your selected candidates don't cover your whole current portfolio (some held "
                "positions were left unchecked), so the 'Your Portfolio Today' point on the "
                "chart only reflects the tickers included here, not your full account."
            )
        current_point = _risk_return_point(current_w, mu, cov)

    cash_return = (1.0 - budget) * rf
    if long_only:
        frontier = {
            "points": _long_only_frontier(mu, cov, w_mv, max_weight, budget, cash_return),
            "min_variance": _risk_return_point(w_mv, mu, cov, cash_return),
            "max_sharpe": _risk_return_point(w_ms, mu, cov, cash_return) if w_ms is not None else None,
        }
    else:
        frontier = _efficient_frontier(w_mv, w_ms, mu, cov) if w_ms is not None else None
    if frontier is not None:
        frontier["equal_weight"] = _risk_return_point(w_ew, mu, cov, cash_return)
        frontier["current"] = current_point

    return {
        "status": "success",
        "account_id": account_id,
        **settings,
        "weights": weights_out,
        "risk_free_rate": rf,
        "efficient_frontier": frontier,
        "estimation_window": estimation_window,
        "data_warnings": data_warnings,
    }
