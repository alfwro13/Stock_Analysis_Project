"""/mcp throttles: per-key calls/minute and concurrency, per-address failed-credential lockout kept apart from the dashboard login."""
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch

import pytest
from starlette.testclient import TestClient

import db_mcp_keys
from constants import (
    MCP_AUTH_FAILURE_WINDOW_SECONDS, MCP_AUTH_FAILURES_MAX, MCP_AUTH_LOCKOUT_SECONDS, MCP_RATE_CALLS_PER_MINUTE,
    MCP_RATE_CONCURRENT_CALLS, MCP_REFUSAL_LOG_SECONDS,
)
from database import get_connection
from mcp_server import keys, limits

from .helpers import MCP_KEY, RPC_HEADERS, call, rpc


class Clock:
    def __init__(self):
        self.now = 10_000.0

    def __call__(self):
        return self.now

    def advance(self, seconds):
        self.now += seconds


@pytest.fixture
def clock(monkeypatch):
    fake = Clock()
    monkeypatch.setattr(limits, "_now", fake)
    return fake


def audit_statuses():
    conn = get_connection()
    try:
        return [r[0] for r in conn.execute("SELECT status FROM mcp_audit_log ORDER BY id")]
    finally:
        conn.close()


def login_throttle_row():
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM login_throttle").fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


@pytest.fixture
def second_key():
    key = "second-mcp-key-do-not-use-in-production"
    key_id = db_mcp_keys.add_key("Second key", keys.hash_key(key), "2026-10-01 09:00:00")
    keys.invalidate()
    try:
        yield key
    finally:
        db_mcp_keys.delete_key(key_id)
        keys.invalidate()


class TestCallRate:
    def test_the_call_after_the_limit_is_refused_until_the_oldest_ages_out(self, clock):
        for _ in range(MCP_RATE_CALLS_PER_MINUTE):
            assert limits.acquire(1) == 0
            limits.release(1)
            clock.advance(0.5)
        assert limits.acquire(1) > 0
        clock.advance(30)
        assert limits.acquire(1) == 0

    def test_retry_after_is_the_time_until_the_oldest_call_leaves_the_window(self, clock):
        for _ in range(MCP_RATE_CALLS_PER_MINUTE):
            assert limits.acquire(1) == 0
            limits.release(1)
        clock.advance(20)
        assert limits.acquire(1) == 40
        clock.advance(40)
        assert limits.acquire(1) == 0

    def test_a_refused_call_is_not_counted(self, clock):
        for _ in range(MCP_RATE_CALLS_PER_MINUTE):
            limits.acquire(1)
            limits.release(1)
        for _ in range(10):
            assert limits.acquire(1) > 0
        clock.advance(60)
        assert limits.acquire(1) == 0

    def test_keys_are_counted_separately(self, clock):
        for _ in range(MCP_RATE_CALLS_PER_MINUTE):
            limits.acquire(1)
            limits.release(1)
        assert limits.acquire(1) > 0
        assert limits.acquire(2) == 0


class TestConcurrency:
    def test_calls_in_flight_over_the_limit_wait_one_second(self):
        for _ in range(MCP_RATE_CONCURRENT_CALLS):
            assert limits.acquire(1) == 0
        assert limits.acquire(1) == 1
        assert limits.acquire(2) == 0
        limits.release(1)
        assert limits.acquire(1) == 0

    def test_release_without_a_slot_is_harmless(self):
        limits.release(99)
        assert limits.acquire(99) == 0


class TestFailureLockout:
    def test_the_fifth_failure_inside_the_window_locks_the_address_out(self, clock):
        for _ in range(MCP_AUTH_FAILURES_MAX - 1):
            assert limits.record_failure("203.0.113.5") is False
            assert limits.lockout_remaining("203.0.113.5") == 0
        assert limits.record_failure("203.0.113.5") is True
        assert limits.lockout_remaining("203.0.113.5") == MCP_AUTH_LOCKOUT_SECONDS
        assert limits.lockout_remaining("203.0.113.6") == 0
        clock.advance(MCP_AUTH_LOCKOUT_SECONDS - 1)
        assert limits.lockout_remaining("203.0.113.5") == 1
        clock.advance(1)
        assert limits.lockout_remaining("203.0.113.5") == 0

    def test_failures_spread_over_more_than_the_window_never_lock(self, clock):
        for _ in range(3 * MCP_AUTH_FAILURES_MAX):
            assert limits.record_failure("203.0.113.5") is False
            clock.advance(MCP_AUTH_FAILURE_WINDOW_SECONDS / (MCP_AUTH_FAILURES_MAX - 1) + 1)

    def test_a_lockout_restarts_the_count(self, clock):
        for _ in range(MCP_AUTH_FAILURES_MAX):
            limits.record_failure("203.0.113.5")
        clock.advance(MCP_AUTH_LOCKOUT_SECONDS + 1)
        assert limits.record_failure("203.0.113.5") is False

    def test_tracking_is_bounded_and_expired_entries_are_swept(self, clock, monkeypatch):
        monkeypatch.setattr(limits, "MCP_THROTTLE_MAX_ENTRIES", 3)
        for index in range(6):
            limits.record_failure(f"198.51.100.{index}")
        assert list(limits._failures) == ["198.51.100.3", "198.51.100.4", "198.51.100.5"]
        clock.advance(MCP_AUTH_FAILURE_WINDOW_SECONDS + 31)
        limits.record_failure("198.51.100.99")
        assert list(limits._failures) == ["198.51.100.99"]

    def test_refusals_are_logged_once_per_window(self, clock):
        assert limits.first_refusal(("address", "a")) is True
        assert limits.first_refusal(("address", "a")) is False
        assert limits.first_refusal(("address", "b")) is True
        clock.advance(MCP_REFUSAL_LOG_SECONDS)
        assert limits.first_refusal(("address", "a")) is True


class TestHttpCallRate:
    def test_over_the_limit_is_429_with_retry_after_and_one_audit_row(self, mcp_client, monkeypatch):
        monkeypatch.setattr(limits, "MCP_RATE_CALLS_PER_MINUTE", 3)
        assert [rpc(mcp_client, "tools/list").status_code for _ in range(3)] == [200, 200, 200]
        refused = [rpc(mcp_client, "tools/list") for _ in range(4)]
        assert [r.status_code for r in refused] == [429] * 4
        assert 1 <= int(refused[0].headers["Retry-After"]) <= 60
        assert refused[0].json() == {"detail": "Too many MCP requests for this key"}
        assert audit_statuses() == [200, 200, 200, 429]

    def test_another_key_is_not_affected(self, mcp_client, second_key, monkeypatch):
        monkeypatch.setattr(limits, "MCP_RATE_CALLS_PER_MINUTE", 2)
        assert [rpc(mcp_client, "tools/list").status_code for _ in range(3)] == [200, 200, 429]
        assert rpc(mcp_client, "tools/list", key=second_key).status_code == 200

    def test_the_window_slides(self, mcp_client, clock, monkeypatch):
        monkeypatch.setattr(limits, "MCP_RATE_CALLS_PER_MINUTE", 2)
        assert [rpc(mcp_client, "tools/list").status_code for _ in range(3)] == [200, 200, 429]
        clock.advance(61)
        assert rpc(mcp_client, "tools/list").status_code == 200

    def test_calls_in_flight_over_the_limit_are_refused_while_the_event_loop_keeps_answering(self, mcp_client, monkeypatch):
        monkeypatch.setattr(limits, "MCP_RATE_CONCURRENT_CALLS", 2)
        gate, entered = threading.Event(), threading.Semaphore(0)

        def stalled(*args, **kwargs):
            entered.release()
            assert gate.wait(10)
            return [], 0.0

        with patch("mcp_server.tools.get_accounts.list_scope_accounts_with_values", side_effect=stalled):
            with ThreadPoolExecutor(max_workers=4) as pool:
                parked = [pool.submit(call, mcp_client, "get_accounts") for _ in range(2)]
                try:
                    assert entered.acquire(timeout=10) and entered.acquire(timeout=10)
                    refused = pool.submit(rpc, mcp_client, "tools/list").result(timeout=3)
                    assert refused.status_code == 429 and refused.headers["Retry-After"] == "1"
                    assert pool.submit(mcp_client.get, "/static/css/styles.css").result(timeout=3).status_code == 200
                    assert not any(f.done() for f in parked)
                finally:
                    gate.set()
                assert [f.result(timeout=20).status_code for f in parked] == [200, 200]
        assert not limits._active
        assert rpc(mcp_client, "tools/list").status_code == 200

    def test_a_burst_of_parallel_calls_is_answered_and_leaks_no_slot(self, mcp_client):
        with ThreadPoolExecutor(max_workers=20) as pool:
            statuses = [f.result(timeout=30).status_code for f in [pool.submit(rpc, mcp_client, "tools/list") for _ in range(20)]]
        assert set(statuses) <= {200, 429} and 200 in statuses
        assert not limits._active
        assert rpc(mcp_client, "tools/list").status_code in {200, 429}

    def test_a_refused_body_and_a_failing_tool_give_their_slot_back(self, mcp_client, monkeypatch):
        monkeypatch.setattr(limits, "MCP_RATE_CONCURRENT_CALLS", 1)
        too_large = mcp_client.post("/mcp", headers={**RPC_HEADERS, "Authorization": f"Bearer {MCP_KEY}"}, content=b"x" * (64 * 1024 + 1))
        assert too_large.status_code == 413
        with patch("mcp_server.tools.get_accounts.portfolio_metrics_engine.other_accounts_list", side_effect=RuntimeError("boom")):
            assert call(mcp_client, "get_accounts").json()["result"]["isError"] is True
        assert not limits._active
        assert rpc(mcp_client, "tools/list").status_code == 200


class TestHttpLockout:
    def test_five_wrong_keys_lock_the_address_even_for_the_right_key(self, mcp_client, clock, caplog):
        with caplog.at_level(logging.WARNING, logger="mcp_server.auth"):
            first = [rpc(mcp_client, "tools/list", key=f"wrong-{i}").status_code for i in range(MCP_AUTH_FAILURES_MAX)]
        assert first == [401] * MCP_AUTH_FAILURES_MAX
        assert "Locked testclient out of /mcp" in caplog.text
        assert "wrong-" not in caplog.text
        locked = [rpc(mcp_client, "tools/list"), rpc(mcp_client, "tools/list", key="wrong"), rpc(mcp_client, "tools/list", key=None)]
        assert [r.status_code for r in locked] == [429, 429, 429]
        assert 1 <= int(locked[0].headers["Retry-After"]) <= MCP_AUTH_LOCKOUT_SECONDS
        assert all("www-authenticate" not in r.headers for r in locked)
        assert audit_statuses() == [401] * MCP_AUTH_FAILURES_MAX + [429]
        clock.advance(MCP_AUTH_LOCKOUT_SECONDS + 1)
        assert rpc(mcp_client, "tools/list").status_code == 200

    def test_the_lockout_is_per_address(self, mcp_client):
        for i in range(MCP_AUTH_FAILURES_MAX):
            rpc(mcp_client, "tools/list", key=f"wrong-{i}")
        assert rpc(mcp_client, "tools/list").status_code == 429
        elsewhere = TestClient(mcp_client.app, base_url="http://localhost", client=("203.0.113.9", 4000), raise_server_exceptions=False)
        assert rpc(elsewhere, "tools/list").status_code == 200

    def test_the_dashboard_login_throttle_is_not_touched(self, mcp_client):
        before = login_throttle_row()
        for i in range(MCP_AUTH_FAILURES_MAX + 2):
            rpc(mcp_client, "tools/list", key=f"wrong-{i}")
        assert login_throttle_row() == before

    def test_the_home_assistant_header_counts_as_a_failure(self, mcp_client):
        statuses = [rpc(mcp_client, "tools/list", key=None, headers={"X-API-Key": "ha-key"}).status_code for _ in range(MCP_AUTH_FAILURES_MAX + 1)]
        assert statuses == [403] * MCP_AUTH_FAILURES_MAX + [429]

    def test_right_keys_and_a_disabled_server_never_count(self, mcp_client, monkeypatch):
        for _ in range(MCP_AUTH_FAILURES_MAX + 2):
            assert rpc(mcp_client, "tools/list").status_code == 200
        monkeypatch.setattr("mcp_server.auth.load_config", lambda: {"MCP_SERVER_ENABLED": False})
        for _ in range(MCP_AUTH_FAILURES_MAX + 2):
            assert rpc(mcp_client, "tools/list", key="wrong").status_code == 503
        assert limits.lockout_remaining("testclient") == 0
