from typing import Any

from db_helpers import parse_utc_epoch
from page_helpers import get_portfolio_heat_row
from risk_orchestrator_engine import get_ticker_risk_contributions

from ..envelope import NIGHTLY_STALE_AFTER_SECONDS, envelope, iso_from_stored_utc
from ..scope import ACCOUNT_ALL
from ..server import READ_ONLY, server

HEAT_TOP_CONTRIBUTORS = 5

_HEAT_UNITS = {
    "phi_score": "0-100 composite of VaR, correlation and drawdown (higher is hotter); tier GREEN below 40, YELLOW 40-75, RED 75 and above by default (Settings)",
    "var.pct_of_equity": "percent: 1-day 95% VaR as a share of portfolio equity",
    "max_correlation.value": "-1 to 1: highest pairwise correlation between two holdings",
    "max_drawdown.pct": "percent, absolute value of the worst peak-to-trough fall",
    "top_risk_contributors.risk_score": "0-100 per-position score from the same three inputs, tier by the same thresholds",
    "top_risk_contributors.marginal_var_contribution_pct": "percent of the portfolio's VaR that comes from this position",
    "top_risk_contributors.stop_distance_pct": "percent of price above the ATR stop-loss; negative means the price is through the stop",
    "computed_at": "UTC time of the Risk Orchestrator Scan that wrote the figures (daily on weekdays)",
}
_HEAT_INPUTS = (("var_pct_of_equity", "VaR"), ("max_correlation", "correlation"), ("drawdown_pct", "drawdown"))


@server.tool(name="get_heat_index", annotations=READ_ONLY)
def get_heat_index() -> dict[str, Any]:
    """Portfolio Heat Index for all accounts combined (0-100 risk score, Green/Yellow/Red tier, its VaR, correlation and drawdown inputs) and the five positions with the highest Risk Contribution."""
    row = get_portfolio_heat_row(ACCOUNT_ALL)
    contributors = get_ticker_risk_contributions(HEAT_TOP_CONTRIBUTORS)
    missing = []
    heat = None
    if row is None:
        missing.append("Portfolio Heat Index has not been computed yet (the Risk Orchestrator Scan has not run, or the portfolio is empty)")
    else:
        missing += [
            f"Heat Index {label} input is not available, so the score counts it as zero risk"
            for key, label in _HEAT_INPUTS if row[key] is None
        ]
        heat = {
            "phi_score": row["phi_score"], "tier": row["tier"],
            "var": {"pct_of_equity": row["var_pct_of_equity"], "tier": row["var_tier"]},
            "max_correlation": {"value": row["max_correlation"], "tier": row["correlation_tier"]},
            "max_drawdown": {"pct": row["drawdown_pct"], "tier": row["drawdown_tier"]},
            "breakdown": row["breakdown"],
            "computed_at": iso_from_stored_utc(row["last_updated"]),
        }
        if not contributors:
            missing.append("No per-position Risk Contribution rows exist yet")
    epochs = [e for e in (parse_utc_epoch(r["last_updated"]) for r in [*([row] if row else []), *contributors]) if e]
    return envelope(
        {
            "heat_index": heat,
            "top_risk_contributors": [
                {key: c[key] for key in ("ticker", "risk_score", "risk_tier", "marginal_var_contribution_pct", "max_pairwise_correlation", "stop_distance_pct")}
                for c in contributors
            ],
        },
        as_of=min(epochs) if epochs else None, units=_HEAT_UNITS, missing=missing,
        stale_after_seconds=NIGHTLY_STALE_AFTER_SECONDS,
    )
