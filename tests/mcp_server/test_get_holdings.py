"""get_holdings tool: cost basis, P/L, allocation, day change, extended hours, pence quotes, unavailable FX."""
from unittest.mock import patch

import pytest

from .helpers import call, table_counts, tool_data


class TestGetHoldings:
    def _rows(self, mcp_client, account="all"):
        data = tool_data(call(mcp_client, "get_holdings", {"account": account}))
        return data, {(r["account_id"], r["ticker"]): r for r in data["data"]["holdings"]}

    def test_envelope_and_units(self, mcp_client, holdings_book):
        data, _ = self._rows(mcp_client)
        assert set(data) == {"as_of", "age_seconds", "market_state", "base_currency", "units", "stale", "missing", "data"}
        assert data["units"]["allocation_pct"].startswith("percent of that row's own account")
        assert data["units"]["market_price"].count("GBp means pence")

    def test_priced_position_with_cost_basis_pnl_weight_and_day_change(self, mcp_client, holdings_book):
        _, rows = self._rows(mcp_client, f"acct:{holdings_book['first']}")
        row = rows[(holdings_book["first"], "ZZMCPA")]
        assert row["scope_id"] == f"acct:{holdings_book['first']}"
        assert (row["shares"], row["total_investment"], row["average_buy_price"]) == (10, 1000.0, 100.0)
        assert (row["market_price"], row["market_price_currency"], row["market_price_in_base_currency"]) == (120.0, "GBP", 120.0)
        assert (row["market_value"], row["gain_value"], row["gain_pct"]) == (1200.0, 200.0, 20.0)
        assert row["allocation_pct"] == pytest.approx(1200 / 1450 * 100, abs=0.01)
        assert (row["day_change"], row["day_change_pct"]) == (1.5, 1.27)
        assert row["priced_at_cost"] is False
        assert rows[(holdings_book["first"], "ZZMCPB")]["allocation_pct"] == pytest.approx(250 / 1450 * 100, abs=0.01)

    def test_extended_hours_move_is_a_separate_field_never_merged_into_the_price(self, mcp_client, holdings_book):
        _, rows = self._rows(mcp_client, f"acct:{holdings_book['first']}")
        row = rows[(holdings_book["first"], "ZZMCPA")]
        assert row["market_price"] == 120.0 and row["day_change_pct"] == 1.27
        assert row["pre_market_change_pct"] == 4.2 and row["post_market_change_pct"] is None

    def test_same_ticker_in_two_accounts_is_two_rows(self, mcp_client, holdings_book):
        _, rows = self._rows(mcp_client)
        assert rows[(holdings_book["first"], "ZZMCPA")]["shares"] == 10
        assert rows[(holdings_book["second"], "ZZMCPA")]["shares"] == 2

    def test_account_scope_returns_only_that_account(self, mcp_client, holdings_book):
        data, rows = self._rows(mcp_client, f"acct:{holdings_book['second']}")
        assert {account_id for account_id, _ in rows} == {holdings_book["second"]}
        assert data["data"]["account"] == f"acct:{holdings_book['second']}"

    def test_pence_quoted_instrument_keeps_its_native_price_and_converts_through_fx(self, mcp_client, holdings_book):
        _, rows = self._rows(mcp_client, f"acct:{holdings_book['second']}")
        row = rows[(holdings_book["second"], "ZZMCPP")]
        assert (row["market_price"], row["market_price_currency"]) == (5000.0, "GBp")
        assert row["market_price_in_base_currency"] == 50.0
        assert row["market_value"] == 100.0

    def test_unavailable_fx_gives_nulls_a_marker_and_stale(self, mcp_client, holdings_book):
        data, rows = self._rows(mcp_client, f"acct:{holdings_book['second']}")
        row = rows[(holdings_book["second"], "ZZMCPU")]
        assert row["priced_at_cost"] is True
        assert row["market_price"] == 10.0 and row["market_price_currency"] == "USD"
        assert row["market_price_in_base_currency"] is None
        assert row["market_value"] is None and row["gain_value"] is None and row["gain_pct"] is None and row["allocation_pct"] is None
        assert row["total_investment"] == 19.2
        assert any(item.startswith("ZZMCPU (McpBookB)") for item in data["missing"])
        assert data["stale"] is True
        assert all(rows[key]["priced_at_cost"] is False for key in rows if key[1] != "ZZMCPU")

    def test_as_of_is_the_oldest_quote_behind_the_prices(self, mcp_client, holdings_book):
        data, _ = self._rows(mcp_client, f"acct:{holdings_book['first']}")
        assert data["as_of"] == "2026-10-01T09:00:00Z"

    def test_account_with_no_positions_is_empty_with_no_as_of(self, mcp_client, holdings_book):
        data, rows = self._rows(mcp_client, f"acct:{holdings_book['empty']}")
        assert rows == {} and data["data"]["holdings"] == []
        assert data["as_of"] is None and data["missing"] == [] and data["stale"] is False

    def test_all_notes_that_ghostfolio_accounts_are_not_covered(self, mcp_client, holdings_book):
        with patch("mcp_server.scope.load_config", return_value={"GHOSTFOLIO_ACCOUNTS": {"active": ["gf-uuid-1"]}}):
            data, _ = self._rows(mcp_client)
            scoped, _ = self._rows(mcp_client, f"acct:{holdings_book['first']}")
        assert any("Ghostfolio accounts are not covered" in item for item in data["missing"])
        assert not any("Ghostfolio" in item for item in scoped["missing"])

    def test_fx_is_read_from_the_persisted_quote(self, mcp_client):
        from portfolio_service import _persisted_fx_reads
        seen = []

        def spy():
            seen.append(_persisted_fx_reads.get())
            return {"holdings": []}

        with patch("mcp_server.tools.get_holdings.portfolio_metrics_engine.holdings_with_metrics_all_accounts", side_effect=spy):
            tool_data(call(mcp_client, "get_holdings"))
        assert seen == [True]

    def test_failure_returns_fixed_text_not_the_exception(self, mcp_client):
        with patch("mcp_server.tools.get_holdings.portfolio_metrics_engine.holdings_with_metrics_all_accounts", side_effect=RuntimeError("secret-path /etc/shadow")):
            response = call(mcp_client, "get_holdings")
        assert response.json()["result"]["isError"] is True
        assert "secret-path" not in response.text and "/etc/shadow" not in response.text

    def test_leaves_the_database_unchanged(self, mcp_client, holdings_book):
        before = table_counts()
        tool_data(call(mcp_client, "get_holdings"))
        assert table_counts() == before
