"""Tests for scheduler_engine pure-function helpers and DB utilities."""
import threading
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest
import database as _db_module
from scheduler_engine import (
    _build_contagion_feed_text,
    _build_contagion_message,
    log_sched_notification,
    record_job_run,
    get_all_job_last_runs,
    resume_interrupted_scans,
    _mark_job_started,
    _mark_job_done,
    get_active_jobs,
)
import scheduler_engine as _sched_module


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(autouse=True)
def clean_tables():
    """Wipe scheduler-related rows before each test."""
    conn = _db_module.get_connection()
    conn.execute("DELETE FROM system_notifications")
    conn.execute("DELETE FROM scheduler_run_log")
    conn.execute("DELETE FROM quant_scan_states")
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


# ---------------------------------------------------------------------------
# Active-jobs tracking: _mark_job_started / _mark_job_done / get_active_jobs
# ---------------------------------------------------------------------------

class TestActiveJobsTracking:
    def setup_method(self):
        """Reset the shared _active_jobs dict before each test."""
        with _sched_module._active_jobs_lock:
            _sched_module._active_jobs.clear()

    def test_mark_job_started_adds_entry(self):
        _mark_job_started("my_job")
        jobs = get_active_jobs()
        assert "my_job" in jobs

    def test_mark_job_started_stores_utc_iso_timestamp(self):
        _mark_job_started("ts_job")
        ts = get_active_jobs()["ts_job"]
        # Must parse as a valid ISO datetime string (no timezone suffix — stored as naive UTC)
        from datetime import datetime
        parsed = datetime.fromisoformat(ts)
        assert parsed.year >= 2024

    def test_mark_job_done_removes_entry(self):
        _mark_job_started("remove_me")
        _mark_job_done("remove_me")
        assert "remove_me" not in get_active_jobs()

    def test_mark_job_done_on_missing_key_does_not_raise(self):
        _mark_job_done("never_started")  # must not raise

    def test_get_active_jobs_returns_snapshot_copy(self):
        _mark_job_started("snap_job")
        snapshot = get_active_jobs()
        # Mutating the returned dict must not affect internal state
        snapshot["snap_job"] = "tampered"
        assert get_active_jobs()["snap_job"] != "tampered"

    def test_multiple_jobs_tracked_independently(self):
        _mark_job_started("job_a")
        _mark_job_started("job_b")
        _mark_job_done("job_a")
        jobs = get_active_jobs()
        assert "job_a" not in jobs
        assert "job_b" in jobs

    def test_get_active_jobs_empty_when_no_jobs_running(self):
        assert get_active_jobs() == {}


# ---------------------------------------------------------------------------
# resume_interrupted_scans
# ---------------------------------------------------------------------------

def _today_str():
    return datetime.now(timezone.utc).strftime('%Y-%m-%d')


def _seed_scan_state(scan_type: str, status: str, last_ticker: str = '', scan_date: str = None):
    conn = _db_module.get_connection()
    conn.execute(
        "INSERT INTO quant_scan_states (scan_date, scan_type, last_processed_ticker, status) VALUES (?, ?, ?, ?)",
        (scan_date or _today_str(), scan_type, last_ticker, status),
    )
    conn.commit()
    conn.close()


class TestResumeInterruptedScans:

    def test_no_interrupted_scans_dispatches_nothing(self):
        threads_before = threading.active_count()
        resume_interrupted_scans()
        assert threading.active_count() == threads_before

    def test_in_progress_daily_dispatches_overnight_scan(self):
        _seed_scan_state('daily', 'IN_PROGRESS')
        with patch("scheduler_jobs.run_overnight_quant_scan") as mock_fn:
            resume_interrupted_scans()
            # Wait briefly for daemon thread to call the function
            import time; time.sleep(0.1)
            mock_fn.assert_called_once()

    def test_in_progress_universe_dispatches_weekend_routine(self):
        _seed_scan_state('universe', 'IN_PROGRESS')
        with patch("scheduler_jobs.run_weekend_universe_routine") as mock_fn:
            resume_interrupted_scans()
            import time; time.sleep(0.1)
            mock_fn.assert_called_once()

    def test_deep_sync_stage_present_dispatches_deep_sync(self):
        _seed_scan_state('deep_sync_s1', 'COMPLETED')
        _seed_scan_state('deep_sync_s2', 'COMPLETED')
        _seed_scan_state('deep_sync_s4', 'IN_PROGRESS')
        with patch("scheduler_jobs.run_universe_deep_sync_job") as mock_fn:
            resume_interrupted_scans()
            import time; time.sleep(0.1)
            mock_fn.assert_called_once()

    def test_completed_deep_sync_does_not_redispatch(self):
        _seed_scan_state('deep_sync_s5', 'COMPLETED')
        with patch("scheduler_jobs.run_universe_deep_sync_job") as mock_fn:
            resume_interrupted_scans()
            import time; time.sleep(0.05)
            mock_fn.assert_not_called()

    def test_standalone_ml_backfill_dispatches_when_no_deep_sync(self):
        _seed_scan_state('ml_backfill', 'IN_PROGRESS')
        with patch("scheduler_jobs.run_ml_backfill") as mock_fn:
            resume_interrupted_scans()
            import time; time.sleep(0.1)
            mock_fn.assert_called_once()

    def test_ml_backfill_not_dispatched_standalone_when_deep_sync_active(self):
        """ml_backfill IN_PROGRESS during a deep sync should not trigger a standalone resume."""
        _seed_scan_state('deep_sync_s1', 'COMPLETED')
        _seed_scan_state('ml_backfill', 'IN_PROGRESS')
        with (
            patch("scheduler_jobs.run_ml_backfill") as mock_ml,
            patch("scheduler_jobs.run_universe_deep_sync_job"),
        ):
            resume_interrupted_scans()
            import time; time.sleep(0.1)
            mock_ml.assert_not_called()

    def test_completed_daily_does_not_redispatch(self):
        _seed_scan_state('daily', 'COMPLETED')
        with patch("scheduler_jobs.run_overnight_quant_scan") as mock_fn:
            resume_interrupted_scans()
            import time; time.sleep(0.05)
            mock_fn.assert_not_called()

    def test_tail_risk_daily_in_progress_dispatches_when_daily_completed(self):
        _seed_scan_state('daily', 'COMPLETED')
        _seed_scan_state('tail_risk_daily', 'IN_PROGRESS', last_ticker='AAPL')
        with patch("scheduler_jobs.update_all_tail_risks") as mock_fn:
            resume_interrupted_scans()
            import time; time.sleep(0.1)
            mock_fn.assert_called_once()
            _, kwargs = mock_fn.call_args
            assert kwargs.get('scan_type') == 'tail_risk_daily'

    def test_tail_risk_daily_not_dispatched_when_daily_in_progress(self):
        """If the parent quant scan is still IN_PROGRESS, its own resume will handle tail risk."""
        _seed_scan_state('daily', 'IN_PROGRESS')
        _seed_scan_state('tail_risk_daily', 'IN_PROGRESS', last_ticker='AAPL')
        with (
            patch("scheduler_jobs.run_overnight_quant_scan"),
            patch("scheduler_jobs.update_all_tail_risks") as mock_tr,
        ):
            resume_interrupted_scans()
            import time; time.sleep(0.1)
            mock_tr.assert_not_called()

    def test_tail_risk_universe_in_progress_dispatches_when_universe_completed(self):
        _seed_scan_state('universe', 'COMPLETED')
        _seed_scan_state('tail_risk_universe', 'IN_PROGRESS', last_ticker='VOD.L')
        with patch("scheduler_jobs.update_all_tail_risks") as mock_fn:
            resume_interrupted_scans()
            import time; time.sleep(0.1)
            mock_fn.assert_called_once()
            _, kwargs = mock_fn.call_args
            assert kwargs.get('scan_type') == 'tail_risk_universe'

    def test_tail_risk_daily_completed_does_not_redispatch(self):
        _seed_scan_state('daily', 'COMPLETED')
        _seed_scan_state('tail_risk_daily', 'COMPLETED')
        with patch("scheduler_jobs.update_all_tail_risks") as mock_fn:
            resume_interrupted_scans()
            import time; time.sleep(0.05)
            mock_fn.assert_not_called()


# ---------------------------------------------------------------------------
# Workflow Monitor: manifest, graph, conflicts, status, duration listener
# ---------------------------------------------------------------------------

from scheduler_engine import (
    JOB_GRAPH,
    CONFIG_KEY_TO_JOB,
    job_label,
    display_name_for_config_key,
    scheduler_display_names,
    build_workflow_graph,
    detect_workflow_conflicts,
    reload_scheduler,
    _resolve_manifest,
    _job_status,
    _on_job_event,
)
import config as _config_module
from apscheduler.events import EVENT_JOB_SUBMITTED, EVENT_JOB_EXECUTED, EVENT_JOB_ERROR


def _wf_node(**kw):
    base = {
        "id": "x", "label": "X", "category": "quant", "engine": "e",
        "produces": [], "consumes": [], "enabled": True,
        "last_run": None, "last_status": None, "avg_duration_sec": None,
        "next_run": None, "schedule": None, "status": "green", "status_reason": "ok",
    }
    base.update(kw)
    return base


class _Evt:
    def __init__(self, code, job_id):
        self.code = code
        self.job_id = job_id


class TestWorkflowManifest:
    def test_every_static_entry_has_required_keys(self):
        for job_id, meta in JOB_GRAPH.items():
            for key in ("label", "category", "engine", "produces", "consumes"):
                assert key in meta, f"{job_id} missing {key}"

    def test_every_registered_job_is_in_manifest(self):
        reload_scheduler()
        live_ids = {j.id for j in _sched_module.scheduler.get_jobs()}
        unmapped = [jid for jid in live_ids if _resolve_manifest(jid) is None]
        assert unmapped == [], f"Scheduler jobs missing a JOB_GRAPH entry: {unmapped}"

    def test_dynamic_etf_job_resolves(self):
        assert _resolve_manifest("etf_predictor_7_pre_job") is not None
        assert _resolve_manifest("etf_predictor_12_post_job") is not None

    def test_dynamic_template_not_resolved_as_static(self):
        assert _resolve_manifest("etf_predictor_dynamic") is None

    def test_dynamic_account_scraper_job_resolves(self):
        assert _resolve_manifest("account_scraper_7_job") is not None
        assert _resolve_manifest("account_scraper_dynamic") is None

    @pytest.mark.db
    def test_scraper_enabled_account_registers_a_live_resolvable_job(self):
        from database import create_account, update_account
        aid = create_account("ManifestScraperAcc", "GBP", account_type="House")
        update_account(
            aid, scraper_url="http://example.test/x.html", scraper_selector="#gf-price",
            scraper_enabled=True,
        )
        reload_scheduler()
        job_id = f"account_scraper_{aid}_job"
        live_ids = {j.id for j in _sched_module.scheduler.get_jobs()}
        assert job_id in live_ids
        assert _resolve_manifest(job_id) is not None


class TestBuildWorkflowGraph:
    def test_returns_nodes_and_edges(self):
        graph = build_workflow_graph()
        assert isinstance(graph["nodes"], list) and graph["nodes"]
        assert isinstance(graph["edges"], list) and graph["edges"]

    def test_edges_derive_from_produces_consumes(self):
        graph = build_workflow_graph()
        pairs = {(e["from"], e["to"], e["via"]) for e in graph["edges"]}
        assert ("overnight_quant_scan_job", "ml_inference_job", "quant_signals") in pairs
        assert ("ml_training_job", "ml_inference_job", "ml_model") in pairs

    def test_no_self_edges(self):
        graph = build_workflow_graph()
        assert all(e["from"] != e["to"] for e in graph["edges"])


class TestWorkflowConflicts:
    def test_backwards_ordering_flagged(self):
        # Weekly producer fires 60 min AFTER the weekly consumer on the same day:
        # the consumer can never use the same cycle's output (mirrors ml_backfill
        # being mis-scheduled to run after ml_training).
        graph = {
            "nodes": [
                _wf_node(id="P", label="Producer", produces=["a"], schedule={"weekdays": [6], "minute_of_day": 300}),
                _wf_node(id="C", label="Consumer", consumes=["a"], schedule={"weekdays": [6], "minute_of_day": 240}),
            ],
            "edges": [{"from": "P", "to": "C", "via": "a"}],
        }
        types = {c["type"] for c in detect_workflow_conflicts(graph)}
        assert "backwards_ordering" in types

    def test_normal_overnight_to_morning_not_flagged(self):
        # Producer runs daily 18:00; consumer daily 07:15 next morning uses the prior
        # evening's output (~13h old). This is intended and must NOT be flagged.
        graph = {
            "nodes": [
                _wf_node(id="P", label="Evening Quant", produces=["a"], avg_duration_sec=600,
                         schedule={"weekdays": [0, 1, 2, 3, 4], "minute_of_day": 18 * 60}),
                _wf_node(id="C", label="Morning Briefing", consumes=["a"],
                         schedule={"weekdays": [0, 1, 2, 3, 4], "minute_of_day": 7 * 60 + 15}),
            ],
            "edges": [{"from": "P", "to": "C", "via": "a"}],
        }
        types = {c["type"] for c in detect_workflow_conflicts(graph)}
        assert "backwards_ordering" not in types
        assert "overlap_risk" not in types

    def test_overlap_risk_flagged_with_known_duration(self):
        graph = {
            "nodes": [
                _wf_node(id="P", produces=["a"], avg_duration_sec=1800, schedule={"weekdays": [0], "minute_of_day": 60}),
                _wf_node(id="C", consumes=["a"], schedule={"weekdays": [0], "minute_of_day": 70}),
            ],
            "edges": [{"from": "P", "to": "C", "via": "a"}],
        }
        conflicts = detect_workflow_conflicts(graph)
        assert any(c["type"] == "overlap_risk" and c["severity"] == "warning" for c in conflicts)

    def test_overlap_unknown_duration_info(self):
        graph = {
            "nodes": [
                _wf_node(id="P", produces=["a"], avg_duration_sec=None, schedule={"weekdays": [0], "minute_of_day": 60}),
                _wf_node(id="C", consumes=["a"], schedule={"weekdays": [0], "minute_of_day": 70}),
            ],
            "edges": [{"from": "P", "to": "C", "via": "a"}],
        }
        assert any(c["type"] == "overlap_risk" and c["severity"] == "info" for c in detect_workflow_conflicts(graph))

    def test_wide_gap_no_overlap(self):
        graph = {
            "nodes": [
                _wf_node(id="P", produces=["a"], avg_duration_sec=600, schedule={"weekdays": [0], "minute_of_day": 60}),
                _wf_node(id="C", consumes=["a"], schedule={"weekdays": [0], "minute_of_day": 600}),
            ],
            "edges": [{"from": "P", "to": "C", "via": "a"}],
        }
        assert not [c for c in detect_workflow_conflicts(graph) if c["type"] == "overlap_risk"]

    def test_disabled_upstream_flagged(self):
        graph = {
            "nodes": [
                _wf_node(id="P", produces=["a"], enabled=False, status="disabled", status_reason="disabled"),
                _wf_node(id="C", consumes=["a"], schedule={"weekdays": [0], "minute_of_day": 70}),
            ],
            "edges": [{"from": "P", "to": "C", "via": "a"}],
        }
        assert any(c["type"] == "disabled_upstream" for c in detect_workflow_conflicts(graph))

    def test_disabled_upstream_suppressed_when_another_enabled_producer_exists(self):
        graph = {
            "nodes": [
                _wf_node(id="P_disabled", produces=["a"], enabled=False, status="disabled", status_reason="disabled"),
                _wf_node(id="P_enabled", produces=["a"]),
                _wf_node(id="C", consumes=["a"], schedule={"weekdays": [0], "minute_of_day": 70}),
            ],
            "edges": [
                {"from": "P_disabled", "to": "C", "via": "a"},
                {"from": "P_enabled", "to": "C", "via": "a"},
            ],
        }
        assert not any(c["type"] == "disabled_upstream" for c in detect_workflow_conflicts(graph))

    def test_last_run_error_flagged(self):
        graph = {"nodes": [_wf_node(id="E", status="red", status_reason="error")], "edges": []}
        assert any(c["type"] == "last_run_error" for c in detect_workflow_conflicts(graph))

    def test_overdue_flagged_as_stale(self):
        graph = {"nodes": [_wf_node(id="S", status="red", status_reason="overdue", last_run="2020-01-01 00:00")], "edges": []}
        assert any(c["type"] == "stale_never_run" for c in detect_workflow_conflicts(graph))

    def test_no_conflict_when_no_shared_weekday(self):
        graph = {
            "nodes": [
                _wf_node(id="P", produces=["a"], avg_duration_sec=3600, schedule={"weekdays": [5], "minute_of_day": 120}),
                _wf_node(id="C", consumes=["a"], schedule={"weekdays": [0], "minute_of_day": 60}),
            ],
            "edges": [{"from": "P", "to": "C", "via": "a"}],
        }
        assert not [c for c in detect_workflow_conflicts(graph) if c["type"] in ("overlap_risk", "backwards_ordering")]


class TestJobStatus:
    def test_disabled(self):
        assert _job_status(_wf_node(enabled=False))[0] == "disabled"

    def test_error_is_red(self):
        assert _job_status(_wf_node(enabled=True, last_status="error"))[0] == "red"

    def test_never_run_is_amber(self):
        status, reason = _job_status(_wf_node(enabled=True, last_run=None))
        assert status == "amber" and reason == "never_run"

    def test_recent_run_is_green(self):
        now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M")
        status, _ = _job_status(_wf_node(enabled=True, last_run=now, schedule={"weekdays": [0, 1, 2, 3, 4], "minute_of_day": 60}))
        assert status == "green"

    def test_overdue_is_red(self):
        status, reason = _job_status(_wf_node(enabled=True, last_run="2020-01-01 00:00", schedule={"weekdays": [0, 1, 2, 3, 4], "minute_of_day": 60}))
        assert status == "red" and reason == "overdue"

    def test_monthly_job_no_schedule_is_green_despite_age(self):
        old_run = "2026-06-01 06:00:00"
        status, _ = _job_status(_wf_node(enabled=True, last_run=old_run, schedule=None))
        assert status == "green"


class TestDurationListener:
    def test_executed_records_duration_and_success(self):
        _on_job_event(_Evt(EVENT_JOB_SUBMITTED, "dur_job"))
        _on_job_event(_Evt(EVENT_JOB_EXECUTED, "dur_job"))
        runs = get_all_job_last_runs()
        assert "dur_job" in runs
        assert runs["dur_job"]["last_status"] == "success"
        assert runs["dur_job"]["last_duration_sec"] is not None
        assert runs["dur_job"]["avg_duration_sec"] is not None

    def test_error_records_error_status(self):
        _on_job_event(_Evt(EVENT_JOB_SUBMITTED, "err_job"))
        _on_job_event(_Evt(EVENT_JOB_ERROR, "err_job"))
        assert get_all_job_last_runs()["err_job"]["last_status"] == "error"

    def test_executed_without_submitted_is_noop(self):
        _on_job_event(_Evt(EVENT_JOB_EXECUTED, "ghost_job"))
        assert "ghost_job" not in get_all_job_last_runs()


# ---------------------------------------------------------------------------
# Canonical job naming — one name per job across all surfaces
# ---------------------------------------------------------------------------

# Code-style / title-cased variants that must NOT be a canonical display name.
_FORBIDDEN_DISPLAY_NAMES = {
    "Update Pipeline", "Intraday Orchestrator", "ML Global Training", "Anomaly Training",
    "Overnight Quant Scan", "Weekend Universe Routine", "Ml Training", "Ml Backfill",
    "Quant Engine", "Quant Analysis",
}


class TestCanonicalJobNames:
    def test_every_config_key_maps_to_a_known_job(self):
        bad = [k for k, jid in CONFIG_KEY_TO_JOB.items() if jid not in JOB_GRAPH]
        assert bad == [], f"CONFIG_KEY_TO_JOB points at unknown job ids: {bad}"

    def test_display_name_for_config_key_resolves(self):
        assert display_name_for_config_key("ML_TRAINING") == "Global Model Training (Walk-Forward)"
        assert display_name_for_config_key("QUANT_ENGINE") == "Daily Quant Screener (Portfolio & Watchlist)"
        assert display_name_for_config_key("CRASH_ALERTS") == "Crash & Moonshot Alerts"

    def test_scheduler_display_names_cover_all_scheduling_config_keys(self):
        """Every SCHEDULING key in config resolves to a canonical name (no title-case fallback)."""
        scheduling = _config_module.load_config().get("SCHEDULING", {})
        names = scheduler_display_names()
        unmapped = [k for k in scheduling if k not in names]
        assert unmapped == [], f"SCHEDULING keys with no canonical display name: {unmapped}"

    def test_no_forbidden_variant_is_a_canonical_label(self):
        labels = {m["label"] for m in JOB_GRAPH.values()}
        leaked = labels & _FORBIDDEN_DISPLAY_NAMES
        assert leaked == set(), f"Code-style variants used as canonical labels: {leaked}"

    def test_active_job_name_comes_from_job_graph(self):
        """The Active-Jobs panel must show the canonical label, not a code-style literal."""
        captured = {}

        def _fake_training():
            captured.update(get_active_jobs())

        with patch("scheduler_jobs.train_global_ml_model", _fake_training):
            _sched_module.run_ml_training()
        assert "Global Model Training (Walk-Forward)" in captured
        assert "ML Global Training" not in captured
