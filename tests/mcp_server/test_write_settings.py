"""MCP Write Access in Settings (MCP-20): the global switch, the per-key and per-app Changes flags, their step-up rules and the schema migration."""
import json
import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest

import config
from constants import MCP_WRITE_LIMIT_BOUNDS, MCP_WRITE_LIMIT_DEFAULTS
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


LIMITS_URL = "/api/mcp-writes/limits"


def _limits(**changes):
    return {**MCP_WRITE_LIMIT_DEFAULTS, **changes}


@pytest.fixture
def saved_limits(monkeypatch):
    """The currently saved MCP_WRITE_LIMITS the route compares against; the route's config write is replaced by a recorder."""
    current = dict(MCP_WRITE_LIMIT_DEFAULTS)
    monkeypatch.setattr("api.routes_auth.load_config", lambda: {"MCP_WRITE_LIMITS": dict(current)})
    with patch("api.routes_auth.update_config_atomic") as update:
        update.current = current
        yield update


class TestWriteLimitsRoute:
    @pytest.mark.parametrize("key", list(MCP_WRITE_LIMIT_DEFAULTS))
    def test_raising_a_limit_needs_step_up_and_saves_nothing_without_it(self, client, auth_cookies, step_up_headers, saved_limits, key):
        raised = _limits(**{key: MCP_WRITE_LIMIT_DEFAULTS[key] + 1})
        refused = client.post(LIMITS_URL, json=raised, headers=_plain(auth_cookies))
        assert refused.status_code == 403 and refused.headers["X-Step-Up-Required"] == "1"
        saved_limits.assert_not_called()
        allowed = client.post(LIMITS_URL, json=raised, headers=step_up_headers)
        assert allowed.status_code == 200 and allowed.json() == {"status": "success", "limits": raised}
        saved_limits.assert_called_once_with({"MCP_WRITE_LIMITS": raised})

    def test_lowering_needs_no_step_up(self, client, auth_cookies, saved_limits):
        lowered = _limits(proposals_per_hour=5, pending_max=0, adds_per_day=3, proposal_seconds=60)
        response = client.post(LIMITS_URL, json=lowered, headers=_plain(auth_cookies))
        assert response.status_code == 200 and response.json()["limits"] == lowered
        saved_limits.assert_called_once_with({"MCP_WRITE_LIMITS": lowered})

    def test_unchanged_needs_no_step_up(self, client, auth_cookies, saved_limits):
        assert client.post(LIMITS_URL, json=_limits(), headers=_plain(auth_cookies)).status_code == 200
        saved_limits.assert_called_once_with({"MCP_WRITE_LIMITS": _limits()})

    def test_one_raised_value_among_lowered_ones_still_needs_step_up(self, client, auth_cookies, saved_limits):
        mixed = _limits(proposals_per_hour=1, proposal_seconds=MCP_WRITE_LIMIT_DEFAULTS["proposal_seconds"] + 60)
        assert client.post(LIMITS_URL, json=mixed, headers=_plain(auth_cookies)).status_code == 403
        saved_limits.assert_not_called()

    @pytest.mark.parametrize("key", list(MCP_WRITE_LIMIT_DEFAULTS))
    def test_a_value_outside_its_range_is_refused_with_nothing_saved(self, client, step_up_headers, saved_limits, key):
        low, high = MCP_WRITE_LIMIT_BOUNDS[key]
        for value in (low - 1, high + 1):
            response = client.post(LIMITS_URL, json=_limits(**{key: value}), headers=step_up_headers)
            assert response.status_code == 400 and key in response.json()["detail"]
        saved_limits.assert_not_called()

    def test_adds_above_changes_is_refused(self, client, step_up_headers, saved_limits):
        response = client.post(LIMITS_URL, json=_limits(changes_per_day=5, adds_per_day=6), headers=step_up_headers)
        assert response.status_code == 400 and "adds_per_day" in response.json()["detail"]
        saved_limits.assert_not_called()

    @pytest.mark.parametrize("value", [5.5, None, [1], "many", True, "20"])
    def test_a_non_integer_is_refused_with_nothing_saved(self, client, step_up_headers, saved_limits, value):
        response = client.post(LIMITS_URL, json=_limits(pending_max=value), headers=step_up_headers)
        assert response.status_code in (400, 422)
        saved_limits.assert_not_called()

    @pytest.mark.parametrize("key", list(MCP_WRITE_LIMIT_DEFAULTS))
    def test_a_missing_field_is_refused(self, client, step_up_headers, saved_limits, key):
        body = _limits()
        del body[key]
        assert client.post(LIMITS_URL, json=body, headers=step_up_headers).status_code in (400, 422)
        saved_limits.assert_not_called()

    def test_an_extra_field_cannot_be_saved(self, client, step_up_headers, saved_limits):
        client.post(LIMITS_URL, json={**_limits(), "evil": 1}, headers=step_up_headers)
        for call in saved_limits.call_args_list:
            assert set(call.args[0]["MCP_WRITE_LIMITS"]) == set(MCP_WRITE_LIMIT_DEFAULTS)

    def test_it_needs_a_session_and_neither_key_reaches_it(self, raw_client, saved_limits):
        assert raw_client.post(LIMITS_URL, json=_limits()).status_code in (302, 401, 403)
        assert raw_client.post(LIMITS_URL, json=_limits(), headers={"X-API-Key": os.environ["API_KEY"]}).status_code == 403
        assert raw_client.post(LIMITS_URL, json=_limits(), headers={"Authorization": f"Bearer {MCP_KEY}"}).status_code in (302, 401, 403)
        saved_limits.assert_not_called()

    def test_a_change_is_an_audited_dashboard_action(self, client, auth_cookies, saved_limits):
        client.post(LIMITS_URL, json=_limits(adds_per_day=1), headers=_plain(auth_cookies))
        rows = sql("SELECT credential_kind, mcp_method, mcp_name FROM mcp_audit_log WHERE mcp_method = 'dashboard'")
        assert rows == [{"credential_kind": "session", "mcp_method": "dashboard", "mcp_name": "write-limits changed"}]

    def test_a_refused_change_writes_no_audit_row(self, client, step_up_headers, saved_limits):
        client.post(LIMITS_URL, json=_limits(pending_max=99), headers=step_up_headers)
        assert sql("SELECT 1 FROM mcp_audit_log WHERE mcp_method = 'dashboard'") == []


class TestWriteLimitsConfig:
    @pytest.fixture
    def stored(self, monkeypatch, tmp_path):
        path = tmp_path / "config.json"
        monkeypatch.setattr(config, "SECRETS_PATH", path)
        config._clear_config_cache()

        def write(value):
            path.write_text(json.dumps({"MCP_WRITE_LIMITS": value}))
            config._clear_config_cache()

        yield write
        config._clear_config_cache()

    def test_defaults_are_valid_and_in_the_default_config(self):
        assert config.validate_mcp_write_limits(MCP_WRITE_LIMIT_DEFAULTS) == MCP_WRITE_LIMIT_DEFAULTS
        assert config.DEFAULT_CONFIG["MCP_WRITE_LIMITS"] == MCP_WRITE_LIMIT_DEFAULTS
        assert config.DEFAULT_CONFIG["MCP_WRITE_LIMITS"] is not MCP_WRITE_LIMIT_DEFAULTS

    def test_the_bounds_edges_are_accepted(self):
        low = {key: bounds[0] for key, bounds in MCP_WRITE_LIMIT_BOUNDS.items()}
        high = {key: bounds[1] for key, bounds in MCP_WRITE_LIMIT_BOUNDS.items()}
        high["adds_per_day"] = 50
        assert config.validate_mcp_write_limits(low) == low
        assert config.validate_mcp_write_limits(high) == high

    @pytest.mark.parametrize("values", [
        None, [], {}, {**MCP_WRITE_LIMIT_DEFAULTS, "extra": 1}, {k: v for k, v in MCP_WRITE_LIMIT_DEFAULTS.items() if k != "pending_max"},
        _limits(pending_max=True), _limits(pending_max="5"), _limits(pending_max=5.0), _limits(pending_max=-1), _limits(pending_max=21),
        _limits(proposal_seconds=0), _limits(proposal_seconds=59), _limits(proposal_seconds=3601), _limits(proposals_per_hour=101),
        _limits(changes_per_day=101), _limits(adds_per_day=51), _limits(changes_per_day=4, adds_per_day=5),
    ])
    def test_invalid_limits_are_rejected(self, values):
        with pytest.raises(ValueError):
            config.validate_mcp_write_limits(values)

    def test_zero_is_valid_for_the_four_caps_only(self):
        zeros = _limits(proposals_per_hour=0, pending_max=0, changes_per_day=0, adds_per_day=0)
        assert config.validate_mcp_write_limits(zeros) == zeros

    def test_a_valid_stored_value_is_loaded(self, stored):
        wanted = _limits(proposals_per_hour=3, adds_per_day=2)
        stored(wanted)
        assert config.load_config()["MCP_WRITE_LIMITS"] == wanted

    def test_a_file_without_the_key_gets_the_defaults(self, stored):
        stored(None)
        (config.SECRETS_PATH).write_text(json.dumps({"HOME_EXCHANGE": "NYSE"}))
        config._clear_config_cache()
        assert config.load_config()["MCP_WRITE_LIMITS"] == MCP_WRITE_LIMIT_DEFAULTS

    @pytest.mark.parametrize("bad", [_limits(pending_max=999), _limits(changes_per_day=1, adds_per_day=2), {"pending_max": 1}, "many", _limits(adds_per_day=True)])
    def test_an_invalid_stored_value_falls_back_to_the_defaults_with_a_warning(self, stored, capsys, bad):
        stored(bad)
        assert config.load_config()["MCP_WRITE_LIMITS"] == MCP_WRITE_LIMIT_DEFAULTS
        assert "Invalid MCP_WRITE_LIMITS" in capsys.readouterr().out


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

    def test_the_card_renders_the_five_limit_inputs_with_saved_values_and_bounds(self, client):
        saved = _limits(proposals_per_hour=7, pending_max=0, changes_per_day=33, adds_per_day=4, proposal_seconds=900)
        with patch("pages.page_routes.load_config", lambda: {**config.load_config(), "MCP_WRITE_LIMITS": saved}):
            html = client.get("/settings").text
        for key, (low, high) in MCP_WRITE_LIMIT_BOUNDS.items():
            tag = re.search(rf'<input[^>]*id="mcp_write_limit_{key}"[^>]*>', html)
            assert tag, key
            for attribute in (f'data-limit-key="{key}"', f'min="{low}"', f'max="{high}"', f'value="{saved[key]}"'):
                assert attribute in tag.group(0)
        assert "saveMcpWriteLimits()" in html and 'id="mcp-write-limits-msg"' in html

    def test_the_scripts_post_to_the_three_routes_and_the_remote_one_never_parses_markup(self):
        account = (ROOT / "static/js/settings_account.js").read_text(encoding="utf-8")
        remote_js = (ROOT / "static/js/settings_mcp_remote.js").read_text(encoding="utf-8")
        assert "/api/mcp-writes/enabled" in account and "'/writes'" in account and "/api/mcp-writes/limits" in account
        assert "'/writes'" in remote_js
        assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|eval\(|document\.write", remote_js)
