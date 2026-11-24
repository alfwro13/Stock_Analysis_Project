"""get_accounts tool: snapshot and missing-snapshot Trading rows, Pension, soft-deleted and Ghostfolio accounts."""
from unittest.mock import patch

from db.database import get_connection, get_performance_cache

from .helpers import call, tool_data


class TestGetAccounts:
    def _rows(self, mcp_client):
        data = tool_data(call(mcp_client, "get_accounts"))
        return data, {row["account_id"]: row for row in data["data"]["accounts"] if row["account_id"] is not None}

    def test_envelope_is_present_on_the_tool_response(self, mcp_client, seeded_accounts):
        data, _ = self._rows(mcp_client)
        assert set(data) == {"as_of", "age_seconds", "market_state", "session", "base_currency", "units", "stale", "incomplete", "missing", "data"}
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

    def test_account_with_a_holding_valued_at_cost_nulls_the_dependent_figures_and_is_marked_missing(self, mcp_client, seeded_accounts):
        from db.database import add_transaction
        from accounts import accounts_engine
        account_id = seeded_accounts["snapshot"]
        add_transaction(account_id, "Buy", "2026-01-06", ticker="ZZMCPNOPRICE", currency="GBP", quantity=5, unit_price=100, exchange_rate=1.0)
        accounts_engine.refresh_performance_cache(account_id)
        cached = get_performance_cache(account_id)
        assert cached["priced_at_cost_count"] == 1
        data, rows = self._rows(mcp_client)
        row = rows[account_id]
        assert row["priced_at_cost_count"] == 1
        for field in ("equity_value", "unrealized_pnl", "gain_1d", "gain_1w", "gain_1m", "gain_3m", "gain_1y", "mwrr_pct"):
            assert row[field] is None, field
        assert row["cash_balance"] == cached["cash_balance"]
        assert any(f"account {account_id}:" in item and "valued at cost" in item for item in data["missing"])
        assert data["incomplete"] is True

    def test_fully_priced_account_keeps_its_figures(self, mcp_client, seeded_accounts):
        _, rows = self._rows(mcp_client)
        row = rows[seeded_accounts["snapshot"]]
        assert row["priced_at_cost_count"] == 0
        assert row["equity_value"] == 1200.0

    def test_account_without_a_snapshot_is_null_and_marked_missing_and_nothing_is_written(self, mcp_client, seeded_accounts):
        account_id = seeded_accounts["no_snapshot"]
        assert get_performance_cache(account_id) is None
        data, rows = self._rows(mcp_client)
        row = rows[account_id]
        assert row["equity_value"] is None and row["cash_balance"] is None and row["gain_1d"] is None
        assert row["snapshot_at"] is None
        assert any(f"account {account_id}:" in item for item in data["missing"])
        assert data["incomplete"] is True
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
        from db.database import add_price_history
        account_id = seeded_accounts["pension"]
        add_price_history(account_id, "2026-01-05", 2.0, "manual")
        _, rows = self._rows(mcp_client)
        assert rows[account_id]["price_as_of"] == "2026-01-05"
        assert rows[account_id]["equity_value"] is not None

    def test_soft_deleted_accounts_are_not_exposed(self, mcp_client, seeded_accounts):
        from db.database import soft_delete_account
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


class TestDayPnl:
    def _seed_change(self, ticker, change_pts):
        conn = get_connection()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, is_positive, last_updated) VALUES (?, ?, 50.0, ?, 1.0, 1, 0)",
                (ticker, ticker, change_pts),
            )
            conn.commit()
        finally:
            conn.close()

    def _accounts(self, mcp_client):
        data = tool_data(call(mcp_client, "get_accounts"))
        return data, {row["account_id"]: row for row in data["data"]["accounts"] if row["account_id"] is not None}

    def test_day_pnl_is_the_sum_of_the_day_change_get_holdings_shows_times_shares(self, mcp_client, holdings_book):
        self._seed_change("ZZMCPB", -0.5)
        first = holdings_book["first"]
        holdings = tool_data(call(mcp_client, "get_holdings", {"account": f"acct:{first}"}))["data"]["holdings"]
        expected = round(sum(h["day_change"] * h["shares"] for h in holdings), 2)
        data, rows = self._accounts(mcp_client)
        assert rows[first]["day_pnl"] == expected == 12.5
        assert not any(f"account {first}:" in item and "day_pnl" in item for item in data["missing"])

    def test_day_pnl_is_independent_of_gain_1d_and_its_snapshot_baseline(self, mcp_client, seeded_accounts):
        self._seed_change("ZZMCP", -2.0)
        try:
            _, rows = self._accounts(mcp_client)
        finally:
            conn = get_connection()
            try:
                conn.execute("DELETE FROM market_pulse_cache WHERE ticker = 'ZZMCP'")
                conn.commit()
            finally:
                conn.close()
        row = rows[seeded_accounts["snapshot"]]
        assert row["day_pnl"] == -20.0
        assert row["gain_1d"] == get_performance_cache(seeded_accounts["snapshot"])["return_1d"]

    def test_day_pnl_is_null_and_named_in_missing_when_a_position_has_no_fx_rate(self, mcp_client, holdings_book):
        self._seed_change("ZZMCPB", -0.5)
        second = holdings_book["second"]
        data, rows = self._accounts(mcp_client)
        assert rows[second]["day_pnl"] is None
        assert any(f"account {second}: day_pnl unavailable" in item and "ZZMCPU" in item for item in data["missing"])
        assert data["incomplete"] is True

    def test_day_pnl_is_null_and_named_in_missing_when_a_position_has_no_day_change(self, mcp_client, seeded_accounts):
        data, rows = self._accounts(mcp_client)
        account_id = seeded_accounts["snapshot"]
        assert rows[account_id]["day_pnl"] is None
        assert any(f"account {account_id}: day_pnl unavailable" in item and "ZZMCP" in item for item in data["missing"])

    def test_account_without_positions_has_a_real_zero(self, mcp_client, holdings_book):
        _, rows = self._accounts(mcp_client)
        assert rows[holdings_book["empty"]]["day_pnl"] == 0.0

    def test_pension_and_ghostfolio_rows_carry_no_day_pnl(self, mcp_client, holdings_book):
        with patch("mcp_server.tools.get_accounts.list_scope_accounts_with_values", return_value=([{"id": "uuid-1", "name": "GF Main", "value": 1.0}], 1.0)):
            data, rows = self._accounts(mcp_client)
        assert "day_pnl" not in rows[holdings_book["pension"]]
        assert all("day_pnl" not in r for r in data["data"]["accounts"] if r["account_type"] in ("Pension", "House", "Ghostfolio"))

    def test_units_explain_the_baseline_of_gain_1d_and_the_meaning_of_day_pnl(self, mcp_client, seeded_accounts):
        data, _ = self._accounts(mcp_client)
        assert "snapshot" in data["units"]["gain_1d/1w/1m/3m/1y"] and "day_pnl" in data["units"]["gain_1d/1w/1m/3m/1y"]
        assert "get_holdings" in data["units"]["day_pnl"] and "never a partial sum" in data["units"]["day_pnl"]
