"""Public MCP hostname guard (MCP-15b): the eight allowed (method, path) pairs, fail-closed for every other route, the in-app client-address check, and the config keys behind it."""
import asyncio
import re

import pytest
from starlette.responses import PlainTextResponse
from starlette.testclient import TestClient

import config
from database import get_connection

PUBLIC_HOST = "mcp.awroblew.biz"
IN_RANGE = "160.79.104.5"
OUT_OF_RANGE = "203.0.113.9"
EIGHT = {
    ("POST", "/mcp"),
    ("GET", "/.well-known/oauth-protected-resource/mcp"),
    ("GET", "/.well-known/oauth-authorization-server"),
    ("GET", "/oauth/authorize"),
    ("GET", "/oauth/approve"),
    ("GET", "/oauth/approve-status"),
    ("POST", "/oauth/token"),
    ("POST", "/oauth/revoke"),
}
MACHINE = {
    ("POST", "/mcp"), ("GET", "/.well-known/oauth-protected-resource/mcp"),
    ("GET", "/.well-known/oauth-authorization-server"), ("POST", "/oauth/token"), ("POST", "/oauth/revoke"),
}
ALL_METHODS = ("GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS")


def _settings(monkeypatch, enabled=True, cidrs=("160.79.104.0/21",)):
    monkeypatch.setattr(
        "mcp_server.public_host.load_config",
        lambda: {"MCP_REMOTE_ENABLED": enabled, "MCP_REMOTE_ALLOWED_CIDRS": list(cidrs)},
    )


class _Reached:
    """Inner app that records the requests the guard let through."""

    def __init__(self):
        self.calls = []

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http":
            self.calls.append((scope["method"], scope["path"], scope["client"]))
            await PlainTextResponse("inner")(scope, receive, send)
        else:
            self.calls.append((scope["type"], None, None))


def _guarded(inner=None, public_host=PUBLIC_HOST, address=IN_RANGE, host=PUBLIC_HOST):
    from mcp_server import PublicHostGuard
    inner = inner or _Reached()
    return inner, TestClient(
        PublicHostGuard(inner, public_host=public_host), base_url=f"https://{host}",
        raise_server_exceptions=False, client=(address, 50000),
    )


def _request(client, method, path, **kwargs):
    return client.request(method, path, **kwargs)


class TestAllowlist:
    def test_the_eight_pairs_reach_the_app_when_enabled_and_in_range(self, monkeypatch):
        _settings(monkeypatch)
        inner, client = _guarded()
        for method, path in sorted(EIGHT):
            response = _request(client, method, path)
            assert response.status_code == 200 and response.text == "inner", (method, path)
        assert {(m, p) for m, p, _ in inner.calls} == EIGHT

    def test_every_pair_is_404_while_remote_access_is_off(self, monkeypatch):
        _settings(monkeypatch, enabled=False)
        inner, client = _guarded()
        for method, path in sorted(EIGHT):
            assert _request(client, method, path).status_code == 404, (method, path)
        assert inner.calls == []

    def test_anything_else_is_404_and_never_reaches_the_app(self, monkeypatch):
        _settings(monkeypatch)
        inner, client = _guarded()
        others = [
            ("GET", "/"), ("GET", "/login"), ("GET", "/portfolio"), ("GET", "/api/accounts/portfolio-totals"),
            ("GET", "/mcp"), ("DELETE", "/mcp"), ("OPTIONS", "/mcp"), ("HEAD", "/oauth/authorize"),
            ("POST", "/oauth/authorize"), ("GET", "/oauth/token"), ("POST", "/oauth/approve"),
            ("GET", "/mcp/"), ("POST", "/mcp/"), ("GET", "//mcp"), ("GET", "/MCP"), ("POST", "/mcp/extra"),
            ("GET", "/.well-known/oauth-protected-resource"), ("GET", "/.well-known/oauth-authorization-server/x"),
            ("GET", "/oauth/register"), ("GET", "/static/css/styles.css"), ("GET", "/assets/mcp_integration.md"),
        ]
        for method, path in others:
            response = _request(client, method, path)
            assert response.status_code == 404, (method, path)
            assert response.content == b"", (method, path)
        assert inner.calls == []

    @pytest.mark.parametrize("host_header", [PUBLIC_HOST.upper(), f"{PUBLIC_HOST}:8090", f"{PUBLIC_HOST}.", f"{PUBLIC_HOST.title()}.:443"])
    def test_variant_spellings_of_the_host_are_guarded_too(self, monkeypatch, host_header):
        _settings(monkeypatch)
        inner, client = _guarded()
        assert _request(client, "GET", "/login", headers={"Host": host_header}).status_code == 404
        assert inner.calls == []

    def test_other_hosts_are_untouched(self, monkeypatch):
        _settings(monkeypatch, enabled=False)
        inner, client = _guarded(host="stock.awroblew.biz")
        for method, path in (("GET", "/login"), ("GET", "/mcp"), ("POST", "/mcp")):
            assert _request(client, method, path).status_code == 200
        assert len(inner.calls) == 3

    def test_without_a_configured_host_nothing_is_guarded(self, monkeypatch):
        _settings(monkeypatch, enabled=False)
        inner, client = _guarded(public_host="")
        assert _request(client, "GET", "/login").status_code == 200
        assert len(inner.calls) == 1

    def test_websocket_on_the_public_host_is_closed_and_lifespan_passes(self, monkeypatch):
        _settings(monkeypatch)
        from mcp_server import PublicHostGuard
        inner = _Reached()
        guard = PublicHostGuard(inner, public_host=PUBLIC_HOST)
        sent = []

        async def send(message):
            sent.append(message)

        async def scenario():
            scope = {"type": "websocket", "path": "/ws", "headers": [(b"host", PUBLIC_HOST.encode())], "client": (IN_RANGE, 1)}
            await guard(scope, None, send)
            assert sent == [{"type": "websocket.close", "code": 1008}] and inner.calls == []
            await guard({"type": "websocket", "path": "/ws", "headers": [(b"host", b"localhost")]}, None, send)
            await guard({"type": "lifespan"}, None, send)

        asyncio.run(scenario())
        assert [call[0] for call in inner.calls] == ["websocket", "lifespan"]


class TestClientAddress:
    def test_machine_paths_refuse_an_address_outside_the_range_with_403(self, monkeypatch):
        _settings(monkeypatch)
        inner, client = _guarded(address=OUT_OF_RANGE)
        for method, path in sorted(MACHINE):
            response = _request(client, method, path)
            assert response.status_code == 403 and response.content == b"", (method, path)
        assert inner.calls == []

    def test_browser_paths_are_not_range_limited(self, monkeypatch):
        _settings(monkeypatch)
        inner, client = _guarded(address=OUT_OF_RANGE)
        for method, path in sorted(EIGHT - MACHINE):
            assert _request(client, method, path).status_code == 200, (method, path)
        assert len(inner.calls) == 3

    @pytest.mark.parametrize("address", ["160.79.104.0", "160.79.111.255", "::ffff:160.79.104.5"])
    def test_range_edges_and_mapped_ipv4_are_accepted(self, monkeypatch, address):
        _settings(monkeypatch)
        _, client = _guarded(address=address)
        assert _request(client, "POST", "/mcp").status_code == 200

    @pytest.mark.parametrize("address", ["160.79.103.255", "160.79.112.0", "127.0.0.1", "192.168.1.72", "2001:db8::1", "testclient", "not-an-address", ""])
    def test_neighbours_loopback_lan_ipv6_and_junk_are_refused(self, monkeypatch, address):
        _settings(monkeypatch)
        inner, client = _guarded(address=address)
        assert _request(client, "POST", "/mcp").status_code == 403
        assert inner.calls == []

    @pytest.mark.parametrize("cidrs", [[], ["garbage"], ["160.79.104.0/33"], "160.79.104.0/21", None, [None, 5]])
    def test_an_empty_or_broken_range_setting_refuses_everything(self, monkeypatch, cidrs):
        monkeypatch.setattr("mcp_server.public_host.load_config", lambda: {"MCP_REMOTE_ENABLED": True, "MCP_REMOTE_ALLOWED_CIDRS": cidrs})
        inner, client = _guarded()
        assert _request(client, "POST", "/mcp").status_code == 403
        assert inner.calls == []

    def test_ipv6_ranges_are_supported(self, monkeypatch):
        _settings(monkeypatch, cidrs=("2001:db8::/32",))
        _, inside = _guarded(address="2001:db8::7")
        _, outside = _guarded(address=IN_RANGE)
        assert _request(inside, "POST", "/mcp").status_code == 200
        assert _request(outside, "POST", "/mcp").status_code == 403

    def test_a_missing_peer_address_is_refused(self, monkeypatch):
        _settings(monkeypatch)
        from mcp_server import PublicHostGuard
        sent = []

        async def send(message):
            sent.append(message)

        async def receive():
            return {"type": "http.request", "body": b"", "more_body": False}

        scope = {
            "type": "http", "method": "POST", "path": "/mcp", "query_string": b"", "client": None,
            "headers": [(b"host", PUBLIC_HOST.encode())],
        }
        asyncio.run(PublicHostGuard(_Reached(), public_host=PUBLIC_HOST)(scope, receive, send))
        assert sent[0]["status"] == 403

    def test_a_refusal_is_written_to_the_audit_log_once_per_minute_per_address(self, monkeypatch):
        _settings(monkeypatch)
        _, client = _guarded(address=OUT_OF_RANGE)
        for _ in range(3):
            assert _request(client, "POST", "/mcp").status_code == 403

        def rows():
            conn = get_connection()
            try:
                return conn.execute("SELECT credential_kind, client_address, status FROM mcp_audit_log WHERE client_address = ?", (OUT_OF_RANGE,)).fetchall()
            finally:
                conn.close()

        assert [tuple(r) for r in rows()] == [("none", OUT_OF_RANGE, 403)]

    def test_forged_forwarded_for_from_a_direct_peer_is_ignored(self, monkeypatch):
        _settings(monkeypatch)
        inner, client = _guarded(address=OUT_OF_RANGE)
        response = _request(client, "POST", "/mcp", headers={"X-Forwarded-For": IN_RANGE, "X-Real-IP": IN_RANGE, "CF-Connecting-IP": IN_RANGE, "Forwarded": f"for={IN_RANGE}"})
        assert response.status_code == 403
        assert inner.calls == []

    def test_forwarded_for_counts_only_from_a_trusted_proxy_as_uvicorn_resolves_it(self, monkeypatch):
        _settings(monkeypatch)
        from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware
        from mcp_server import PublicHostGuard
        proxy = "192.168.1.72"

        def post(peer, forwarded):
            inner = _Reached()
            app = ProxyHeadersMiddleware(PublicHostGuard(inner, public_host=PUBLIC_HOST), trusted_hosts=proxy)
            client = TestClient(app, base_url=f"https://{PUBLIC_HOST}", raise_server_exceptions=False, client=(peer, 50000))
            return client.post("/mcp", headers={"X-Forwarded-For": forwarded}).status_code

        assert post(proxy, IN_RANGE) == 200
        assert post(proxy, OUT_OF_RANGE) == 403
        assert post(proxy, f"{IN_RANGE}, {OUT_OF_RANGE}") == 403
        assert post(OUT_OF_RANGE, IN_RANGE) == 403
        assert post("10.9.9.9", IN_RANGE) == 403


class TestRealApp:
    @staticmethod
    def _real_stack(monkeypatch, host):
        import main
        from mcp_server import PublicHostGuard
        _settings(monkeypatch)
        return TestClient(
            PublicHostGuard(main.app, public_host=host), base_url=f"https://{host}",
            raise_server_exceptions=False, client=(IN_RANGE, 50000), follow_redirects=False,
        )

    @pytest.fixture
    def guarded_app(self, monkeypatch):
        return self._real_stack(monkeypatch, PUBLIC_HOST)

    @pytest.fixture
    def trusted_host_app(self, monkeypatch):
        """The test app's own public host is empty, so its TrustedHostMiddleware would answer 400 for mcp.awroblew.biz; localhost is trusted, so this guard reaches the real auth_middleware."""
        return self._real_stack(monkeypatch, "localhost")

    @staticmethod
    def _registered_pairs():
        import main
        pairs = set()
        for top in main.app.routes:
            for route in (top.effective_route_contexts() if hasattr(top, "effective_route_contexts") else [top]):
                path = re.sub(r"\{[^}]+\}", "x", route.path)
                if hasattr(route, "routes"):
                    path += "/x"
                for method in set(getattr(route, "methods", None) or ()) | set(ALL_METHODS):
                    pairs.add((method, path))
        return pairs

    def test_the_guard_is_the_outermost_middleware_and_carries_the_configured_host(self):
        import main
        from mcp_server import PublicHostGuard
        outermost = main.app.user_middleware[0]
        assert outermost.cls is PublicHostGuard
        assert outermost.kwargs == {"public_host": config.MCP_PUBLIC_HOST}

    def test_the_public_host_is_a_trusted_host_when_configured(self):
        import main
        expected = {*config.ALLOWED_HOSTS, "localhost", "127.0.0.1", *([config.MCP_PUBLIC_HOST] if config.MCP_PUBLIC_HOST else [])}
        assert set(main._TRUSTED_HOSTS) == expected
        assert "" not in main._TRUSTED_HOSTS

    def test_every_registered_route_except_the_eight_is_404_on_the_public_host(self, guarded_app):
        pairs = self._registered_pairs()
        assert len(pairs) > 2000 and ("GET", "/login") in pairs and ("POST", "/api/settings") in pairs and ("POST", "/mcp") in pairs and ("GET", "/docs") in pairs
        leaked = [(m, p, r.status_code) for m, p in sorted(pairs - EIGHT) if (r := _request(guarded_app, m, p)).status_code != 404]
        assert leaked == []

    def test_all_pairs_including_the_eight_are_404_while_remote_access_is_off(self, guarded_app, monkeypatch):
        _settings(monkeypatch, enabled=False)
        leaked = [(m, p) for m, p in sorted(self._registered_pairs() | EIGHT) if _request(guarded_app, m, p).status_code != 404]
        assert leaked == []

    def test_session_cookie_api_key_and_embed_token_have_no_effect_on_the_public_host(self, guarded_app, auth_cookies, client, monkeypatch):
        monkeypatch.setenv("EMBED_TOKEN", "embed-token-for-test")
        cookie = {"Cookie": "; ".join(f"{k}={v}" for k, v in auth_cookies.items())}
        api_key = {"X-API-Key": "test-api-key-do-not-use-in-production"}
        assert client.get("/portfolio", headers={"Host": "localhost"}).status_code == 200
        assert client.get("/api/accounts/portfolio-totals", headers={"Host": "localhost", **api_key, "Cookie": ""}).status_code == 200
        assert _request(guarded_app, "GET", "/portfolio", headers=cookie).status_code == 404
        assert _request(guarded_app, "GET", "/portfolio?embed=true&embed_token=embed-token-for-test").status_code == 404
        assert _request(guarded_app, "GET", "/api/accounts/portfolio-totals", headers=api_key).status_code == 404
        assert _request(guarded_app, "GET", "/api/accounts/portfolio-totals", headers={**api_key, **cookie}).status_code == 404

    def test_credentials_other_than_a_bearer_key_do_not_authenticate_mcp_on_the_public_host(self, trusted_host_app, auth_cookies, monkeypatch):
        monkeypatch.setenv("EMBED_TOKEN", "embed-token-for-test")
        monkeypatch.setattr("mcp_server.auth.load_config", lambda: {"MCP_SERVER_ENABLED": True})
        cookie = {"Cookie": "; ".join(f"{k}={v}" for k, v in auth_cookies.items())}
        assert _request(trusted_host_app, "POST", "/mcp", headers=cookie, json={}).status_code == 401
        assert _request(trusted_host_app, "POST", "/mcp?embed=true&embed_token=embed-token-for-test", json={}).status_code == 401
        response = _request(trusted_host_app, "POST", "/mcp", headers={"X-API-Key": "test-api-key-do-not-use-in-production", **cookie}, json={})
        assert response.status_code == 403


class TestConfigKeys:
    def test_defaults(self):
        assert config.DEFAULT_CONFIG["MCP_REMOTE_ENABLED"] is False
        assert config.DEFAULT_CONFIG["MCP_PUBLIC_HOST"] == ""
        assert config.DEFAULT_CONFIG["MCP_REMOTE_ALLOWED_CIDRS"] == ["160.79.104.0/21"]

    @pytest.mark.parametrize("value,expected", [
        ("", ""), ("  ", ""), ("mcp.awroblew.biz", "mcp.awroblew.biz"), (" MCP.Awroblew.biz. ", "mcp.awroblew.biz"), ("a-b.example", "a-b.example"), ("localhost", "localhost"),
    ])
    def test_public_host_is_normalised(self, value, expected):
        assert config.validate_mcp_public_host(value) == expected

    @pytest.mark.parametrize("value", [
        "https://mcp.awroblew.biz", "mcp.awroblew.biz:443", "mcp.awroblew.biz/x", "*.awroblew.biz", "a b.example", "-a.example", "a-.example",
        "a..example", ".example", "mcp_host.example", "x" * 254, "mcp.awroblew\n.biz", 5, None,
    ])
    def test_public_host_rejects_anything_but_a_plain_host_name(self, value):
        with pytest.raises(ValueError):
            config.validate_mcp_public_host(value)

    def test_cidrs_are_canonicalised(self):
        assert config.validate_mcp_remote_cidrs([" 160.79.104.0/21 ", "2001:DB8::/32", "10.0.0.5"]) == ["160.79.104.0/21", "2001:db8::/32", "10.0.0.5/32"]

    @pytest.mark.parametrize("value", [[], None, "160.79.104.0/21", ["160.79.104.5/21"], ["garbage"], ["160.79.104.0/33"], [""], [5], ["x"] * 17, ["10.0.0.0/8"] * 16 + ["bad"]])
    def test_cidrs_reject_bad_lists(self, value):
        with pytest.raises(ValueError):
            config.validate_mcp_remote_cidrs(value)

    def test_the_settings_schema_accepts_and_normalises_the_keys(self):
        from api_routes_system import SettingsConfig
        model = SettingsConfig(MCP_REMOTE_ENABLED=True, MCP_PUBLIC_HOST="MCP.Awroblew.biz", MCP_REMOTE_ALLOWED_CIDRS=["160.79.104.0/21"])
        assert model.model_dump(exclude_none=True) == {
            "MCP_REMOTE_ENABLED": True, "MCP_PUBLIC_HOST": "mcp.awroblew.biz", "MCP_REMOTE_ALLOWED_CIDRS": ["160.79.104.0/21"],
        }
        assert SettingsConfig(MCP_PUBLIC_HOST="").MCP_PUBLIC_HOST == ""

    def test_the_settings_route_rejects_bad_values(self, client, step_up_headers):
        for body in (
            {"MCP_PUBLIC_HOST": "https://mcp.awroblew.biz"}, {"MCP_PUBLIC_HOST": "a b"}, {"MCP_PUBLIC_HOST": None},
            {"MCP_REMOTE_ALLOWED_CIDRS": []}, {"MCP_REMOTE_ALLOWED_CIDRS": ["nope"]}, {"MCP_REMOTE_ALLOWED_CIDRS": None},
            {"MCP_REMOTE_ENABLED": "maybe"},
        ):
            assert client.post("/api/settings", json=body, headers=step_up_headers).status_code == 422, body
