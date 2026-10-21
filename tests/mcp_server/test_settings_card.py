"""Settings card for the MCP Server: enable toggle and named keys (create, list, delete)."""
import json
import os
from unittest.mock import patch

import pytest

import config
import db_mcp_keys
from database import get_connection
from .helpers import MCP_KEY, rpc


class _LazyKeys:
    """Imports mcp_server.keys on first use: importing the package at collection time sets the root log level before test_04's caplog check runs."""

    def __getattr__(self, name):
        import importlib
        return getattr(importlib.import_module("mcp_server.keys"), name)


keys = _LazyKeys()


@pytest.fixture
def real_config(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SECRETS_PATH", tmp_path / "config.json")
    monkeypatch.setattr("mcp_server.auth.load_config", config.load_config)
    config._clear_config_cache()
    yield
    config._clear_config_cache()


def _set_enabled(client, headers, enabled):
    return client.post("/api/mcp-lan/enabled", json={"enabled": enabled}, headers=headers)


class TestEnableToggle:
    def test_disabled_by_default_and_the_old_master_key_is_gone(self):
        assert config.DEFAULT_CONFIG["MCP_LAN_ENABLED"] is False
        assert "MCP_SERVER_ENABLED" not in config.DEFAULT_CONFIG

    def test_the_key_is_not_in_the_settings_schema(self):
        from api_routes_system import SettingsConfig
        assert "MCP_LAN_ENABLED" not in SettingsConfig.model_fields

    def test_disabled_lan_access_refuses_even_the_right_key(self, mcp_client, monkeypatch):
        monkeypatch.setattr("mcp_server.auth.load_config", lambda: {"MCP_LAN_ENABLED": False})
        response = rpc(mcp_client, "tools/list")
        assert response.status_code == 503
        assert response.json() == {"detail": "MCP Local/LAN Access is disabled"}
        assert "www-authenticate" not in response.headers

    def test_disabled_lan_access_answers_the_same_to_a_wrong_key(self, mcp_client, monkeypatch):
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

    def test_enabling_with_step_up_writes_only_the_lan_key(self, client, step_up_headers):
        with patch("api_routes_auth.update_config_atomic") as update:
            response = _set_enabled(client, step_up_headers, True)
        assert response.status_code == 200 and response.json() == {"status": "success", "enabled": True}
        update.assert_called_once_with({"MCP_LAN_ENABLED": True})

    def test_disabling_needs_no_step_up(self, client, auth_cookies):
        headers = {"Cookie": "; ".join(f"{k}={v}" for k, v in auth_cookies.items()), "X-CSRFToken": auth_cookies["csrftoken"]}
        with patch("api_routes_auth.update_config_atomic") as update:
            response = _set_enabled(client, headers, False)
        assert response.status_code == 200
        update.assert_called_once_with({"MCP_LAN_ENABLED": False})

    def test_toggle_needs_a_session(self, raw_client):
        assert raw_client.post("/api/mcp-lan/enabled", json={"enabled": False}).status_code in (302, 401, 403)

    def test_api_key_and_mcp_key_cannot_reach_the_toggle(self, raw_client):
        assert raw_client.post("/api/mcp-lan/enabled", json={"enabled": False}, headers={"X-API-Key": os.environ["API_KEY"]}).status_code == 403
        assert raw_client.post("/api/mcp-lan/enabled", json={"enabled": False}, headers={"Authorization": f"Bearer {MCP_KEY}"}).status_code in (302, 401, 403)

    def test_the_old_route_is_gone(self, client, step_up_headers):
        assert client.post("/api/mcp-server/enabled", json={"enabled": True}, headers=step_up_headers).status_code in (404, 405)

    def test_rejects_a_non_boolean(self, client, step_up_headers):
        assert client.post("/api/mcp-lan/enabled", json={"enabled": "maybe"}, headers=step_up_headers).status_code == 422

    def test_toggle_opens_and_closes_mcp(self, client, mcp_client, step_up_headers, auth_cookies, real_config):
        assert rpc(mcp_client, "tools/list").status_code == 503
        assert _set_enabled(client, step_up_headers, True).status_code == 200
        assert rpc(mcp_client, "tools/list").status_code == 200
        plain = {"Cookie": "; ".join(f"{k}={v}" for k, v in auth_cookies.items()), "X-CSRFToken": auth_cookies["csrftoken"]}
        assert _set_enabled(client, plain, False).status_code == 200
        assert rpc(mcp_client, "tools/list").status_code == 503

    def test_the_lan_switch_leaves_the_remote_switch_alone(self, client, step_up_headers, real_config):
        config.update_config_atomic({"MCP_REMOTE_ENABLED": True})
        assert _set_enabled(client, step_up_headers, True).status_code == 200
        assert config.load_config()["MCP_REMOTE_ENABLED"] is True
        assert _set_enabled(client, step_up_headers, False).status_code == 200
        assert config.load_config()["MCP_REMOTE_ENABLED"] is True


class TestLegacyMasterSwitch:
    @staticmethod
    def _stored(tmp_path, **values):
        (tmp_path / "config.json").write_text(json.dumps(values))
        config._clear_config_cache()
        return config.load_config()

    def test_an_enabled_master_becomes_lan_access_and_leaves_remote_as_stored(self, real_config, tmp_path):
        loaded = self._stored(tmp_path, MCP_SERVER_ENABLED=True, MCP_REMOTE_ENABLED=True)
        assert (loaded["MCP_LAN_ENABLED"], loaded["MCP_REMOTE_ENABLED"]) == (True, True)
        assert "MCP_SERVER_ENABLED" not in loaded

    def test_a_disabled_master_keeps_both_paths_closed(self, real_config, tmp_path):
        loaded = self._stored(tmp_path, MCP_SERVER_ENABLED=False, MCP_REMOTE_ENABLED=True)
        assert (loaded["MCP_LAN_ENABLED"], loaded["MCP_REMOTE_ENABLED"]) == (False, False)
        assert "MCP_SERVER_ENABLED" not in loaded

    def test_a_stored_lan_value_wins_over_the_master(self, real_config, tmp_path):
        loaded = self._stored(tmp_path, MCP_SERVER_ENABLED=False, MCP_LAN_ENABLED=True)
        assert loaded["MCP_LAN_ENABLED"] is True

    def test_without_the_old_key_nothing_changes(self, real_config, tmp_path):
        loaded = self._stored(tmp_path, MCP_REMOTE_ENABLED=True)
        assert (loaded["MCP_LAN_ENABLED"], loaded["MCP_REMOTE_ENABLED"]) == (False, True)

    def test_the_next_save_writes_the_migrated_values_and_drops_the_old_key(self, real_config, tmp_path):
        self._stored(tmp_path, MCP_SERVER_ENABLED=True, MCP_REMOTE_ENABLED=True)
        config.update_config_atomic({"BASE_CURRENCY": "GBP"})
        stored = json.loads((tmp_path / "config.json").read_text())
        assert "MCP_SERVER_ENABLED" not in stored
        assert (stored["MCP_LAN_ENABLED"], stored["MCP_REMOTE_ENABLED"]) == (True, True)


def _plain_headers(auth_cookies):
    return {"Cookie": "; ".join(f"{k}={v}" for k, v in auth_cookies.items()), "X-CSRFToken": auth_cookies["csrftoken"]}


@pytest.fixture
def clean_keys():
    yield
    for row in db_mcp_keys.list_keys():
        if row["name"] != "Test key":
            db_mcp_keys.delete_key(row["id"])
    keys.invalidate()


class TestNamedKeys:
    def test_creating_requires_step_up(self, client, auth_cookies):
        response = client.post("/api/mcp-keys", json={"name": "dev box"}, headers=_plain_headers(auth_cookies))
        assert response.status_code == 403
        assert response.headers["X-Step-Up-Required"] == "1"
        assert [r["name"] for r in db_mcp_keys.list_keys()] == ["Test key"]

    def test_deleting_requires_step_up(self, client, auth_cookies):
        key_id = db_mcp_keys.list_keys()[0]["id"]
        assert client.delete(f"/api/mcp-keys/{key_id}", headers=_plain_headers(auth_cookies)).status_code == 403
        assert len(db_mcp_keys.list_keys()) == 1

    def test_listing_needs_a_session(self, raw_client):
        assert raw_client.get("/api/mcp-keys").status_code in (302, 401, 403)

    @pytest.mark.parametrize("method,path", [("post", "/api/mcp-keys"), ("get", "/api/mcp-keys"), ("delete", "/api/mcp-keys/1")])
    def test_api_key_and_mcp_key_cannot_reach_the_routes(self, raw_client, method, path):
        assert getattr(raw_client, method)(path, headers={"X-API-Key": os.environ["API_KEY"]}).status_code == 403
        assert getattr(raw_client, method)(path, headers={"Authorization": f"Bearer {MCP_KEY}"}).status_code == 302

    def test_create_returns_the_key_once_and_stores_only_its_hash(self, client, step_up_headers, clean_keys):
        response = client.post("/api/mcp-keys", json={"name": "  dev box 1  "}, headers=step_up_headers)
        assert response.status_code == 200
        created = response.json()
        assert created["name"] == "dev box 1" and len(created["key"]) == 64
        conn = get_connection()
        try:
            stored = conn.execute("SELECT key_hash FROM mcp_api_keys WHERE id = ?", (created["id"],)).fetchone()[0]
        finally:
            conn.close()
        assert stored == keys.hash_key(created["key"]) and stored != created["key"]
        listing = client.get("/api/mcp-keys").json()["keys"]
        assert "dev box 1" in [k["name"] for k in listing]
        assert created["key"] not in client.get("/api/mcp-keys").text

    def test_each_key_authenticates_and_deleting_one_leaves_the_others(self, client, mcp_client, step_up_headers, clean_keys):
        first = client.post("/api/mcp-keys", json={"name": "box one"}, headers=step_up_headers).json()
        second = client.post("/api/mcp-keys", json={"name": "box two"}, headers=step_up_headers).json()
        for key in (MCP_KEY, first["key"], second["key"]):
            assert rpc(mcp_client, "tools/list", key=key).status_code == 200
        assert client.delete(f"/api/mcp-keys/{first['id']}", headers=step_up_headers).status_code == 200
        assert rpc(mcp_client, "tools/list", key=first["key"]).status_code == 401
        assert rpc(mcp_client, "tools/list", key=second["key"]).status_code == 200
        assert rpc(mcp_client, "tools/list", key=MCP_KEY).status_code == 200

    def test_deleting_the_last_key_closes_the_server(self, client, mcp_client, step_up_headers):
        key_id = db_mcp_keys.list_keys()[0]["id"]
        assert client.delete(f"/api/mcp-keys/{key_id}", headers=step_up_headers).status_code == 200
        assert rpc(mcp_client, "tools/list").status_code == 401

    def test_deleting_an_unknown_key_is_404(self, client, step_up_headers):
        assert client.delete("/api/mcp-keys/999999", headers=step_up_headers).status_code == 404

    def test_duplicate_name_is_refused_ignoring_case(self, client, step_up_headers, clean_keys):
        assert client.post("/api/mcp-keys", json={"name": "Laptop"}, headers=step_up_headers).status_code == 200
        response = client.post("/api/mcp-keys", json={"name": "laptop"}, headers=step_up_headers)
        assert response.status_code == 409

    @pytest.mark.parametrize("name", ["", "   ", "x" * 61, "\u200b\u202e"])
    def test_bad_names_are_refused(self, client, step_up_headers, name):
        assert client.post("/api/mcp-keys", json={"name": name}, headers=step_up_headers).status_code in (400, 422)

    def test_key_count_is_capped(self, client, step_up_headers, clean_keys, monkeypatch):
        monkeypatch.setattr("mcp_server.keys.MCP_KEYS_MAX", 2)
        assert client.post("/api/mcp-keys", json={"name": "second"}, headers=step_up_headers).status_code == 200
        assert client.post("/api/mcp-keys", json={"name": "third"}, headers=step_up_headers).status_code == 409

    def test_last_used_is_recorded_and_throttled(self, mcp_client, monkeypatch):
        calls = []
        monkeypatch.setattr("mcp_server.keys._write_use", lambda key_id, used_at: calls.append(key_id))
        keys.invalidate()
        rpc(mcp_client, "tools/list")
        rpc(mcp_client, "tools/list")
        assert len(calls) == 1
        rpc(mcp_client, "tools/list", key="wrong")
        assert len(calls) == 1

    def test_last_used_is_stored(self, mcp_client):
        keys.invalidate()
        rpc(mcp_client, "tools/list")
        row = next(r for r in db_mcp_keys.list_keys() if r["name"] == "Test key")
        assert row["last_used_at"]
        listed = next(k for k in keys.list_keys() if k["name"] == "Test key")
        assert listed["last_used_at"] and listed["created_at"]


class TestKeysWhileLanAccessIsOff:
    @pytest.fixture(autouse=True)
    def _off(self, monkeypatch):
        monkeypatch.setattr("mcp_server.auth.load_config", lambda: {"MCP_LAN_ENABLED": False})

    def test_creating_and_deleting_are_refused_with_409(self, client, step_up_headers):
        key_id = db_mcp_keys.list_keys()[0]["id"]
        created = client.post("/api/mcp-keys", json={"name": "while off"}, headers=step_up_headers)
        deleted = client.delete(f"/api/mcp-keys/{key_id}", headers=step_up_headers)
        assert (created.status_code, deleted.status_code) == (409, 409)
        assert [r["name"] for r in db_mcp_keys.list_keys()] == ["Test key"]

    def test_the_gate_still_comes_first(self, client, auth_cookies):
        headers = _plain_headers(auth_cookies)
        assert client.post("/api/mcp-keys", json={"name": "x"}, headers=headers).status_code == 403
        assert client.delete("/api/mcp-keys/1", headers=headers).status_code == 403

    def test_listing_stays_available(self, client):
        assert [k["name"] for k in client.get("/api/mcp-keys").json()["keys"]] == ["Test key"]


class TestSettingsPage:
    def test_card_lists_no_key_values(self, client):
        html = client.get("/settings").text
        assert 'id="mcp-server-card"' in html
        assert 'id="mcp-keys-body"' in html
        assert MCP_KEY not in html
        assert keys.hash_key(MCP_KEY) not in html

    def test_remote_access_comes_before_local_lan_access(self, client):
        html = client.get("/settings").text
        for element in ('id="mcp-remote-panel"', 'id="mcp-lan-panel"', 'id="MCP_LAN_ENABLED"', 'id="mcp-key-create-btn"', "toggleMcpLan(this)"):
            assert element in html, element
        assert html.index('id="mcp-remote-panel"') < html.index('id="mcp-lan-panel"')
        assert 'id="MCP_SERVER_ENABLED"' not in html and "toggleMcpServer" not in html
