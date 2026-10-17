"""Bearer MCP key check on /mcp: key matrix, scoping away from the other credentials, logging."""
import logging
import os

import pytest

from .helpers import MCP_KEY, RPC_HEADERS, rpc


class TestAuth:
    def test_right_key_is_accepted(self, mcp_client):
        assert rpc(mcp_client, "tools/list").status_code == 200

    def test_scheme_is_case_insensitive(self, mcp_client):
        response = mcp_client.post(
            "/mcp", headers={**RPC_HEADERS, "Authorization": f"bearer {MCP_KEY}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert response.status_code == 200

    def test_missing_key_is_401_with_bearer_challenge(self, mcp_client):
        response = rpc(mcp_client, "tools/list", key=None)
        assert response.status_code == 401
        assert response.headers["www-authenticate"] == "Bearer"

    @pytest.mark.parametrize("key", ["wrong-key", "", MCP_KEY + "x", MCP_KEY[:-1], os.environ["API_KEY"]])
    def test_wrong_key_is_401(self, mcp_client, key):
        assert rpc(mcp_client, "tools/list", key=key).status_code == 401

    def test_other_scheme_is_401(self, mcp_client):
        response = mcp_client.post(
            "/mcp", headers={**RPC_HEADERS, "Authorization": f"Basic {MCP_KEY}"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )
        assert response.status_code == 401

    @pytest.mark.parametrize("presented", [MCP_KEY, "", "anything"])
    def test_no_keys_fails_closed(self, mcp_client, no_mcp_keys, presented):
        assert rpc(mcp_client, "tools/list", key=presented).status_code == 401

    def test_empty_key_fails_closed(self, mcp_client):
        assert rpc(mcp_client, "tools/list", key="").status_code == 401

    def test_env_variable_no_longer_authenticates(self, mcp_client, no_mcp_keys, monkeypatch):
        monkeypatch.setenv("MCP_API_KEY", "legacy-env-key")
        assert rpc(mcp_client, "tools/list", key="legacy-env-key").status_code == 401

    def test_home_assistant_key_is_403(self, mcp_client):
        response = rpc(mcp_client, "tools/list", key=None, headers={"X-API-Key": os.environ["API_KEY"]})
        assert response.status_code == 403

    def test_home_assistant_key_next_to_a_valid_bearer_is_403(self, mcp_client):
        response = rpc(mcp_client, "tools/list", headers={"X-API-Key": os.environ["API_KEY"]})
        assert response.status_code == 403

    def test_mcp_key_in_the_ha_header_is_403(self, mcp_client):
        assert rpc(mcp_client, "tools/list", key=None, headers={"X-API-Key": MCP_KEY}).status_code == 403

    def test_session_cookie_does_not_authenticate_mcp(self, client):
        assert rpc(client, "tools/list", key=None).status_code == 401

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
        rpc(mcp_client, "tools/list")
        rpc(mcp_client, "tools/list", key="wrong-key-value-123")
        rpc(mcp_client, "tools/list", key=None, headers={"X-API-Key": os.environ["API_KEY"]})
        assert MCP_KEY not in caplog.text
        assert "wrong-key-value-123" not in caplog.text
        assert os.environ["API_KEY"] not in caplog.text

    def test_refusing_because_no_key_exists_is_logged_without_a_secret(self, mcp_client, no_mcp_keys, caplog):
        with caplog.at_level(logging.WARNING, logger="mcp_server"):
            rpc(mcp_client, "tools/list", key="whatever-secret-attempt")
        assert "no MCP key exists" in caplog.text
        assert "whatever-secret-attempt" not in caplog.text
