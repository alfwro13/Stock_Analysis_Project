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

  LR-03 (4 Nov) reversed the fix for BUG 2: withholding record_alert_fired on a failed
  Talk send made every alert repeat on each 10-minute scan for as long as Talk failed
  (572 failures, 408 Moonshot rows for 11 conditions). notify() now stamps the dedup
  ledger once the in-app/log channels are written and retries only the Talk message.

Tests are grouped into three suites:

  TestSendTextMessageCredentials
      Verifies that send_text_message resolves credentials from os.environ
      regardless of what is in the config_data dict.

  TestDispatchAlerts
      Verifies that _dispatch_alerts stamps the dedup ledger and writes one in-app row
      whether the Talk send succeeds, returns False or raises, and that a failed message
      is queued for Talk-only retry.

  TestSendTextMessageStats
      Verifies the daily Talk counters and that a failure logs no URL or token.

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
    conn = _conn()
    conn.execute("DELETE FROM nextcloud_talk_stats")
    conn.commit()
    conn.close()


@pytest.fixture(autouse=True)
def _reset_talk_state():
    import notification_engine as ne
    ne._pending_talk.clear()
    ne._health_warning["last"] = None
    yield
    ne._pending_talk.clear()
    ne._health_warning["last"] = None
    conn = _conn()
    conn.execute("DELETE FROM system_notifications WHERE message_text LIKE 'Nextcloud Talk delivery is failing%'")
    conn.commit()
    conn.close()


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
#  Suite 2 — _dispatch_alerts with a failing Talk channel
# ═══════════════════════════════════════════════════════════════════════════════

@pytest.fixture
def _talk_configured():
    with patch.dict(os.environ, NC_ENV, clear=False):
        yield


@pytest.mark.usefixtures("_talk_configured")
class TestDispatchAlerts:
    """
    _dispatch_alerts routes through notification_engine.notify(). A failed Talk send
    must not leave the alert unrecorded: it is stamped in alert_state once the in-app
    row is written, and only the Talk message is queued for bounded retry. Routing
    falls back to the registry default (crash/moonshot -> log+in-app+Nextcloud all on)
    since TEST_CONFIG has no NOTIFICATION_ROUTING block.
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
        import notification_engine as ne
        with self._patch(result=True):
            orch._dispatch_alerts(
                "Crash", _make_alert_tuple(), db_conn, _msg_builder, _feed_builder
            )

        assert _read_alert_state("Crash", TEST_TICKER) is not None
        assert _read_notifications(), "notify() must write an in-app feed row on success."
        assert ne._pending_talk == []

    def test_failed_send_records_alert_once_and_queues_the_talk_message(self, orch, db_conn):
        import notification_engine as ne
        with self._patch(result=False):
            orch._dispatch_alerts(
                "Crash", _make_alert_tuple(), db_conn, _msg_builder, _feed_builder
            )

        assert _read_alert_state("Crash", TEST_TICKER) is not None, (
            "A failed Talk send must still stamp the dedup ledger, or the alert repeats every scan."
        )
        assert len(_read_notifications()) == 1
        assert [m["source"] for m in ne._pending_talk] == ["crash_alert"]

    def test_failed_send_suppresses_the_same_condition_on_the_next_scan(self, orch, db_conn):
        tuples = _make_alert_tuple()
        with self._patch(result=False):
            orch._dispatch_alerts("Crash", tuples, db_conn, _msg_builder, _feed_builder)
            suppressed = orch._evaluate_alert_gate(
                "Crash", TEST_TICKER, tuples[0][1]["price"], tuples[0][1]["reason"], db_conn
            )

        assert suppressed, "The second scan must find the condition already fired."

    def test_exception_during_send_records_alert_and_queues_the_message(self, orch, db_conn):
        import notification_engine as ne
        with self._patch(exc=RuntimeError("boom")):
            orch._dispatch_alerts(
                "Crash", _make_alert_tuple(), db_conn, _msg_builder, _feed_builder
            )

        assert _read_alert_state("Crash", TEST_TICKER) is not None
        assert len(ne._pending_talk) == 1

    def test_unconfigured_talk_records_alert_and_queues_nothing(self, orch, db_conn):
        import notification_engine as ne
        clean = {k: v for k, v in os.environ.items() if k not in NC_ENV}
        with patch.dict(os.environ, clean, clear=True), self._patch(result=False):
            orch._dispatch_alerts(
                "Crash", _make_alert_tuple(), db_conn, _msg_builder, _feed_builder
            )

        assert _read_alert_state("Crash", TEST_TICKER) is not None
        assert ne._pending_talk == []

    def test_moonshot_failed_send_records_alert(self, orch, db_conn):
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

        assert _read_alert_state("Moonshot", TEST_TICKER) is not None

    def test_multiple_tickers_partial_failure_records_both(self, orch, db_conn):
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

            assert _read_alert_state("Crash", ticker_a) is not None
            assert _read_alert_state("Crash", ticker_b) is not None
        finally:
            conn = _conn()
            conn.execute("DELETE FROM alert_state WHERE ticker IN (?, ?)", (ticker_a, ticker_b))
            conn.execute(
                "DELETE FROM system_notifications WHERE message_text LIKE ?",
                (f"%{MSG_MARKER}%",),
            )
            conn.commit()
            conn.close()


# ═══════════════════════════════════════════════════════════════════════════════
#  Suite 3 — daily counters and log content of send_text_message
# ═══════════════════════════════════════════════════════════════════════════════

class TestSendTextMessageStats:
    @staticmethod
    def _today():
        from database import get_nextcloud_talk_today
        return get_nextcloud_talk_today()

    def test_success_is_counted_and_state_is_ok(self):
        with patch.dict(os.environ, NC_ENV, clear=False), patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock()
            from nextcloud_talk import send_text_message
            assert send_text_message("hello", {}) is True

        today = self._today()
        assert today["sent"] == 1 and today["failed"] == 0 and today["state"] == "ok"

    def test_http_404_is_counted_and_logs_status_without_url_or_token(self, caplog):
        import requests
        response = MagicMock(status_code=404)
        error = requests.exceptions.HTTPError(
            f"404 Client Error: Not Found for url: {NC_ENV['NEXTCLOUD_URL']}/ocs/v2.php/apps/spreed/api/v1/chat/{NC_ENV['NEXTCLOUD_CONVERSATION_TOKEN']}",
            response=response,
        )
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = error
        with patch.dict(os.environ, NC_ENV, clear=False), patch("requests.post", return_value=mock_resp):
            from nextcloud_talk import send_text_message
            with caplog.at_level("ERROR", logger="nextcloud_talk"):
                assert send_text_message("hello", {}) is False

        today = self._today()
        assert today["failed"] == 1 and today["state"] == "failing" and today["last_error"] == "HTTP 404"
        assert "HTTP 404" in caplog.text
        assert NC_ENV["NEXTCLOUD_CONVERSATION_TOKEN"] not in caplog.text
        assert NC_ENV["NEXTCLOUD_URL"] not in caplog.text

    def test_connection_error_is_counted_by_exception_class(self):
        import requests
        with patch.dict(os.environ, NC_ENV, clear=False), patch("requests.post", side_effect=requests.exceptions.ConnectionError("unreachable")):
            from nextcloud_talk import send_text_message
            send_text_message("hello", {})

        assert self._today()["last_error"] == "ConnectionError"

    def test_missing_configuration_is_not_an_attempt(self):
        clean = {k: v for k, v in os.environ.items() if k not in NC_ENV}
        with patch.dict(os.environ, clean, clear=True):
            from nextcloud_talk import send_text_message
            assert send_text_message("hello", {}) is False

        assert self._today() == {"sent": 0, "failed": 0, "state": "idle", "last_error": None}

    def test_a_success_after_a_failure_turns_the_state_back_to_ok(self):
        import requests
        with patch.dict(os.environ, NC_ENV, clear=False):
            from nextcloud_talk import send_text_message
            with patch("requests.post", side_effect=requests.exceptions.ConnectionError("down")):
                send_text_message("hello", {})
            conn = _conn()
            conn.execute("UPDATE nextcloud_talk_stats SET last_failure_utc = '2000-01-01 00:00:00'")
            conn.commit()
            conn.close()
            with patch("requests.post", return_value=MagicMock()):
                send_text_message("hello", {})

        today = self._today()
        assert today["state"] == "ok" and today["sent"] == 1 and today["failed"] == 1
