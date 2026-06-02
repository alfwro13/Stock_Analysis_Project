"""
tests/test_rss_feed.py  ── RSS Feed Tests

Covers the three invariants of the unauthenticated RSS feed at /rss/alerts.xml:

  1. Feed returns 404 when NOTIFICATIONS.RSS_FEED.ENABLED is False (the default).
     Prevents accidental data exposure if the toggle is off.

  2. Feed returns valid RSS 2.0 XML (200, correct Content-Type, well-formed XML,
     required channel elements present) when the feed is enabled and the DB
     contains crash/moonshot notifications.

  3. Feed is reachable without any authentication — no session cookie and no
     X-API-Key header.  A future middleware change must not silently break this.
"""

import copy
import sqlite3
import sys
import xml.etree.ElementTree as ET
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient

sys.path.insert(0, str(Path(__file__).parent.parent))

import config as _cfg_module
import database as _db_module


# ── helpers ───────────────────────────────────────────────────────────────────

def _rss_config(enabled: bool) -> dict:
    """Return a config dict with RSS_FEED.ENABLED set to the given value."""
    cfg = copy.deepcopy(_cfg_module.DEFAULT_CONFIG)
    cfg["NOTIFICATIONS"]["RSS_FEED"]["ENABLED"] = enabled
    return cfg


@contextmanager
def _rss_enabled(enabled: bool):
    """Context manager that patches page_routes.load_config for the duration."""
    with patch("page_routes.load_config", return_value=_rss_config(enabled)):
        yield


@contextmanager
def _fresh_client():
    """Yield a brand-new unauthenticated TestClient (no X-API-Key, redirects not followed)."""
    with (
        patch("main.run_yfinance_smoke_test"),
        patch("main.start_scheduler"),
        patch("main.reload_scheduler"),
        patch("main.shutdown_scheduler"),
    ):
        import main as _main_module
        with TestClient(_main_module.app, raise_server_exceptions=False, follow_redirects=False) as c:
            yield c


def _seed_alerts(conn: sqlite3.Connection) -> None:
    """Insert one Crash and one Moonshot row into system_notifications."""
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    conn.executemany(
        "INSERT INTO system_notifications (message_type, message_text, timestamp, is_read, status) "
        "VALUES (?, ?, ?, 0, 'sent')",
        [
            ("Crash",    "**Price:** £100.00 | Intraday Alert triggered for AAPL. Reason: SESSION CRASH", ts),
            ("Moonshot", "**Price:** £200.00 | Intraday Alert triggered for TSLA. Reason: Breached 52-Week High", ts),
        ],
    )
    conn.commit()


# ── tests ─────────────────────────────────────────────────────────────────────

def test_rss_feed_disabled_returns_404(client):
    """Feed must return 404 when RSS_FEED.ENABLED is False (the default)."""
    with _rss_enabled(False):
        resp = client.get("/rss/alerts.xml")
    assert resp.status_code == 404, (
        f"Expected 404 when feed is disabled, got {resp.status_code}"
    )


def test_rss_feed_enabled_returns_valid_xml(client):
    """Feed returns 200 with well-formed RSS 2.0 XML when enabled."""
    conn = _db_module.get_connection()
    try:
        _seed_alerts(conn)
    finally:
        conn.close()

    with _rss_enabled(True):
        resp = client.get("/rss/alerts.xml")

    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}\n{resp.text[:500]}"
    assert "application/rss+xml" in resp.headers.get("content-type", ""), (
        f"Unexpected Content-Type: {resp.headers.get('content-type')}"
    )

    try:
        root = ET.fromstring(resp.text)
    except ET.ParseError as exc:
        raise AssertionError(f"Feed body is not valid XML: {exc}\n{resp.text[:500]}") from exc

    assert root.tag == "rss", f"Root element should be <rss>, got <{root.tag}>"
    assert root.get("version") == "2.0", "RSS version attribute should be 2.0"

    channel = root.find("channel")
    assert channel is not None, "<channel> element is missing"
    assert channel.find("title") is not None, "<title> missing from <channel>"
    assert channel.find("description") is not None, "<description> missing from <channel>"

    items = channel.findall("item")
    assert len(items) >= 2, f"Expected at least 2 <item> elements, found {len(items)}"
    for item in items:
        assert item.find("title") is not None, "<item> is missing <title>"
        assert item.find("description") is not None, "<item> is missing <description>"
        assert item.find("pubDate") is not None, "<item> is missing <pubDate>"
        assert item.find("guid") is not None, "<item> is missing <guid>"


def test_rss_feed_accessible_without_auth():
    """Feed must be reachable with no session cookie and no X-API-Key.
    A future middleware change must not silently gate this endpoint."""
    with _fresh_client() as anon:
        with _rss_enabled(True):
            resp = anon.get("/rss/alerts.xml")
    # 200 or 404 are acceptable — 401 and 302-to-login are not.
    assert resp.status_code not in (401, 302), (
        f"Feed should never require authentication, got {resp.status_code}. "
        "Check _EXEMPT_PREFIXES in main.py."
    )


def test_rss_feed_excludes_non_alert_notification_types(client):
    """Feed must only include Crash/Moonshot items, not Scheduler/Info/etc."""
    conn = _db_module.get_connection()
    try:
        ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        conn.execute(
            "INSERT INTO system_notifications (message_type, message_text, timestamp, is_read, status) "
            "VALUES (?, ?, ?, 0, 'sent')",
            ("Scheduler", "Background job completed successfully", ts),
        )
        conn.commit()
    finally:
        conn.close()

    with _rss_enabled(True):
        resp = client.get("/rss/alerts.xml")

    assert resp.status_code == 200
    root = ET.fromstring(resp.text)
    channel = root.find("channel")
    for item in channel.findall("item"):
        title = item.find("title").text or ""
        assert "Scheduler" not in title, (
            f"Non-alert item leaked into RSS feed: {title!r}"
        )
