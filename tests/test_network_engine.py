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
    """Reset IPv6 latch, rate-limit circuit breaker, and init-guard before and after every test."""
    original_flag = ne._IPV6_FAULT_FLAG
    ne.GLOBAL_IPV6_STATUS.update({"is_failing": False, "last_error": "", "last_fail_time": 0.0})
    ne._latch_initialized = False
    ne._RATE_LIMIT_READY.set()
    ne._routing_counter = 0
    yield
    ne.GLOBAL_IPV6_STATUS.update({"is_failing": False, "last_error": "", "last_fail_time": 0.0})
    ne._latch_initialized = False
    ne._RATE_LIMIT_READY.set()
    ne._routing_counter = 0
    ne._IPV6_FAULT_FLAG = original_flag


# ── _trigger_fallback_alert ───────────────────────────────────────────────────

class TestTriggerFallbackAlert:
    def _call(self, tmp_path):
        ne._IPV6_FAULT_FLAG = tmp_path / "fault.flag"
        with patch("tools.network_engine.notify") as mock_notify:
            ne._trigger_fallback_alert("::1", "test-ctx", "err-summary", "trace-detail", {})
        return mock_notify

    def test_sets_is_failing_latch(self, tmp_path):
        self._call(tmp_path)
        assert ne.GLOBAL_IPV6_STATUS["is_failing"] is True

    def test_records_error_and_fail_time(self, tmp_path):
        self._call(tmp_path)
        assert ne.GLOBAL_IPV6_STATUS["last_error"] == "err-summary"
        assert ne.GLOBAL_IPV6_STATUS["last_fail_time"] > 0

    def test_dispatches_notification_through_router(self, tmp_path):
        mock_notify = self._call(tmp_path)
        mock_notify.assert_called_once()
        args, kwargs = mock_notify.call_args
        assert args[0] == "network_fault"
        assert args[1] == "Network Fault"
        assert "test-ctx" in args[2]
        assert "err-summary" in args[2]

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
        with patch("tools.network_engine.notify") as mock_notify:
            ne._trigger_fallback_alert("::1", "ctx", "err", "trace", {})
        mock_notify.assert_not_called()


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
    _ipv4_only_cfg = {"YAHOO_IPV6_ADDRESS": "", "YAHOO_USE_IPV4": True, "YAHOO_USE_IPV6": False}
    _ipv6_only_cfg = {"YAHOO_IPV6_ADDRESS": "::1", "YAHOO_USE_IPV4": False, "YAHOO_USE_IPV6": True}
    _dual_cfg      = {"YAHOO_IPV6_ADDRESS": "::1", "YAHOO_USE_IPV4": True,  "YAHOO_USE_IPV6": True}

    def test_yields_standard_session_when_no_ipv6_configured(self):
        mock_session = MagicMock()
        with patch("tools.network_engine.load_config", return_value=self._ipv4_only_cfg), \
             patch("tools.network_engine.cffi_requests.Session", return_value=mock_session), \
             patch("tools.network_engine._patch_session_with_retries"), \
             patch("tools.network_engine._maybe_restore_latch"), \
             patch("tools.network_engine._increment_api_stat"):
            with ne.yahoo_connection_boundary("test") as session:
                assert session is mock_session
        mock_session.close.assert_called_once()

    def test_falls_back_to_standard_when_latch_is_set(self):
        ne.GLOBAL_IPV6_STATUS["is_failing"] = True
        mock_session = MagicMock()
        with patch("tools.network_engine.load_config", return_value=self._ipv6_only_cfg), \
             patch("tools.network_engine.cffi_requests.Session", return_value=mock_session), \
             patch("tools.network_engine._patch_session_with_retries"), \
             patch("tools.network_engine._maybe_restore_latch"), \
             patch("tools.network_engine._increment_api_stat"):
            with ne.yahoo_connection_boundary("test") as session:
                assert session is mock_session

    def test_calls_create_failover_session_when_ipv6_only(self):
        mock_session = MagicMock()
        with patch("tools.network_engine.load_config", return_value=self._ipv6_only_cfg), \
             patch("tools.network_engine.create_failover_session", return_value=mock_session) as mock_cfs, \
             patch("tools.network_engine._maybe_restore_latch"), \
             patch("tools.network_engine._increment_api_stat"):
            with ne.yahoo_connection_boundary("test") as session:
                assert session is mock_session
        mock_cfs.assert_called_once_with("::1", "test", self._ipv6_only_cfg)
        mock_session.close.assert_called_once()

    def test_dual_mode_uses_plain_ipv6_session(self):
        mock_session = MagicMock()
        ne._routing_counter = 1  # force ipv6 on first call (odd counter → ipv6)
        with patch("tools.network_engine.load_config", return_value=self._dual_cfg), \
             patch("tools.network_engine.cffi_requests.Session", return_value=mock_session), \
             patch("tools.network_engine._patch_session_with_retries") as mock_patch, \
             patch("tools.network_engine._maybe_restore_latch"), \
             patch("tools.network_engine._increment_api_stat"):
            with ne.yahoo_connection_boundary("test") as session:
                assert session is mock_session
        mock_patch.assert_called_once()

    def test_dual_mode_uses_ipv4_session(self):
        mock_session = MagicMock()
        ne._routing_counter = 0  # force ipv4 (even counter → ipv4)
        with patch("tools.network_engine.load_config", return_value=self._dual_cfg), \
             patch("tools.network_engine.cffi_requests.Session", return_value=mock_session), \
             patch("tools.network_engine._patch_session_with_retries") as mock_patch, \
             patch("tools.network_engine._maybe_restore_latch"), \
             patch("tools.network_engine._increment_api_stat"):
            with ne.yahoo_connection_boundary("test") as session:
                assert session is mock_session
        mock_patch.assert_called_once()

    def test_stat_incremented_on_success(self):
        mock_session = MagicMock()
        with patch("tools.network_engine.load_config", return_value=self._ipv4_only_cfg), \
             patch("tools.network_engine.cffi_requests.Session", return_value=mock_session), \
             patch("tools.network_engine._patch_session_with_retries"), \
             patch("tools.network_engine._maybe_restore_latch"), \
             patch("tools.network_engine._increment_api_stat") as mock_inc:
            with ne.yahoo_connection_boundary("test"):
                pass
        mock_inc.assert_called_once_with("ipv4", "success", "test", 0)

    def test_stat_incremented_as_error_on_exception(self):
        mock_session = MagicMock()
        with patch("tools.network_engine.load_config", return_value=self._ipv4_only_cfg), \
             patch("tools.network_engine.cffi_requests.Session", return_value=mock_session), \
             patch("tools.network_engine._patch_session_with_retries"), \
             patch("tools.network_engine._maybe_restore_latch"), \
             patch("tools.network_engine._increment_api_stat") as mock_inc:
            with pytest.raises(RuntimeError):
                with ne.yahoo_connection_boundary("test"):
                    raise RuntimeError("boom")
        mock_inc.assert_called_once_with("ipv4", "error", "test", 0)

    def test_stat_incremented_as_429_on_rate_limit(self):
        mock_session = MagicMock()
        with patch("tools.network_engine.load_config", return_value=self._ipv4_only_cfg), \
             patch("tools.network_engine.cffi_requests.Session", return_value=mock_session), \
             patch("tools.network_engine._patch_session_with_retries"), \
             patch("tools.network_engine._maybe_restore_latch"), \
             patch("tools.network_engine._increment_api_stat") as mock_inc:
            with pytest.raises(ne._RateLimitedError):
                with ne.yahoo_connection_boundary("test"):
                    raise ne._RateLimitedError("rate limited")
        mock_inc.assert_called_once_with("ipv4", "429", "test", 0)

    def test_yfinance_logged_errors_counted_without_being_raised(self):
        import logging as _logging
        mock_session = MagicMock()
        with patch("tools.network_engine.load_config", return_value=self._ipv4_only_cfg), \
             patch("tools.network_engine.cffi_requests.Session", return_value=mock_session), \
             patch("tools.network_engine._patch_session_with_retries"), \
             patch("tools.network_engine._maybe_restore_latch"), \
             patch("tools.network_engine._increment_api_stat") as mock_inc:
            with ne.yahoo_connection_boundary("test"):
                _logging.getLogger("yfinance").error("$FAKE.L: possibly delisted; no price data found")
        mock_inc.assert_called_once_with("ipv4", "success", "test", 1)


# ── _select_interface ─────────────────────────────────────────────────────────

class TestSelectInterface:
    def test_ipv4_only(self):
        assert ne._select_interface(True, False) == "ipv4"

    def test_ipv6_only(self):
        assert ne._select_interface(False, True) == "ipv6"

    def test_dual_alternates(self):
        ne._routing_counter = 0
        assert ne._select_interface(True, True) == "ipv4"
        assert ne._select_interface(True, True) == "ipv6"
        assert ne._select_interface(True, True) == "ipv4"

    def test_neither_defaults_to_ipv4(self):
        assert ne._select_interface(False, False) == "ipv4"


# ── get_yahoo_api_stats (DB function) ─────────────────────────────────────────

class TestGetYahooApiStats:
    def test_returns_empty_list_when_no_rows(self):
        import database as db
        rows = db.get_yahoo_api_stats(days=8)
        assert isinstance(rows, list)

    def test_returns_inserted_rows(self):
        import database as db
        conn = db.get_connection()
        try:
            conn.execute(
                "INSERT OR REPLACE INTO yahoo_api_stats "
                "(date, total_calls, ipv4_calls, ipv6_calls, rate_limit_429, other_errors) "
                "VALUES ('2099-01-01', 5, 3, 2, 1, 0)"
            )
            conn.commit()
        finally:
            conn.close()
        rows = db.get_yahoo_api_stats(days=8)
        dates = [r["date"] for r in rows]
        assert "2099-01-01" in dates
        row = next(r for r in rows if r["date"] == "2099-01-01")
        assert row["total_calls"] == 5
        assert row["ipv4_calls"] == 3
        assert row["ipv6_calls"] == 2
        assert row["rate_limit_429"] == 1
        assert row["other_errors"] == 0

    def test_respects_days_limit(self):
        import database as db
        conn = db.get_connection()
        try:
            for i in range(10):
                conn.execute(
                    "INSERT OR REPLACE INTO yahoo_api_stats "
                    "(date, total_calls, ipv4_calls, ipv6_calls, rate_limit_429, other_errors) "
                    "VALUES (?, 1, 1, 0, 0, 0)",
                    ("2099-02-%02d" % (i + 1),),
                )
            conn.commit()
        finally:
            conn.close()
        rows = db.get_yahoo_api_stats(days=3)
        assert len(rows) <= 3


# ── yahoo_api_call_log (per-call detail + job attribution) ───────────────────

class TestYahooApiCallLog:
    def test_increment_api_stat_captures_job_source_and_action_context(self):
        with patch("tools.network_engine.current_job_source", return_value="quant_analysis_job"), \
             patch.object(ne, "_ensure_stats_writer"):
            ne._increment_api_stat("ipv4", "success", "Ticker Info: AAPL")
        call_time, date_str, interface, status, job_id, action_context, yf_errors = ne._stats_queue.get_nowait()
        assert interface == "ipv4"
        assert status == "success"
        assert job_id == "quant_analysis_job"
        assert action_context == "Ticker Info: AAPL"
        assert date_str == call_time[:10]
        assert yf_errors == 0

    def test_increment_api_stat_carries_yfinance_logged_error_count(self):
        with patch("tools.network_engine.current_job_source", return_value=None), \
             patch.object(ne, "_ensure_stats_writer"):
            ne._increment_api_stat("ipv6", "success", "Ticker Info: SMGB.L", yf_errors=2)
        *_, yf_errors = ne._stats_queue.get_nowait()
        assert yf_errors == 2

    def test_write_call_log_entry_inserts_row(self):
        import database as db
        ne._write_call_log_entry("2099-03-01 10:00:00", "2099-03-01", "ipv6", "success", "quant_analysis_job", "Ticker Info: MSFT")
        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM yahoo_api_call_log WHERE date = '2099-03-01'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row["interface"] == "ipv6"
        assert row["job_id"] == "quant_analysis_job"
        assert row["action_context"] == "Ticker Info: MSFT"

    def test_write_call_log_entry_allows_null_job_id(self):
        import database as db
        ne._write_call_log_entry("2099-03-02 10:00:00", "2099-03-02", "ipv4", "error", None, "Ticker Info: TSLA")
        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM yahoo_api_call_log WHERE date = '2099-03-02'"
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row["job_id"] is None

    def test_prune_call_log_removes_rows_older_than_retention(self):
        import database as db
        conn = db.get_connection()
        try:
            conn.execute(
                "INSERT INTO yahoo_api_call_log (call_time, date, interface, status, job_id, action_context) "
                "VALUES ('2000-01-01 00:00:00', '2000-01-01', 'ipv4', 'success', NULL, NULL)"
            )
            conn.commit()
        finally:
            conn.close()
        ne._last_call_log_prune = 0.0
        ne._maybe_prune_call_log()
        conn = db.get_connection()
        try:
            row = conn.execute("SELECT 1 FROM yahoo_api_call_log WHERE date = '2000-01-01'").fetchone()
        finally:
            conn.close()
        assert row is None

    def test_prune_call_log_throttled(self):
        ne._last_call_log_prune = time.time()
        with patch("database.get_connection") as mock_conn:
            ne._maybe_prune_call_log()
        mock_conn.assert_not_called()


class TestGetYahooApiCallLog:
    def test_returns_empty_list_for_unknown_date(self):
        import database as db
        assert db.get_yahoo_api_call_log("2099-04-01") == []

    def test_aggregates_by_minute_job_and_status(self):
        import database as db
        conn = db.get_connection()
        try:
            for _ in range(3):
                conn.execute(
                    "INSERT INTO yahoo_api_call_log (call_time, date, interface, status, job_id, action_context) "
                    "VALUES ('2099-04-02 09:05:12', '2099-04-02', 'ipv4', 'success', 'quant_analysis_job', 'x')"
                )
            conn.commit()
        finally:
            conn.close()
        rows = db.get_yahoo_api_call_log("2099-04-02")
        assert len(rows) == 1
        assert rows[0]["minute_ts"] == "2099-04-02 09:05"
        assert rows[0]["job_id"] == "quant_analysis_job"
        assert rows[0]["call_count"] == 3
