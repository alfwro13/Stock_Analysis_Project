import logging
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

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


def _returns_matrix_for_candidates(tickers: List[str]) -> Tuple[Optional[pd.DataFrame], List[str]]:
    """Two-tier read: xray_returns_cache first (covers held tickers), then a direct parquet
    read for anything missing (covers Watchlist-only tickers — the nightly X-ray precompute
    only ever scans get_combined_holdings(), so a never-held ticker has no cache row)."""
    cached_df, warnings = get_scope_returns_matrix(tickers, include_benchmark=False)
    cached_symbols = set(cached_df.columns) if cached_df is not None else set()
    missing = [t for t in tickers if t not in cached_symbols]

    if not missing:
        return cached_df, warnings

    fallback_prices = fetch_close_returns_from_parquet(missing)
    if cached_df is None and fallback_prices.empty:
        return None, warnings
    if cached_df is None:
        combined = fallback_prices
    elif fallback_prices.empty:
        combined = cached_df
    else:
        combined = cached_df.join(fallback_prices, how="inner")

    combined = combined.dropna(how="any")
    if len(combined) < 30 or combined.shape[1] < 2:
        return None, warnings
    return combined, warnings


def _closed_form_weights(mu: np.ndarray, cov: np.ndarray, rf: float) -> Dict:
    """Unconstrained closed-form Min-Variance (w ∝ Σ⁻¹·1) and Max-Sharpe/tangency
    (w ∝ Σ⁻¹·(μ−rf)) weights, each normalized to sum to 1. No shorting/position-cap
    constraints — entries can be negative; callers must surface that, never clip it."""
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


def _efficient_frontier(w_mv: np.ndarray, w_ms: np.ndarray, mu: np.ndarray, cov: np.ndarray) -> Dict:
    """Two-fund separation — every point on the mean-variance frontier is a linear combination
    of any two frontier portfolios, so sweeping t traces the curve without a second optimization."""
    points = []
    for t in np.linspace(-0.5, 1.5, 25):
        w = w_mv + t * (w_ms - w_mv)
        ret = float(w @ mu)
        var = float(w @ cov @ w)
        points.append({"return": round(ret, 4), "volatility": round(max(var, 0.0) ** 0.5, 4)})
    return {
        "points": points,
        "min_variance": {
            "return": round(float(w_mv @ mu), 4),
            "volatility": round(max(float(w_mv @ cov @ w_mv), 0.0) ** 0.5, 4),
        },
        "max_sharpe": {
            "return": round(float(w_ms @ mu), 4),
            "volatility": round(max(float(w_ms @ cov @ w_ms), 0.0) ** 0.5, 4),
        },
    }


def optimize_portfolio(account_id: str, include_tickers: Optional[List[str]] = None) -> Dict:
    """Closed-form Min-Variance / Max-Sharpe suggested weights for an account scope plus any
    opted-in Watchlist tickers — pure computation, no DB writes, mirrors
    performance_analytics_engine.assemble_performance_report()'s shape."""
    try:
        holdings, _ = resolve_scope_holdings(account_id)
    except RuntimeError as e:
        logger.warning("Portfolio Optimizer failed for account_id=%s: %s", account_id, e)
        return {"status": "error", "message": str(e)}

    held = {h["symbol"]: h for h in holdings if h.get("weight", 0) > 0}
    candidate_tickers = list(include_tickers) if include_tickers else list(held.keys())
    candidate_tickers = list(dict.fromkeys(t for t in candidate_tickers if t))

    if len(candidate_tickers) < 2:
        return {
            "status": "success", "account_id": account_id, "weights": None,
            "risk_free_rate": None, "efficient_frontier": None,
            "data_warnings": [MIN_TICKERS_WARNING],
        }

    returns_df, data_warnings = _returns_matrix_for_candidates(candidate_tickers)
    data_warnings = list(data_warnings)
    if returns_df is None or returns_df.shape[1] < 2:
        data_warnings.append(NOT_ENOUGH_DATA_WARNING)
        return {
            "status": "success", "account_id": account_id, "weights": None,
            "risk_free_rate": None, "efficient_frontier": None,
            "data_warnings": data_warnings,
        }

    resolved_tickers = list(returns_df.columns)
    if len(resolved_tickers) < len(candidate_tickers):
        dropped = sorted(set(candidate_tickers) - set(resolved_tickers))
        data_warnings.append(
            f"{len(dropped)} candidate ticker(s) excluded — no aligned return history: "
            + ", ".join(dropped[:5])
            + (f" and {len(dropped) - 5} more" if len(dropped) > 5 else "")
        )

    overlapping_days = len(returns_df)
    n = len(resolved_tickers)
    if n > overlapping_days / 3:
        data_warnings.append(
            f"{n} candidate tickers vs. only {overlapping_days} overlapping trading days — the "
            "covariance estimate is thin relative to the number of tickers and weights may be "
            "unstable. Consider selecting fewer candidates."
        )

    mu = returns_df.mean(axis=0).to_numpy() * TRADING_DAYS
    cov = returns_df.cov().to_numpy() * TRADING_DAYS
    rf = float(load_config().get("RISK_FREE_RATE", 0.045))

    result = _closed_form_weights(mu, cov, rf)
    data_warnings.extend(result["warnings"])

    names = _ticker_names([t for t in resolved_tickers if t not in held])
    for ticker in resolved_tickers:
        if ticker in held:
            names[ticker] = held[ticker].get("name") or ticker

    any_short = False
    weights_out = []
    for i, ticker in enumerate(resolved_tickers):
        current_weight = held.get(ticker, {}).get("weight", 0.0)
        w_mv = float(result["w_mv"][i])
        w_ms = float(result["w_ms"][i]) if result["w_ms"] is not None else None
        is_short = w_mv < 0 or (w_ms is not None and w_ms < 0)
        any_short = any_short or is_short
        weights_out.append({
            "symbol": ticker,
            "name": names.get(ticker, ticker),
            "current_weight": round(current_weight, 4),
            "suggested_weight_mv": round(w_mv, 4),
            "suggested_weight_ms": round(w_ms, 4) if w_ms is not None else None,
            "is_new_addition": current_weight == 0.0,
            "is_short": is_short,
        })

    if any_short:
        data_warnings.append(
            "One or more holdings have a negative suggested weight in the closed-form solution "
            "— this reflects the unconstrained math, not a shorting recommendation. This app has "
            "no order execution and cannot act on it."
        )

    frontier = (
        _efficient_frontier(result["w_mv"], result["w_ms"], mu, cov)
        if result["w_ms"] is not None else None
    )

    return {
        "status": "success",
        "account_id": account_id,
        "weights": weights_out,
        "risk_free_rate": rf,
        "efficient_frontier": frontier,
        "data_warnings": data_warnings,
    }
