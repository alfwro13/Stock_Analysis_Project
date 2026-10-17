from typing import Annotated, Literal, Optional

from pydantic import Field

from config import BASE_CURRENCY

from ..scope import ACCOUNT_ALL, AccountArg
from ..server import server
from ..tools.get_ticker_snapshot import TickerArg
from ..tools.pretrade_check import AmountArg
from .shared import DATA_GAPS_SECTION, GROUND_RULES, policy_step, scope_text, valid_scope, valid_ticker


@server.prompt(
    name="pre_trade_review", title="Pre-trade check",
    description="Checks a proposed buy or sale of one ticker against the data and the investment policy: go, wait or avoid with an evidence table, the position size after the trade and upcoming events. Read-only analysis.",
)
def pre_trade_review(
    ticker: Annotated[TickerArg, Field(description="The ticker to buy or sell")],
    side: Annotated[Literal["buy", "sell"], Field(description='"buy" or "sell"')],
    amount: Annotated[Optional[AmountArg], Field(description=f"Value of the trade in {BASE_CURRENCY}; asked for when side is buy and it is left out")] = None,
    account: Annotated[AccountArg, Field(description='"all" (default) or a scope_id from get_accounts')] = ACCOUNT_ALL,
) -> str:
    ticker = valid_ticker(ticker)
    scope = valid_scope(account)
    amount_text = "" if amount is None else f" of {amount:,.2f} {BASE_CURRENCY}"
    ask_amount = ""
    check_args = f'ticker="{ticker}", side="{side}", account="{scope}"'
    if side == "buy" and amount is None:
        check_args += ", amount=<the amount I give you>"
        ask_amount = (
            f"No amount was given for this buy. Before anything else, ask me how much (in {BASE_CURRENCY}) I plan to buy and wait "
            "for my answer; then use it as the amount in the steps below.\n\n"
        )
    elif side == "buy":
        check_args += f", amount={amount:.2f}"
    return (
        f"Pre-trade check: {side} {ticker}{amount_text}. Scope: {scope_text(scope)}.\n\n"
        f"{ask_amount}"
        f"{GROUND_RULES}\n\n"
        "Do these steps in this order:\n"
        f"1. {policy_step()}\n"
        f'2. Call get_ticker_snapshot(ticker="{ticker}") for price, trend, technicals, valuation, forensic scores, ML confidence, Position Targets and next earnings. '
        "If it says the ticker is unknown, tell me the dashboard does not track it and treat that evidence as missing.\n"
        f'3. Call get_holdings(account="{scope}") for what I already hold of {ticker} and how large that account is.\n'
        f'4. Call get_xray(account="{scope}") for the sector, currency, continent and concentration weights this trade would change.\n'
        "5. Call get_heat_index() for the current Portfolio Heat Index (all accounts combined).\n"
        "6. Call get_market_regime() for the market regime, stress and Fear & Greed.\n"
        "7. Call get_alerts() for recent alerts and the Trap Monitor phase; look for this ticker and its sector.\n"
        f'8. Call get_news(ticker="{ticker}") for recent headlines and sentiment.\n'
        f"9. Call pretrade_check({check_args}). A buy returns the Heat Index tier after the addition, its VaR, correlation and drawdown inputs and a smaller size that would help; "
        "a sell returns no verdict, only the current Heat Index, so judge a sale from the other evidence. "
        "Treat its verdict as one input, not the answer, and say why when it is null.\n\n"
        "Then answer with these sections, in this order:\n"
        "1. Verdict: exactly one of GO, WAIT or AVOID, with two sentences of reasoning. A never-do rule or hard limit in the policy that the trade would break means AVOID.\n"
        "2. Evidence table: one row per piece of evidence with columns Evidence, Source tool, Value (with as_of), and Reads as (supports, against, neutral or unknown).\n"
        f"3. Position size after the trade: my {ticker} position before, the trade, and the position after, as a weight, "
        "compared with each policy limit that applies (one position, sector, country, currency) and marked OK, breach or no policy.\n"
        "4. Upcoming events: next earnings date and any other dated event the data shows within the next two weeks; say so if there is none.\n"
        f"5. {DATA_GAPS_SECTION}"
    )
