"""
tests/test_job_process.py — heavy jobs run in a child process

Covers:
  • the supervisor relays the child's log lines, reports a clean exit, a handled step failure and a kill
  • the memory watchdog kills a child that leaves too little available RAM, and not one that does not
  • only one child runs at a time; a second request waits and logs whom it waits for
  • the child entry point raises its own OOM score, reads its request from stdin and maps the entry's result to its exit code
  • the real child command starts a second interpreter, imports the engine and exits with the mapped code
"""

import io
import json
import logging
import sys
import threading
import time
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import job_process as proc


JOB = "global_model_training"


def _child(code: str) -> list[str]:
    return [sys.executable, "-c", code]


@pytest.fixture
def fast_poll():
    with patch.object(proc, "CHILD_JOB_WATCHDOG_POLL_SECONDS", 0.05):
        yield


def test_clean_exit_relays_child_log_lines_at_their_level(fast_poll, caplog):
    code = "print('WARNING|ml_training_engine|low on memory'); print('a traceback line'); print('INFO|x|done')"
    with patch.object(proc, "_CHILD_COMMAND", _child(code)), caplog.at_level(logging.INFO):
        proc.run_child_job(JOB)

    relayed = {(r.name, r.levelname, r.getMessage()) for r in caplog.records}
    assert ("ml_training_engine", "WARNING", "low on memory") in relayed
    assert ("x", "INFO", "done") in relayed
    assert (proc.logger.name, "INFO", "Global Model Training (Walk-Forward) child: a traceback line") in relayed
    assert any("Global Model Training (Walk-Forward) child finished: exit code 0" in r.getMessage() for r in caplog.records)


def test_a_handled_step_failure_raises_with_the_exit_code_meaning(fast_poll):
    with patch.object(proc, "_CHILD_COMMAND", _child(f"import sys; sys.exit({proc.EXIT_STEP_FAILED})")):
        with pytest.raises(proc.ChildJobFailed, match="a step of Global Model Training \\(Walk-Forward\\) did not finish"):
            proc.run_child_job(JOB)


def test_a_killed_child_raises_and_names_the_signal(fast_poll):
    code = "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"
    with patch.object(proc, "_CHILD_COMMAND", _child(code)):
        with pytest.raises(proc.ChildJobFailed, match="Global Model Training \\(Walk-Forward\\) process was killed \\(signal 9"):
            proc.run_child_job(JOB)


def test_an_unexpected_exit_code_is_reported(fast_poll):
    with patch.object(proc, "_CHILD_COMMAND", _child("import sys; sys.exit(7)")):
        with pytest.raises(proc.ChildJobFailed, match="Walk-Forward\\) process exited with code 7"):
            proc.run_child_job(JOB)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="VmHWM is read from /proc")
def test_the_finish_line_reports_a_memory_spike_that_ended_before_the_poll(fast_poll, caplog):
    code = (
        "import time\n"
        "blob = bytearray(150 * 1024 * 1024)\n"
        "blob[::4096] = b'x' * len(blob[::4096])\n"
        "del blob\n"
        "time.sleep(0.6)\n"
    )
    with patch.object(proc, "_CHILD_COMMAND", _child(code)), caplog.at_level(logging.INFO):
        proc.run_child_job(JOB)

    finish = next(r.getMessage() for r in caplog.records if "child finished" in r.getMessage())
    assert float(finish.split("peak RSS ")[1].split(" MiB")[0]) >= 150


def test_the_finish_line_says_n_a_when_the_peak_could_not_be_read(fast_poll, caplog):
    with patch.object(proc, "_CHILD_COMMAND", _child("import time; time.sleep(0.3)")), \
            patch.object(proc.memory_helpers, "peak_rss_mib", return_value=None), caplog.at_level(logging.INFO):
        proc.run_child_job(JOB)
    assert any("peak RSS n/a MiB" in r.getMessage() for r in caplog.records)


def test_the_watchdog_kills_a_child_when_available_memory_falls_below_the_floor(fast_poll):
    low = proc.CHILD_JOB_WATCHDOG_MIN_AVAILABLE_MIB - 100
    started = time.monotonic()
    with patch.object(proc, "_CHILD_COMMAND", _child("import time; time.sleep(60)")), \
            patch.object(proc.memory_helpers, "available_mib", return_value=low):
        with pytest.raises(proc.ChildJobFailed, match="memory watchdog"):
            proc.run_child_job(JOB)
    assert time.monotonic() - started < 30


def test_the_watchdog_leaves_a_child_alone_while_memory_is_available(fast_poll):
    plenty = 8 * 1024.0
    with patch.object(proc, "_CHILD_COMMAND", _child("import time; time.sleep(0.4)")), \
            patch.object(proc.memory_helpers, "available_mib", return_value=plenty):
        proc.run_child_job(JOB)


def _stdin(job=JOB, **kwargs):
    return patch.object(proc.sys, "stdin", io.StringIO(json.dumps({"job": job, "kwargs": kwargs})))


def test_child_main_raises_its_oom_score_and_exits_zero_when_both_steps_finish():
    opened = mock_open()
    with _stdin(), patch.object(proc, "open", opened, create=True), \
            patch.object(proc.logging, "basicConfig"), \
            patch("ml_training_engine.train_global_ml_model", return_value=True) as classifier, \
            patch("ml_training_engine.train_quantile_models", return_value=True) as quantile:
        assert proc.child_main() == 0

    opened.assert_called_once_with("/proc/self/oom_score_adj", "w")
    opened().write.assert_called_once_with(str(proc.CHILD_JOB_OOM_SCORE_ADJ))
    classifier.assert_called_once()
    quantile.assert_called_once()


@pytest.mark.parametrize("classifier_ok,quantile_ok", [(False, True), (True, False), (False, False)])
def test_child_main_exits_with_the_failure_code_but_still_runs_the_second_step(classifier_ok, quantile_ok):
    with _stdin(), patch.object(proc, "open", mock_open(), create=True), \
            patch.object(proc.logging, "basicConfig"), \
            patch("ml_training_engine.train_global_ml_model", return_value=classifier_ok), \
            patch("ml_training_engine.train_quantile_models", return_value=quantile_ok) as quantile:
        assert proc.child_main() == proc.EXIT_STEP_FAILED

    quantile.assert_called_once()


def test_child_main_still_runs_when_the_oom_score_cannot_be_written(caplog):
    with _stdin(), patch.object(proc, "open", side_effect=PermissionError("denied"), create=True), \
            patch.object(proc.logging, "basicConfig"), \
            patch("ml_training_engine.train_global_ml_model", return_value=True), \
            patch("ml_training_engine.train_quantile_models", return_value=True), \
            caplog.at_level(logging.WARNING):
        assert proc.child_main() == 0

    assert any("Could not raise this process's OOM score" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("steps_ok,expected_exit", [(True, 0), (False, proc.EXIT_STEP_FAILED)])
def test_real_child_command_imports_the_engine_and_maps_the_steps_to_its_exit_code(fast_poll, caplog, steps_ok, expected_exit):
    assert proc._CHILD_COMMAND[:2] == [sys.executable, "-c"]
    assert "job_process.child_main()" in proc._CHILD_COMMAND[2]
    stubbed = (
        "import sys, ml_training_engine as e, job_process as m\n"
        "def step():\n"
        "    e.logger.info('child reached the stubbed steps')\n"
        f"    return {steps_ok}\n"
        "e.train_global_ml_model = step\n"
        "e.train_quantile_models = step\n"
        "sys.exit(m.child_main())\n"
    )

    with patch.object(proc, "_CHILD_COMMAND", _child(stubbed)), caplog.at_level(logging.INFO):
        if expected_exit:
            with pytest.raises(proc.ChildJobFailed):
                proc.run_child_job(JOB)
        else:
            proc.run_child_job(JOB)
    assert any(r.getMessage() == "child reached the stubbed steps" for r in caplog.records)


def test_real_child_command_receives_its_request_through_stdin(fast_poll):
    stubbed = (
        "import sys, risk_engine as r, job_process as m\n"
        "r.update_all_tail_risks = lambda tickers, scan_type=None: sys.exit(0 if (tickers, scan_type) == (['A', 'B'], 'x') else 9)\n"
        "sys.exit(m.child_main())\n"
    )
    with patch.object(proc, "_CHILD_COMMAND", _child(stubbed)):
        proc.run_child_job("tail_risk_scan", tickers=["A", "B"], scan_type="x")


def test_tail_risk_entry_passes_tickers_and_scan_type_and_exits_zero():
    with _stdin("tail_risk_scan", tickers=["AAPL", "VOD.L"], scan_type="tail_risk_daily"), \
            patch.object(proc, "open", mock_open(), create=True), \
            patch.object(proc.logging, "basicConfig"), \
            patch("risk_engine.update_all_tail_risks") as scan:
        assert proc.child_main() == 0

    scan.assert_called_once_with(["AAPL", "VOD.L"], scan_type="tail_risk_daily")


def test_a_second_child_waits_for_the_running_one_and_never_overlaps(fast_poll, caplog):
    events = []
    first_started = threading.Event()
    real_supervise = proc._supervise

    def tracked(job, label, kwargs):
        events.append(("start", job))
        first_started.set()
        try:
            real_supervise(job, label, kwargs)
        finally:
            events.append(("end", job))

    def run(job):
        proc.run_child_job(job)

    with patch.object(proc, "_CHILD_COMMAND", _child("import time; time.sleep(0.6)")), \
            patch.object(proc, "_supervise", tracked), caplog.at_level(logging.INFO):
        one = threading.Thread(target=run, args=("global_model_training",))
        one.start()
        assert first_started.wait(5)
        two = threading.Thread(target=run, args=("tail_risk_scan",))
        two.start()
        one.join(30)
        two.join(30)

    assert events == [
        ("start", "global_model_training"), ("end", "global_model_training"),
        ("start", "tail_risk_scan"), ("end", "tail_risk_scan"),
    ]
    assert any(
        r.getMessage() == "Tail Risk (VaR) Scan child waits for the running Global Model Training (Walk-Forward) child to finish."
        for r in caplog.records
    )
    assert proc._running_label is None
