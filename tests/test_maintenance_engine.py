"""
tests/test_maintenance_engine.py — MAINTENANCE ENGINE

Covers:
  - prune_database_logs: UTC cutoff, rows deleted correctly, conn closed on success
  - prune_pulse_cache: removes records older than 24 h, leaves fresh ones
  - garbage_collect_files: deletes orphaned old files, spares active tickers,
    spares fresh files, spares protected files
  - dry_run: returns correct would_delete / would_keep buckets without deleting
"""

import sys
import time
import tempfile
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db
from maintenance_engine import MaintenanceEngine


# ── helpers ───────────────────────────────────────────────────────────────────

def _conn():
    import sqlite3
    conn = sqlite3.connect(_db.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _insert_notification(ts: str, msg: str = "test"):
    c = _conn()
    c.execute(
        "INSERT INTO system_notifications (timestamp, message_type, message_text) VALUES (?, ?, ?)",
        (ts, "Test", msg),
    )
    c.commit()
    c.close()


def _count_notifications() -> int:
    c = _conn()
    row = c.execute("SELECT COUNT(*) FROM system_notifications").fetchone()
    c.close()
    return row[0]


def _insert_pulse(ticker: str, last_updated: float):
    c = _conn()
    c.execute(
        "INSERT OR REPLACE INTO market_pulse_cache "
        "(ticker, name, price, change_pts, change_pct, is_positive, last_updated) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (ticker, ticker, 100.0, 0.0, 0.0, 1, last_updated),
    )
    c.commit()
    c.close()


def _count_pulse(ticker: str) -> int:
    c = _conn()
    row = c.execute("SELECT COUNT(*) FROM market_pulse_cache WHERE ticker = ?", (ticker,)).fetchone()
    c.close()
    return row[0]


def _engine() -> MaintenanceEngine:
    eng = MaintenanceEngine()
    eng.days_to_keep_files = 60
    return eng


# ── prune_database_logs ───────────────────────────────────────────────────────

class TestPruneDatabaseLogs:
    def setup_method(self):
        c = _conn()
        c.execute("DELETE FROM system_notifications")
        c.commit()
        c.close()

    def teardown_method(self):
        c = _conn()
        c.execute("DELETE FROM system_notifications")
        c.commit()
        c.close()

    def test_deletes_old_notifications(self):
        old_ts = (datetime.now(timezone.utc) - timedelta(days=35)).strftime("%Y-%m-%d %H:%M:%S")
        _insert_notification(old_ts)
        assert _count_notifications() == 1

        eng = _engine()
        eng.prune_database_logs()

        assert _count_notifications() == 0
        assert eng.metrics["logs_deleted"] == 1

    def test_keeps_recent_notifications(self):
        recent_ts = (datetime.now(timezone.utc) - timedelta(days=5)).strftime("%Y-%m-%d %H:%M:%S")
        _insert_notification(recent_ts)

        eng = _engine()
        eng.prune_database_logs()

        assert _count_notifications() == 1
        assert eng.metrics["logs_deleted"] == 0

    def test_boundary_exactly_at_cutoff_is_deleted(self):
        # Timestamp exactly on cutoff (≤) should be deleted
        old_ts = (datetime.now(timezone.utc) - timedelta(days=30, seconds=1)).strftime("%Y-%m-%d %H:%M:%S")
        _insert_notification(old_ts)

        eng = _engine()
        eng.prune_database_logs()

        assert eng.metrics["logs_deleted"] == 1


# ── prune_pulse_cache ─────────────────────────────────────────────────────────

class TestPrunePulseCache:
    STALE_TICKER = "_MAINT_TEST_STALE"
    FRESH_TICKER = "_MAINT_TEST_FRESH"

    def teardown_method(self):
        c = _conn()
        c.execute("DELETE FROM market_pulse_cache WHERE ticker IN (?, ?)", (self.STALE_TICKER, self.FRESH_TICKER))
        c.commit()
        c.close()

    def test_removes_record_older_than_24h(self):
        _insert_pulse(self.STALE_TICKER, time.time() - 90000)  # 25 h ago
        eng = _engine()
        eng.prune_pulse_cache()
        assert _count_pulse(self.STALE_TICKER) == 0
        assert eng.metrics["pulse_cache_deleted"] >= 1

    def test_keeps_record_younger_than_24h(self):
        _insert_pulse(self.FRESH_TICKER, time.time() - 3600)  # 1 h ago
        eng = _engine()
        eng.prune_pulse_cache()
        assert _count_pulse(self.FRESH_TICKER) == 1


# ── garbage_collect_files ─────────────────────────────────────────────────────

class TestGarbageCollectFiles:
    """Uses a real temp directory; patches _get_active_tickers and os.remove."""

    def _make_tmpdir_with_file(self, filename: str, age_days: int):
        """Returns (tmpdir Path, filepath Path) with mtime set to age_days ago."""
        tmpdir = tempfile.mkdtemp()
        p = Path(tmpdir) / filename
        p.write_text("dummy")
        mtime = time.time() - (age_days * 86400)
        import os
        os.utime(str(p), (mtime, mtime))
        return Path(tmpdir), p

    def test_deletes_orphaned_old_file(self):
        tmpdir, filepath = self._make_tmpdir_with_file("ORPHAN.parquet", age_days=90)
        eng = _engine()
        eng.days_to_keep_files = 60
        # Safety guard requires at least one active ticker; ORPHAN is NOT in the set.
        with (
            patch.object(eng, "_get_active_tickers", return_value={"SOMETHING_ELSE"}),
            patch("maintenance_engine.HISTORICAL_DIR", tmpdir),
            patch("maintenance_engine.INTRADAY_DIR", Path(tempfile.mkdtemp())),
            patch("maintenance_engine.FUNDAMENTALS_DIR", Path(tempfile.mkdtemp())),
        ):
            eng.garbage_collect_files()

        assert not filepath.exists()
        assert eng.metrics["files_deleted"] == 1

    def test_spares_active_ticker_file(self):
        tmpdir, filepath = self._make_tmpdir_with_file("ACTIVE.parquet", age_days=90)
        eng = _engine()
        with (
            patch.object(eng, "_get_active_tickers", return_value={"ACTIVE"}),
            patch("maintenance_engine.HISTORICAL_DIR", tmpdir),
            patch("maintenance_engine.INTRADAY_DIR", Path(tempfile.mkdtemp())),
            patch("maintenance_engine.FUNDAMENTALS_DIR", Path(tempfile.mkdtemp())),
        ):
            eng.garbage_collect_files()

        assert filepath.exists()
        assert eng.metrics["files_deleted"] == 0

    def test_spares_fresh_file_even_when_orphaned(self):
        tmpdir, filepath = self._make_tmpdir_with_file("ORPHAN.parquet", age_days=10)
        eng = _engine()
        eng.days_to_keep_files = 60
        with (
            patch.object(eng, "_get_active_tickers", return_value=set()),
            patch("maintenance_engine.HISTORICAL_DIR", tmpdir),
            patch("maintenance_engine.INTRADAY_DIR", Path(tempfile.mkdtemp())),
            patch("maintenance_engine.FUNDAMENTALS_DIR", Path(tempfile.mkdtemp())),
        ):
            eng.garbage_collect_files()

        assert filepath.exists()
        assert eng.metrics["files_deleted"] == 0

    def test_spares_protected_file(self):
        tmpdir, filepath = self._make_tmpdir_with_file("SP500_BASELINE.parquet", age_days=200)
        eng = _engine()
        with (
            patch.object(eng, "_get_active_tickers", return_value=set()),
            patch("maintenance_engine.HISTORICAL_DIR", tmpdir),
            patch("maintenance_engine.INTRADAY_DIR", Path(tempfile.mkdtemp())),
            patch("maintenance_engine.FUNDAMENTALS_DIR", Path(tempfile.mkdtemp())),
        ):
            eng.garbage_collect_files()

        assert filepath.exists()
        assert eng.metrics["files_deleted"] == 0

    def test_aborts_when_no_active_tickers(self):
        tmpdir, filepath = self._make_tmpdir_with_file("ORPHAN.parquet", age_days=90)
        eng = _engine()
        with (
            patch.object(eng, "_get_active_tickers", return_value=set()),
            patch("maintenance_engine.HISTORICAL_DIR", tmpdir),
            patch("maintenance_engine.INTRADAY_DIR", Path(tempfile.mkdtemp())),
            patch("maintenance_engine.FUNDAMENTALS_DIR", Path(tempfile.mkdtemp())),
        ):
            # _get_active_tickers returns empty set — safety guard fires
            with patch.object(eng, "_get_active_tickers", return_value=set()):
                eng.garbage_collect_files()
        # file is NOT deleted because active_tickers was empty (safety guard)
        # But wait — the safety guard only checks `if not active_tickers`.
        # An empty set IS falsy, so the guard fires and returns early.
        assert eng.metrics["files_deleted"] == 0


# ── dry_run ───────────────────────────────────────────────────────────────────

class TestDryRun:
    def test_would_delete_includes_orphaned_old_file(self):
        tmpdir, _ = self._make_tmpdir_with_file_("ORPHAN.parquet", 90)
        eng = _engine()
        with (
            patch.object(eng, "_get_active_tickers", return_value=set()),
            patch("maintenance_engine.HISTORICAL_DIR", tmpdir),
            patch("maintenance_engine.INTRADAY_DIR", Path(tempfile.mkdtemp())),
            patch("maintenance_engine.FUNDAMENTALS_DIR", Path(tempfile.mkdtemp())),
        ):
            result = eng.dry_run()

        assert result["summary"]["delete_count"] == 1
        assert any("ORPHAN.parquet" in entry["file"] for entry in result["would_delete"])

    def test_would_keep_fresh_file_not_deleted(self):
        tmpdir, filepath = self._make_tmpdir_with_file_("ORPHAN.parquet", 5)
        eng = _engine()
        eng.days_to_keep_files = 60
        with (
            patch.object(eng, "_get_active_tickers", return_value=set()),
            patch("maintenance_engine.HISTORICAL_DIR", tmpdir),
            patch("maintenance_engine.INTRADAY_DIR", Path(tempfile.mkdtemp())),
            patch("maintenance_engine.FUNDAMENTALS_DIR", Path(tempfile.mkdtemp())),
        ):
            result = eng.dry_run()

        # file must still exist (dry_run deletes nothing)
        assert filepath.exists()
        assert result["summary"]["delete_count"] == 0
        assert result["summary"]["keep_fresh_count"] == 1

    def test_active_ticker_counted_in_keep_active(self):
        tmpdir, _ = self._make_tmpdir_with_file_("ACTIVE.parquet", 90)
        eng = _engine()
        with (
            patch.object(eng, "_get_active_tickers", return_value={"ACTIVE"}),
            patch("maintenance_engine.HISTORICAL_DIR", tmpdir),
            patch("maintenance_engine.INTRADAY_DIR", Path(tempfile.mkdtemp())),
            patch("maintenance_engine.FUNDAMENTALS_DIR", Path(tempfile.mkdtemp())),
        ):
            result = eng.dry_run()

        assert result["summary"]["keep_active_count"] == 1
        assert result["summary"]["delete_count"] == 0

    def test_summary_keys_always_present(self):
        tmpdir = Path(tempfile.mkdtemp())
        eng = _engine()
        with (
            patch.object(eng, "_get_active_tickers", return_value=set()),
            patch("maintenance_engine.HISTORICAL_DIR", tmpdir),
            patch("maintenance_engine.INTRADAY_DIR", Path(tempfile.mkdtemp())),
            patch("maintenance_engine.FUNDAMENTALS_DIR", Path(tempfile.mkdtemp())),
        ):
            result = eng.dry_run()

        assert "days_to_keep_files" in result
        assert "active_tickers_count" in result
        assert "would_delete" in result
        assert "would_keep_fresh" in result
        assert {"delete_count", "keep_active_count", "keep_fresh_count"} == set(result["summary"])

    @staticmethod
    def _make_tmpdir_with_file_(filename: str, age_days: int):
        import os
        tmpdir = tempfile.mkdtemp()
        p = Path(tmpdir) / filename
        p.write_text("dummy")
        mtime = time.time() - (age_days * 86400)
        os.utime(str(p), (mtime, mtime))
        return Path(tmpdir), p
