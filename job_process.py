import json
import logging
import os
import re
import subprocess
import sys
import threading
import time

import memory_helpers
from config import BASE_DIR
from constants import (
    CHILD_JOB_OOM_SCORE_ADJ,
    CHILD_JOB_WATCHDOG_MIN_AVAILABLE_MIB,
    CHILD_JOB_WATCHDOG_POLL_SECONDS,
)
from log_config import SafeFormatter
from scheduler_manifest import job_label

logger = logging.getLogger(__name__)

EXIT_STEP_FAILED = 3
_CHILD_COMMAND = [sys.executable, "-c", "import sys, job_process; sys.exit(job_process.child_main())"]
_CHILD_LINE_FORMAT = "%(levelname)s|%(name)s|%(message)s"
_CHILD_LINE = re.compile(r"^(DEBUG|INFO|WARNING|ERROR|CRITICAL)\|([^|]*)\|(.*)$")

# Two heavy children side by side can exhaust the host's RAM, so a second one waits for the first.
_child_slot = threading.Lock()
_running_label = None


class ChildJobFailed(RuntimeError):
    """The text is written for the Notifications tab; steps that fail on their own have already notified."""


def _train_global_models() -> bool:
    from ml_training_engine import train_global_ml_model, train_quantile_models

    classifier_ok = train_global_ml_model()
    quantile_ok = train_quantile_models()
    return classifier_ok and quantile_ok


def _scan_tail_risk(tickers: list, scan_type: str | None) -> bool:
    from risk_engine import update_all_tail_risks

    update_all_tail_risks(tickers, scan_type=scan_type)
    return True


# Entry points import their engines inside the child, so the web process never loads those stacks for them.
CHILD_JOBS = {
    "global_model_training": (job_label("ml_training_job"), _train_global_models),
    "tail_risk_scan": ("Tail Risk (VaR) Scan", _scan_tail_risk),
}


def _relay_child_output(stream, label: str) -> None:
    for raw in stream:
        line = raw.rstrip("\r\n")
        match = _CHILD_LINE.match(line)
        if match:
            level, name, message = match.groups()
            logging.getLogger(name).log(logging.getLevelName(level), "%s", message)
        elif line:
            logger.info("%s child: %s", label, line)


def _mib_text(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0f}"


def _failure_reason(label: str, returncode: int, killed_by_watchdog: str | None) -> str:
    if killed_by_watchdog:
        return killed_by_watchdog
    if returncode == EXIT_STEP_FAILED:
        return f"a step of {label} did not finish (see the Error notifications above and app.log)"
    if returncode == -9:
        return f"the {label} process was killed (signal 9, usually the kernel's out-of-memory killer)"
    if returncode < 0:
        return f"the {label} process was killed by signal {-returncode}"
    return f"the {label} process exited with code {returncode}"


def run_child_job(job: str, **kwargs) -> None:
    """Runs a CHILD_JOBS entry in a fresh interpreter so its memory goes back to the OS and a kill cannot take the web server down."""
    global _running_label
    label = CHILD_JOBS[job][0]
    if not _child_slot.acquire(blocking=False):
        logger.info("%s child waits for the running %s child to finish.", label, _running_label)
        _child_slot.acquire()
    _running_label = label
    try:
        _supervise(job, label, kwargs)
    finally:
        _running_label = None
        _child_slot.release()


def _supervise(job: str, label: str, kwargs: dict) -> None:
    started = time.monotonic()
    process = subprocess.Popen(
        _CHILD_COMMAND,
        cwd=BASE_DIR,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env={**os.environ, "PYTHONUNBUFFERED": "1", "PYTHONIOENCODING": "utf-8"},
    )
    relay = threading.Thread(target=_relay_child_output, args=(process.stdout, label), name="child-job-log-relay", daemon=True)
    relay.start()
    logger.info("%s child started (pid %d).", label, process.pid)
    try:
        process.stdin.write(json.dumps({"job": job, "kwargs": kwargs}))
        process.stdin.close()
    except BrokenPipeError:
        pass  # the child died before reading its request; its exit code below says why

    peak_rss_mib = None
    lowest_available_mib = float("inf")
    killed_by_watchdog = None
    try:
        while True:
            try:
                returncode = process.wait(timeout=CHILD_JOB_WATCHDOG_POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                pass
            available_mib = memory_helpers.available_mib()
            lowest_available_mib = min(lowest_available_mib, available_mib)
            peak_rss_mib = memory_helpers.peak_rss_mib(process.pid) or peak_rss_mib
            if available_mib < CHILD_JOB_WATCHDOG_MIN_AVAILABLE_MIB and killed_by_watchdog is None:
                killed_by_watchdog = (
                    f"stopped by the memory watchdog: only {available_mib:.0f} MiB RAM available "
                    f"(minimum {CHILD_JOB_WATCHDOG_MIN_AVAILABLE_MIB} MiB)"
                )
                logger.error("%s child killed: %s; its peak RSS was %s MiB.", label, killed_by_watchdog, _mib_text(peak_rss_mib))
                process.kill()
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()
    relay.join(timeout=5)

    logger.info(
        "%s child finished: exit code %d after %.0f s, peak RSS %s MiB, lowest available RAM %s MiB.",
        label, returncode, time.monotonic() - started, _mib_text(peak_rss_mib),
        _mib_text(None if lowest_available_mib == float("inf") else lowest_available_mib),
    )
    if returncode != 0:
        raise ChildJobFailed(_failure_reason(label, returncode, killed_by_watchdog))


def child_main() -> int:
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(SafeFormatter(_CHILD_LINE_FORMAT))
    logging.basicConfig(level=logging.INFO, handlers=[handler])
    try:
        with open("/proc/self/oom_score_adj", "w") as score:
            score.write(str(CHILD_JOB_OOM_SCORE_ADJ))
    except OSError as e:
        logger.warning("Could not raise this process's OOM score: %s", e)

    request = json.load(sys.stdin)
    entry = CHILD_JOBS[request["job"]][1]
    return 0 if entry(**request["kwargs"]) else EXIT_STEP_FAILED
