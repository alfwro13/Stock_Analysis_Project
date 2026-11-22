"""MCP Write Access in Settings (MCP-20): the global switch, the per-key and per-app Changes flags, their step-up rules and the schema migration."""
import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest

from db import mcp_keys as db_mcp_keys
from db.database import get_connection
from mcp_server import keys

from .helpers import MCP_KEY
from .test_oauth_server import remote, sql, tokens  # noqa: F401  remote is a fixture
from .test_remote_access import _plain, _status

ROOT = Path(__file__).resolve().parents[2]

pytestmark = pytest.mark.restore_tables("mcp_api_keys")


def _key_id():
    return keys.match(MCP_KEY)


class TestGlobalSwitch:
    def test_enabling_needs_step_up_and_writes_only_its_own_key(self, client, auth_cookies, step_up_headers):
        with patch("api.routes_auth.update_config_atomic") as update:
            refused = client.post("/api/mcp-writes/enabled", json={"enabled": True}, headers=_plain(auth_cookies))
            assert refused.status_code == 403 and refused.headers["X-Step-Up-Required"] == "1"
            update.assert_not_called()
            allowed = client.post("/api/mcp-writes/enabled", json={"enabled": True}, headers=step_up_headers)
        assert allowed.status_code == 200 and allowed.json() == {"status": "success", "enabled": True}
        update.assert_called_once_with({"MCP_WRITES_ENABLED": True})

    def test_disabling_needs_no_step_up(self, client, auth_cookies):
        with patch("api.routes_auth.update_config_atomic") as update:
            response = client.post("/api/mcp-writes/enabled", json={"enabled": False}, headers=_plain(auth_cookies))
        assert response.status_code == 200
        update.assert_called_once_with({"MCP_WRITES_ENABLED": False})

    def test_it_needs_a_session_and_neither_key_reaches_it(self, raw_client):
        assert raw_client.post("/api/mcp-writes/enabled", json={"enabled": False}).status_code in (302, 401, 403)
        assert raw_client.post("/api/mcp-writes/enabled", json={"enabled": False}, headers={"X-API-Key": os.environ["API_KEY"]}).status_code == 403
        assert raw_client.post("/api/mcp-writes/enabled", json={"enabled": False}, headers={"Authorization": f"Bearer {MCP_KEY}"}).status_code in (302, 401, 403)

    def test_a_non_boolean_is_refused(self, client, step_up_headers):
        assert client.post("/api/mcp-writes/enabled", json={"enabled": "maybe"}, headers=step_up_headers).status_code == 422


class TestKeyFlag:
    def test_a_key_starts_without_the_flag_and_the_list_shows_it(self, client):
        created = keys.create_key("Flag test")
        listed = {k["name"]: k for k in client.get("/api/mcp-keys").json()["keys"]}
        assert listed["Flag test"]["write_allowed"] is False
        assert client.get("/api/mcp-keys").json()["writes_enabled"] in (True, False)
        assert created["id"] == listed["Flag test"]["id"]

    def test_turning_it_on_needs_step_up_and_turning_it_off_does_not(self, client, auth_cookies, step_up_headers):
        key_id = _key_id()
        url = f"/api/mcp-keys/{key_id}/writes"
        refused = client.post(url, json={"enabled": True}, headers=_plain(auth_cookies))
        assert refused.status_code == 403 and refused.headers["X-Step-Up-Required"] == "1"
        assert keys.write_allowed(key_id) is False
        assert client.post(url, json={"enabled": True}, headers=step_up_headers).status_code == 200
        assert keys.write_allowed(key_id) is True
        assert client.post(url, json={"enabled": False}, headers=_plain(auth_cookies)).status_code == 200
        assert keys.write_allowed(key_id) is False

    def test_an_unknown_key_is_404(self, client, step_up_headers):
        assert client.post("/api/mcp-keys/999999/writes", json={"enabled": True}, headers=step_up_headers).status_code == 404

    def test_a_flag_change_is_an_audited_dashboard_action(self, client, step_up_headers):
        client.post(f"/api/mcp-keys/{_key_id()}/writes", json={"enabled": True}, headers=step_up_headers)
        rows = sql("SELECT credential_kind, mcp_method, mcp_name FROM mcp_audit_log WHERE mcp_method = 'dashboard'")
        assert rows == [{"credential_kind": "session", "mcp_method": "dashboard", "mcp_name": f"writes-on: key #{_key_id()}"}]

    def test_a_deleted_and_recreated_key_does_not_inherit_the_flag(self):
        created = keys.create_key("Inherit test")
        db_mcp_keys.set_key_write_allowed(created["id"], True)
        keys.delete_key(created["id"])
        again = keys.create_key("Inherit test")
        assert keys.write_allowed(again["id"]) is False


class TestGrantFlag:
    def _grant_id(self, remote):
        tokens(remote)
        return sql("SELECT id FROM mcp_oauth_grants")[0]["id"]

    def test_the_panel_shows_the_flag_and_the_global_switch(self, client, remote):
        self._grant_id(remote)
        status = _status(client)
        assert status["apps"][0]["write_allowed"] is False and status["writes_enabled"] in (True, False)

    def test_turning_it_on_needs_step_up_and_turning_it_off_does_not(self, client, auth_cookies, step_up_headers, remote):
        grant_id = self._grant_id(remote)
        url = f"/api/mcp-remote/grants/{grant_id}/writes"
        refused = client.post(url, json={"enabled": True}, headers=_plain(auth_cookies))
        assert refused.status_code == 403
        assert sql("SELECT write_allowed FROM mcp_oauth_grants")[0]["write_allowed"] == 0
        assert client.post(url, json={"enabled": True}, headers=step_up_headers).status_code == 200
        assert _status(client)["apps"][0]["write_allowed"] is True
        assert client.post(url, json={"enabled": False}, headers=_plain(auth_cookies)).status_code == 200
        assert _status(client)["apps"][0]["write_allowed"] is False

    def test_the_change_is_audited_against_the_grant(self, client, step_up_headers, remote):
        grant_id = self._grant_id(remote)
        client.post(f"/api/mcp-remote/grants/{grant_id}/writes", json={"enabled": True}, headers=step_up_headers)
        row = sql("SELECT credential_kind, key_id, mcp_name FROM mcp_audit_log WHERE mcp_method = 'dashboard'")[0]
        assert row == {"credential_kind": "session", "key_id": grant_id, "mcp_name": "writes-on"}

    def test_a_revoked_or_unknown_grant_is_404(self, client, auth_cookies, step_up_headers, remote):
        grant_id = self._grant_id(remote)
        client.post(f"/api/mcp-remote/grants/{grant_id}/revoke", headers=_plain(auth_cookies))
        assert client.post(f"/api/mcp-remote/grants/{grant_id}/writes", json={"enabled": True}, headers=step_up_headers).status_code == 404
        assert client.post("/api/mcp-remote/grants/999999/writes", json={"enabled": True}, headers=step_up_headers).status_code == 404

    @pytest.mark.parametrize("grant_id", ["0", "-1", "abc", str(2**70)])
    def test_a_grant_id_has_a_fixed_shape(self, client, step_up_headers, grant_id):
        assert client.post(f"/api/mcp-remote/grants/{grant_id}/writes", json={"enabled": True}, headers=step_up_headers).status_code == 422


class TestSchemaAndPage:
    def test_the_migration_adds_the_column_with_the_safe_default(self):
        from db import schema
        conn = get_connection()
        try:
            conn.execute("INSERT INTO mcp_api_keys (name, key_hash, created_at) VALUES ('Migration key', 'migration-hash', '2026-11-01 00:00:00')")
            conn.execute("ALTER TABLE mcp_api_keys DROP COLUMN write_allowed")
            conn.commit()
            assert "write_allowed" not in [r[1] for r in conn.execute("PRAGMA table_info(mcp_api_keys)")]
            schema.migrate_db(conn, conn.cursor())
            conn.commit()
            assert conn.execute("SELECT write_allowed FROM mcp_api_keys WHERE name = 'Migration key'").fetchone()[0] == 0
        finally:
            conn.close()

    def test_the_card_has_the_switch_and_both_columns(self, client):
        html = client.get("/settings").text
        for element in ('id="mcp-write-panel"', 'id="MCP_WRITES_ENABLED"', "toggleMcpWrites(this)", "MCP Write Access"):
            assert element in html
        assert html.count("<abbr title=\"Lets this") == 2

    def test_the_scripts_post_to_the_three_routes_and_the_remote_one_never_parses_markup(self):
        account = (ROOT / "static/js/settings_account.js").read_text(encoding="utf-8")
        remote_js = (ROOT / "static/js/settings_mcp_remote.js").read_text(encoding="utf-8")
        assert "/api/mcp-writes/enabled" in account and "'/writes'" in account
        assert "'/writes'" in remote_js
        assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|eval\(|document\.write", remote_js)
