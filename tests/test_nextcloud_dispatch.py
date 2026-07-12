"""
tests/test_nextcloud_dispatch.py  ── NEXTCLOUD ALERT DISPATCH

Regression tests for the two bugs fixed in the Crash/Moonshot notification path:

  BUG 1 — send_text_message read credentials from load_config() dict, which never
           contains the Nextcloud secrets (they are SENSITIVE_KEYS, written only to
           .env / os.environ, never to config.json).  Every call silently returned
           False; alerts were recorded as "fired" (120-min cooldown) and logged as
           "sent" — but Nextcloud received nothing.

  BUG 2 — _dispatch_alerts ignored the bool return value of send_text_message.
           A False (failed send) was treated identically to a True (successful
           send): record_alert_fired was called (burning the cooldown) and the
           notification was logged with status='sent'.

Tests are grouped into two suites:

  TestSendTextMessageCredentials
      Verifies that send_text_message resolves credentials from os.environ
      regardless of what is in the config_data dict.

  TestDispatchAlerts
      Verifies that _dispatch_alerts:
        - on success   → calls record_alert_fired + logs status='sent'
        - on False     → skips record_alert_fired + logs status='failed'
        - on exception → skips record_alert_fired + logs status='failed'

All tests are pure unit tests: no network calls, no yfinance, no real DB writes
beyond what the session-level temp DB already provides.
"""

import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db


@pytest.fixture(autouse=True)
def _block_real_nextcloud_send():
    """Same fixture name as conftest.py's session-wide safety-net mock — pytest resolves the
    module-level definition first, so this no-op *overrides* (not adds to) it for this module:
    TestSendTextMessageCredentials unit-tests send_text_message's own real implementation against
    a mocked requests.post, so it needs the real function running, not the global mock.
    TestDispatchAlerts patches send_text_message itself per-test regardless, so is unaffected."""
    yield


# ── helpers ───────────────────────────────────────────────────────────────────

TEST_TICKER = "_NC_DISPATCH_TEST"
MSG_MARKER = "__nc_dispatch_test__"

TEST_CONFIG = {
    "NOTIFICATIONS": {
        "CRASH_ALERTS":   {"COOLDOWN_MINUTES": 120.0, "RETRIGGER_PERCENT": 2.0, "REARM_PERCENT": 3.0},
        "MOONSHOT_ALERTS": {"COOLDOWN_MINUTES": 120.0, "RETRIGGER_PERCENT": 2.0, "REARM_PERCENT": 3.0},
        "MACRO_ALERTS":   {"COOLDOWN_MINUTES": 120.0, "RETRIGGER_PERCENT": 2.0, "REARM_PERCENT": 3.0},
    },
    # Deliberately empty — secrets must come from os.environ, not here.
    "NEXTCLOUD_URL":        "",
    "BOT_USERNAME":         "",
    "APP_PASSWORD":         "",
    "CONVERSATION_TOKEN":   "",
}

NC_ENV = {
    "NEXTCLOUD_URL":                "https://cloud.example.com",
    "NEXTCLOUD_BOT_USERNAME":       "bot",
    "NEXTCLOUD_APP_PASSWORD":       "secret",
    "NEXTCLOUD_CONVERSATION_TOKEN": "abc123",
}


def _conn():
    conn = sqlite3.connect(_db.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _read_alert_state(engine, ticker):
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM alert_state WHERE engine=? AND ticker=?", (engine, ticker)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _read_notifications():
    conn = _conn()
    rows = conn.execute(
        "SELECT status FROM system_notifications WHERE message_text LIKE ? ORDER BY id DESC",
        (f"%{MSG_MARKER}%",),
    ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _clear():
    conn = _conn()
    conn.execute("DELETE FROM alert_state WHERE ticker=?", (TEST_TICKER,))
    conn.execute(
        "DELETE FROM system_notifications WHERE message_text LIKE ?",
        (f"%{MSG_MARKER}%",),
    )
    conn.commit()
    conn.close()


# ── fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def orch():
    """IntradayOrchestrator with engines mocked and controlled config."""
    with (
        patch("intraday_orchestrator.CrashEngine"),
        patch("intraday_orchestrator.MoonshotEngine"),
        patch("intraday_orchestrator.load_config", return_value=TEST_CONFIG),
    ):
        from intraday_orchestrator import IntradayOrchestrator
        o = IntradayOrchestrator()
        yield o
    _clear()


@pytest.fixture
def db_conn():
    conn = _conn()
    yield conn
    conn.close()


def _make_alert_tuple():
    """Minimal alert tuple for _dispatch_alerts."""
    alert = {"price": 100.0, "reason": f"SESSION CRASH {MSG_MARKER}"}
    currency = "USD"
    meta = {}
    return [(TEST_TICKER, alert, currency, meta)]


def _msg_builder(t, p, a, ml, var, sent, url):
    return f"crash msg {MSG_MARKER}"


def _feed_builder(t, p, a):
    return f"crash feed {MSG_MARKER}"


# ═══════════════════════════════════════════════════════════════════════════════
#  Suite 1 — send_text_message credential resolution
# ═══════════════════════════════════════════════════════════════════════════════

class TestSendTextMessageCredentials:
    """
    send_text_message must read credentials from os.environ, not from the
    config_data dict.  Nextcloud secrets are SENSITIVE_KEYS — they are never
    written to config.json / load_config(), only to .env / os.environ.
    """

    def test_empty_config_dict_with_env_vars_sends_successfully(self):
        """Core regression: empty config dict + populated env vars → send attempted."""
        with patch.dict(os.environ, NC_ENV, clear=False):
            with patch("requests.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.raise_for_status.return_value = None
                mock_post.return_value = mock_resp

                from nextcloud_talk import send_text_message
                result = send_text_message("hello", {})

        assert result is True, (
            "send_text_message returned False with a valid os.environ but empty config dict. "
            "Credentials must be read from os.environ, not config_data."
        )
        mock_post.assert_called_once()

    def test_missing_env_vars_returns_false_without_raising(self):
        """All four env vars absent → returns False (no exception, no side-effects)."""
        clean_env = {k: "" for k in NC_ENV}
        with patch.dict(os.environ, clean_env, clear=False):
            # Also clear any values that may have leaked from the process env
            for k in NC_ENV:
                os.environ.pop(k, None)
            from nextcloud_talk import send_text_message
            result = send_text_message("hello", {})

        assert result is False

    def test_env_var_takes_precedence_over_config_dict(self):
        """Env var value wins when both config_data and os.environ have values."""
        config_with_wrong_url = {
            "NEXTCLOUD_URL": "https://wrong.example.com",
            "BOT_USERNAME":  "wrong_user",
            "APP_PASSWORD":  "wrong_pass",
            "CONVERSATION_TOKEN": "wrong_token",
        }
        with patch.dict(os.environ, NC_ENV, clear=False):
            with patch("requests.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.raise_for_status.return_value = None
                mock_post.return_value = mock_resp

                from nextcloud_talk import send_text_message
                send_text_message("hello", config_with_wrong_url)

        call_kwargs = mock_post.call_args
        actual_url = call_kwargs[0][0] if call_kwargs[0] else call_kwargs[1].get("url", "")
        assert "wrong.example.com" not in actual_url, (
            "os.environ value must take precedence over config_data value."
        )
        assert "cloud.example.com" in actual_url

    def test_partial_env_vars_still_returns_false(self):
        """If any one credential is missing, returns False without HTTP call."""
        partial_env = {k: v for k, v in NC_ENV.items() if k != "NEXTCLOUD_APP_PASSWORD"}
        os.environ.pop("NEXTCLOUD_APP_PASSWORD", None)
        with patch.dict(os.environ, partial_env, clear=False):
            with patch("requests.post") as mock_post:
                from nextcloud_talk import send_text_message
                result = send_text_message("hello", {})

        assert result is False
        mock_post.assert_not_called()

    def test_http_error_returns_false_and_logs(self):
        """HTTP-level error → returns False, does not raise to caller."""
        import requests
        with patch.dict(os.environ, NC_ENV, clear=False):
            with patch("requests.post") as mock_post:
                mock_post.side_effect = requests.exceptions.ConnectionError("unreachable")
                from nextcloud_talk import send_text_message
                result = send_text_message("hello", {})

        assert result is False


# ═══════════════════════════════════════════════════════════════════════════════
#  Suite 2 — _dispatch_alerts return-value handling
# ═══════════════════════════════════════════════════════════════════════════════

class TestDispatchAlerts:
    """
    _dispatch_alerts now routes through notification_engine.notify(). The dedup
    guarantee is preserved: notify() returns False when an enabled Nextcloud send
    fails, and the alert must then NOT be recorded in alert_state (so it retries on
    the next scan). The in-app feed row is an independent channel and is written
    regardless of the Nextcloud outcome. Routing falls back to the registry default
    (crash/moonshot → log+in-app+Nextcloud all on) since TEST_CONFIG has no
    NOTIFICATION_ROUTING block.
    """

    @staticmethod
    def _patch(result=None, exc=None, side_effect=None):
        from contextlib import contextmanager

        @contextmanager
        def _cm():
            with patch("notification_engine.load_config", return_value=TEST_CONFIG):
                kw = {}
                if exc is not None:
                    kw["side_effect"] = exc
                elif side_effect is not None:
                    kw["side_effect"] = side_effect
                else:
                    kw["return_value"] = result
                with patch("notification_engine.nextcloud_talk.send_text_message", **kw):
                    yield
        return _cm()

    def test_successful_send_records_alert_and_writes_feed(self, orch, db_conn):
        """Happy path: Nextcloud send succeeds → alert recorded, in-app feed row written."""
        with self._patch(result=True):
            orch._dispatch_alerts(
                "Crash", _make_alert_tuple(), db_conn, _msg_builder, _feed_builder
            )

        assert _read_alert_state("Crash", TEST_TICKER) is not None, (
            "record_alert_fired must be called on a successful send."
        )
        assert _read_notifications(), "notify() must write an in-app feed row on success."

    def test_failed_send_does_not_record_alert(self, orch, db_conn):
        """Nextcloud send returns False → alert NOT recorded (retries next scan)."""
        with self._patch(result=False):
            orch._dispatch_alerts(
                "Crash", _make_alert_tuple(), db_conn, _msg_builder, _feed_builder
            )

        assert _read_alert_state("Crash", TEST_TICKER) is None, (
            "record_alert_fired must NOT be called when the Nextcloud send returns False. "
            "The alert must remain unrecorded so it retries on the next scan cycle."
        )

    def test_failed_send_still_writes_in_app_feed(self, orch, db_conn):
        """Nextcloud send returns False → in-app feed row still written (independent channel)."""
        with self._patch(result=False):
            orch._dispatch_alerts(
                "Crash", _make_alert_tuple(), db_conn, _msg_builder, _feed_builder
            )

        assert _read_notifications(), (
            "The in-app feed row must still be written even when the Nextcloud send fails."
        )

    def test_exception_during_send_does_not_record_alert(self, orch, db_conn):
        """Exception from the Nextcloud send → alert NOT recorded."""
        with self._patch(exc=RuntimeError("boom")):
            orch._dispatch_alerts(
                "Crash", _make_alert_tuple(), db_conn, _msg_builder, _feed_builder
            )

        assert _read_alert_state("Crash", TEST_TICKER) is None, (
            "record_alert_fired must NOT be called when the Nextcloud send raises."
        )

    def test_moonshot_failed_send_does_not_record_alert(self, orch, db_conn):
        """Same return-value check applies for the Moonshot engine."""
        moonshot_tuple = [
            (TEST_TICKER, {"price": 200.0, "reason": f"SPIKE {MSG_MARKER}", "cautions": []}, "USD", {})
        ]

        def moon_msg(t, p, a, ml, var, sent, url):
            return f"moonshot msg {MSG_MARKER}"

        def moon_feed(t, p, a):
            return f"moonshot feed {MSG_MARKER}"

        with self._patch(result=False):
            orch._dispatch_alerts(
                "Moonshot", moonshot_tuple, db_conn, moon_msg, moon_feed
            )

        assert _read_alert_state("Moonshot", TEST_TICKER) is None, (
            "Moonshot: record_alert_fired must NOT be called when the send returns False."
        )

    def test_multiple_tickers_partial_failure(self, orch, db_conn):
        """If the first ticker send fails and the second succeeds, only the second is recorded."""
        ticker_a = TEST_TICKER + "_A"
        ticker_b = TEST_TICKER + "_B"
        alert = {"price": 100.0, "reason": f"CRASH {MSG_MARKER}"}
        tuples = [
            (ticker_a, alert, "USD", {}),
            (ticker_b, alert, "USD", {}),
        ]

        call_count = {"n": 0}

        def send_side_effect(msg, cfg):
            call_count["n"] += 1
            return call_count["n"] > 1  # first call fails, second succeeds

        try:
            with self._patch(side_effect=send_side_effect):
                orch._dispatch_alerts("Crash", tuples, db_conn, _msg_builder, _feed_builder)

            assert _read_alert_state("Crash", ticker_a) is None, \
                "ticker_a send failed — must not be recorded."
            assert _read_alert_state("Crash", ticker_b) is not None, \
                "ticker_b send succeeded — must be recorded."
        finally:
            conn = _conn()
            conn.execute("DELETE FROM alert_state WHERE ticker IN (?, ?)", (ticker_a, ticker_b))
            conn.execute(
                "DELETE FROM system_notifications WHERE message_text LIKE ?",
                (f"%{MSG_MARKER}%",),
            )
            conn.commit()
            conn.close()
