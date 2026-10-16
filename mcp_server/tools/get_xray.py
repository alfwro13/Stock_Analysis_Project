from typing import Any

from mcp.server.mcpserver.exceptions import ToolError

from portfolio_service import persisted_fx_reads
from xray_engine import XRayScopeUnavailable, assemble_xray_report

from ..envelope import NIGHTLY_STALE_AFTER_SECONDS, date_epoch, envelope
from ..scope import ACCOUNT_ALL, AccountArg, resolve_scope
from ..server import READ_ONLY, server

XRAY_TOP_POSITIONS = 5
XRAY_TOP_EXPOSURES = 8
XRAY_MAX_FINDINGS = 10

_XRAY_UNITS = {
    "amounts": "BASE_CURRENCY (base_currency); cash is excluded everywhere",
    "weights": "fraction of invested capital (0.25 = 25%); ETFs are looked through for sector and geography",
    "risk.annualized_vol": "fraction (0.2 = 20% a year)",
    "risk.max_drawdown": "fraction, negative (-0.18 = an 18% fall from the peak)",
    "risk.var_95_1d": "BASE_CURRENCY: parametric 1-day 95% Value at Risk",
    "risk.portfolio_beta": "versus the benchmark named in risk.benchmark over the trailing year of trading days",
    "correlation": "-1 to 1, from the cached pairwise matrix",
    "geographic_exposure": "by continent (look-through), not by country; per-country figures appear only as findings when a country target is set",
    "currency_exposure": "weight by the currency each position is recorded in",
    "findings": "breaches of the X-Ray Allocation Targets set in Settings, first findings_cap of findings_total in report order; targets_checked counts every target evaluated",
    "risk_cache_date": "UTC date of the nightly risk cache the beta, volatility and correlation come from (as_of is that date at 00:00 UTC)",
}


def _xray_findings(recommendations: dict) -> tuple[int, list]:
    checked = 0
    findings = []
    for section, items in recommendations.items():
        for item in items:
            checked += 1
            if item["status"] in ("exceeds", "below"):
                findings.append({
                    "section": section, "target": item["category"], "status": item["status"],
                    "current_value": item["current_value"], "min": item["min"], "max": item["max"],
                    "unit": item["unit"], "message": item["message"],
                })
    return checked, findings


def _xray_summary(report: dict) -> tuple[dict, list]:
    risk = report["risk_metrics"]
    holdings = report["holdings"]
    checked, findings = _xray_findings(report["recommendations"])
    most_correlated = sorted(
        (h for h in holdings if h["max_pairwise_correlation"] is not None),
        key=lambda h: h["max_pairwise_correlation"], reverse=True,
    )[:3]
    missing = [
        f"risk.{key}: not available (see data_warnings)"
        for key in ("portfolio_beta", "annualized_vol", "max_drawdown", "var_95_1d", "sharpe_ratio")
        if risk[key] is None
    ]
    missing += [
        f"correlation.{label}: not available (see data_warnings)"
        for label, key in (("average", "avg_pairwise_correlation"), ("maximum", "max_pairwise_correlation"))
        if risk[key] is None
    ]
    data = {
        "scope_id": report["account_id"],
        "portfolio_total_value": report["portfolio_total_value"],
        "portfolio_total_investment": report["portfolio_total_investment"],
        "positions": len(holdings),
        "concentration": {
            **{key: report["concentration"][key] for key in ("hhi", "top5_weight", "top10_weight")},
            "largest_positions": [
                {key: h[key] for key in ("symbol", "name", "weight", "value")} for h in holdings[:XRAY_TOP_POSITIONS]
            ],
        },
        "risk": {key: risk[key] for key in ("portfolio_beta", "annualized_vol", "max_drawdown", "var_95_1d", "sharpe_ratio", "benchmark")},
        "risk_cache_date": risk["cache_date"],
        "correlation": {
            "average": risk["avg_pairwise_correlation"],
            "maximum": risk["max_pairwise_correlation"],
            "most_correlated_holdings": [
                {"symbol": h["symbol"], "max_pairwise_correlation": h["max_pairwise_correlation"]} for h in most_correlated
            ],
        },
        "sector_exposure": report["sector_allocation"][:XRAY_TOP_EXPOSURES],
        "geographic_exposure": report["geographic_allocation"][:XRAY_TOP_EXPOSURES],
        "currency_exposure": report["fx_exposure"][:XRAY_TOP_EXPOSURES],
        "targets_checked": checked,
        "findings_total": len(findings),
        "findings_cap": XRAY_MAX_FINDINGS,
        "findings": findings[:XRAY_MAX_FINDINGS],
        "data_warnings": report["data_warnings"],
    }
    return data, missing


@server.tool(name="get_xray", annotations=READ_ONLY)
def get_xray(account: AccountArg = ACCOUNT_ALL) -> dict[str, Any]:
    """Portfolio X-ray summary: concentration, beta, volatility, drawdown, VaR, correlation, sector, continent and currency exposure and breaches of the X-Ray Allocation Targets; account is "all" or a scope_id from get_accounts."""
    scope = resolve_scope(account, allow_ghostfolio=True)
    try:
        with persisted_fx_reads():
            report = assemble_xray_report(scope)
    except XRayScopeUnavailable as e:
        raise ToolError(str(e)) from e
    data, missing = _xray_summary(report)
    return envelope(
        data, as_of=date_epoch(report["risk_metrics"]["cache_date"]), units=_XRAY_UNITS, missing=missing,
        stale_after_seconds=NIGHTLY_STALE_AFTER_SECONDS,
    )
