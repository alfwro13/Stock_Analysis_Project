from typing import Any

import portfolio_metrics_engine
from accounts_engine import oldest_quote_epoch
from portfolio_service import persisted_fx_reads

from ..envelope import envelope
from ..scope import ACCOUNT_ALL, AccountArg, ghostfolio_scope_ids, resolve_scope
from ..server import READ_ONLY, server

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


@server.tool(name="get_holdings", annotations=READ_ONLY)
def get_holdings(account: AccountArg = ACCOUNT_ALL) -> dict[str, Any]:
    """Open positions per built-in Trading account (shares, cost basis, market value, P/L, allocation within the account, day change); account is "all" or a scope_id from get_accounts."""
    scope = resolve_scope(account, allow_ghostfolio=False)
    with persisted_fx_reads():
        rows = portfolio_metrics_engine.holdings_with_metrics_all_accounts()["holdings"]
    if scope != ACCOUNT_ALL:
        account_id = int(scope.split(":", 1)[1])
        rows = [r for r in rows if r["account_id"] == account_id]

    missing = [
        f"{r['ticker']} ({r['account_name']}): no usable price or FX rate, so market value, gain and allocation are null"
        for r in rows if r["priced_at_cost"]
    ]
    if scope == ACCOUNT_ALL and ghostfolio_scope_ids():
        missing.append("Ghostfolio accounts are not covered by get_holdings; get_xray includes them")
    return envelope(
        {"account": scope, "holdings": [_holding_row(r) for r in rows]},
        as_of=oldest_quote_epoch(sorted({r["ticker"] for r in rows})),
        units=_HOLDING_UNITS,
        missing=missing,
    )
