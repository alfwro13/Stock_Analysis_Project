from typing import Any

import portfolio_metrics_engine
from accounts_engine import list_scope_accounts_with_values
from db_accounts import get_performance_cache
from portfolio_service import persisted_fx_reads

from ..envelope import envelope, iso_utc
from ..server import READ_ONLY, server

_ACCOUNT_UNITS = {
    "amounts": "BASE_CURRENCY (base_currency); not the account's own currency",
    "equity_value": "market value of holdings; cash_balance is separate, total is their sum",
    "gain_1d/1w/1m/3m/1y": "BASE_CURRENCY gain over the window excluding deposits and withdrawals (an amount, not a percent)",
    "mwrr_pct": "percent, since inception (Modified Dietz)",
    "performance_pct": "percent over 1m / ytd / 1y from the scraped price history (Pension and House only)",
    "snapshot_at": "UTC time of the performance snapshot the Trading row was read from",
    "price_as_of": "date of the latest scraped price (Pension and House only)",
    "priced_at_cost_count": "Trading only: holdings with no usable price or FX rate, valued at cost; when above 0 equity_value, unrealized_pnl, gain_* and mwrr_pct are null",
    "scope_id": "account id accepted by scope-aware tools; null when the account has no holdings",
}


_COST_BASIS_DEPENDENT = (
    "equity_value", "unrealized_pnl", "gain_1d", "gain_1w", "gain_1m", "gain_3m", "gain_1y", "mwrr_pct",
)


@server.tool(name="get_accounts", annotations=READ_ONLY)
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
        unpriced = metrics.get("priced_at_cost_count")
        if unpriced:
            metrics = {**metrics, **dict.fromkeys(_COST_BASIS_DEPENDENT)}
            missing.append(f"account {account_id}: {unpriced} holding(s) have no usable price or FX rate and are valued at cost, so equity, P/L, period gains and MWRR are unavailable")
        scope_id = f"acct:{account_id}"
        rows.append({
            **metrics,
            "account_type": "Trading",
            "scope_id": scope_id if scope_id in scope_by_id else None,
            "snapshot_at": None if snapshot_at is None else iso_utc(snapshot_at),
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
