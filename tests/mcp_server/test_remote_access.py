"""MCP Remote Access (MCP-15d): the Settings routes (enable, client, pairing window, approve, deny), the public waiting page and its status poll."""
import os
import re
from pathlib import Path
from unittest.mock import patch

import pytest

import config
import db_mcp_oauth
from mcp_server import oauth

from .helpers import MCP_KEY
from .test_oauth_server import (
    PUBLIC_HOST, REDIRECT_URI, authorize_params, binding_of, pending_request_id, remote, sql, start,  # noqa: F401  remote is a fixture
)

PUBLIC = {"Host": PUBLIC_HOST}
LAN = {"Host": "127.0.0.1"}
ROOT = Path(__file__).resolve().parents[2]


def _plain(auth_cookies):
    return {"Cookie": "; ".join(f"{k}={v}" for k, v in auth_cookies.items()), "X-CSRFToken": auth_cookies["csrftoken"]}


@pytest.fixture
def saved_config(monkeypatch):
    """What the Settings routes read: remote access on, with a saved public host."""
    values = {"MCP_REMOTE_ENABLED": True, "MCP_PUBLIC_HOST": PUBLIC_HOST}
    monkeypatch.setattr("api_routes_mcp_remote.load_config", lambda: values)
    monkeypatch.setattr("api_routes_mcp_remote.arm_mcp_pairing_window_check", lambda until: None)
    return values


def _status(client):
    response = client.get("/api/mcp-remote")
    assert response.status_code == 200
    return response.json()


class TestAccessControl:
    @pytest.mark.parametrize("method,path", [
        ("get", "/api/mcp-remote"), ("post", "/api/mcp-remote/enabled"), ("post", "/api/mcp-remote/client"),
        ("post", "/api/mcp-remote/pairing"), ("post", "/api/mcp-remote/requests/AAAAAAAAAAAAAAAAAAAAAA/approve"),
        ("post", "/api/mcp-remote/requests/AAAAAAAAAAAAAAAAAAAAAA/deny"),
    ])
    def test_needs_a_session_and_refuses_both_keys(self, raw_client, method, path):
        assert getattr(raw_client, method)(path).status_code in (302, 401, 403)
        assert getattr(raw_client, method)(path, headers={"X-API-Key": os.environ["API_KEY"]}).status_code == 403
        assert getattr(raw_client, method)(path, headers={"Authorization": f"Bearer {MCP_KEY}"}).status_code == 302

    @pytest.mark.parametrize("path", ["/api/mcp-remote/client", "/api/mcp-remote/pairing", "/api/mcp-remote/requests/AAAAAAAAAAAAAAAAAAAAAA/approve"])
    def test_the_gated_routes_ask_for_the_password(self, client, auth_cookies, path):
        response = client.post(path, headers=_plain(auth_cookies))
        assert response.status_code == 403 and response.headers["X-Step-Up-Required"] == "1"

    def test_request_ids_have_a_fixed_shape(self, client, step_up_headers):
        for action in ("approve", "deny"):
            assert client.post(f"/api/mcp-remote/requests/short/{action}", headers=step_up_headers).status_code == 422
            assert client.post(f"/api/mcp-remote/requests/{'a' * 23}/{action}", headers=step_up_headers).status_code == 422


class TestEnable:
    def test_enabling_needs_the_password(self, client, auth_cookies):
        with patch("api_routes_mcp_remote.update_config_atomic") as update:
            response = client.post("/api/mcp-remote/enabled", json={"enabled": True}, headers=_plain(auth_cookies))
        assert response.status_code == 403 and response.headers["X-Step-Up-Required"] == "1"
        update.assert_not_called()

    def test_enabling_needs_a_saved_public_host(self, client, step_up_headers, monkeypatch):
        monkeypatch.setattr("api_routes_mcp_remote.load_config", lambda: {"MCP_PUBLIC_HOST": ""})
        with patch("api_routes_mcp_remote.update_config_atomic") as update:
            response = client.post("/api/mcp-remote/enabled", json={"enabled": True}, headers=step_up_headers)
        assert response.status_code == 409
        update.assert_not_called()

    def test_enabling_writes_the_flag(self, client, step_up_headers, saved_config):
        with patch("api_routes_mcp_remote.update_config_atomic") as update:
            assert client.post("/api/mcp-remote/enabled", json={"enabled": True}, headers=step_up_headers).status_code == 200
        update.assert_called_once_with({"MCP_REMOTE_ENABLED": True})

    def test_disabling_needs_no_password(self, client, auth_cookies):
        with patch("api_routes_mcp_remote.update_config_atomic") as update:
            assert client.post("/api/mcp-remote/enabled", json={"enabled": False}, headers=_plain(auth_cookies)).status_code == 200
        update.assert_called_once_with({"MCP_REMOTE_ENABLED": False})

    def test_rejects_a_non_boolean(self, client, step_up_headers):
        assert client.post("/api/mcp-remote/enabled", json={"enabled": "maybe"}, headers=step_up_headers).status_code == 422


class TestClientAndPairing:
    def test_creating_the_client_shows_the_secret_once_and_stores_only_its_hash(self, client, step_up_headers, remote):
        created = client.post("/api/mcp-remote/client", headers=step_up_headers).json()
        assert created["status"] == "success" and len(created["client_secret"]) == 64
        row = db_mcp_oauth.get_client()
        assert row["client_id"] == created["client_id"] and row["secret_hash"] != created["client_secret"]
        listing = client.get("/api/mcp-remote")
        assert created["client_secret"] not in listing.text and row["secret_hash"] not in listing.text
        assert listing.json()["client"]["client_id"] == created["client_id"]

    def test_rotating_the_secret_ends_the_connected_apps(self, client, step_up_headers, remote):
        from .test_oauth_server import exchange
        assert exchange(remote).status_code == 200
        assert len(_status(client)["apps"]) == 1
        client.post("/api/mcp-remote/client", headers=step_up_headers)
        assert _status(client)["apps"] == []

    def test_the_window_needs_remote_access_turned_on(self, client, step_up_headers, monkeypatch):
        monkeypatch.setattr("api_routes_mcp_remote.load_config", lambda: {"MCP_REMOTE_ENABLED": False})
        assert client.post("/api/mcp-remote/pairing", headers=step_up_headers).status_code == 409

    def test_the_window_needs_a_client(self, client, step_up_headers, saved_config):
        assert db_mcp_oauth.get_client() is None
        assert client.post("/api/mcp-remote/pairing", headers=step_up_headers).status_code == 409

    def test_opening_the_window_arms_the_check_and_shows_the_time_left(self, client, step_up_headers, saved_config, remote, monkeypatch):
        oauth.close_pairing_window()
        armed = []
        monkeypatch.setattr("api_routes_mcp_remote.arm_mcp_pairing_window_check", armed.append)
        response = client.post("/api/mcp-remote/pairing", headers=step_up_headers)
        assert response.status_code == 200
        pairing = response.json()["pairing"]
        assert pairing["open"] and 0 < pairing["seconds_left"] <= 600
        assert armed == [db_mcp_oauth.get_client()["pairing_open_until"]]
        assert _status(client)["pairing"]["open"]

    def test_status_reports_the_saved_host_and_whether_it_is_active(self, client, saved_config, remote, monkeypatch):
        data = _status(client)
        assert data["endpoint_url"] == f"https://{PUBLIC_HOST}/mcp" and data["host_active"] is True
        monkeypatch.setattr(config, "MCP_PUBLIC_HOST", "")
        assert _status(client)["host_active"] is False


class TestRequests:
    def _wait(self, remote):
        response, _ = start(remote)
        assert response.status_code == 302
        return response

    def test_a_pending_request_is_listed_with_its_code_and_requester(self, client, remote):
        self._wait(remote)
        [pending] = _status(client)["requests"]
        stored = sql("SELECT display_code, requester_address FROM mcp_oauth_requests")[0]
        assert pending["code"] == stored["display_code"] and pending["address"] == stored["requester_address"]
        assert set(pending) == {"id", "code", "requested_display", "address", "user_agent", "seconds_left"}

    def test_a_script_in_the_user_agent_is_neither_stored_raw_nor_rendered_as_markup(self, client, remote):
        hostile = "<img src=x onerror=alert(1)>\x01\x7f\xad" + "A" * 400
        remote.http.get("/oauth/authorize", params=authorize_params(remote.client_id, "v" * 50), headers={"User-Agent": hostile.encode("latin-1")})
        stored = sql("SELECT user_agent FROM mcp_oauth_requests")[0]["user_agent"]
        assert len(stored) <= 200 and "\x00" not in stored and "‮" not in stored
        script = (ROOT / "static/js/settings_mcp_remote.js").read_text(encoding="utf-8")
        assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|document\.write", script)
        assert "textContent" in script

    def test_approving_needs_an_open_window(self, client, step_up_headers, remote):
        self._wait(remote)
        request_id = pending_request_id()
        oauth.close_pairing_window()
        assert client.post(f"/api/mcp-remote/requests/{request_id}/approve", headers=step_up_headers).status_code == 409
        assert sql("SELECT status FROM mcp_oauth_requests")[0]["status"] == "pending"

    def test_approving_marks_the_request_and_tells_the_operator(self, client, step_up_headers, remote, remote_notifications):
        self._wait(remote)
        request_id = pending_request_id()
        assert client.post(f"/api/mcp-remote/requests/{request_id}/approve", headers=step_up_headers).status_code == 200
        assert sql("SELECT status FROM mcp_oauth_requests")[0]["status"] == "approved"
        assert _status(client)["requests"] == []
        assert any("was approved" in sent["text"] for sent in remote_notifications)

    def test_a_decided_or_expired_request_cannot_be_approved_again(self, client, step_up_headers, remote):
        self._wait(remote)
        request_id = pending_request_id()
        url = f"/api/mcp-remote/requests/{request_id}/approve"
        assert client.post(url, headers=step_up_headers).status_code == 200
        assert client.post(url, headers=step_up_headers).status_code == 404
        self._wait(remote)
        request_id = pending_request_id()
        remote.clock.advance(minutes=6)
        assert client.post(f"/api/mcp-remote/requests/{request_id}/approve", headers=step_up_headers).status_code == 404

    def test_denying_needs_no_password_and_works_once(self, client, auth_cookies, remote):
        self._wait(remote)
        url = f"/api/mcp-remote/requests/{pending_request_id()}/deny"
        assert client.post(url, headers=_plain(auth_cookies)).status_code == 200
        assert sql("SELECT status FROM mcp_oauth_requests")[0]["status"] == "denied"
        assert client.post(url, headers=_plain(auth_cookies)).status_code == 404


def poll(remote, binding=None):
    headers = dict(PUBLIC)
    if binding is not None:
        headers["Cookie"] = f"mcp_oauth_binding={binding}"
    return remote.http.get("/oauth/approve-status", headers=headers)


def start_bound(remote):
    response, _ = start(remote)
    assert response.status_code == 302
    return binding_of(response)


class TestWaitingPage:
    def test_the_page_is_self_contained_and_shows_no_code(self, remote):
        response = remote.http.get("/oauth/approve", headers=PUBLIC)
        assert response.status_code == 200
        html = response.text
        assert "<style>" in html and "<script>" in html and "/static/" not in html and "https://cdn" not in html
        assert f'data-redirect-prefix="{REDIRECT_URI}"' in html
        assert response.headers["cache-control"] == "no-store" and "mcp_oauth_binding" not in response.headers.get("set-cookie", "")
        assert "{{" not in html and "{%" not in html

    def test_the_page_script_renders_only_through_textContent(self):
        script = (ROOT / "static/js/oauth_approve.js").read_text(encoding="utf-8")
        assert not re.search(r"innerHTML|outerHTML|insertAdjacentHTML|eval\(|document\.write", script)
        assert "startsWith(redirectPrefix)" in script

    def test_other_hosts_get_404_and_a_disabled_server_503(self, remote, monkeypatch):
        for path in ("/oauth/approve", "/oauth/approve-status"):
            assert remote.http.get(path, headers=LAN).status_code == 404
        monkeypatch.setattr("mcp_server.auth.load_config", lambda: {"MCP_SERVER_ENABLED": False})
        for path in ("/oauth/approve", "/oauth/approve-status"):
            assert remote.http.get(path, headers=PUBLIC).status_code == 503

    def test_loading_the_page_is_audited_but_polling_is_not(self, remote):
        remote.http.get("/oauth/approve", headers=PUBLIC)
        poll(remote)
        rows = sql("SELECT mcp_method, mcp_name, status FROM mcp_audit_log")
        assert rows == [{"mcp_method": "oauth", "mcp_name": "approve", "status": 200}]

    def test_without_a_valid_binding_cookie_nothing_is_revealed(self, remote):
        start_bound(remote)
        for binding in (None, "x" * 43, "x", "A" * 500):
            assert poll(remote, binding).json() == {"state": "unknown"}

    def test_a_pending_request_shows_its_code_to_the_binding_browser(self, remote):
        binding = start_bound(remote)
        response = poll(remote, binding)
        assert response.json() == {"state": "pending", "code": sql("SELECT display_code FROM mcp_oauth_requests")[0]["display_code"]}
        assert response.headers["cache-control"] == "no-store"

    def test_approval_hands_the_redirect_over_once(self, remote):
        binding = start_bound(remote)
        oauth.approve_request(pending_request_id())
        data = poll(remote, binding).json()
        assert data["state"] == "approved" and data["redirect"].startswith(REDIRECT_URI + "?")
        assert "code=" in data["redirect"] and "state=state-123" in data["redirect"] and "iss=" in data["redirect"]
        assert poll(remote, binding).json() == {"state": "done"}

    def test_denial_and_expiry_are_reported(self, remote):
        binding = start_bound(remote)
        oauth.deny_request(pending_request_id())
        assert poll(remote, binding).json() == {"state": "denied"}
        binding = start_bound(remote)
        remote.clock.advance(minutes=6)
        assert poll(remote, binding).json() == {"state": "expired"}

    def test_an_approval_cannot_be_claimed_after_it_expired(self, remote):
        binding = start_bound(remote)
        oauth.approve_request(pending_request_id())
        remote.clock.advance(minutes=6)
        assert poll(remote, binding).json() == {"state": "expired"}
        assert sql("SELECT code_hash FROM mcp_oauth_requests")[0]["code_hash"] is None

    def test_another_browser_cannot_claim_the_approved_code(self, remote):
        start_bound(remote)
        oauth.approve_request(pending_request_id())
        assert poll(remote, "y" * 43).json() == {"state": "unknown"}
        assert sql("SELECT code_hash FROM mcp_oauth_requests")[0]["code_hash"] is None


class TestSettingsPage:
    def test_the_card_has_every_control_and_loads_its_script(self, client, remote):
        html = client.get("/settings").text
        for element in (
            'id="mcp-remote-panel"', 'id="MCP_REMOTE_ENABLED"', 'id="mcp_public_host"', 'id="mcp-remote-client-btn"',
            'id="mcp-remote-pairing-btn"', 'id="mcp-remote-requests-body"', 'id="mcp-remote-apps-body"',
        ):
            assert element in html, element
        assert "/static/js/settings_mcp_remote.js?v=" in html
        assert remote.secret not in html and db_mcp_oauth.get_client()["secret_hash"] not in html

    def test_the_script_file_exists_and_has_no_template_syntax(self):
        script = (ROOT / "static/js/settings_mcp_remote.js").read_text(encoding="utf-8")
        assert "{{" not in script and "{%" not in script
