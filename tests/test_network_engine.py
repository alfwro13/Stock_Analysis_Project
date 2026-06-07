"""
tests/test_network_engine.py — unit tests for tools/network_engine.py

Covers the one-time IPv6 latch (trigger, dedup, restore) and session routing.
No real network calls; curl_cffi Session is mocked throughout.
"""
import json
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch, call

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))
sys.path.insert(0, str(Path(__file__).parent.parent / "tools"))

import tools.network_engine as ne


@pytest.fixture(autouse=True)
def reset_global_state():
    """Reset IPv6 latch and init-guard before and after every test."""
    original_flag = ne._IPV6_FAULT_FLAG
    ne.GLOBAL_IPV6_STATUS.update({"is_failing": False, "last_error": "", "last_fail_time": 0.0})
    ne._latch_initialized = False
    yield
    ne.GLOBAL_IPV6_STATUS.update({"is_failing": False, "last_error": "", "last_fail_time": 0.0})
    ne._latch_initialized = False
    ne._IPV6_FAULT_FLAG = original_flag


# ── _trigger_fallback_alert ───────────────────────────────────────────────────

class TestTriggerFallbackAlert:
    def _call(self, tmp_path):
        ne._IPV6_FAULT_FLAG = tmp_path / "fault.flag"
        mock_conn = MagicMock()
        with patch("tools.network_engine.get_connection", return_value=mock_conn), \
             patch("tools.network_engine.send_text_message"):
            ne._trigger_fallback_alert("::1", "test-ctx", "err-summary", "trace-detail", {})
        return mock_conn

    def test_sets_is_failing_latch(self, tmp_path):
        self._call(tmp_path)
        assert ne.GLOBAL_IPV6_STATUS["is_failing"] is True

    def test_records_error_and_fail_time(self, tmp_path):
        self._call(tmp_path)
        assert ne.GLOBAL_IPV6_STATUS["last_error"] == "err-summary"
        assert ne.GLOBAL_IPV6_STATUS["last_fail_time"] > 0

    def test_writes_notification_to_db(self, tmp_path):
        mock_conn = self._call(tmp_path)
        cursor = mock_conn.cursor.return_value
        cursor.execute.assert_called_once()
        sql, params = cursor.execute.call_args[0]
        assert "system_notifications" in sql
        assert params[0] == "Network Fault"
        assert "test-ctx" in params[1]
        assert "err-summary" in params[1]

    def test_writes_flag_file_with_timestamp(self, tmp_path):
        self._call(tmp_path)
        flag = tmp_path / "fault.flag"
        assert flag.exists()
        data = json.loads(flag.read_text())
        assert "timestamp" in data
        assert data["error"] == "err-summary"

    def test_second_call_is_noop(self, tmp_path):
        ne.GLOBAL_IPV6_STATUS["is_failing"] = True
        ne._IPV6_FAULT_FLAG = tmp_path / "fault.flag"
        with patch("tools.network_engine.get_connection") as mock_get_conn, \
             patch("tools.network_engine.send_text_message") as mock_nc:
            ne._trigger_fallback_alert("::1", "ctx", "err", "trace", {})
        mock_get_conn.assert_not_called()
        mock_nc.assert_not_called()


# ── _maybe_restore_latch ──────────────────────────────────────────────────────

class TestMaybeRestoreLatch:
    def test_sets_latch_for_recent_fault_flag(self, tmp_path):
        flag = tmp_path / "fault.flag"
        flag.write_text(json.dumps({"timestamp": time.time() - 60, "error": "recent"}))
        ne._IPV6_FAULT_FLAG = flag
        ne._maybe_restore_latch()
        assert ne.GLOBAL_IPV6_STATUS["is_failing"] is True

    def test_ignores_flag_older_than_one_hour(self, tmp_path):
        flag = tmp_path / "fault.flag"
        flag.write_text(json.dumps({"timestamp": time.time() - 7200, "error": "old"}))
        ne._IPV6_FAULT_FLAG = flag
        ne._maybe_restore_latch()
        assert ne.GLOBAL_IPV6_STATUS["is_failing"] is False

    def test_missing_flag_file_is_safe(self, tmp_path):
        ne._IPV6_FAULT_FLAG = tmp_path / "nonexistent.flag"
        ne._maybe_restore_latch()
        assert ne.GLOBAL_IPV6_STATUS["is_failing"] is False

    def test_corrupted_flag_file_is_safe(self, tmp_path):
        flag = tmp_path / "fault.flag"
        flag.write_text("{ not valid json")
        ne._IPV6_FAULT_FLAG = flag
        ne._maybe_restore_latch()
        assert ne.GLOBAL_IPV6_STATUS["is_failing"] is False

    def test_initialises_only_once_per_process(self, tmp_path):
        flag = tmp_path / "fault.flag"
        flag.write_text(json.dumps({"timestamp": time.time() - 60, "error": "test"}))
        ne._IPV6_FAULT_FLAG = flag
        ne._maybe_restore_latch()
        assert ne.GLOBAL_IPV6_STATUS["is_failing"] is True
        ne.GLOBAL_IPV6_STATUS["is_failing"] = False
        ne._maybe_restore_latch()
        assert ne.GLOBAL_IPV6_STATUS["is_failing"] is False, (
            "Second call must be a no-op — _latch_initialized guard failed"
        )


# ── yahoo_connection_boundary ─────────────────────────────────────────────────

class TestYahooConnectionBoundary:
    def test_yields_standard_session_when_no_ipv6_configured(self):
        mock_session = MagicMock()
        with patch("tools.network_engine.load_config", return_value={"YAHOO_IPV6_ADDRESS": ""}), \
             patch("tools.network_engine.cffi_requests.Session", return_value=mock_session), \
             patch("tools.network_engine._patch_session_with_retries"), \
             patch("tools.network_engine._maybe_restore_latch"):
            with ne.yahoo_connection_boundary("test") as session:
                assert session is mock_session
        mock_session.close.assert_called_once()

    def test_falls_back_to_standard_when_latch_is_set(self):
        ne.GLOBAL_IPV6_STATUS["is_failing"] = True
        mock_session = MagicMock()
        with patch("tools.network_engine.load_config", return_value={"YAHOO_IPV6_ADDRESS": "::1"}), \
             patch("tools.network_engine.cffi_requests.Session", return_value=mock_session), \
             patch("tools.network_engine._patch_session_with_retries"), \
             patch("tools.network_engine._maybe_restore_latch"):
            with ne.yahoo_connection_boundary("test") as session:
                assert session is mock_session

    def test_calls_create_failover_session_when_ipv6_available(self):
        mock_session = MagicMock()
        with patch("tools.network_engine.load_config", return_value={"YAHOO_IPV6_ADDRESS": "::1"}), \
             patch("tools.network_engine.create_failover_session", return_value=mock_session) as mock_cfs, \
             patch("tools.network_engine._maybe_restore_latch"):
            with ne.yahoo_connection_boundary("test") as session:
                assert session is mock_session
        mock_cfs.assert_called_once_with("::1", "test", {"YAHOO_IPV6_ADDRESS": "::1"})
        mock_session.close.assert_called_once()
