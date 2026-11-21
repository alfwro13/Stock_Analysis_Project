from typing import Annotated

from pydantic import Field

from ..scope import ACCOUNT_ALL, AccountArg
from ..server import server
from .shared import DATA_GAPS_SECTION, GROUND_RULES, policy_step, scope_text, valid_scope


@server.prompt(
    name="suggest_watchlist_additions", title="What should I add to my watchlist?",
    description="Researches the market universe and the reports for tickers I neither hold nor watch that are worth adding to my Watchlist. Read-only analysis.",
)
def suggest_watchlist_additions(
    account: Annotated[AccountArg, Field(description='"all" (default) or a scope_id from get_accounts, for the portfolio the ideas are measured against')] = ACCOUNT_ALL,
) -> str:
    scope = valid_scope(account)
    return (
        f"What should I add to my watchlist? Portfolio scope: {scope_text(scope)}.\n\n"
        f"{GROUND_RULES}\n\n"
        "Do these steps in this order:\n"
        f"1. {policy_step()}\n"
        "2. Call get_watchlist(scope=\"watchlist_only\", compact=true) for what I already watch, so you do not suggest it again and can say which of those look strongest.\n"
        f'3. Call get_xray(account="{scope}") for the sector, currency, continent and concentration weights, so the ideas can fill gaps.\n'
        "4. Call get_market_regime() for the market regime, stress and Fear & Greed.\n"
        f'5. Call find_candidates(goal="add_to_watchlist", account="{scope}") for tickers I neither hold nor watch, ranked by how many of the dashboard\'s reports list them and then by composite score.\n'
        "6. Call get_market_report(report=<report>, scope=\"new\") for the one or two reports that fit the regime (for example quality_compounders or quality_on_sale), and screen_universe(scope=\"new\", ...) for anything the candidates miss.\n"
        "7. Call get_ticker_snapshot(ticker=<candidate>) and get_news(ticker=<candidate>) for the five best candidates.\n\n"
        "Then answer with these sections, in this order:\n"
        "1. Shortlist: up to five tickers, each with the reports that list it, composite score, quality grade, and the gap in my portfolio it would fill.\n"
        "2. Timing: for each, the evidence (tool, figure, as_of) and what makes it worth watching today, not only good in general.\n"
        "3. Against the policy: each ticker's fit with the goals, limits and never-do list, or \"no policy\".\n"
        "4. Already on my watchlist: any watched ticker that looks stronger than the shortlist, or that no longer deserves a place.\n"
        "5. What to check before buying: earnings dates, news and anything a quick read of the evidence does not settle. You cannot change my Watchlist; I add the tickers myself.\n"
        f"6. {DATA_GAPS_SECTION}"
    )
