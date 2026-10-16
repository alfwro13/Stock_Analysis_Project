# GUI name: "Claude MCP Server". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.
import contextlib
import logging
import os
import secrets
import time
from datetime import datetime, timezone
from typing import Any, Optional

from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

import portfolio_metrics_engine
from accounts_engine import list_scope_accounts_with_values
from config import BASE_CURRENCY
from db_accounts import get_performance_cache
from market_session_helpers import get_exchange_session_state
from portfolio_service import persisted_fx_reads
from utils import sanitize_floats

logger = logging.getLogger(__name__)

MCP_PATH = "/mcp"
KEY_ENV = "MCP_API_KEY"
ENVELOPE_EXCHANGES = ("LSE", "NYSE")
STALE_AFTER_SECONDS = 900

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


def envelope(data: Any, *, as_of: Optional[float], units: dict, missing: list) -> dict:
    market_state = {exchange: get_exchange_session_state(exchange) for exchange in ENVELOPE_EXCHANGES}
    age_seconds = None if as_of is None else max(0, int(time.time() - as_of))
    overdue = age_seconds is not None and age_seconds > STALE_AFTER_SECONDS and "open" in market_state.values()
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
