"""get_heat_index tool: score, contributors and their cap, not computed yet, missing inputs, nightly stale limit."""
from unittest.mock import patch

import pytest

from database import get_connection
from db_helpers import parse_utc_epoch
from mcp_server.tools import get_heat_index as get_heat_index_tool

from .helpers import call, rpc, table_counts, tool_data


_HEAT_ROW = {
    "scope": "all", "scope_label": "All Accounts", "phi_score": 62.5, "tier": "YELLOW",
    "var_pct_of_equity": 2.4, "var_tier": "YELLOW", "max_correlation": 0.81, "correlation_tier": "RED",
    "drawdown_pct": 6.1, "drawdown_tier": "YELLOW",
    "breakdown_json": '["VaR (95%, 1d): 2.4% of equity (YELLOW)", "Max pairwise correlation: 0.81 (RED)"]',
}


def _contribution(ticker, score, tier="YELLOW"):
    return {"ticker": ticker, "risk_score": score, "risk_tier": tier, "marginal_var_contribution_pct": 12.5,
            "max_pairwise_correlation": 0.7, "stop_distance_pct": 3.2}


@pytest.fixture
def heat_tables():
    tables = ("portfolio_heat_index", "ticker_risk_contribution")
    conn = get_connection()
    try:
        saved = {t: [dict(r) for r in conn.execute(f"SELECT * FROM {t}")] for t in tables}
        for t in tables:
            conn.execute(f"DELETE FROM {t}")
        conn.commit()
    finally:
        conn.close()
    try:
        yield
    finally:
        conn = get_connection()
        try:
            for t in tables:
                conn.execute(f"DELETE FROM {t}")
                for row in saved[t]:
                    conn.execute(f"INSERT INTO {t} ({', '.join(row)}) VALUES ({', '.join('?' * len(row))})", list(row.values()))
            conn.commit()
        finally:
            conn.close()


class TestGetHeatIndex:
    def _heat(self, mcp_client):
        return tool_data(call(mcp_client, "get_heat_index"))

    def test_envelope_units_and_score(self, mcp_client, heat_tables):
        from risk_orchestrator_engine import persist_heat_index, persist_ticker_contributions
        persist_heat_index(dict(_HEAT_ROW))
        persist_ticker_contributions([_contribution("ZZH1", 80.0, "RED")])
        data = self._heat(mcp_client)
        assert set(data) == {"as_of", "age_seconds", "market_state", "base_currency", "units", "stale", "missing", "data"}
        assert data["units"]["phi_score"].startswith("0-100 composite")
        heat = data["data"]["heat_index"]
        assert (heat["phi_score"], heat["tier"]) == (62.5, "YELLOW")
        assert heat["var"] == {"pct_of_equity": 2.4, "tier": "YELLOW"}
        assert heat["max_correlation"] == {"value": 0.81, "tier": "RED"}
        assert heat["max_drawdown"] == {"pct": 6.1, "tier": "YELLOW"}
        assert heat["breakdown"] == ["VaR (95%, 1d): 2.4% of equity (YELLOW)", "Max pairwise correlation: 0.81 (RED)"]
        assert heat["computed_at"].endswith("Z") and data["as_of"] is not None
        assert data["missing"] == [] and data["stale"] is False

    def test_top_contributors_are_the_hottest_five_in_order(self, mcp_client, heat_tables):
        from risk_orchestrator_engine import persist_heat_index, persist_ticker_contributions
        persist_heat_index(dict(_HEAT_ROW))
        persist_ticker_contributions([_contribution(f"ZZH{i}", float(i * 10)) for i in range(1, 8)])
        contributors = self._heat(mcp_client)["data"]["top_risk_contributors"]
        assert [c["ticker"] for c in contributors] == ["ZZH7", "ZZH6", "ZZH5", "ZZH4", "ZZH3"]
        assert len(contributors) == get_heat_index_tool.HEAT_TOP_CONTRIBUTORS
        assert set(contributors[0]) == {"ticker", "risk_score", "risk_tier", "marginal_var_contribution_pct", "max_pairwise_correlation", "stop_distance_pct"}

    def test_not_computed_yet_is_null_and_marked_missing(self, mcp_client, heat_tables):
        data = self._heat(mcp_client)
        assert data["data"] == {"heat_index": None, "top_risk_contributors": []}
        assert data["as_of"] is None and data["stale"] is True
        assert any("has not been computed yet" in item for item in data["missing"])

    def test_unavailable_input_is_flagged_not_shown_as_zero(self, mcp_client, heat_tables):
        from risk_orchestrator_engine import persist_heat_index, persist_ticker_contributions
        persist_heat_index({**_HEAT_ROW, "phi_score": None, "tier": None, "var_pct_of_equity": None, "var_tier": None})
        persist_ticker_contributions([_contribution("ZZH1", 10.0)])
        data = self._heat(mcp_client)
        heat = data["data"]["heat_index"]
        assert heat["var"] == {"pct_of_equity": None, "tier": None}
        assert (heat["phi_score"], heat["tier"]) == (None, None)
        assert data["missing"] == ["Heat Index VaR input is not available, so the score and tier are withheld"]

    def test_contributor_without_a_score_is_flagged_and_listed_last(self, mcp_client, heat_tables):
        from risk_orchestrator_engine import persist_heat_index, persist_ticker_contributions
        persist_heat_index(dict(_HEAT_ROW))
        persist_ticker_contributions([_contribution("ZZH1", None, None), _contribution("ZZH2", 10.0, "GREEN")])
        data = self._heat(mcp_client)
        assert [c["ticker"] for c in data["data"]["top_risk_contributors"]] == ["ZZH2", "ZZH1"]
        assert data["data"]["top_risk_contributors"][1]["risk_score"] is None
        assert any("no Risk Contribution score" in item for item in data["missing"])

    def test_score_without_contributors_is_marked(self, mcp_client, heat_tables):
        from risk_orchestrator_engine import persist_heat_index
        persist_heat_index(dict(_HEAT_ROW))
        data = self._heat(mcp_client)
        assert data["data"]["top_risk_contributors"] == []
        assert data["missing"] == ["No per-position Risk Contribution rows exist yet"]

    def test_as_of_is_the_older_of_the_two_writes(self, mcp_client, heat_tables):
        from risk_orchestrator_engine import persist_heat_index, persist_ticker_contributions
        persist_heat_index(dict(_HEAT_ROW))
        persist_ticker_contributions([_contribution("ZZH1", 10.0)])
        conn = get_connection()
        try:
            conn.execute("UPDATE ticker_risk_contribution SET last_updated = '2026-10-14 19:05:00'")
            conn.commit()
        finally:
            conn.close()
        assert self._heat(mcp_client)["as_of"] == "2026-10-14T19:05:00Z"

    def test_a_missed_nightly_run_turns_stale_only_after_several_days(self, mcp_client, heat_tables):
        from risk_orchestrator_engine import persist_heat_index, persist_ticker_contributions
        persist_heat_index(dict(_HEAT_ROW))
        persist_ticker_contributions([_contribution("ZZH1", 10.0)])
        conn = get_connection()
        try:
            conn.execute("UPDATE portfolio_heat_index SET last_updated = '2026-10-14 19:05:00'")
            conn.commit()
        finally:
            conn.close()
        with patch("mcp_server.envelope.get_exchange_session_state", return_value="open"), \
                patch("mcp_server.envelope.time.time", return_value=parse_utc_epoch("2026-10-16 10:00:00")):
            assert get_heat_index_tool.get_heat_index()["stale"] is False
        with patch("mcp_server.envelope.get_exchange_session_state", return_value="open"), \
                patch("mcp_server.envelope.time.time", return_value=parse_utc_epoch("2026-10-21 10:00:00")):
            assert get_heat_index_tool.get_heat_index()["stale"] is True

    def test_takes_no_arguments_and_reads_the_all_scope_only(self, mcp_client, heat_tables):
        from risk_orchestrator_engine import persist_heat_index
        persist_heat_index({**_HEAT_ROW, "scope": "acct:1", "scope_label": "Other", "phi_score": 99.0, "tier": "RED"})
        assert self._heat(mcp_client)["data"]["heat_index"] is None
        tool = next(t for t in rpc(mcp_client, "tools/list").json()["result"]["tools"] if t["name"] == "get_heat_index")
        assert tool["inputSchema"].get("properties", {}) == {}

    def test_leaves_the_database_unchanged(self, mcp_client, heat_tables):
        from risk_orchestrator_engine import persist_heat_index, persist_ticker_contributions
        persist_heat_index(dict(_HEAT_ROW))
        persist_ticker_contributions([_contribution("ZZH1", 10.0)])
        before = table_counts()
        self._heat(mcp_client)
        assert table_counts() == before
