"""Streamable HTTP transport on /mcp: Host/Origin handling, tool list, read-only annotations, lifespans, worker threads, fixed error text."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

from mcp_server import transport

from .helpers import EXPECTED_TOOLS, call, rpc, table_counts, tool_data


class TestHostHandling:
    def test_every_trusted_host_is_accepted_by_both_layers(self, mcp_client):
        import main
        for host in main._TRUSTED_HOSTS:
            response = rpc(mcp_client, "tools/list", headers={"Host": f"{host}:8090"})
            assert response.status_code == 200, host
            response = rpc(mcp_client, "tools/list", headers={"Host": host})
            assert response.status_code == 200, host

    def test_unlisted_host_is_refused(self, mcp_client):
        response = rpc(mcp_client, "tools/list", headers={"Host": "evil.example"})
        assert response.status_code == 400

    def test_foreign_origin_is_refused_by_the_sdk(self, mcp_client):
        response = rpc(mcp_client, "tools/list", headers={"Origin": "http://evil.example"})
        assert response.status_code == 403

    def test_trusted_origin_is_accepted(self, mcp_client):
        assert rpc(mcp_client, "tools/list", headers={"Origin": "http://localhost:8090"}).status_code == 200

    def test_sdk_hosts_mirror_the_trusted_hosts(self):
        settings = transport._transport_security(["a.example", "10.0.0.5"])
        assert settings.enable_dns_rebinding_protection is True
        assert set(settings.allowed_hosts) == {"a.example", "10.0.0.5", "a.example:*", "10.0.0.5:*"}
        assert set(settings.allowed_origins) == {
            f"{scheme}://{host}{port}"
            for host in ("a.example", "10.0.0.5") for scheme in ("http", "https") for port in ("", ":*")
        }


class TestTransport:
    def test_tools_list_is_exactly_the_expected_tools(self, mcp_client):
        tools = rpc(mcp_client, "tools/list").json()["result"]["tools"]
        assert {tool["name"] for tool in tools} == EXPECTED_TOOLS

    def test_every_tool_is_declared_read_only(self, mcp_client):
        for tool in rpc(mcp_client, "tools/list").json()["result"]["tools"]:
            assert tool["annotations"]["readOnlyHint"] is True
            assert tool["annotations"]["destructiveHint"] is False

    def test_server_identifies_by_its_canonical_name(self, mcp_client):
        response = rpc(mcp_client, "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "pytest", "version": "1"},
        })
        assert response.json()["result"]["serverInfo"]["name"] == "MCP Server"

    def test_no_active_lifespan_answers_503(self, mcp_client, monkeypatch):
        monkeypatch.setattr(transport.endpoint, "active", [])
        assert rpc(mcp_client, "tools/list").status_code == 503

    def test_overlapping_lifespans_each_get_their_own_session_manager(self):
        async def scenario():
            before = list(transport.endpoint.active)
            async with transport.run(["localhost"]):
                async with transport.run(["localhost"]):
                    assert len(transport.endpoint.active) == len(before) + 2
                assert len(transport.endpoint.active) == len(before) + 1
            assert transport.endpoint.active == before

        asyncio.run(scenario())

    def test_blocked_tool_call_does_not_stall_an_unrelated_request(self, mcp_client):
        entered, release = Event(), Event()

        def stalled_call(*args, **kwargs):
            entered.set()
            assert release.wait(10)
            return [], 0.0

        with patch("mcp_server.tools.get_accounts.list_scope_accounts_with_values", side_effect=stalled_call):
            with ThreadPoolExecutor(max_workers=2) as pool:
                slow = pool.submit(call, mcp_client, "get_accounts")
                try:
                    assert entered.wait(10)
                    unrelated = pool.submit(mcp_client.get, "/static/css/styles.css")
                    assert unrelated.result(timeout=3).status_code == 200
                    assert not slow.done()
                finally:
                    release.set()
                assert slow.result(timeout=20).status_code == 200

    def test_tool_failure_returns_fixed_text_not_the_exception(self, mcp_client):
        with patch("mcp_server.tools.get_accounts.portfolio_metrics_engine.other_accounts_list", side_effect=RuntimeError("secret-path /etc/shadow")):
            response = call(mcp_client, "get_accounts")
        result = response.json()["result"]
        assert result["isError"] is True
        assert "secret-path" not in response.text and "/etc/shadow" not in response.text


class TestReadOnlyGuarantee:
    def test_every_tool_leaves_the_database_unchanged(self, mcp_client, seeded_accounts):
        before = table_counts()
        tools = rpc(mcp_client, "tools/list").json()["result"]["tools"]
        for tool in tools:
            arguments = {"ticker": "ZZMCP"} if "ticker" in tool["inputSchema"].get("properties", {}) else {}
            if "side" in tool["inputSchema"].get("properties", {}):
                arguments["side"] = "sell"
            tool_data(call(mcp_client, tool["name"], arguments))
        assert table_counts() == before
