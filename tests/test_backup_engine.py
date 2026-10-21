import hashlib
import io
import sqlite3
import tarfile
from unittest.mock import patch

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.schedulers.base import STATE_PAUSED, STATE_RUNNING

import backup_engine
import scheduler_engine
from database import get_connection


def _cfg(**overrides):
    cfg = {
        "LOCATION": "local",
        "LOCAL_PATH": "backups",
        "NFS_SERVER": "",
        "NFS_PATH": "",
        "INCLUDE_DATA": True,
        "INCLUDE_MODELS": True,
        "INCLUDE_DATABASE": True,
        "RETENTION_COUNT": 7,
    }
    cfg.update(overrides)
    return {"SCHEDULING": {"BACKUP": cfg}}


def _make_db(path, with_sha256=True):
    sha_col = ", sha256 TEXT" if with_sha256 else ""
    conn = sqlite3.connect(path)
    try:
        conn.execute(
            "CREATE TABLE backup_history (id INTEGER PRIMARY KEY AUTOINCREMENT, started_at TEXT NOT NULL, "
            "finished_at TEXT, trigger_type TEXT NOT NULL, location_type TEXT NOT NULL, destination TEXT, "
            f"components TEXT, filename TEXT, size_bytes INTEGER, status TEXT NOT NULL, error_message TEXT{sha_col})"
        )
        conn.execute("CREATE TABLE marker (v TEXT)")
        conn.execute("INSERT INTO marker VALUES ('original')")
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def app_tree(tmp_path, monkeypatch):
    base = tmp_path / "app"
    data_dir = base / "data"
    models_dir = base / "models"
    (data_dir / "historical").mkdir(parents=True)
    models_dir.mkdir()
    (data_dir / "historical" / "AAPL.parquet").write_text("fake-parquet")
    (models_dir / "ml_ensemble.joblib").write_text("fake-model")
    _make_db(data_dir / "analysis.db")

    monkeypatch.setattr(backup_engine, "BASE_DIR", base)
    monkeypatch.setattr(backup_engine, "DATA_DIR", data_dir)
    monkeypatch.setattr(backup_engine, "MODELS_DIR", models_dir)
    monkeypatch.setattr(backup_engine, "DB_PATH", data_dir / "analysis.db")
    monkeypatch.setattr(backup_engine, "_NFS_MOUNT_POINT", base / ".nfs_backup_mount")
    return base


@pytest.fixture
def live_wal_db(app_tree):
    """Keeps a WAL-mode writer open so a committed row lives only in analysis.db-wal, as on a running app."""
    conn = sqlite3.connect(app_tree / "data" / "analysis.db")
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA wal_autocheckpoint=0")
        conn.execute("INSERT INTO marker VALUES ('only-in-wal')")
        conn.commit()
        yield conn
    finally:
        conn.close()


@pytest.fixture(autouse=True)
def _clean_backup_history():
    conn = get_connection()
    try:
        high_water = conn.execute("SELECT COALESCE(MAX(id), 0) FROM backup_history").fetchone()[0]
    finally:
        conn.close()
    yield
    conn = None
    try:
        conn = get_connection()
        conn.execute("DELETE FROM backup_history WHERE id > ?", (high_water,))
        conn.commit()
    finally:
        if conn:
            conn.close()


def _insert_history(filename, sha256, status="success", trigger="manual"):
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO backup_history (started_at, trigger_type, location_type, filename, status, sha256) "
            "VALUES ('2026-01-01 00:00:00', ?, 'local', ?, ?, ?)",
            (trigger, filename, status, sha256),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def _history_hash(filename):
    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT sha256 FROM backup_history WHERE filename = ? ORDER BY id DESC LIMIT 1", (filename,)
        ).fetchone()
        return row["sha256"] if row else None
    finally:
        if conn:
            conn.close()


def _sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _craft_archive(app_tree, name, members, register_hash=True):
    """members: iterable of (arcname, TarInfo-type, payload-or-linkname)."""
    backups = app_tree / "backups"
    backups.mkdir(exist_ok=True)
    path = backups / name
    with tarfile.open(path, "w:gz") as tar:
        for arcname, kind, payload in members:
            info = tarfile.TarInfo(arcname)
            if kind == "file":
                data = payload if isinstance(payload, bytes) else payload.encode()
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
            elif kind == "dir":
                info.type = tarfile.DIRTYPE
                info.mode = 0o755
                tar.addfile(info)
            elif kind == "symlink":
                info.type = tarfile.SYMTYPE
                info.linkname = payload
                tar.addfile(info)
            elif kind == "hardlink":
                info.type = tarfile.LNKTYPE
                info.linkname = payload
                tar.addfile(info)
    if register_hash:
        _insert_history(name, _sha(path))
    return path


def _db_bytes(tmp_path, with_sha256=True, marker="from-archive"):
    path = tmp_path / "crafted.db"
    path.unlink(missing_ok=True)
    _make_db(path, with_sha256=with_sha256)
    conn = sqlite3.connect(path)
    try:
        conn.execute("UPDATE marker SET v = ?", (marker,))
        conn.commit()
    finally:
        conn.close()
    return path.read_bytes()


def _live_marker(app_tree):
    conn = sqlite3.connect(app_tree / "data" / "analysis.db")
    try:
        return conn.execute("SELECT v FROM marker").fetchone()[0]
    finally:
        conn.close()


def test_nfs_mount_point_is_not_nested_under_data_dir():
    """A mountpoint under DATA_DIR would let the Data component's tar walk re-archive the live NFS mount, and everything already stored on it."""
    assert backup_engine.DATA_DIR not in backup_engine._NFS_MOUNT_POINT.parents


class TestSettingsFieldValidation:
    def test_local_path_inside_app_dir_is_accepted(self, app_tree):
        assert backup_engine.check_local_backup_path("backups") == (app_tree / "backups").resolve()
        assert backup_engine.check_local_backup_path("") == (app_tree / "backups").resolve()
        assert backup_engine.check_local_backup_path(str(app_tree / "more" / "backups")).is_relative_to(app_tree.resolve())

    @pytest.mark.parametrize("raw", ["/etc", "../outside", "backups/../../outside", "data", "data/sub", "models"])
    def test_local_path_outside_app_dir_or_inside_archived_dirs_is_refused(self, app_tree, raw):
        with pytest.raises(ValueError):
            backup_engine.check_local_backup_path(raw)

    def test_local_path_symlink_leaving_app_dir_is_refused(self, app_tree, tmp_path):
        outside = tmp_path / "elsewhere"
        outside.mkdir()
        (app_tree / "link").symlink_to(outside)
        with pytest.raises(ValueError):
            backup_engine.check_local_backup_path("link")

    @pytest.mark.parametrize("raw", ["-oevil", "host name", "host;rm", "a:b", "/x"])
    def test_nfs_server_rejects_non_hostnames(self, raw):
        with pytest.raises(ValueError):
            backup_engine.check_nfs_server(raw)

    @pytest.mark.parametrize("raw", ["192.168.1.200", "nas.local", "nas-1"])
    def test_nfs_server_accepts_host_or_ip(self, raw):
        assert backup_engine.check_nfs_server(f" {raw} ") == raw

    @pytest.mark.parametrize("raw", ["relative/path", "/a/../b", "/a b", "/a;b", "/a\nb"])
    def test_nfs_path_rejects_unsafe_values(self, raw):
        with pytest.raises(ValueError):
            backup_engine.check_nfs_path(raw)

    def test_nfs_path_accepts_absolute_export(self):
        assert backup_engine.check_nfs_path("/volume1/Backups/Stock_Analysis_Project") == "/volume1/Backups/Stock_Analysis_Project"
        assert backup_engine.check_nfs_path("") == ""

    def test_settings_model_rejects_bad_backup_fields(self):
        from pydantic import ValidationError

        from api_routes_system import SettingsConfig

        for field, value in (
            ("LOCAL_PATH", "/etc"), ("NFS_SERVER", "-oevil"), ("NFS_PATH", "/a/../b"),
            ("LOCATION", "sftp"), ("RETENTION_COUNT", 0), ("RETENTION_COUNT", 366),
        ):
            with pytest.raises(ValidationError):
                SettingsConfig(SCHEDULING={"BACKUP": {field: value}})

        ok = SettingsConfig(SCHEDULING={"BACKUP": {"LOCATION": "nfs", "NFS_SERVER": "192.168.1.200",
                                                    "NFS_PATH": "/volume1/Backups/x", "LOCAL_PATH": "backups",
                                                    "RETENTION_COUNT": 14}})
        assert ok.SCHEDULING.BACKUP.NFS_SERVER == "192.168.1.200"

    @pytest.mark.api
    def test_post_settings_rejects_backup_path_outside_app_dir(self, client, step_up_headers):
        resp = client.post(
            "/api/settings",
            json={"SCHEDULING": {"BACKUP": {"LOCAL_PATH": "/etc"}}},
            headers=step_up_headers,
        )
        assert resp.status_code == 422

    def test_resolve_backup_dir_revalidates_hand_edited_config(self, app_tree):
        with pytest.raises(ValueError):
            backup_engine._resolve_backup_dir({"LOCATION": "local", "LOCAL_PATH": "/etc"})
        with pytest.raises(ValueError):
            backup_engine._resolve_backup_dir({"LOCATION": "nfs", "NFS_SERVER": "-oevil", "NFS_PATH": "/x"})


@pytest.mark.db
class TestRunBackup:
    def test_creates_archive_with_all_components_no_db_duplication(self, app_tree):
        with patch("backup_engine.load_config", return_value=_cfg()):
            result = backup_engine.run_backup(trigger_type="manual")

        assert result["status"] == "success"
        archive_path = app_tree / "backups" / result["filename"]
        assert archive_path.exists()

        with tarfile.open(archive_path, "r:gz") as tar:
            names = tar.getnames()
        assert "data/historical/AAPL.parquet" in names
        assert "models/ml_ensemble.joblib" in names
        assert names.count("data/analysis.db") == 1, "DB must appear exactly once, not duplicated by the data/db overlap"

    def test_data_component_archives_the_investment_policy_file(self, app_tree):
        (app_tree / "data" / "investment_policy.md").write_text("# Policy\n", encoding="utf-8")
        with patch("backup_engine.load_config", return_value=_cfg(INCLUDE_MODELS=False, INCLUDE_DATABASE=False)):
            result = backup_engine.run_backup(trigger_type="manual")

        assert result["status"] == "success"
        with tarfile.open(app_tree / "backups" / result["filename"], "r:gz") as tar:
            assert "data/investment_policy.md" in tar.getnames()

    def test_skips_when_no_components_selected(self, app_tree):
        cfg = _cfg(INCLUDE_DATA=False, INCLUDE_MODELS=False, INCLUDE_DATABASE=False)
        with patch("backup_engine.load_config", return_value=cfg):
            result = backup_engine.run_backup(trigger_type="manual")
        assert result["status"] == "skipped"
        assert not (app_tree / "backups").exists() or list((app_tree / "backups").glob("*.tar.gz")) == []

    def test_nfs_without_server_or_path_returns_error(self, app_tree):
        cfg = _cfg(LOCATION="nfs", NFS_SERVER="", NFS_PATH="")
        with patch("backup_engine.load_config", return_value=cfg):
            result = backup_engine.run_backup(trigger_type="manual")
        assert result["status"] == "error"
        assert "NFS" in result["message"]

    def test_records_history_row_with_archive_sha256(self, app_tree):
        with patch("backup_engine.load_config", return_value=_cfg()):
            result = backup_engine.run_backup(trigger_type="manual")

        conn = None
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT * FROM backup_history WHERE filename = ? ORDER BY id DESC", (result["filename"],)
            ).fetchone()
        finally:
            if conn:
                conn.close()
        assert row is not None
        assert row["status"] == "success"
        assert row["trigger_type"] == "manual"
        assert row["sha256"] == _sha(app_tree / "backups" / result["filename"])

    def test_database_member_is_a_snapshot_that_includes_uncheckpointed_wal_rows(self, app_tree, live_wal_db):
        wal = app_tree / "data" / "analysis.db-wal"
        assert wal.exists() and wal.stat().st_size > 0, "fixture must leave a committed row only in the WAL"

        with patch("backup_engine.load_config", return_value=_cfg()):
            result = backup_engine.run_backup(trigger_type="manual")

        assert result["status"] == "success"
        with tarfile.open(app_tree / "backups" / result["filename"], "r:gz") as tar:
            names = tar.getnames()
            extracted = app_tree / "extracted.db"
            extracted.write_bytes(tar.extractfile("data/analysis.db").read())
        assert not [n for n in names if n.endswith(("-wal", "-shm"))]
        assert names.count("data/analysis.db") == 1

        conn = sqlite3.connect(extracted)
        try:
            values = [row[0] for row in conn.execute("SELECT v FROM marker ORDER BY rowid")]
            verdict = conn.execute("PRAGMA integrity_check").fetchone()[0]
        finally:
            conn.close()
        assert values == ["original", "only-in-wal"]
        assert verdict == "ok"

    def test_data_component_alone_never_archives_the_database_or_its_sidecars(self, app_tree, live_wal_db):
        with patch("backup_engine.load_config", return_value=_cfg(INCLUDE_MODELS=False, INCLUDE_DATABASE=False)):
            result = backup_engine.run_backup(trigger_type="manual")

        assert result["status"] == "success"
        with tarfile.open(app_tree / "backups" / result["filename"], "r:gz") as tar:
            names = tar.getnames()
        assert "data/historical/AAPL.parquet" in names
        assert not [n for n in names if "analysis.db" in n]

    def test_work_folder_is_removed_after_a_successful_run(self, app_tree):
        with patch("backup_engine.load_config", return_value=_cfg()):
            result = backup_engine.run_backup(trigger_type="manual")

        assert result["status"] == "success"
        assert list(app_tree.glob(".backup_*")) == []

    def test_snapshot_failure_records_an_error_and_leaves_no_archive_or_work_folder(self, app_tree):
        with patch("backup_engine.load_config", return_value=_cfg()), \
                patch("backup_engine._snapshot_database", side_effect=sqlite3.OperationalError("disk I/O error")):
            result = backup_engine.run_backup(trigger_type="manual")

        assert result["status"] == "error"
        assert "disk I/O error" in result["message"]
        assert list((app_tree / "backups").glob("*.tar.gz")) == []
        assert list(app_tree.glob(".backup_*")) == []


@pytest.mark.db
class TestRetentionAndListing:
    def test_enforce_retention_keeps_only_newest(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        for ts in ("20260101_000000", "20260102_000000", "20260103_000000", "20260104_000000"):
            (backup_dir / f"backup_{ts}.tar.gz").write_text("x")

        backup_engine._enforce_retention(backup_dir, 2)
        remaining = sorted(p.name for p in backup_dir.glob("backup_*.tar.gz"))
        assert remaining == ["backup_20260103_000000.tar.gz", "backup_20260104_000000.tar.gz"]

    def test_non_engine_names_are_neither_retained_nor_deleted(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        odd = ["backup_x.tar.gz", "backup_<img src=x onerror=alert(1)>.tar.gz", "backup_20260101_000000.tar.gz.bak"]
        for name in odd:
            (backup_dir / name).write_text("x")
        for ts in ("20260101_000000", "20260102_000000"):
            (backup_dir / f"backup_{ts}.tar.gz").write_text("x")

        backup_engine._enforce_retention(backup_dir, 1)

        assert (backup_dir / "backup_20260102_000000.tar.gz").exists()
        assert not (backup_dir / "backup_20260101_000000.tar.gz").exists()
        assert all((backup_dir / name).exists() for name in odd)

    def test_list_backups_sorted_newest_first_and_only_engine_names(self, app_tree):
        backup_dir = app_tree / "backups"
        backup_dir.mkdir()
        (backup_dir / "backup_20260101_000000.tar.gz").write_text("x")
        (backup_dir / "backup_20260201_000000.tar.gz").write_text("xx")
        (backup_dir / "backup_<img src=x onerror=alert(1)>.tar.gz").write_text("x")
        (backup_dir / "backup_x.tar.gz").write_text("x")
        (backup_dir / "backup_20260301_000000.tar.gz").symlink_to(backup_dir / "backup_20260201_000000.tar.gz")

        with patch("backup_engine.load_config", return_value=_cfg()):
            backups = backup_engine.list_backups()

        assert [b["filename"] for b in backups] == ["backup_20260201_000000.tar.gz", "backup_20260101_000000.tar.gz"]
        assert backups[0]["size_bytes"] == 2

    def test_list_backups_flags_whether_a_hash_is_recorded(self, app_tree):
        backup_dir = app_tree / "backups"
        backup_dir.mkdir()
        (backup_dir / "backup_20260101_000000.tar.gz").write_text("x")
        (backup_dir / "backup_20260102_000000.tar.gz").write_text("x")
        _insert_history("backup_20260102_000000.tar.gz", "ab" * 32)

        with patch("backup_engine.load_config", return_value=_cfg()):
            flags = {b["filename"]: b["hash_recorded"] for b in backup_engine.list_backups()}

        assert flags == {"backup_20260102_000000.tar.gz": True, "backup_20260101_000000.tar.gz": False}


@pytest.mark.db
class TestGetBackupStatus:
    def test_aggregates_last_backup_and_stored_files(self, app_tree):
        with patch("backup_engine.load_config", return_value=_cfg()):
            backup_engine.run_backup(trigger_type="manual")
            status = backup_engine.get_backup_status()

        assert status["last_backup"]["status"] == "success"
        assert status["stored_count"] == 1
        assert status["stored_size_bytes"] > 0

    def test_adopted_rows_are_not_reported_as_the_last_backup(self, app_tree):
        with patch("backup_engine.load_config", return_value=_cfg()):
            result = backup_engine.run_backup(trigger_type="manual")
            _insert_history("backup_20200101_000000.tar.gz", "cd" * 32, trigger="adopted")
            status = backup_engine.get_backup_status()

        assert status["last_backup"]["filename"] == result["filename"]


@pytest.mark.db
class TestRestoreBackup:
    def test_rejects_path_traversal_filename(self):
        with pytest.raises(ValueError):
            backup_engine.restore_backup("../../etc/passwd")

    def test_rejects_filename_with_slash(self):
        with pytest.raises(ValueError):
            backup_engine.restore_backup("sub/dir/backup.tar.gz")

    @pytest.mark.parametrize("name", [
        "backup_x.tar.gz", "backup_20260101_000000.tar.gz.bak", "backup_20260101_000000.tar.gz\n",
        "backup_<img src=x>.tar.gz", "evil.tar.gz", "",
    ])
    def test_rejects_names_the_engine_never_writes(self, name):
        with pytest.raises(ValueError):
            backup_engine.restore_backup(name)

    def test_round_trip_restores_files_and_database(self, app_tree):
        with patch("backup_engine.load_config", return_value=_cfg()):
            result = backup_engine.run_backup(trigger_type="manual")
            assert result["status"] == "success"

            (app_tree / "data" / "historical" / "AAPL.parquet").write_text("tampered")
            (app_tree / "models" / "ml_ensemble.joblib").unlink()
            conn = sqlite3.connect(app_tree / "data" / "analysis.db")
            conn.execute("UPDATE marker SET v = 'changed'")
            conn.commit()
            conn.close()

            restore_result = backup_engine.restore_backup(result["filename"])

        assert restore_result["status"] == "success"
        assert (app_tree / "data" / "historical" / "AAPL.parquet").read_text() == "fake-parquet"
        assert (app_tree / "models" / "ml_ensemble.joblib").read_text() == "fake-model"
        assert _live_marker(app_tree) == "original"
        assert list(app_tree.glob(".restore_*")) == []
        assert list((app_tree / "data").glob(".restore_*")) == []

    def test_missing_file_returns_error_dict(self, app_tree):
        with patch("backup_engine.load_config", return_value=_cfg()):
            result = backup_engine.restore_backup("backup_20200101_000000.tar.gz")
        assert result["status"] == "error"

    def test_symlinked_archive_is_not_found(self, app_tree):
        backup_dir = app_tree / "backups"
        backup_dir.mkdir()
        target = _craft_archive(app_tree, "backup_20200101_000001.tar.gz", [("data/x.txt", "file", "x")])
        (backup_dir / "backup_20200101_000002.tar.gz").symlink_to(target)
        with patch("backup_engine.load_config", return_value=_cfg()):
            result = backup_engine.restore_backup("backup_20200101_000002.tar.gz")
        assert result["status"] == "error"

    def test_hash_mismatch_is_refused_even_with_trust_flag(self, app_tree):
        path = _craft_archive(
            app_tree, "backup_20200101_000003.tar.gz", [("data/restored.txt", "file", "x")], register_hash=False,
        )
        _insert_history(path.name, "00" * 32)
        with patch("backup_engine.load_config", return_value=_cfg()):
            with pytest.raises(backup_engine.RestoreRefused, match="does not match"):
                backup_engine.restore_backup(path.name, trust_unhashed=True)
        assert not (app_tree / "data" / "restored.txt").exists()

    def test_unhashed_archive_is_refused_without_trust(self, app_tree):
        path = _craft_archive(
            app_tree, "backup_20200101_000004.tar.gz", [("data/restored.txt", "file", "x")], register_hash=False,
        )
        with patch("backup_engine.load_config", return_value=_cfg()):
            with pytest.raises(backup_engine.RestoreRefused, match="No SHA-256"):
                backup_engine.restore_backup(path.name)
        assert not (app_tree / "data" / "restored.txt").exists()

    def test_unhashed_archive_restores_with_trust_and_is_then_verified(self, app_tree):
        path = _craft_archive(
            app_tree, "backup_20200101_000005.tar.gz",
            [("data", "dir", None), ("data/restored.txt", "file", "x"), ("data/analysis.db", "file", _db_bytes(app_tree))],
            register_hash=False,
        )
        with patch("backup_engine.load_config", return_value=_cfg()):
            result = backup_engine.restore_backup(path.name, trust_unhashed=True)
            assert result["status"] == "success"
            assert (app_tree / "data" / "restored.txt").read_text() == "x"
            assert _history_hash(path.name) is None, "history is written into the restored DB, not the live one"

            conn = sqlite3.connect(app_tree / "data" / "analysis.db")
            try:
                adopted = conn.execute(
                    "SELECT trigger_type, sha256, status FROM backup_history WHERE filename = ?", (path.name,)
                ).fetchone()
            finally:
                conn.close()
        assert adopted == ("adopted", _sha(path), "success")

    def test_trusted_archive_without_a_database_is_recorded_in_the_live_history(self, app_tree):
        path = _craft_archive(
            app_tree, "backup_20200101_000006.tar.gz", [("data/restored.txt", "file", "x")], register_hash=False,
        )
        with patch("backup_engine.load_config", return_value=_cfg()):
            backup_engine.restore_backup(path.name, trust_unhashed=True)
        assert _history_hash(path.name) == _sha(path)

    def test_crafted_members_are_refused_before_anything_is_extracted(self, app_tree):
        cases = {
            "backup_20200102_000001.tar.gz": [("data/ok.txt", "file", "ok"), ("../evil.txt", "file", "x")],
            "backup_20200102_000002.tar.gz": [("data/ok.txt", "file", "ok"), ("/tmp/abs.txt", "file", "x")],
            "backup_20200102_000003.tar.gz": [("data/ok.txt", "file", "ok"), (".env", "file", "SECRET=1")],
            "backup_20200102_000004.tar.gz": [("data/ok.txt", "file", "ok"), ("data/link", "symlink", "/etc/passwd")],
            "backup_20200102_000005.tar.gz": [("data/ok.txt", "file", "ok"), ("data/hard", "hardlink", "data/ok.txt")],
            "backup_20200102_000006.tar.gz": [("data/ok.txt", "file", "ok"), ("data/../../up.txt", "file", "x")],
            "backup_20200102_000007.tar.gz": [("data", "file", "not-a-directory")],
            "backup_20200102_000008.tar.gz": [("data/ok.txt", "file", "ok"), ("models_evil/x.txt", "file", "x")],
        }
        with patch("backup_engine.load_config", return_value=_cfg()):
            for name, members in cases.items():
                _craft_archive(app_tree, name, members)
                with pytest.raises(backup_engine.RestoreRefused):
                    backup_engine.restore_backup(name)
        assert not (app_tree / "data" / "ok.txt").exists()
        assert not (app_tree / ".env").exists()
        assert not (app_tree.parent / "evil.txt").exists()
        assert not (app_tree.parent / "up.txt").exists()
        assert not (app_tree / "data" / "link").exists()

    def test_corrupt_database_is_refused_before_any_file_is_replaced(self, app_tree):
        _craft_archive(
            app_tree, "backup_20200103_000001.tar.gz",
            [("data/historical/AAPL.parquet", "file", "from-archive"), ("data/analysis.db", "file", b"not a sqlite file" * 50)],
        )
        with patch("backup_engine.load_config", return_value=_cfg()):
            with pytest.raises(backup_engine.RestoreRefused, match="integrity"):
                backup_engine.restore_backup("backup_20200103_000001.tar.gz")
        assert (app_tree / "data" / "historical" / "AAPL.parquet").read_text() == "fake-parquet"
        assert _live_marker(app_tree) == "original"
        assert list((app_tree / "data").glob(".restore_*")) == []

    def test_stale_wal_and_shm_are_removed_and_archived_sidecars_are_not_extracted(self, app_tree):
        db = app_tree / "data" / "analysis.db"
        (app_tree / "data" / "analysis.db-wal").write_text("stale-wal")
        (app_tree / "data" / "analysis.db-shm").write_text("stale-shm")
        _craft_archive(
            app_tree, "backup_20200104_000001.tar.gz",
            [
                ("data/analysis.db", "file", _db_bytes(app_tree)),
                ("data/analysis.db-wal", "file", "archived-wal"),
                ("data/analysis.db-shm", "file", "archived-shm"),
            ],
        )
        with patch("backup_engine.load_config", return_value=_cfg()):
            result = backup_engine.restore_backup("backup_20200104_000001.tar.gz")

        assert result["status"] == "success"
        assert _live_marker(app_tree) == "from-archive"
        assert not (db.parent / "analysis.db-wal").exists()
        assert not (db.parent / "analysis.db-shm").exists()

    def test_wal_and_shm_in_a_data_only_archive_are_not_extracted(self, app_tree):
        (app_tree / "data" / "analysis.db-wal").write_text("stale-wal")
        _craft_archive(
            app_tree, "backup_20200104_000002.tar.gz",
            [("data/historical/AAPL.parquet", "file", "from-archive"), ("data/analysis.db-wal", "file", "archived-wal")],
        )
        with patch("backup_engine.load_config", return_value=_cfg()):
            backup_engine.restore_backup("backup_20200104_000002.tar.gz")

        assert (app_tree / "data" / "historical" / "AAPL.parquet").read_text() == "from-archive"
        assert (app_tree / "data" / "analysis.db-wal").read_text() == "stale-wal"
        assert _live_marker(app_tree) == "original"

    @pytest.mark.parametrize("archive_has_sha_column", [True, False])
    def test_history_rows_missing_from_the_restored_database_are_carried_forward(self, app_tree, archive_has_sha_column):
        _insert_history("backup_20991231_235959.tar.gz", "ef" * 32)
        _craft_archive(
            app_tree, "backup_20200105_000001.tar.gz",
            [("data/analysis.db", "file", _db_bytes(app_tree, with_sha256=archive_has_sha_column))],
        )
        with patch("backup_engine.load_config", return_value=_cfg()):
            backup_engine.restore_backup("backup_20200105_000001.tar.gz")

        conn = sqlite3.connect(app_tree / "data" / "analysis.db")
        try:
            rows = dict(conn.execute("SELECT filename, sha256 FROM backup_history").fetchall())
        finally:
            conn.close()
        assert rows["backup_20991231_235959.tar.gz"] == "ef" * 32
        assert rows["backup_20200105_000001.tar.gz"] == _sha(app_tree / "backups" / "backup_20200105_000001.tar.gz")

    def test_carried_forward_history_is_not_duplicated_by_a_second_restore(self, app_tree):
        _craft_archive(
            app_tree, "backup_20200106_000001.tar.gz", [("data/analysis.db", "file", _db_bytes(app_tree))],
        )
        with patch("backup_engine.load_config", return_value=_cfg()):
            backup_engine.restore_backup("backup_20200106_000001.tar.gz")
            backup_engine.restore_backup("backup_20200106_000001.tar.gz")

        conn = sqlite3.connect(app_tree / "data" / "analysis.db")
        try:
            count = conn.execute(
                "SELECT COUNT(*) FROM backup_history WHERE filename = 'backup_20200106_000001.tar.gz'"
            ).fetchone()[0]
        finally:
            conn.close()
        assert count == 1

    @pytest.mark.api
    def test_restore_route_returns_400_with_a_fixed_message_for_an_unhashed_archive(
        self, client, step_up_headers, app_tree
    ):
        _craft_archive(
            app_tree, "backup_20200107_000001.tar.gz", [("data/x.txt", "file", "x")], register_hash=False,
        )
        with patch("backup_engine.load_config", return_value=_cfg()):
            resp = client.post(
                "/api/backup/restore",
                json={"filename": "backup_20200107_000001.tar.gz"},
                headers=step_up_headers,
            )
        assert resp.status_code == 400
        assert "No SHA-256" in resp.json()["message"]


_OAUTH_TABLES = ("mcp_oauth_client", "mcp_oauth_requests", "mcp_oauth_grants", "mcp_oauth_tokens")


def _oauth_db_bytes(tmp_path, grants_sql=None):
    """An archive database that still holds an active grant, a revoked one, an open pairing window and a waiting request, as an older backup would."""
    path = tmp_path / "crafted_oauth.db"
    path.unlink(missing_ok=True)
    _make_db(path)
    live = get_connection()
    try:
        ddl = [r[0] for r in live.execute(f"SELECT sql FROM sqlite_master WHERE type = 'table' AND name IN ({', '.join('?' * len(_OAUTH_TABLES))})", _OAUTH_TABLES)]
    finally:
        live.close()
    conn = sqlite3.connect(path)
    try:
        for statement in ddl:
            conn.execute(statement)
        if grants_sql:
            conn.execute("DROP TABLE mcp_oauth_grants")
            conn.execute(grants_sql)
        else:
            conn.execute(
                "INSERT INTO mcp_oauth_grants (id, client_id, scope, resource, created_at, expires_at, revoked_at) VALUES "
                "(1, 'mcp-old', 'mcp:read', 'https://x/mcp', '2026-10-01 00:00:00', '2999-01-01 00:00:00', NULL), "
                "(2, 'mcp-old', 'mcp:read', 'https://x/mcp', '2026-10-01 00:00:00', '2999-01-01 00:00:00', '2026-10-02 00:00:00')"
            )
        conn.execute("INSERT INTO mcp_oauth_client (id, client_id, secret_hash, created_at, pairing_open_until) VALUES (1, 'mcp-old', 'h', '2026-10-01 00:00:00', '2999-01-01 00:00:00')")
        if not grants_sql:
            conn.execute(
                "INSERT INTO mcp_oauth_requests (id, display_code, client_state, code_challenge, redirect_uri, resource, scope, created_at, expires_at, binding_hash) "
                "VALUES ('r1', 'ABC234', 's', 'c', 'u', 'r', 'mcp:read', '2026-10-01 00:00:00', '2999-01-01 00:00:00', 'b')"
            )
        conn.commit()
    finally:
        conn.close()
    return path.read_bytes()


def _live_oauth_rows(app_tree):
    conn = sqlite3.connect(app_tree / "data" / "analysis.db")
    try:
        grants = conn.execute("SELECT id, revoked_at IS NOT NULL, revoked_at FROM mcp_oauth_grants ORDER BY id").fetchall()
        client = conn.execute("SELECT client_id, pairing_open_until FROM mcp_oauth_client").fetchall()
        requests = conn.execute("SELECT COUNT(*) FROM mcp_oauth_requests").fetchone()[0]
    finally:
        conn.close()
    return grants, client, requests


class TestRestoreRevokesRemoteAccess:
    """An older backup would bring revoked OAuth grants back to life, so a restore that swaps the database revokes them all."""

    def _restore(self, app_tree, name, db_bytes):
        _craft_archive(app_tree, name, [("data/analysis.db", "file", db_bytes)])
        with patch("backup_engine.load_config", return_value=_cfg()), patch("mcp_server.audit.record_action") as record:
            result = backup_engine.restore_backup(name)
        return result, record

    def test_every_grant_is_revoked_the_window_closed_and_the_requests_dropped(self, app_tree):
        result, _ = self._restore(app_tree, "backup_20200201_000001.tar.gz", _oauth_db_bytes(app_tree))
        assert result["status"] == "success"
        grants, client, requests = _live_oauth_rows(app_tree)
        assert [(g[0], g[1]) for g in grants] == [(1, 1), (2, 1)]
        assert grants[1][2] == "2026-10-02 00:00:00", "a grant that was already revoked keeps its own revocation time"
        assert client == [("mcp-old", None)]
        assert requests == 0

    def test_the_in_memory_token_cache_is_dropped_so_a_cached_token_dies_at_once(self, app_tree):
        from mcp_server import oauth
        oauth._cache["cached-token-hash"] = (1, 9e12)
        try:
            self._restore(app_tree, "backup_20200201_000002.tar.gz", _oauth_db_bytes(app_tree))
            assert "cached-token-hash" not in oauth._cache
        finally:
            oauth._cache.pop("cached-token-hash", None)

    def test_the_restore_is_written_to_the_mcp_audit_log(self, app_tree):
        _, record = self._restore(app_tree, "backup_20200201_000003.tar.gz", _oauth_db_bytes(app_tree))
        record.assert_called_once_with("restore: 1 grants revoked")

    def test_an_archive_from_before_the_oauth_tables_restores_and_logs_nothing(self, app_tree):
        from mcp_server import oauth
        oauth._cache["cached-token-hash"] = (1, 9e12)
        try:
            result, record = self._restore(app_tree, "backup_20200201_000004.tar.gz", _db_bytes(app_tree))
            assert result["status"] == "success" and _live_marker(app_tree) == "from-archive"
            record.assert_not_called()
            assert "cached-token-hash" not in oauth._cache
        finally:
            oauth._cache.pop("cached-token-hash", None)

    def test_an_archive_without_a_database_leaves_the_live_grants_alone(self, app_tree):
        from mcp_server import oauth
        oauth._cache["cached-token-hash"] = (1, 9e12)
        try:
            _craft_archive(app_tree, "backup_20200201_000005.tar.gz", [("data/restored.txt", "file", "x")])
            with patch("backup_engine.load_config", return_value=_cfg()), patch("mcp_server.audit.record_action") as record:
                assert backup_engine.restore_backup("backup_20200201_000005.tar.gz")["status"] == "success"
            record.assert_not_called()
            assert "cached-token-hash" in oauth._cache
        finally:
            oauth._cache.pop("cached-token-hash", None)

    def test_a_failure_while_revoking_changes_nothing_live(self, app_tree):
        broken = _oauth_db_bytes(app_tree, grants_sql="CREATE TABLE mcp_oauth_grants (id INTEGER)")
        result, record = self._restore(app_tree, "backup_20200201_000006.tar.gz", broken)
        assert result["status"] == "error"
        assert _live_marker(app_tree) == "original"
        assert (app_tree / "data" / "historical" / "AAPL.parquet").read_text() == "fake-parquet"
        record.assert_not_called()
        assert list((app_tree / "data").glob(".restore_*")) == []

    def test_no_work_file_is_left_beside_the_restored_database(self, app_tree):
        self._restore(app_tree, "backup_20200201_000007.tar.gz", _oauth_db_bytes(app_tree))
        assert sorted(p.name for p in (app_tree / "data").iterdir() if p.is_file()) == ["analysis.db"]


@pytest.mark.api
class TestRestoreJobGuard:
    @pytest.fixture
    def live_scheduler(self):
        sched = BackgroundScheduler()
        sched.start()
        with patch.object(scheduler_engine, "scheduler", sched):
            yield sched
        if sched.running:
            sched.shutdown(wait=False)

    @pytest.fixture(autouse=True)
    def _no_leftover_active_jobs(self):
        yield
        scheduler_engine._mark_job_done("Busy Job")

    @staticmethod
    def _post(client, step_up_headers):
        return client.post(
            "/api/backup/restore",
            json={"filename": "backup_20200107_000001.tar.gz"},
            headers=step_up_headers,
        )

    def test_returns_409_and_restores_nothing_while_a_job_is_running(self, client, step_up_headers, live_scheduler):
        scheduler_engine._mark_job_started("Busy Job")
        with patch("api_routes_triggers.restore_backup") as restore:
            resp = self._post(client, step_up_headers)

        assert resp.status_code == 409
        assert resp.json()["status"] == "busy"
        assert "Busy Job" in resp.json()["message"]
        restore.assert_not_called()
        assert live_scheduler.state == STATE_RUNNING

    def test_pauses_the_scheduler_for_the_restore_and_resumes_it(self, client, step_up_headers, live_scheduler):
        seen = []

        def _restore(filename, trust_unhashed=False):
            seen.append(live_scheduler.state)
            return {"status": "success", "message": "done"}

        with patch("api_routes_triggers.restore_backup", side_effect=_restore):
            resp = self._post(client, step_up_headers)

        assert resp.status_code == 200
        assert seen == [STATE_PAUSED]
        assert live_scheduler.state == STATE_RUNNING

    def test_resumes_the_scheduler_when_the_restore_is_refused(self, client, step_up_headers, live_scheduler):
        with patch("api_routes_triggers.restore_backup", side_effect=backup_engine.RestoreRefused("bad archive")):
            resp = self._post(client, step_up_headers)

        assert resp.status_code == 400
        assert live_scheduler.state == STATE_RUNNING


@pytest.mark.db
def test_run_backup_job_records_scheduler_run():
    """scheduler_jobs.run_backup_job() must call record_job_run('backup_job') even when the backup itself errors."""
    import scheduler_jobs

    with patch("scheduler_jobs.run_backup", return_value={"status": "error", "message": "boom"}):
        scheduler_jobs.run_backup_job()

    conn = None
    try:
        conn = get_connection()
        row = conn.execute("SELECT last_run FROM scheduler_run_log WHERE job_id = 'backup_job'").fetchone()
    finally:
        if conn:
            conn.close()
    assert row is not None
    assert row["last_run"] is not None
