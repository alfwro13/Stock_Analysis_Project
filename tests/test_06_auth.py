"""
tests/test_06_auth.py  ── Authentication Subsystem Tests

Full coverage of the auth surface:

  1. auth.py helpers         – token creation, HMAC verification, tamper resistance
  2. Auth middleware          – public paths, protected paths, API-key path, session-cookie path
  3. POST /api/login          – credential validation, cookie lifecycle, remember-me flag
  4. GET  /logout             – cookie deletion and redirect
  5. POST /api/change-password – all validation rules and the happy path
  6. require_confirm_token    – missing / invalid / valid token on admin endpoints

Design notes
────────────
• `raw_client`  — session-scoped, no X-API-Key, follow_redirects=False (from conftest).
  Safe for tests that never trigger a server-set cookie.

• `_fresh_client()` — context-manager helper that yields a brand-new TestClient.
  Used for login/logout tests so that server-set session cookies can never bleed
  between test cases via the shared cookie jar.

• `client`      — session-scoped, X-API-Key pre-set (from conftest).
  Used when auth must pass but we are testing a different layer (e.g. confirm token,
  change-password business logic).
"""

import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

from auth import COOKIE_NAME, create_session_token, verify_session_token


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
    ):
        import main as _main_module
        with TestClient(_main_module.app, raise_server_exceptions=False, follow_redirects=False) as c:
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
# 2.  Auth middleware
# ─────────────────────────────────────────────────────────────────────────────

class TestAuthMiddleware:

    # ── Public paths ──────────────────────────────────────────────────────────

    def test_login_page_is_public(self, raw_client):
        resp = raw_client.get("/login")
        assert resp.status_code == 200

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

    def test_valid_api_key_grants_access(self, raw_client):
        key = os.environ["API_KEY"]
        resp = raw_client.get("/api/notifications/latest", headers={"X-API-Key": key})
        assert resp.status_code == 200

    def test_invalid_api_key_returns_401(self, raw_client):
        resp = raw_client.get("/api/notifications/latest", headers={"X-API-Key": "wrong-key"})
        assert resp.status_code == 401

    def test_api_key_with_env_var_unset_returns_401(self, raw_client, monkeypatch):
        # When API_KEY is not configured, no key value can succeed.
        monkeypatch.delenv("API_KEY", raising=False)
        resp = raw_client.get("/api/notifications/latest", headers={"X-API-Key": "any-key"})
        assert resp.status_code == 401

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

class TestChangePassword:
    """Uses the api-key-authenticated `client` so middleware is bypassed;
    only the change-password business logic is under test.
    All requests must include X-Confirm-Token (added as a security fix)."""

    def test_wrong_current_password_returns_400(self, client, confirm_token, test_password):
        resp = client.post("/api/change-password", json={
            "current_password": "definitely-not-the-right-password",
            "new_password": "brandnewpass99",
            "confirm_password": "brandnewpass99",
        }, headers={"X-Confirm-Token": confirm_token})
        assert resp.status_code == 400
        assert "incorrect" in resp.json().get("detail", "").lower()

    def test_mismatched_new_passwords_return_400(self, client, confirm_token, test_password):
        resp = client.post("/api/change-password", json={
            "current_password": test_password,
            "new_password": "newpassword456",
            "confirm_password": "differentpass456",
        }, headers={"X-Confirm-Token": confirm_token})
        assert resp.status_code == 400
        assert "match" in resp.json().get("detail", "").lower()

    def test_password_shorter_than_8_chars_returns_400(self, client, confirm_token, test_password):
        resp = client.post("/api/change-password", json={
            "current_password": test_password,
            "new_password": "short",
            "confirm_password": "short",
        }, headers={"X-Confirm-Token": confirm_token})
        assert resp.status_code == 400
        assert "8" in resp.json().get("detail", "")

    def test_changeme_password_is_rejected(self, client, confirm_token, test_password):
        resp = client.post("/api/change-password", json={
            "current_password": test_password,
            "new_password": "changeme",
            "confirm_password": "changeme",
        }, headers={"X-Confirm-Token": confirm_token})
        assert resp.status_code == 400

    def test_valid_password_change_returns_200(self, client, confirm_token, test_password):
        original = os.environ.get("DASHBOARD_PASSWORD", test_password)
        try:
            with patch("dotenv.set_key"):  # prevent writing to the real .env file
                resp = client.post("/api/change-password", json={
                    "current_password": original,
                    "new_password": "brandnewpass99",
                    "confirm_password": "brandnewpass99",
                }, headers={"X-Confirm-Token": confirm_token})
            assert resp.status_code == 200
            assert resp.json().get("status") == "ok"
        finally:
            # The handler mutates os.environ; restore it so subsequent tests are unaffected.
            os.environ["DASHBOARD_PASSWORD"] = original


# ─────────────────────────────────────────────────────────────────────────────
# 6.  require_confirm_token  (admin endpoint guard)
# ─────────────────────────────────────────────────────────────────────────────

class TestConfirmToken:
    """Uses the api-key-authenticated `client` (middleware bypassed) to isolate
    the confirm-token dependency layer."""

    def test_missing_token_returns_422(self, client):
        resp = client.post("/api/settings", json={})
        assert resp.status_code == 422, (
            f"A missing X-Confirm-Token header must return 422 Unprocessable Entity; "
            f"got {resp.status_code}"
        )

    def test_invalid_token_returns_403(self, client):
        resp = client.post("/api/settings", json={}, headers={"X-Confirm-Token": "wrong-token"})
        assert resp.status_code == 403

    def test_valid_token_allows_settings_update(self, client, confirm_token):
        resp = client.post(
            "/api/settings",
            json={"UI_PREFERENCES": {"REFRESH_RATE": 60}},
            headers={"X-Confirm-Token": confirm_token},
        )
        assert resp.status_code == 200
        assert resp.json().get("status") == "success"

    def test_git_pull_requires_confirm_token(self, client):
        resp = client.post("/api/system/git-pull")
        assert resp.status_code == 422

    def test_system_restart_requires_confirm_token(self, client):
        resp = client.post("/api/system/restart")
        assert resp.status_code == 422

    def test_generate_api_key_requires_confirm_token(self, client):
        """POST /api/generate-api-key must reject requests without X-Confirm-Token."""
        resp = client.post("/api/generate-api-key")
        assert resp.status_code == 422, (
            f"Missing X-Confirm-Token must return 422; got {resp.status_code}"
        )

    def test_generate_api_key_invalid_token_returns_403(self, client):
        resp = client.post("/api/generate-api-key", headers={"X-Confirm-Token": "wrong"})
        assert resp.status_code == 403

    def test_generate_api_key_valid_token_returns_200(self, client, confirm_token):
        with patch("dotenv.set_key"), patch.dict(os.environ, {}, clear=False):
            resp = client.post("/api/generate-api-key", headers={"X-Confirm-Token": confirm_token})
        assert resp.status_code == 200
        data = resp.json()
        assert "api_key" in data, f"Response must contain api_key; got {data}"
        assert len(data["api_key"]) == 64, "api_key must be 64 hex chars (32-byte token)"

    def test_change_password_requires_confirm_token(self, client):
        """POST /api/change-password must reject requests without X-Confirm-Token."""
        resp = client.post("/api/change-password", json={
            "current_password": "x", "new_password": "y", "confirm_password": "y"
        })
        assert resp.status_code == 422, (
            f"Missing X-Confirm-Token must return 422; got {resp.status_code}"
        )

    def test_change_password_invalid_token_returns_403(self, client):
        resp = client.post("/api/change-password", json={
            "current_password": "x", "new_password": "newpass99", "confirm_password": "newpass99"
        }, headers={"X-Confirm-Token": "wrong"})
        assert resp.status_code == 403

    def test_test_nextcloud_message_requires_confirm_token(self, client):
        """POST /api/test-nextcloud-message must reject requests without X-Confirm-Token."""
        resp = client.post("/api/test-nextcloud-message")
        assert resp.status_code == 422, (
            f"Missing X-Confirm-Token must return 422; got {resp.status_code}"
        )

    def test_test_nextcloud_message_invalid_token_returns_403(self, client):
        resp = client.post("/api/test-nextcloud-message", headers={"X-Confirm-Token": "wrong"})
        assert resp.status_code == 403


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
