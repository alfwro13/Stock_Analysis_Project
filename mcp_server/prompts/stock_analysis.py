from typing import Annotated

from pydantic import Field

from mcp.shared.exceptions import MCPError
from mcp_types import INVALID_PARAMS

from ai_engine import AIPromptEngine
from portfolio_service import persisted_fx_reads

from ..server import server
from ..tools.get_ticker_snapshot import TickerArg
from .shared import dashboard_analysis_message, valid_ticker

STOCK_MODES = (
    ("devils_advocate", "The Devil's Advocate analysis", "Argues against the dashboard's current verdict on one ticker while acknowledging its sector, beta and macro tailwinds."),
    ("risk_reward_audit", "Risk/Reward Audit", "Audits one ticker's stop-loss buffer, unrealised P&L, VaR, macro yield threat and 52-week range, and suggests sizing or stop changes."),
    ("quantamental_deep_dive", "Quantamental Deep-Dive", "Checks whether one ticker's fundamentals validate its price action, sector rank and ML/sentiment signals, ending in a 12-month conviction rating."),
    ("earnings_strategy", "Earnings Strategy", "Plays out holding, trimming or hedging one ticker through its approaching earnings, using the options and earnings volatility edge."),
)


def _register(name: str, mode: str, summary: str) -> None:
    @server.prompt(
        name=name, title=mode,
        description=f"{summary} The text is the one the Stock Detail page's AI prompt button copies, with the investment policy read first. Read-only analysis.",
    )
    def render(ticker: Annotated[TickerArg, Field(description="The ticker to analyse")]) -> str:
        ticker = valid_ticker(ticker)
        with persisted_fx_reads():
            app_prompt = AIPromptEngine().generate_prompt(ticker, mode)
        if not app_prompt:
            raise MCPError(INVALID_PARAMS, "Unknown ticker: the dashboard has no stock data for it.")
        return dashboard_analysis_message(app_prompt)


for _name, _mode, _summary in STOCK_MODES:
    _register(_name, _mode, _summary)
