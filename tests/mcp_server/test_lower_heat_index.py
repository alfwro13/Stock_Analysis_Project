"""lower_heat_index prompt: rendering with and without an amount, the tools it asks for, validation."""
from .helpers import get_prompt, prompt_error, prompt_text

HEAT_TOOLS = ("get_heat_index", "get_holdings", "get_xray", "get_market_regime", "find_candidates", "pretrade_check")


def _render(client, **arguments):
    return prompt_text(get_prompt(client, "lower_heat_index", arguments))


class TestLowerHeatIndex:
    def test_with_an_amount_the_finalists_are_checked_at_that_size(self, mcp_client):
        text = _render(mcp_client, amount="2000")
        assert "Amount to invest: 2,000.00 GBP." in text
        assert 'side="buy", account="all", amount=2000.00' in text and "No amount was given" not in text

    def test_without_an_amount_it_asks_before_the_pretrade_step(self, mcp_client):
        text = _render(mcp_client)
        assert "No amount was given. Before step 6, ask me how much (in GBP)" in text
        assert text.index("No amount was given") < text.index("1. Read the resource policy://investment-policy")
        assert "amount=<the amount I give you>" in text

    def test_calls_the_listed_tools_in_order(self, mcp_client):
        text = _render(mcp_client, amount="2000")
        positions = [text.lower().index(f"call {tool}") for tool in HEAT_TOOLS]
        assert positions == sorted(positions)

    def test_looks_for_the_lowest_single_holding_correlation(self, mcp_client):
        assert 'find_candidates(goal="lower_heat", account="all")' in _render(mcp_client)

    def test_outputs_the_sections_in_order(self, mcp_client):
        text = _render(mcp_client)
        sections = ["1. What drives the heat", "2. Ways to lower it", "3. Candidates", "4. Recommendation"]
        positions = [text.index(section) for section in sections]
        assert positions == sorted(positions)

    def test_a_trading_account_scopes_the_account_aware_tools(self, mcp_client, seeded_accounts):
        scope = f"acct:{seeded_accounts['snapshot']}"
        text = _render(mcp_client, account=scope, amount="100")
        assert f'get_holdings(account="{scope}")' in text and f'find_candidates(goal="lower_heat", account="{scope}")' in text

    def test_a_bad_amount_or_account_is_refused_without_echo(self, mcp_client, seeded_accounts):
        for arguments in ({"amount": "0"}, {"amount": "-1"}, {"amount": "nan"}):
            assert "error" in get_prompt(mcp_client, "lower_heat_index", arguments).json()
        for account in ("nope", f"acct:{seeded_accounts['pension']}", "x" * 500):
            message = prompt_error(get_prompt(mcp_client, "lower_heat_index", {"account": account}))
            assert message and "nope" not in message and "x" * 100 not in message
