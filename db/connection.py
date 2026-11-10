import contextlib
import gc
import logging
import os
import random
import sqlite3
import sys
import threading
import time
import weakref
from typing import Callable, Optional

from constants import (
    DB_DEFERRED_WRITE_QUEUE_MAX, DB_DEFERRED_WRITE_RETRY_SECONDS, DB_SWAP_DRAIN_TIMEOUT_SECONDS,
    DB_SWAP_GATE_WAIT_SECONDS, DB_WRITE_HOLD_WARN_SECONDS,
)

logger = logging.getLogger(__name__)

_LOCK_RETRY_ATTEMPTS = 3
_LOCK_RETRY_BASE_DELAY = 0.5

_open_writes: dict = {}
_open_writes_lock = threading.Lock()

_swap_gate = threading.Condition()
_swap_gate_closed = False
_live_connections = 0


class DatabaseSwapInProgress(sqlite3.OperationalError):
    pass


class DatabaseInUse(Exception):
    pass


def _claim_connection_slot() -> None:
    global _live_connections
    deadline = time.monotonic() + DB_SWAP_GATE_WAIT_SECONDS
    with _swap_gate:
        while _swap_gate_closed:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise DatabaseSwapInProgress("The database is being replaced by a backup restore. Try again in a moment.")
            _swap_gate.wait(remaining)
        _live_connections += 1


def _release_connection_slot() -> None:
    global _live_connections
    with _swap_gate:
        _live_connections -= 1
        _swap_gate.notify_all()


@contextlib.contextmanager
def database_swap_gate(drain_timeout: float = DB_SWAP_DRAIN_TIMEOUT_SECONDS):
    """Holds new get_connection() calls and waits for the open ones to close, so the database file can be replaced with no connection spanning the swap; raises DatabaseInUse (nothing held) when connections stay open."""
    global _swap_gate_closed
    with _swap_gate:
        _swap_gate_closed = True
    try:
        gc.collect()
        deadline = time.monotonic() + drain_timeout
        with _swap_gate:
            while _live_connections > 0:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise DatabaseInUse(f"{_live_connections} database connection(s) are still open.")
                _swap_gate.wait(remaining)
        yield
    finally:
        with _swap_gate:
            _swap_gate_closed = False
            _swap_gate.notify_all()


def is_lock_error(error: BaseException) -> bool:
    return isinstance(error, sqlite3.OperationalError) and "database is locked" in str(error)


_CONNECTION_MODULES = ("db.connection", "db.database")


def _caller() -> str:
    frame = sys._getframe(1)
    while frame is not None and frame.f_globals.get("__name__") in _CONNECTION_MODULES:
        frame = frame.f_back
    if frame is None:
        return "unknown"
    return f"{os.path.basename(frame.f_code.co_filename)}:{frame.f_code.co_name}:{frame.f_lineno}"


def _describe_open_writes() -> str:
    now = time.monotonic()
    holders = []
    with _open_writes_lock:
        for key, entry in list(_open_writes.items()):
            if entry["conn"]() is None:
                del _open_writes[key]
            else:
                holders.append((now - entry["started"], entry))
    if not holders:
        return "none tracked (held by another process or by a connection not opened through get_connection)"
    holders.sort(key=lambda holder: -holder[0])
    return "; ".join(
        f"held {age:.1f}s by {entry['caller']} [{entry['thread']}], first statement: {entry['statement']}"
        for age, entry in holders
    )


def _retry_on_locked(call, *args, **kwargs):
    # A busy_timeout expiry means the write lock was contended for the full 20s already —
    # a handful of short, jittered retries recovers from bursty multi-job pileups (several
    # scheduled jobs writing at once) without the caller having to know retry ever happened.
    for attempt in range(_LOCK_RETRY_ATTEMPTS + 1):
        try:
            return call(*args, **kwargs)
        except sqlite3.OperationalError as e:
            if not is_lock_error(e):
                raise
            final = attempt == _LOCK_RETRY_ATTEMPTS
            if attempt == 0 or final:
                logger.warning(
                    "SQLite write lock not obtained (wait %d of %d) for %s; open write transactions: %s",
                    attempt + 1, _LOCK_RETRY_ATTEMPTS + 1, _caller(), _describe_open_writes(),
                )
            if final:
                raise
            time.sleep(_LOCK_RETRY_BASE_DELAY * (2 ** attempt) + random.uniform(0, 0.2))


class _RetryingCursor(sqlite3.Cursor):
    def execute(self, sql, parameters=()):
        result = _retry_on_locked(super().execute, sql, parameters)
        self.connection._track_write(sql)
        return result

    def executemany(self, sql, seq_of_parameters):
        result = _retry_on_locked(super().executemany, sql, seq_of_parameters)
        self.connection._track_write(sql)
        return result


class _RetryingConnection(sqlite3.Connection):
    _write_entry = None
    _slot = None

    def cursor(self, factory=_RetryingCursor):
        return super().cursor(factory)

    def execute(self, sql, parameters=()):
        return self.cursor().execute(sql, parameters)

    def executemany(self, sql, seq_of_parameters):
        return self.cursor().executemany(sql, seq_of_parameters)

    def commit(self):
        result = _retry_on_locked(super().commit)
        self._release_write()
        return result

    def rollback(self):
        try:
            return super().rollback()
        finally:
            self._release_write()

    def close(self):
        self._release_write()
        super().close()
        if self._slot is not None:
            self._slot()

    def _track_write(self, sql):
        if self._write_entry is None and self.in_transaction:
            self._write_entry = {
                "conn": weakref.ref(self),
                "started": time.monotonic(),
                "caller": _caller(),
                "thread": threading.current_thread().name,
                "statement": " ".join(sql.split())[:80],
            }
            with _open_writes_lock:
                _open_writes[id(self)] = self._write_entry

    def _release_write(self):
        entry, self._write_entry = self._write_entry, None
        if entry is None:
            return
        with _open_writes_lock:
            _open_writes.pop(id(self), None)
        held = time.monotonic() - entry["started"]
        if held >= DB_WRITE_HOLD_WARN_SECONDS:
            logger.warning(
                "SQLite write transaction held %.1fs by %s [%s], first statement: %s",
                held, entry["caller"], entry["thread"], entry["statement"],
            )


_deferred: list = []
_deferred_lock = threading.Lock()
_replay = threading.local()
_deferred_wake = threading.Event()
_deferred_worker: Optional[threading.Thread] = None


def replaying_deferred_write() -> bool:
    """True while a queued write is being retried; a writer of a value that a newer write may have replaced since consults it to apply its newer-wins guard."""
    return getattr(_replay, "active", False)


def write_or_defer(description: str, write: Callable[[], None]) -> bool:
    """Runs a self-contained write (it opens and closes its own connection); a lock error that survives get_connection()'s retries queues the write for a later retry instead of losing it. Other errors propagate. Returns True when written now."""
    try:
        write()
        return True
    except sqlite3.OperationalError as e:
        if not is_lock_error(e):
            raise
        defer_write(description, write)
        return False


def defer_write(description: str, write: Callable[[], None]) -> None:
    now = time.monotonic()
    with _deferred_lock:
        full = len(_deferred) >= DB_DEFERRED_WRITE_QUEUE_MAX
        if not full:
            _deferred.append({
                "description": description, "write": write, "retries": 0,
                "queued": now, "due": now + DB_DEFERRED_WRITE_RETRY_SECONDS[0],
            })
    if full:
        logger.error("Deferred-write queue is full; dropped: %s", description)
        return
    logger.warning("Write deferred after 'database is locked': %s (retry in %ds)", description, DB_DEFERRED_WRITE_RETRY_SECONDS[0])
    _start_deferred_worker()


def _run_due_deferred(now: Optional[float] = None) -> Optional[float]:
    """Runs every deferred write that is due; returns the seconds until the next one is due, or None when the queue is empty."""
    now = time.monotonic() if now is None else now
    with _deferred_lock:
        due = [item for item in _deferred if item["due"] <= now]
        for item in due:
            _deferred.remove(item)
    for item in due:
        _replay.active = True
        try:
            item["write"]()
        except Exception as e:
            item["retries"] += 1
            if is_lock_error(e) and item["retries"] < len(DB_DEFERRED_WRITE_RETRY_SECONDS):
                delay = DB_DEFERRED_WRITE_RETRY_SECONDS[item["retries"]]
                item["due"] = time.monotonic() + delay
                with _deferred_lock:
                    _deferred.append(item)
                logger.warning("Deferred write still blocked: %s (retry %d, next in %ds)", item["description"], item["retries"], delay)
            else:
                logger.error("Deferred write abandoned after %d failed retries: %s: %s", item["retries"], item["description"], e)
        else:
            logger.warning(
                "Deferred write completed on retry %d, %.0fs after it was queued: %s",
                item["retries"] + 1, time.monotonic() - item["queued"], item["description"],
            )
        finally:
            _replay.active = False
    with _deferred_lock:
        if not _deferred:
            return None
        return max(0.0, min(item["due"] for item in _deferred) - time.monotonic())


def _deferred_worker_loop() -> None:
    while True:
        wait = _run_due_deferred()
        _deferred_wake.wait(wait)
        _deferred_wake.clear()


def _start_deferred_worker() -> None:
    global _deferred_worker
    with _deferred_lock:
        if _deferred_worker is None or not _deferred_worker.is_alive():
            _deferred_worker = threading.Thread(target=_deferred_worker_loop, name="deferred-db-writes", daemon=True)
            _deferred_worker.start()
    _deferred_wake.set()
