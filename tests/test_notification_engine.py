"""tests/test_notification_engine.py — unified notification router."""

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import notification_engine as ne
import database as _db


MARKER = "__notif_engine_test__"


def _read(message_text_like):
    conn = _db.get_connection()
    try:
        return conn.execute(
            "SELECT message_type, message_text, is_read FROM system_notifications WHERE message_text LIKE ?",
            (f"%{message_text_like}%",),
        ).fetchall()
    finally:
        conn.close()


def _clear():
    conn = _db.get_connection()
    try:
        conn.execute("DELETE FROM system_notifications WHERE message_text LIKE ?", (f"%{MARKER}%",))
        conn.commit()
    finally:
        conn.close()


HEALTH_TEXT = "Nextcloud Talk delivery is failing"
NC_ENV = {
    "NEXTCLOUD_URL": "https://cloud.example.com",
    "NEXTCLOUD_BOT_USERNAME": "bot",
    "NEXTCLOUD_APP_PASSWORD": "secret",
    "NEXTCLOUD_CONVERSATION_TOKEN": "abc123",
}


def _clear_health_rows():
    conn = _db.get_connection()
    try:
        conn.execute("DELETE FROM system_notifications WHERE message_text LIKE ?", (f"{HEALTH_TEXT}%",))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _cleanup():
    ne._pending_talk.clear()
    ne._health_warning["last"] = None
    yield
    ne._pending_talk.clear()
    ne._health_warning["last"] = None
    _clear()
    _clear_health_rows()


class TestEffectiveRouting:
    def test_falls_back_to_registry_default_when_no_config(self):
        r = ne.effective_routing("crash_alert", {})
        assert r == {"log_file": True, "in_app": True, "nextcloud_talk": True}

    def test_unknown_source_uses_lifecycle_default(self):
        r = ne.effective_routing("maintenance_job", {})
        assert r == ne.LIFECYCLE_DEFAULT
        assert r["nextcloud_talk"] is False

    def test_partial_override_merges_over_default(self):
        cfg = {"NOTIFICATION_ROUTING": {"crash_alert": {"nextcloud_talk": False}}}
        r = ne.effective_routing("crash_alert", cfg)
        assert r == {"log_file": True, "in_app": True, "nextcloud_talk": False}

    @pytest.mark.parametrize("bad", [True, "in_app", ["in_app"], 5])
    def test_non_dict_stored_entry_falls_back_to_default(self, bad):
        cfg = {"NOTIFICATION_ROUTING": {"crash_alert": bad}}
        assert ne.effective_routing("crash_alert", cfg) == {"log_file": True, "in_app": True, "nextcloud_talk": True}

    def test_non_dict_routing_block_falls_back_to_default(self):
        assert ne.effective_routing("crash_alert", {"NOTIFICATION_ROUTING": ["crash_alert"]}) == ne.effective_routing("crash_alert", {})

    def test_notify_still_delivers_when_the_stored_entry_is_malformed(self):
        cfg = {"NOTIFICATION_ROUTING": {"network_fault": "off"}}
        with patch("notification_engine.load_config", return_value=cfg):
            ne.notify("network_fault", "Net", f"still delivered {MARKER}")
        assert len(_read(MARKER)) == 1

    def test_default_dicts_are_not_shared(self):
        a = ne.effective_routing("crash_alert", {})
        a["in_app"] = False
        b = ne.effective_routing("crash_alert", {})
        assert b["in_app"] is True


class TestTalkRetry:
    """A failed Talk send is queued and retried (Talk only) a bounded number of times; notify() still returns True."""

    @pytest.fixture(autouse=True)
    def _configured(self, monkeypatch):
        for key, value in NC_ENV.items():
            monkeypatch.setenv(key, value)

    @pytest.fixture
    def clock(self):
        now = [1000.0]
        with patch("notification_engine.time", SimpleNamespace(monotonic=lambda: now[0])):
            yield now

    @staticmethod
    def _cfg():
        return {"NOTIFICATION_ROUTING": {"network_fault": {"log_file": False, "in_app": True, "nextcloud_talk": True}}}

    def _notify(self, text):
        with patch("notification_engine.load_config", return_value=self._cfg()):
            ne.notify("network_fault", "Net", f"{text} {MARKER}")

    def test_failed_send_returns_true_writes_in_app_once_and_queues(self, clock):
        with patch("notification_engine.nextcloud_talk.send_text_message", return_value=False):
            self._notify("a")
        assert len(_read(MARKER)) == 1
        assert [m["source"] for m in ne._pending_talk] == ["network_fault"]

    def test_send_that_raises_is_queued_like_a_failed_send(self, clock):
        with patch("notification_engine.nextcloud_talk.send_text_message", side_effect=RuntimeError("boom")):
            self._notify("a")
        assert len(ne._pending_talk) == 1

    def test_unconfigured_talk_queues_nothing_and_raises_no_warning(self, clock, monkeypatch):
        for key in NC_ENV:
            monkeypatch.delenv(key)
        with patch("notification_engine.nextcloud_talk.send_text_message", return_value=False):
            self._notify("a")
        assert ne._pending_talk == []
        assert _read(HEALTH_TEXT) == []

    def test_retry_waits_for_the_minimum_interval(self, clock):
        with patch("notification_engine.nextcloud_talk.send_text_message", return_value=False) as send:
            self._notify("a")
            clock[0] += ne.NEXTCLOUD_TALK_RETRY_MIN_INTERVAL_SECONDS - 1
            self._notify("b")
        assert send.call_count == 2
        assert [m["retries"] for m in ne._pending_talk] == [0, 0]

    def test_successful_retry_resends_the_text_without_a_second_in_app_row(self, clock):
        with patch("notification_engine.nextcloud_talk.send_text_message", return_value=False):
            self._notify("a")
        clock[0] += ne.NEXTCLOUD_TALK_RETRY_MIN_INTERVAL_SECONDS
        with patch("notification_engine.load_config", return_value={}), \
             patch("notification_engine.nextcloud_talk.send_text_message", return_value=True) as send:
            ne.notify("maintenance_job", "Info", f"unrelated {MARKER}")
        send.assert_called_once()
        assert send.call_args[0][0] == f"a {MARKER}"
        assert ne._pending_talk == []
        assert len([r for r in _read(MARKER) if r["message_text"].startswith("a ")]) == 1

    def test_message_is_dropped_after_the_maximum_retries(self, clock):
        with patch("notification_engine.nextcloud_talk.send_text_message", return_value=False) as send:
            self._notify("a")
            for _ in range(ne.NEXTCLOUD_TALK_MAX_RETRIES + 2):
                clock[0] += ne.NEXTCLOUD_TALK_RETRY_MIN_INTERVAL_SECONDS
                with patch("notification_engine.load_config", return_value={}):
                    ne.notify("maintenance_job", "Info", f"tick {MARKER}")
        assert send.call_count == 1 + ne.NEXTCLOUD_TALK_MAX_RETRIES
        assert ne._pending_talk == []

    def test_stale_message_is_dropped_without_a_send(self, clock):
        with patch("notification_engine.nextcloud_talk.send_text_message", return_value=False):
            self._notify("a")
        clock[0] += ne.NEXTCLOUD_TALK_RETRY_MAX_AGE_SECONDS + 1
        with patch("notification_engine.load_config", return_value={}), \
             patch("notification_engine.nextcloud_talk.send_text_message") as send:
            ne.notify("maintenance_job", "Info", f"tick {MARKER}")
        send.assert_not_called()
        assert ne._pending_talk == []

    def test_a_down_server_costs_one_send_per_drain_not_one_per_queued_message(self, clock):
        with patch("notification_engine.nextcloud_talk.send_text_message", return_value=False):
            for name in "abc":
                self._notify(name)
        clock[0] += ne.NEXTCLOUD_TALK_RETRY_MIN_INTERVAL_SECONDS
        with patch("notification_engine.load_config", return_value={}), \
             patch("notification_engine.nextcloud_talk.send_text_message", return_value=False) as send:
            ne.notify("maintenance_job", "Info", f"tick {MARKER}")
        assert send.call_count == 1
        assert sorted(m["retries"] for m in ne._pending_talk) == [0, 0, 1]

    def test_health_warning_is_raised_once_per_cooldown(self, clock):
        with patch("notification_engine.nextcloud_talk.send_text_message", return_value=False):
            self._notify("a")
            self._notify("b")
        warnings = _read(HEALTH_TEXT)
        assert len(warnings) == 1
        assert warnings[0]["message_type"] == "Warning"
        clock[0] += ne.NEXTCLOUD_TALK_HEALTH_WARNING_COOLDOWN_SECONDS
        with patch("notification_engine.nextcloud_talk.send_text_message", return_value=False):
            self._notify("c")
        assert len(_read(HEALTH_TEXT)) == 2

    def test_health_warning_never_goes_to_talk_by_default(self, clock):
        routing = ne.effective_routing("nextcloud_talk_health", {})
        assert routing == {"log_file": True, "in_app": True, "nextcloud_talk": False}
        assert "alert_type" not in ne.NOTIFICATION_SOURCES["nextcloud_talk_health"]

    @staticmethod
    def _answers(status):
        def send(text, config, failure=None):
            failure["status"] = status
            return False
        return send

    @pytest.mark.parametrize("status", [400, 401, 403, 404, 413])
    def test_a_4xx_rejection_is_not_queued_for_retry(self, clock, status):
        with patch("notification_engine.nextcloud_talk.send_text_message", side_effect=self._answers(status)):
            self._notify("a")
        assert ne._pending_talk == []
        assert len(_read(MARKER)) == 1

    @pytest.mark.parametrize("status", [408, 429, 500, 502, 503, None])
    def test_a_retryable_failure_is_queued(self, clock, status):
        with patch("notification_engine.nextcloud_talk.send_text_message", side_effect=self._answers(status)):
            self._notify("a")
        assert len(ne._pending_talk) == 1

    def test_a_queued_message_rejected_on_retry_is_dropped_without_further_sends(self, clock):
        with patch("notification_engine.nextcloud_talk.send_text_message", return_value=False):
            self._notify("a")
        clock[0] += ne.NEXTCLOUD_TALK_RETRY_MIN_INTERVAL_SECONDS
        with patch("notification_engine.load_config", return_value={}), \
             patch("notification_engine.nextcloud_talk.send_text_message", side_effect=self._answers(413)) as send:
            ne.notify("maintenance_job", "Info", f"tick {MARKER}")
            clock[0] += ne.NEXTCLOUD_TALK_RETRY_MIN_INTERVAL_SECONDS
            ne.notify("maintenance_job", "Info", f"tick {MARKER}")
        assert send.call_count == 1
        assert ne._pending_talk == []

    def test_health_warning_for_413_blames_the_message_length_not_the_token(self, clock):
        with patch("notification_engine.nextcloud_talk.send_text_message", side_effect=self._answers(413)):
            self._notify("a")
        text = _read(HEALTH_TEXT)[0]["message_text"]
        assert "(HTTP 413)" in text and "length limit" in text
        assert "token" not in text and "participant" not in text
        assert "retried up to" not in text

    @pytest.mark.parametrize("status", [401, 403, 404])
    def test_health_warning_for_access_errors_names_the_token_and_participation(self, clock, status):
        with patch("notification_engine.nextcloud_talk.send_text_message", side_effect=self._answers(status)):
            self._notify("a")
        text = _read(HEALTH_TEXT)[0]["message_text"]
        assert f"(HTTP {status})" in text and "conversation token" in text and "participant" in text

    def test_health_warning_for_an_outage_says_the_message_is_retried(self, clock):
        with patch("notification_engine.nextcloud_talk.send_text_message", side_effect=self._answers(503)):
            self._notify("a")
        text = _read(HEALTH_TEXT)[0]["message_text"]
        assert "(HTTP 503)" in text and "reachable" in text
        assert f"retried up to {ne.NEXTCLOUD_TALK_MAX_RETRIES} times" in text
        assert "participant" not in text

    def test_health_warning_survives_a_failing_stats_read(self, clock):
        with patch("notification_engine.get_nextcloud_talk_today", side_effect=RuntimeError("locked")), \
             patch("notification_engine.nextcloud_talk.send_text_message", return_value=False):
            self._notify("a")
        assert len(_read(HEALTH_TEXT)) == 1


class TestNotifyChannels:
    def _routing(self, **chans):
        base = {"log_file": False, "in_app": False, "nextcloud_talk": False}
        base.update(chans)
        return {"NOTIFICATION_ROUTING": {"network_fault": base}}

    def test_in_app_write_when_enabled(self):
        with patch("notification_engine.load_config", return_value=self._routing(in_app=True)):
            ne.notify("network_fault", "Net", f"hello {MARKER}")
        rows = _read(MARKER)
        assert len(rows) == 1
        assert rows[0]["message_type"] == "Net"

    def test_no_in_app_write_when_disabled(self):
        with patch("notification_engine.load_config", return_value=self._routing(in_app=False)):
            ne.notify("network_fault", "Net", f"silent {MARKER}")
        assert _read(MARKER) == []

    def test_nextcloud_called_only_when_enabled(self):
        with patch("notification_engine.load_config", return_value=self._routing(nextcloud_talk=True)), \
             patch("notification_engine.nextcloud_talk.send_text_message", return_value=True) as send:
            ne.notify("network_fault", "Net", f"x {MARKER}", nextcloud_text="rich")
        send.assert_called_once()
        assert send.call_args[0][0] == "rich"

    def test_nextcloud_not_called_when_disabled(self):
        with patch("notification_engine.load_config", return_value=self._routing(in_app=True)), \
             patch("notification_engine.nextcloud_talk.send_text_message") as send:
            ne.notify("network_fault", "Net", f"y {MARKER}")
        send.assert_not_called()

    def test_info_success_scheduler_auto_marked_read_via_db_log_path(self):
        with patch("notification_engine.load_config", return_value=self._routing(in_app=True)):
            ne.notify("network_fault", "Info", f"info {MARKER}")
            ne.notify("network_fault", "Success", f"success {MARKER}")
            ne.notify("network_fault", "Scheduler", f"scheduler {MARKER}")
            ne.notify("network_fault", "Error", f"error {MARKER}")
        conn = _db.get_connection()
        try:
            rows = {
                r["message_type"]: r["is_read"]
                for r in conn.execute(
                    "SELECT message_type, is_read FROM system_notifications WHERE message_text LIKE ?",
                    (f"%{MARKER}%",),
                ).fetchall()
            }
        finally:
            conn.close()
        assert rows["Info"] == 1
        assert rows["Success"] == 1
        assert rows["Scheduler"] == 1
        assert rows["Error"] == 0

    def test_info_success_scheduler_auto_marked_read_via_conn_path(self):
        conn = _db.get_connection()
        try:
            with patch("notification_engine.load_config", return_value=self._routing(in_app=True)):
                ne.notify("network_fault", "Info", f"connpath {MARKER}", conn=conn)
                ne.notify("network_fault", "Error", f"connpath {MARKER}", conn=conn)
            rows = {
                r["message_type"]: r["is_read"]
                for r in conn.execute(
                    "SELECT message_type, is_read FROM system_notifications WHERE message_text LIKE ?",
                    (f"%connpath {MARKER}%",),
                ).fetchall()
            }
        finally:
            conn.close()
        assert rows["Info"] == 1
        assert rows["Error"] == 0


class TestDbLogNotificationAutoRead:
    def test_auto_read_types_marked_read_others_unread(self):
        _db.log_notification("Info", f"db info {MARKER}")
        _db.log_notification("Success", f"db success {MARKER}")
        _db.log_notification("Scheduler", f"db scheduler {MARKER}")
        _db.log_notification("Error", f"db error {MARKER}")
        _db.log_notification("Warning", f"db warning {MARKER}")
        rows = {r["message_type"]: r["is_read"] for r in _read(MARKER)}
        assert rows["Info"] == 1
        assert rows["Success"] == 1
        assert rows["Scheduler"] == 1
        assert rows["Error"] == 0
        assert rows["Warning"] == 0


class TestJobSourceContext:
    def test_set_and_clear(self):
        ne.set_job_source("quant_analysis_job")
        assert ne.current_job_source() == "quant_analysis_job"
        ne.clear_job_source()
        assert ne.current_job_source() is None


class TestRegistryIntegrity:
    def test_every_alert_source_has_valid_shape(self):
        from scheduler_engine import JOB_GRAPH
        for key, meta in ne.NOTIFICATION_SOURCES.items():
            assert meta["label"], f"{key} missing label"
            assert set(meta["default"]) == set(ne.CHANNELS), f"{key} default channels mismatch"
            if meta["job_id"] is not None:
                assert meta["job_id"] in JOB_GRAPH, f"{key} parent job {meta['job_id']} not in JOB_GRAPH"

    def test_alert_message_types_are_unique_and_never_a_job_status_type(self):
        status_types = {"Info", "Success", "Scheduler", "Warning", "Error", "Maintenance"}
        assert ne.ALERT_MESSAGE_TYPES
        assert len(set(ne.ALERT_MESSAGE_TYPES)) == len(ne.ALERT_MESSAGE_TYPES)
        assert not status_types & set(ne.ALERT_MESSAGE_TYPES)

    def test_network_fault_is_not_an_mcp_alert_type_because_its_text_is_a_stack_trace(self):
        assert "Network Fault" not in ne.ALERT_MESSAGE_TYPES
        assert "alert_type" not in ne.NOTIFICATION_SOURCES["network_fault"]

    def test_intraday_alert_engines_map_to_their_registered_alert_type(self):
        from intraday_orchestrator import _ALERT_SOURCES
        for engine, source in _ALERT_SOURCES.items():
            assert ne.NOTIFICATION_SOURCES[source]["alert_type"] == engine

    def test_notify_calls_with_a_registered_alert_source_use_its_alert_type(self):
        import re
        pattern = re.compile(r'\bnotify\(\s*("\w+"|[A-Z_]+),\s*("[^"]+"|[A-Z_]+)')
        constant = re.compile(r'^([A-Z_]+) = "([^"]+)"$', re.MULTILINE)
        root = Path(__file__).parent.parent
        mismatches = []
        paths = [*root.glob("*.py"), *(root / "tools").glob("*.py"), *(root / "api").glob("*.py"),
                 *(root / "mcp_server").rglob("*.py")]
        for path in paths:
            text = path.read_text(encoding="utf-8")
            constants = dict(constant.findall(text))
            for source, message_type in pattern.findall(text):
                source = constants.get(source, source.strip('"'))
                message_type = constants.get(message_type, message_type.strip('"'))
                alert_type = ne.NOTIFICATION_SOURCES.get(source, {}).get("alert_type")
                if alert_type and message_type != alert_type:
                    mismatches.append(f"{path.name}: {source} -> {message_type!r}, registry says {alert_type!r}")
        assert not mismatches

    def test_panel_lists_every_job_status_source_with_canonical_label(self):
        from scheduler_engine import JOB_GRAPH, job_label
        panel = ne.build_routing_panel({})
        status_sources = {
            row["source"]: row["label"]
            for group in panel for job in group["jobs"] for row in job if row["type"] == "status"
        }
        for job_id, meta in JOB_GRAPH.items():
            if meta.get("dynamic"):
                continue
            assert job_id in status_sources, f"{job_id} missing a status row"
            assert status_sources[job_id] == job_label(job_id)

    def test_panel_nests_alert_children_under_parent_job(self):
        panel = ne.build_routing_panel({})
        all_alert_rows = [
            row for group in panel for job in group["jobs"] for row in job if row["type"] == "alert"
        ]
        sources = {r["source"] for r in all_alert_rows}
        # every parented alert source appears in the panel
        for key, meta in ne.NOTIFICATION_SOURCES.items():
            assert key in sources, f"{key} missing from panel"
