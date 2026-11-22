"""Access token life and the restart warning: the saved life reaches the token response, a longer life reverts to the default on its own,
the Settings route is gated when it lengthens the life, and the restart-risk route reports a connected app that is due to refresh."""
import os
from datetime import timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

import config
from constants import MCP_OAUTH_ACCESS_TOKEN_CHOICES, MCP_OAUTH_ACCESS_TOKEN_SECONDS, MCP_OAUTH_REFRESH_AHEAD_SECONDS, MCP_OAUTH_REFRESH_BEHIND_SECONDS
from mcp_server import oauth

from .helpers import MCP_KEY
from .test_oauth_server import refresh, remote, sql, tokens  # noqa: F401  remote is a fixture
from .test_remote_access import _plain

ROOT = Path(__file__).resolve().parents[2]
HOUR = 3600
DAY = 24 * HOUR


@pytest.fixture
def life(monkeypatch):
    """The saved token-life keys the OAuth core reads; everything else in the config stays as it is."""
    values = {}
    real = config.load_config
    monkeypatch.setattr(config, "load_config", lambda: {**real(), **values})
    return values


@pytest.fixture
def remote_on(monkeypatch):
    values = {"MCP_REMOTE_ENABLED": True, "MCP_PUBLIC_HOST": "localhost"}
    monkeypatch.setattr("api.routes_mcp_remote.load_config", lambda: values)
    return values


def _revert_stamp(clock, hours):
    return (clock.now + timedelta(hours=hours)).strftime("%Y-%m-%d %H:%M:%S")


class TestLifeAtIssue:
    def test_default_is_fifteen_minutes(self, remote, life):
        assert tokens(remote)["expires_in"] == MCP_OAUTH_ACCESS_TOKEN_SECONDS == 900

    @pytest.mark.parametrize("seconds", MCP_OAUTH_ACCESS_TOKEN_CHOICES)
    def test_saved_life_reaches_the_token_response(self, remote, life, seconds):
        life["MCP_ACCESS_TOKEN_SECONDS"] = seconds
        assert tokens(remote)["expires_in"] == seconds

    def test_a_refresh_uses_the_life_saved_at_that_moment(self, remote, life):
        body = tokens(remote)
        life["MCP_ACCESS_TOKEN_SECONDS"] = 8 * HOUR
        remote.clock.advance(seconds=800)
        assert refresh(remote, body["refresh_token"]).json()["expires_in"] == 8 * HOUR

    def test_the_life_reverts_to_the_default_at_the_revert_time(self, remote, life):
        life["MCP_ACCESS_TOKEN_SECONDS"] = 8 * HOUR
        life["MCP_ACCESS_TOKEN_REVERT_AT"] = _revert_stamp(remote.clock, 1)
        body = tokens(remote)
        assert body["expires_in"] == 8 * HOUR
        remote.clock.advance(seconds=HOUR + 1)
        assert refresh(remote, body["refresh_token"]).json()["expires_in"] == 900

    def test_no_revert_time_keeps_the_life_until_changed(self, remote, life):
        life["MCP_ACCESS_TOKEN_SECONDS"] = DAY
        life["MCP_ACCESS_TOKEN_REVERT_AT"] = ""
        body = tokens(remote)
        remote.clock.advance(days=20)
        assert refresh(remote, body["refresh_token"]).json()["expires_in"] == DAY

    @pytest.mark.parametrize("stored", [1, 0, -900, 7200, 10 ** 9, "3600", None, 3600.5])
    def test_a_stored_life_outside_the_offered_choices_is_ignored(self, remote, life, stored):
        life["MCP_ACCESS_TOKEN_SECONDS"] = stored
        assert tokens(remote)["expires_in"] == 900

    def test_an_unreadable_revert_time_means_the_default(self, remote, life):
        life["MCP_ACCESS_TOKEN_SECONDS"] = DAY
        life["MCP_ACCESS_TOKEN_REVERT_AT"] = "tomorrow-ish"
        assert tokens(remote)["expires_in"] == 900

    def test_the_stored_expiry_matches_the_life(self, remote, life):
        life["MCP_ACCESS_TOKEN_SECONDS"] = 8 * HOUR
        tokens(remote)
        row = sql("SELECT created_at, expires_at FROM mcp_oauth_tokens WHERE kind = 'access'")[0]
        created, expires = oauth._moment(row["created_at"]), oauth._moment(row["expires_at"])
        assert (expires - created).total_seconds() == 8 * HOUR


class TestSavedState:
    def test_a_longer_life_stores_the_revert_time(self, remote):
        with patch("mcp_server.oauth.update_config_atomic") as update:
            oauth.save_access_token_life(8 * HOUR, 4)
        update.assert_called_once_with({"MCP_ACCESS_TOKEN_SECONDS": 8 * HOUR, "MCP_ACCESS_TOKEN_REVERT_AT": _revert_stamp(remote.clock, 4)})

    def test_zero_hours_keeps_it_until_changed_back(self, remote):
        with patch("mcp_server.oauth.update_config_atomic") as update:
            oauth.save_access_token_life(DAY, 0)
        update.assert_called_once_with({"MCP_ACCESS_TOKEN_SECONDS": DAY, "MCP_ACCESS_TOKEN_REVERT_AT": ""})

    def test_the_default_life_clears_any_revert_time(self, remote):
        with patch("mcp_server.oauth.update_config_atomic") as update:
            oauth.save_access_token_life(900, 8)
        update.assert_called_once_with({"MCP_ACCESS_TOKEN_SECONDS": 900, "MCP_ACCESS_TOKEN_REVERT_AT": ""})

    def test_the_state_shows_a_revert_time_only_while_the_life_is_longer(self, remote, life):
        life["MCP_ACCESS_TOKEN_SECONDS"] = 8 * HOUR
        life["MCP_ACCESS_TOKEN_REVERT_AT"] = _revert_stamp(remote.clock, 1)
        state = oauth.access_token_life()
        assert state["seconds"] == 8 * HOUR and state["revert_at"] == life["MCP_ACCESS_TOKEN_REVERT_AT"]
        remote.clock.advance(seconds=HOUR + 1)
        state = oauth.access_token_life()
        assert state["seconds"] == 900 and state["revert_at"] is None


class TestTokenLifeRoute:
    @pytest.mark.parametrize("method,path", [("post", "/api/mcp-remote/token-life"), ("get", "/api/mcp-remote/restart-risk")])
    def test_needs_a_session_and_refuses_both_keys(self, raw_client, method, path):
        assert getattr(raw_client, method)(path).status_code in (302, 401, 403, 422)
        assert getattr(raw_client, method)(path, headers={"X-API-Key": os.environ["API_KEY"]}).status_code == 403
        assert getattr(raw_client, method)(path, headers={"Authorization": f"Bearer {MCP_KEY}"}).status_code == 302

    def test_the_status_carries_the_life(self, client, remote_on, life):
        state = client.get("/api/mcp-remote").json()["token_life"]
        assert state["seconds"] == 900 and state["default_seconds"] == 900
        assert state["choices"] == list(MCP_OAUTH_ACCESS_TOKEN_CHOICES) and 0 in state["revert_hours_choices"] and state["revert_display"] is None

    def test_a_longer_life_needs_the_password(self, client, auth_cookies):
        with patch("mcp_server.oauth.update_config_atomic") as update:
            response = client.post("/api/mcp-remote/token-life", json={"seconds": 8 * HOUR, "revert_hours": 8}, headers=_plain(auth_cookies))
        assert response.status_code == 403 and response.headers["X-Step-Up-Required"] == "1"
        update.assert_not_called()

    def test_going_back_to_the_default_needs_no_password(self, client, auth_cookies):
        with patch("mcp_server.oauth.update_config_atomic") as update:
            response = client.post("/api/mcp-remote/token-life", json={"seconds": 900}, headers=_plain(auth_cookies))
        assert response.status_code == 200 and response.json()["token_life"]["default_seconds"] == 900
        update.assert_called_once_with({"MCP_ACCESS_TOKEN_SECONDS": 900, "MCP_ACCESS_TOKEN_REVERT_AT": ""})

    def test_saving_a_longer_life_writes_the_keys_and_an_audit_row(self, client, step_up_headers):
        with patch("mcp_server.oauth.update_config_atomic") as update:
            response = client.post("/api/mcp-remote/token-life", json={"seconds": 8 * HOUR, "revert_hours": 8}, headers=step_up_headers)
        assert response.status_code == 200
        saved = update.call_args.args[0]
        assert saved["MCP_ACCESS_TOKEN_SECONDS"] == 8 * HOUR and saved["MCP_ACCESS_TOKEN_REVERT_AT"]
        names = [row["mcp_name"] for row in sql("SELECT mcp_name FROM mcp_audit_log WHERE mcp_method = 'dashboard'")]
        assert any(name.startswith("token-life: 28800 s") for name in names)

    @pytest.mark.parametrize("body", [
        {"seconds": 7200}, {"seconds": 0}, {"seconds": -900}, {"seconds": 900, "revert_hours": 3}, {"seconds": 900, "revert_hours": -1},
        {"revert_hours": 8}, {"seconds": "soon"}, {"seconds": 1e9},
    ])
    def test_only_the_offered_values_are_accepted(self, client, step_up_headers, body):
        with patch("mcp_server.oauth.update_config_atomic") as update:
            assert client.post("/api/mcp-remote/token-life", json=body, headers=step_up_headers).status_code == 422
        update.assert_not_called()


class TestRestartRisk:
    def _risk(self, client):
        response = client.get("/api/mcp-remote/restart-risk")
        assert response.status_code == 200
        return response.json()

    def test_no_connected_app_is_no_risk(self, client, remote_on, remote):
        assert self._risk(client) == {"status": "success", "refresh_due": False, "seconds": None}

    def test_a_token_far_from_expiry_is_no_risk(self, client, remote_on, remote):
        tokens(remote)
        assert self._risk(client)["refresh_due"] is False

    def test_a_token_about_to_expire_is_a_risk(self, client, remote_on, remote):
        tokens(remote)
        remote.clock.advance(seconds=900 - MCP_OAUTH_REFRESH_AHEAD_SECONDS + 10)
        risk = self._risk(client)
        assert risk["refresh_due"] is True and 0 < risk["seconds"] <= MCP_OAUTH_REFRESH_AHEAD_SECONDS

    def test_a_late_refresh_is_still_a_risk_for_a_few_minutes(self, client, remote_on, remote):
        tokens(remote)
        remote.clock.advance(seconds=900 + 100)
        risk = self._risk(client)
        assert risk["refresh_due"] is True and risk["seconds"] == -100
        remote.clock.advance(seconds=MCP_OAUTH_REFRESH_BEHIND_SECONDS)
        assert self._risk(client)["refresh_due"] is False

    def test_an_app_that_stopped_calling_long_ago_is_no_risk(self, client, remote_on, remote):
        tokens(remote)
        remote.clock.advance(hours=5)
        assert self._risk(client)["refresh_due"] is False

    def test_only_the_newest_token_of_an_app_counts(self, client, remote_on, remote):
        body = tokens(remote)
        remote.clock.advance(seconds=800)
        assert refresh(remote, body["refresh_token"]).status_code == 200
        remote.clock.advance(seconds=60)
        assert self._risk(client)["refresh_due"] is False

    def test_a_revoked_app_is_no_risk(self, client, remote_on, remote):
        tokens(remote)
        remote.clock.advance(seconds=850)
        assert self._risk(client)["refresh_due"] is True
        assert oauth.revoke_grant(sql("SELECT id FROM mcp_oauth_grants")[0]["id"])
        assert self._risk(client)["refresh_due"] is False

    def test_remote_access_off_is_no_risk(self, client, remote_on, remote):
        tokens(remote)
        remote.clock.advance(seconds=850)
        remote_on["MCP_REMOTE_ENABLED"] = False
        assert self._risk(client)["refresh_due"] is False

    def test_a_long_life_makes_the_window_rare(self, client, remote_on, remote, life):
        life["MCP_ACCESS_TOKEN_SECONDS"] = 8 * HOUR
        tokens(remote)
        remote.clock.advance(seconds=900)
        assert self._risk(client)["refresh_due"] is False
        remote.clock.advance(seconds=8 * HOUR - 900 - 60)
        assert self._risk(client)["refresh_due"] is True


class TestRestartButtons:
    def _function(self, name):
        source = (ROOT / "static" / "js" / "settings_backup.js").read_text()
        start = source.index(f"async function {name}(")
        end = source.find("\nasync function ", start + 1)
        return source[start:] if end == -1 else source[start:end]

    @pytest.mark.parametrize("name,post", [("restartSystem", "/api/system/restart"), ("forceRestart", "/api/system/force-restart")])
    def test_both_restart_buttons_ask_before_posting(self, name, post):
        body = self._function(name)
        assert "confirmRestartOutsideMcpRefresh()" in body and post in body
        assert body.index("confirmRestartOutsideMcpRefresh()") < body.index(post)

    def test_the_warning_reads_the_restart_risk_route_and_never_blocks_on_a_failure(self):
        body = self._function("confirmRestartOutsideMcpRefresh")
        assert "/api/mcp-remote/restart-risk" in body and body.count("return true") >= 3
