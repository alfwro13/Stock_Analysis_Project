"""diversify_account prompt: rendering, the tools and sections it asks for, account validation."""
from .helpers import get_prompt, prompt_error, prompt_text

DIVERSIFY_TOOLS = ("get_holdings", "get_xray", "get_heat_index", "find_candidates", "get_market_regime", "get_ticker_snapshot")


def _render(client, **arguments):
    return prompt_text(get_prompt(client, "diversify_account", arguments))


class TestDiversifyAccount:
    def test_renders_without_arguments_for_all_accounts(self, mcp_client):
        text = _render(mcp_client)
        assert text.startswith("Diversify my portfolio. Scope: all built-in Trading accounts.")
        assert 'find_candidates(goal="diversify", account="all")' in text and 'scope="watchlist"' in text

    def test_calls_the_listed_tools_in_order(self, mcp_client):
        text = _render(mcp_client)
        positions = [text.lower().index(f"call {tool}") for tool in DIVERSIFY_TOOLS]
        assert positions == sorted(positions)

    def test_outputs_the_sections_in_order(self, mcp_client):
        text = _render(mcp_client)
        sections = ["1. Gaps", "2. Candidates", "3. Trade-offs", "4. Next step"]
        positions = [text.index(section) for section in sections]
        assert positions == sorted(positions)

    def test_the_heat_index_after_a_purchase_is_left_to_the_pre_trade_prompt(self, mcp_client):
        text = _render(mcp_client)
        assert "pretrade_check(" not in text and "Pre-trade check prompt" in text

    def test_a_trading_account_scopes_the_account_aware_tools(self, mcp_client, seeded_accounts):
        scope = f"acct:{seeded_accounts['snapshot']}"
        text = _render(mcp_client, account=scope)
        assert f'get_holdings(account="{scope}")' in text and f'find_candidates(goal="diversify", account="{scope}")' in text

    def test_a_bad_account_is_refused_without_echo(self, mcp_client, seeded_accounts):
        for account in ("nope", f"acct:{seeded_accounts['pension']}", "x" * 500):
            message = prompt_error(get_prompt(mcp_client, "diversify_account", {"account": account}))
            assert message and "nope" not in message and "x" * 100 not in message
