# GUI name: "Portfolio Tearsheet". Natively-computed performance-analytics metrics that
# fill gaps versus xray_engine's existing Sharpe/VaR/CVaR/skew/kurtosis (never duplicated here).

import logging
from typing import Dict, List, Optional

import numpy as np
import pandas as pd

from config import load_config
from xray_engine import (
    annualized_return,
    get_scope_return_series,
    native_max_drawdown,
    resolve_scope_holdings,
)

logger = logging.getLogger(__name__)

TRADING_DAYS = 252
NOT_ENOUGH_DATA_WARNING = (
    "Not enough cached return history for this scope yet — need at least 30 overlapping "
    "cached trading days across the in-scope holdings."
)


def _downside_deviation(returns: pd.Series) -> float:
    downside = returns[returns < 0]
    if downside.empty:
        return 0.0
    return float(downside.std() * np.sqrt(TRADING_DAYS))


def _sortino_ratio(returns: pd.Series, ann_return: float, rf: float) -> Optional[float]:
    dd = _downside_deviation(returns)
    if dd == 0:
        return None
    return round((ann_return - rf) / dd, 3)


def _omega_ratio(returns: pd.Series, rf: float) -> Optional[float]:
    daily_rf = rf / TRADING_DAYS
    excess = returns - daily_rf
    gains = float(excess[excess > 0].sum())
    losses = float(excess[excess < 0].sum())
    if losses == 0:
        return None
    return round(gains / abs(losses), 3)


def _profit_factor(returns: pd.Series) -> Optional[float]:
    gains = float(returns[returns > 0].sum())
    losses = float(returns[returns < 0].sum())
    if losses == 0:
        return None
    return round(gains / abs(losses), 3)


def _calmar_ratio(ann_return: float, max_dd: float) -> Optional[float]:
    if max_dd >= 0:
        return None
    return round(ann_return / abs(max_dd), 3)


def _drawdown_stats(drawdown_series: pd.Series) -> Dict:
    idx = drawdown_series.index
    underwater = drawdown_series < 0

    longest_days = 0
    streak_start = None
    for i, is_under in enumerate(underwater):
        if is_under:
            if streak_start is None:
                streak_start = idx[i]
            longest_days = max(longest_days, int((idx[i] - streak_start).days))
        else:
            streak_start = None

    at_peak = idx[drawdown_series == 0]
    last_peak = at_peak[-1] if len(at_peak) > 0 else idx[0]
    time_underwater_days = int((idx[-1] - last_peak).days)

    return {
        "longest_drawdown_days": longest_days,
        "time_underwater_days": time_underwater_days,
        "ulcer_index": round(float(np.sqrt((drawdown_series ** 2).mean())), 4),
    }


def _win_loss_stats(returns: pd.Series) -> Dict:
    wins = returns[returns > 0]
    losses = returns[returns < 0]
    avg_win = float(wins.mean()) if not wins.empty else None
    avg_loss = float(losses.mean()) if not losses.empty else None
    payoff_ratio = (
        round(avg_win / abs(avg_loss), 3)
        if avg_win is not None and avg_loss not in (None, 0)
        else None
    )
    return {
        "win_rate": round(float(len(wins) / len(returns)), 4) if len(returns) else None,
        "avg_win": round(avg_win, 4) if avg_win is not None else None,
        "avg_loss": round(avg_loss, 4) if avg_loss is not None else None,
        "payoff_ratio": payoff_ratio,
    }


def _max_consecutive(returns: pd.Series) -> Dict:
    max_win_streak = max_loss_streak = cur_win = cur_loss = 0
    for r in returns:
        if r > 0:
            cur_win += 1
            cur_loss = 0
            max_win_streak = max(max_win_streak, cur_win)
        elif r < 0:
            cur_loss += 1
            cur_win = 0
            max_loss_streak = max(max_loss_streak, cur_loss)
        else:
            cur_win = cur_loss = 0
    return {"max_consecutive_wins": max_win_streak, "max_consecutive_losses": max_loss_streak}


def _monthly_returns(returns: pd.Series) -> pd.Series:
    return returns.resample("ME").apply(lambda x: float((1 + x).prod() - 1))


def _distribution_stats(returns: pd.Series) -> Dict:
    monthly = _monthly_returns(returns)
    tail_hi = float(returns.quantile(0.95))
    tail_lo = float(returns.quantile(0.05))
    return {
        "best_day": round(float(returns.max()), 4),
        "worst_day": round(float(returns.min()), 4),
        "best_month": round(float(monthly.max()), 4) if not monthly.empty else None,
        "worst_month": round(float(monthly.min()), 4) if not monthly.empty else None,
        "tail_ratio": round(abs(tail_hi) / abs(tail_lo), 3) if tail_lo != 0 else None,
    }


def _monthly_heatmap_matrix(returns: pd.Series) -> Dict:
    monthly = _monthly_returns(returns)
    if monthly.empty:
        return {"years": [], "months": [], "matrix": []}
    years = sorted({d.year for d in monthly.index})
    months = list(range(1, 13))

    matrix = []
    for year in years:
        row = []
        for month in months:
            match = monthly[(monthly.index.year == year) & (monthly.index.month == month)]
            row.append(round(float(match.iloc[0]), 4) if not match.empty else None)
        matrix.append(row)

    return {"years": years, "months": months, "matrix": matrix}


def _underwater_chart_data(drawdown_series: pd.Series) -> List[Dict]:
    return [
        {"date": d.strftime("%Y-%m-%d"), "value": round(float(v), 4)}
        for d, v in drawdown_series.items()
    ]


def _cumulative_growth_chart_data(port_rets: pd.Series, bench_rets: pd.Series) -> Dict:
    port_growth = (1 + port_rets).cumprod() * 100
    bench_growth = (1 + bench_rets).cumprod() * 100
    return {
        "dates": [d.strftime("%Y-%m-%d") for d in port_rets.index],
        "portfolio": [round(float(v), 2) for v in port_growth],
        "benchmark": [round(float(v), 2) for v in bench_growth],
    }


def _histogram_chart_data(returns: pd.Series) -> Dict:
    return {
        "returns": [round(float(v), 5) for v in returns],
        "mean": round(float(returns.mean()), 5),
        "var_95": round(float(returns.quantile(0.05)), 5),
    }


def assemble_performance_report(account_id: str) -> Dict:
    """Native quantstats-parity performance report for a scope — pure computation, no DB
    writes, mirrors monte_carlo_engine.run_simulation()'s shape."""
    try:
        holdings, total_value = resolve_scope_holdings(account_id)
    except RuntimeError as e:
        logger.warning("Performance report failed for account_id=%s: %s", account_id, e)
        return {"status": "error", "message": str(e)}

    port_rets, bench_rets, data_warnings = get_scope_return_series(holdings, total_value)
    data_warnings = list(data_warnings)

    if port_rets is None:
        data_warnings.append(NOT_ENOUGH_DATA_WARNING)
        return {
            "status": "success",
            "account_id": account_id,
            "annualized_return": None,
            "metrics": None,
            "charts": None,
            "data_warnings": data_warnings,
        }

    rf_rate = float(load_config().get("RISK_FREE_RATE", 0.045))
    ann_return = annualized_return(port_rets)
    max_dd, drawdown_series = native_max_drawdown(port_rets)

    metrics = {
        "risk_adjusted_ratios": {
            "sortino_ratio": _sortino_ratio(port_rets, ann_return, rf_rate),
            "calmar_ratio": _calmar_ratio(ann_return, max_dd),
            "omega_ratio": _omega_ratio(port_rets, rf_rate),
            "profit_factor": _profit_factor(port_rets),
        },
        "drawdown_analytics": {
            "max_drawdown": round(max_dd, 4),
            **_drawdown_stats(drawdown_series),
        },
        "distribution_tail_stats": _distribution_stats(port_rets),
        "win_loss_stats": {**_win_loss_stats(port_rets), **_max_consecutive(port_rets)},
    }

    charts = {
        "underwater": _underwater_chart_data(drawdown_series),
        "cumulative_growth": (
            _cumulative_growth_chart_data(port_rets, bench_rets)
            if bench_rets is not None else None
        ),
        "monthly_heatmap": _monthly_heatmap_matrix(port_rets),
        "histogram": _histogram_chart_data(port_rets),
    }

    return {
        "status": "success",
        "account_id": account_id,
        "annualized_return": round(ann_return, 4),
        "metrics": metrics,
        "charts": charts,
        "data_warnings": data_warnings,
    }
