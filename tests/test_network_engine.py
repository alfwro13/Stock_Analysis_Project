"""
tests/test_network_engine.py — unit tests for tools/network_engine.py

Covers the one-time IPv6 latch (trigger, dedup, restore) and session routing.
No real network calls; curl_cffi Session is mocked throughout.
"""
import json
import logging
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
    """Reset IPv6 latch, rate-limit circuit breaker, init-guard, and the cached-session pool
    before and after every test — the session cache is module-level state, so a stale cached
    MagicMock from an earlier test would otherwise silently satisfy a later test's `is mock_session`
    assertion for the wrong reason."""
    original_flag = ne._IPV6_FAULT_FLAG
    ne.GLOBAL_IPV6_STATUS.update({"is_failing": False, "last_error": "", "last_fail_time": 0.0})
    ne._latch_initialized = False
    ne._RATE_LIMIT_READY.set()
    ne._routing_counter = 0
    ne._session_cache.clear()
    yield
    ne.GLOBAL_IPV6_STATUS.update({"is_failing": False, "last_error": "", "last_fail_time": 0.0})
    ne._latch_initialized = False
    ne._RATE_LIMIT_READY.set()
    ne._routing_counter = 0
    ne._session_cache.clear()
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
        mock_session.close.assert_not_called()

    def test_ipv4_session_is_reused_across_calls(self):
        mock_session = MagicMock()
        with patch("tools.network_engine.load_config", return_value=self._ipv4_only_cfg), \
             patch("tools.network_engine.cffi_requests.Session", return_value=mock_session) as mock_ctor, \
             patch("tools.network_engine._patch_session_with_retries"), \
             patch("tools.network_engine._maybe_restore_latch"), \
             patch("tools.network_engine._increment_api_stat"):
            with ne.yahoo_connection_boundary("first") as session_a:
                assert session_a is mock_session
            with ne.yahoo_connection_boundary("second") as session_b:
                assert session_b is mock_session
        mock_ctor.assert_called_once()
        mock_session.close.assert_not_called()

    def test_ipv4_only_failover_session_never_reused(self):
        # The IPv6-only failover branch is deliberately excluded from session caching (see
        # _session_cache's module docstring) — each call must still build (and close) its own
        # fresh session.
        mock_session_1 = MagicMock()
        mock_session_2 = MagicMock()
        with patch("tools.network_engine.load_config", return_value=self._ipv6_only_cfg), \
             patch("tools.network_engine.create_failover_session", side_effect=[mock_session_1, mock_session_2]) as mock_cfs, \
             patch("tools.network_engine._maybe_restore_latch"), \
             patch("tools.network_engine._increment_api_stat"):
            with ne.yahoo_connection_boundary("first") as session_a:
                assert session_a is mock_session_1
            with ne.yahoo_connection_boundary("second") as session_b:
                assert session_b is mock_session_2
        assert mock_cfs.call_count == 2
        mock_session_1.close.assert_called_once()
        mock_session_2.close.assert_called_once()

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
        mock_cfs.assert_called_once_with("::1", "test", self._ipv6_only_cfg, lock=None)
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


class TestPatchSessionWithRetries:
    def test_success_response_returned_as_is(self):
        session = MagicMock()
        ok_response = MagicMock(status_code=200)
        session.request = MagicMock(return_value=ok_response)
        ne._patch_session_with_retries(session, "test-ctx", timeout=5, max_retries=3)
        assert session.request("GET", "http://example.com") is ok_response

    def test_retries_on_500_then_succeeds(self):
        session = MagicMock()
        ok_response = MagicMock(status_code=200)
        err_response = MagicMock(status_code=500)
        original = MagicMock(side_effect=[err_response, ok_response])
        session.request = original
        ne._patch_session_with_retries(session, "test-ctx", timeout=5, max_retries=3)
        with patch("tools.network_engine.time.sleep"):
            result = session.request("GET", "http://example.com")
        assert result is ok_response
        assert original.call_count == 2

    def test_raises_transient_http_error_after_exhausting_retries_on_500(self):
        session = MagicMock()
        err_response = MagicMock(status_code=500)
        original = MagicMock(return_value=err_response)
        session.request = original
        ne._patch_session_with_retries(session, "test-ctx", timeout=5, max_retries=2)
        with patch("tools.network_engine.time.sleep"):
            with pytest.raises(ne._TransientHTTPError, match="test-ctx"):
                session.request("GET", "http://example.com")
        assert original.call_count == 3

    def test_429_raises_rate_limited_error_without_retry(self):
        session = MagicMock()
        response_429 = MagicMock(status_code=429)
        original = MagicMock(return_value=response_429)
        session.request = original
        with patch("tools.network_engine._enter_yahoo_rate_limit") as mock_enter:
            ne._patch_session_with_retries(session, "test-ctx", timeout=5, max_retries=3)
            with pytest.raises(ne._RateLimitedError):
                session.request("GET", "http://example.com")
        mock_enter.assert_called_once_with("test-ctx")
        assert original.call_count == 1

    def test_second_call_does_not_rewrap_a_reused_session(self):
        # A cached session (see _cached_plain_session) is patched again on every reuse purely to
        # refresh the thread-local action_context/lock — it must not nest a second retry wrapper
        # around the first, or a single transient error would sleep/log twice.
        session = MagicMock()
        ok_response = MagicMock(status_code=200)
        original = MagicMock(return_value=ok_response)
        session.request = original
        ne._patch_session_with_retries(session, "first-ctx", timeout=5, max_retries=3)
        wrapped_once = session.request
        ne._patch_session_with_retries(session, "second-ctx", timeout=5, max_retries=3)
        assert session.request is wrapped_once

    def test_reused_session_retry_error_reports_latest_action_context(self):
        # The wrapper reads action_context from the per-thread _retry_context at call time, not
        # from whichever call first wrapped the session — so a cached session's error messages
        # stay accurate across reuse for a different ticker/call.
        session = MagicMock()
        err_response = MagicMock(status_code=500)
        original = MagicMock(return_value=err_response)
        session.request = original
        ne._patch_session_with_retries(session, "first-ctx", timeout=5, max_retries=0)
        ne._patch_session_with_retries(session, "second-ctx", timeout=5, max_retries=0)
        with patch("tools.network_engine.time.sleep"):
            with pytest.raises(ne._TransientHTTPError, match="second-ctx"):
                session.request("GET", "http://example.com")

    def test_retry_sleep_releases_and_reacquires_lock(self):
        session = MagicMock()
        ok_response = MagicMock(status_code=200)
        err_response = MagicMock(status_code=500)
        original = MagicMock(side_effect=[err_response, ok_response])
        session.request = original
        mock_lock = MagicMock()
        ne._patch_session_with_retries(session, "test-ctx", timeout=5, max_retries=3, lock=mock_lock)
        with patch("tools.network_engine.time.sleep"):
            result = session.request("GET", "http://example.com")
        assert result is ok_response
        mock_lock.release.assert_called_once()
        mock_lock.__enter__.assert_called_once()


class TestSleepOutsideLock:
    def test_no_lock_just_sleeps(self):
        with patch("tools.network_engine.time.sleep") as mock_sleep:
            ne._sleep_outside_lock(1.5, lock=None)
        mock_sleep.assert_called_once_with(1.5)

    def test_releases_before_sleeping_and_reacquires_after(self):
        mock_lock = MagicMock()
        calls = []
        mock_lock.release.side_effect = lambda: calls.append("release")
        mock_lock.__enter__.side_effect = lambda: calls.append("enter")
        with patch("tools.network_engine.time.sleep", side_effect=lambda s: calls.append("sleep")):
            ne._sleep_outside_lock(1.0, lock=mock_lock)
        assert calls == ["release", "sleep", "enter"]

    def test_reacquires_even_if_sleep_raises(self):
        mock_lock = MagicMock()
        with patch("tools.network_engine.time.sleep", side_effect=RuntimeError("boom")):
            with pytest.raises(RuntimeError):
                ne._sleep_outside_lock(1.0, lock=mock_lock)
        mock_lock.__enter__.assert_called_once()


class TestCreateFailoverSession:
    def test_retries_on_500_then_succeeds(self):
        ok_response = MagicMock(status_code=200)
        err_response = MagicMock(status_code=500)
        mock_session = MagicMock()
        original = MagicMock(side_effect=[err_response, ok_response])
        mock_session.request = original
        with patch("tools.network_engine.cffi_requests.Session", return_value=mock_session), \
             patch("tools.network_engine.time.sleep"):
            session = ne.create_failover_session("::1", "test-ctx", {})
            result = session.request("GET", "http://example.com")
        assert result is ok_response
        assert original.call_count == 2

    def test_raises_transient_http_error_after_exhausting_retries_and_skips_ipv6_fault_alert(self):
        err_response = MagicMock(status_code=500)
        mock_session = MagicMock()
        original = MagicMock(return_value=err_response)
        mock_session.request = original
        with patch("tools.network_engine.cffi_requests.Session", return_value=mock_session), \
             patch("tools.network_engine.time.sleep"), \
             patch("tools.network_engine._trigger_fallback_alert") as mock_alert:
            session = ne.create_failover_session("::1", "test-ctx", {})
            with pytest.raises(ne._TransientHTTPError, match="test-ctx"):
                session.request("GET", "http://example.com")
        mock_alert.assert_not_called()
        assert original.call_count == 4


class TestYfErrorNoiseFilter:
    def setup_method(self):
        ne._ensure_yf_error_filter()
        ne._yf_logged_error_local.count = 0

    def test_demotes_delisted_message_while_suppression_active(self):
        record = logging.LogRecord("yfinance", logging.ERROR, __file__, 1, "$FAKE.L: possibly delisted; no price data found", None, None)
        ne.suppress_yf_delisted_noise(True)
        try:
            keep = ne._YfErrorNoiseFilter().filter(record)
        finally:
            ne.suppress_yf_delisted_noise(False)
        assert keep is True
        assert record.levelno == logging.DEBUG
        assert ne._yf_logged_error_local.count == 1

    def test_leaves_delisted_message_at_error_when_suppression_inactive(self):
        record = logging.LogRecord("yfinance", logging.ERROR, __file__, 1, "$FAKE.L: possibly delisted; no price data found", None, None)
        ne.suppress_yf_delisted_noise(False)
        keep = ne._YfErrorNoiseFilter().filter(record)
        assert keep is True
        assert record.levelno == logging.ERROR
        assert ne._yf_logged_error_local.count == 1

    def test_leaves_unrelated_error_message_untouched_even_when_suppressed(self):
        record = logging.LogRecord("yfinance", logging.ERROR, __file__, 1, "HTTP Error 404: something else entirely", None, None)
        ne.suppress_yf_delisted_noise(True)
        try:
            ne._YfErrorNoiseFilter().filter(record)
        finally:
            ne.suppress_yf_delisted_noise(False)
        assert record.levelno == logging.ERROR
        assert ne._yf_logged_error_local.count == 1


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
