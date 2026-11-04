# GUI name: "Process Memory Telemetry". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.
import ctypes
import gc
import logging
import sys
from datetime import datetime, timezone

import psutil

from constants import MEMORY_JOB_LOG_MIN_DELTA_MIB, MEMORY_JOB_LOG_MIN_SECONDS

logger = logging.getLogger(__name__)

_MIB = 1024 * 1024


class _Mallinfo2(ctypes.Structure):
    _fields_ = [(name, ctypes.c_size_t) for name in (
        "arena", "ordblks", "smblks", "hblks", "hblkhd",
        "usmblks", "fsmblks", "uordblks", "fordblks", "keepcost",
    )]


def _load_mallinfo2():
    try:
        function = ctypes.CDLL(None).mallinfo2
    except (AttributeError, OSError):
        return None
    function.restype = _Mallinfo2
    return function


_MALLINFO2 = _load_mallinfo2()


def _proc_kb_fields(path: str) -> dict[str, int]:
    fields = {}
    try:
        with open(path) as handle:
            for line in handle:
                name, _, rest = line.partition(":")
                parts = rest.split()
                if parts and parts[0].isdigit():
                    fields[name] = int(parts[0])
    except OSError:
        pass
    return fields


def _kb_to_mib(value: int | None) -> float | None:
    return None if value is None else value / 1024


def _bytes_to_mib(value: int | None) -> float | None:
    return None if value is None else value / _MIB


def _arrow_pool() -> tuple[str | None, float | None]:
    arrow = sys.modules.get("pyarrow")
    if arrow is None:
        return None, None
    pool = arrow.default_memory_pool()
    return pool.backend_name, pool.bytes_allocated() / _MIB


def rss_mib() -> float:
    return psutil.Process().memory_info().rss / _MIB


def sample() -> dict:
    """Process and allocator memory in MiB; a figure this platform cannot report is None, never 0."""
    process = psutil.Process()
    status = _proc_kb_fields("/proc/self/status")
    rollup = _proc_kb_fields("/proc/self/smaps_rollup")
    now = datetime.now(timezone.utc)
    info = _MALLINFO2() if _MALLINFO2 else None
    arrow_backend, arrow_mib = _arrow_pool()
    host = psutil.virtual_memory()
    return {
        "taken_at": now.strftime("%Y-%m-%d %H:%M:%S"),
        "rss_mib": process.memory_info().rss / _MIB,
        "rss_anon_mib": _kb_to_mib(status.get("RssAnon")),
        "rss_file_mib": _kb_to_mib(status.get("RssFile")),
        "rss_peak_mib": _kb_to_mib(status.get("VmHWM")),
        "anon_huge_mib": _kb_to_mib(rollup.get("AnonHugePages")),
        "available_mib": host.available / _MIB,
        "total_mib": host.total / _MIB,
        "glibc_in_use_mib": _bytes_to_mib(info.uordblks + info.hblkhd) if info else None,
        "glibc_free_held_mib": _bytes_to_mib(info.fordblks) if info else None,
        "arrow_backend": arrow_backend,
        "arrow_mib": arrow_mib,
        "python_blocks": sys.getallocatedblocks(),
        "gc_gen2_collections": gc.get_stats()[2]["collections"],
        "threads": status.get("Threads"),
        "uptime_hours": (now.timestamp() - process.create_time()) / 3600,
    }


def _fmt(value: float | int | None) -> str:
    return "n/a" if value is None else f"{value:.0f}"


def log_sample(reason: str) -> None:
    s = sample()
    logger.info(
        "Memory %s: rss=%s MiB (anon=%s file=%s thp=%s) peak=%s available=%s "
        "glibc_in_use=%s glibc_free_held=%s arrow=%s(%s) py_blocks=%d gc2=%d threads=%s uptime=%.1fh",
        reason, _fmt(s["rss_mib"]), _fmt(s["rss_anon_mib"]), _fmt(s["rss_file_mib"]), _fmt(s["anon_huge_mib"]),
        _fmt(s["rss_peak_mib"]), _fmt(s["available_mib"]),
        _fmt(s["glibc_in_use_mib"]), _fmt(s["glibc_free_held_mib"]), _fmt(s["arrow_mib"]), s["arrow_backend"] or "n/a",
        s["python_blocks"], s["gc_gen2_collections"], _fmt(s["threads"]), s["uptime_hours"],
    )


def log_job_memory(job_id: str, rss_before_mib: float, duration_sec: float) -> None:
    """One line only for a job that ran long or moved the process RSS, so a once-a-minute job adds nothing to the log."""
    rss_after = rss_mib()
    delta = rss_after - rss_before_mib
    if duration_sec < MEMORY_JOB_LOG_MIN_SECONDS and abs(delta) < MEMORY_JOB_LOG_MIN_DELTA_MIB:
        return
    logger.info(
        "Memory job %s: %.0fs, rss %.0f -> %.0f MiB (%+.0f), %.0f MiB available",
        job_id, duration_sec, rss_before_mib, rss_after, delta, psutil.virtual_memory().available / _MIB,
    )
