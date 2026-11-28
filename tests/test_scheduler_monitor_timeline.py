"""Tests for classify_dependency_gap and the Job Scheduling Timeline builder, using real APScheduler triggers on stub jobs."""
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from zoneinfo import ZoneInfo

import pytest
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

import scheduler_engine
import scheduler_monitor
from db.database import get_connection
from scheduler_monitor import build_schedule_timeline, classify_dependency_gap, timeline_window_start

pytestmark = pytest.mark.restore_tables("scheduler_run_log", "scheduler_run_history")

LONDON = ZoneInfo("Europe/London")
MON = datetime(2026, 11, 23, tzinfo=timezone.utc)
QUANT, XRAY, ORCH, DIGEST = "quant_analysis_job", "xray_risk_cache_job", "risk_orchestrator_job", "risk_orchestrator_digest_job"


class TestClassifyDependencyGap:
    def test_unknown_without_average_and_short_gap(self):
        assert classify_dependency_gap(10, float("inf"), None) == ("unknown", False)

    def test_ok_without_average_and_long_gap(self):
        assert classify_dependency_gap(30, float("inf"), None) == ("ok", False)

    def test_violation_when_gap_shorter_than_average(self):
        assert classify_dependency_gap(40, float("inf"), 47.6 * 60)[0] == "violation"

    def test_tight_within_margin(self):
        assert classify_dependency_gap(48, float("inf"), 47.6 * 60)[0] == "tight"

    def test_ok_beyond_margin(self):
        assert classify_dependency_gap(60, float("inf"), 47.6 * 60)[0] == "ok"

    def test_backwards_when_consumer_precedes_producer_and_data_is_a_day_old(self):
        assert classify_dependency_gap(1440, 240, 600) == ("ok", True)
        assert classify_dependency_gap(1439, 240, 600)[1] is False
        assert classify_dependency_gap(1440, 241, 600)[1] is False


@pytest.fixture(autouse=True)
def _london(monkeypatch):
    monkeypatch.setattr(scheduler_monitor, "get_user_tz", lambda: LONDON)
    conn = get_connection()
    conn.execute("DELETE FROM scheduler_run_log")
    conn.execute("DELETE FROM scheduler_run_history")
    conn.commit()
    conn.close()


def _daily(job_id, hour, minute=0, dow=None):
    kwargs = {"day_of_week": dow} if dow else {}
    return SimpleNamespace(id=job_id, next_run_time=None, trigger=CronTrigger(hour=hour, minute=minute, timezone=LONDON, **kwargs))


def _set_jobs(monkeypatch, *jobs):
    monkeypatch.setattr(scheduler_engine.scheduler, "get_jobs", lambda: list(jobs))


def _log_avg(job_id, avg_sec):
    conn = get_connection()
    conn.execute(
        "INSERT INTO scheduler_run_log (job_id, last_run, last_started, last_duration_sec, avg_duration_sec, last_status) "
        "VALUES (?, '2026-11-22 23:00:00', '2026-11-22 22:00:00', ?, ?, 'success')", (job_id, avg_sec, avg_sec))
    conn.commit()
    conn.close()


def _log_history(job_id, started, duration_sec, status="success"):
    finished = datetime.strptime(started, "%Y-%m-%d %H:%M:%S") + timedelta(seconds=duration_sec)
    conn = get_connection()
    conn.execute(
        "INSERT INTO scheduler_run_history (job_id, started_at, finished_at, duration_sec, trigger_source, status) VALUES (?, ?, ?, ?, 'scheduled', ?)",
        (job_id, started, finished.strftime("%Y-%m-%d %H:%M:%S"), duration_sec, status))
    conn.commit()
    conn.close()


def _timeline(days=7, start=MON, **kw):
    return build_schedule_timeline(start, days, **kw)


def _job(tl, job_id):
    return next(j for j in tl["jobs"] if j["id"] == job_id)


def test_timeline_window_start_is_local_midnight_in_utc():
    from datetime import date
    assert timeline_window_start(date(2026, 7, 1)) == datetime(2026, 6, 30, 23, 0, tzinfo=timezone.utc)
    assert timeline_window_start(date(2026, 11, 23)) == MON


def test_timeline_window_start_defaults_to_a_monday_midnight():
    start = timeline_window_start(None).astimezone(LONDON)
    assert start.weekday() == 0 and (start.hour, start.minute) == (0, 0)


def test_weekday_cron_gives_five_blocks_in_scheduled_row(monkeypatch):
    _set_jobs(monkeypatch, _daily(QUANT, 22, dow="mon-fri"))
    tl = _timeline()
    assert _job(tl, QUANT)["row"] == "scheduled"
    assert len(tl["blocks"]) == 5
    assert {b["start"][11:] for b in tl["blocks"]} == {"22:00:00"}
    assert tl["bands"] == []
    assert tl["window"]["start"] == "2026-11-23 00:00:00" and tl["window"]["end"] == "2026-11-30 00:00:00"


def test_block_from_day_before_window_start_is_drawn(monkeypatch):
    _set_jobs(monkeypatch, _daily(QUANT, 22))
    tl = _timeline(days=1)
    assert sorted(b["start"] for b in tl["blocks"]) == ["2026-11-22 22:00:00", "2026-11-23 22:00:00"]


def test_overnight_chain_scenario(monkeypatch):
    _set_jobs(monkeypatch, _daily(QUANT, 22), _daily(XRAY, 23), _daily(ORCH, 23, 10), _daily(DIGEST, 23, 20))
    _log_avg(QUANT, 2856)
    for day, dur in (("2026-11-25", 5016), ("2026-11-24", 2800), ("2026-11-23", 2900)):
        _log_history(QUANT, f"{day} 22:00:00", dur)
    tl = _timeline()
    deps = {(d["from"], d["to"], d["consumer_start"]): d for d in tl["dependencies"]}
    dep = deps[(QUANT, XRAY, "2026-11-25 23:00:00")]
    assert dep["overlap"] == "ok" and dep["gap_min"] == 60.0 and dep["backwards"] is False
    assert dep["recent_overrun"] is True
    assert dep["producer_start"] == "2026-11-25 22:00:00" and dep["producer_end"] == "2026-11-25 22:47:36"
    block = next(b for b in tl["blocks"] if b["job_id"] == QUANT and b["start"] == "2026-11-25 22:00:00")
    assert block["end"] == "2026-11-25 22:47:36"
    assert block["whisker_end"] == "2026-11-25 23:23:36"
    assert deps[(QUANT, ORCH, "2026-11-25 23:10:00")]["overlap"] == "ok"
    assert (ORCH, DIGEST, "2026-11-25 23:20:00") in deps
    assert _job(tl, QUANT)["recent_max_sec"] == 5016
    assert [r["duration_sec"] for r in tl["runs"] if r["job_id"] == QUANT and r["start"].startswith("2026-11-25")] == [5016]


def test_no_whisker_when_recent_max_not_above_average(monkeypatch):
    _set_jobs(monkeypatch, _daily(QUANT, 22))
    _log_avg(QUANT, 2856)
    _log_history(QUANT, "2026-11-24 22:00:00", 2000)
    assert all(b["whisker_end"] is None for b in _timeline()["blocks"])


def test_errored_runs_do_not_extend_the_whisker(monkeypatch):
    _set_jobs(monkeypatch, _daily(QUANT, 22))
    _log_avg(QUANT, 2856)
    _log_history(QUANT, "2026-11-24 22:00:00", 9000, status="error")
    assert all(b["whisker_end"] is None for b in _timeline()["blocks"])


def test_multi_fire_cron_is_a_frequent_band_per_weekday(monkeypatch):
    job = SimpleNamespace(id=QUANT, next_run_time=None, trigger=CronTrigger(day_of_week="mon-fri", hour="7-21", minute="*/2", timezone=LONDON))
    _set_jobs(monkeypatch, job)
    tl = _timeline()
    assert _job(tl, QUANT)["row"] == "frequent" and _job(tl, QUANT)["max_fires_per_day"] == 15 * 30
    assert tl["blocks"] == []
    assert len(tl["bands"]) == 5
    assert tl["bands"][0]["start"] == "2026-11-23 07:00:00" and tl["bands"][0]["fires"] == 450


def test_interval_trigger_is_frequent(monkeypatch):
    trigger = IntervalTrigger(minutes=15, start_date=datetime(2026, 1, 1, tzinfo=timezone.utc))
    _set_jobs(monkeypatch, SimpleNamespace(id=QUANT, next_run_time=None, trigger=trigger))
    tl = _timeline()
    assert _job(tl, QUANT)["row"] == "frequent"
    assert len(tl["bands"]) == 1 and tl["bands"][0]["fires"] == 7 * 96


def test_dst_change_week_keeps_local_wall_clock(monkeypatch):
    _set_jobs(monkeypatch, _daily(QUANT, 22))
    tl = _timeline(start=datetime(2027, 3, 22, tzinfo=timezone.utc))
    starts = sorted(b["start"] for b in tl["blocks"])
    assert len(starts) == 8
    assert {s[11:] for s in starts} == {"22:00:00"}
    assert "2027-03-28 22:00:00" in starts


def test_override_retimes_daily_job_and_changes_dependency_class(monkeypatch):
    _set_jobs(monkeypatch, _daily(QUANT, 22), _daily(XRAY, 23))
    _log_avg(QUANT, 2856)
    base = _timeline()
    assert {d["overlap"] for d in base["dependencies"] if d["to"] == XRAY} == {"ok"}
    moved = _timeline(overrides={XRAY: "22:30"})
    xray_blocks = [b for b in moved["blocks"] if b["job_id"] == XRAY]
    assert xray_blocks and {b["start"][11:] for b in xray_blocks} == {"22:30:00"}
    deps = [d for d in moved["dependencies"] if d["to"] == XRAY]
    assert deps and {d["overlap"] for d in deps} == {"violation"}
    assert {d["gap_min"] for d in deps} == {30.0}


def test_override_ignored_for_multi_fire_jobs(monkeypatch):
    job = SimpleNamespace(id=QUANT, next_run_time=None, trigger=CronTrigger(hour="7-21", minute="*/2", timezone=LONDON))
    _set_jobs(monkeypatch, job)
    tl = _timeline(overrides={QUANT: "03:00"})
    assert tl["blocks"] == [] and tl["bands"]


def _unrelated_pair():
    graph = scheduler_monitor.build_workflow_graph()
    linked = {frozenset((e["from"], e["to"])) for e in graph["edges"]}
    ids = [n["id"] for n in graph["nodes"] if n["category"] not in ("external", "manual") and not n["id"].startswith(("etf_", "account_"))]
    for a in ids:
        for b in ids:
            if a < b and frozenset((a, b)) not in linked:
                return a, b
    pytest.fail("no unrelated pair of jobs in JOB_GRAPH")


def test_independent_overlapping_jobs_get_separate_lanes(monkeypatch):
    a, b = _unrelated_pair()
    _set_jobs(monkeypatch, _daily(a, 22), _daily(b, 22, 5))
    _log_avg(a, 1800)
    _log_avg(b, 1800)
    tl = _timeline(days=1)
    assert tl["lanes"] == 2
    assert {blk["lane"] for blk in tl["blocks"]} == {0, 1}
    by_job = {blk["job_id"]: blk for blk in tl["blocks"] if blk["start"].startswith("2026-11-23")}
    assert by_job[a]["overlap_with"] == [b] and by_job[b]["overlap_with"] == [a]
    assert tl["dependencies"] == []


def test_sequential_jobs_share_a_lane_without_overlap(monkeypatch):
    a, b = _unrelated_pair()
    _set_jobs(monkeypatch, _daily(a, 22), _daily(b, 23))
    _log_avg(a, 600)
    tl = _timeline(days=1)
    assert tl["lanes"] == 1
    assert all(blk["overlap_with"] == [] for blk in tl["blocks"])


def test_reschedule_meta_and_job_fields(monkeypatch):
    _set_jobs(monkeypatch, _daily(QUANT, 22))
    job = _job(_timeline(), QUANT)
    assert job["reschedule"] == {"config_key": "QUANT_ANALYSIS", "field": "TIME", "editable": True}
    assert job["label"] == "Quantamental Analysis Engine"


def test_reschedule_not_editable_for_weekday_restricted_or_frequent(monkeypatch):
    frequent = SimpleNamespace(id=XRAY, next_run_time=None, trigger=CronTrigger(hour="7-21", minute="*/2", timezone=LONDON))
    _set_jobs(monkeypatch, _daily(QUANT, 22), frequent)
    tl = _timeline()
    assert _job(tl, XRAY)["reschedule"]["editable"] is False


def test_jobs_not_live_are_excluded(monkeypatch):
    _set_jobs(monkeypatch, _daily(QUANT, 22))
    assert [j["id"] for j in _timeline()["jobs"]] == [QUANT]
