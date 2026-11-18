from typing import Annotated, Literal

from pydantic import Field

from constants import MCP_DISCOVERY_MAX_ROWS, MCP_DISCOVERY_NAME_MAX_CHARS, MCP_DISCOVERY_TEXT_MAX_CHARS
from db.helpers import get_portfolio_tickers, get_universe_tickers, get_watchlist_only_tickers
from utils import clean_display_text

ScopeArg = Annotated[Literal["all", "new", "watchlist", "portfolio"], Field(description="all, or only tickers that are new (neither held nor watched), watched-only, or held")]
LimitArg = Annotated[int, Field(ge=1, le=MCP_DISCOVERY_MAX_ROWS)]
Score = Annotated[float, Field(ge=0, le=100)]


def list_flags() -> dict[str, str]:
    """Rule 14: a held ticker is `portfolio` even when it is also watched; a watched-only ticker is `watchlist`."""
    return {**{t: "watchlist" for t in get_watchlist_only_tickers()}, **{t: "portfolio" for t in get_portfolio_tickers()}}


def scoped_rows(rows: list[dict], scope: str) -> tuple[list[dict], int]:
    """Adds `list` to every ticker row and keeps the rows of `scope`; a `new` row must be in the market universe the Settings Freetrade switch allows, and the count of those it dropped is returned so an empty universe read is visible."""
    flags = list_flags()
    buyable = set(get_universe_tickers())
    kept, excluded = [], 0
    for row in rows:
        flag = flags.get(row["ticker"], "new")
        if scope not in ("all", flag):
            continue
        if flag == "new" and row["ticker"] not in buyable:
            excluded += 1
            continue
        kept.append({**row, "list": flag})
    return kept, excluded


def identity(row: dict) -> dict:
    return {
        "ticker": row["ticker"],
        "company_name": clean_display_text(row.get("company_name"), MCP_DISCOVERY_NAME_MAX_CHARS),
        "sector": clean_display_text(row.get("sector"), MCP_DISCOVERY_TEXT_MAX_CHARS),
        "exchange": clean_display_text(row.get("exchange"), MCP_DISCOVERY_TEXT_MAX_CHARS),
        "currency": row.get("currency"),
        "list": row["list"],
    }


def page(rows: list[dict], limit: int) -> dict:
    return {"total_matches": len(rows), "truncated": len(rows) > limit, "rows": rows[:limit]}
