"""get_alerts tool: alert-only notifications, Trap Monitor phases, Referee confidence, day and row caps, empty log, read-only."""
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

from alert_referee_engine import CONFLUENCE_ENGINE, TRAP_MONITOR_ENGINE, get_recent_evaluations as real_get_recent_evaluations, log_veto_evaluation
from constants import MCP_ALERT_TEXT_MAX_CHARS, MCP_ALERTS_MAX_DAYS, MCP_ALERTS_MAX_ROWS
from database import get_connection
from notification_engine import ALERT_MESSAGE_TYPES

from .helpers import call, emptied_tables, rpc, table_counts, tool_data

_STAMP = "%Y-%m-%d %H:%M:%S"
_TABLES = ("system_notifications", "trap_monitor_results", "alert_referee_log")
NOT_ALERT_TYPES = ("Info", "Success", "Scheduler", "Warning", "Error", "Maintenance", "Network Fault", "CSV Import — Skipped Rows")


def _ago(**delta):
    return (datetime.now(timezone.utc) - timedelta(**delta)).strftime(_STAMP)


def _notify(message_type, text, timestamp, status="sent"):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO system_notifications (timestamp, message_type, message_text, status) VALUES (?, ?, ?, ?)",
            (timestamp, message_type, text, status),
        )
        conn.commit()
    finally:
        conn.close()


def _trap(ticker, phase, scan_ts, ema=-4.5, rsi=31.0):
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO trap_monitor_results (ticker, phase, bull_trap_level, bear_trap_level, cap_level, wyckoff_level, ema_distance, rsi, scan_ts) "
            "VALUES (?, ?, 'ELEVATED_RISK', 'SAFE', 'SAFE', 'SAFE', ?, ?, ?)",
            (ticker, phase, ema, rsi, scan_ts),
        )
        conn.commit()
    finally:
        conn.close()


def _referee(engine, ticker, phase, probability, would_veto, mode, scan_ts, direction=None):
    conn = get_connection()
    try:
        log_veto_evaluation(engine, ticker, phase, probability, would_veto, mode, None, conn, direction=direction)
        conn.execute("UPDATE alert_referee_log SET scan_ts = ? WHERE id = (SELECT MAX(id) FROM alert_referee_log)", (scan_ts,))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def alert_tables():
    with emptied_tables(*_TABLES):
        yield


class TestGetAlerts:
    def _alerts(self, mcp_client, **arguments):
        return tool_data(call(mcp_client, "get_alerts", arguments))

    def test_envelope_units_and_default_window(self, mcp_client, alert_tables):
        _trap("ZZAL1", "ACTIVE_SELLOFF", _ago(minutes=10))
        data = self._alerts(mcp_client)
        assert set(data) == {"as_of", "age_seconds", "market_state", "base_currency", "units", "stale", "missing", "data"}
        assert {"days", "notifications", "trap_monitor", "referee"} <= set(data["units"])
        assert data["data"]["days"] == 7
        assert set(data["data"]) == {"days", "since", "notifications", "trap_monitor", "referee"}
        assert data["as_of"] is not None and data["missing"] == []

    def test_notifications_are_alerts_only_newest_first_and_inside_the_window(self, mcp_client, alert_tables):
        _notify("Crash", "**ZZAL1** crashed", _ago(days=2))
        _notify("TrapMonitor", "**ZZAL2** — Phase: BULL TRAP RISK", _ago(hours=1), status="sent")
        _notify("Moonshot", "too old to count", _ago(days=9))
        for index, status_type in enumerate(NOT_ALERT_TYPES):
            _notify(status_type, f"job status {index}: Traceback (most recent call last)", _ago(minutes=index + 1))
        rows = self._alerts(mcp_client)["data"]["notifications"]["rows"]
        assert [r["type"] for r in rows] == ["TrapMonitor", "Crash"]
        assert rows[0]["text"] == "**ZZAL2** — Phase: BULL TRAP RISK" and rows[0]["status"] == "sent"
        assert rows[0]["at"].endswith("Z")
        assert "Traceback" not in str(rows)

    def test_every_registered_alert_type_is_returned(self, mcp_client, alert_tables):
        for index, alert_type in enumerate(ALERT_MESSAGE_TYPES):
            _notify(alert_type, f"alert {index}", _ago(minutes=index))
        part = self._alerts(mcp_client)["data"]["notifications"]
        assert {r["type"] for r in part["rows"]} == set(ALERT_MESSAGE_TYPES) and part["truncated"] is False

    def test_a_new_unregistered_type_is_hidden_until_registered(self, mcp_client, alert_tables):
        _notify("BrandNewStatus", "raw Traceback (most recent call last)", _ago(minutes=1))
        assert self._alerts(mcp_client)["data"]["notifications"]["rows"] == []

    def test_days_widens_and_narrows_the_window(self, mcp_client, alert_tables):
        _notify("Crash", "old", _ago(days=9))
        _notify("Crash", "recent", _ago(hours=2))
        assert [r["text"] for r in self._alerts(mcp_client, days=1)["data"]["notifications"]["rows"]] == ["recent"]
        assert [r["text"] for r in self._alerts(mcp_client, days=10)["data"]["notifications"]["rows"]] == ["recent", "old"]

    @pytest.mark.parametrize("days", [0, -1, MCP_ALERTS_MAX_DAYS + 1, 365])
    def test_days_outside_the_cap_is_refused(self, mcp_client, alert_tables, days):
        result = call(mcp_client, "get_alerts", {"days": days}).json()["result"]
        assert result["isError"] is True

    def test_the_cap_itself_is_accepted(self, mcp_client, alert_tables):
        assert self._alerts(mcp_client, days=MCP_ALERTS_MAX_DAYS)["data"]["days"] == MCP_ALERTS_MAX_DAYS

    def test_notification_rows_are_capped_and_flagged_truncated(self, mcp_client, alert_tables):
        for index in range(MCP_ALERTS_MAX_ROWS + 3):
            _notify("Crash", f"ZZAL{index}", _ago(minutes=index))
        part = self._alerts(mcp_client)["data"]["notifications"]
        assert len(part["rows"]) == MCP_ALERTS_MAX_ROWS and part["truncated"] is True
        assert part["rows"][0]["text"] == "ZZAL0"

    def test_exactly_the_cap_is_not_truncated(self, mcp_client, alert_tables):
        for index in range(MCP_ALERTS_MAX_ROWS):
            _notify("Crash", f"ZZAL{index}", _ago(minutes=index))
        part = self._alerts(mcp_client)["data"]["notifications"]
        assert len(part["rows"]) == MCP_ALERTS_MAX_ROWS and part["truncated"] is False

    def test_notification_text_is_one_line_and_length_capped(self, mcp_client, alert_tables):
        _notify("Crash", "line one\nline two\x00‮" + "x" * (MCP_ALERT_TEXT_MAX_CHARS * 2), _ago(minutes=1))
        text = self._alerts(mcp_client)["data"]["notifications"]["rows"][0]["text"]
        assert text.startswith("line one line two") and len(text) == MCP_ALERT_TEXT_MAX_CHARS and text.endswith("…")
        assert not any(c in text for c in "\n\x00‮")

    def test_trap_monitor_lists_alert_phases_most_severe_first(self, mcp_client, alert_tables):
        _trap("ZZALB", "BEAR_TRAP_RISK", _ago(minutes=5))
        _trap("ZZALA", "ACTIVE_SELLOFF", _ago(minutes=6), ema=-8.25, rsi=22.5)
        _trap("ZZALC", "BULL_TRAP_RISK", _ago(minutes=7))
        _trap("ZZALD", "ACCUMULATION", _ago(minutes=5))
        _trap("ZZALE", "NEUTRAL", _ago(minutes=5))
        _trap("ZZALF", "CAUTION", _ago(minutes=5))
        data = self._alerts(mcp_client)
        trap = data["data"]["trap_monitor"]
        assert [r["ticker"] for r in trap["rows"]] == ["ZZALA", "ZZALC", "ZZALB"]
        first = trap["rows"][0]
        assert first["phase"] == "ACTIVE_SELLOFF" and first["phase_label"] == "Active Selloff"
        assert (first["ema_distance_pct"], first["rsi"]) == (-8.25, 22.5)
        assert set(first) == {"ticker", "phase", "phase_label", "bull_trap_level", "bear_trap_level", "capitulation_level", "wyckoff_level", "ema_distance_pct", "rsi", "at"}
        assert trap["truncated"] is False and trap["last_scan_at"] == data["as_of"]

    def test_trap_monitor_rows_outside_the_window_are_left_out_and_reported(self, mcp_client, alert_tables):
        _trap("ZZALOLD", "ACTIVE_SELLOFF", _ago(days=12))
        data = self._alerts(mcp_client, days=3)
        assert data["data"]["trap_monitor"]["rows"] == []
        assert data["data"]["trap_monitor"]["last_scan_at"] is not None
        assert any("before the requested window" in line for line in data["missing"]) and data["stale"] is True

    def test_no_trap_monitor_results_is_marked_missing(self, mcp_client, alert_tables):
        data = self._alerts(mcp_client)
        assert data["data"]["trap_monitor"] == {"last_scan_at": None, "rows": [], "truncated": False}
        assert data["as_of"] is None
        assert any("no results yet" in line for line in data["missing"]) and data["stale"] is True

    def test_trap_monitor_rows_are_capped(self, mcp_client, alert_tables):
        for index in range(MCP_ALERTS_MAX_ROWS + 2):
            _trap(f"ZZAL{index:03d}", "BULL_TRAP_RISK", _ago(minutes=index))
        part = self._alerts(mcp_client)["data"]["trap_monitor"]
        assert len(part["rows"]) == MCP_ALERTS_MAX_ROWS and part["truncated"] is True

    def test_referee_is_listed_per_engine_with_the_veto_meaning(self, mcp_client, alert_tables):
        _referee(TRAP_MONITOR_ENGINE, "ZZAL1", "BULL_TRAP_RISK", 0.21, True, "shadow", _ago(minutes=3))
        _referee(TRAP_MONITOR_ENGINE, "ZZAL2", "ACTIVE_SELLOFF", 0.84, False, "active", _ago(minutes=2))
        _referee(CONFLUENCE_ENGINE, "ZZAL3", "bullish", 0.55, False, "shadow", _ago(minutes=1), direction="bullish")
        _referee(TRAP_MONITOR_ENGINE, "ZZAL4", "ACTIVE_SELLOFF", 0.1, True, "shadow", _ago(days=20))
        referee = self._alerts(mcp_client)["data"]["referee"]
        assert set(referee) == {TRAP_MONITOR_ENGINE, CONFLUENCE_ENGINE}
        trap_rows = referee[TRAP_MONITOR_ENGINE]["rows"]
        assert [(r["ticker"], r["would_veto"], r["mode"]) for r in trap_rows] == [("ZZAL2", False, "active"), ("ZZAL1", True, "shadow")]
        assert trap_rows[1]["fire_probability"] == 0.21 and trap_rows[1]["phase"] == "BULL_TRAP_RISK"
        assert [r["phase"] for r in referee[CONFLUENCE_ENGINE]["rows"]] == ["bullish"]
        assert referee[TRAP_MONITOR_ENGINE]["truncated"] is False

    def test_referee_rows_are_capped_per_engine(self, mcp_client, alert_tables):
        for index in range(MCP_ALERTS_MAX_ROWS + 2):
            _referee(TRAP_MONITOR_ENGINE, f"ZZAL{index}", "BULL_TRAP_RISK", 0.5, False, "shadow", _ago(minutes=index))
        referee = self._alerts(mcp_client)["data"]["referee"]
        assert len(referee[TRAP_MONITOR_ENGINE]["rows"]) == MCP_ALERTS_MAX_ROWS and referee[TRAP_MONITOR_ENGINE]["truncated"] is True
        assert referee[CONFLUENCE_ENGINE] == {"rows": [], "truncated": False}

    def test_empty_log_returns_empty_lists_not_errors(self, mcp_client, alert_tables):
        data = self._alerts(mcp_client)["data"]
        assert data["notifications"] == {"rows": [], "truncated": False}
        assert data["referee"][TRAP_MONITOR_ENGINE] == {"rows": [], "truncated": False}

    def test_a_failed_referee_read_is_missing_not_an_empty_list(self, mcp_client, alert_tables):
        _notify("Crash", "ZZAL1 crashed", _ago(minutes=5))
        _trap("ZZAL1", "ACTIVE_SELLOFF", _ago(minutes=1))
        _referee(CONFLUENCE_ENGINE, "ZZAL2", "bullish", 0.55, False, "shadow", _ago(minutes=1), direction="bullish")

        def fail_for_trap_monitor(engine, **kwargs):
            if engine == TRAP_MONITOR_ENGINE:
                raise sqlite3.OperationalError("database is locked")
            return real_get_recent_evaluations(engine, **kwargs)

        with patch("mcp_server.tools.get_alerts.get_recent_evaluations", side_effect=fail_for_trap_monitor):
            data = self._alerts(mcp_client)
        assert data["data"]["referee"][TRAP_MONITOR_ENGINE] is None
        assert [r["ticker"] for r in data["data"]["referee"][CONFLUENCE_ENGINE]["rows"]] == ["ZZAL2"]
        assert data["missing"] == [f"Alert Confidence Referee log for {TRAP_MONITOR_ENGINE} could not be read"]
        assert data["stale"] is True
        assert "locked" not in str(data)
        assert [n["text"] for n in data["data"]["notifications"]["rows"]] == ["ZZAL1 crashed"]
        assert [r["ticker"] for r in data["data"]["trap_monitor"]["rows"]] == ["ZZAL1"]

    def test_reading_changes_nothing_and_leaves_alert_state_alone(self, mcp_client, alert_tables):
        _notify("Crash", "ZZAL1", _ago(minutes=1))
        _trap("ZZAL1", "ACTIVE_SELLOFF", _ago(minutes=1))
        _referee(TRAP_MONITOR_ENGINE, "ZZAL1", "ACTIVE_SELLOFF", 0.4, False, "shadow", _ago(minutes=1))
        conn = get_connection()
        try:
            state_before = [tuple(r) for r in conn.execute("SELECT * FROM alert_state ORDER BY engine, ticker")]
            unread_before = conn.execute("SELECT COUNT(*) FROM system_notifications WHERE is_read = 0").fetchone()[0]
        finally:
            conn.close()
        counts = table_counts()
        self._alerts(mcp_client)
        self._alerts(mcp_client, days=30)
        assert table_counts() == counts
        conn = get_connection()
        try:
            assert [tuple(r) for r in conn.execute("SELECT * FROM alert_state ORDER BY engine, ticker")] == state_before
            assert conn.execute("SELECT COUNT(*) FROM system_notifications WHERE is_read = 0").fetchone()[0] == unread_before
        finally:
            conn.close()

    def test_tool_is_listed_with_a_bounded_days_argument(self, mcp_client):
        tools = {t["name"]: t for t in rpc(mcp_client, "tools/list").json()["result"]["tools"]}
        days = tools["get_alerts"]["inputSchema"]["properties"]["days"]
        assert (days["minimum"], days["maximum"], days["default"]) == (1, MCP_ALERTS_MAX_DAYS, 7)
        assert tools["get_alerts"]["annotations"]["readOnlyHint"] is True
