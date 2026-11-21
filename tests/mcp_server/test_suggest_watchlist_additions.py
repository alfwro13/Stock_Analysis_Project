"""suggest_watchlist_additions prompt: rendering, the discovery tools it asks for, account validation."""
from .helpers import get_prompt, prompt_error, prompt_text

IDEA_TOOLS = ("get_watchlist", "get_xray", "get_market_regime", "find_candidates", "get_market_report", "get_ticker_snapshot")


def _render(client, **arguments):
    return prompt_text(get_prompt(client, "suggest_watchlist_additions", arguments))


class TestSuggestWatchlistAdditions:
    def test_renders_without_arguments_for_all_accounts(self, mcp_client):
        text = _render(mcp_client)
        assert text.startswith("What should I add to my watchlist? Portfolio scope: all built-in Trading accounts.")
        assert 'find_candidates(goal="add_to_watchlist", account="all")' in text

    def test_calls_the_listed_tools_in_order(self, mcp_client):
        text = _render(mcp_client)
        positions = [text.lower().index(f"call {tool}") for tool in IDEA_TOOLS]
        assert positions == sorted(positions)

    def test_reports_and_screens_look_only_at_tickers_that_are_new(self, mcp_client):
        text = _render(mcp_client)
        assert 'get_market_report(report=<report>, scope="new")' in text and 'screen_universe(scope="new"' in text

    def test_the_prompt_says_it_cannot_change_the_watchlist(self, mcp_client):
        text = _render(mcp_client)
        assert "You cannot change my Watchlist" in text and "You cannot trade or change anything" in text

    def test_outputs_the_sections_in_order(self, mcp_client):
        text = _render(mcp_client)
        sections = ["1. Shortlist", "2. Timing", "3. Against the policy", "4. Already on my watchlist", "5. What to check before buying"]
        positions = [text.index(section) for section in sections]
        assert positions == sorted(positions)

    def test_a_bad_account_is_refused_without_echo(self, mcp_client, seeded_accounts):
        for account in ("nope", f"acct:{seeded_accounts['pension']}", "x" * 500):
            message = prompt_error(get_prompt(mcp_client, "suggest_watchlist_additions", {"account": account}))
            assert message and "nope" not in message and "x" * 100 not in message
