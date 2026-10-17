"""Settings card for the MCP Server: enable toggle, key generation and rotation."""
import os
from unittest.mock import patch

import pytest

import config
from .helpers import MCP_KEY, rpc


@pytest.fixture
def real_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SECRETS_PATH", tmp_path / "config.json")
    monkeypatch.setattr("mcp_server.auth.load_config", config.load_config)
    config._clear_config_cache()
    yield
    config._clear_config_cache()


def _set_enabled(client, headers, enabled):
    return client.post("/api/mcp-server/enabled", json={"enabled": enabled}, headers=headers)


class TestEnableToggle:
    def test_disabled_by_default(self):
        assert config.DEFAULT_CONFIG["MCP_SERVER_ENABLED"] is False

    def test_disabled_server_refuses_even_the_right_key(self, mcp_client, monkeypatch):
        monkeypatch.setattr("mcp_server.auth.load_config", lambda: {"MCP_SERVER_ENABLED": False})
        response = rpc(mcp_client, "tools/list")
        assert response.status_code == 503
        assert "www-authenticate" not in response.headers

    def test_disabled_server_answers_the_same_to_a_wrong_key(self, mcp_client, monkeypatch):
        monkeypatch.setattr("mcp_server.auth.load_config", lambda: {})
        assert rpc(mcp_client, "tools/list", key="wrong").status_code == 503
        assert rpc(mcp_client, "tools/list", key=None).status_code == 503

    def test_enabling_requires_step_up(self, client, auth_cookies):
        headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in auth_cookies.items()), "X-CSRFToken": auth_cookies["csrftoken"]}
        with patch("api_routes_auth.update_config_atomic") as update:
            response = _set_enabled(client, headers, True)
        assert response.status_code == 403
        assert response.headers["X-Step-Up-Required"] == "1"
        update.assert_not_called()

    def test_disabling_needs_no_step_up(self, client, auth_cookies):
        headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in auth_cookies.items()), "X-CSRFToken": auth_cookies["csrftoken"]}
        with patch("api_routes_auth.update_config_atomic") as update:
            response = _set_enabled(client, headers, False)
        assert response.status_code == 200
        update.assert_called_once_with({"MCP_SERVER_ENABLED": False})

    def test_toggle_needs_a_session(self, raw_client):
        assert raw_client.post("/api/mcp-server/enabled", json={"enabled": False}).status_code in (302, 401, 403)

    def test_rejects_a_non_boolean(self, client, step_up_headers):
        assert client.post("/api/mcp-server/enabled", json={"enabled": "maybe"}, headers=step_up_headers).status_code == 422

    def test_toggle_opens_and_closes_mcp(self, client, mcp_client, step_up_headers, auth_cookies, real_config):
        assert rpc(mcp_client, "tools/list").status_code == 503
        assert _set_enabled(client, step_up_headers, True).status_code == 200
        assert rpc(mcp_client, "tools/list").status_code == 200
        plain = {"Cookie": "; ".join(f"{k}={v}" for k, v in auth_cookies.items()), "X-CSRFToken": auth_cookies["csrftoken"]}
        assert _set_enabled(client, plain, False).status_code == 200
        assert rpc(mcp_client, "tools/list").status_code == 503


class TestGenerateKey:
    def test_requires_step_up(self, client, auth_cookies):
        headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in auth_cookies.items()), "X-CSRFToken": auth_cookies["csrftoken"]}
        with patch("dotenv.set_key") as set_key:
            response = client.post("/api/generate-mcp-key", headers=headers)
        assert response.status_code == 403
        set_key.assert_not_called()

    def test_api_key_header_cannot_reach_it(self, raw_client):
        response = raw_client.post("/api/generate-mcp-key", headers={"X-API-Key": os.environ["API_KEY"]})
        assert response.status_code == 403

    def test_mcp_key_cannot_reach_it(self, mcp_client):
        response = mcp_client.post("/api/generate-mcp-key", headers={"Authorization": f"Bearer {MCP_KEY}"})
        assert response.status_code == 302

    def test_returns_a_key_and_writes_it_to_env(self, client, step_up_headers):
        with patch("dotenv.set_key") as set_key, patch.dict(os.environ, {}, clear=False):
            response = client.post("/api/generate-mcp-key", headers=step_up_headers)
            stored = os.environ["MCP_API_KEY"]
        assert response.status_code == 200
        key = response.json()["mcp_api_key"]
        assert len(key) == 64 and key == stored
        assert set_key.call_args.args[1:] == ("MCP_API_KEY", key)

    def test_rotation_invalidates_the_old_key(self, client, mcp_client, step_up_headers):
        assert rpc(mcp_client, "tools/list").status_code == 200
        with patch("dotenv.set_key"), patch.dict(os.environ, {}, clear=False):
            new_key = client.post("/api/generate-mcp-key", headers=step_up_headers).json()["mcp_api_key"]
            assert rpc(mcp_client, "tools/list", key=MCP_KEY).status_code == 401
            assert rpc(mcp_client, "tools/list", key=new_key).status_code == 200


class TestSettingsPage:
    def test_card_reports_state_without_the_key(self, client, monkeypatch):
        monkeypatch.setenv("MCP_API_KEY", MCP_KEY)
        html = client.get("/settings").text
        assert 'id="mcp-server-card"' in html
        assert "An MCP key is set." in html
        assert MCP_KEY not in html

    def test_card_reports_a_missing_key(self, client, monkeypatch):
        monkeypatch.delenv("MCP_API_KEY", raising=False)
        html = client.get("/settings").text
        assert "No MCP key set" in html
