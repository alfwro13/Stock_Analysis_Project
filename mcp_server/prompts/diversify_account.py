from typing import Annotated

from pydantic import Field

from ..scope import ACCOUNT_ALL, AccountArg
from ..server import server
from .shared import DATA_GAPS_SECTION, GROUND_RULES, policy_step, scope_text, valid_scope


@server.prompt(
    name="diversify_account", title="Diversify account",
    description="Finds the concentration gaps in the Portfolio X-ray and suggests tickers I do not hold that would fill them. Read-only analysis.",
)
def diversify_account(
    account: Annotated[AccountArg, Field(description='"all" (default) or a scope_id from get_accounts')] = ACCOUNT_ALL,
) -> str:
    scope = valid_scope(account)
    return (
        f"Diversify my portfolio. Scope: {scope_text(scope)}.\n\n"
        f"{GROUND_RULES}\n\n"
        "Do these steps in this order:\n"
        f"1. {policy_step()}\n"
        f'2. Call get_holdings(account="{scope}") for the positions and their weights.\n'
        f'3. Call get_xray(account="{scope}") for the sector, currency, continent and country weights, the correlation findings and the target breaches. These are the gaps to fill.\n'
        "4. Call get_heat_index() for the Portfolio Heat Index and its top risk contributors (it always covers all accounts combined).\n"
        f'5. Call find_candidates(goal="diversify", account="{scope}") for tickers I neither hold nor watch, lowest correlation with the portfolio first, in under-weight sectors. '
        "Call it again with scope=\"watchlist\" for tickers I watch but do not hold.\n"
        "6. Call get_market_regime() for the market regime and stress the candidates would enter.\n"
        "7. Call get_ticker_snapshot(ticker=<candidate>) and get_news(ticker=<candidate>) for the three best candidates.\n\n"
        "Then answer with these sections, in this order:\n"
        "1. Gaps: the concentrations and missing exposures in the X-ray, each with the figure, as_of and the policy limit it touches or \"no policy\". \"None\" is a valid answer.\n"
        "2. Candidates: up to five tickers, each with held, watched or new, which gap it fills, its correlation with the portfolio and with its closest holding, the sector weight it adds to, and the evidence for its quality. A candidate whose correlation is null is not ranked as if it were low.\n"
        "3. Trade-offs: what each candidate costs (a weaker score, an earnings date, a currency or country exposure the policy limits).\n"
        "4. Next step: which candidates deserve the Pre-trade check prompt, since the Heat Index after a purchase is not computed here.\n"
        f"5. {DATA_GAPS_SECTION}"
    )
