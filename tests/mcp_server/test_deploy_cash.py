"""deploy_cash prompt: rendering, the tools and sections it asks for, amount and account validation."""
import pytest

from .helpers import get_prompt, prompt_error, prompt_text

DEPLOY_TOOLS = ("get_accounts", "get_holdings", "get_xray", "get_heat_index", "get_market_regime", "get_watchlist", "find_candidates", "pretrade_check")


def _render(client, **arguments):
    return prompt_text(get_prompt(client, "deploy_cash", arguments))


class TestDeployCash:
    def test_renders_the_amount_and_defaults_to_all_accounts(self, mcp_client):
        text = _render(mcp_client, amount="5000")
        assert text.startswith("Deploy cash: 5,000.00 GBP. Scope: all built-in Trading accounts.")
        assert 'get_holdings(account="all")' in text and 'find_candidates(goal="diversify", account="all")' in text

    def test_calls_the_listed_tools_in_order(self, mcp_client):
        text = _render(mcp_client, amount="5000")
        positions = [text.lower().index(f"call {tool}") for tool in DEPLOY_TOOLS]
        assert positions == sorted(positions)

    def test_finalists_are_checked_with_a_buy_of_the_amount(self, mcp_client):
        text = _render(mcp_client, amount="5000")
        assert 'side="buy", account="all", amount=<the part of 5000.00 meant for it>' in text

    def test_outputs_the_sections_in_order(self, mcp_client):
        text = _render(mcp_client, amount="5000")
        sections = ["1. Cash and room", "2. Options", "3. Recommendation", "4. Risks"]
        positions = [text.index(section) for section in sections]
        assert positions == sorted(positions)

    def test_a_trading_account_scopes_the_account_aware_tools(self, mcp_client, seeded_accounts):
        scope = f"acct:{seeded_accounts['snapshot']}"
        text = _render(mcp_client, amount="100", account=scope)
        for call in ("get_holdings", "get_xray"):
            assert f'{call}(account="{scope}")' in text
        assert f'find_candidates(goal="diversify", account="{scope}")' in text

    def test_the_amount_is_required_and_must_be_positive_and_finite(self, mcp_client):
        for arguments in ({}, {"amount": "0"}, {"amount": "-5"}, {"amount": "nan"}, {"amount": "abc"}):
            assert "error" in get_prompt(mcp_client, "deploy_cash", arguments).json()

    def test_large_amounts_are_not_written_in_exponent_form(self, mcp_client):
        assert "e+" not in _render(mcp_client, amount="2500000")

    @pytest.mark.parametrize("account", ["nope", "ALL", "x" * 500])
    def test_a_bad_account_is_refused_without_echo(self, mcp_client, account):
        message = prompt_error(get_prompt(mcp_client, "deploy_cash", {"amount": "100", "account": account}))
        assert message
        assert "nope" not in message and "x" * 100 not in message

    def test_pension_account_is_refused(self, mcp_client, seeded_accounts):
        assert prompt_error(get_prompt(mcp_client, "deploy_cash", {"amount": "100", "account": f"acct:{seeded_accounts['pension']}"}))
