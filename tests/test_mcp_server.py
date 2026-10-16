"""Claude MCP Server (mcp_server.py, mounted at /mcp): Bearer-key auth, transport, response envelope, get_accounts, get_holdings, get_xray, get_heat_index, investment policy resource, read-only guarantee."""
import asyncio
import logging
import os
import re
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from unittest.mock import patch

import pytest

import accounts_engine
import mcp_server
from database import add_transaction, create_account, get_connection, get_performance_cache
from scheduler_manifest import JOB_GRAPH
from scheduler_monitor import _build_node, _derive_edges
from xray_engine import XRayScopeUnavailable

MCP_KEY = "test-mcp-key-do-not-use-in-production"
RPC_HEADERS = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
EXPECTED_TOOLS = {"get_accounts", "get_holdings", "get_xray", "get_heat_index"}


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

    @pytest.mark.parametrize("stale_after, stale", [(mcp_server.STALE_AFTER_SECONDS, True), (mcp_server.NIGHTLY_STALE_AFTER_SECONDS, False)])
    def test_stale_after_can_be_widened_per_tool(self, stale_after, stale):
        with patch("mcp_server.get_exchange_session_state", return_value="open"), patch("mcp_server.time.time", return_value=2_000_000_000.0):
            result = mcp_server.envelope({}, as_of=2_000_000_000.0 - 2 * 86400, units={}, missing=[], stale_after_seconds=stale_after)
        assert result["stale"] is stale

    def test_nightly_tools_are_stale_only_after_a_missed_run(self):
        with patch("mcp_server.get_exchange_session_state", return_value="open"), patch("mcp_server.time.time", return_value=2_000_000_000.0):
            overdue = mcp_server.envelope(
                {}, as_of=2_000_000_000.0 - mcp_server.NIGHTLY_STALE_AFTER_SECONDS - 60, units={}, missing=[],
                stale_after_seconds=mcp_server.NIGHTLY_STALE_AFTER_SECONDS,
            )
        assert overdue["stale"] is True

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


def _error_text(response):
    assert response.status_code == 200, response.text
    result = response.json()["result"]
    assert result["isError"] is True, result
    return re.sub(r"^Error executing tool \w+: ", "", result["content"][0]["text"])


def _fake_rate(currency, **kwargs):
    return {"USD": None, "GBp": 0.01}.get(currency, 1.0)


_BOOK_TICKERS = ("ZZMCPA", "ZZMCPB", "ZZMCPU", "ZZMCPP")
_PRICE_EPOCH_B = "2026-10-01 09:00:00"


@pytest.fixture
def holdings_book(monkeypatch):
    import time
    monkeypatch.setattr(accounts_engine, "get_rate_to_base", _fake_rate)
    monkeypatch.setattr("portfolio_metrics_engine.get_rate_to_base", _fake_rate)
    live_epoch = time.time() - 120
    conn = get_connection()
    try:
        for ticker, price, currency, updated in (
            ("ZZMCPA", 120.0, "GBP", None), ("ZZMCPB", 50.0, "GBP", _PRICE_EPOCH_B),
            ("ZZMCPU", 10.0, "USD", _PRICE_EPOCH_B), ("ZZMCPP", 5000.0, "GBp", _PRICE_EPOCH_B),
        ):
            conn.execute(
                "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, quote_type, last_updated) VALUES (?, ?, ?, 'EQUITY', ?)",
                (ticker, price, currency, updated),
            )
        conn.execute(
            "INSERT OR REPLACE INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, is_positive, last_updated, "
            "extended_price, extended_change_pct, extended_session) VALUES ('ZZMCPA', 'A', 120.0, 1.5, 1.27, 1, ?, 125.0, 4.2, 'pre')",
            (live_epoch,),
        )
        conn.commit()
    finally:
        conn.close()
    first = create_account("McpBookA", "GBP", initial_cash=0.0)
    second = create_account("McpBookB", "GBP", initial_cash=0.0)
    empty = create_account("McpBookEmpty", "GBP")
    pension = create_account("McpBookPension", "GBP", account_type="Pension")
    add_transaction(first, "Buy", "2026-01-05", ticker="ZZMCPA", currency="GBP", quantity=10, unit_price=100, exchange_rate=1.0)
    add_transaction(first, "Buy", "2026-01-05", ticker="ZZMCPB", currency="GBP", quantity=5, unit_price=40, exchange_rate=1.0)
    add_transaction(second, "Buy", "2026-01-06", ticker="ZZMCPA", currency="GBP", quantity=2, unit_price=110, exchange_rate=1.0)
    add_transaction(second, "Buy", "2026-01-06", ticker="ZZMCPU", currency="USD", quantity=3, unit_price=8, exchange_rate=0.8)
    add_transaction(second, "Buy", "2026-01-06", ticker="ZZMCPP", currency="GBP", quantity=2, unit_price=40, exchange_rate=1.0)
    ids = {"first": first, "second": second, "empty": empty, "pension": pension, "live_epoch": live_epoch}
    try:
        yield ids
    finally:
        conn = get_connection()
        try:
            for account_id in (first, second, empty, pension):
                for table in _ACCOUNT_TABLES:
                    conn.execute(f"DELETE FROM {table} WHERE account_id = ?", (account_id,))
                conn.execute("DELETE FROM accounts WHERE id = ?", (account_id,))
            for ticker in _BOOK_TICKERS:
                conn.execute("DELETE FROM stock_signals WHERE ticker = ?", (ticker,))
                conn.execute("DELETE FROM market_pulse_cache WHERE ticker = ?", (ticker,))
                conn.execute("DELETE FROM xray_risk_cache WHERE ticker = ?", (ticker,))
            conn.commit()
        finally:
            conn.close()


class TestAccountArgument:
    @pytest.mark.parametrize("tool", ["get_holdings", "get_xray"])
    def test_unknown_account_is_a_clear_error_listing_the_valid_values(self, mcp_client, holdings_book, tool):
        text = _error_text(_call(mcp_client, tool, {"account": "nonsense"}))
        assert text.startswith("Unknown account")
        assert "all" in text and f"acct:{holdings_book['first']}" in text
        assert "nonsense" not in text

    @pytest.mark.parametrize("tool", ["get_holdings", "get_xray"])
    def test_pension_and_missing_ids_are_not_accounts_for_these_tools(self, mcp_client, holdings_book, tool):
        for account in (f"acct:{holdings_book['pension']}", "acct:999999", "1", f"acct:{holdings_book['first']} ", "ALL"):
            assert _error_text(_call(mcp_client, tool, {"account": account})).startswith("Unknown account"), account

    @pytest.mark.parametrize("tool", ["get_holdings", "get_xray"])
    def test_soft_deleted_account_is_not_exposed(self, mcp_client, holdings_book, tool):
        from database import soft_delete_account
        soft_delete_account(holdings_book["empty"])
        assert _error_text(_call(mcp_client, tool, {"account": f"acct:{holdings_book['empty']}"})).startswith("Unknown account")

    @pytest.mark.parametrize("tool", ["get_holdings", "get_xray"])
    def test_argument_longer_than_the_cap_is_refused_before_any_lookup(self, mcp_client, tool):
        response = _call(mcp_client, tool, {"account": "x" * (mcp_server.MCP_ACCOUNT_SCOPE_MAX_CHARS + 1)})
        assert response.json()["result"]["isError"] is True

    def test_cap_comes_from_the_request_limits_block(self):
        import constants
        assert mcp_server.MCP_ACCOUNT_SCOPE_MAX_CHARS is constants.MCP_ACCOUNT_SCOPE_MAX_CHARS

    def test_ghostfolio_account_is_accepted_by_xray_only(self, mcp_client):
        config = {"GHOSTFOLIO_ACCOUNTS": {"active": ["gf-uuid-1"]}}
        with patch("mcp_server.load_config", return_value=config):
            assert "Ghostfolio" in _error_text(_call(mcp_client, "get_holdings", {"account": "gf-uuid-1"}))
            assert _error_text(_call(mcp_client, "get_holdings", {"account": "gf-uuid-2"})).startswith("Unknown account")
            with patch("mcp_server.assemble_xray_report", side_effect=XRayScopeUnavailable("Ghostfolio authentication failed.")) as xray:
                assert _error_text(_call(mcp_client, "get_xray", {"account": "gf-uuid-1"})) == "Ghostfolio authentication failed."
            xray.assert_called_once_with("gf-uuid-1")


class TestGetHoldings:
    def _rows(self, mcp_client, account="all"):
        data = _tool_data(_call(mcp_client, "get_holdings", {"account": account}))
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
        with patch("mcp_server.load_config", return_value={"GHOSTFOLIO_ACCOUNTS": {"active": ["gf-uuid-1"]}}):
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

        with patch("mcp_server.portfolio_metrics_engine.holdings_with_metrics_all_accounts", side_effect=spy):
            _tool_data(_call(mcp_client, "get_holdings"))
        assert seen == [True]

    def test_failure_returns_fixed_text_not_the_exception(self, mcp_client):
        with patch("mcp_server.portfolio_metrics_engine.holdings_with_metrics_all_accounts", side_effect=RuntimeError("secret-path /etc/shadow")):
            response = _call(mcp_client, "get_holdings")
        assert response.json()["result"]["isError"] is True
        assert "secret-path" not in response.text and "/etc/shadow" not in response.text

    def test_leaves_the_database_unchanged(self, mcp_client, holdings_book):
        before = _table_counts()
        _tool_data(_call(mcp_client, "get_holdings"))
        assert _table_counts() == before


def _xray_cache(ticker, beta, cache_date):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO xray_risk_cache (ticker, benchmark, last_updated, beta, annualized_vol) VALUES (?, 'SWDA.L', ?, ?, 0.2)",
            (ticker, cache_date, beta),
        )
        conn.commit()
    finally:
        conn.close()


def _fake_report(**overrides):
    report = {
        "account_id": "all", "portfolio_total_value": 1000.0, "portfolio_total_investment": 900.0,
        "holdings": [
            {"symbol": f"T{i}", "name": f"Tick {i}", "weight": 0.1, "value": 100.0, "max_pairwise_correlation": 0.5 + i / 100}
            for i in range(12)
        ],
        "concentration": {"hhi": 0.1, "top5_weight": 0.5, "top10_weight": 0.9},
        "sector_allocation": [{"name": f"S{i}", "weight": 0.05} for i in range(12)],
        "geographic_allocation": [{"name": "Europe", "weight": 1.0}],
        "fx_exposure": [{"currency": "GBP", "weight": 1.0}],
        "risk_metrics": {
            "portfolio_beta": 1.1, "annualized_vol": 0.2, "max_drawdown": -0.1, "var_95_1d": 20.0, "sharpe_ratio": 0.5,
            "benchmark": "SWDA.L", "cache_date": "2026-10-15", "avg_pairwise_correlation": 0.3, "max_pairwise_correlation": 0.6,
        },
        "recommendations": {
            "sector": [
                {"category": f"S{i}", "status": "exceeds", "current_value": 20.0, "min": None, "max": 10.0, "unit": "%", "message": f"S{i} high"}
                for i in range(7)
            ] + [{"category": "Fine", "status": "within", "current_value": 5.0, "min": 1.0, "max": 10.0, "unit": "%", "message": "ok"}],
            "concentration": [
                {"category": f"C{i}", "status": "below", "current_value": 1.0, "min": 5.0, "max": None, "unit": "%", "message": f"C{i} low"}
                for i in range(5)
            ],
        },
        "data_warnings": ["one warning"],
    }
    report.update(overrides)
    return report


class TestGetXray:
    def _xray(self, mcp_client, account="all"):
        return _tool_data(_call(mcp_client, "get_xray", {"account": account}))

    def _patched_targets(self):
        import xray_engine
        real = xray_engine.load_config
        return patch("xray_engine.load_config", side_effect=lambda: {**real(), "XRAY_TARGETS": {"concentration_targets": {"max_single_position_pct": 50.0}}})

    def test_envelope_units_and_summary_for_one_account(self, mcp_client, holdings_book):
        with self._patched_targets():
            data = self._xray(mcp_client, f"acct:{holdings_book['first']}")
        assert set(data) == {"as_of", "age_seconds", "market_state", "base_currency", "units", "stale", "missing", "data"}
        assert data["units"]["weights"].startswith("fraction of invested capital")
        body = data["data"]
        assert body["scope_id"] == f"acct:{holdings_book['first']}"
        assert (body["portfolio_total_value"], body["portfolio_total_investment"], body["positions"]) == (1450.0, 1200.0, 2)
        weights = (1200 / 1450, 250 / 1450)
        concentration = body["concentration"]
        assert [p["symbol"] for p in concentration["largest_positions"]] == ["ZZMCPA", "ZZMCPB"]
        assert concentration["largest_positions"][0]["weight"] == pytest.approx(weights[0], abs=1e-4)
        assert concentration["hhi"] == pytest.approx(sum(w * w for w in weights), abs=1e-4)
        assert concentration["top5_weight"] == pytest.approx(1.0, abs=1e-4)
        assert body["currency_exposure"] == [{"currency": "GBP", "weight": 1.0}]
        assert body["geographic_exposure"] and body["sector_exposure"]
        assert "holdings" not in body and "correlation_matrix" not in body

    def test_target_breaches_become_findings(self, mcp_client, holdings_book):
        with self._patched_targets():
            body = self._xray(mcp_client, f"acct:{holdings_book['first']}")["data"]
        assert body["targets_checked"] >= 1
        finding = next(f for f in body["findings"] if f["target"] == "Max Single Position")
        assert finding["status"] == "exceeds" and finding["max"] == 50.0 and finding["unit"] == "%"
        assert finding["current_value"] == pytest.approx(1200 / 1450 * 100, abs=0.01)

    def test_without_a_risk_cache_the_risk_figures_are_null_and_marked_missing(self, mcp_client, holdings_book):
        data = self._xray(mcp_client, f"acct:{holdings_book['first']}")
        assert data["data"]["risk"]["portfolio_beta"] is None
        assert data["as_of"] is None and data["stale"] is True
        assert any(item.startswith("risk.portfolio_beta") for item in data["missing"])
        assert any(item.startswith("correlation.maximum") for item in data["missing"])
        assert data["data"]["data_warnings"]

    def test_risk_cache_date_is_the_as_of_and_a_nightly_figure_is_not_stale_in_session(self, mcp_client, holdings_book):
        from datetime import datetime, timedelta, timezone
        yesterday = (datetime.now(timezone.utc) - timedelta(days=1)).date().isoformat()
        _xray_cache("ZZMCPA", 1.2, yesterday)
        _xray_cache("ZZMCPB", 0.8, yesterday)
        with patch("mcp_server.get_exchange_session_state", return_value="open"):
            data = self._xray(mcp_client, f"acct:{holdings_book['first']}")
        assert data["as_of"] == f"{yesterday}T00:00:00Z"
        assert data["data"]["risk_cache_date"] == yesterday
        assert data["data"]["risk"]["portfolio_beta"] == pytest.approx((1200 * 1.2 + 250 * 0.8) / 1450, abs=0.001)
        assert data["data"]["risk"]["benchmark"] == "SWDA.L"
        assert not any(item.startswith("risk.portfolio_beta") for item in data["missing"])

    def test_empty_scope_returns_the_curated_message(self, mcp_client, holdings_book):
        text = _error_text(_call(mcp_client, "get_xray", {"account": f"acct:{holdings_book['empty']}"}))
        assert text == "No holdings found for this scope (Ghostfolio and/or built-in accounts may be empty)."

    def test_scope_unavailable_maps_to_its_own_curated_text(self, mcp_client):
        with patch("mcp_server.assemble_xray_report", side_effect=XRayScopeUnavailable("Ghostfolio is not configured (check GHOSTFOLIO_URL / GHOSTFOLIO_TOKEN).")):
            text = _error_text(_call(mcp_client, "get_xray"))
        assert text == "Ghostfolio is not configured (check GHOSTFOLIO_URL / GHOSTFOLIO_TOKEN)."

    def test_other_failures_return_fixed_text_not_the_exception(self, mcp_client):
        with patch("mcp_server.assemble_xray_report", side_effect=RuntimeError("secret-path /etc/shadow")):
            response = _call(mcp_client, "get_xray")
        assert response.json()["result"]["isError"] is True
        assert "secret-path" not in response.text and "/etc/shadow" not in response.text

    def test_default_scope_is_all(self, mcp_client):
        with patch("mcp_server.assemble_xray_report", return_value=_fake_report()) as xray:
            _tool_data(_call(mcp_client, "get_xray"))
        xray.assert_called_once_with("all")

    def test_fx_is_read_from_the_persisted_quote(self, mcp_client):
        from portfolio_service import _persisted_fx_reads
        seen = []

        def spy(scope):
            seen.append(_persisted_fx_reads.get())
            return _fake_report()

        with patch("mcp_server.assemble_xray_report", side_effect=spy):
            _tool_data(_call(mcp_client, "get_xray"))
        assert seen == [True]

    def test_summary_is_capped_and_ordered(self):
        body, missing = mcp_server._xray_summary(_fake_report())
        assert missing == []
        assert len(body["concentration"]["largest_positions"]) == mcp_server.XRAY_TOP_POSITIONS
        assert [h["symbol"] for h in body["correlation"]["most_correlated_holdings"]] == ["T11", "T10", "T9"]
        assert len(body["sector_exposure"]) == mcp_server.XRAY_TOP_EXPOSURES
        assert body["targets_checked"] == 13
        assert body["findings_total"] == 12 and len(body["findings"]) == mcp_server.XRAY_MAX_FINDINGS == body["findings_cap"]
        assert [f["target"] for f in body["findings"]][:2] == ["S0", "S1"]
        assert all(f["status"] in ("exceeds", "below") for f in body["findings"])
        assert body["data_warnings"] == ["one warning"]

    def test_null_risk_metrics_are_listed_as_missing(self):
        report = _fake_report()
        report["risk_metrics"].update(portfolio_beta=None, max_pairwise_correlation=None, cache_date=None)
        body, missing = mcp_server._xray_summary(report)
        assert body["risk"]["portfolio_beta"] is None and body["correlation"]["maximum"] is None
        assert missing == ["risk.portfolio_beta: not available (see data_warnings)", "correlation.maximum: not available (see data_warnings)"]
        assert mcp_server._xray_as_of(None) is None

    def test_leaves_the_database_unchanged(self, mcp_client, holdings_book):
        before = _table_counts()
        self._xray(mcp_client, f"acct:{holdings_book['first']}")
        assert _table_counts() == before


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
        return _tool_data(_call(mcp_client, "get_heat_index"))

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
        assert len(contributors) == mcp_server.HEAT_TOP_CONTRIBUTORS
        assert set(contributors[0]) == {"ticker", "risk_score", "risk_tier", "marginal_var_contribution_pct", "max_pairwise_correlation", "stop_distance_pct"}

    def test_not_computed_yet_is_null_and_marked_missing(self, mcp_client, heat_tables):
        data = self._heat(mcp_client)
        assert data["data"] == {"heat_index": None, "top_risk_contributors": []}
        assert data["as_of"] is None and data["stale"] is True
        assert any("has not been computed yet" in item for item in data["missing"])

    def test_unavailable_input_is_flagged_not_shown_as_zero(self, mcp_client, heat_tables):
        from risk_orchestrator_engine import persist_heat_index, persist_ticker_contributions
        persist_heat_index({**_HEAT_ROW, "var_pct_of_equity": None, "var_tier": "GREEN"})
        persist_ticker_contributions([_contribution("ZZH1", 10.0)])
        data = self._heat(mcp_client)
        assert data["data"]["heat_index"]["var"]["pct_of_equity"] is None
        assert data["missing"] == ["Heat Index VaR input is not available, so the score counts it as zero risk"]

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
        with patch("mcp_server.get_exchange_session_state", return_value="open"), \
                patch("mcp_server.time.time", return_value=mcp_server.parse_utc_epoch("2026-10-16 10:00:00")):
            assert mcp_server.get_heat_index()["stale"] is False
        with patch("mcp_server.get_exchange_session_state", return_value="open"), \
                patch("mcp_server.time.time", return_value=mcp_server.parse_utc_epoch("2026-10-21 10:00:00")):
            assert mcp_server.get_heat_index()["stale"] is True

    def test_takes_no_arguments_and_reads_the_all_scope_only(self, mcp_client, heat_tables):
        from risk_orchestrator_engine import persist_heat_index
        persist_heat_index({**_HEAT_ROW, "scope": "acct:1", "scope_label": "Other", "phi_score": 99.0, "tier": "RED"})
        assert self._heat(mcp_client)["data"]["heat_index"] is None
        tool = next(t for t in _rpc(mcp_client, "tools/list").json()["result"]["tools"] if t["name"] == "get_heat_index")
        assert tool["inputSchema"].get("properties", {}) == {}

    def test_leaves_the_database_unchanged(self, mcp_client, heat_tables):
        from risk_orchestrator_engine import persist_heat_index, persist_ticker_contributions
        persist_heat_index(dict(_HEAT_ROW))
        persist_ticker_contributions([_contribution("ZZH1", 10.0)])
        before = _table_counts()
        self._heat(mcp_client)
        assert _table_counts() == before


POLICY_URI = "policy://investment-policy"


@pytest.fixture
def policy_path(tmp_path, monkeypatch):
    path = tmp_path / "investment_policy.md"
    monkeypatch.setattr(mcp_server, "POLICY_PATH", path)
    return path


def _read_policy(client, uri=POLICY_URI, **kwargs):
    return _rpc(client, "resources/read", {"uri": uri}, **kwargs)


def _policy_text(client):
    response = _read_policy(client)
    assert response.status_code == 200, response.text
    contents = response.json()["result"]["contents"]
    assert len(contents) == 1 and contents[0]["uri"] == POLICY_URI
    return contents[0]["text"]


class TestInvestmentPolicy:
    def test_resource_is_listed_and_is_the_only_one(self, mcp_client):
        resources = _rpc(mcp_client, "resources/list").json()["result"]["resources"]
        assert [r["uri"] for r in resources] == [POLICY_URI]
        listed = resources[0]
        assert listed["mimeType"] == "text/markdown"
        assert "BEFORE any analysis" in listed["description"]
        assert "instead of inventing one" in listed["description"]

    def test_resource_needs_the_mcp_key(self, mcp_client, policy_path):
        policy_path.write_text("secret policy", encoding="utf-8")
        assert _read_policy(mcp_client, key=None).status_code == 401
        assert _read_policy(mcp_client, key="wrong").status_code == 401

    def test_missing_file_gives_the_notice_and_the_template(self, mcp_client, policy_path):
        assert not policy_path.exists()
        text = _policy_text(mcp_client)
        assert text.startswith("POLICY NOT CONFIGURED")
        assert "Do not invent goals, limits or rules" in text
        assert mcp_server.POLICY_TEMPLATE_PATH.read_text(encoding="utf-8") in text

    def test_template_covers_every_suggested_section(self):
        template = mcp_server.POLICY_TEMPLATE_PATH.read_text(encoding="utf-8").lower()
        for expected in (
            "goals and horizon", "risk tolerance", "max weight of one position", "max weight of one sector",
            "max weight of one country", "max weight of one currency", "tax wrappers and constraints",
            "never-do list", "profit-taking style", "preferred response format",
        ):
            assert expected in template

    @pytest.mark.parametrize("content", ["", "   \n\t\n"])
    def test_empty_file_is_treated_as_missing(self, mcp_client, policy_path, content):
        policy_path.write_text(content, encoding="utf-8")
        assert _policy_text(mcp_client).startswith("POLICY NOT CONFIGURED")

    def test_present_file_is_served_as_written(self, mcp_client, policy_path):
        content = "# My policy\n\n- Max position: 10% — never above £5,000\n- Zażółć gęślą jaźń\n"
        policy_path.write_text(content, encoding="utf-8")
        assert _policy_text(mcp_client) == content

    def test_utf8_bom_is_dropped(self, mcp_client, policy_path):
        policy_path.write_bytes(b"\xef\xbb\xbf# Policy\n")
        assert _policy_text(mcp_client) == "# Policy\n"

    def test_edits_show_up_on_the_next_read(self, mcp_client, policy_path):
        policy_path.write_text("first", encoding="utf-8")
        assert _policy_text(mcp_client) == "first"
        policy_path.write_text("second", encoding="utf-8")
        assert _policy_text(mcp_client) == "second"

    def test_file_at_the_cap_is_served(self, mcp_client, policy_path):
        content = "x" * mcp_server.INVESTMENT_POLICY_MAX_CHARS
        policy_path.write_text(content, encoding="utf-8")
        assert _policy_text(mcp_client) == content

    def test_oversize_file_is_refused_not_cut(self, mcp_client, policy_path):
        policy_path.write_text("x" * (mcp_server.INVESTMENT_POLICY_MAX_CHARS + 1), encoding="utf-8")
        text = _policy_text(mcp_client)
        assert text.startswith("POLICY NOT LOADED")
        assert str(mcp_server.INVESTMENT_POLICY_MAX_CHARS) in text
        assert "xxxx" not in text
        assert "Do not invent goals, limits or rules" in text

    def test_cap_comes_from_the_request_limits_block(self):
        import constants
        assert mcp_server.INVESTMENT_POLICY_MAX_CHARS is constants.INVESTMENT_POLICY_MAX_CHARS

    def test_file_that_is_not_utf8_is_reported_not_guessed(self, mcp_client, policy_path, caplog):
        policy_path.write_bytes("Max position £5,000".encode("latin-1"))
        with caplog.at_level(logging.ERROR, logger="mcp_server"):
            text = _policy_text(mcp_client)
        assert text.startswith("POLICY NOT LOADED")
        assert "Max position" not in text
        assert "could not be read" in caplog.text

    def test_unreadable_path_is_reported(self, mcp_client, policy_path):
        policy_path.mkdir()
        assert _policy_text(mcp_client).startswith("POLICY NOT LOADED")

    def test_no_caller_supplied_path_exists(self, mcp_client, policy_path, tmp_path):
        import inspect
        assert not inspect.signature(mcp_server.investment_policy).parameters
        assert _rpc(mcp_client, "resources/templates/list").json()["result"]["resourceTemplates"] == []
        elsewhere = tmp_path / "elsewhere.md"
        elsewhere.write_text("do not leak", encoding="utf-8")
        policy_path.write_text("the policy", encoding="utf-8")
        for uri in (
            f"{POLICY_URI}?path={elsewhere}", f"{POLICY_URI}/{elsewhere}", f"{POLICY_URI}/../../etc/passwd",
            f"file://{elsewhere}", "policy://other",
        ):
            response = _read_policy(mcp_client, uri=uri)
            assert "error" in response.json(), uri
            assert "do not leak" not in response.text, uri

    def test_reading_never_writes_the_file_or_the_database(self, mcp_client, policy_path):
        before = _table_counts()
        _policy_text(mcp_client)
        assert not policy_path.exists()
        policy_path.write_text("keep me", encoding="utf-8")
        stat = policy_path.stat()
        _policy_text(mcp_client)
        assert policy_path.read_bytes() == b"keep me"
        assert policy_path.stat().st_mtime_ns == stat.st_mtime_ns
        assert _table_counts() == before

    def test_no_write_method_is_offered(self, mcp_client):
        assert _rpc(mcp_client, "resources/subscribe", {"uri": POLICY_URI}).json().get("error")
        capabilities = _rpc(mcp_client, "initialize", {
            "protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "t", "version": "1"},
        }).json()["result"]["capabilities"]
        assert not capabilities.get("resources", {}).get("subscribe")


class TestWorkflowEntry:
    def test_policy_is_a_manual_input_that_the_server_consumes(self):
        meta = JOB_GRAPH["investment_policy_source"]
        assert meta["label"] == "Investment Policy"
        assert meta["category"] == "manual" and meta["non_job"] is True
        assert meta["produces"] == ["investment_policy"] and meta["consumes"] == []
        assert "investment_policy" in JOB_GRAPH["claude_mcp_server_source"]["consumes"]

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
