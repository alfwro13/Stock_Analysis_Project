# GUI name: "Claude MCP Server". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.
import contextlib
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Annotated, Any, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

import portfolio_metrics_engine
from accounts_engine import list_scope_accounts_with_values, oldest_quote_epoch
from config import BASE_CURRENCY, BASE_DIR, DATA_DIR, load_config
from constants import INVESTMENT_POLICY_MAX_CHARS, MCP_ACCOUNT_SCOPE_MAX_CHARS
from db_accounts import get_accounts as get_db_accounts, get_performance_cache
from db_helpers import parse_utc_epoch
from market_session_helpers import get_exchange_session_state
from page_helpers import get_portfolio_heat_row
from portfolio_service import persisted_fx_reads
from risk_orchestrator_engine import get_ticker_risk_contributions
from utils import sanitize_floats
from xray_engine import XRayScopeUnavailable, assemble_xray_report

logger = logging.getLogger(__name__)

MCP_PATH = "/mcp"
KEY_ENV = "MCP_API_KEY"
ENVELOPE_EXCHANGES = ("LSE", "NYSE")
STALE_AFTER_SECONDS = 900
NIGHTLY_STALE_AFTER_SECONDS = 5 * 86400  # Friday evening's run must still count as current on a Tuesday after a bank holiday
ACCOUNT_ALL = "all"
XRAY_TOP_POSITIONS = 5
XRAY_TOP_EXPOSURES = 8
XRAY_MAX_FINDINGS = 10
HEAT_TOP_CONTRIBUTORS = 5

server = MCPServer(
    "Claude MCP Server",
    instructions=(
        "Read-only view of the Quantamental Dashboard. Every tool response carries as_of (when the data was "
        "produced), market_state, units, and stale/missing markers. A null value means unavailable, never zero."
    ),
)

_READ_ONLY = ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True, open_world_hint=False)


def auth_failure(request: Request) -> Optional[JSONResponse]:
    if request.headers.get("X-API-Key"):
        return JSONResponse({"detail": "The API key cannot be used on this route"}, status_code=403)
    expected = os.environ.get(KEY_ENV, "")
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    if expected and scheme.lower() == "bearer" and secrets.compare_digest(token.strip().encode(), expected.encode()):
        return None
    if not expected:
        logger.warning("Refused a %s request: %s is not set", MCP_PATH, KEY_ENV)
    return JSONResponse({"detail": "Invalid or missing MCP key"}, status_code=401, headers={"WWW-Authenticate": "Bearer"})


def _iso_utc(epoch: float) -> str:
    return datetime.fromtimestamp(epoch, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def envelope(data: Any, *, as_of: Optional[float], units: dict, missing: list, stale_after_seconds: int = STALE_AFTER_SECONDS) -> dict:
    market_state = {exchange: get_exchange_session_state(exchange) for exchange in ENVELOPE_EXCHANGES}
    age_seconds = None if as_of is None else max(0, int(time.time() - as_of))
    overdue = age_seconds is not None and age_seconds > stale_after_seconds and "open" in market_state.values()
    return sanitize_floats({
        "as_of": None if as_of is None else _iso_utc(as_of),
        "age_seconds": age_seconds,
        "market_state": market_state,
        "base_currency": BASE_CURRENCY,
        "units": units,
        "stale": bool(missing) or overdue,
        "missing": missing,
        "data": data,
    })


_ACCOUNT_UNITS = {
    "amounts": "BASE_CURRENCY (base_currency); not the account's own currency",
    "equity_value": "market value of holdings; cash_balance is separate, total is their sum",
    "gain_1d/1w/1m/3m/1y": "BASE_CURRENCY gain over the window excluding deposits and withdrawals (an amount, not a percent)",
    "mwrr_pct": "percent, since inception (Modified Dietz)",
    "performance_pct": "percent over 1m / ytd / 1y from the scraped price history (Pension and House only)",
    "snapshot_at": "UTC time of the performance snapshot the Trading row was read from",
    "price_as_of": "date of the latest scraped price (Pension and House only)",
    "scope_id": "account id accepted by scope-aware tools; null when the account has no holdings",
}


@server.tool(name="get_accounts", annotations=_READ_ONLY)
def get_accounts() -> dict[str, Any]:
    """List every account (Trading, Pension, House, plus Ghostfolio accounts when enabled) with cash, equity value, P/L and period gains in the base currency."""
    missing = []
    with persisted_fx_reads():
        scope_accounts, _ = list_scope_accounts_with_values()
        trading = portfolio_metrics_engine.account_metrics_list(refresh_missing=False)["accounts"]
        other = portfolio_metrics_engine.other_accounts_list()["accounts"]

    scope_by_id = {a["id"]: a for a in scope_accounts}
    rows = []
    snapshot_times = []
    for metrics in trading:
        account_id = metrics["account_id"]
        snapshot = get_performance_cache(account_id)
        snapshot_at = snapshot["last_updated"] if snapshot else None
        if snapshot_at is None:
            missing.append(f"account {account_id}: performance snapshot not computed yet")
        else:
            snapshot_times.append(snapshot_at)
        scope_id = f"acct:{account_id}"
        rows.append({
            **metrics,
            "account_type": "Trading",
            "scope_id": scope_id if scope_id in scope_by_id else None,
            "snapshot_at": None if snapshot_at is None else _iso_utc(snapshot_at),
        })

    for scope_id, scope in scope_by_id.items():
        if not scope_id.startswith("acct:"):
            rows.append({
                "account_id": None, "name": scope["name"], "account_type": "Ghostfolio",
                "scope_id": scope_id, "equity_value": scope["value"],
            })

    for account in other:
        price_as_of = account["last_updated"]
        if price_as_of is None:
            missing.append(f"account {account['account_id']}: no price history, value unavailable")
        rows.append({
            "account_id": account["account_id"], "name": account["name"], "account_type": account["account_type"],
            "scope_id": None, "account_currency": account["currency"],
            "equity_value": None if price_as_of is None else account["current_value"],
            "performance_pct": account["performance"], "price_as_of": price_as_of,
        })

    return envelope(
        {"accounts": rows},
        as_of=min(snapshot_times) if snapshot_times else None,
        units=_ACCOUNT_UNITS,
        missing=missing,
    )


_AccountArg = Annotated[str, Field(max_length=MCP_ACCOUNT_SCOPE_MAX_CHARS)]


def _ghostfolio_scope_ids() -> list:
    return list(load_config().get("GHOSTFOLIO_ACCOUNTS", {}).get("active", []))


def _resolve_scope(account: str, *, allow_ghostfolio: bool) -> str:
    trading = [f"acct:{a['id']}" for a in get_db_accounts() if a["account_type"] == "Trading"]
    ghostfolio = _ghostfolio_scope_ids()
    if account == ACCOUNT_ALL or account in trading:
        return account
    if account in ghostfolio:
        if allow_ghostfolio:
            return account
        raise ToolError("get_holdings has no per-position metrics for Ghostfolio accounts; use get_xray for them.")
    valid = [ACCOUNT_ALL, *trading, *(ghostfolio if allow_ghostfolio else [])]
    raise ToolError("Unknown account. Valid values: " + ", ".join(valid) + " (the scope_id values from get_accounts).")


_HOLDING_UNITS = {
    "amounts": "BASE_CURRENCY (base_currency) unless a field says otherwise",
    "market_price": "latest cached quote in the instrument's own currency (market_price_currency); GBp means pence, not pounds",
    "market_price_in_base_currency": "market_price converted to the base currency; null when no usable FX rate exists",
    "average_buy_price": "base currency per share",
    "gain_pct": "percent versus total_investment",
    "allocation_pct": "percent of that row's own account equity (an account's rows add up to 100), not of the whole portfolio; get_xray has portfolio-wide weights",
    "day_change": "change since the previous close, per share, in market_price_currency",
    "day_change_pct": "percent change since the previous close (regular session only)",
    "pre_market_change_pct/post_market_change_pct": "percent versus the regular close, extended-hours only; never mixed into market_price or day_change",
    "priced_at_cost": "true when no usable price or FX rate exists; market_value, gain_value, gain_pct and allocation_pct are then null",
    "as_of": "oldest market quote behind the prices; Treasury Bills are priced analytically and carry no quote time",
}


def _holding_row(h: dict) -> dict:
    unpriced = h["priced_at_cost"]
    return {
        "account_id": h["account_id"], "account_name": h["account_name"], "scope_id": f"acct:{h['account_id']}",
        "ticker": h["ticker"], "company_name": h["company_name"], "asset_class": h["asset_class"], "shares": h["shares"],
        "market_price": h["market_price"], "market_price_currency": h["market_price_currency"],
        "market_price_in_base_currency": h["market_price_in_base_currency"],
        "average_buy_price": h["average_buy_price"], "total_investment": h["total_investment"],
        "market_value": None if unpriced else h["market_value"],
        "gain_value": None if unpriced else h["gain_value"],
        "gain_pct": None if unpriced else h["gain_pct"],
        "allocation_pct": None if unpriced else h["allocation_pct"],
        "accumulated_dividends": h["accumulated_dividends"],
        "day_change": h["market_change_24h"], "day_change_pct": h["market_change_pct_24h"],
        "pre_market_change_pct": h["pre_market_change_pct"], "post_market_change_pct": h["post_market_change_pct"],
        "priced_at_cost": unpriced,
    }


@server.tool(name="get_holdings", annotations=_READ_ONLY)
def get_holdings(account: _AccountArg = ACCOUNT_ALL) -> dict[str, Any]:
    """Open positions per built-in Trading account (shares, cost basis, market value, P/L, allocation within the account, day change); account is "all" or a scope_id from get_accounts."""
    scope = _resolve_scope(account, allow_ghostfolio=False)
    with persisted_fx_reads():
        rows = portfolio_metrics_engine.holdings_with_metrics_all_accounts()["holdings"]
    if scope != ACCOUNT_ALL:
        account_id = int(scope.split(":", 1)[1])
        rows = [r for r in rows if r["account_id"] == account_id]

    missing = [
        f"{r['ticker']} ({r['account_name']}): no usable price or FX rate, so market value, gain and allocation are null"
        for r in rows if r["priced_at_cost"]
    ]
    if scope == ACCOUNT_ALL and _ghostfolio_scope_ids():
        missing.append("Ghostfolio accounts are not covered by get_holdings; get_xray includes them")
    return envelope(
        {"account": scope, "holdings": [_holding_row(r) for r in rows]},
        as_of=oldest_quote_epoch(sorted({r["ticker"] for r in rows})),
        units=_HOLDING_UNITS,
        missing=missing,
    )


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


def _xray_as_of(cache_date: Optional[str]) -> Optional[float]:
    if not cache_date:
        return None
    return datetime.strptime(cache_date[:10], "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp()


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


@server.tool(name="get_xray", annotations=_READ_ONLY)
def get_xray(account: _AccountArg = ACCOUNT_ALL) -> dict[str, Any]:
    """Portfolio X-ray summary: concentration, beta, volatility, drawdown, VaR, correlation, sector, continent and currency exposure and breaches of the X-Ray Allocation Targets; account is "all" or a scope_id from get_accounts."""
    scope = _resolve_scope(account, allow_ghostfolio=True)
    try:
        with persisted_fx_reads():
            report = assemble_xray_report(scope)
    except XRayScopeUnavailable as e:
        raise ToolError(str(e)) from e
    data, missing = _xray_summary(report)
    return envelope(
        data, as_of=_xray_as_of(report["risk_metrics"]["cache_date"]), units=_XRAY_UNITS, missing=missing,
        stale_after_seconds=NIGHTLY_STALE_AFTER_SECONDS,
    )


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


@server.tool(name="get_heat_index", annotations=_READ_ONLY)
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
            "computed_at": _iso_utc(computed_epoch) if (computed_epoch := parse_utc_epoch(row["last_updated"])) else None,
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


POLICY_URI = "policy://investment-policy"
POLICY_PATH = DATA_DIR / "investment_policy.md"
POLICY_TEMPLATE_PATH = BASE_DIR / "assets" / "investment_policy_template.md"
_POLICY_RULE = (
    "Tell the user the investment policy is unavailable. Do not invent goals, limits or rules, do not assume defaults, "
    "and do not present any limit as the operator's own."
)


def _policy_unavailable(reason: str) -> str:
    return f"POLICY NOT LOADED: {reason}\n\n{_POLICY_RULE}"


def _policy_missing() -> str:
    template = POLICY_TEMPLATE_PATH.read_text(encoding="utf-8")
    return (
        "POLICY NOT CONFIGURED: data/investment_policy.md is missing or empty.\n\n"
        f"{_POLICY_RULE} The operator writes this file; these are the sections it should cover:\n\n{template}"
    )


@server.resource(
    POLICY_URI, name="investment_policy", title="Investment policy", mime_type="text/markdown",
    description=(
        "The operator's investment policy: goals and horizon per account, risk tolerance, maximum position, sector, "
        "country and currency weights, tax wrappers and constraints, never-do list, profit-taking style and preferred "
        "response format. Read it BEFORE any analysis or recommendation and apply it. If it says the policy is not "
        "configured, say so instead of inventing one. Read-only."
    ),
)
def investment_policy() -> str:
    try:
        with POLICY_PATH.open(encoding="utf-8-sig") as policy_file:
            text = policy_file.read(INVESTMENT_POLICY_MAX_CHARS + 1)
    except FileNotFoundError:
        return _policy_missing()
    except (OSError, UnicodeDecodeError) as e:
        logger.error("Investment policy file could not be read: %s", e)
        return _policy_unavailable("data/investment_policy.md exists but could not be read as UTF-8 text.")
    if not text.strip():
        return _policy_missing()
    if len(text) > INVESTMENT_POLICY_MAX_CHARS:
        return _policy_unavailable(f"data/investment_policy.md is longer than {INVESTMENT_POLICY_MAX_CHARS} characters.")
    return text


class _Endpoint:
    """ASGI endpoint for /mcp; answers 405 to everything but POST because stateless JSON mode has no stream to open (the SDK would hold a GET open forever)."""

    __name__ = "mcp_endpoint"  # SlowAPIMiddleware reads handler.__name__

    def __init__(self):
        self.active = []

    async def __call__(self, scope, receive, send):
        if scope["method"] != "POST":
            response = Response(status_code=405, headers={"Allow": "POST"})
        elif not self.active:
            response = Response(status_code=503)
        else:
            await self.active[-1](scope, receive, send)
            return
        await response(scope, receive, send)


endpoint = _Endpoint()


def _transport_security(hosts) -> TransportSecuritySettings:
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=[*hosts, *(f"{host}:*" for host in hosts)],
        allowed_origins=[f"{scheme}://{host}{port}" for host in hosts for scheme in ("http", "https") for port in ("", ":*")],
    )


@contextlib.asynccontextmanager
async def run(trusted_hosts):
    # The SDK's session manager can only run once, so every app lifespan (several per process under test) builds its own.
    app = server.streamable_http_app(
        streamable_http_path=MCP_PATH, stateless_http=True, json_response=True,
        transport_security=_transport_security(trusted_hosts),
    )
    manager = server.session_manager
    endpoint.active.append(app)
    try:
        async with manager.run():
            yield
    finally:
        endpoint.active.remove(app)
