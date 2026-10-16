"""pretrade_check tool: compute-only what-if for a buy, Heat Index context for a sell, argument validation, no verdict from missing inputs, curated errors, no write to the database and no fetch."""
from unittest.mock import patch

import pytest

from database import get_connection
from mcp_server.tools import pretrade_check as pretrade_tool
from xray_engine import XRayScopeUnavailable

from .helpers import call, error_text, table_counts, tool_data

_CHECK = {
    "scope": "all", "ticker": "ZZPT", "proposed_value": 1000.0, "verdict": "warn", "breached_constraint": "Correlation",
    "phi_score": 52.5, "tier": "YELLOW", "var_pct_of_equity": 1.8, "var_tier": "GREEN", "max_correlation": 0.82,
    "correlation_tier": "YELLOW", "drawdown_pct": 6.1, "drawdown_tier": "GREEN", "hypothetical_weight": 0.05,
    "new_portfolio_total_value": 21000.0, "suggested_reduced_value": 400.0, "risk_cache_date": "2026-10-15",
    "data_warnings": [],
}
_HEAT_ROW = {
    "scope": "all", "scope_label": "All Accounts", "phi_score": 62.5, "tier": "YELLOW",
    "var_pct_of_equity": 2.4, "var_tier": "YELLOW", "max_correlation": 0.81, "correlation_tier": "RED",
    "drawdown_pct": 6.1, "drawdown_tier": "YELLOW", "breakdown_json": '["VaR (95%, 1d): 2.4% of equity (YELLOW)"]',
}


def _check(client, **arguments):
    return call(client, "pretrade_check", {"ticker": "ZZPT", "side": "buy", "amount": 1000, **arguments})


@pytest.fixture
def heat_row():
    from risk_orchestrator_engine import persist_heat_index
    conn = get_connection()
    try:
        saved = [dict(r) for r in conn.execute("SELECT * FROM portfolio_heat_index")]
        conn.execute("DELETE FROM portfolio_heat_index")
        conn.commit()
    finally:
        conn.close()
    persist_heat_index(dict(_HEAT_ROW))
    try:
        yield
    finally:
        conn = get_connection()
        try:
            conn.execute("DELETE FROM portfolio_heat_index")
            for row in saved:
                conn.execute(f"INSERT INTO portfolio_heat_index ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
            conn.commit()
        finally:
            conn.close()


class TestBuy:
    def test_envelope_verdict_and_units(self, mcp_client):
        with patch.object(pretrade_tool, "evaluate_pretrade_check", return_value=dict(_CHECK)) as engine:
            data = tool_data(_check(mcp_client))
        assert set(data) == {"as_of", "age_seconds", "market_state", "base_currency", "units", "stale", "missing", "data"}
        assert data["units"]["amount/proposed_value"].startswith("BASE_CURRENCY")
        body = data["data"]
        assert (body["side"], body["ticker"], body["account"], body["proposed_value"]) == ("buy", "ZZPT", "all", 1000.0)
        assert (body["verdict"], body["tier"], body["phi_score"], body["breached_constraint"]) == ("warn", "YELLOW", 52.5, "Correlation")
        assert body["var"] == {"pct_of_equity": 1.8, "tier": "GREEN"}
        assert body["max_correlation"] == {"value": 0.82, "tier": "YELLOW"}
        assert body["max_drawdown"] == {"pct": 6.1, "tier": "GREEN"}
        assert (body["hypothetical_weight"], body["new_portfolio_total_value"], body["suggested_reduced_value"]) == (0.05, 21000.0, 400.0)
        assert data["as_of"] == "2026-10-15T00:00:00Z" and data["missing"] == []
        assert engine.call_args.args == ("all", "ZZPT", 1000.0) and engine.call_args.kwargs == {"read_only": True}

    def test_ticker_is_trimmed_and_upper_cased(self, mcp_client):
        with patch.object(pretrade_tool, "evaluate_pretrade_check", return_value=dict(_CHECK)) as engine:
            tool_data(_check(mcp_client, ticker="  zzpt "))
        assert engine.call_args.args[1] == "ZZPT"

    def test_account_scope_is_passed_to_the_engine(self, mcp_client, holdings_book):
        scope = f"acct:{holdings_book['first']}"
        with patch.object(pretrade_tool, "evaluate_pretrade_check", return_value=dict(_CHECK)) as engine:
            body = tool_data(_check(mcp_client, account=scope))["data"]
        assert body["account"] == scope and engine.call_args.args[0] == scope

    def test_missing_inputs_give_no_verdict_instead_of_an_approval(self, mcp_client):
        inconclusive = {
            **_CHECK, "verdict": "approve", "tier": "GREEN", "phi_score": 0.0, "breached_constraint": None,
            "var_pct_of_equity": None, "max_correlation": None, "var_tier": "GREEN", "correlation_tier": "GREEN",
            "suggested_reduced_value": None, "hypothetical_weight": None,
            "data_warnings": ["No usable return history for ZZPT (need at least 10 daily observations)."],
        }
        with patch.object(pretrade_tool, "evaluate_pretrade_check", return_value=inconclusive):
            data = tool_data(_check(mcp_client))
        body = data["data"]
        assert (body["verdict"], body["tier"], body["phi_score"], body["breached_constraint"], body["suggested_reduced_value"]) == (None,) * 5
        assert body["var"] == {"pct_of_equity": None, "tier": None}
        assert body["max_correlation"] == {"value": None, "tier": None}
        assert body["max_drawdown"] == {"pct": 6.1, "tier": "GREEN"}
        assert data["stale"] is True
        assert any("No usable return history" in line for line in data["missing"])
        assert any(line.startswith("No verdict") and "VaR, correlation" in line for line in data["missing"])

    def test_missing_drawdown_alone_also_withholds_the_verdict(self, mcp_client):
        with patch.object(pretrade_tool, "evaluate_pretrade_check", return_value={**_CHECK, "drawdown_pct": None}):
            data = tool_data(_check(mcp_client))
        assert data["data"]["verdict"] is None and data["data"]["max_drawdown"] == {"pct": None, "tier": None}

    def test_stale_risk_cache_is_flagged_while_a_market_is_open(self, mcp_client):
        with patch.object(pretrade_tool, "evaluate_pretrade_check", return_value={**_CHECK, "risk_cache_date": "2020-01-01"}), \
             patch("mcp_server.envelope.get_exchange_session_state", return_value="open"):
            data = tool_data(_check(mcp_client))
        assert data["stale"] is True

    def test_empty_scope_is_a_curated_error(self, mcp_client):
        with patch.object(pretrade_tool, "evaluate_pretrade_check", side_effect=XRayScopeUnavailable("No holdings found for this scope (Ghostfolio and/or built-in accounts may be empty).")):
            assert error_text(_check(mcp_client)).startswith("No holdings found for this scope")

    def test_unexpected_engine_failure_does_not_leak_its_text(self, mcp_client):
        with patch.object(pretrade_tool, "evaluate_pretrade_check", side_effect=ValueError("secret detail /etc/passwd")):
            response = _check(mcp_client)
        assert "secret detail" not in response.text and "/etc/passwd" not in response.text


class TestArguments:
    def test_amount_is_required_for_a_buy(self, mcp_client):
        with patch.object(pretrade_tool, "evaluate_pretrade_check") as engine:
            text = error_text(call(mcp_client, "pretrade_check", {"ticker": "ZZPT", "side": "buy"}))
        assert text.startswith("amount is required for a buy") and "GBP" in text
        engine.assert_not_called()

    @pytest.mark.parametrize("side", ["hold", "BUY", "", "short"])
    def test_side_must_be_buy_or_sell(self, mcp_client, side):
        with patch.object(pretrade_tool, "evaluate_pretrade_check") as engine:
            response = call(mcp_client, "pretrade_check", {"ticker": "ZZPT", "side": side, "amount": 1000})
        assert response.json()["result"]["isError"] is True
        engine.assert_not_called()

    @pytest.mark.parametrize("amount", [0, -5, 1e13, "1e999", "abc"])
    def test_amount_must_be_positive_finite_and_capped(self, mcp_client, amount):
        with patch.object(pretrade_tool, "evaluate_pretrade_check") as engine:
            response = _check(mcp_client, amount=amount)
        assert response.json()["result"]["isError"] is True
        engine.assert_not_called()

    @pytest.mark.parametrize("ticker", ["", "../etc/passwd", "A B", "X" * 65])
    def test_malformed_ticker_is_refused_before_any_work(self, mcp_client, ticker):
        with patch.object(pretrade_tool, "evaluate_pretrade_check") as engine:
            response = _check(mcp_client, ticker=ticker)
        assert response.json()["result"]["isError"] is True
        assert "etc/passwd" not in response.text
        engine.assert_not_called()

    def test_unknown_account_is_refused_without_echoing_it(self, mcp_client):
        with patch.object(pretrade_tool, "evaluate_pretrade_check") as engine:
            text = error_text(_check(mcp_client, account="acct:999999"))
        assert text.startswith("Unknown account. Valid values:") and "999999" not in text
        engine.assert_not_called()


class TestSell:
    def test_sell_gives_no_verdict_but_the_account_heat_index(self, mcp_client, heat_row):
        with patch.object(pretrade_tool, "evaluate_pretrade_check") as engine:
            data = tool_data(call(mcp_client, "pretrade_check", {"ticker": "ZZPT", "side": "sell"}))
        body = data["data"]
        assert (body["side"], body["ticker"], body["account"], body["verdict"]) == ("sell", "ZZPT", "all", None)
        assert "only models adding a position" in body["note"]
        assert (body["heat_index"]["phi_score"], body["heat_index"]["tier"]) == (62.5, "YELLOW")
        assert data["as_of"] is not None and data["missing"] == []
        engine.assert_not_called()

    def test_amount_is_not_needed_and_is_ignored(self, mcp_client, heat_row):
        body = tool_data(call(mcp_client, "pretrade_check", {"ticker": "ZZPT", "side": "sell", "amount": 500}))["data"]
        assert body["verdict"] is None and "proposed_value" not in body

    def test_no_heat_index_yet_is_null_and_marked_missing(self, mcp_client):
        conn = get_connection()
        try:
            saved = [dict(r) for r in conn.execute("SELECT * FROM portfolio_heat_index")]
            conn.execute("DELETE FROM portfolio_heat_index")
            conn.commit()
        finally:
            conn.close()
        try:
            data = tool_data(call(mcp_client, "pretrade_check", {"ticker": "ZZPT", "side": "sell"}))
        finally:
            conn = get_connection()
            try:
                for row in saved:
                    conn.execute(f"INSERT INTO portfolio_heat_index ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
                conn.commit()
            finally:
                conn.close()
        assert data["data"]["heat_index"] is None and data["as_of"] is None
        assert any("has not been computed" in line for line in data["missing"])


class TestReadOnly:
    def test_real_engine_buy_check_leaves_the_database_unchanged_and_logs_nothing(self, mcp_client, holdings_book):
        before = table_counts()
        with patch("xray_engine.load_daily_close", return_value=None) as loader:
            response = call(mcp_client, "pretrade_check", {"ticker": "ZZPTNEW", "side": "buy", "amount": 500, "account": f"acct:{holdings_book['first']}"})
        assert response.status_code == 200
        assert table_counts() == before
        assert all(call_.kwargs.get("read_only") is True for call_ in loader.call_args_list)

    def test_sell_leaves_the_database_unchanged(self, mcp_client, heat_row):
        before = table_counts()
        tool_data(call(mcp_client, "pretrade_check", {"ticker": "ZZPT", "side": "sell"}))
        assert table_counts() == before

    def test_the_route_still_logs_its_verdict(self):
        import inspect
        import api_routes_analysis
        assert "log_pretrade_check" in inspect.getsource(api_routes_analysis.get_risk_orchestrator_pretrade_check)
        assert "log_pretrade_check" not in inspect.getsource(pretrade_tool)
