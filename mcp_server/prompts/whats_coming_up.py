from typing import Annotated

from pydantic import Field

from ..scope import ACCOUNT_ALL, AccountArg
from ..server import server
from ..tools.get_calendar import DaysArg
from .shared import DATA_GAPS_SECTION, GROUND_RULES, policy_step, scope_text, valid_scope


@server.prompt(
    name="whats_coming_up", title="What's coming up?",
    description="Lists the earnings, ex-dividend dates and macro releases ahead, and what each means for my positions and the investment policy. Read-only analysis.",
)
def whats_coming_up(
    days: Annotated[DaysArg, Field(description="Look-ahead window in days, 1 to 30 (default 7)")] = 7,
    account: Annotated[AccountArg, Field(description='"all" (default) or a scope_id from get_accounts, for the positions')] = ACCOUNT_ALL,
) -> str:
    scope = valid_scope(account)
    return (
        f"What's coming up in the next {days} days? Positions scope: {scope_text(scope)}.\n\n"
        f"{GROUND_RULES}\n\n"
        "Do these steps in this order:\n"
        f"1. {policy_step()}\n"
        f"2. Call get_calendar(days={days}) for the earnings dates, ex-dividend dates and High-impact US and UK macro releases. "
        "Its macro calendar only covers the current week: if it says later releases are not known, say so instead of assuming none.\n"
        f'3. Call get_holdings(account="{scope}") for the size of each position that has an earnings or ex-dividend date, and which of those I hold versus only watch.\n'
        "4. Call get_market_regime() for the market regime, stress and Fear & Greed that the macro releases land in.\n"
        "5. Call get_news(ticker=...) for the few held tickers with the nearest earnings date.\n\n"
        "Then answer with these sections, in this order:\n"
        "1. Timeline: every dated event in order, one line each (date, ticker or release, what it is, held or watched), with the as_of of the calendar.\n"
        "2. Positions at risk: held tickers reporting earnings, with the position weight, and any policy limit that a surprise move could breach or \"no policy\".\n"
        "3. Macro to watch: the releases that matter most for the regime and for my holdings, with forecast against previous and the surprise probability when the tool gives one.\n"
        "4. Dividends: held tickers going ex-dividend in the window; remind me that the price drops by about the dividend on that date, and mark any tax wrapper rule from the policy.\n"
        "5. Preparation: concrete things I could decide before each event (size, limits, orders to review), each tied to a policy rule or marked \"no policy\". You cannot place any of them.\n"
        f"6. {DATA_GAPS_SECTION}"
    )
