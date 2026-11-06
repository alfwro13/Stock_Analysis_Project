"""Process Memory Telemetry: the sampler, its scheduled runner, the per-job memory line and the diagnostics payload."""
import logging
import sys

import pytest

import memory_helpers
import scheduler_engine
from constants import MEMORY_JOB_LOG_MIN_DELTA_MIB, MEMORY_JOB_LOG_MIN_SECONDS, MEMORY_SAMPLE_INTERVAL_MINUTES

SAMPLE_KEYS = {
    "taken_at", "rss_mib", "rss_anon_mib", "rss_file_mib", "rss_peak_mib", "anon_huge_mib", "available_mib",
    "total_mib", "glibc_in_use_mib", "glibc_free_held_mib", "arrow_backend", "arrow_mib", "python_blocks",
    "gc_gen2_collections", "threads", "uptime_hours",
}


def _memory_lines(caplog):
    return [r.getMessage() for r in caplog.records if r.name == "memory_helpers"]


def test_sample_reports_the_running_process():
    s = memory_helpers.sample()
    assert SAMPLE_KEYS <= set(s)
    assert s["rss_mib"] > 0
    assert s["available_mib"] > 0 and s["total_mib"] >= s["available_mib"]
    assert s["python_blocks"] > 0
    assert s["uptime_hours"] >= 0


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="/proc breakdown is Linux-only")
def test_rss_and_available_memory_readers_follow_psutil(monkeypatch):
    import os

    import psutil

    assert memory_helpers.rss_mib() == pytest.approx(memory_helpers.rss_mib(os.getpid()), rel=0.2)
    monkeypatch.setattr(psutil, "virtual_memory", lambda: type("Mem", (), {"available": 3 * 1024 * 1024 * 1024})())
    assert memory_helpers.available_mib() == 3 * 1024


def test_sample_splits_rss_into_anon_and_file_on_linux():
    s = memory_helpers.sample()
    assert s["rss_anon_mib"] > 0
    assert s["rss_file_mib"] > 0
    assert s["rss_peak_mib"] >= s["rss_anon_mib"]


def test_sample_marks_unavailable_figures_none_not_zero(monkeypatch):
    monkeypatch.setattr(memory_helpers, "_MALLINFO2", None)
    monkeypatch.setattr(memory_helpers, "_proc_kb_fields", lambda path: {})
    monkeypatch.delitem(sys.modules, "pyarrow", raising=False)
    s = memory_helpers.sample()
    for key in ("rss_anon_mib", "rss_file_mib", "rss_peak_mib", "anon_huge_mib", "glibc_in_use_mib",
                "glibc_free_held_mib", "arrow_mib", "arrow_backend", "threads"):
        assert s[key] is None, key
    assert s["rss_mib"] > 0


def test_arrow_pool_is_not_imported_just_for_telemetry(monkeypatch):
    monkeypatch.delitem(sys.modules, "pyarrow", raising=False)
    assert memory_helpers._arrow_pool() == (None, None)


def test_log_sample_writes_one_plain_line_with_unavailable_figures_as_na(monkeypatch, caplog):
    monkeypatch.setattr(memory_helpers, "_MALLINFO2", None)
    with caplog.at_level(logging.INFO, logger="memory_helpers"):
        memory_helpers.log_sample("periodic")
    (line,) = _memory_lines(caplog)
    assert line.startswith("Memory periodic: rss=")
    assert "glibc_in_use=n/a" in line and "glibc_free_held=n/a" in line
    assert "\n" not in line


def _fake_rss(monkeypatch, value):
    monkeypatch.setattr(memory_helpers, "rss_mib", lambda: value)


def test_job_memory_line_is_skipped_for_a_short_job_that_did_not_move_rss(monkeypatch, caplog):
    _fake_rss(monkeypatch, 1000 + MEMORY_JOB_LOG_MIN_DELTA_MIB - 1)
    with caplog.at_level(logging.INFO, logger="memory_helpers"):
        memory_helpers.log_job_memory("quick_job", 1000.0, MEMORY_JOB_LOG_MIN_SECONDS - 0.1)
    assert _memory_lines(caplog) == []


def test_job_memory_line_is_written_for_a_long_job(monkeypatch, caplog):
    _fake_rss(monkeypatch, 1000.0)
    with caplog.at_level(logging.INFO, logger="memory_helpers"):
        memory_helpers.log_job_memory("slow_job", 1000.0, MEMORY_JOB_LOG_MIN_SECONDS)
    (line,) = _memory_lines(caplog)
    assert "slow_job" in line and "rss 1000 -> 1000 MiB (+0)" in line


def test_job_memory_line_is_written_when_rss_moves_by_the_threshold(monkeypatch, caplog):
    _fake_rss(monkeypatch, 1000 + MEMORY_JOB_LOG_MIN_DELTA_MIB)
    with caplog.at_level(logging.INFO, logger="memory_helpers"):
        memory_helpers.log_job_memory("growing_job", 1000.0, 0.2)
    (line,) = _memory_lines(caplog)
    assert "growing_job" in line and f"(+{MEMORY_JOB_LOG_MIN_DELTA_MIB})" in line


def test_tracked_job_logs_memory_for_a_slow_job_and_stays_clean_after(monkeypatch, caplog):
    monkeypatch.setattr(memory_helpers, "MEMORY_JOB_LOG_MIN_SECONDS", 0)
    with caplog.at_level(logging.INFO, logger="memory_helpers"):
        scheduler_engine.run_manual_job("memory_probe_job", lambda: None)
    assert any("Memory job memory_probe_job" in line for line in _memory_lines(caplog))
    assert not scheduler_engine.is_job_running("memory_probe_job")


@pytest.mark.db
def test_run_memory_telemetry_job_runner_logs_and_records_the_run(caplog):
    import scheduler_jobs
    with caplog.at_level(logging.INFO, logger="memory_helpers"):
        scheduler_jobs.run_memory_telemetry_job()
    assert any(line.startswith("Memory periodic:") for line in _memory_lines(caplog))
    assert scheduler_engine.get_all_job_last_runs()["memory_telemetry_job"]["last_run"]


@pytest.mark.db
def test_run_memory_telemetry_job_reports_a_failure_and_still_records_the_run(monkeypatch):
    import scheduler_jobs
    seen = []

    def boom(reason):
        raise RuntimeError("no /proc")

    monkeypatch.setattr(memory_helpers, "log_sample", boom)
    monkeypatch.setattr(scheduler_jobs, "log_sched_notification", lambda kind, text: seen.append((kind, text)))
    scheduler_jobs.run_memory_telemetry_job()
    assert seen == [("Error", "Process Memory Telemetry failed: no /proc")]


def test_memory_telemetry_job_is_scheduled_every_interval_with_its_canonical_label():
    scheduler_engine.reload_scheduler()
    job = scheduler_engine.scheduler.get_job("memory_telemetry_job")
    assert job is not None
    assert job.trigger.interval.total_seconds() == MEMORY_SAMPLE_INTERVAL_MINUTES * 60
    assert scheduler_engine.job_label("memory_telemetry_job") == "Process Memory Telemetry"
    node = next(n for n in scheduler_engine.build_workflow_graph()["nodes"] if n["id"] == "memory_telemetry_job")
    assert node["label"] == "Process Memory Telemetry"


@pytest.mark.api
def test_system_metrics_carries_the_memory_sample(client):
    body = client.get("/api/system/metrics").json()
    assert SAMPLE_KEYS <= set(body["memory"])
    assert body["memory"]["rss_mib"] > 0
