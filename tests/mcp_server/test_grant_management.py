"""Grant management (MCP-15e): revoke one app, revoke all remote access, the recent-activity list, the Diagnostics counts and the 503 of a disabled server."""
import json
import os
from types import SimpleNamespace

import pytest

import db_mcp_oauth
from api_deps import limiter
from constants import MCP_ACTIVITY_ROWS
from database import get_connection
from mcp_server import audit, oauth

from .helpers import MCP_KEY
from .test_oauth_server import mcp, pair, refresh, remote, sql, start, token_form, tokens  # noqa: F401  remote is a fixture
from .test_remote_access import PUBLIC, ROOT, _plain, _status


@pytest.fixture(autouse=True)
def _fresh_rate_limits():
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def connected(remote):
    body = tokens(remote)
    return SimpleNamespace(http=remote.http, remote=remote, access=body["access_token"], refresh=body["refresh_token"], grant_id=sql("SELECT id FROM mcp_oauth_grants")[0]["id"])


def revoke_url(grant_id):
    return f"/api/mcp-remote/grants/{grant_id}/revoke"


def insert_audit(logged_at, status, kind="key", method="tools/list"):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO mcp_audit_log (logged_at, credential_kind, key_id, client_address, mcp_method, status) VALUES (?, ?, NULL, '203.0.113.7', ?, ?)",
            (logged_at, kind, method, status),
        )
        conn.commit()
    finally:
        conn.close()


class TestAccessControl:
    @pytest.mark.parametrize("method,path", [
        ("post", "/api/mcp-remote/grants/1/revoke"), ("post", "/api/mcp-remote/revoke-all"), ("get", "/api/mcp-remote/activity"),
    ])
    def test_needs_a_session_and_refuses_both_keys(self, raw_client, method, path):
        assert getattr(raw_client, method)(path).status_code in (302, 401, 403)
        assert getattr(raw_client, method)(path, headers={"X-API-Key": os.environ["API_KEY"]}).status_code == 403
        assert getattr(raw_client, method)(path, headers={"Authorization": f"Bearer {MCP_KEY}"}).status_code == 302

    def test_revoking_needs_no_password_step(self, client, auth_cookies, connected):
        assert client.post(revoke_url(connected.grant_id), headers=_plain(auth_cookies)).status_code == 200
        assert client.post("/api/mcp-remote/revoke-all", headers=_plain(auth_cookies)).status_code == 200

    @pytest.mark.parametrize("grant_id", ["0", "-1", "abc", "1.5", str(2**70)])
    def test_a_grant_id_has_a_fixed_shape(self, client, auth_cookies, grant_id):
        assert client.post(revoke_url(grant_id), headers=_plain(auth_cookies)).status_code == 422


class TestRevokeOneApp:
    def test_the_apps_tokens_stop_working_at_once(self, client, auth_cookies, connected):
        assert mcp(connected.remote, connected.access).status_code == 200
        response = client.post(revoke_url(connected.grant_id), headers=_plain(auth_cookies))
        assert response.status_code == 200 and response.json() == {"status": "success"}
        assert mcp(connected.remote, connected.access).status_code == 401
        refused = refresh(connected.remote, connected.refresh)
        assert refused.status_code == 400 and refused.json()["error"] == "invalid_grant"
        assert sql("SELECT revoked_at FROM mcp_oauth_grants")[0]["revoked_at"] is not None
        assert _status(client)["apps"] == []

    def test_only_the_named_app_is_revoked(self, client, auth_cookies, connected):
        second = tokens(connected.remote)
        assert client.post(revoke_url(connected.grant_id), headers=_plain(auth_cookies)).status_code == 200
        assert mcp(connected.remote, connected.access).status_code == 401
        assert mcp(connected.remote, second["access_token"]).status_code == 200
        assert [app["id"] for app in _status(client)["apps"]] == [sql("SELECT id FROM mcp_oauth_grants WHERE revoked_at IS NULL")[0]["id"]]

    def test_an_unknown_or_already_revoked_app_is_404(self, client, auth_cookies, connected):
        assert client.post(revoke_url(connected.grant_id + 1000), headers=_plain(auth_cookies)).status_code == 404
        assert client.post(revoke_url(connected.grant_id), headers=_plain(auth_cookies)).status_code == 200
        assert client.post(revoke_url(connected.grant_id), headers=_plain(auth_cookies)).status_code == 404
        assert len(sql("SELECT id FROM mcp_audit_log WHERE mcp_method = 'dashboard'")) == 1

    def test_the_revoke_is_written_to_the_audit_log(self, client, auth_cookies, connected):
        client.post(revoke_url(connected.grant_id), headers=_plain(auth_cookies))
        row = sql("SELECT * FROM mcp_audit_log WHERE mcp_method = 'dashboard'")[0]
        assert (row["credential_kind"], row["key_id"], row["mcp_name"], row["status"]) == ("session", connected.grant_id, "revoke-grant", 200)
        assert row["client_address"]

    def test_the_list_shows_each_apps_last_use_and_address(self, client, connected):
        assert _status(client)["apps"][0]["last_used_display"] is None
        assert mcp(connected.remote, connected.access).status_code == 200
        app = _status(client)["apps"][0]
        assert app["id"] == connected.grant_id and app["last_used_display"] and app["last_address"]
        assert app["last_address"] == sql("SELECT last_address FROM mcp_oauth_grants")[0]["last_address"]

    def test_the_script_asks_before_revoking_and_renders_through_textContent(self):
        text = (ROOT / "static/js/settings_mcp_remote.js").read_text(encoding="utf-8")
        assert "confirm(" in text.split("async function revokeMcpRemoteGrant")[1].split("async function")[0]
        assert "confirm(" in text.split("async function revokeAllMcpRemoteAccess")[1].split("document.addEventListener")[0]


class TestRevokeAll:
    def test_it_signs_every_app_out_closes_the_window_and_drops_requests(self, client, auth_cookies, connected):
        second = tokens(connected.remote)
        start(connected.remote)
        assert _status(client)["pairing"]["open"] and len(_status(client)["requests"]) == 1
        response = client.post("/api/mcp-remote/revoke-all", headers=_plain(auth_cookies))
        assert response.status_code == 200 and response.json() == {"status": "success", "revoked": 2}
        for access in (connected.access, second["access_token"]):
            assert mcp(connected.remote, access).status_code == 401
        assert sql("SELECT COUNT(*) AS n FROM mcp_oauth_grants WHERE revoked_at IS NULL")[0]["n"] == 0
        assert sql("SELECT COUNT(*) AS n FROM mcp_oauth_requests")[0]["n"] == 0
        status = _status(client)
        assert status["pairing"]["open"] is False and status["requests"] == [] and status["apps"] == []

    def test_the_oauth_client_and_its_secret_stay(self, client, auth_cookies, connected):
        client.post("/api/mcp-remote/revoke-all", headers=_plain(auth_cookies))
        assert db_mcp_oauth.get_client()["client_id"] == connected.remote.client_id
        assert oauth.authenticate_client(connected.remote.client_id, connected.remote.secret)

    def test_an_approved_request_cannot_turn_into_a_grant_afterwards(self, client, auth_cookies, remote):
        code, verifier, _ = pair(remote)
        client.post("/api/mcp-remote/revoke-all", headers=_plain(auth_cookies))
        response = remote.http.post("/oauth/token", data=token_form(remote, code, verifier), auth=remote.auth)
        assert response.status_code == 400 and response.json()["error"] == "invalid_grant"
        assert sql("SELECT COUNT(*) AS n FROM mcp_oauth_grants")[0]["n"] == 0
        assert start(remote)[0].status_code == 403

    def test_with_nothing_connected_it_still_succeeds(self, client, auth_cookies, remote):
        response = client.post("/api/mcp-remote/revoke-all", headers=_plain(auth_cookies))
        assert response.status_code == 200 and response.json()["revoked"] == 0

    def test_the_revoke_is_written_to_the_audit_log(self, client, auth_cookies, connected):
        client.post("/api/mcp-remote/revoke-all", headers=_plain(auth_cookies))
        row = sql("SELECT * FROM mcp_audit_log WHERE mcp_method = 'dashboard'")[0]
        assert (row["credential_kind"], row["key_id"], row["mcp_name"]) == ("session", None, "revoke-all: 1 grants")

    def test_a_later_rotation_of_the_client_still_revokes_through_the_same_helper(self, connected):
        oauth.create_client()
        assert sql("SELECT COUNT(*) AS n FROM mcp_oauth_grants WHERE revoked_at IS NULL")[0]["n"] == 0
        assert mcp(connected.remote, connected.access).status_code == 401


class TestRecentActivity:
    def test_it_lists_the_newest_events_first_up_to_the_limit(self, client):
        for i in range(MCP_ACTIVITY_ROWS + 10):
            audit.record(None, "203.0.113.7", "tools/call", f"t{i}", 200, 1, 10, grant_id=None)
        events = client.get("/api/mcp-remote/activity").json()["events"]
        assert len(events) == MCP_ACTIVITY_ROWS
        assert [e["name"] for e in events[:2]] == [f"t{MCP_ACTIVITY_ROWS + 9}", f"t{MCP_ACTIVITY_ROWS + 8}"]

    def test_an_empty_log_gives_an_empty_list(self, client):
        assert client.get("/api/mcp-remote/activity").json() == {"status": "success", "events": []}

    def test_each_row_names_who_made_the_request(self, client, auth_cookies, connected):
        audit.record(1, "10.0.0.5", "tools/list", None, 200, 1, 10)
        audit.record(None, "203.0.113.7", None, None, 401, 0, 10)
        mcp(connected.remote, connected.access)
        client.post(revoke_url(connected.grant_id), headers=_plain(auth_cookies))
        events = client.get("/api/mcp-remote/activity").json()["events"]
        by_name = {(e["credential"], e["method"], e["name"]): e for e in events}
        assert ("Key #1", "tools/list", None) in by_name
        assert ("None", None, None) in by_name and by_name[("None", None, None)]["status_code"] == 401
        assert (f"App #{connected.grant_id}", "tools/list", None) in by_name
        assert ("Dashboard", "dashboard", f"revoke-grant (App #{connected.grant_id})") in by_name
        assert all(e["time_display"] for e in events)

    def test_no_secret_or_token_is_ever_in_the_list(self, client, connected):
        mcp(connected.remote, connected.access)
        mcp(connected.remote, "mcpat_wrong")
        body = json.dumps(client.get("/api/mcp-remote/activity").json())
        for secret in (connected.access, connected.refresh, connected.remote.secret, "mcpat_wrong", MCP_KEY):
            assert secret not in body

    def test_a_dashboard_action_is_not_held_back_by_the_cap_on_rows_without_a_credential(self, monkeypatch):
        monkeypatch.setattr(audit, "MCP_AUDIT_UNAUTH_ROWS_PER_MINUTE", 2)
        for _ in range(6):
            audit.record(None, "203.0.113.7", None, None, 401, 0, 10)
        audit.record_action("revoke-all: 0 grants", "10.0.0.5")
        assert len(sql("SELECT id FROM mcp_audit_log WHERE credential_kind = 'none'")) == 2
        assert len(sql("SELECT id FROM mcp_audit_log WHERE credential_kind = 'session'")) == 1

    def test_the_action_text_and_address_are_cleaned(self):
        audit.record_action("revoke\x00-all\n" + "x" * 500, "10.0.0.5\r\nforged")
        row = sql("SELECT mcp_name, client_address FROM mcp_audit_log")[0]
        assert "\x00" not in row["mcp_name"] and "\n" not in row["mcp_name"] and len(row["mcp_name"]) <= 100
        assert "\r" not in row["client_address"] and "\n" not in row["client_address"]


class TestDiagnosticsCounts:
    def test_the_counts_cover_apps_requests_and_the_last_day(self, client, connected):
        start(connected.remote)
        insert_audit("2999-01-01 00:00:00", 200)
        insert_audit("2999-01-01 00:00:01", 401)
        insert_audit("2999-01-01 00:00:02", 429)
        insert_audit("2999-01-01 00:00:03", 403, kind="none", method="oauth")
        insert_audit("2999-01-01 00:00:06", 401, kind="none", method=None)
        insert_audit("2999-01-01 00:00:04", 0, kind="none", method="audit")
        insert_audit("2999-01-01 00:00:05", 200, kind="session", method="dashboard")
        insert_audit("2000-01-01 00:00:00", 401)
        mcp_counts = client.get("/api/system/metrics").json()["mcp"]
        assert mcp_counts["connected_apps"] == 1 and mcp_counts["pending_requests"] == 1 and mcp_counts["pairing_open"] is True
        assert mcp_counts["refused_24h"] == 4
        assert mcp_counts["requests_24h"] >= 5

    def test_a_revoked_app_and_a_closed_window_are_not_counted(self, client, auth_cookies, connected):
        client.post("/api/mcp-remote/revoke-all", headers=_plain(auth_cookies))
        counts = client.get("/api/system/metrics").json()["mcp"]
        assert (counts["connected_apps"], counts["pending_requests"], counts["pairing_open"]) == (0, 0, False)

    def test_the_diagnostics_panel_has_the_rows_and_the_script_fills_them(self, client):
        html = client.get("/settings").text
        for element in ('id="diag-mcp-apps"', 'id="diag-mcp-pending"', 'id="diag-mcp-requests"', 'id="diag-mcp-refused"'):
            assert element in html, element


class TestDisabledServer:
    def test_every_path_answers_503_and_the_revoke_buttons_still_work(self, client, auth_cookies, connected, monkeypatch):
        from mcp_server import OAUTH_ROUTES
        monkeypatch.setattr("mcp_server.auth.load_config", lambda: {"MCP_SERVER_ENABLED": False})
        for path, _, methods in OAUTH_ROUTES:
            for method in methods:
                assert connected.http.request(method, path, headers=PUBLIC).status_code == 503, path
        assert mcp(connected.remote, connected.access).status_code == 503
        assert mcp(connected.remote, MCP_KEY, headers={"Host": "127.0.0.1"}).status_code == 503
        assert client.post(revoke_url(connected.grant_id), headers=_plain(auth_cookies)).status_code == 200


class TestCard:
    def test_the_card_has_the_new_controls(self, client, remote):
        html = client.get("/settings").text
        for element in ('id="mcp-remote-activity-body"', 'id="mcp-remote-apps-msg"', "revokeAllMcpRemoteAccess()"):
            assert element in html, element
        assert str(MCP_ACTIVITY_ROWS) in html
