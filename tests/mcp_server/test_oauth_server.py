"""OAuth authorization server core (MCP-15c): metadata documents, /oauth/authorize, /oauth/token, /oauth/revoke, token verification on /mcp per host, rotation and reuse detection, expiry in UTC."""
import base64
import hashlib
import secrets
import time
from datetime import datetime, timedelta, timezone
from http.cookies import SimpleCookie
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import pytest

import config
import db_mcp_oauth
from constants import (
    MCP_AUTH_FAILURES_MAX, MCP_OAUTH_ACCESS_TOKEN_SECONDS, MCP_OAUTH_CACHE_MAX_ENTRIES, MCP_OAUTH_PENDING_MAX,
    MCP_OAUTH_REFRESH_RETRY_GRACE_SECONDS, MCP_OAUTH_REQUESTS_PER_HOUR, MCP_OAUTH_USER_AGENT_MAX_CHARS,
)
from database import get_connection
from mcp_server import keys, limits, oauth

from .helpers import MCP_KEY, rpc

PUBLIC_HOST = "localhost"
LAN_HOST = {"Host": "127.0.0.1"}
REDIRECT_URI = "https://claude.ai/api/mcp/auth_callback"
RESOURCE = f"https://{PUBLIC_HOST}/mcp"
CHALLENGE = f'Bearer resource_metadata="https://{PUBLIC_HOST}/.well-known/oauth-protected-resource/mcp", scope="mcp:read"'
OAUTH_TABLES = ("mcp_oauth_client", "mcp_oauth_requests", "mcp_oauth_grants", "mcp_oauth_tokens")


class FakeClock:
    def __init__(self):
        self.now = datetime(2026, 10, 21, 12, 0, 0, tzinfo=timezone.utc)

    def __call__(self):
        return self.now

    def advance(self, **delta):
        self.now += timedelta(**delta)


def sql(query, *args):
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute(query, args)]
    finally:
        conn.close()


def challenge_for(verifier):
    return base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip("=")


def authorize_params(known_client, verifier, **override):
    params = {
        "response_type": "code", "client_id": known_client, "redirect_uri": REDIRECT_URI, "code_challenge": challenge_for(verifier),
        "code_challenge_method": "S256", "state": "state-123", "scope": "mcp:read", "resource": RESOURCE,
    }
    params.update(override)
    return {key: value for key, value in params.items() if value is not None}


@pytest.fixture
def remote(monkeypatch, raw_client):
    monkeypatch.setattr(config, "MCP_PUBLIC_HOST", PUBLIC_HOST)
    clock = FakeClock()
    monkeypatch.setattr(oauth, "_now", clock)
    created = oauth.create_client()
    oauth.open_pairing_window()
    try:
        yield SimpleNamespace(
            http=raw_client, client_id=created["client_id"], secret=created["client_secret"], clock=clock,
            auth=(created["client_id"], created["client_secret"]),
        )
    finally:
        raw_client.cookies.clear()


def start(remote, verifier=None, **override):
    verifier = verifier or secrets.token_urlsafe(48)
    response = remote.http.get("/oauth/authorize", params=authorize_params(remote.client_id, verifier, **override))
    return response, verifier


def binding_cookie(response):
    return next((c for c in response.headers.get_list("set-cookie") if c.startswith("mcp_oauth_binding=")), None)


def binding_of(response):
    cookie = SimpleCookie()
    cookie.load(binding_cookie(response))
    return cookie["mcp_oauth_binding"].value


def pending_request_id():
    return sql("SELECT id FROM mcp_oauth_requests ORDER BY rowid DESC LIMIT 1")[0]["id"]


def pair(remote, **override):
    """Runs authorize, approval and claim; returns the authorization code, the PKCE verifier and the state."""
    response, verifier = start(remote, **override)
    assert response.status_code == 302, response.text
    assert oauth.approve_request(pending_request_id())
    url = oauth.claim_authorization(binding_of(response))
    query = parse_qs(urlsplit(url).query)
    return query["code"][0], verifier, query["state"][0]


def token_form(remote, auth_code, verifier, **override):
    data = {"grant_type": "authorization_code", "code": auth_code, "redirect_uri": REDIRECT_URI, "code_verifier": verifier, "resource": RESOURCE}
    data.update(override)
    return {key: value for key, value in data.items() if value is not None}


def exchange(remote, **override):
    code, verifier, _ = pair(remote)
    return remote.http.post("/oauth/token", data=token_form(remote, code, verifier, **override), auth=remote.auth)


def tokens(remote):
    response = exchange(remote)
    assert response.status_code == 200, response.text
    return response.json()


def refresh(remote, refresh_token, **extra):
    return remote.http.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": refresh_token, **extra}, auth=remote.auth)


def mcp(remote, token, **kwargs):
    return rpc(remote.http, "tools/list", key=token, **kwargs)


def one_error(response, status, error):
    assert response.status_code == status, response.text
    assert response.json()["error"] == error
    assert response.headers["cache-control"] == "no-store"


class TestMetadata:
    def test_protected_resource_document_names_this_server_and_the_one_authorization_server(self, remote):
        response = remote.http.get("/.well-known/oauth-protected-resource/mcp")
        assert response.status_code == 200
        assert response.json() == {
            "resource": RESOURCE, "authorization_servers": [f"https://{PUBLIC_HOST}"], "scopes_supported": ["mcp:read"],
            "bearer_methods_supported": ["header"],
        }

    def test_authorization_server_document_has_no_registration_and_only_s256(self, remote):
        document = remote.http.get("/.well-known/oauth-authorization-server").json()
        origin = f"https://{PUBLIC_HOST}"
        assert document == {
            "issuer": origin, "authorization_endpoint": f"{origin}/oauth/authorize", "token_endpoint": f"{origin}/oauth/token",
            "revocation_endpoint": f"{origin}/oauth/revoke", "response_types_supported": ["code"],
            "grant_types_supported": ["authorization_code", "refresh_token"], "code_challenge_methods_supported": ["S256"],
            "token_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
            "revocation_endpoint_auth_methods_supported": ["client_secret_post", "client_secret_basic"],
            "scopes_supported": ["mcp:read"], "authorization_response_iss_parameter_supported": True,
        }
        assert "registration_endpoint" not in document and "client_id_metadata_document_supported" not in document

    def test_urls_come_from_the_configured_host_not_from_forwarded_headers(self, remote):
        headers = {"X-Forwarded-Host": "evil.example", "X-Forwarded-Proto": "http", "Origin": "https://evil.example"}
        document = remote.http.get("/.well-known/oauth-authorization-server", headers=headers).json()
        assert document["issuer"] == f"https://{PUBLIC_HOST}"
        assert "evil.example" not in str(document)

    @pytest.mark.parametrize("method, path", [
        ("GET", "/.well-known/oauth-protected-resource/mcp"), ("GET", "/.well-known/oauth-authorization-server"),
        ("GET", "/oauth/authorize"), ("POST", "/oauth/token"), ("POST", "/oauth/revoke"),
    ])
    def test_every_oauth_route_is_404_on_any_other_host(self, remote, method, path):
        response = remote.http.request(method, path, headers=LAN_HOST)
        assert response.status_code == 404 and response.content == b""

    def test_every_oauth_route_is_404_when_no_public_host_is_configured(self, remote, monkeypatch):
        monkeypatch.setattr(config, "MCP_PUBLIC_HOST", "")
        for method, path in (("GET", "/.well-known/oauth-authorization-server"), ("GET", "/oauth/authorize"), ("POST", "/oauth/token")):
            assert remote.http.request(method, path).status_code == 404

    def test_a_disabled_mcp_server_answers_503_on_every_oauth_route(self, remote, monkeypatch):
        monkeypatch.setattr("mcp_server.auth.load_config", lambda: {"MCP_SERVER_ENABLED": False})
        for method, path in (
            ("GET", "/.well-known/oauth-protected-resource/mcp"), ("GET", "/.well-known/oauth-authorization-server"),
            ("GET", "/oauth/authorize"), ("POST", "/oauth/token"), ("POST", "/oauth/revoke"),
        ):
            assert remote.http.request(method, path).status_code == 503, path

    def test_a_session_cookie_is_not_needed_and_not_redirected(self, remote):
        assert remote.http.get("/oauth/authorize").status_code != 302


class TestAuthorize:
    def test_refused_with_403_and_no_write_while_the_pairing_window_is_closed(self, remote):
        oauth.close_pairing_window()
        response, _ = start(remote)
        assert response.status_code == 403 and binding_cookie(response) is None
        assert sql("SELECT COUNT(*) AS n FROM mcp_oauth_requests")[0]["n"] == 0

    def test_refused_when_no_client_exists(self, remote):
        with_client = db_mcp_oauth.get_client()
        conn = get_connection()
        try:
            conn.execute("DELETE FROM mcp_oauth_client")
            conn.commit()
        finally:
            conn.close()
        response, _ = start(remote)
        assert response.status_code == 403 and with_client is not None

    def test_the_window_expires_in_utc(self, remote):
        remote.clock.advance(minutes=9, seconds=59)
        assert start(remote)[0].status_code == 302
        remote.clock.advance(seconds=2)
        assert start(remote)[0].status_code == 403

    def test_the_window_survives_the_server_timezone(self, remote, monkeypatch):
        monkeypatch.setenv("TZ", "Pacific/Auckland")
        time.tzset()
        try:
            oauth.open_pairing_window(10)
            until = db_mcp_oauth.get_client()["pairing_open_until"]
            assert until == (remote.clock.now + timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        finally:
            monkeypatch.delenv("TZ")
            time.tzset()

    def test_success_stores_one_pending_request_and_sets_the_binding_cookie(self, remote):
        response, _ = start(remote, state="abc")
        assert response.status_code == 302 and response.headers["location"] == "/oauth/approve"
        assert response.headers["cache-control"] == "no-store"
        set_cookie = binding_cookie(response)
        for attribute in ("HttpOnly", "Secure", "Path=/oauth", "SameSite=lax"):
            assert attribute.lower() in set_cookie.lower(), set_cookie
        rows = sql("SELECT * FROM mcp_oauth_requests")
        assert len(rows) == 1
        row = rows[0]
        assert row["status"] == "pending" and row["client_state"] == "abc" and row["redirect_uri"] == REDIRECT_URI
        assert row["resource"] == RESOURCE and row["scope"] == "mcp:read"
        assert len(row["display_code"]) == 6 and not set(row["display_code"]) & set("ILO01")
        assert row["created_at"] == "2026-10-21 12:00:00" and row["expires_at"] == "2026-10-21 12:05:00"
        assert row["binding_hash"] == keys.hash_key(binding_of(response)) and binding_of(response) not in str(row)
        assert row["code_hash"] is None

    def test_requester_text_is_cleaned_and_capped_before_it_is_stored(self, remote):
        agent = "Mozilla\x01\x7f\t<img src=x onerror=alert(1)>" + "A" * 1000
        response = remote.http.get(
            "/oauth/authorize", params=authorize_params(remote.client_id, "v" * 50), headers={"User-Agent": agent},
        )
        assert response.status_code == 302
        stored = sql("SELECT user_agent FROM mcp_oauth_requests")[0]["user_agent"]
        assert "\x01" not in stored and "\x7f" not in stored and "\t" not in stored
        assert len(stored) <= MCP_OAUTH_USER_AGENT_MAX_CHARS

    def test_line_breaks_and_bidirectional_controls_never_reach_the_table(self, remote):
        params = authorize_params(remote.client_id, "v" * 50)
        oauth.begin_authorization(params, "203.0.113.5\r\nforged", "Mozilla\r\n\u202eevil\u200b<script>alert(1)</script>")
        row = sql("SELECT user_agent, requester_address FROM mcp_oauth_requests")[0]
        assert row["user_agent"] == "Mozilla evil<script>alert(1)</script>"
        assert row["requester_address"] == "203.0.113.5 forged"

    @pytest.mark.parametrize("redirect_uri", [
        "https://claude.ai/api/mcp/auth_callback/", "http://claude.ai/api/mcp/auth_callback", "https://claude.ai/api/mcp/auth_callbac",
        "https://claude.ai/api/mcp/auth_callback?x=1", "https://claude.ai.evil.example/api/mcp/auth_callback",
        "https://evil.example/api/mcp/auth_callback", "https://claude.ai/api/mcp/auth_callback#frag", "HTTPS://claude.ai/api/mcp/auth_callback", "",
    ])
    def test_the_redirect_uri_must_match_exactly_and_is_never_redirected_to(self, remote, redirect_uri):
        response, _ = start(remote, redirect_uri=redirect_uri)
        assert response.status_code == 400 and "location" not in response.headers
        assert response.json()["error"] == "invalid_request"
        assert sql("SELECT COUNT(*) AS n FROM mcp_oauth_requests")[0]["n"] == 0

    def test_a_missing_redirect_uri_or_an_unknown_client_is_a_plain_400(self, remote):
        assert start(remote, redirect_uri=None)[0].status_code == 400
        assert start(remote, client_id="someone-else")[0].status_code == 400
        assert start(remote, client_id=None)[0].status_code == 400

    @pytest.mark.parametrize("state", [None, ""])
    def test_state_is_required(self, remote, state):
        response, _ = start(remote, state=state)
        assert response.status_code == 302
        target = urlsplit(response.headers["location"])
        query = parse_qs(target.query)
        assert f"{target.scheme}://{target.netloc}{target.path}" == REDIRECT_URI
        assert query["error"] == ["invalid_request"] and "state" not in query and query["iss"] == [f"https://{PUBLIC_HOST}"]
        assert sql("SELECT COUNT(*) AS n FROM mcp_oauth_requests")[0]["n"] == 0

    def test_an_oversized_state_is_refused_without_being_echoed(self, remote):
        response, _ = start(remote, state="s" * 600)
        assert response.status_code == 302 and "ssss" not in response.headers["location"]

    @pytest.mark.parametrize("override", [
        {"code_challenge": None}, {"code_challenge_method": None}, {"code_challenge_method": "plain"}, {"code_challenge": "short"},
        {"code_challenge": "a" * 44}, {"code_challenge": "a" * 42 + "="},
    ])
    def test_pkce_s256_is_required(self, remote, override):
        response, _ = start(remote, **override)
        assert response.status_code == 302
        query = parse_qs(urlsplit(response.headers["location"]).query)
        assert query["error"] == ["invalid_request"] and query["state"] == ["state-123"]
        assert sql("SELECT COUNT(*) AS n FROM mcp_oauth_requests")[0]["n"] == 0

    def test_only_the_code_response_type_is_supported(self, remote):
        response, _ = start(remote, response_type="token")
        assert parse_qs(urlsplit(response.headers["location"]).query)["error"] == ["unsupported_response_type"]

    @pytest.mark.parametrize("scope, accepted", [
        ("mcp:read", True), (None, True), ("mcp:read mcp:read", True), ("mcp:write", False), ("mcp:read mcp:write", False),
        ("offline_access", False), ("", False),
    ])
    def test_scope_must_be_a_subset_of_mcp_read(self, remote, scope, accepted):
        response, _ = start(remote, scope=scope)
        assert response.status_code == 302
        error = parse_qs(urlsplit(response.headers["location"]).query).get("error")
        assert (error is None) == accepted
        if not accepted:
            assert error == ["invalid_scope"]

    @pytest.mark.parametrize("resource", [None, "", "https://localhost/mcp/", "https://localhost", "http://localhost/mcp", "https://evil.example/mcp"])
    def test_the_resource_must_be_this_server(self, remote, resource):
        response, _ = start(remote, resource=resource)
        assert parse_qs(urlsplit(response.headers["location"]).query)["error"] == ["invalid_target"]
        assert sql("SELECT COUNT(*) AS n FROM mcp_oauth_requests")[0]["n"] == 0

    def test_a_repeated_or_oversized_parameter_is_refused(self, remote):
        url = "/oauth/authorize?" + "&".join(f"{k}={v}" for k, v in authorize_params(remote.client_id, "v" * 50).items()) + "&state=again"
        assert remote.http.get(url).status_code == 400
        assert start(remote, state_extra="x" * 3000)[0].status_code == 400

    def test_at_most_five_pending_requests_at_once(self, remote):
        for _ in range(MCP_OAUTH_PENDING_MAX):
            assert start(remote)[0].status_code == 302
        refused, _ = start(remote)
        assert refused.status_code == 429 and refused.headers["retry-after"] == "300"
        assert sql("SELECT COUNT(*) AS n FROM mcp_oauth_requests")[0]["n"] == MCP_OAUTH_PENDING_MAX

    def test_at_most_ten_requests_per_hour(self, remote):
        for _ in range(MCP_OAUTH_REQUESTS_PER_HOUR):
            assert start(remote)[0].status_code == 302
            remote.clock.advance(minutes=6)
            oauth.open_pairing_window(30)
        assert start(remote)[0].status_code == 429
        remote.clock.advance(hours=1)
        oauth.open_pairing_window(30)
        assert start(remote)[0].status_code == 302

    def test_closed_window_refusals_are_capped_per_minute_in_the_audit_log(self, remote, monkeypatch):
        from mcp_server import audit
        monkeypatch.setattr(audit, "MCP_AUDIT_UNAUTH_ROWS_PER_MINUTE", 2)
        oauth.close_pairing_window()
        statuses = [start(remote)[0].status_code for _ in range(6)]
        assert statuses == [403] * 6
        assert len(sql("SELECT * FROM mcp_audit_log WHERE mcp_name = 'authorize'")) == 2

    def test_the_authorize_endpoint_is_audited_without_the_request_values(self, remote):
        start(remote, state="very-secret-state")
        rows = sql("SELECT * FROM mcp_audit_log WHERE mcp_name = 'authorize'")
        assert len(rows) == 1 and rows[0]["mcp_method"] == "oauth" and rows[0]["status"] == 302 and rows[0]["credential_kind"] == "none"
        assert "very-secret-state" not in str(sql("SELECT * FROM mcp_audit_log"))


class TestApprovalPrimitives:
    def test_a_request_can_be_claimed_once_by_its_own_browser_only_after_approval(self, remote):
        response, _ = start(remote)
        binding = binding_of(response)
        assert oauth.claim_authorization(binding) is None
        assert oauth.approve_request(pending_request_id())
        assert oauth.claim_authorization("someone-elses-binding") is None
        url = oauth.claim_authorization(binding)
        query = parse_qs(urlsplit(url).query)
        assert url.startswith(REDIRECT_URI + "?") and query["state"] == ["state-123"] and query["iss"] == [f"https://{PUBLIC_HOST}"]
        assert oauth.claim_authorization(binding) is None
        row = sql("SELECT code_hash, code_expires_at FROM mcp_oauth_requests")[0]
        assert row["code_hash"] == keys.hash_key(query["code"][0]) and query["code"][0] not in str(row)
        assert row["code_expires_at"] == "2026-10-21 12:01:00"

    def test_an_expired_request_cannot_be_approved_or_claimed(self, remote):
        response, _ = start(remote)
        remote.clock.advance(minutes=5, seconds=1)
        assert not oauth.approve_request(pending_request_id())
        assert oauth.claim_authorization(binding_of(response)) is None

    def test_a_request_is_approved_only_once(self, remote):
        start(remote)
        request_id = pending_request_id()
        assert oauth.approve_request(request_id) and not oauth.approve_request(request_id)
        assert not oauth.approve_request("no-such-request")


class TestTokenExchange:
    def test_the_response_and_what_is_stored(self, remote):
        response = exchange(remote)
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store" and response.headers["pragma"] == "no-cache"
        body = response.json()
        assert set(body) == {"access_token", "token_type", "expires_in", "refresh_token", "scope"}
        assert body["token_type"] == "Bearer" and body["scope"] == "mcp:read" and body["expires_in"] == MCP_OAUTH_ACCESS_TOKEN_SECONDS
        assert body["access_token"].startswith("mcpat_") and body["refresh_token"].startswith("mcprt_")
        stored = {row["token_hash"]: row for row in sql("SELECT * FROM mcp_oauth_tokens")}
        assert set(stored) == {keys.hash_key(body["access_token"]), keys.hash_key(body["refresh_token"])}
        assert stored[keys.hash_key(body["access_token"])]["kind"] == "access"
        assert stored[keys.hash_key(body["access_token"])]["expires_at"] == "2026-10-21 12:15:00"
        assert stored[keys.hash_key(body["refresh_token"])]["expires_at"] == "2026-11-20 12:00:00"
        dump = "".join(str(sql(f"SELECT * FROM {table}")) for table in OAUTH_TABLES)
        assert body["access_token"] not in dump and body["refresh_token"] not in dump and remote.secret not in dump
        grant = sql("SELECT * FROM mcp_oauth_grants")[0]
        assert grant["expires_at"] == "2027-01-19 12:00:00" and grant["resource"] == RESOURCE and grant["revoked_at"] is None
        assert sql("SELECT grant_id FROM mcp_oauth_requests")[0]["grant_id"] == grant["id"]

    def test_the_client_secret_is_stored_only_as_a_hash(self, remote):
        row = db_mcp_oauth.get_client()
        assert row["secret_hash"] == keys.hash_key(remote.secret) and remote.secret not in str(row)

    def test_client_secret_post_works_like_basic(self, remote):
        code, verifier, _ = pair(remote)
        data = token_form(remote, code, verifier, client_id=remote.client_id, client_secret=remote.secret)
        assert remote.http.post("/oauth/token", data=data).status_code == 200

    def test_basic_credentials_may_be_form_encoded(self, remote):
        code, verifier, _ = pair(remote)
        encoded = base64.b64encode(f"{remote.client_id}:{remote.secret}".encode()).decode()
        response = remote.http.post("/oauth/token", data=token_form(remote, code, verifier), headers={"Authorization": f"Basic {encoded}"})
        assert response.status_code == 200

    @pytest.mark.parametrize("secret", ["", "wrong", "x" * 1500])
    def test_a_wrong_or_missing_secret_is_invalid_client(self, remote, secret):
        code, verifier, _ = pair(remote)
        response = remote.http.post("/oauth/token", data=token_form(remote, code, verifier, client_id=remote.client_id, client_secret=secret))
        one_error(response, 401, "invalid_client")
        assert response.json()["error_description"] == "Client authentication failed."

    def test_every_client_authentication_failure_looks_alike(self, remote):
        code, verifier, _ = pair(remote)
        form = token_form(remote, code, verifier)
        responses = [
            remote.http.post("/oauth/token", data={**form, "client_id": "someone-else", "client_secret": remote.secret}),
            remote.http.post("/oauth/token", data={**form, "client_id": remote.client_id, "client_secret": "nope"}),
            remote.http.post("/oauth/token", data=form),
            remote.http.post("/oauth/token", data=form, headers={"Authorization": "Basic !!!not-base64"}),
            remote.http.post("/oauth/token", data=form, headers={"Authorization": "Basic " + base64.b64encode(b"no-colon").decode()}),
        ]
        assert {(r.status_code, r.text) for r in responses} == {(401, responses[0].text)}

    def test_two_authentication_methods_at_once_are_refused(self, remote):
        code, verifier, _ = pair(remote)
        response = remote.http.post("/oauth/token", data=token_form(remote, code, verifier, client_secret=remote.secret), auth=remote.auth)
        one_error(response, 400, "invalid_request")

    def test_only_a_urlencoded_body_is_read(self, remote):
        code, verifier, _ = pair(remote)
        form = token_form(remote, code, verifier)
        one_error(remote.http.post("/oauth/token", json=form, auth=remote.auth), 400, "invalid_request")
        one_error(remote.http.post("/oauth/token", files={"code": ("code", code.encode())}, auth=remote.auth), 400, "invalid_request")
        one_error(remote.http.post("/oauth/token", content=b"", auth=remote.auth), 400, "invalid_request")

    def test_an_oversized_or_repeated_form_is_refused(self, remote):
        big = remote.http.post("/oauth/token", data={"x": "y" * 9000}, auth=remote.auth)
        assert big.status_code == 413 and big.json()["error"] == "invalid_request"
        repeated = remote.http.post(
            "/oauth/token", content=b"grant_type=refresh_token&grant_type=authorization_code", auth=remote.auth,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        one_error(repeated, 400, "invalid_request")

    @pytest.mark.parametrize("override", [{"grant_type": "password"}, {"grant_type": None}, {"grant_type": "client_credentials"}])
    def test_other_grant_types_are_unsupported(self, remote, override):
        code, verifier, _ = pair(remote)
        form = token_form(remote, code, verifier, **override)
        one_error(remote.http.post("/oauth/token", data=form, auth=remote.auth), 400, "unsupported_grant_type")

    @pytest.mark.parametrize("missing", ["code", "redirect_uri", "code_verifier"])
    def test_required_parameters(self, remote, missing):
        code, verifier, _ = pair(remote)
        one_error(remote.http.post("/oauth/token", data=token_form(remote, code, verifier, **{missing: None}), auth=remote.auth), 400, "invalid_request")

    @pytest.mark.parametrize("verifier", ["wrong-but-long-enough-verifier-" + "a" * 20, "short", "v" * 129, "bad chars!" + "a" * 40])
    def test_pkce_verifier_must_match_the_challenge(self, remote, verifier):
        code, _, _ = pair(remote)
        one_error(remote.http.post("/oauth/token", data=token_form(remote, code, verifier), auth=remote.auth), 400, "invalid_grant")
        assert sql("SELECT COUNT(*) AS n FROM mcp_oauth_tokens")[0]["n"] == 0

    def test_a_failed_attempt_burns_the_code(self, remote):
        code, verifier, _ = pair(remote)
        bad = token_form(remote, code, "wrong-but-long-enough-verifier-" + "a" * 20)
        one_error(remote.http.post("/oauth/token", data=bad, auth=remote.auth), 400, "invalid_grant")
        one_error(remote.http.post("/oauth/token", data=token_form(remote, code, verifier), auth=remote.auth), 400, "invalid_grant")

    @pytest.mark.parametrize("redirect_uri", ["https://claude.ai/api/mcp/auth_callback/", "https://evil.example/cb", "http://claude.ai/api/mcp/auth_callback"])
    def test_the_redirect_uri_must_be_the_one_from_the_authorization_request(self, remote, redirect_uri):
        code, verifier, _ = pair(remote)
        one_error(remote.http.post("/oauth/token", data=token_form(remote, code, verifier, redirect_uri=redirect_uri), auth=remote.auth), 400, "invalid_grant")

    def test_resource_must_match_when_sent_and_may_be_left_out(self, remote):
        code, verifier, _ = pair(remote)
        one_error(remote.http.post("/oauth/token", data=token_form(remote, code, verifier, resource="https://evil.example/mcp"), auth=remote.auth), 400, "invalid_target")
        code, verifier, _ = pair(remote)
        assert remote.http.post("/oauth/token", data=token_form(remote, code, verifier, resource=None), auth=remote.auth).status_code == 200

    def test_an_unknown_code_is_invalid_grant(self, remote):
        one_error(remote.http.post("/oauth/token", data=token_form(remote, "no-such-code", "v" * 50), auth=remote.auth), 400, "invalid_grant")

    def test_a_code_expires_sixty_seconds_after_it_is_claimed(self, remote):
        code, verifier, _ = pair(remote)
        remote.clock.advance(seconds=61)
        one_error(remote.http.post("/oauth/token", data=token_form(remote, code, verifier), auth=remote.auth), 400, "invalid_grant")
        code, verifier, _ = pair(remote)
        remote.clock.advance(seconds=59)
        assert remote.http.post("/oauth/token", data=token_form(remote, code, verifier), auth=remote.auth).status_code == 200

    def test_a_code_works_once_and_its_reuse_revokes_what_it_issued(self, remote):
        code, verifier, _ = pair(remote)
        form = token_form(remote, code, verifier)
        first = remote.http.post("/oauth/token", data=form, auth=remote.auth).json()
        assert mcp(remote, first["access_token"]).status_code == 200
        one_error(remote.http.post("/oauth/token", data=form, auth=remote.auth), 400, "invalid_grant")
        assert mcp(remote, first["access_token"]).status_code == 401
        one_error(refresh(remote, first["refresh_token"]), 400, "invalid_grant")
        assert sql("SELECT revoked_at FROM mcp_oauth_grants")[0]["revoked_at"] == "2026-10-21 12:00:00"

    def test_the_token_endpoint_is_audited_without_any_secret(self, remote):
        code, verifier, _ = pair(remote)
        wrong = remote.http.post("/oauth/token", data=token_form(remote, code, verifier), auth=(remote.client_id, "wrong-secret-value"))
        assert wrong.status_code == 401
        body = remote.http.post("/oauth/token", data=token_form(remote, code, verifier), auth=remote.auth).json()
        rows = sql("SELECT * FROM mcp_audit_log WHERE mcp_name = 'token' ORDER BY id")
        assert [(r["status"], r["credential_kind"]) for r in rows] == [(401, "none"), (200, "oauth")]
        grant_id = sql("SELECT id FROM mcp_oauth_grants")[0]["id"]
        assert rows[1]["key_id"] == grant_id and all(r["mcp_method"] == "oauth" for r in rows)
        everything = str(sql("SELECT * FROM mcp_audit_log"))
        for secret in (code, verifier, body["access_token"], body["refresh_token"], remote.secret, "wrong-secret-value"):
            assert secret not in everything

    def test_five_wrong_secrets_lock_the_address_out_of_the_oauth_endpoints_and_mcp(self, remote):
        login_before = sql("SELECT * FROM login_throttle")
        for _ in range(MCP_AUTH_FAILURES_MAX):
            assert remote.http.post("/oauth/token", data={"grant_type": "refresh_token"}, auth=(remote.client_id, "wrong")).status_code == 401
        refused = remote.http.post("/oauth/token", data={"grant_type": "refresh_token"}, auth=remote.auth)
        assert refused.status_code == 429 and int(refused.headers["retry-after"]) > 0
        assert remote.http.post("/oauth/revoke", data={"token": "x"}, auth=remote.auth).status_code == 429
        assert rpc(remote.http, "tools/list", key=MCP_KEY, headers=LAN_HOST).status_code == 429
        assert sql("SELECT * FROM login_throttle") == login_before

    def test_a_malformed_basic_header_counts_toward_the_lockout(self, remote):
        for _ in range(MCP_AUTH_FAILURES_MAX):
            remote.http.post("/oauth/token", data={"grant_type": "refresh_token"}, headers={"Authorization": "Basic %%%"})
        assert remote.http.post("/oauth/token", data={"grant_type": "refresh_token"}, auth=remote.auth).status_code == 429


class TestRefresh:
    def test_every_refresh_rotates_the_refresh_token(self, remote):
        first = tokens(remote)
        remote.clock.advance(minutes=5)
        response = refresh(remote, first["refresh_token"])
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        second = response.json()
        assert second["refresh_token"] != first["refresh_token"] and second["access_token"] != first["access_token"]
        assert second["refresh_token"].startswith("mcprt_") and second["expires_in"] == MCP_OAUTH_ACCESS_TOKEN_SECONDS
        assert mcp(remote, second["access_token"]).status_code == 200
        third = refresh(remote, second["refresh_token"]).json()
        assert third["refresh_token"] not in (first["refresh_token"], second["refresh_token"])
        rotated = sql("SELECT COUNT(*) AS n FROM mcp_oauth_tokens WHERE kind = 'refresh' AND rotated_at IS NOT NULL")[0]["n"]
        assert rotated == 2

    def test_a_rotated_refresh_token_presented_again_revokes_the_whole_grant(self, remote, caplog):
        first = tokens(remote)
        second = refresh(remote, first["refresh_token"]).json()
        assert mcp(remote, second["access_token"]).status_code == 200
        remote.clock.advance(seconds=MCP_OAUTH_REFRESH_RETRY_GRACE_SECONDS + 1)
        one_error(refresh(remote, first["refresh_token"]), 400, "invalid_grant")
        assert sql("SELECT revoked_at FROM mcp_oauth_grants")[0]["revoked_at"] is not None
        assert mcp(remote, second["access_token"]).status_code == 401
        assert mcp(remote, first["access_token"]).status_code == 401
        one_error(refresh(remote, second["refresh_token"]), 400, "invalid_grant")
        assert first["refresh_token"] not in caplog.text and second["refresh_token"] not in caplog.text

    def test_a_rotated_refresh_token_presented_again_within_the_grace_is_refused_without_revoking(self, remote, caplog):
        first = tokens(remote)
        second = refresh(remote, first["refresh_token"]).json()
        remote.clock.advance(seconds=MCP_OAUTH_REFRESH_RETRY_GRACE_SECONDS)
        one_error(refresh(remote, first["refresh_token"]), 400, "invalid_grant")
        one_error(refresh(remote, first["refresh_token"]), 400, "invalid_grant")
        assert sql("SELECT revoked_at FROM mcp_oauth_grants")[0]["revoked_at"] is None
        assert mcp(remote, second["access_token"]).status_code == 200
        assert refresh(remote, second["refresh_token"]).status_code == 200
        assert first["refresh_token"] not in caplog.text and second["refresh_token"] not in caplog.text

    def test_the_grace_ends_one_second_after_its_length(self, remote):
        first = tokens(remote)
        second = refresh(remote, first["refresh_token"]).json()
        remote.clock.advance(seconds=MCP_OAUTH_REFRESH_RETRY_GRACE_SECONDS + 1)
        one_error(refresh(remote, first["refresh_token"]), 400, "invalid_grant")
        assert sql("SELECT revoked_at FROM mcp_oauth_grants")[0]["revoked_at"] is not None
        assert mcp(remote, second["access_token"]).status_code == 401

    def test_a_refresh_that_loses_the_rotation_race_does_not_revoke_the_grant(self, remote, monkeypatch):
        first = tokens(remote)
        real_get_token = db_mcp_oauth.get_token
        winner = {}

        def get_token_then_lose_the_race(token_hash):
            row = real_get_token(token_hash)
            if row is not None and row["kind"] == "refresh" and not winner:
                winner["body"] = None
                winner["body"] = refresh(remote, first["refresh_token"]).json()
            return row

        monkeypatch.setattr(db_mcp_oauth, "get_token", get_token_then_lose_the_race)
        response = remote.http.post("/oauth/token", data={"grant_type": "refresh_token", "refresh_token": first["refresh_token"]}, auth=remote.auth)
        one_error(response, 400, "invalid_grant")
        assert sql("SELECT revoked_at FROM mcp_oauth_grants")[0]["revoked_at"] is None
        assert mcp(remote, winner["body"]["access_token"]).status_code == 200

    @pytest.mark.parametrize("make_token", [
        lambda body: "mcprt_unknown-token", lambda body: body["access_token"], lambda body: "", lambda body: "not-prefixed",
    ])
    def test_every_refresh_failure_is_invalid_grant(self, remote, make_token):
        body = tokens(remote)
        response = refresh(remote, make_token(body))
        assert response.status_code == 400 and response.json()["error"] in ("invalid_grant", "invalid_request")
        if make_token(body):
            assert response.json()["error"] == "invalid_grant"

    def test_an_idle_refresh_token_expires_after_thirty_days(self, remote):
        body = tokens(remote)
        remote.clock.advance(days=30, seconds=1)
        one_error(refresh(remote, body["refresh_token"]), 400, "invalid_grant")

    def test_a_grant_ends_ninety_days_after_it_was_approved_however_often_it_is_refreshed(self, remote):
        body = tokens(remote)
        for _ in range(3):
            remote.clock.advance(days=29)
            body = refresh(remote, body["refresh_token"]).json()
            assert "refresh_token" in body
        grant_expires = sql("SELECT expires_at FROM mcp_oauth_grants")[0]["expires_at"]
        assert max(r["expires_at"] for r in sql("SELECT expires_at FROM mcp_oauth_tokens WHERE kind = 'refresh'")) == grant_expires
        remote.clock.advance(days=4)
        one_error(refresh(remote, body["refresh_token"]), 400, "invalid_grant")
        assert mcp(remote, body["access_token"]).status_code == 401

    def test_a_revoked_grant_cannot_be_refreshed(self, remote):
        body = tokens(remote)
        grant_id = sql("SELECT id FROM mcp_oauth_grants")[0]["id"]
        oauth._revoke(grant_id, remote.clock.now)
        one_error(refresh(remote, body["refresh_token"]), 400, "invalid_grant")

    def test_scope_and_resource_checks_do_not_consume_the_token(self, remote):
        body = tokens(remote)
        one_error(refresh(remote, body["refresh_token"], scope="mcp:read mcp:write"), 400, "invalid_scope")
        one_error(refresh(remote, body["refresh_token"], scope=""), 400, "invalid_scope")
        one_error(refresh(remote, body["refresh_token"], resource="https://evil.example/mcp"), 400, "invalid_target")
        assert refresh(remote, body["refresh_token"], scope="mcp:read", resource=RESOURCE).status_code == 200

    def test_refreshing_drops_the_grants_cached_access_tokens(self, remote):
        body = tokens(remote)
        assert mcp(remote, body["access_token"]).status_code == 200
        assert keys.hash_key(body["access_token"]) in oauth._cache
        refresh(remote, body["refresh_token"])
        assert keys.hash_key(body["access_token"]) not in oauth._cache

    def test_a_refresh_token_cannot_be_used_as_an_access_token(self, remote):
        body = tokens(remote)
        assert mcp(remote, body["refresh_token"]).status_code == 401


class TestVerificationOnMcp:
    def test_an_access_token_works_on_the_public_host_and_is_audited_as_oauth(self, remote):
        body = tokens(remote)
        response = mcp(remote, body["access_token"])
        assert response.status_code == 200 and "tools" in response.json()["result"]
        grant_id = sql("SELECT id FROM mcp_oauth_grants")[0]["id"]
        row = sql("SELECT * FROM mcp_audit_log WHERE mcp_method = 'tools/list'")[0]
        assert (row["credential_kind"], row["key_id"], row["status"]) == ("oauth", grant_id, 200)
        used = sql("SELECT last_used_at, last_address FROM mcp_oauth_grants")[0]
        assert used["last_used_at"] == "2026-10-21 12:00:00" and used["last_address"] == "testclient"
        assert body["access_token"] not in str(sql("SELECT * FROM mcp_audit_log"))

    def test_last_used_is_written_at_most_once_a_minute(self, remote):
        body = tokens(remote)
        mcp(remote, body["access_token"])
        remote.clock.advance(seconds=30)
        mcp(remote, body["access_token"])
        assert sql("SELECT last_used_at FROM mcp_oauth_grants")[0]["last_used_at"] == "2026-10-21 12:00:00"
        remote.clock.advance(seconds=31)
        mcp(remote, body["access_token"])
        assert sql("SELECT last_used_at FROM mcp_oauth_grants")[0]["last_used_at"] == "2026-10-21 12:01:01"

    def test_the_access_token_expires_fifteen_minutes_after_issue_in_utc(self, remote):
        body = tokens(remote)
        remote.clock.advance(minutes=14, seconds=59)
        assert mcp(remote, body["access_token"]).status_code == 200
        remote.clock.advance(seconds=2)
        assert mcp(remote, body["access_token"]).status_code == 401

    def test_every_wrong_credential_on_the_public_host_gets_the_same_401_and_challenge(self, remote, monkeypatch):
        body = tokens(remote)
        other = tokens(remote)
        oauth.invalidate()
        oauth._revoke(sql("SELECT id FROM mcp_oauth_grants ORDER BY id DESC LIMIT 1")[0]["id"], remote.clock.now)
        attempts = {
            "none": {}, "named key": {"key": MCP_KEY}, "refresh token": {"key": body["refresh_token"]},
            "garbage": {"key": "mcpat_" + "a" * 43}, "not oauth": {"key": "not-a-token"}, "revoked": {"key": other["access_token"]},
            "empty": {"key": ""},
        }
        answers = {}
        for label, kwargs in attempts.items():
            limits.reset()
            response = rpc(remote.http, "tools/list", **{"key": None, **kwargs})
            answers[label] = (response.status_code, response.text, response.headers.get("www-authenticate"))
        assert {a for a in answers.values()} == {(401, answers["none"][1], CHALLENGE)}, answers
        limits.reset()
        with_api_key = rpc(remote.http, "tools/list", key=body["access_token"], headers={"X-API-Key": "test-api-key-do-not-use-in-production"})
        assert (with_api_key.status_code, with_api_key.text, with_api_key.headers["www-authenticate"]) == (401, answers["none"][1], CHALLENGE)

    def test_the_named_key_is_refused_on_the_public_host_and_still_works_on_the_lan_host(self, remote):
        assert rpc(remote.http, "tools/list", key=MCP_KEY).status_code == 401
        assert rpc(remote.http, "tools/list", key=MCP_KEY, headers=LAN_HOST).status_code == 200

    def test_an_oauth_token_is_refused_on_a_lan_host(self, remote):
        body = tokens(remote)
        assert mcp(remote, body["access_token"]).status_code == 200
        response = mcp(remote, body["access_token"], headers=LAN_HOST)
        assert response.status_code == 401 and response.headers["www-authenticate"] == "Bearer"
        assert response.json() == {"detail": "Invalid or missing MCP key"}

    def test_a_server_that_is_disabled_answers_503_before_any_token_check(self, remote, monkeypatch):
        body = tokens(remote)
        monkeypatch.setattr("mcp_server.auth.load_config", lambda: {"MCP_SERVER_ENABLED": False})
        assert mcp(remote, body["access_token"]).status_code == 503

    def test_five_wrong_tokens_lock_the_address_out_even_with_a_valid_token(self, remote):
        body = tokens(remote)
        for _ in range(MCP_AUTH_FAILURES_MAX):
            assert mcp(remote, "mcpat_wrong").status_code == 401
        assert mcp(remote, body["access_token"]).status_code == 429

    def test_a_token_for_another_resource_or_without_the_scope_is_refused(self, remote):
        conn = get_connection()
        try:
            for index, (resource, scope) in enumerate((("https://other.example/mcp", "mcp:read"), (RESOURCE, "mcp:write"))):
                grant_id = conn.execute(
                    "INSERT INTO mcp_oauth_grants (client_id, scope, resource, created_at, expires_at) VALUES ('c', ?, ?, '2026-10-21 12:00:00', '2027-01-01 00:00:00')",
                    (scope, resource),
                ).lastrowid
                conn.execute(
                    "INSERT INTO mcp_oauth_tokens (token_hash, kind, grant_id, created_at, expires_at) VALUES (?, 'access', ?, '2026-10-21 12:00:00', '2026-10-21 13:00:00')",
                    (keys.hash_key(f"mcpat_manual_{index}"), grant_id),
                )
            conn.commit()
        finally:
            conn.close()
        for index in range(2):
            limits.reset()
            assert mcp(remote, f"mcpat_manual_{index}").status_code == 401

    def test_the_cache_serves_repeat_requests_and_revocation_takes_effect_on_the_next_one(self, remote, monkeypatch):
        body = tokens(remote)
        reads = []
        original = db_mcp_oauth.get_token
        monkeypatch.setattr(db_mcp_oauth, "get_token", lambda token_hash: reads.append(token_hash) or original(token_hash))
        for _ in range(3):
            assert mcp(remote, body["access_token"]).status_code == 200
        assert len(reads) == 1
        revoked = remote.http.post("/oauth/revoke", data={"token": body["access_token"]}, auth=remote.auth)
        assert revoked.status_code == 200
        assert mcp(remote, body["access_token"]).status_code == 401

    def test_the_cache_is_bounded_and_drops_expired_entries(self, remote, monkeypatch):
        monkeypatch.setattr(oauth, "MCP_OAUTH_CACHE_MAX_ENTRIES", 2)
        first, second = tokens(remote), tokens(remote)
        remote.clock.advance(minutes=1)
        third = tokens(remote)
        for body in (first, second, third):
            assert mcp(remote, body["access_token"]).status_code == 200
        assert len(oauth._cache) == 2 and MCP_OAUTH_CACHE_MAX_ENTRIES >= 2
        remote.clock.advance(minutes=20)
        oauth._remember("hash", 1, remote.clock.now.timestamp() + 60, remote.clock.now.timestamp())
        assert list(oauth._cache) == ["hash"]

    def test_the_token_check_runs_off_the_event_loop(self, remote, monkeypatch):
        import main
        body = tokens(remote)
        calls = []
        original = main.run_in_threadpool

        async def spy(function, *args, **kwargs):
            calls.append(getattr(function, "__name__", ""))
            return await original(function, *args, **kwargs)

        monkeypatch.setattr(main, "run_in_threadpool", spy)
        assert mcp(remote, body["access_token"]).status_code == 200
        assert "auth_failure" in calls


class TestRevoke:
    def test_revoking_a_refresh_token_ends_the_grant_and_every_token_of_it(self, remote):
        body = tokens(remote)
        response = remote.http.post("/oauth/revoke", data={"token": body["refresh_token"]}, auth=remote.auth)
        assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
        assert mcp(remote, body["access_token"]).status_code == 401
        one_error(refresh(remote, body["refresh_token"]), 400, "invalid_grant")

    def test_an_unknown_token_is_still_200(self, remote):
        assert remote.http.post("/oauth/revoke", data={"token": "mcprt_nothing"}, auth=remote.auth).status_code == 200

    def test_the_client_must_authenticate_and_send_a_token(self, remote):
        body = tokens(remote)
        wrong = remote.http.post("/oauth/revoke", data={"token": body["refresh_token"]}, auth=(remote.client_id, "wrong"))
        one_error(wrong, 401, "invalid_client")
        assert mcp(remote, body["access_token"]).status_code == 200
        one_error(remote.http.post("/oauth/revoke", data={}, auth=remote.auth), 400, "invalid_request")

    def test_revoking_one_grant_leaves_the_others(self, remote):
        first, second = tokens(remote), tokens(remote)
        remote.http.post("/oauth/revoke", data={"token": first["access_token"]}, auth=remote.auth)
        assert mcp(remote, first["access_token"]).status_code == 401
        assert mcp(remote, second["access_token"]).status_code == 200


class TestClient:
    def test_creating_a_client_again_revokes_every_grant_and_forgets_pending_requests(self, remote):
        body = tokens(remote)
        start(remote)
        replacement = oauth.create_client()
        assert replacement["client_id"] != remote.client_id and replacement["client_secret"] != remote.secret
        assert mcp(remote, body["access_token"]).status_code == 401
        assert sql("SELECT COUNT(*) AS n FROM mcp_oauth_requests")[0]["n"] == 0
        assert sql("SELECT COUNT(*) AS n FROM mcp_oauth_tokens")[0]["n"] == 0
        assert not oauth.authenticate_client(remote.client_id, remote.secret)
        assert oauth.authenticate_client(replacement["client_id"], replacement["client_secret"])
        assert not oauth.pairing_window_open()

    def test_authentication_needs_both_halves(self, remote):
        assert oauth.authenticate_client(remote.client_id, remote.secret)
        assert not oauth.authenticate_client(remote.client_id, remote.secret + "x")
        assert not oauth.authenticate_client("other", remote.secret)
        assert not oauth.authenticate_client("", "")

    def test_without_a_client_nobody_authenticates(self, remote):
        conn = get_connection()
        try:
            conn.execute("DELETE FROM mcp_oauth_client")
            conn.commit()
        finally:
            conn.close()
        assert not oauth.authenticate_client("", "") and not oauth.pairing_window_open()
        assert not oauth.open_pairing_window()


class TestOldRowsAreDeleted:
    def test_expired_tokens_and_requests_older_than_a_day_go_when_a_new_one_is_written(self, remote):
        tokens(remote)
        remote.clock.advance(days=2)
        oauth.open_pairing_window(30)
        start(remote)
        assert sql("SELECT COUNT(*) AS n FROM mcp_oauth_requests")[0]["n"] == 1
        assert sql("SELECT COUNT(*) AS n FROM mcp_oauth_tokens WHERE kind = 'access'")[0]["n"] == 0


class TestBehindTheHostGuard:
    @staticmethod
    def _stack(monkeypatch, address, enabled=True):
        import main
        from starlette.testclient import TestClient
        from mcp_server import PublicHostGuard
        monkeypatch.setattr(
            "mcp_server.public_host.load_config", lambda: {"MCP_REMOTE_ENABLED": enabled, "MCP_REMOTE_ALLOWED_CIDRS": ["160.79.104.0/21"]}
        )
        return TestClient(
            PublicHostGuard(main.app, public_host=PUBLIC_HOST), base_url=f"https://{PUBLIC_HOST}",
            raise_server_exceptions=False, client=(address, 50000), follow_redirects=False,
        )

    def test_the_flow_from_the_allowed_range(self, remote, monkeypatch):
        http = self._stack(monkeypatch, "160.79.104.5")
        assert http.get("/.well-known/oauth-authorization-server").status_code == 200
        assert http.get("/.well-known/oauth-protected-resource/mcp").status_code == 200
        verifier = secrets.token_urlsafe(48)
        authorized = http.get("/oauth/authorize", params=authorize_params(remote.client_id, verifier))
        assert authorized.status_code == 302
        assert oauth.approve_request(pending_request_id())
        query = parse_qs(urlsplit(oauth.claim_authorization(binding_of(authorized))).query)
        issued = http.post("/oauth/token", data=token_form(remote, query["code"][0], verifier), auth=remote.auth)
        assert issued.status_code == 200
        assert http.post("/mcp", headers={"Authorization": f"Bearer {issued.json()['access_token']}", "Accept": "application/json, text/event-stream"}, json={"jsonrpc": "2.0", "id": 1, "method": "ping"}).status_code != 401

    def test_machine_routes_from_outside_the_range_never_reach_the_handlers_but_the_browser_route_does(self, remote, monkeypatch):
        http = self._stack(monkeypatch, "203.0.113.9")
        assert http.post("/oauth/token", data={}, auth=remote.auth).status_code == 403
        assert http.get("/.well-known/oauth-authorization-server").status_code == 403
        assert http.get("/oauth/authorize", params=authorize_params(remote.client_id, "v" * 50)).status_code == 302

    def test_every_route_is_404_while_remote_access_is_off(self, remote, monkeypatch):
        http = self._stack(monkeypatch, "160.79.104.5", enabled=False)
        for method, path in (("GET", "/.well-known/oauth-authorization-server"), ("GET", "/oauth/authorize"), ("POST", "/oauth/token"), ("POST", "/oauth/revoke")):
            assert http.request(method, path).status_code == 404
