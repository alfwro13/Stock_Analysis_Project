"""
tests/test_db_lock_handling.py — "database is locked" handling in db/connection.py.

Covers the open-write registry that names the holder of the write lock in the log, the
deferred-write queue that retries a write which lost the lock, and the writers that use it
(in-app notifications, FX quotes, Workflow Monitor run records, Pattern Detection results).
The queue's worker thread is replaced by direct calls to _run_due_deferred() so no thread
outlives a test.
"""

import gc
import logging
import sqlite3
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from db import database as _db
from db import connection as _conn
from db import helpers as db_helpers
import notification_engine
import scheduler_engine
from pattern_detection_engine import PatternDetectionEngine

_LOCKED = sqlite3.OperationalError("database is locked")
_FOREVER = float("inf")


@pytest.fixture
def deferred_queue(monkeypatch):
    monkeypatch.setattr(_conn, "_start_deferred_worker", lambda: None)
    monkeypatch.setattr(_conn, "_deferred", [])
    return _conn._deferred


def _raise_once_then(real):
    state = {"calls": 0}

    def wrapper(*args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            raise _LOCKED
        return real(*args, **kwargs)

    return wrapper


def _tracked_connection(timeout=0.05):
    return sqlite3.connect(str(_db.DB_PATH), timeout=timeout, factory=_conn._RetryingConnection)


def _insert_score_row(conn, ticker):
    conn.execute("INSERT INTO score_history (ticker, date, score, signal) VALUES (?, '2099-01-02', 1, 'BUY')", (ticker,))


def _delete_score_rows(*tickers):
    conn = _db.get_connection()
    try:
        conn.executemany("DELETE FROM score_history WHERE ticker = ?", [(t,) for t in tickers])
        conn.commit()
    finally:
        conn.close()


@pytest.mark.db
class TestOpenWriteRegistry:
    @pytest.mark.parametrize("module_name", ["db.connection", "db.database"])
    def test_caller_skips_frames_of_the_connection_modules(self, module_name):
        namespace = {"__name__": module_name, "_caller": _conn._caller}
        exec("def inner():\n    return _caller()\n", namespace)
        assert namespace["inner"]().startswith("test_db_lock_handling.py:test_caller_skips_frames_of_the_connection_modules:")

    def test_long_write_transaction_is_logged_with_its_caller(self, monkeypatch, caplog):
        monkeypatch.setattr(_conn, "DB_WRITE_HOLD_WARN_SECONDS", 0)
        conn = _tracked_connection()
        try:
            _insert_score_row(conn, "TST_HOLD1")
            assert id(conn) in _conn._open_writes
            with caplog.at_level(logging.WARNING, logger="db.connection"):
                conn.commit()
        finally:
            conn.close()
            _delete_score_rows("TST_HOLD1")
        message = next(r.getMessage() for r in caplog.records if "write transaction held" in r.getMessage())
        assert "test_db_lock_handling.py:_insert_score_row" in message
        assert "INSERT INTO score_history" in message

    def test_short_write_transaction_is_not_logged(self, caplog):
        conn = _tracked_connection()
        try:
            _insert_score_row(conn, "TST_HOLD2")
            with caplog.at_level(logging.WARNING, logger="db.connection"):
                conn.commit()
        finally:
            conn.close()
            _delete_score_rows("TST_HOLD2")
        assert not [r for r in caplog.records if "write transaction held" in r.getMessage()]

    @pytest.mark.parametrize("release", ["commit", "rollback", "close"])
    def test_every_way_a_transaction_ends_clears_the_registry(self, release):
        conn = _tracked_connection()
        _insert_score_row(conn, "TST_HOLD3")
        assert id(conn) in _conn._open_writes
        getattr(conn, release)()
        try:
            assert id(conn) not in _conn._open_writes
        finally:
            if release != "close":
                conn.close()
            _delete_score_rows("TST_HOLD3")

    def test_read_only_connection_is_not_registered(self):
        conn = _tracked_connection()
        try:
            conn.execute("SELECT 1 FROM score_history LIMIT 1").fetchall()
            assert id(conn) not in _conn._open_writes
        finally:
            conn.close()

    def test_lock_error_names_the_holder(self, monkeypatch, caplog):
        monkeypatch.setattr(_conn, "_LOCK_RETRY_BASE_DELAY", 0.01)
        holder = _tracked_connection()
        waiter = _tracked_connection(timeout=0.05)
        try:
            _insert_score_row(holder, "TST_HOLD4")
            with caplog.at_level(logging.WARNING, logger="db.connection"):
                with pytest.raises(sqlite3.OperationalError, match="database is locked"):
                    _insert_score_row(waiter, "TST_HOLD5")
        finally:
            holder.rollback()
            holder.close()
            waiter.close()
        messages = [r.getMessage() for r in caplog.records if "write lock not obtained" in r.getMessage()]
        assert len(messages) == 2
        assert all("held" in m and "_insert_score_row" in m and "INSERT INTO score_history" in m for m in messages)
        assert "wait 1 of 4" in messages[0] and "wait 4 of 4" in messages[1]

    def test_lock_error_says_so_when_no_tracked_holder_exists(self, monkeypatch, caplog):
        monkeypatch.setattr(_conn, "_LOCK_RETRY_BASE_DELAY", 0.01)
        outsider = sqlite3.connect(str(_db.DB_PATH), timeout=0.1)
        waiter = _tracked_connection(timeout=0.05)
        try:
            outsider.execute("BEGIN IMMEDIATE")
            with caplog.at_level(logging.WARNING, logger="db.connection"):
                with pytest.raises(sqlite3.OperationalError):
                    _insert_score_row(waiter, "TST_HOLD6")
        finally:
            outsider.rollback()
            outsider.close()
            waiter.close()
        assert any("none tracked" in r.getMessage() for r in caplog.records)

    def test_abandoned_connection_is_not_reported_as_a_holder(self):
        conn = _tracked_connection()
        _insert_score_row(conn, "TST_HOLD7")
        key = id(conn)
        assert key in _conn._open_writes
        del conn
        gc.collect()
        assert "TST_HOLD7" not in _conn._describe_open_writes()
        assert key not in _conn._open_writes
        _delete_score_rows("TST_HOLD7")


class TestDeferredWriteQueue:
    def test_write_that_succeeds_is_not_queued(self, deferred_queue):
        calls = []
        assert _conn.write_or_defer("probe", lambda: calls.append(1)) is True
        assert calls == [1]
        assert deferred_queue == []

    def test_lock_error_queues_the_write_and_a_later_run_completes_it(self, deferred_queue, caplog):
        calls = []

        def write():
            calls.append(1)
            if len(calls) == 1:
                raise _LOCKED

        with caplog.at_level(logging.WARNING, logger="db.connection"):
            assert _conn.write_or_defer("probe write", write) is False
            assert len(deferred_queue) == 1
            assert _conn._run_due_deferred(now=_FOREVER) is None
        assert calls == [1, 1]
        assert deferred_queue == []
        texts = [r.getMessage() for r in caplog.records]
        assert any("Write deferred" in t and "probe write" in t for t in texts)
        assert any("Deferred write completed on retry 1" in t and "probe write" in t for t in texts)

    def test_replay_flag_is_set_only_while_a_queued_write_runs(self, deferred_queue):
        seen = []

        def write():
            seen.append(_conn.replaying_deferred_write())
            if len(seen) == 1:
                raise _LOCKED

        assert _conn.replaying_deferred_write() is False
        _conn.write_or_defer("probe", write)
        _conn._run_due_deferred(now=_FOREVER)
        assert seen == [False, True]
        assert _conn.replaying_deferred_write() is False

    def test_other_errors_propagate_and_are_never_queued(self, deferred_queue):
        def write():
            raise sqlite3.OperationalError("no such table: missing")

        with pytest.raises(sqlite3.OperationalError, match="no such table"):
            _conn.write_or_defer("probe", write)
        assert deferred_queue == []

    def test_write_that_stays_locked_is_retried_then_abandoned(self, deferred_queue, caplog):
        calls = []

        def write():
            calls.append(1)
            raise _LOCKED

        _conn.write_or_defer("stuck write", write)
        with caplog.at_level(logging.WARNING, logger="db.connection"):
            for _ in range(len(_conn.DB_DEFERRED_WRITE_RETRY_SECONDS)):
                _conn._run_due_deferred(now=_FOREVER)
        assert len(calls) == 1 + len(_conn.DB_DEFERRED_WRITE_RETRY_SECONDS)
        assert deferred_queue == []
        errors = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(errors) == 1 and "abandoned" in errors[0].getMessage() and "stuck write" in errors[0].getMessage()
        assert sum("still blocked" in r.getMessage() for r in caplog.records) == len(_conn.DB_DEFERRED_WRITE_RETRY_SECONDS) - 1

    def test_non_lock_failure_on_retry_abandons_at_once(self, deferred_queue, caplog):
        calls = []

        def write():
            calls.append(1)
            raise _LOCKED if len(calls) == 1 else RuntimeError("boom")

        _conn.write_or_defer("flaky write", write)
        with caplog.at_level(logging.ERROR, logger="db.connection"):
            _conn._run_due_deferred(now=_FOREVER)
        assert deferred_queue == []
        assert any("abandoned after 1 failed retries" in r.getMessage() and "boom" in r.getMessage() for r in caplog.records)

    def test_a_write_is_not_retried_before_it_is_due(self, deferred_queue):
        calls = []
        _conn.defer_write("later", lambda: calls.append(1))
        wait = _conn._run_due_deferred()
        assert calls == []
        assert 0 < wait <= _conn.DB_DEFERRED_WRITE_RETRY_SECONDS[0]

    def test_full_queue_drops_the_new_write_and_logs_it(self, deferred_queue, monkeypatch, caplog):
        monkeypatch.setattr(_conn, "DB_DEFERRED_WRITE_QUEUE_MAX", 2)
        with caplog.at_level(logging.ERROR, logger="db.connection"):
            for index in range(3):
                _conn.defer_write(f"write {index}", lambda: None)
        assert [item["description"] for item in deferred_queue] == ["write 0", "write 1"]
        assert any("queue is full" in r.getMessage() and "write 2" in r.getMessage() for r in caplog.records)


@pytest.mark.db
class TestInAppNotifications:
    def _rows(self, text):
        conn = _db.get_connection()
        try:
            return conn.execute("SELECT timestamp, message_type, is_read FROM system_notifications WHERE message_text = ?", (text,)).fetchall()
        finally:
            conn.close()

    def _cleanup(self, text):
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM system_notifications WHERE message_text = ?", (text,))
            conn.commit()
        finally:
            conn.close()

    def test_log_notification_is_retried_with_its_original_time(self, deferred_queue):
        text = "LR07 deferred standalone notification"
        try:
            with patch.object(_db, "insert_notification", _raise_once_then(_db.insert_notification)):
                _db.log_notification("Success", text)
                assert self._rows(text) == []
                with patch.object(_db, "datetime") as clock:
                    clock.now.side_effect = AssertionError("the original time must be reused")
                    _conn._run_due_deferred(now=_FOREVER)
            rows = self._rows(text)
            assert len(rows) == 1 and rows[0]["message_type"] == "Success" and rows[0]["is_read"] == 1
            assert len(rows[0]["timestamp"]) == 19
        finally:
            self._cleanup(text)

    def test_notify_with_a_caller_connection_defers_a_locked_insert(self, deferred_queue, monkeypatch):
        text = "LR07 deferred connection notification"

        class LockedConnection:
            committed = False

            def execute(self, *args):
                raise _LOCKED

            def commit(self):
                self.committed = True

        monkeypatch.setattr(notification_engine, "effective_routing", lambda source, config: {"log_file": False, "in_app": True, "nextcloud_talk": False})
        connection = LockedConnection()
        try:
            notification_engine._route("scheduler_status", "Error", text, None, "error", connection, {})
            assert len(deferred_queue) == 1 and not connection.committed
            _conn._run_due_deferred(now=_FOREVER)
            assert [row["message_type"] for row in self._rows(text)] == ["Error"]
        finally:
            self._cleanup(text)

    def test_notify_does_not_defer_when_only_the_commit_failed(self, deferred_queue, monkeypatch, caplog):
        class CommitFails:
            def execute(self, *args):
                return None

            def commit(self):
                raise _LOCKED

        monkeypatch.setattr(notification_engine, "effective_routing", lambda source, config: {"log_file": False, "in_app": True, "nextcloud_talk": False})
        with caplog.at_level(logging.ERROR, logger="notification_engine"):
            notification_engine._route("scheduler_status", "Error", "LR07 commit failure", None, "error", CommitFails(), {})
        assert deferred_queue == []
        assert any("in-app write failed" in r.getMessage() for r in caplog.records)


@pytest.mark.db
class TestFxQuoteWrites:
    PAIR = "ZZLOCKFX=X"

    def _cleanup(self):
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM market_pulse_cache WHERE ticker = ?", (self.PAIR,))
            conn.commit()
        finally:
            conn.close()

    def test_retried_quote_never_replaces_a_newer_one(self, deferred_queue):
        try:
            db_helpers.upsert_fx_quote(self.PAIR, 0.9, 2000.0)
            with patch.object(db_helpers, "get_connection", _raise_once_then(_db.get_connection)):
                db_helpers.upsert_fx_quote(self.PAIR, 0.5, 1000.0)
            _conn._run_due_deferred(now=_FOREVER)
            assert db_helpers.get_cached_fx_quote(self.PAIR) == {"rate": 0.9, "updated_at": 2000.0}
            with patch.object(db_helpers, "get_connection", _raise_once_then(_db.get_connection)):
                db_helpers.upsert_fx_quote(self.PAIR, 0.95, 3000.0)
            _conn._run_due_deferred(now=_FOREVER)
            assert db_helpers.get_cached_fx_quote(self.PAIR) == {"rate": 0.95, "updated_at": 3000.0}
        finally:
            self._cleanup()

    def test_locked_quote_is_written_by_the_retry(self, deferred_queue):
        try:
            with patch.object(db_helpers, "get_connection", _raise_once_then(_db.get_connection)):
                db_helpers.upsert_fx_quote(self.PAIR, 0.8, 4000.0)
            assert db_helpers.get_cached_fx_quote(self.PAIR) is None
            _conn._run_due_deferred(now=_FOREVER)
            assert db_helpers.get_cached_fx_quote(self.PAIR) == {"rate": 0.8, "updated_at": 4000.0}
        finally:
            self._cleanup()

    def test_caller_connection_is_used_without_commit_or_deferral(self, deferred_queue):
        conn = _db.get_connection()
        try:
            db_helpers.upsert_fx_quote(self.PAIR, 0.7, 5000.0, conn=conn)
            conn.rollback()
            assert db_helpers.get_cached_fx_quote(self.PAIR) is None
        finally:
            conn.close()
            self._cleanup()
        assert deferred_queue == []


@pytest.mark.db
class TestSchedulerRunRecords:
    JOB = "lr07_test_job"

    def _log_row(self):
        conn = _db.get_connection()
        try:
            return conn.execute("SELECT last_run, last_status FROM scheduler_run_log WHERE job_id = ?", (self.JOB,)).fetchone()
        finally:
            conn.close()

    def _history_count(self):
        conn = _db.get_connection()
        try:
            return conn.execute("SELECT COUNT(*) FROM scheduler_run_history WHERE job_id = ?", (self.JOB,)).fetchone()[0]
        finally:
            conn.close()

    def _cleanup(self):
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM scheduler_run_log WHERE job_id = ?", (self.JOB,))
            conn.execute("DELETE FROM scheduler_run_history WHERE job_id = ?", (self.JOB,))
            conn.commit()
        finally:
            conn.close()

    def test_retried_record_never_replaces_a_newer_run_time(self, deferred_queue):
        try:
            scheduler_engine._record_job_duration(self.JOB, "2030-01-01 10:00:00", "2030-01-01 10:00:05", 5.0, "success", "scheduled")
            with patch.object(scheduler_engine, "get_connection", _raise_once_then(_db.get_connection)):
                scheduler_engine._record_job_duration(self.JOB, "2029-01-01 10:00:00", "2029-01-01 10:00:05", 5.0, "error", "scheduled")
            _conn._run_due_deferred(now=_FOREVER)
            row = self._log_row()
            assert row["last_run"] == "2030-01-01 10:00:05" and row["last_status"] == "success"
            assert self._history_count() == 2
        finally:
            self._cleanup()

    def test_locked_duration_record_is_written_by_the_retry(self, deferred_queue):
        try:
            with patch.object(scheduler_engine, "get_connection", _raise_once_then(_db.get_connection)):
                scheduler_engine._record_job_duration(self.JOB, "2031-01-01 10:00:00", "2031-01-01 10:00:07", 7.0, "success", "manual")
            assert self._log_row() is None
            _conn._run_due_deferred(now=_FOREVER)
            assert self._log_row()["last_run"] == "2031-01-01 10:00:07"
            assert self._history_count() == 1
        finally:
            self._cleanup()

    def test_locked_run_time_is_written_by_the_retry_with_its_original_time(self, deferred_queue):
        try:
            with patch.object(scheduler_engine, "get_connection", _raise_once_then(_db.get_connection)):
                scheduler_engine.record_job_run(self.JOB)
            assert self._log_row() is None
            with patch.object(scheduler_engine, "datetime") as clock:
                clock.now.side_effect = AssertionError("the original time must be reused")
                _conn._run_due_deferred(now=_FOREVER)
            assert self._log_row() is not None
        finally:
            self._cleanup()


@pytest.mark.db
class TestPatternDetectionSave:
    TICKER = "PDLOCK1"

    @staticmethod
    def _row(scan_ts="2026-02-10 22:20:00", phase="CONFIRMED"):
        return {
            "ticker": TestPatternDetectionSave.TICKER, "pattern_family": "head_shoulders", "pattern_type": "regular", "phase": phase,
            "points": [{"label": "Head", "date": "2026-01-20", "price": 120.0}],
            "lines": [], "breakout_date": None, "breakout_price": None, "measured_target": 74.0,
            "volume_confirms": True, "rsi_divergence": False, "pattern_r2": 0.85, "prior_trend_pct": 12.0,
            "close_price": 90.0, "scan_ts": scan_ts,
        }

    def _saved(self):
        conn = _db.get_connection()
        try:
            return conn.execute("SELECT phase, scan_ts FROM pattern_detection_results WHERE ticker = ?", (self.TICKER,)).fetchone()
        finally:
            conn.close()

    def _cleanup(self):
        conn = _db.get_connection()
        try:
            conn.execute("DELETE FROM pattern_detection_results WHERE ticker = ?", (self.TICKER,))
            conn.execute("DELETE FROM pattern_detection_history WHERE ticker = ?", (self.TICKER,))
            conn.commit()
        finally:
            conn.close()

    def test_locked_save_is_written_by_the_retry(self, deferred_queue):
        engine = PatternDetectionEngine({})
        try:
            with patch.object(PatternDetectionEngine, "_write_results", _raise_once_then(PatternDetectionEngine._write_results)):
                engine._save_results([self._row()])
                assert self._saved() is None
                _conn._run_due_deferred(now=_FOREVER)
            assert self._saved()["phase"] == "CONFIRMED"
        finally:
            self._cleanup()

    def test_retried_save_does_not_replace_a_newer_scan(self, monkeypatch):
        monkeypatch.setattr("pattern_detection_engine.replaying_deferred_write", lambda: True)
        engine = PatternDetectionEngine({})
        try:
            engine._save_results([self._row(scan_ts="2030-01-01 22:20:00", phase="CONFIRMED")])
            engine._write_results([self._row(scan_ts="2029-01-01 22:20:00", phase="FORMING")], [], "2029-01-01 22:21:00")
            assert self._saved()["phase"] == "CONFIRMED"
        finally:
            self._cleanup()

    def test_retried_stale_clear_does_not_delete_a_newer_row(self, monkeypatch):
        monkeypatch.setattr("pattern_detection_engine.replaying_deferred_write", lambda: True)
        engine = PatternDetectionEngine({})
        try:
            engine._save_results([self._row(scan_ts="2030-01-01 22:20:00")])
            engine._write_results([], [(self.TICKER, "head_shoulders")], "2029-01-01 22:21:00")
            assert self._saved() is not None
            engine._write_results([], [(self.TICKER, "head_shoulders")], "2031-01-01 00:00:00")
            assert self._saved() is None
        finally:
            self._cleanup()

    def test_direct_save_is_unconditional(self):
        engine = PatternDetectionEngine({})
        try:
            engine._save_results([self._row(scan_ts="2030-01-01 22:20:00", phase="CONFIRMED")])
            engine._save_results([self._row(scan_ts="2029-01-01 22:20:00", phase="FORMING")])
            assert self._saved()["phase"] == "FORMING"
        finally:
            self._cleanup()


class TestSwapGate:
    """database_swap_gate() holds new connections and waits for open ones, so a restore never has a connection spanning the file swap."""

    @pytest.fixture(autouse=True)
    def _settled(self):
        gc.collect()
        before = _conn._live_connections
        yield
        gc.collect()
        assert _conn._live_connections == before

    def test_a_closed_connection_releases_its_slot(self):
        before = _conn._live_connections
        conn = _db.get_connection()
        assert _conn._live_connections == before + 1
        conn.close()
        assert _conn._live_connections == before
        gc.collect()
        assert _conn._live_connections == before

    def test_an_unclosed_connection_releases_its_slot_when_collected(self):
        before = _conn._live_connections
        conn = _db.get_connection()
        del conn
        gc.collect()
        assert _conn._live_connections == before

    def test_a_failed_connect_does_not_leak_a_slot(self):
        before = _conn._live_connections
        with patch.object(_db.sqlite3, "connect", side_effect=sqlite3.OperationalError("unable to open")):
            with pytest.raises(sqlite3.OperationalError):
                _db.get_connection()
        assert _conn._live_connections == before

    def test_the_gate_waits_for_an_open_connection_to_close(self):
        import threading
        import time

        conn = _db.get_connection()
        entered = threading.Event()

        def _swap():
            with _conn.database_swap_gate(drain_timeout=5):
                entered.set()

        thread = threading.Thread(target=_swap)
        thread.start()
        time.sleep(0.2)
        assert not entered.is_set()
        conn.close()
        thread.join(5)
        assert entered.is_set()

    def test_the_gate_refuses_and_reopens_when_a_connection_stays_open(self):
        conn = _db.get_connection()
        try:
            with pytest.raises(_conn.DatabaseInUse):
                with _conn.database_swap_gate(drain_timeout=0.1):
                    pytest.fail("the gate must not open while a connection is live")
            assert not _conn._swap_gate_closed
        finally:
            conn.close()

    def test_a_new_connection_waits_for_the_gate_and_proceeds_after(self):
        import threading
        import time

        opened = []

        def _connect():
            conn = _db.get_connection()
            opened.append(time.monotonic())
            conn.close()

        with _conn.database_swap_gate(drain_timeout=5):
            thread = threading.Thread(target=_connect)
            thread.start()
            time.sleep(0.2)
            assert opened == []
            released = time.monotonic()
        thread.join(5)
        assert len(opened) == 1 and opened[0] >= released

    def test_a_connection_gives_up_with_a_clear_error_when_the_gate_stays_closed(self, monkeypatch):
        monkeypatch.setattr(_conn, "DB_SWAP_GATE_WAIT_SECONDS", 0.1)
        with _conn.database_swap_gate(drain_timeout=5):
            with pytest.raises(_conn.DatabaseSwapInProgress, match="being replaced by a backup restore"):
                _db.get_connection()

    def test_the_gate_reopens_when_the_body_raises(self):
        with pytest.raises(KeyError):
            with _conn.database_swap_gate(drain_timeout=5):
                raise KeyError("boom")
        assert not _conn._swap_gate_closed
        _db.get_connection().close()
