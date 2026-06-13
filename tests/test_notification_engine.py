"""tests/test_notification_engine.py — unified notification router."""

import sys
from pathlib import Path
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
            "SELECT message_type, message_text FROM system_notifications WHERE message_text LIKE ?",
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


@pytest.fixture(autouse=True)
def _cleanup():
    yield
    _clear()


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

    def test_default_dicts_are_not_shared(self):
        a = ne.effective_routing("crash_alert", {})
        a["in_app"] = False
        b = ne.effective_routing("crash_alert", {})
        assert b["in_app"] is True


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
            result = ne.notify("network_fault", "Net", f"x {MARKER}", nextcloud_text="rich")
        send.assert_called_once()
        assert send.call_args[0][0] == "rich"
        assert result is True

    def test_nextcloud_not_called_when_disabled(self):
        with patch("notification_engine.load_config", return_value=self._routing(in_app=True)), \
             patch("notification_engine.nextcloud_talk.send_text_message") as send:
            result = ne.notify("network_fault", "Net", f"y {MARKER}")
        send.assert_not_called()
        assert result is True

    def test_returns_false_when_enabled_nextcloud_send_fails(self):
        with patch("notification_engine.load_config", return_value=self._routing(nextcloud_talk=True)), \
             patch("notification_engine.nextcloud_talk.send_text_message", return_value=False):
            assert ne.notify("network_fault", "Net", f"z {MARKER}") is False

    def test_returns_false_when_nextcloud_send_raises(self):
        with patch("notification_engine.load_config", return_value=self._routing(nextcloud_talk=True)), \
             patch("notification_engine.nextcloud_talk.send_text_message", side_effect=RuntimeError("boom")):
            assert ne.notify("network_fault", "Net", f"q {MARKER}") is False


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
