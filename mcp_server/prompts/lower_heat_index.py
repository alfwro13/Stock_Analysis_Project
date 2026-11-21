from typing import Annotated, Optional

from pydantic import Field

from config import BASE_CURRENCY

from ..scope import ACCOUNT_ALL, AccountArg
from ..server import server
from ..tools.pretrade_check import AmountArg
from .shared import DATA_GAPS_SECTION, GROUND_RULES, policy_step, scope_text, valid_scope


@server.prompt(
    name="lower_heat_index", title="Lower my Heat Index",
    description="Finds what drives the Portfolio Heat Index and tickers I do not hold that are weakly correlated with every holding, then checks the Heat Index after buying them. Read-only analysis.",
)
def lower_heat_index(
    amount: Annotated[Optional[AmountArg], Field(description=f"Value I would invest in {BASE_CURRENCY}; asked for when it is left out")] = None,
    account: Annotated[AccountArg, Field(description='"all" (default) or a scope_id from get_accounts')] = ACCOUNT_ALL,
) -> str:
    scope = valid_scope(account)
    amount_text = "" if amount is None else f" Amount to invest: {amount:,.2f} {BASE_CURRENCY}."
    ask_amount = ""
    check_amount = "<the amount I give you>" if amount is None else f"{amount:.2f}"
    if amount is None:
        ask_amount = (
            f"No amount was given. Before step 6, ask me how much (in {BASE_CURRENCY}) I would invest and wait for my answer; "
            "do steps 1 to 5 first.\n\n"
        )
    return (
        f"Lower my Portfolio Heat Index. Scope: {scope_text(scope)}.{amount_text}\n\n"
        f"{ask_amount}"
        f"{GROUND_RULES}\n\n"
        "Do these steps in this order:\n"
        f"1. {policy_step()}\n"
        "2. Call get_heat_index() for the Heat Index score and tier, its VaR, correlation and drawdown components and the top risk contributors (it always covers all accounts combined).\n"
        f'3. Call get_holdings(account="{scope}") for the size of each contributor and what trimming it would free.\n'
        f'4. Call get_xray(account="{scope}") for the correlation findings and the sector and concentration weights.\n'
        "5. Call get_market_regime() for the market regime and stress that the Heat Index is read in.\n"
        f'6. Call find_candidates(goal="lower_heat", account="{scope}") for tickers I neither hold nor watch whose highest correlation with any single holding is lowest. '
        f'Call pretrade_check(ticker=<candidate>, side="buy", account="{scope}", amount={check_amount}) for the two or three best, and get_news(ticker=<candidate>) for each.\n\n'
        "Then answer with these sections, in this order:\n"
        "1. What drives the heat: the tier, which component (VaR, correlation or drawdown) is highest and which holdings contribute most, with the figures and as_of.\n"
        "2. Ways to lower it: trimming a contributor and adding a weakly correlated ticker are two different levers; list each with its evidence and what it costs, and say plainly if the Heat Index is already low.\n"
        "3. Candidates: the finalists with their highest holding correlation (and with which holding), the Heat Index tier after the purchase from pretrade_check, and whether each fits the policy or \"no policy\". A null correlation or verdict is unavailable, never low.\n"
        "4. Recommendation: the single change you would make first and what would change your mind. \"Leave it\" is a valid answer.\n"
        f"5. {DATA_GAPS_SECTION}"
    )
