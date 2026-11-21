from typing import Annotated

from pydantic import Field

from config import BASE_CURRENCY

from ..scope import ACCOUNT_ALL, AccountArg
from ..server import server
from ..tools.pretrade_check import AmountArg
from .shared import DATA_GAPS_SECTION, GROUND_RULES, policy_step, scope_text, valid_scope


@server.prompt(
    name="deploy_cash", title="Deploy cash",
    description="Where an amount of cash could go: top up a holding, buy from the Watchlist or a new ticker, checked against diversification, the Heat Index and the investment policy. Read-only analysis.",
)
def deploy_cash(
    amount: Annotated[AmountArg, Field(description=f"Cash to deploy, in {BASE_CURRENCY}")],
    account: Annotated[AccountArg, Field(description='"all" (default) or a scope_id from get_accounts')] = ACCOUNT_ALL,
) -> str:
    scope = valid_scope(account)
    return (
        f"Deploy cash: {amount:,.2f} {BASE_CURRENCY}. Scope: {scope_text(scope)}.\n\n"
        f"{GROUND_RULES}\n\n"
        "Do these steps in this order:\n"
        f"1. {policy_step()}\n"
        "2. Call get_accounts for the cash in each account; say so if the scope holds less cash than the amount.\n"
        f'3. Call get_holdings(account="{scope}") for the positions I could top up and their weights.\n'
        f'4. Call get_xray(account="{scope}") for the sector, currency, continent and concentration weights and the target breaches the cash could ease.\n'
        "5. Call get_heat_index() for the current Portfolio Heat Index (it always covers all accounts combined).\n"
        "6. Call get_market_regime() for the market regime, stress and Fear & Greed.\n"
        "7. Call get_watchlist(only_buy_recommended=true) for the tickers I already watch that the dashboard rates a buy.\n"
        f'8. Call find_candidates(goal="diversify", account="{scope}") for tickers I neither hold nor watch that would spread the portfolio.\n'
        f'9. Pick the two or three best options across steps 3, 7 and 8, then call pretrade_check(ticker=<option>, side="buy", account="{scope}", amount=<the part of {amount:.2f} meant for it>) for each, '
        "and get_news(ticker=<option>) for headlines and sentiment. Treat a verdict as one input, not the answer, and say why when it is null.\n\n"
        "Then answer with these sections, in this order:\n"
        "1. Cash and room: the cash available in the scope, and the policy limits (cash reserve, one position, sector, country, currency) that bound how much can go where, or \"no policy\".\n"
        f"2. Options: a table of up to three ways to deploy {amount:,.2f} {BASE_CURRENCY}, each with ticker, held, watched or new, amount, why, the position weight after, and the Heat Index tier after (from pretrade_check). A top-up of a holding is a valid option; so is keeping cash.\n"
        "3. Recommendation: the option you would pick and why, and what would change your mind (an event, a price level, a regime change). \"Wait\" is a valid answer.\n"
        "4. Risks: what could go wrong with the pick, with the evidence (tool, figure, as_of).\n"
        f"5. {DATA_GAPS_SECTION}"
    )
