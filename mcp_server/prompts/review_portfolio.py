from typing import Annotated

from pydantic import Field

from ..scope import ACCOUNT_ALL, AccountArg
from ..server import server
from .shared import DATA_GAPS_SECTION, GROUND_RULES, policy_step, scope_text, valid_scope


@server.prompt(
    name="review_portfolio", title="Review my portfolio",
    description="Reviews the portfolio against the investment policy: health summary, risks, sell or trim candidates and what to watch this week. Read-only analysis.",
)
def review_portfolio(
    account: Annotated[AccountArg, Field(description='"all" (default) or a scope_id from get_accounts')] = ACCOUNT_ALL,
) -> str:
    scope = valid_scope(account)
    return (
        f"Review my portfolio. Scope: {scope_text(scope)}.\n\n"
        f"{GROUND_RULES}\n\n"
        "Do these steps in this order:\n"
        f"1. {policy_step()}\n"
        "2. Call get_accounts for the account list, cash and period gains.\n"
        f'3. Call get_holdings(account="{scope}") for the positions, cost basis, P/L and day change.\n'
        f'4. Call get_xray(account="{scope}") for concentration, correlation, sector, currency and continent exposure and the target breaches.\n'
        "5. Call get_heat_index() for the Portfolio Heat Index and the top risk contributors (it always covers all accounts combined).\n"
        "6. Call get_market_regime() for the market regime, stress, yield threat levels and Fear & Greed.\n"
        "7. Call get_alerts() for the last 7 days of alerts, Trap Monitor phases and Referee confidence.\n"
        "8. Call get_news() for recent headlines, then get_news(ticker=...) for the positions that look most at risk.\n\n"
        "Then answer with these sections, in this order:\n"
        "1. Health summary: value, P/L, cash, diversification, Heat Index tier and the market backdrop, in a few lines.\n"
        "2. Risks: the biggest ones, each with the evidence behind it (tool, figure, as_of) and the policy limit it touches or \"no policy\".\n"
        "3. Sell or trim candidates: ticker, account, why, and which rule or limit it breaks. \"None\" is a valid answer. Rank by urgency.\n"
        "4. What I'd watch this week: alerts that could develop, regime or stress levels near a threshold, and news worth a second look.\n"
        f"5. {DATA_GAPS_SECTION}"
    )
