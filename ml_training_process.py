# GUI name: "Global Model Training (Walk-Forward)". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.
import logging
import os
import re
import subprocess
import sys
import threading
import time

import psutil

import memory_helpers
from config import BASE_DIR
from constants import (
    ML_TRAINING_CHILD_OOM_SCORE_ADJ,
    ML_TRAINING_WATCHDOG_MIN_AVAILABLE_MIB,
    ML_TRAINING_WATCHDOG_POLL_SECONDS,
)
from log_config import SafeFormatter

logger = logging.getLogger(__name__)

EXIT_STEP_FAILED = 3
_CHILD_COMMAND = [sys.executable, "-c", "import sys, ml_training_process; sys.exit(ml_training_process.child_main())"]
_CHILD_LINE_FORMAT = "%(levelname)s|%(name)s|%(message)s"
_CHILD_LINE = re.compile(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)\|([^|]*)\|(.*)$")


class TrainingFailed(RuntimeError):
    """The text is written for the Notifications tab; training steps that fail on their own have already notified."""


def _relay_child_output(stream) -> None:
    for raw in stream:
        line = raw.rstrip("\r\n")
        match = _CHILD_LINE.match(line)
        if match:
            level, name, message = match.groups()
            logging.getLogger(name).log(logging.getLevelName(level), "%s", message)
        elif line:
            logger.info("training child: %s", line)


def _failure_reason(returncode: int, killed_by_watchdog: str | None) -> str:
    if killed_by_watchdog:
        return killed_by_watchdog
    if returncode == EXIT_STEP_FAILED:
        return "a training step did not finish (see the Error notifications above and app.log)"
    if returncode == -9:
        return "the training process was killed (signal 9, usually the kernel's out-of-memory killer)"
    if returncode < 0:
        return f"the training process was killed by signal {-returncode}"
    return f"the training process exited with code {returncode}"


def run_training_child() -> None:
    """Runs both training steps in a fresh interpreter so their memory goes back to the OS and a kill cannot take the web server down."""
    started = time.monotonic()
    process = subprocess.Popen(
        _CHILD_COMMAND,
        cwd=BASE_DIR,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
    )
    relay = threading.Thread(target=_relay_child_output, args=(process.stdout,), name="ml-training-log-relay", daemon=True)
    relay.start()
    logger.info("Global Model Training child started (pid %d).", process.pid)

    peak_rss_mib = 0.0
    lowest_available_mib = float("inf")
    killed_by_watchdog = None
    try:
        while True:
            try:
                returncode = process.wait(timeout=ML_TRAINING_WATCHDOG_POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                pass
            available_mib = memory_helpers.available_mib()
            lowest_available_mib = min(lowest_available_mib, available_mib)
            try:
                peak_rss_mib = max(peak_rss_mib, memory_helpers.rss_mib(process.pid))
            except psutil.NoSuchProcess:
                continue
            if available_mib < ML_TRAINING_WATCHDOG_MIN_AVAILABLE_MIB and killed_by_watchdog is None:
                killed_by_watchdog = (
                    f"stopped by the memory watchdog: only {available_mib:.0f} MiB RAM available "
                    f"(minimum {ML_TRAINING_WATCHDOG_MIN_AVAILABLE_MIB} MiB)"
                )
                logger.error("Global Model Training child killed: %s; its sampled peak RSS was %.0f MiB.", killed_by_watchdog, peak_rss_mib)
                process.kill()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    relay.join(timeout=5)

    logger.info(
        "Global Model Training child finished: exit code %d after %.0f s, sampled peak RSS %.0f MiB, lowest available RAM %s MiB.",
        returncode, time.monotonic() - started, peak_rss_mib,
        "n/a" if lowest_available_mib == float("inf") else f"{lowest_available_mib:.0f}",
    )
    if returncode != 0:
        raise TrainingFailed(_failure_reason(returncode, killed_by_watchdog))


def child_main() -> int:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(SafeFormatter(_CHILD_LINE_FORMAT))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    try:
        with open("/proc/self/oom_score_adj", "w") as score:
            score.write(str(ML_TRAINING_CHILD_OOM_SCORE_ADJ))
    except OSError as e:
        logger.warning("Could not raise this process's OOM score: %s", e)

    # Imported here so the web process never loads the training stack.
    from ml_training_engine import train_global_ml_model, train_quantile_models

    classifier_ok = train_global_ml_model()
    quantile_ok = train_quantile_models()
    return 0 if classifier_ok and quantile_ok else EXIT_STEP_FAILED
