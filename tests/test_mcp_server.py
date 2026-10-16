"""Claude MCP Server (mcp_server.py, mounted at /mcp): Bearer-key auth, transport, response envelope, get_accounts, read-only guarantee."""
import asyncio
import logging
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import pytest

import accounts_engine
import mcp_server
from database import add_transaction, create_account, get_connection, get_performance_cache
from scheduler_manifest import JOB_GRAPH
from scheduler_monitor import _build_node, _derive_edges

MCP_KEY = "test-mcp-key-do-not-use-in-production"
RPC_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
EXPECTED_TOOLS = {"get_accounts"}


@pytest.fixture(autouse=True)
def _mcp_key(monkeypatch):
    monkeypatch.setenv("MCP_API_KEY", MCP_KEY)


@pytest.fixture
def mcp_client(raw_client):
    return raw_client


def _rpc(client, method, params=None, key=MCP_KEY, headers=None):
    request_headers = {**RPC_HEADERS, **(headers or {})}
    if key is not None:
        request_headers["Authorization"] = f"Bearer {key}"
    return client.post(
        "/mcp", headers=request_headers,
        json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}},
    )


def _call(client, name, arguments=None, **kwargs):
    return _rpc(client, "tools/call", {"name": name, "arguments": arguments or {}}, **kwargs)


def _tool_data(response):
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is False, result
    return result["structuredContent"]


def _table_counts():
    conn = get_connection()
    try:
        tables = [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")]
        return {t: conn.execute(f'SELECT COUNT(*) FROM "{t}"').fetchone()[0] for t in tables}
    finally:
        conn.close()


_ACCOUNT_TABLES = (
    "account_transactions", "account_performance_cache", "account_price_history",
    "account_benchmark_tickers", "account_value_history", "account_value_history_currency",
)


@pytest.fixture
def seeded_accounts():
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency) VALUES ('ZZMCP', 120.0, 'GBP')"
        )
        conn.commit()
    finally:
        conn.close()
    with_snapshot = create_account("McpSnapshotAcc", "GBP", initial_cash=1000.0)
    add_transaction(with_snapshot, "Buy", "2026-01-05", ticker="ZZMCP", currency="GBP", quantity=10, unit_price=100, exchange_rate=1.0)
    accounts_engine.refresh_performance_cache(with_snapshot)
    without_snapshot = create_account("McpNoSnapshotAcc", "GBP", initial_cash=50.0)
    pension_unpriced = create_account("McpPensionAcc", "GBP", account_type="Pension")
    ids = {"snapshot": with_snapshot, "no_snapshot": without_snapshot, "pension": pension_unpriced}
    try:
        yield ids
    finally:
        conn = get_connection()
        try:
            for account_id in ids.values():
                for table in _ACCOUNT_TABLES:
                    conn.execute(f"DELETE FROM {table} WHERE account_id = ?", (account_id,))
                conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            conn.execute("DELETE FROM stock_signals WHERE ticker = 'ZZMCP'")
            conn.commit()
        finally:
            conn.close()


class TestAuth:
    def test_right_key_is_accepted(self, mcp_client):
        assert _rpc(mcp_client, "tools/list").status_code == 200

    def test_scheme_is_case_insensitive(self, mcp_client):
        response = mcp_client.post(
            "/mcp", headers={**RPC_HEADERS, "Authorization": f"bearer {MCP_KEY}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert response.status_code == 200

    def test_missing_key_is_401_with_bearer_challenge(self, mcp_client):
        response = _rpc(mcp_client, "tools/list", key=None)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    @pytest.mark.parametrize("key", ["wrong-key", "", MCP_KEY + "x", MCP_KEY[:-1], os.environ["API_KEY"]])
    def test_wrong_key_is_401(self, mcp_client, key):
        assert _rpc(mcp_client, "tools/list", key=key).status_code == 401

    def test_other_scheme_is_401(self, mcp_client):
        response = mcp_client.post(
            "/mcp", headers={**RPC_HEADERS, "Authorization": f"Basic {MCP_KEY}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert response.status_code == 401

    @pytest.mark.parametrize("presented", [MCP_KEY, "", "anything"])
    def test_unset_key_fails_closed(self, mcp_client, monkeypatch, presented):
        monkeypatch.delenv("MCP_API_KEY")
        assert _rpc(mcp_client, "tools/list", key=presented).status_code == 401

    def test_empty_key_fails_closed(self, mcp_client, monkeypatch):
        monkeypatch.setenv("MCP_API_KEY", "")
        assert _rpc(mcp_client, "tools/list", key="").status_code == 401

    def test_home_assistant_key_is_403(self, mcp_client):
        response = _rpc(mcp_client, "tools/list", key=None, headers={"X-API-Key": os.environ["API_KEY"]})
        assert response.status_code == 403

    def test_home_assistant_key_next_to_a_valid_bearer_is_403(self, mcp_client):
        response = _rpc(mcp_client, "tools/list", headers={"X-API-Key": os.environ["API_KEY"]})
        assert response.status_code == 403

    def test_mcp_key_in_the_ha_header_is_403(self, mcp_client):
        assert _rpc(mcp_client, "tools/list", key=None, headers={"X-API-Key": MCP_KEY}).status_code == 403

    def test_session_cookie_does_not_authenticate_mcp(self, client):
        assert _rpc(client, "tools/list", key=None).status_code == 401

    @pytest.mark.parametrize("path", [
        "/api/accounts/holdings-list", "/api/accounts/portfolio-totals", "/api/system/market-status", "/portfolio", "/api/settings",
    ])
    def test_mcp_key_does_not_authenticate_other_routes(self, mcp_client, path):
        response = mcp_client.get(path, headers={"Authorization": f"Bearer {MCP_KEY}"})
        assert response.status_code == 302
        assert response.headers["location"].startswith("/login")

    def test_mcp_key_does_not_authenticate_other_post_routes(self, mcp_client):
        response = mcp_client.post("/api/accounts/refresh-now", headers={"Authorization": f"Bearer {MCP_KEY}"})
        assert response.status_code == 302

    def test_mcp_key_in_the_ha_header_is_rejected_elsewhere(self, mcp_client):
        response = mcp_client.get("/api/accounts/holdings-list", headers={"X-API-Key": MCP_KEY})
        assert response.status_code == 401

    def test_mcp_is_not_an_api_key_route(self):
        from api_deps import API_KEY_ROUTES
        assert not [route for route in API_KEY_ROUTES if route[1].startswith("/mcp")]

    @pytest.mark.parametrize("method", ["get", "post", "put", "patch", "delete", "options", "head"])
    @pytest.mark.parametrize("path", ["/mcp", "/mcp/", "/mcp/anything"])
    def test_every_method_and_path_needs_the_key(self, mcp_client, method, path):
        assert getattr(mcp_client, method)(path).status_code == 401

    @pytest.mark.parametrize("method", ["get", "delete", "put", "patch"])
    def test_non_post_methods_are_405_with_the_key(self, mcp_client, method):
        response = getattr(mcp_client, method)("/mcp", headers={**RPC_HEADERS, "Authorization": f"Bearer {MCP_KEY}"})
        assert response.status_code == 405
        assert response.headers["allow"] == "POST"

    def test_trailing_slash_is_redirected_back_to_mcp_only_after_authentication(self, mcp_client):
        response = mcp_client.post("/mcp/", headers={**RPC_HEADERS, "Authorization": f"Bearer {MCP_KEY}"}, json={})
        assert response.status_code == 307
        assert response.headers["location"].endswith("/mcp")

    def test_unknown_subpath_is_404_with_the_key(self, mcp_client):
        assert mcp_client.post("/mcp/anything", headers={**RPC_HEADERS, "Authorization": f"Bearer {MCP_KEY}"}, json={}).status_code == 404

    def test_post_without_a_json_content_type_is_rejected(self, mcp_client):
        response = mcp_client.post("/mcp", headers={"Authorization": f"Bearer {MCP_KEY}", "Content-Type": "text/plain"}, content="x")
        assert response.status_code == 400

    def test_no_key_value_reaches_the_logs(self, mcp_client, caplog):
        caplog.set_level(logging.DEBUG)
        _rpc(mcp_client, "tools/list")
        _rpc(mcp_client, "tools/list", key="wrong-key-value-123")
        _rpc(mcp_client, "tools/list", key=None, headers={"X-API-Key": os.environ["API_KEY"]})
        assert MCP_KEY not in caplog.text
        assert "wrong-key-value-123" not in caplog.text
        assert os.environ["API_KEY"] not in caplog.text

    def test_refusing_because_the_key_is_unset_is_logged_without_a_secret(self, mcp_client, monkeypatch, caplog):
        monkeypatch.delenv("MCP_API_KEY")
        with caplog.at_level(logging.WARNING, logger="mcp_server"):
            _rpc(mcp_client, "tools/list", key="whatever-secret-attempt")
        assert "MCP_API_KEY is not set" in caplog.text
        assert "whatever-secret-attempt" not in caplog.text


class TestHostHandling:
    def test_every_trusted_host_is_accepted_by_both_layers(self, mcp_client):
        import main
        for host in main._TRUSTED_HOSTS:
            response = _rpc(mcp_client, "tools/list", headers={"Host": f"{host}:8090"})
            assert response.status_code == 200, host
            response = _rpc(mcp_client, "tools/list", headers={"Host": host})
            assert response.status_code == 200, host

    def test_unlisted_host_is_refused(self, mcp_client):
        response = _rpc(mcp_client, "tools/list", headers={"Host": "evil.example"})
        assert response.status_code == 400

    def test_foreign_origin_is_refused_by_the_sdk(self, mcp_client):
        response = _rpc(mcp_client, "tools/list", headers={"Origin": "http://evil.example"})
        assert response.status_code == 403

    def test_trusted_origin_is_accepted(self, mcp_client):
        assert _rpc(mcp_client, "tools/list", headers={"Origin": "http://localhost:8090"}).status_code == 200

    def test_sdk_hosts_mirror_the_trusted_hosts(self):
        settings = mcp_server._transport_security(["a.example", "10.0.0.5"])
        assert settings.enable_dns_rebinding_protection is True
        assert set(settings.allowed_hosts) == {"a.example", "10.0.0.5", "a.example:*", "10.0.0.5:*"}
        assert set(settings.allowed_origins) == {
            f"{scheme}://{host}{port}"
            for host in ("a.example", "10.0.0.5") for scheme in ("http", "https") for port in ("", ":*")
        }


class TestTransport:
    def test_tools_list_is_exactly_the_expected_tools(self, mcp_client):
        tools = _rpc(mcp_client, "tools/list").json()["result"]["tools"]
        assert {tool["name"] for tool in tools} == EXPECTED_TOOLS

    def test_every_tool_is_declared_read_only(self, mcp_client):
        for tool in _rpc(mcp_client, "tools/list").json()["result"]["tools"]:
            assert tool["annotations"]["readOnlyHint"] is True
            assert tool["annotations"]["destructiveHint"] is False

    def test_server_identifies_by_its_canonical_name(self, mcp_client):
        response = _rpc(mcp_client, "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"},
        })
        assert response.json()["result"]["serverInfo"]["name"] == "Claude MCP Server"

    def test_no_active_lifespan_answers_503(self, mcp_client, monkeypatch):
        monkeypatch.setattr(mcp_server.endpoint, "active", [])
        assert _rpc(mcp_client, "tools/list").status_code == 503

    def test_overlapping_lifespans_each_get_their_own_session_manager(self):
        async def scenario():
            before = list(mcp_server.endpoint.active)
            async with mcp_server.run(["localhost"]):
                async with mcp_server.run(["localhost"]):
                    assert len(mcp_server.endpoint.active) == len(before) + 2
                assert len(mcp_server.endpoint.active) == len(before) + 1
            assert mcp_server.endpoint.active == before

        asyncio.run(scenario())

    def test_blocked_tool_call_does_not_stall_an_unrelated_request(self, mcp_client):
        entered, release = Event(), Event()

        def stalled_call(*args, **kwargs):
            entered.set()
            assert release.wait(10)
            return [], 0.0

        with patch("mcp_server.list_scope_accounts_with_values", side_effect=stalled_call):
            with ThreadPoolExecutor(max_workers=2) as pool:
                slow = pool.submit(_call, mcp_client, "get_accounts")
                try:
                    assert entered.wait(10)
                    unrelated = pool.submit(mcp_client.get, "/static/css/styles.css")
                    assert unrelated.result(timeout=3).status_code == 200
                    assert not slow.done()
                finally:
                    release.set()
                assert slow.result(timeout=20).status_code == 200

    def test_tool_failure_returns_fixed_text_not_the_exception(self, mcp_client):
        with patch("mcp_server.portfolio_metrics_engine.other_accounts_list", side_effect=RuntimeError("secret-path /etc/shadow")):
            response = _call(mcp_client, "get_accounts")
        result = response.json()["result"]
        assert result["isError"] is True
        assert "secret-path" not in response.text and "/etc/shadow" not in response.text


class TestEnvelope:
    def test_envelope_fields(self):
        with patch("mcp_server.get_exchange_session_state", return_value="closed"):
            result = mcp_server.envelope({"x": 1}, as_of=1_700_000_000.0, units={"u": "v"}, missing=[])
        assert set(result) == {"as_of", "age_seconds", "market_state", "base_currency", "units", "stale", "missing", "data"}
        assert result["as_of"] == "2023-11-14T22:13:20Z"
        assert result["market_state"] == {"LSE": "closed", "NYSE": "closed"}
        assert result["data"] == {"x": 1}
        assert result["base_currency"] == mcp_server.BASE_CURRENCY

    @pytest.mark.parametrize("state, age, missing, stale", [
        ("open", mcp_server.STALE_AFTER_SECONDS + 60, [], True),
        ("open", 10, [], False),
        ("closed", mcp_server.STALE_AFTER_SECONDS * 100, [], False),
        ("post", mcp_server.STALE_AFTER_SECONDS * 100, [], False),
        ("closed", 10, ["account 1: performance snapshot not computed yet"], True),
    ])
    def test_stale_marker(self, state, age, missing, stale):
        with patch("mcp_server.get_exchange_session_state", return_value=state), patch("mcp_server.time.time", return_value=2_000_000_000.0):
            result = mcp_server.envelope({}, as_of=2_000_000_000.0 - age, units={}, missing=missing)
        assert result["stale"] is stale
        assert result["missing"] == missing

    def test_no_data_point_is_not_stale_by_age(self):
        with patch("mcp_server.get_exchange_session_state", return_value="open"):
            result = mcp_server.envelope({}, as_of=None, units={}, missing=[])
        assert result["as_of"] is None and result["age_seconds"] is None and result["stale"] is False

    def test_non_finite_numbers_become_null(self):
        with patch("mcp_server.get_exchange_session_state", return_value="closed"):
            result = mcp_server.envelope({"a": float("nan"), "b": [float("inf")]}, as_of=None, units={}, missing=[])
        assert result["data"] == {"a": None, "b": [None]}


class TestGetAccounts:
    def _rows(self, mcp_client):
        data = _tool_data(_call(mcp_client, "get_accounts"))
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
        with patch("mcp_server.list_scope_accounts_with_values", return_value=([{"id": "uuid-1", "name": "GF Main", "value": 123.45}], 123.45)):
            data = _tool_data(_call(mcp_client, "get_accounts"))
        row = next(r for r in data["data"]["accounts"] if r["scope_id"] == "uuid-1")
        assert row == {"account_id": None, "name": "GF Main", "account_type": "Ghostfolio", "scope_id": "uuid-1", "equity_value": 123.45}

    def test_fx_is_read_from_the_persisted_quote(self, mcp_client):
        from portfolio_service import _persisted_fx_reads
        seen = []

        def spy():
            seen.append(_persisted_fx_reads.get())
            return {"accounts": []}

        with patch("mcp_server.portfolio_metrics_engine.other_accounts_list", side_effect=spy):
            _tool_data(_call(mcp_client, "get_accounts"))
        assert seen == [True]

    def test_every_tool_leaves_the_database_unchanged(self, mcp_client, seeded_accounts):
        before = _table_counts()
        tools = _rpc(mcp_client, "tools/list").json()["result"]["tools"]
        for tool in tools:
            _tool_data(_call(mcp_client, tool["name"]))
        assert _table_counts() == before


class TestWorkflowEntry:
    def test_entry_is_an_external_node_with_the_canonical_label(self):
        meta = JOB_GRAPH["claude_mcp_server_source"]
        assert meta["label"] == "Claude MCP Server"
        assert meta["category"] == "external"
        assert meta["produces"] == []

    def test_node_keeps_consumes_and_every_artifact_has_a_producer(self):
        meta = JOB_GRAPH["claude_mcp_server_source"]
        node = _build_node("claude_mcp_server_source", meta, None, {})
        assert node["consumes"] == meta["consumes"] and node["consumes"]
        produced = {artifact for other in JOB_GRAPH.values() for artifact in other["produces"]}
        assert set(node["consumes"]) <= produced

    def test_graph_draws_an_edge_into_the_node(self):
        nodes = [_build_node(job_id, meta, None, {}) for job_id, meta in JOB_GRAPH.items() if not meta.get("dynamic")]
        edges = [e for e in _derive_edges(nodes) if e["to"] == "claude_mcp_server_source"]
        assert {e["via"] for e in edges} == set(JOB_GRAPH["claude_mcp_server_source"]["consumes"])

    def test_yahoo_source_still_has_no_inputs(self):
        assert _build_node("yahoo_finance_source", JOB_GRAPH["yahoo_finance_source"], None, {})["consumes"] == []
