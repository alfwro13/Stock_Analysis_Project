from typing import Annotated

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from config import load_config
from constants import MCP_ACCOUNT_SCOPE_MAX_CHARS
from db_accounts import get_accounts as get_db_accounts
from utils import INVALID_TICKER_MESSAGE, is_valid_ticker

ACCOUNT_ALL = "all"

AccountArg = Annotated[str, Field(max_length=MCP_ACCOUNT_SCOPE_MAX_CHARS)]


def ghostfolio_scope_ids() -> list:
    return list(load_config().get("GHOSTFOLIO_ACCOUNTS", {}).get("active", []))


def resolve_scope(account: str, *, allow_ghostfolio: bool) -> str:
    trading = [f"acct:{a['id']}" for a in get_db_accounts() if a["account_type"] == "Trading"]
    ghostfolio = ghostfolio_scope_ids()
    if account == ACCOUNT_ALL or account in trading:
        return account
    if account in ghostfolio:
        if allow_ghostfolio:
            return account
        raise ToolError("get_holdings has no per-position metrics for Ghostfolio accounts; use get_xray for them.")
    valid = [ACCOUNT_ALL, *trading, *(ghostfolio if allow_ghostfolio else [])]
    raise ToolError("Unknown account. Valid values: " + ", ".join(valid) + " (the scope_id values from get_accounts).")


def valid_ticker(ticker: str) -> str:
    stripped = ticker.strip()
    if not is_valid_ticker(stripped):
        raise ToolError("Invalid ticker: " + INVALID_TICKER_MESSAGE)
    return stripped
