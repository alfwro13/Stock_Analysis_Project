"""MCP Remote Access notifications (MCP-15d): the six events, their dedup, the Notification Settings entry and the pairing-window check job."""
from datetime import datetime, timezone

import pytest
from apscheduler.schedulers.background import BackgroundScheduler

import db_mcp_oauth
import notification_engine as ne
import scheduler_engine
import scheduler_jobs
from constants import MCP_AUTH_FAILURES_MAX, MCP_NOTIFY_COOLDOWN_SECONDS, MCP_OAUTH_PAIRING_MINUTES, MCP_OAUTH_REFRESH_RETRY_GRACE_SECONDS
from database import get_connection
from mcp_server import events, limits, oauth

from .helpers import MCP_KEY, rpc
from .test_oauth_server import LAN_HOST, PUBLIC_HOST, refresh, remote, sql, start, tokens  # noqa: F401  remote is a fixture
from .test_public_host import OUT_OF_RANGE, _guarded, _settings

PUBLIC = {"Host": PUBLIC_HOST}


def texts(sent):
    return [item["text"] for item in sent]


class TestRegistry:
    def test_the_source_has_the_canonical_label_and_hangs_under_the_mcp_server(self):
        meta = ne.NOTIFICATION_SOURCES[events.SOURCE]
        assert meta["label"] == "MCP Remote Access" and meta["job_id"] == "claude_mcp_server_source"
        assert meta["default"] == {"log_file": True, "in_app": True, "nextcloud_talk": True}

    def test_its_rows_are_never_served_to_an_mcp_client(self):
        assert "alert_type" not in ne.NOTIFICATION_SOURCES[events.SOURCE]
        assert events.MESSAGE_TYPE not in ne.ALERT_MESSAGE_TYPES

    def test_the_panel_lists_it_under_the_mcp_server_row(self):
        panel = ne.build_routing_panel({})
        rows = [row for group in panel for job in group["jobs"] for row in job]
        position = next(i for i, row in enumerate(rows) if row["source"] == "claude_mcp_server_source")
        assert rows[position + 1]["source"] == events.SOURCE and rows[position + 1]["label"] == "MCP Remote Access"

    def test_events_go_through_the_router_with_a_prefixed_talk_text(self, remote_notifications):
        events.request_approved("K7P2QX")
        [sent] = remote_notifications
        assert sent["source"] == events.SOURCE and sent["type"] == "MCP Remote Access"
        assert sent["text"] == "Connection request K7P2QX was approved."
        assert sent["nextcloud_text"] == "MCP Remote Access: Connection request K7P2QX was approved."


class TestDelivery:
    def test_an_event_never_runs_notify_on_the_calling_thread(self, monkeypatch):
        queued, called = [], []
        monkeypatch.setattr(events, "_executor", type("Q", (), {"submit": lambda self, fn, *a: queued.append((fn, a))})())
        monkeypatch.setattr(events, "notify", lambda *a, **k: called.append(a))
        events.request_approved("ABC123")
        assert queued and not called
        fn, args = queued[0]
        fn(*args)
        assert len(called) == 1

    def test_a_failed_delivery_is_logged_not_raised(self, monkeypatch, caplog):
        def boom(*args, **kwargs):
            raise RuntimeError("talk is down")
        monkeypatch.setattr(events, "notify", boom)
        events.request_approved("ABC123")
        assert "Could not send an MCP Remote Access notification" in caplog.text

    def test_the_address_in_a_message_is_cleaned(self, remote_notifications):
        events.request_waiting("ABC123", "10.0.0.1\n\x07evil")
        assert "\n" not in remote_notifications[0]["text"] and "\x07" not in remote_notifications[0]["text"]


class TestRequestEvents:
    def test_a_new_request_announces_its_code_and_address(self, remote, remote_notifications):
        start(remote)
        code = sql("SELECT display_code FROM mcp_oauth_requests")[0]["display_code"]
        assert texts(remote_notifications) == [f"A connection request (code {code}) from testclient is waiting for approval."]

    def test_a_refused_request_announces_nothing(self, remote, remote_notifications):
        oauth.close_pairing_window()
        start(remote)
        assert remote_notifications == []

    def test_approval_is_announced_once(self, remote, remote_notifications):
        start(remote)
        request_id = sql("SELECT id, display_code FROM mcp_oauth_requests")[0]
        remote_notifications.clear()
        assert oauth.approve_request(request_id["id"]) and not oauth.approve_request(request_id["id"])
        assert texts(remote_notifications) == [f"Connection request {request_id['display_code']} was approved."]

    def test_a_denial_is_not_announced(self, remote, remote_notifications):
        start(remote)
        remote_notifications.clear()
        oauth.deny_request(sql("SELECT id FROM mcp_oauth_requests")[0]["id"])
        assert remote_notifications == []


class TestRefreshReuse:
    def test_a_reused_refresh_token_is_announced_once_when_the_grant_is_revoked(self, remote, remote_notifications):
        first = tokens(remote)
        refresh(remote, first["refresh_token"])
        remote.clock.advance(seconds=MCP_OAUTH_REFRESH_RETRY_GRACE_SECONDS + 1)
        remote_notifications.clear()
        assert refresh(remote, first["refresh_token"]).status_code == 400
        assert texts(remote_notifications) == ["A refresh token was used twice; the connected app was signed out."]
        assert refresh(remote, first["refresh_token"]).status_code == 400
        assert len(remote_notifications) == 1

    def test_a_retry_within_the_grace_announces_nothing(self, remote, remote_notifications):
        first = tokens(remote)
        refresh(remote, first["refresh_token"])
        remote_notifications.clear()
        assert refresh(remote, first["refresh_token"]).status_code == 400
        assert remote_notifications == []

    def test_an_ordinary_refresh_announces_nothing(self, remote, remote_notifications):
        first = tokens(remote)
        remote_notifications.clear()
        assert refresh(remote, first["refresh_token"]).status_code == 200
        assert remote_notifications == []


class TestLockoutAndRange:
    def test_five_wrong_secrets_at_the_token_endpoint_announce_one_lockout(self, remote, remote_notifications):
        remote_notifications.clear()
        for _ in range(MCP_AUTH_FAILURES_MAX + 2):
            remote.http.post("/oauth/token", data={"grant_type": "refresh_token"}, auth=(remote.client_id, "wrong"))
        assert texts(remote_notifications) == ["testclient was locked out for 5 minutes after 5 wrong credentials."]

    def test_five_wrong_mcp_keys_announce_one_lockout(self, remote, mcp_client, remote_notifications):
        for _ in range(MCP_AUTH_FAILURES_MAX + 2):
            rpc(mcp_client, "tools/list", key="wrong")
        assert texts(remote_notifications) == ["testclient was locked out for 5 minutes after 5 wrong credentials."]

    def test_a_request_outside_the_range_is_announced_once_per_address(self, monkeypatch, remote_notifications):
        _settings(monkeypatch)
        _, client = _guarded(address=OUT_OF_RANGE)
        for _ in range(3):
            assert client.post("/mcp").status_code == 403
        assert texts(remote_notifications) == [f"A request from {OUT_OF_RANGE} was refused: outside the allowed address range."]

    def test_a_private_peer_is_announced_with_the_proxy_hint(self, monkeypatch, remote_notifications):
        _settings(monkeypatch)
        _, client = _guarded(address="192.168.1.72")
        for _ in range(3):
            assert client.post("/mcp").status_code == 403
        assert texts(remote_notifications) == [
            "A request from 192.168.1.72 was refused: it is a private address, so the reverse proxy is probably not trusted in FORWARDED_ALLOW_IPS."
        ]

    def test_a_request_inside_the_range_announces_nothing(self, monkeypatch, remote_notifications):
        _settings(monkeypatch)
        _, client = _guarded()
        assert client.post("/mcp").status_code == 200
        assert remote_notifications == []


class TestCooldown:
    @pytest.fixture
    def clock(self, monkeypatch):
        now = [1000.0]
        monkeypatch.setattr(limits, "_now", lambda: now[0])
        return now

    def test_a_repeat_waits_for_the_cooldown_counted_from_the_send(self, clock, remote_notifications):
        events.lockout_tripped("203.0.113.5")
        clock[0] += MCP_NOTIFY_COOLDOWN_SECONDS - 1
        events.lockout_tripped("203.0.113.5")
        assert len(remote_notifications) == 1
        clock[0] += 1
        events.lockout_tripped("203.0.113.5")
        assert len(remote_notifications) == 2

    def test_a_calendar_day_alone_never_re_arms_it(self, clock, remote_notifications):
        events.off_range_request("203.0.113.5")
        clock[0] += 3600 - 5
        events.off_range_request("203.0.113.5")
        assert len(remote_notifications) == 1

    def test_each_address_and_each_kind_has_its_own_cooldown(self, clock, remote_notifications):
        events.lockout_tripped("203.0.113.5")
        events.lockout_tripped("203.0.113.6")
        events.off_range_request("203.0.113.5")
        assert len(remote_notifications) == 3

    def test_reset_clears_the_cooldowns(self, clock, remote_notifications):
        events.lockout_tripped("203.0.113.5")
        limits.reset()
        events.lockout_tripped("203.0.113.5")
        assert len(remote_notifications) == 2


class TestPairingWindowCheck:
    def _until(self):
        return db_mcp_oauth.get_client()["pairing_open_until"]

    def test_an_unused_window_is_announced_once_it_has_ended(self, remote, remote_notifications):
        until = self._until()
        remote.clock.advance(minutes=MCP_OAUTH_PAIRING_MINUTES, seconds=1)
        assert oauth.check_pairing_window(until) is True
        assert texts(remote_notifications) == ["The pairing window closed without a connection request."]

    def test_a_window_with_a_request_in_it_is_not_announced(self, remote, remote_notifications):
        until = self._until()
        start(remote)
        remote_notifications.clear()
        remote.clock.advance(minutes=MCP_OAUTH_PAIRING_MINUTES, seconds=1)
        assert oauth.check_pairing_window(until) is False
        assert remote_notifications == []

    def test_a_denied_or_expired_request_still_counts_as_use(self, remote, remote_notifications):
        until = self._until()
        start(remote)
        oauth.deny_request(sql("SELECT id FROM mcp_oauth_requests")[0]["id"])
        remote_notifications.clear()
        remote.clock.advance(minutes=MCP_OAUTH_PAIRING_MINUTES, seconds=1)
        assert oauth.check_pairing_window(until) is False

    def test_a_window_that_has_not_ended_is_left_alone(self, remote, remote_notifications):
        assert oauth.check_pairing_window(self._until()) is False
        assert remote_notifications == []

    def test_a_window_that_was_closed_or_replaced_is_left_alone(self, remote, remote_notifications):
        old = self._until()
        remote.clock.advance(minutes=1)
        oauth.open_pairing_window()
        remote.clock.advance(minutes=MCP_OAUTH_PAIRING_MINUTES)
        assert oauth.check_pairing_window(old) is False
        oauth.close_pairing_window()
        assert oauth.check_pairing_window(self._until() or old) is False
        assert remote_notifications == []

    def test_no_client_means_nothing_to_announce(self, remote_notifications):
        assert db_mcp_oauth.get_client() is None
        assert oauth.check_pairing_window("2026-10-21 12:10:00") is False

    def test_the_end_of_a_window_is_the_value_the_job_is_armed_with(self, remote):
        assert oauth.open_pairing_window() == self._until()
        oauth.close_pairing_window()
        assert oauth.open_pairing_window() == self._until()


class TestScheduledJob:
    @pytest.fixture(autouse=True)
    def live_scheduler(self, monkeypatch):
        # Jobs added to a scheduler that has not started are only queued and never replace each other, so these tests start a paused one.
        replica = BackgroundScheduler(timezone=timezone.utc)
        replica.start(paused=True)
        monkeypatch.setattr(scheduler_engine, "scheduler", replica)
        yield replica
        replica.shutdown(wait=False)
        conn = get_connection()
        try:
            conn.execute("DELETE FROM scheduler_run_log WHERE job_id = 'mcp_pairing_window_check_job'")
            conn.execute("DELETE FROM scheduler_run_history WHERE job_id = 'mcp_pairing_window_check_job'")
            conn.commit()
        finally:
            conn.close()

    def test_arming_schedules_one_utc_job_at_the_end_of_the_window(self):
        scheduler_engine.arm_mcp_pairing_window_check("2031-05-06 07:08:09")
        job = scheduler_engine.scheduler.get_job("mcp_pairing_window_check_job")
        assert job.args == ("2031-05-06 07:08:09",)
        assert job.trigger.run_date == datetime(2031, 5, 6, 7, 8, 9, tzinfo=timezone.utc)

    def test_a_window_opened_later_replaces_the_earlier_check(self):
        scheduler_engine.arm_mcp_pairing_window_check("2031-05-06 07:08:09")
        scheduler_engine.arm_mcp_pairing_window_check("2031-05-06 07:20:00")
        jobs = [j for j in scheduler_engine.scheduler.get_jobs() if j.id == "mcp_pairing_window_check_job"]
        assert len(jobs) == 1 and jobs[0].args == ("2031-05-06 07:20:00",)

    def test_a_reload_arms_a_window_that_is_still_open(self, remote):
        until = oauth.open_pairing_window()
        scheduler_engine.rearm_mcp_pairing_window_check()
        job = scheduler_engine.scheduler.get_job("mcp_pairing_window_check_job")
        assert job is not None and job.args == (until,)

    def test_a_reload_arms_nothing_for_a_closed_window(self, remote):
        oauth.close_pairing_window()
        scheduler_engine.rearm_mcp_pairing_window_check()
        assert scheduler_engine.scheduler.get_job("mcp_pairing_window_check_job") is None

    def test_the_runner_checks_the_window_and_records_the_run(self, remote, remote_notifications):
        until = oauth.open_pairing_window()
        remote.clock.advance(minutes=MCP_OAUTH_PAIRING_MINUTES, seconds=1)
        scheduler_jobs.run_mcp_pairing_window_check(until)
        assert texts(remote_notifications) == ["The pairing window closed without a connection request."]
        assert "mcp_pairing_window_check_job" in scheduler_engine.get_all_job_last_runs()

    def test_a_failing_check_is_reported_and_still_recorded(self, monkeypatch):
        def boom(armed_until):
            raise RuntimeError("database is locked")
        sent = []
        monkeypatch.setattr(oauth, "check_pairing_window", boom)
        monkeypatch.setattr(scheduler_jobs, "log_sched_notification", lambda kind, text: sent.append((kind, text)))
        scheduler_jobs.run_mcp_pairing_window_check("2031-05-06 07:08:09")
        assert sent == [("Error", "MCP Pairing Window Check failed: database is locked")]
        assert "mcp_pairing_window_check_job" in scheduler_engine.get_all_job_last_runs()

    def test_the_job_has_its_canonical_label(self):
        assert scheduler_engine.job_label("mcp_pairing_window_check_job") == "MCP Pairing Window Check"
