"""get_accounts tool: snapshot and missing-snapshot Trading rows, Pension, soft-deleted and Ghostfolio accounts."""
from unittest.mock import patch

from database import get_performance_cache

from .helpers import call, tool_data


class TestGetAccounts:
    def _rows(self, mcp_client):
        data = tool_data(call(mcp_client, "get_accounts"))
        return data, {row["account_id"]: row for row in data["data"]["accounts"] if row["account_id"] is not None}

    def test_envelope_is_present_on_the_tool_response(self, mcp_client, seeded_accounts):
        data, _ = self._rows(mcp_client)
        assert set(data) == {"as_of", "age_seconds", "market_state", "base_currency", "units", "stale", "missing", "data"}
        assert set(data["market_state"]) == {"LSE", "NYSE"}
        assert set(data["market_state"].values()) <= {"open", "pre", "post", "closed"}
        assert data["units"]["amounts"].startswith("BASE_CURRENCY")

    def test_trading_account_with_a_snapshot(self, mcp_client, seeded_accounts):
        data, rows = self._rows(mcp_client)
        row = rows[seeded_accounts["snapshot"]]
        cached = get_performance_cache(seeded_accounts["snapshot"])
        assert row["account_type"] == "Trading"
        assert row["scope_id"] == f"acct:{seeded_accounts['snapshot']}"
        assert row["equity_value"] == cached["equity_value"] == 1200.0
        assert row["cash_balance"] == cached["cash_balance"]
        assert row["unrealized_pnl"] == cached["unrealized_pnl"] == 200.0
        assert row["gain_1d"] == cached["return_1d"]
        assert row["mwrr_pct"] == cached["mwrr"]
        assert row["snapshot_at"].endswith("Z")
        assert data["as_of"] is not None

    def test_account_without_a_snapshot_is_null_and_marked_missing_and_nothing_is_written(self, mcp_client, seeded_accounts):
        account_id = seeded_accounts["no_snapshot"]
        assert get_performance_cache(account_id) is None
        data, rows = self._rows(mcp_client)
        row = rows[account_id]
        assert row["equity_value"] is None and row["cash_balance"] is None and row["gain_1d"] is None
        assert row["snapshot_at"] is None
        assert any(f"account {account_id}:" in item for item in data["missing"])
        assert data["stale"] is True
        assert get_performance_cache(account_id) is None

    def test_pension_without_price_history_is_null_not_zero(self, mcp_client, seeded_accounts):
        account_id = seeded_accounts["pension"]
        data, rows = self._rows(mcp_client)
        row = rows[account_id]
        assert row["account_type"] == "Pension"
        assert row["equity_value"] is None
        assert row["scope_id"] is None
        assert row["price_as_of"] is None
        assert any(f"account {account_id}:" in item for item in data["missing"])

    def test_priced_pension_reports_its_value(self, mcp_client, seeded_accounts):
        from database import add_price_history
        account_id = seeded_accounts["pension"]
        add_price_history(account_id, "2026-01-05", 2.0, "manual")
        _, rows = self._rows(mcp_client)
        assert rows[account_id]["price_as_of"] == "2026-01-05"
        assert rows[account_id]["equity_value"] is not None

    def test_soft_deleted_accounts_are_not_exposed(self, mcp_client, seeded_accounts):
        from database import soft_delete_account
        soft_delete_account(seeded_accounts["no_snapshot"])
        _, rows = self._rows(mcp_client)
        assert seeded_accounts["no_snapshot"] not in rows

    def test_ghostfolio_accounts_appear_with_their_scope_id_and_value_only(self, mcp_client):
        with patch("mcp_server.tools.get_accounts.list_scope_accounts_with_values", return_value=([{"id": "uuid-1", "name": "GF Main", "value": 123.45}], 123.45)):
            data = tool_data(call(mcp_client, "get_accounts"))
        row = next(r for r in data["data"]["accounts"] if r["scope_id"] == "uuid-1")
        assert row == {"account_id": None, "name": "GF Main", "account_type": "Ghostfolio", "scope_id": "uuid-1", "equity_value": 123.45}

    def test_fx_is_read_from_the_persisted_quote(self, mcp_client):
        from portfolio_service import _persisted_fx_reads
        seen = []

        def spy():
            seen.append(_persisted_fx_reads.get())
            return {"accounts": []}

        with patch("mcp_server.tools.get_accounts.portfolio_metrics_engine.other_accounts_list", side_effect=spy):
            tool_data(call(mcp_client, "get_accounts"))
        assert seen == [True]
