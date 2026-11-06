"""
tests/test_ml_training_process.py — Global Model Training runs in a child process (LR-02)

Covers:
  • the supervisor relays the child's log lines, reports a clean exit, a handled step failure and a kill
  • the memory watchdog kills a child that leaves too little available RAM, and not one that does not
  • the child entry point raises its own OOM score and maps the two training steps to its exit code
  • the real child command starts a second interpreter, imports the training engine and exits with the mapped code
"""

import logging
import sys
import time
from pathlib import Path
from unittest.mock import mock_open, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import ml_training_process as proc


def _child(code: str) -> list[str]:
    return [sys.executable, "-c", code]


@pytest.fixture
def fast_poll():
    with patch.object(proc, "ML_TRAINING_WATCHDOG_POLL_SECONDS", 0.05):
        yield


def test_clean_exit_relays_child_log_lines_at_their_level(fast_poll, caplog):
    code = "print('WARNING|ml_training_engine|low on memory'); print('a traceback line'); print('INFO|x|done')"
    with patch.object(proc, "_CHILD_COMMAND", _child(code)), caplog.at_level(logging.INFO):
        proc.run_training_child()

    relayed = {(r.name, r.levelname, r.getMessage()) for r in caplog.records}
    assert ("ml_training_engine", "WARNING", "low on memory") in relayed
    assert ("x", "INFO", "done") in relayed
    assert (proc.logger.name, "INFO", "training child: a traceback line") in relayed
    assert any("Global Model Training child finished: exit code 0" in r.getMessage() for r in caplog.records)


def test_a_handled_step_failure_raises_with_the_exit_code_meaning(fast_poll):
    with patch.object(proc, "_CHILD_COMMAND", _child(f"import sys; sys.exit({proc.EXIT_STEP_FAILED})")):
        with pytest.raises(proc.TrainingFailed, match="did not finish"):
            proc.run_training_child()


def test_a_killed_child_raises_and_names_the_signal(fast_poll):
    code = "import os, signal; os.kill(os.getpid(), signal.SIGKILL)"
    with patch.object(proc, "_CHILD_COMMAND", _child(code)):
        with pytest.raises(proc.TrainingFailed, match="signal 9"):
            proc.run_training_child()


def test_an_unexpected_exit_code_is_reported(fast_poll):
    with patch.object(proc, "_CHILD_COMMAND", _child("import sys; sys.exit(7)")):
        with pytest.raises(proc.TrainingFailed, match="exited with code 7"):
            proc.run_training_child()


def test_the_watchdog_kills_a_child_when_available_memory_falls_below_the_floor(fast_poll):
    low = proc.ML_TRAINING_WATCHDOG_MIN_AVAILABLE_MIB - 100
    started = time.monotonic()
    with patch.object(proc, "_CHILD_COMMAND", _child("import time; time.sleep(60)")), \
            patch.object(proc.memory_helpers, "available_mib", return_value=low):
        with pytest.raises(proc.TrainingFailed, match="memory watchdog"):
            proc.run_training_child()
    assert time.monotonic() - started < 30


def test_the_watchdog_leaves_a_child_alone_while_memory_is_available(fast_poll):
    plenty = 8 * 1024.0
    with patch.object(proc, "_CHILD_COMMAND", _child("import time; time.sleep(0.4)")), \
            patch.object(proc.memory_helpers, "available_mib", return_value=plenty):
        proc.run_training_child()


def test_child_main_raises_its_oom_score_and_exits_zero_when_both_steps_finish():
    opened = mock_open()
    with patch.object(proc, "open", opened, create=True), \
            patch.object(proc.logging, "basicConfig"), \
            patch("ml_training_engine.train_global_ml_model", return_value=True) as classifier, \
            patch("ml_training_engine.train_quantile_models", return_value=True) as quantile:
        assert proc.child_main() == 0

    opened.assert_called_once_with("/proc/self/oom_score_adj", "w")
    opened().write.assert_called_once_with(str(proc.ML_TRAINING_CHILD_OOM_SCORE_ADJ))
    classifier.assert_called_once()
    quantile.assert_called_once()


@pytest.mark.parametrize("classifier_ok,quantile_ok", [(False, True), (True, False), (False, False)])
def test_child_main_exits_with_the_failure_code_but_still_runs_the_second_step(classifier_ok, quantile_ok):
    with patch.object(proc, "open", mock_open(), create=True), \
            patch.object(proc.logging, "basicConfig"), \
            patch("ml_training_engine.train_global_ml_model", return_value=classifier_ok), \
            patch("ml_training_engine.train_quantile_models", return_value=quantile_ok) as quantile:
        assert proc.child_main() == proc.EXIT_STEP_FAILED

    quantile.assert_called_once()


def test_child_main_still_runs_when_the_oom_score_cannot_be_written(caplog):
    with patch.object(proc, "open", side_effect=PermissionError("denied"), create=True), \
            patch.object(proc.logging, "basicConfig"), \
            patch("ml_training_engine.train_global_ml_model", return_value=True), \
            patch("ml_training_engine.train_quantile_models", return_value=True), \
            caplog.at_level(logging.WARNING):
        assert proc.child_main() == 0

    assert any("Could not raise this process's OOM score" in r.getMessage() for r in caplog.records)


@pytest.mark.parametrize("steps_ok,expected_exit", [(True, 0), (False, proc.EXIT_STEP_FAILED)])
def test_real_child_command_imports_the_engine_and_maps_the_steps_to_its_exit_code(fast_poll, caplog, steps_ok, expected_exit):
    assert proc._CHILD_COMMAND[:2] == [sys.executable, "-c"]
    assert "ml_training_process.child_main()" in proc._CHILD_COMMAND[2]
    stubbed = (
        "import sys, ml_training_engine as e, ml_training_process as m\n"
        "def step():\n"
        "    e.logger.info('child reached the stubbed steps')\n"
        f"    return {steps_ok}\n"
        "e.train_global_ml_model = step\n"
        "e.train_quantile_models = step\n"
        "sys.exit(m.child_main())\n"
    )

    with patch.object(proc, "_CHILD_COMMAND", _child(stubbed)), caplog.at_level(logging.INFO):
        if expected_exit:
            with pytest.raises(proc.TrainingFailed):
                proc.run_training_child()
        else:
            proc.run_training_child()
    assert any(r.getMessage() == "child reached the stubbed steps" for r in caplog.records)
