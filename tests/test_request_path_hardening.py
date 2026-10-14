"""
tests/test_request_path_hardening.py  ── Host allow-list, security headers, cookie Secure flag

Covers the request-path hardening from the security review (S7-7, S1-7, S1-12, S5-5):
  1. TrustedHostMiddleware rejects unlisted and path-bearing Host headers
  2. The app sends its own security headers, including on a rejected request
  3. auth.cookie_secure() resolves the COOKIE_SECURE tri-state
  4. POST /api/settings rejects malformed hosts, origins and cookie modes
"""

from unittest.mock import patch

import auth


def test_unlisted_host_is_rejected(raw_client):
    r = raw_client.get("/login", headers={"Host": "evil.example.com"})
    assert r.status_code == 400


def test_path_bearing_host_cannot_bypass_login(raw_client):
    r = raw_client.get("/settings", headers={"Host": "x/static/"})
    assert r.status_code == 400


def test_listed_host_reaches_login_redirect(raw_client):
    r = raw_client.get("/settings", headers={"Host": "stock.awroblew.biz"})
    assert r.status_code == 302
    assert r.headers["location"].startswith("/login")


def test_localhost_is_always_allowed(raw_client):
    assert raw_client.get("/login", headers={"Host": "127.0.0.1:8090"}).status_code == 200


def test_security_headers_on_page_and_on_rejection(raw_client):
    ok = raw_client.get("/login")
    rejected = raw_client.get("/login", headers={"Host": "evil.example.com"})
    for r in (ok, rejected):
        assert r.headers["X-Content-Type-Options"] == "nosniff"
        assert r.headers["Referrer-Policy"] == "no-referrer"
        assert r.headers["X-XSS-Protection"] == "0"
        csp = r.headers["Content-Security-Policy"]
        assert "object-src 'none'" in csp
        assert "frame-ancestors 'self'" in csp


def test_cookie_secure_modes():
    with patch("config.load_config", return_value={"COOKIE_SECURE": "true", "SERVER_URL": "http://x"}):
        assert auth.cookie_secure() is True
    with patch("config.load_config", return_value={"COOKIE_SECURE": "false", "SERVER_URL": "https://x"}):
        assert auth.cookie_secure() is False
    with patch("config.load_config", return_value={"COOKIE_SECURE": "auto", "SERVER_URL": "https://x"}):
        assert auth.cookie_secure() is True
    with patch("config.load_config", return_value={"SERVER_URL": "http://x"}):
        assert auth.cookie_secure() is False


def test_settings_rejects_invalid_values(client, step_up_headers):
    for body in (
        {"ALLOWED_HOSTS": []},
        {"ALLOWED_HOSTS": ["bad host/x"]},
        {"SCRAPER_ALLOWED_HOSTS": ["192.168.1.0/24"]},
        {"SCRAPER_ALLOWED_HOSTS": ["*.lan"]},
        {"SCRAPER_ALLOWED_HOSTS": ["bad host"]},
        {"FRAME_ANCESTORS": ["https://ok.example.com; script-src *"]},
        {"COOKIE_SECURE": "maybe"},
        {"BIND_HOST": "0.0.0.0; rm"},
    ):
        r = client.post("/api/settings", json=body, headers=step_up_headers)
        assert r.status_code == 422, body


def test_scraper_allowed_hosts_accepts_exact_hosts_and_an_empty_list():
    from api_routes_system import SettingsConfig
    assert SettingsConfig(SCRAPER_ALLOWED_HOSTS=[]).SCRAPER_ALLOWED_HOSTS == []
    hosts = ["192.168.1.71", "ha.lan", "fd00::71"]
    assert SettingsConfig(SCRAPER_ALLOWED_HOSTS=hosts).SCRAPER_ALLOWED_HOSTS == hosts
