"""Tests for scheduler_engine pure-function helpers and DB utilities."""
import pytest
import database as _db_module
from scheduler_engine import (
    _build_contagion_feed_text,
    _build_contagion_message,
    log_sched_notification,
    record_job_run,
    get_all_job_last_runs,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_tables():
    """Wipe scheduler-related rows before each test."""
    conn = _db_module.get_connection()
    conn.execute("DELETE FROM system_notifications")
    conn.execute("DELETE FROM scheduler_run_log")
    conn.commit()
    conn.close()


# ---------------------------------------------------------------------------
# _build_contagion_feed_text
# ---------------------------------------------------------------------------

class TestBuildContagionFeedText:
    def _event(self, n_leaders=1, n_etfs=0, vol_spikes=None, severity=0.5):
        leaders = [{"ticker": f"NVDA{i}", "intraday_pct": -4.5 - i} for i in range(n_leaders)]
        etfs = [{"ticker": f"SMH{i}", "intraday_pct": -2.1} for i in range(n_etfs)]
        return {
            "leader_shocks": leaders,
            "etf_hits": etfs,
            "volume_spikes": vol_spikes or [],
            "severity_score": severity,
        }

    def test_singular_leader_no_s(self):
        text = _build_contagion_feed_text(self._event(n_leaders=1))
        assert "1 leader down" in text

    def test_plural_leaders_with_s(self):
        text = _build_contagion_feed_text(self._event(n_leaders=3))
        assert "3 leaders down" in text

    def test_volume_spike_marker_present(self):
        event = self._event(n_leaders=1, vol_spikes=["NVDA0"])
        text = _build_contagion_feed_text(event)
        assert "⚡" in text

    def test_no_volume_spike_no_marker(self):
        text = _build_contagion_feed_text(self._event(n_leaders=1))
        assert "⚡" not in text

    def test_etf_section_absent_when_empty(self):
        text = _build_contagion_feed_text(self._event(n_leaders=1, n_etfs=0))
        assert "ETFs:" not in text

    def test_etf_section_present_when_provided(self):
        text = _build_contagion_feed_text(self._event(n_leaders=1, n_etfs=2))
        assert "ETFs:" in text

    def test_severity_formatted_as_percent(self):
        text = _build_contagion_feed_text(self._event(severity=0.75))
        assert "75%" in text

    def test_returns_string(self):
        assert isinstance(_build_contagion_feed_text(self._event()), str)


# ---------------------------------------------------------------------------
# _build_contagion_message
# ---------------------------------------------------------------------------

class TestBuildContagionMessage:
    def _event(self, vol_spike=False):
        return {
            "leader_shocks": [{"ticker": "NVDA", "intraday_pct": -5.0}],
            "etf_hits": [{"ticker": "SMH", "intraday_pct": -2.5}],
            "volume_spikes": ["NVDA"] if vol_spike else [],
        }

    def test_volume_spike_annotation_present(self):
        msg = _build_contagion_message(self._event(vol_spike=True), {})
        assert "volume spike" in msg

    def test_no_volume_spike_no_annotation(self):
        msg = _build_contagion_message(self._event(vol_spike=False), {})
        assert "volume spike" not in msg

    def test_etf_section_present(self):
        msg = _build_contagion_message(self._event(), {})
        assert "ETF Contagion" in msg

    def test_leader_ticker_in_output(self):
        msg = _build_contagion_message(self._event(), {})
        assert "NVDA" in msg

    def test_returns_string(self):
        assert isinstance(_build_contagion_message(self._event(), {}), str)


# ---------------------------------------------------------------------------
# log_sched_notification / record_job_run / get_all_job_last_runs
# ---------------------------------------------------------------------------

class TestDbHelpers:
    def test_log_sched_notification_inserts_row(self):
        log_sched_notification("TestType", "Hello world")
        conn = _db_module.get_connection()
        row = conn.execute(
            "SELECT message_type, message_text FROM system_notifications LIMIT 1"
        ).fetchone()
        conn.close()
        assert row["message_type"] == "TestType"
        assert row["message_text"] == "Hello world"

    def test_record_job_run_inserts_row(self):
        record_job_run("test_job")
        conn = _db_module.get_connection()
        row = conn.execute(
            "SELECT job_id, last_run FROM scheduler_run_log WHERE job_id = 'test_job'"
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["job_id"] == "test_job"

    def test_record_job_run_upserts_on_conflict(self):
        record_job_run("test_job")
        record_job_run("test_job")
        conn = _db_module.get_connection()
        count = conn.execute(
            "SELECT COUNT(*) FROM scheduler_run_log WHERE job_id = 'test_job'"
        ).fetchone()[0]
        conn.close()
        assert count == 1

    def test_get_all_job_last_runs_returns_dict(self):
        record_job_run("job_a")
        record_job_run("job_b")
        result = get_all_job_last_runs()
        assert isinstance(result, dict)
        assert "job_a" in result
        assert "job_b" in result

    def test_get_all_job_last_runs_empty_db(self):
        result = get_all_job_last_runs()
        assert result == {}
