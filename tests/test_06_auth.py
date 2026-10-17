"""
tests/test_06_auth.py  ── Authentication Subsystem Tests

Full coverage of the auth surface:

  1. auth.py helpers         – token creation, HMAC verification, tamper resistance
  2. Auth middleware          – public paths, protected paths, API-key path, session-cookie path
  3. POST /api/login          – credential validation, cookie lifecycle, remember-me flag
  4. GET  /logout             – cookie deletion and redirect
  5. POST /api/change-password – all validation rules and the happy path
  6. require_step_up          – missing / expired / valid password step-up on admin endpoints
  7. Reset flows              – retired admin reset, self-service email reset, default-credential provisioning

Design notes
────────────
• `raw_client`  — session-scoped, no X-API-Key, follow_redirects=False (from conftest).
  Safe for tests that never trigger a server-set cookie.

• `_fresh_client()` — context-manager helper that yields a brand-new TestClient.
  Used for login/logout tests so that server-set session cookies can never bleed
  between test cases via the shared cookie jar.

• `client`      — session-scoped, signed in with a fresh session and CSRF cookie on every
  request (from conftest). Used when auth must pass but we are testing a different layer
  (e.g. step-up, change-password business logic). It sends no X-API-Key: key tests use
  `raw_client` with an explicit header.
"""

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import COOKIE_NAME, create_session_token, hash_password, verify_embed_token, verify_password, verify_session_token


# ── Helper ────────────────────────────────────────────────────────────────────

@contextmanager
def _fresh_client():
    """Yield a brand-new unauthenticated TestClient (no X-API-Key, redirects not followed).

    Creating a fresh client per-call ensures the cookie jar is empty, which is
    essential for login and logout tests that receive server-set session cookies.
    """
    with (
        patch("main.run_yfinance_smoke_test"),
        patch("main.start_scheduler"),
        patch("main.reload_scheduler"),
        patch("main.shutdown_scheduler"),
        patch("main.harden_runtime_permissions"),
    ):
        import main as _main_module
        with TestClient(_main_module.app, base_url="http://localhost", raise_server_exceptions=False, follow_redirects=False) as c:
            yield c


# ─────────────────────────────────────────────────────────────────────────────
# 1.  auth.py helpers  (pure unit tests, no HTTP)
# ─────────────────────────────────────────────────────────────────────────────

class TestTokenHelpers:

    def test_valid_token_roundtrip(self):
        token = create_session_token("alice", remember=False)
        assert verify_session_token(token) is True

    def test_remember_me_token_also_verifies(self):
        token = create_session_token("alice", remember=True)
        assert verify_session_token(token) is True

    def test_tampered_payload_fails_verification(self):
        token = create_session_token("alice", remember=False)
        payload, sig = token.rsplit(".", 1)
        # Corrupt the last four characters of the base64 payload
        bad_payload = payload[:-4] + "XXXX"
        assert verify_session_token(f"{bad_payload}.{sig}") is False

    def test_tampered_signature_fails_verification(self):
        token = create_session_token("alice", remember=False)
        payload, _ = token.rsplit(".", 1)
        assert verify_session_token(f"{payload}.{'0' * 64}") is False

    def test_empty_string_fails(self):
        assert verify_session_token("") is False

    def test_token_with_no_dot_separator_fails(self):
        assert verify_session_token("thishasnoseparator") is False

    def test_wrong_secret_invalidates_existing_token(self, monkeypatch):
        # Token signed with the default secret must not verify when the secret changes.
        # verify_session_token() calls _secret() on every invocation, so the env change
        # is picked up immediately without reloading the module.
        token = create_session_token("alice", remember=False)
        monkeypatch.setenv("APP_SECRET_KEY", "completely-different-secret")
        assert verify_session_token(token) is False

    def test_expired_session_token_fails(self, monkeypatch):
        # A non-remember token created 25 hours ago must be rejected (limit is 24h).
        import auth as _auth_module
        past = time.time() - (25 * 3600)
        monkeypatch.setattr(_auth_module.time, "time", lambda: past)
        token = create_session_token("alice", remember=False)
        monkeypatch.undo()
        assert verify_session_token(token) is False

    def test_recent_session_token_is_valid(self):
        # A token created moments ago must still verify (not yet expired).
        token = create_session_token("alice", remember=False)
        assert verify_session_token(token) is True

    def test_expired_remember_me_token_fails(self, monkeypatch):
        # A remember-me token created 31 days ago must be rejected (limit is 30 days).
        import auth as _auth_module
        past = time.time() - (31 * 24 * 3600)
        monkeypatch.setattr(_auth_module.time, "time", lambda: past)
        token = create_session_token("alice", remember=True)
        monkeypatch.undo()
        assert verify_session_token(token) is False


# ─────────────────────────────────────────────────────────────────────────────
# 1a. verify_embed_token  (pure unit tests, no HTTP)
# ─────────────────────────────────────────────────────────────────────────────

class TestEmbedTokenHelper:

    def test_valid_token_matches(self, monkeypatch):
        monkeypatch.setenv("EMBED_TOKEN", "correct-token")
        assert verify_embed_token("correct-token") is True

    def test_wrong_token_fails(self, monkeypatch):
        monkeypatch.setenv("EMBED_TOKEN", "correct-token")
        assert verify_embed_token("wrong-token") is False

    def test_empty_token_fails(self, monkeypatch):
        monkeypatch.setenv("EMBED_TOKEN", "correct-token")
        assert verify_embed_token("") is False

    def test_unset_env_var_fails(self, monkeypatch):
        monkeypatch.delenv("EMBED_TOKEN", raising=False)
        assert verify_embed_token("anything") is False


# ─────────────────────────────────────────────────────────────────────────────
# 1b. hash_password / verify_password  (pure unit tests, no HTTP)
# ─────────────────────────────────────────────────────────────────────────────

class TestPasswordHashing:

    def test_hash_format_is_pbkdf2_sha256(self):
        h = hash_password("mypassword")
        parts = h.split(":")
        assert len(parts) == 5
        assert parts[0] == "pbkdf2"
        assert parts[1] == "sha256"

    def test_each_hash_uses_a_unique_salt(self):
        h1 = hash_password("mypassword")
        h2 = hash_password("mypassword")
        assert h1 != h2, "Two calls with the same password must produce different hashes"

    def test_verify_correct_password_returns_true(self):
        h = hash_password("correctpassword")
        assert verify_password("correctpassword", h) is True

    def test_verify_wrong_password_returns_false(self):
        h = hash_password("correctpassword")
        assert verify_password("wrongpassword", h) is False

    def test_verify_is_case_sensitive(self):
        h = hash_password("MyPassword")
        assert verify_password("mypassword", h) is False

    def test_verify_empty_password_against_nonempty_hash(self):
        h = hash_password("somepass")
        assert verify_password("", h) is False

    def test_verify_malformed_hash_returns_false(self):
        assert verify_password("pass", "not:a:valid:hash") is False
        assert verify_password("pass", "") is False

    def test_verify_invalid_hex_in_hash_returns_false(self):
        assert verify_password("pass", "pbkdf2:sha256:600000:NOTVALIDHEX:NOTVALIDHEX") is False

    def test_verify_wrong_algorithm_prefix_returns_false(self):
        h = hash_password("mypassword")
        bad = "bcrypt:" + ":".join(h.split(":")[1:])
        assert verify_password("mypassword", bad) is False


# ─────────────────────────────────────────────────────────────────────────────
# 2.  Auth middleware
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthMiddleware:

    # ── Public paths ──────────────────────────────────────────────────────────

    def test_login_page_is_public(self, raw_client):
        resp = raw_client.get("/login")
        assert resp.status_code == 200

    @pytest.mark.parametrize("query,expected", [
        ("/portfolio", "/portfolio"),
        ("/stock/AAPL?embed=true", "/stock/AAPL?embed=true"),
        ("%2F%09%2Fevil.example", "/"),
        ("%2F%0A%2Fevil.example", "/"),
        ("%2F%2Fevil.example", "/"),
        ("%2F%5Cevil.example", "/"),
        ("javascript:alert(1)", "/"),
    ])
    def test_login_page_only_keeps_same_origin_next(self, raw_client, query, expected):
        import json as _json
        resp = raw_client.get(f"/login?next={query}")
        assert resp.status_code == 200
        assert f"const nextUrl = {_json.dumps(expected)};" in resp.text

    def test_api_login_endpoint_is_exempt_from_middleware(self, raw_client):
        # Wrong credentials → 401 from the endpoint, not 302 from the middleware.
        resp = raw_client.post("/api/login", json={
            "username": "nobody", "password": "nobody", "remember_me": False
        })
        assert resp.status_code == 401, (
            f"/api/login must be reachable without a session; got {resp.status_code}"
        )

    def test_static_files_bypass_auth(self, raw_client):
        resp = raw_client.get("/static/css/styles.css")
        assert resp.status_code != 302, (
            "Requests under /static/ must never be redirected to /login"
        )

    # ── Protected paths — unauthenticated ────────────────────────────────────

    def test_protected_page_redirects_to_login(self, raw_client):
        resp = raw_client.get("/portfolio")
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

    def test_protected_api_redirects_to_login(self, raw_client):
        resp = raw_client.get("/api/notifications/latest")
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

    def test_redirect_preserves_destination_in_next_param(self, raw_client):
        resp = raw_client.get("/api/screener/data")
        location = resp.headers.get("location", "")
        assert "next=" in location, (
            f"Auth redirect must carry a ?next= parameter; got location: {location}"
        )

    # ── API-key path ──────────────────────────────────────────────────────────

    def test_valid_api_key_grants_access_to_an_allowed_route(self, raw_client):
        key = os.environ["API_KEY"]
        resp = raw_client.get("/api/market-regime/current", headers={"X-API-Key": key})
        assert resp.status_code == 200

    def test_invalid_api_key_returns_401(self, raw_client):
        resp = raw_client.get("/api/market-regime/current", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401

    def test_api_key_with_env_var_unset_returns_401(self, raw_client, monkeypatch):
        # When API_KEY is not configured, no key value can succeed.
        monkeypatch.delenv("API_KEY", raising=False)
        resp = raw_client.get("/api/market-regime/current", headers={"X-API-Key": "any-key"})
        assert resp.status_code == 401

    @pytest.mark.parametrize("method,path", [
        ("GET", "/api/notifications/latest"),
        ("GET", "/api/system/checks"),
        ("GET", "/api/logs/tail"),
        ("GET", "/settings"),
        ("GET", "/portfolio"),
        ("GET", "/"),
        ("POST", "/api/accounts"),
        ("POST", "/api/step-up"),
        ("POST", "/api/settings"),
        ("POST", "/api/system/restart"),
        ("POST", "/api/accounts/portfolio-totals"),
        ("GET", "/api/accounts/refresh-now"),
        ("DELETE", "/api/accounts/holding-price-limit"),
    ])
    def test_valid_api_key_is_refused_outside_the_home_assistant_routes(self, raw_client, method, path):
        resp = raw_client.request(method, path, headers={"X-API-Key": os.environ["API_KEY"]})
        assert resp.status_code == 403, path

    def test_session_cookie_still_reaches_routes_the_api_key_cannot(self, client):
        assert client.get("/api/notifications/latest").status_code == 200

    def test_api_key_routes_are_registered_routes(self):
        from starlette.routing import Match
        import main
        from api_deps import API_KEY_ROUTES
        for method, path in sorted(API_KEY_ROUTES):
            scope = {"type": "http", "method": method, "path": path, "root_path": "", "query_string": b"", "headers": []}
            assert any(r.matches(scope)[0] == Match.FULL for r in main.app.routes), (method, path)

    def test_api_key_routes_match_the_documented_table(self):
        import re
        from api_deps import API_KEY_ROUTES
        doc = (Path(__file__).parent.parent / "assets" / "api_reference.md").read_text()
        start = doc.index("### API key scope")
        section = doc[start:doc.index("\n### ", start + 1)]
        rows = set(re.findall(r"^\| `(GET|POST|PUT|PATCH|DELETE)` \| `([^`]+)` \|", section, re.M))
        assert rows == set(API_KEY_ROUTES)

    # ── Embed-token path ───────────────────────────────────────────────────────

    def test_valid_embed_token_grants_access_to_portfolio(self, raw_client, monkeypatch):
        monkeypatch.setenv("EMBED_TOKEN", "embed-secret")
        resp = raw_client.get("/portfolio?embed=true&embed_token=embed-secret")
        assert resp.status_code == 200

    def test_valid_embed_token_grants_access_to_watchlist(self, raw_client, monkeypatch):
        monkeypatch.setenv("EMBED_TOKEN", "embed-secret")
        resp = raw_client.get("/watchlist?embed=true&embed_token=embed-secret")
        assert resp.status_code == 200

    def test_embed_token_without_embed_flag_still_redirects(self, raw_client, monkeypatch):
        monkeypatch.setenv("EMBED_TOKEN", "embed-secret")
        resp = raw_client.get("/portfolio?embed_token=embed-secret")
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

    def test_wrong_embed_token_still_redirects(self, raw_client, monkeypatch):
        monkeypatch.setenv("EMBED_TOKEN", "embed-secret")
        resp = raw_client.get("/portfolio?embed=true&embed_token=wrong-token")
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

    def test_embed_token_does_not_bypass_non_embed_paths(self, raw_client, monkeypatch):
        monkeypatch.setenv("EMBED_TOKEN", "embed-secret")
        resp = raw_client.get("/api/notifications/latest?embed=true&embed_token=embed-secret")
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

    def test_embed_token_does_not_bypass_non_get_methods(self, raw_client, monkeypatch):
        monkeypatch.setenv("EMBED_TOKEN", "embed-secret")
        resp = raw_client.post("/api/generate-embed-token?embed=true&embed_token=embed-secret")
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

    # ── Session-cookie path ───────────────────────────────────────────────────

    def test_valid_session_cookie_grants_access(self, raw_client):
        token = create_session_token("alice", remember=False)
        resp = raw_client.get("/api/notifications/latest", cookies={COOKIE_NAME: token})
        assert resp.status_code == 200

    def test_tampered_session_cookie_redirects_to_login(self, raw_client):
        token = create_session_token("alice", remember=False)
        # Corrupt the tail so the HMAC check fails
        bad_token = token[:-8] + "XXXXXXXX"
        resp = raw_client.get("/api/notifications/latest", cookies={COOKIE_NAME: bad_token})
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

    def test_absent_session_cookie_redirects_to_login(self, raw_client):
        resp = raw_client.get("/api/notifications/latest")  # no cookie at all
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]


# ─────────────────────────────────────────────────────────────────────────────
# 3.  POST /api/login
# ─────────────────────────────────────────────────────────────────────────────

class TestLoginEndpoint:
    """Each test uses _fresh_client() to get an isolated cookie jar."""

    def test_valid_credentials_return_200(self, test_username, test_password):
        with _fresh_client() as c:
            resp = c.post("/api/login", json={
                "username": test_username,
                "password": test_password,
                "remember_me": False,
            })
        assert resp.status_code == 200
        assert resp.json().get("status") == "ok"

    def test_valid_login_sets_session_cookie(self, test_username, test_password):
        with _fresh_client() as c:
            resp = c.post("/api/login", json={
                "username": test_username,
                "password": test_password,
                "remember_me": False,
            })
        assert COOKIE_NAME in resp.cookies, (
            "A successful login must set the session cookie in the response"
        )

    def test_session_token_passes_hmac_verification(self, test_username, test_password):
        with _fresh_client() as c:
            resp = c.post("/api/login", json={
                "username": test_username,
                "password": test_password,
                "remember_me": False,
            })
        token = resp.cookies.get(COOKIE_NAME, "").strip('"')
        assert token, "Login response must contain a non-empty session cookie"
        assert verify_session_token(token), (
            "The cookie value must be a properly signed HMAC token"
        )

    def test_wrong_username_returns_401(self, test_password):
        with _fresh_client() as c:
            resp = c.post("/api/login", json={
                "username": "nobody",
                "password": test_password,
                "remember_me": False,
            })
        assert resp.status_code == 401

    def test_wrong_password_returns_401(self, test_username):
        with _fresh_client() as c:
            resp = c.post("/api/login", json={
                "username": test_username,
                "password": "wrongpassword",
                "remember_me": False,
            })
        assert resp.status_code == 401

    def test_both_wrong_returns_401(self):
        with _fresh_client() as c:
            resp = c.post("/api/login", json={
                "username": "nobody",
                "password": "wrongpassword",
                "remember_me": False,
            })
        assert resp.status_code == 401

    def test_remember_me_true_sets_max_age_on_cookie(self, test_username, test_password):
        with _fresh_client() as c:
            resp = c.post("/api/login", json={
                "username": test_username,
                "password": test_password,
                "remember_me": True,
            })
        set_cookie = resp.headers.get("set-cookie", "")
        assert "max-age" in set_cookie.lower(), (
            f"remember_me=True must produce a persistent cookie (Max-Age header); "
            f"Set-Cookie was: {set_cookie}"
        )

    def test_remember_me_false_omits_max_age(self, test_username, test_password):
        with _fresh_client() as c:
            resp = c.post("/api/login", json={
                "username": test_username,
                "password": test_password,
                "remember_me": False,
            })
        set_cookie = resp.headers.get("set-cookie", "")
        assert "max-age" not in set_cookie.lower(), (
            f"remember_me=False must produce a session cookie (no Max-Age); "
            f"Set-Cookie was: {set_cookie}"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 4.  GET /logout
# ─────────────────────────────────────────────────────────────────────────────

class TestLogout:

    def test_logout_redirects_to_login(self):
        token = create_session_token("alice", remember=False)
        with _fresh_client() as c:
            resp = c.get("/logout", cookies={COOKIE_NAME: token})
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]

    def test_logout_expires_session_cookie(self):
        token = create_session_token("alice", remember=False)
        with _fresh_client() as c:
            resp = c.get("/logout", cookies={COOKIE_NAME: token})
        set_cookie = resp.headers.get("set-cookie", "")
        assert COOKIE_NAME in set_cookie, (
            "Logout must emit a Set-Cookie header for the session key"
        )
        assert "max-age=0" in set_cookie.lower(), (
            f"Logout must expire the cookie immediately (Max-Age=0); got: {set_cookie}"
        )

    def test_after_logout_subsequent_request_is_unauthenticated(self):
        # Within the same client session: after the server clears the cookie via
        # Max-Age=0, the TestClient removes it from its jar. A following protected
        # request must therefore be redirected to /login again.
        token = create_session_token("alice", remember=False)
        with _fresh_client() as c:
            c.get("/logout", cookies={COOKIE_NAME: token})
            # At this point the server has told the client to clear the cookie.
            follow_resp = c.get("/api/notifications/latest")
        assert follow_resp.status_code == 302, (
            "After logout the cookie jar must be empty; protected routes must redirect again"
        )


# ─────────────────────────────────────────────────────────────────────────────
# 5.  POST /api/change-password
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _reset_rate_limits():
    from api_deps import limiter
    limiter.reset()


def _clear_login_throttle():
    from database import get_connection
    conn = None
    try:
        conn = get_connection()
        conn.execute("DELETE FROM login_throttle")
        conn.commit()
    finally:
        if conn:
            conn.close()


@pytest.fixture(autouse=True)
def _reset_login_throttle_and_credentials():
    names = ("SESSION_EPOCH", "DASHBOARD_PASSWORD", "DASHBOARD_PASSWORD_HASH")
    previous = {name: os.environ.get(name) for name in names}
    _clear_login_throttle()
    yield
    _clear_login_throttle()
    for name, value in previous.items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value


class TestChangePassword:
    """Uses the api-key-authenticated `client` so middleware is bypassed;
    only the change-password business logic is under test. The current password in
    the body is this route's re-authentication, so it needs no step-up cookie."""

    def test_wrong_current_password_returns_400(self, client, test_password):
        resp = client.post("/api/change-password", json={
            "current_password": "definitely-not-the-right-password",
            "new_password": "brandnewpass99",
            "confirm_password": "brandnewpass99",
        })
        assert resp.status_code == 400
        assert "incorrect" in resp.json().get("detail", "").lower()

    def test_mismatched_new_passwords_return_400(self, client, test_password):
        resp = client.post("/api/change-password", json={
            "current_password": test_password,
            "new_password": "newpassword456",
            "confirm_password": "differentpass456",
        })
        assert resp.status_code == 400
        assert "match" in resp.json().get("detail", "").lower()

    def test_password_shorter_than_12_chars_returns_400(self, client, test_password):
        resp = client.post("/api/change-password", json={
            "current_password": test_password,
            "new_password": "elevenchars",
            "confirm_password": "elevenchars",
        })
        assert resp.status_code == 400
        assert "12" in resp.json().get("detail", "")

    def test_common_password_returns_400(self, client, test_password):
        resp = client.post("/api/change-password", json={
            "current_password": test_password,
            "new_password": "Password123456",
            "confirm_password": "Password123456",
        })
        assert resp.status_code == 400
        assert "common" in resp.json().get("detail", "").lower()

    def test_changeme_password_is_rejected(self, client, test_password):
        resp = client.post("/api/change-password", json={
            "current_password": test_password,
            "new_password": "changeme",
            "confirm_password": "changeme",
        })
        assert resp.status_code == 400

    def test_valid_password_change_returns_200(self, client, test_password):
        original = os.environ.get("DASHBOARD_PASSWORD", test_password)
        try:
            with patch("dotenv.set_key"):  # prevent writing to the real .env file
                resp = client.post("/api/change-password", json={
                    "current_password": original,
                    "new_password": "brandnewpass99",
                    "confirm_password": "brandnewpass99",
                })
            assert resp.status_code == 200
            assert resp.json().get("status") == "ok"
        finally:
            os.environ["DASHBOARD_PASSWORD"] = original
            os.environ.pop("DASHBOARD_PASSWORD_HASH", None)

    def test_repeated_wrong_guesses_are_rate_limited(self, client):
        body = {"current_password": "wrong", "new_password": "brandnewpass99", "confirm_password": "brandnewpass99"}
        codes = [client.post("/api/change-password", json=body).status_code for _ in range(6)]
        assert codes[:5] == [400] * 5
        assert codes[5] == 429


# ─────────────────────────────────────────────────────────────────────────────
# 6.  Password step-up  (POST /api/step-up + require_step_up on admin endpoints)
# ─────────────────────────────────────────────────────────────────────────────

_GATED_ROUTES = [
    "/api/settings",
    "/api/system/git-pull",
    "/api/system/restart",
    "/api/system/force-restart",
    "/api/system/terminate-jobs",
    "/api/backup/restore",
    "/api/generate-api-key",
    "/api/generate-embed-token",
    "/api/mcp-keys",
    "/api/save-account-email",
    "/api/save-nextcloud-settings",
    "/api/test-nextcloud-message",
    "/api/save-ghostfolio-settings",
    "/api/save-fred-api-key",
    "/api/save-hf-token",
    "/api/test-hf-token",
    "/api/change-username",
    "/api/rotate-app-secret",
    "/api/logout-everywhere",
    "/api/save-smtp-settings",
    "/api/send-test-email",
]


class TestCheckDashboardPassword:

    def test_hash_takes_precedence_over_the_plaintext_value(self):
        from auth import check_dashboard_password
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD_HASH": hash_password("hashed-secret"), "DASHBOARD_PASSWORD": "plain"}):
            assert check_dashboard_password("hashed-secret") is True
            assert check_dashboard_password("plain") is False

    def test_empty_plaintext_value_never_authenticates(self):
        from auth import check_dashboard_password
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD_HASH": "", "DASHBOARD_PASSWORD": ""}):
            assert check_dashboard_password("") is False
            assert check_dashboard_password("changeme") is False

    def test_falls_back_to_the_plaintext_value_when_no_hash_is_stored(self):
        from auth import check_dashboard_password
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD_HASH": "", "DASHBOARD_PASSWORD": "plain-secret"}):
            assert check_dashboard_password("plain-secret") is True
            assert check_dashboard_password("other") is False


class TestStepUpTokens:

    def test_fresh_token_is_valid_for_its_session(self):
        from auth import create_step_up_token, verify_step_up_token
        session = create_session_token("alice", remember=False)
        assert verify_step_up_token(create_step_up_token(session), session) is True

    def test_token_is_rejected_for_a_different_session(self):
        from auth import create_step_up_token, verify_step_up_token
        first = create_session_token("alice", remember=False)
        second = create_session_token("alice", remember=True)
        assert verify_step_up_token(create_step_up_token(first), second) is False

    def test_token_expires_after_five_minutes(self):
        from auth import STEP_UP_MAX_AGE, create_step_up_token, verify_step_up_token
        session = create_session_token("alice", remember=False)
        token = create_step_up_token(session)
        with patch("auth.time.time", return_value=time.time() + STEP_UP_MAX_AGE + 1):
            assert verify_step_up_token(token, session) is False

    def test_tampered_token_is_rejected(self):
        from auth import create_step_up_token, verify_step_up_token
        session = create_session_token("alice", remember=False)
        payload, sig = create_step_up_token(session).rsplit(".", 1)
        assert verify_step_up_token(payload + "." + sig[:-1] + ("0" if sig[-1] != "0" else "1"), session) is False

    def test_session_token_cannot_stand_in_for_a_step_up_token(self):
        from auth import verify_step_up_token
        session = create_session_token("alice", remember=False)
        assert verify_step_up_token(session, session) is False

    def test_step_up_token_is_not_a_session_token(self):
        from auth import create_step_up_token
        session = create_session_token("alice", remember=False)
        assert verify_session_token(create_step_up_token(session)) is False

    def test_rotating_app_secret_invalidates_the_token(self, monkeypatch):
        from auth import create_step_up_token, verify_step_up_token
        session = create_session_token("alice", remember=False)
        token = create_step_up_token(session)
        monkeypatch.setenv("APP_SECRET_KEY", "a-different-secret")
        assert verify_step_up_token(token, session) is False


class TestStepUpRoutes:

    @pytest.mark.parametrize("path", _GATED_ROUTES)
    def test_gated_route_without_step_up_returns_403_and_flags_the_prompt(self, client, path):
        resp = client.post(path, json={})
        assert resp.status_code == 403
        assert resp.headers.get("X-Step-Up-Required") == "1"

    def test_session_without_step_up_cookie_is_refused(self, client, auth_cookies):
        headers = {
            "Cookie": "; ".join(f"{k}={v}" for k, v in auth_cookies.items()),
            "X-CSRFToken": auth_cookies["csrftoken"],
        }
        assert client.post("/api/save-account-email", json={"email": "a@b.c"}, headers=headers).status_code == 403

    def test_gated_route_runs_with_a_valid_step_up(self, client, step_up_headers):
        with patch("dotenv.set_key"), patch.dict(os.environ, {"ACCOUNT_EMAIL": ""}):
            resp = client.post("/api/save-account-email", json={"email": "a@b.c"}, headers=step_up_headers)
        assert resp.status_code == 200

    def test_expired_step_up_cookie_is_refused(self, client, step_up_headers):
        from auth import STEP_UP_MAX_AGE
        with patch("auth.time.time", return_value=time.time() + STEP_UP_MAX_AGE + 1):
            resp = client.post("/api/save-account-email", json={"email": "a@b.c"}, headers=step_up_headers)
        assert resp.status_code == 403

    def test_api_key_alone_cannot_step_up(self, raw_client, test_password):
        resp = raw_client.post(
            "/api/step-up", json={"password": test_password}, headers={"X-API-Key": os.environ["API_KEY"]}
        )
        assert resp.status_code == 403

    def test_wrong_password_is_refused_and_sets_no_cookie(self, test_password, auth_cookies):
        with _fresh_client() as c:
            c.cookies.update(auth_cookies)
            resp = c.post("/api/step-up", json={"password": "not-the-password"},
                          headers={"X-CSRFToken": auth_cookies["csrftoken"]})
        assert resp.status_code == 401
        assert "stepup=" not in resp.headers.get("set-cookie", "")

    def test_correct_password_sets_a_scoped_httponly_cookie_that_unlocks_gated_routes(
        self, test_password, auth_cookies
    ):
        headers = {"X-CSRFToken": auth_cookies["csrftoken"]}
        with _fresh_client() as c:
            c.cookies.update(auth_cookies)
            resp = c.post("/api/step-up", json={"password": test_password}, headers=headers)
            assert resp.status_code == 200
            set_cookie = resp.headers.get("set-cookie", "").lower()
            assert "stepup=" in set_cookie
            assert "httponly" in set_cookie
            assert "path=/api" in set_cookie
            assert "max-age=300" in set_cookie
            assert "samesite=strict" in set_cookie
            with patch("dotenv.set_key"), patch.dict(os.environ, {"ACCOUNT_EMAIL": ""}):
                follow = c.post("/api/save-account-email", json={"email": "a@b.c"}, headers=headers)
        assert follow.status_code == 200

    def test_logout_clears_the_step_up_cookie(self):
        with _fresh_client() as c:
            resp = c.get("/logout", cookies={COOKIE_NAME: create_session_token("alice", remember=False)})
        cleared = [v for k, v in resp.headers.multi_items() if k.lower() == "set-cookie" and v.startswith("stepup=")]
        assert cleared and "max-age=0" in cleared[0].lower()
        assert "path=/api" in cleared[0].lower()

    def test_change_password_page_does_not_render_a_token(self, client):
        html = client.get("/change-password").text
        assert "X-Confirm-Token" not in html

    def test_settings_page_does_not_render_a_confirm_token(self, client):
        html = client.get("/settings").text
        assert "CONFIRM_TOKEN" not in html
        assert "X-Confirm-Token" not in html
        assert 'id="stepUpModal"' in html

    @pytest.mark.parametrize("endpoint, base, field, clear_field, env_name", [
        ("/api/save-hf-token", {}, "HF_TOKEN", "CLEAR_HF_TOKEN", "HF_TOKEN"),
        ("/api/save-fred-api-key", {}, "FRED_API_KEY", "CLEAR_FRED_API_KEY", "FRED_API_KEY"),
        ("/api/save-ghostfolio-settings", {"GHOSTFOLIO_URL": "http://gf.test"}, "GHOSTFOLIO_TOKEN",
         "CLEAR_GHOSTFOLIO_TOKEN", "GHOSTFOLIO_TOKEN"),
        ("/api/save-nextcloud-settings", {"NEXTCLOUD_URL": "http://nc.test", "BOT_USERNAME": "bot"},
         "APP_PASSWORD", "CLEAR_APP_PASSWORD", "NEXTCLOUD_APP_PASSWORD"),
        ("/api/save-nextcloud-settings", {"NEXTCLOUD_URL": "http://nc.test", "BOT_USERNAME": "bot"},
         "CONVERSATION_TOKEN", "CLEAR_CONVERSATION_TOKEN", "NEXTCLOUD_CONVERSATION_TOKEN"),
        ("/api/save-smtp-settings", {"smtp_host": "smtp.test", "smtp_port": "587", "smtp_user": "u", "smtp_from": "f"},
         "smtp_pass", "clear_smtp_pass", "SMTP_PASS"),
    ])
    def test_secret_save_blank_keeps_value_and_clear_flag_empties_it(
        self, client, step_up_headers, endpoint, base, field, clear_field, env_name
    ):
        headers = step_up_headers
        with patch("dotenv.set_key") as set_key, patch.dict(os.environ, {env_name: "stored-secret"}):
            assert client.post(endpoint, json={**base, field: ""}, headers=headers).status_code == 200
            assert client.post(endpoint, json={**base, field: "   "}, headers=headers).status_code == 200
            assert client.post(endpoint, json=base, headers=headers).status_code == 200
            assert os.environ[env_name] == "stored-secret"
            assert env_name not in [call.args[1] for call in set_key.call_args_list]

            assert client.post(endpoint, json={**base, field: "replacement"}, headers=headers).status_code == 200
            assert os.environ[env_name] == "replacement"

            assert client.post(endpoint, json={**base, clear_field: True}, headers=headers).status_code == 200
            assert os.environ[env_name] == ""

    def test_generate_api_key_with_step_up_returns_a_key(self, client, step_up_headers):
        with patch("dotenv.set_key"), patch.dict(os.environ, {}, clear=False):
            resp = client.post("/api/generate-api-key", headers=step_up_headers)
        assert resp.status_code == 200
        assert len(resp.json()["api_key"]) == 64

    def test_generate_embed_token_with_step_up_returns_a_token(self, client, step_up_headers):
        with patch("dotenv.set_key"), patch.dict(os.environ, {}, clear=False):
            resp = client.post("/api/generate-embed-token", headers=step_up_headers)
        assert resp.status_code == 200
        assert len(resp.json()["embed_token"]) == 64

    def test_settings_update_with_step_up_succeeds(self, client, step_up_headers):
        resp = client.post("/api/settings", json={"UI_PREFERENCES": {"REFRESH_RATE": 60}}, headers=step_up_headers)
        assert resp.status_code == 200
        assert resp.json().get("status") == "success"

    def test_test_hf_token_without_value_uses_stored_token(self, client, step_up_headers):
        with patch.dict(os.environ, {"HF_TOKEN": "stored-hf"}), \
                patch("requests.get", return_value=MagicMock(json=lambda: {"name": "someone"})) as get:
            resp = client.post("/api/test-hf-token", json={}, headers=step_up_headers)
        assert resp.status_code == 200
        assert "someone" in resp.json()["message"]
        assert get.call_args.kwargs["headers"]["Authorization"] == "Bearer stored-hf"

    def test_test_hf_token_reports_a_rejected_token(self, client, step_up_headers):
        import requests
        rejected = MagicMock()
        rejected.raise_for_status.side_effect = requests.HTTPError("401 Client Error: Unauthorized", response=MagicMock(status_code=401))
        with patch("requests.get", return_value=rejected):
            resp = client.post("/api/test-hf-token", json={"HF_TOKEN": "bad"}, headers=step_up_headers)
        assert resp.status_code == 400
        assert "Token verification failed" in resp.json()["detail"]
        assert "401 Client Error" not in resp.json()["detail"]
        assert "rejected" in resp.json()["detail"]

    def test_test_hf_token_hides_connection_error_text(self, client, step_up_headers):
        import requests
        with patch("requests.get", side_effect=requests.ConnectionError("internal-proxy.lan:3128 refused")):
            resp = client.post("/api/test-hf-token", json={"HF_TOKEN": "x"}, headers=step_up_headers)
        assert resp.status_code == 400
        assert "internal-proxy" not in resp.json()["detail"]

    def test_send_test_email_hides_smtp_error_text(self, client, step_up_headers):
        env = {"SMTP_HOST": "mail.lan", "SMTP_PORT": "2525", "ACCOUNT_EMAIL": "me@example.com"}
        with patch.dict(os.environ, env), \
                patch("api_routes_auth._smtp_send", side_effect=ConnectionRefusedError("[Errno 111] mail.lan:2525")):
            resp = client.post("/api/send-test-email", headers=step_up_headers)
        assert resp.status_code == 500
        assert "Errno" not in resp.json()["detail"] and "mail.lan" not in resp.json()["detail"]


# ─────────────────────────────────────────────────────────────────────────────
# 7.  _secret() auto-generation when APP_SECRET_KEY is missing
# ─────────────────────────────────────────────────────────────────────────────

class TestSecretGeneration:

    def test_returns_valid_bytes_when_unset(self, monkeypatch):
        monkeypatch.delenv("APP_SECRET_KEY", raising=False)
        with patch("dotenv.set_key"):
            from auth import _secret
            result = _secret()
        assert isinstance(result, bytes)
        assert len(result) == 64  # token_hex(32) → 64 hex chars
        assert result != b"fallback-insecure"

    def test_sets_env_var_after_generation(self, monkeypatch):
        monkeypatch.delenv("APP_SECRET_KEY", raising=False)
        with patch("dotenv.set_key"):
            from auth import _secret
            _secret()
        assert os.environ.get("APP_SECRET_KEY"), "APP_SECRET_KEY must be cached in env after _secret()"

    def test_calls_dotenv_set_key_to_persist(self, monkeypatch):
        monkeypatch.delenv("APP_SECRET_KEY", raising=False)
        with patch("dotenv.set_key") as mock_set:
            from auth import _secret
            _secret()
        mock_set.assert_called_once()

    def test_graceful_when_dotenv_raises(self, monkeypatch):
        monkeypatch.delenv("APP_SECRET_KEY", raising=False)
        with patch("dotenv.set_key", side_effect=OSError("permission denied")):
            from auth import _secret
            result = _secret()
        assert isinstance(result, bytes)
        assert len(result) == 64

    def test_idempotent_second_call_returns_same_key(self, monkeypatch):
        monkeypatch.delenv("APP_SECRET_KEY", raising=False)
        with patch("dotenv.set_key"):
            from auth import _secret
            first = _secret()
            second = _secret()
        assert first == second


# ─────────────────────────────────────────────────────────────────────────────
# 8.  Reset flows  (S1-5 retired the admin reset; S1-6 hardened the email reset)
# ─────────────────────────────────────────────────────────────────────────────

_RESET_PASSWORD = "A-brand-new-passphrase-1"


def _request_reset(c, email="owner@example.com"):
    return c.post("/api/request-password-reset", json={"email": email})


def _complete_reset(c, token, password=_RESET_PASSWORD):
    return c.post("/api/reset-password", json={"token": token, "new_password": password, "confirm_password": password})


def _token_from(url: str) -> str:
    return url.split("token=", 1)[1]


class TestAdminResetRetired:

    def test_flag_is_not_a_config_default(self):
        from config import DEFAULT_CONFIG
        assert "FORCE_PASSWORD_RESET" not in DEFAULT_CONFIG

    @pytest.mark.parametrize("method,path", [
        ("get", "/admin-reset-password"),
        ("post", "/api/admin-reset-password"),
        ("get", "/change-password"),
        ("post", "/api/change-password"),
    ])
    def test_stale_flag_in_config_grants_no_unauthenticated_access(self, raw_client, method, path):
        with patch("config.load_config", return_value={"FORCE_PASSWORD_RESET": True}):
            resp = getattr(raw_client, method)(path)
        assert resp.status_code == 302
        assert resp.headers["location"].startswith("/login")

    def test_admin_reset_route_is_gone_for_an_authenticated_caller(self, client):
        resp = client.post(
            "/api/admin-reset-password",
            json={"new_password": _RESET_PASSWORD, "confirm_password": _RESET_PASSWORD},
        )
        assert resp.status_code in (404, 405)


class TestSelfServiceReset:

    @pytest.fixture(autouse=True)
    def _account_email_and_tokens(self):
        from database import get_connection
        with patch.dict(os.environ, {"ACCOUNT_EMAIL": "owner@example.com"}):
            yield
        conn = None
        try:
            conn = get_connection()
            conn.execute("DELETE FROM password_reset_tokens")
            conn.commit()
        finally:
            if conn:
                conn.close()

    def test_reply_is_identical_whether_or_not_the_email_matches(self):
        with _fresh_client() as c, patch("api_routes_auth._send_reset_notification", return_value=True):
            matching = _request_reset(c)
            other = _request_reset(c, "someone-else@example.com")
        assert matching.status_code == other.status_code == 200
        assert matching.json() == other.json()

    def test_reply_is_identical_when_delivery_fails(self):
        with _fresh_client() as c:
            with patch("api_routes_auth._send_reset_notification", return_value=True):
                delivered = _request_reset(c)
            with patch("api_routes_auth._send_reset_notification", return_value=False):
                failed = _request_reset(c)
        assert delivered.json() == failed.json()
        assert "log" not in failed.json()["message"].lower()

    def test_non_matching_email_issues_and_sends_nothing(self):
        with _fresh_client() as c, patch("api_routes_auth._send_reset_notification") as send:
            _request_reset(c, "someone-else@example.com")
        send.assert_not_called()

    def test_failed_delivery_never_writes_the_link_to_the_logs(self, caplog):
        sent = []
        with (
            _fresh_client() as c,
            patch("api_routes_auth._send_reset_notification", side_effect=lambda url: sent.append(url) or False),
            caplog.at_level("DEBUG"),
        ):
            _request_reset(c)
        assert len(sent) == 1
        assert _token_from(sent[0]) not in caplog.text
        assert "/reset-password?token=" not in caplog.text
        assert "could not be delivered" in caplog.text

    def test_a_new_request_invalidates_the_older_link(self):
        sent = []
        with (
            _fresh_client() as c,
            patch("api_routes_auth._send_reset_notification", side_effect=lambda url: sent.append(url) or True),
            patch("dotenv.set_key"),
        ):
            _request_reset(c)
            _request_reset(c)
            stale = _complete_reset(c, _token_from(sent[0]))
            current = _complete_reset(c, _token_from(sent[1]))
        assert stale.status_code == 400
        assert current.status_code == 200

    def test_a_valid_link_is_single_use_and_signs_out_other_sessions(self):
        sent = []
        before = create_session_token("alice", remember=False)
        with (
            _fresh_client() as c,
            patch("api_routes_auth._send_reset_notification", side_effect=lambda url: sent.append(url) or True),
            patch("dotenv.set_key"),
        ):
            _request_reset(c)
            first = _complete_reset(c, _token_from(sent[0]))
            second = _complete_reset(c, _token_from(sent[0]))
        assert first.status_code == 200
        assert second.status_code == 400
        assert verify_session_token(before) is False

    def test_request_route_is_rate_limited_to_three_per_hour(self):
        with _fresh_client() as c, patch("api_routes_auth._send_reset_notification", return_value=True):
            codes = [_request_reset(c).status_code for _ in range(4)]
        assert codes == [200, 200, 200, 429]

    def test_reset_route_is_rate_limited_to_ten_per_hour(self):
        with _fresh_client() as c:
            codes = [_complete_reset(c, "not-a-real-token").status_code for _ in range(11)]
        assert codes == [400] * 10 + [429]


class TestDefaultCredentialProvisioning:

    @staticmethod
    def _provision(monkeypatch, **env):
        import config
        for name in ("DASHBOARD_USERNAME", "DASHBOARD_PASSWORD", "DASHBOARD_PASSWORD_HASH"):
            monkeypatch.delenv(name, raising=False)
        for name, value in env.items():
            monkeypatch.setenv(name, value)
        with patch("dotenv.set_key") as set_key:
            config._provision_default_credentials()
        return {call.args[1]: call.args[2] for call in set_key.call_args_list}

    def test_fresh_install_gets_the_default_login(self, monkeypatch):
        written = self._provision(monkeypatch)
        assert written == {"DASHBOARD_USERNAME": "admin", "DASHBOARD_PASSWORD": "changeme"}

    def test_cleared_plaintext_key_is_not_reverted_after_a_password_change(self, monkeypatch):
        written = self._provision(
            monkeypatch, DASHBOARD_USERNAME="admin", DASHBOARD_PASSWORD="", DASHBOARD_PASSWORD_HASH="pbkdf2:sha256:1:aa:bb",
        )
        assert written == {}

    def test_lost_hash_does_not_bring_the_default_password_back(self, monkeypatch):
        written = self._provision(monkeypatch, DASHBOARD_USERNAME="admin", DASHBOARD_PASSWORD="")
        assert written == {}

    def test_stored_hash_without_a_plaintext_key_is_left_alone(self, monkeypatch):
        written = self._provision(
            monkeypatch, DASHBOARD_USERNAME="admin", DASHBOARD_PASSWORD_HASH="pbkdf2:sha256:1:aa:bb",
        )
        assert written == {}


class TestResetDelivery:

    def test_nextcloud_delivery_sends_the_link_when_smtp_is_not_configured(self):
        from api_routes_auth import _send_reset_notification
        with (
            patch.dict(os.environ, {"SMTP_HOST": "", "NEXTCLOUD_URL": "https://cloud.example"}),
            patch("nextcloud_talk.send_text_message", return_value=True) as send,
        ):
            assert _send_reset_notification("http://host:8090/reset-password?token=abc") is True
        assert "reset-password?token=abc" in send.call_args[0][0]

    def test_nextcloud_failure_reports_undelivered(self):
        from api_routes_auth import _send_reset_notification
        with (
            patch.dict(os.environ, {"SMTP_HOST": "", "NEXTCLOUD_URL": "https://cloud.example"}),
            patch("nextcloud_talk.send_text_message", return_value=False),
        ):
            assert _send_reset_notification("http://host:8090/reset-password?token=abc") is False

    def test_no_channel_configured_reports_undelivered(self):
        from api_routes_auth import _send_reset_notification
        with patch.dict(os.environ, {"SMTP_HOST": "", "NEXTCLOUD_URL": ""}):
            assert _send_reset_notification("http://host:8090/reset-password?token=abc") is False


# ─────────────────────────────────────────────────────────────────────────────
# 9.  Session epoch  (S1-4: revoke sessions on password change and "log out everywhere")
# ─────────────────────────────────────────────────────────────────────────────

def _legacy_token(username: str = "alice") -> str:
    """A token minted before the epoch claim existed."""
    import base64
    import json
    from auth import _sign
    payload = base64.urlsafe_b64encode(
        json.dumps({"u": username, "t": int(time.time()), "r": False}).encode()
    ).decode()
    return f"{payload}.{_sign(payload)}"


class TestSessionEpoch:

    def test_bump_invalidates_tokens_issued_before_it(self):
        from auth import bump_session_epoch
        old = create_session_token("alice", remember=True)
        with patch("dotenv.set_key"):
            bump_session_epoch()
        assert verify_session_token(old) is False

    def test_token_issued_after_a_bump_is_valid(self):
        from auth import bump_session_epoch
        with patch("dotenv.set_key"):
            bump_session_epoch()
        assert verify_session_token(create_session_token("alice", remember=False)) is True

    def test_bump_persists_the_epoch_to_env(self):
        from auth import bump_session_epoch
        with patch("dotenv.set_key") as set_key:
            new_epoch = bump_session_epoch()
        assert set_key.call_args[0][1:] == ("SESSION_EPOCH", str(new_epoch))
        assert os.environ["SESSION_EPOCH"] == str(new_epoch)

    def test_token_without_an_epoch_counts_as_epoch_zero(self, monkeypatch):
        monkeypatch.setenv("SESSION_EPOCH", "0")
        assert verify_session_token(_legacy_token()) is True
        monkeypatch.setenv("SESSION_EPOCH", "1")
        assert verify_session_token(_legacy_token()) is False

    def test_change_password_signs_out_other_sessions_and_keeps_this_one(self, test_password, auth_cookies):
        other_device = create_session_token("someone-else", remember=True)
        original = os.environ.get("DASHBOARD_PASSWORD", test_password)
        try:
            with _fresh_client() as c, patch("dotenv.set_key"):
                c.cookies.update(auth_cookies)
                resp = c.post(
                    "/api/change-password",
                    json={
                        "current_password": test_password,
                        "new_password": "A-brand-new-passphrase-1",
                        "confirm_password": "A-brand-new-passphrase-1",
                    },
                    headers={"X-CSRFToken": auth_cookies["csrftoken"]},
                )
            assert resp.status_code == 200
            assert verify_session_token(other_device) is False
            assert verify_session_token(auth_cookies[COOKIE_NAME]) is False
            reissued = resp.cookies.get(COOKIE_NAME, "").strip('"')
            assert reissued and verify_session_token(reissued) is True
        finally:
            os.environ["DASHBOARD_PASSWORD"] = original
            os.environ.pop("DASHBOARD_PASSWORD_HASH", None)

    def test_change_password_is_refused_to_an_api_key(self, raw_client):
        resp = raw_client.post("/api/change-password", json={}, headers={"X-API-Key": os.environ["API_KEY"]})
        assert resp.status_code == 403
        assert COOKIE_NAME not in resp.cookies

    def test_logout_everywhere_revokes_every_session_and_clears_cookies(self, step_up_headers, auth_cookies):
        other_device = create_session_token("alice", remember=True)
        with _fresh_client() as c, patch("dotenv.set_key"):
            resp = c.post("/api/logout-everywhere", headers=step_up_headers)
        assert resp.status_code == 200
        assert verify_session_token(other_device) is False
        assert verify_session_token(auth_cookies[COOKIE_NAME]) is False
        cleared = [v.lower() for k, v in resp.headers.multi_items() if k.lower() == "set-cookie"]
        assert any(v.startswith("session=") and "max-age=0" in v for v in cleared)
        assert any(v.startswith("stepup=") and "max-age=0" in v and "path=/api" in v for v in cleared)

    def test_stale_session_cookie_is_redirected_to_login(self, raw_client):
        from auth import bump_session_epoch
        token = create_session_token("alice", remember=False)
        with patch("dotenv.set_key"):
            bump_session_epoch()
        resp = raw_client.get("/portfolio", cookies={COOKIE_NAME: token})
        assert resp.status_code == 302
        assert "/login" in resp.headers["location"]


# ─────────────────────────────────────────────────────────────────────────────
# 10.  Account-wide failed-password counter and lockout  (S1-8)
# ─────────────────────────────────────────────────────────────────────────────

def _login(c, username, password):
    return c.post("/api/login", json={"username": username, "password": password, "remember_me": False})


def _throttle_row():
    from database import get_connection
    conn = None
    try:
        conn = get_connection()
        return conn.execute("SELECT failed_count, locked_until FROM login_throttle WHERE id = 1").fetchone()
    finally:
        if conn:
            conn.close()


class TestLoginLockout:

    def test_four_failures_do_not_lock_the_account(self, test_username, test_password):
        with _fresh_client() as c:
            codes = [_login(c, test_username, "wrong-guess").status_code for _ in range(4)]
            assert codes == [401] * 4
            assert _login(c, test_username, test_password).status_code == 200

    def test_fifth_failure_locks_even_the_correct_password(self, test_username, test_password):
        with _fresh_client() as c:
            codes = [_login(c, test_username, "wrong-guess").status_code for _ in range(5)]
            assert codes == [401] * 5
            locked = _login(c, test_username, test_password)
        assert locked.status_code == 429
        assert 0 < int(locked.headers["Retry-After"]) <= 30
        assert "Too many failed attempts" in locked.json()["detail"]
        assert COOKIE_NAME not in locked.cookies

    def test_lockout_is_persisted_in_the_database(self, test_username):
        with _fresh_client() as c:
            for _ in range(5):
                _login(c, test_username, "wrong-guess")
        row = _throttle_row()
        assert row["failed_count"] == 5
        assert row["locked_until"]

    def test_success_resets_the_counter(self, test_username, test_password):
        with _fresh_client() as c:
            for _ in range(4):
                _login(c, test_username, "wrong-guess")
            assert _login(c, test_username, test_password).status_code == 200
            assert _throttle_row()["failed_count"] == 0
            codes = [_login(c, test_username, "wrong-guess").status_code for _ in range(4)]
        assert codes == [401] * 4

    def test_wrong_username_with_right_password_counts_as_a_failure(self, test_password):
        with _fresh_client() as c:
            _login(c, "nobody", test_password)
        assert _throttle_row()["failed_count"] == 1

    def test_lock_doubles_with_each_further_failure_up_to_the_cap(self):
        from auth import login_lockout_remaining, record_login_failure
        observed = []
        for _ in range(10):
            record_login_failure("login", "203.0.113.9", known_user=True)
            observed.append(login_lockout_remaining())
        expected = [0] * 4 + [30, 60, 120, 240, 480, 900]
        for got, want in zip(observed, expected):
            assert abs(got - want) <= 1

    def test_lock_expires_on_its_own(self, test_username, test_password):
        from database import get_connection
        with _fresh_client() as c:
            for _ in range(5):
                _login(c, test_username, "wrong-guess")
            conn = None
            try:
                conn = get_connection()
                conn.execute("UPDATE login_throttle SET locked_until = '2000-01-01 00:00:00' WHERE id = 1")
                conn.commit()
            finally:
                if conn:
                    conn.close()
            assert _login(c, test_username, test_password).status_code == 200

    def test_step_up_and_change_password_share_the_counter(self, test_username, auth_cookies, client):
        with _fresh_client() as c:
            c.cookies.update(auth_cookies)
            headers = {"X-CSRFToken": auth_cookies["csrftoken"]}
            for _ in range(3):
                assert c.post("/api/step-up", json={"password": "wrong-guess"}, headers=headers).status_code == 401
            for _ in range(2):
                resp = client.post("/api/change-password", json={
                    "current_password": "wrong-guess", "new_password": "x", "confirm_password": "x",
                })
                assert resp.status_code == 400
            assert _throttle_row()["failed_count"] == 5
            blocked = c.post("/api/step-up", json={"password": os.environ["DASHBOARD_PASSWORD"]}, headers=headers)
            assert blocked.status_code == 429
        assert "stepup=" not in blocked.headers.get("set-cookie", "")
        client_blocked = client.post("/api/change-password", json={
            "current_password": os.environ["DASHBOARD_PASSWORD"], "new_password": "x", "confirm_password": "x",
        })
        assert client_blocked.status_code == 429

    def test_step_up_without_a_session_does_not_count(self, raw_client):
        headers = {"X-API-Key": os.environ["API_KEY"]}
        assert raw_client.post("/api/step-up", json={"password": "wrong-guess"}, headers=headers).status_code == 403
        assert _throttle_row() is None

    def test_api_key_access_is_not_affected_by_a_lockout(self, raw_client, test_username):
        with _fresh_client() as c:
            for _ in range(5):
                _login(c, test_username, "wrong-guess")
        resp = raw_client.get("/api/market-regime/current", headers={"X-API-Key": os.environ["API_KEY"]})
        assert resp.status_code == 200

    def test_failures_are_logged_with_client_and_username_match(self, test_username, caplog):
        with caplog.at_level("WARNING", logger="auth"), _fresh_client() as c:
            _login(c, "nobody", "wrong-guess")
        record = next(r for r in caplog.records if "Failed dashboard password attempt" in r.getMessage())
        assert "login" in record.getMessage()
        assert "username_matched=False" in record.getMessage()
        assert "wrong-guess" not in record.getMessage()

    def test_notification_fires_once_when_the_lockout_starts(self, test_username):
        with patch("notification_engine.notify") as notify, _fresh_client() as c:
            for _ in range(8):
                from database import get_connection
                conn = get_connection()
                conn.execute("UPDATE login_throttle SET locked_until = NULL")
                conn.commit()
                conn.close()
                _login(c, test_username, "wrong-guess")
        alerts = [call for call in notify.call_args_list if call.args[0] == "failed_login_alert"]
        assert len(alerts) == 1
        assert "5 consecutive" in alerts[0].args[2]

    def test_failed_login_alert_is_a_registered_notification_source(self):
        from notification_engine import NOTIFICATION_SOURCES
        meta = NOTIFICATION_SOURCES["failed_login_alert"]
        assert meta["label"] == "Failed Login Alert"
        assert meta["default"] == {"log_file": True, "in_app": True, "nextcloud_talk": True}


# ─────────────────────────────────────────────────────────────────────────────
# 11.  Password policy  (S1-8: 12 characters minimum, no common passwords)
# ─────────────────────────────────────────────────────────────────────────────

class TestPasswordPolicy:

    @pytest.mark.parametrize("password", ["", "short", "elevenchars", "changeme"])
    def test_too_short_is_rejected(self, password):
        from auth import password_policy_error
        assert "at least 12 characters" in password_policy_error(password)

    def test_exactly_twelve_characters_is_accepted(self):
        from auth import password_policy_error
        assert password_policy_error("Zx9!kQw2#mPe") is None

    @pytest.mark.parametrize("password", ["password123456", "PASSWORD123456", "Qwertyuiop123", "123456789012"])
    def test_common_password_is_rejected_whatever_its_case(self, password):
        from auth import password_policy_error
        assert "too common" in password_policy_error(password)

    def test_a_passphrase_is_accepted(self):
        from auth import password_policy_error
        assert password_policy_error("correct horse battery staple") is None

    def test_list_file_entries_are_lowercase_and_not_shorter_than_the_minimum(self):
        from auth import MIN_LENGTH_POLICY, _common_passwords
        entries = _common_passwords()
        assert len(entries) > 500
        assert all(e == e.lower() and len(e) >= MIN_LENGTH_POLICY for e in entries)

    def test_cli_reset_applies_the_policy_and_revokes_sessions(self, monkeypatch, capsys):
        import reset_admin_password
        answers = iter(["elevenchars", "password123456", "Correct-Horse-Battery-9", "Correct-Horse-Battery-9"])
        monkeypatch.setattr("getpass.getpass", lambda prompt="": next(answers))
        with patch("dotenv.set_key") as set_key, patch("auth.clear_login_failures") as clear:
            reset_admin_password.main()
        out = capsys.readouterr().out
        assert out.count("at least 12 characters and not a commonly used password") == 2
        written = [call.args[1] for call in set_key.call_args_list]
        assert {"DASHBOARD_PASSWORD_HASH", "DASHBOARD_PASSWORD", "SESSION_EPOCH"} <= set(written)
        clear.assert_called_once()
