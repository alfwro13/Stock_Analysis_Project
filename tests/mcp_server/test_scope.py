"""The account argument shared by get_holdings and get_xray."""
from unittest.mock import patch

import pytest

import constants
from mcp_server import scope
from xray_engine import XRayScopeUnavailable

from .helpers import call, error_text


class TestAccountArgument:
    @pytest.mark.parametrize("tool", ["get_holdings", "get_xray"])
    def test_unknown_account_is_a_clear_error_listing_the_valid_values(self, mcp_client, holdings_book, tool):
        text = error_text(call(mcp_client, tool, {"account": "nonsense"}))
        assert text.startswith("Unknown account")
        assert "all" in text and f"acct:{holdings_book['first']}" in text
        assert "nonsense" not in text

    @pytest.mark.parametrize("tool", ["get_holdings", "get_xray"])
    def test_pension_and_missing_ids_are_not_accounts_for_these_tools(self, mcp_client, holdings_book, tool):
        for account in (f"acct:{holdings_book['pension']}", "acct:999999", "1", f"acct:{holdings_book['first']} ", "ALL"):
            assert error_text(call(mcp_client, tool, {"account": account})).startswith("Unknown account"), account

    @pytest.mark.parametrize("tool", ["get_holdings", "get_xray"])
    def test_soft_deleted_account_is_not_exposed(self, mcp_client, holdings_book, tool):
        from database import soft_delete_account
        soft_delete_account(holdings_book["empty"])
        assert error_text(call(mcp_client, tool, {"account": f"acct:{holdings_book['empty']}"})).startswith("Unknown account")

    @pytest.mark.parametrize("tool", ["get_holdings", "get_xray"])
    def test_argument_longer_than_the_cap_is_refused_before_any_lookup(self, mcp_client, tool):
        response = call(mcp_client, tool, {"account": "x" * (scope.MCP_ACCOUNT_SCOPE_MAX_CHARS + 1)})
        assert response.json()["result"]["isError"] is True

    def test_cap_comes_from_the_request_limits_block(self):
        assert scope.MCP_ACCOUNT_SCOPE_MAX_CHARS is constants.MCP_ACCOUNT_SCOPE_MAX_CHARS

    def test_ghostfolio_account_is_accepted_by_xray_only(self, mcp_client):
        config = {"GHOSTFOLIO_ACCOUNTS": {"active": ["gf-uuid-1"]}}
        with patch("mcp_server.scope.load_config", return_value=config):
            assert "Ghostfolio" in error_text(call(mcp_client, "get_holdings", {"account": "gf-uuid-1"}))
            assert error_text(call(mcp_client, "get_holdings", {"account": "gf-uuid-2"})).startswith("Unknown account")
            with patch("mcp_server.tools.get_xray.assemble_xray_report", side_effect=XRayScopeUnavailable("Ghostfolio authentication failed.")) as xray:
                assert error_text(call(mcp_client, "get_xray", {"account": "gf-uuid-1"})) == "Ghostfolio authentication failed."
            xray.assert_called_once_with("gf-uuid-1")
