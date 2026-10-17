from typing import Annotated, Any, Literal, Optional

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from config import BASE_CURRENCY
from constants import MCP_PRETRADE_AMOUNT_MAX
from db_helpers import parse_utc_epoch
from page_helpers import get_portfolio_heat_row
from portfolio_service import persisted_fx_reads
from risk_orchestrator_engine import evaluate_pretrade_check
from utils import normalize_ticker
from xray_engine import XRayScopeUnavailable

from ..envelope import NIGHTLY_STALE_AFTER_SECONDS, date_epoch, envelope
from ..scope import ACCOUNT_ALL, AccountArg, resolve_scope, valid_ticker
from ..server import READ_ONLY, server
from .get_heat_index import heat_summary
from .get_ticker_snapshot import TickerArg

AmountArg = Annotated[float, Field(gt=0, le=MCP_PRETRADE_AMOUNT_MAX, allow_inf_nan=False)]

_UNITS = {
    "amount/proposed_value": "BASE_CURRENCY (base_currency): the value of the position to add",
    "verdict": "approve, warn or reject from the Portfolio Heat Index tier after the addition (Green, Yellow, Red), with the same thresholds and weights as the Heat Index; advisory only, the app executes no trades; null when an input could not be computed",
    "phi_score": "0-100 Portfolio Heat Index after the addition (higher is hotter)",
    "var.pct_of_equity": "percent: 1-day 95% VaR as a share of the portfolio equity after the addition",
    "max_correlation.value": "-1 to 1: highest pairwise correlation between two holdings after the addition",
    "max_drawdown.pct": "percent, absolute value of the account's current worst peak-to-trough fall; not re-simulated for the addition",
    "hypothetical_weight": "fraction of the new portfolio total (0.05 = 5%)",
    "new_portfolio_total_value": "BASE_CURRENCY: the account's value plus the addition",
    "suggested_reduced_value": "BASE_CURRENCY: the largest smaller addition that resolves to a better tier; null when no smaller size helps (for example when drawdown drives the breach)",
    "risk_cache_date": "YYYY-MM-DD of the nightly X-ray risk cache behind the figures; stale after 5 days (a missed nightly run)",
}
_SELL_NOTE = (
    "pretrade_check only models adding a position, so it gives no verdict for a sale. "
    "The account's current Portfolio Heat Index is returned for context."
)


def _sell_context(ticker: str, scope: str) -> dict[str, Any]:
    row = get_portfolio_heat_row(scope)
    heat = None
    missing: list[str] = []
    if row is None:
        missing.append("Portfolio Heat Index has not been computed for this account (the Risk Orchestrator Scan has not run, or has no scan for it)")
    else:
        heat, missing = heat_summary(row)
    as_of = None if row is None else parse_utc_epoch(row["last_updated"]) or None
    return envelope(
        {"side": "sell", "ticker": ticker, "account": scope, "verdict": None, "note": _SELL_NOTE, "heat_index": heat},
        as_of=as_of, units=_UNITS, missing=missing, stale_after_seconds=NIGHTLY_STALE_AFTER_SECONDS,
    )


def _buy_check(ticker: str, scope: str, amount: float) -> dict[str, Any]:
    try:
        with persisted_fx_reads():
            check = evaluate_pretrade_check(scope, ticker, amount, read_only=True)
    except XRayScopeUnavailable as e:
        raise ToolError(str(e)) from e
    missing = list(check["data_warnings"])
    if check["unavailable_inputs"]:
        missing.append(
            "No verdict: the " + ", ".join(check["unavailable_inputs"]) + " input could not be computed, and the Heat Index would count it as zero risk"
        )
    return envelope(
        {
            "side": "buy", "ticker": ticker, "account": scope, "proposed_value": check["proposed_value"],
            "verdict": check["verdict"],
            "tier": check["tier"],
            "phi_score": check["phi_score"],
            "breached_constraint": check["breached_constraint"],
            "var": {"pct_of_equity": check["var_pct_of_equity"], "tier": check["var_tier"]},
            "max_correlation": {"value": check["max_correlation"], "tier": check["correlation_tier"]},
            "max_drawdown": {"pct": check["drawdown_pct"], "tier": check["drawdown_tier"]},
            "hypothetical_weight": check["hypothetical_weight"],
            "new_portfolio_total_value": check["new_portfolio_total_value"],
            "suggested_reduced_value": check["suggested_reduced_value"],
            "risk_cache_date": check["risk_cache_date"],
        },
        as_of=date_epoch(check["risk_cache_date"]), units=_UNITS, missing=missing,
        stale_after_seconds=NIGHTLY_STALE_AFTER_SECONDS,
    )


@server.tool(name="pretrade_check", annotations=READ_ONLY)
def pretrade_check(
    ticker: TickerArg, side: Literal["buy", "sell"], amount: Optional[AmountArg] = None, account: AccountArg = ACCOUNT_ALL,
) -> dict[str, Any]:
    """Advisory what-if for adding amount (in the base currency) of ticker to account ("all" or a scope_id from get_accounts): approve, warn or reject from the Portfolio Heat Index tier after the addition, with the VaR, correlation and drawdown behind it and a smaller size that would help. side buy requires amount; side sell gives no verdict, only the account's current Heat Index. Nothing is stored and no data is fetched: a ticker without cached price history gets no verdict."""
    ticker = normalize_ticker(valid_ticker(ticker))
    scope = resolve_scope(account, allow_ghostfolio=True)
    if side == "sell":
        return _sell_context(ticker, scope)
    if amount is None:
        raise ToolError(f"amount is required for a buy: the value to add, in {BASE_CURRENCY}.")
    return _buy_check(ticker, scope, amount)
