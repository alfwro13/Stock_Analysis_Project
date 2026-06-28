import tarfile
from unittest.mock import patch

import pytest

import backup_engine
from database import get_connection


def _cfg(tmp_path, **overrides):
    cfg = {
        "LOCATION": "local",
        "LOCAL_PATH": str(tmp_path / "backups"),
        "NFS_SERVER": "",
        "NFS_PATH": "",
        "INCLUDE_DATA": True,
        "INCLUDE_MODELS": True,
        "INCLUDE_DATABASE": True,
        "RETENTION_COUNT": 7,
    }
    cfg.update(overrides)
    return {"SCHEDULING": {"BACKUP": cfg}}


def _seed_source_tree(tmp_path):
    """Builds a small fake data/models/db tree and points backup_engine at it."""
    data_dir = tmp_path / "data"
    (data_dir / "historical").mkdir(parents=True)
    (data_dir / "historical" / "AAPL.parquet").write_text("fake-parquet")
    db_path = data_dir / "analysis.db"
    db_path.write_text("fake-sqlite-db")

    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "ml_ensemble.joblib").write_text("fake-model")

    return data_dir, models_dir, db_path


@pytest.fixture
def isolated_source(tmp_path, monkeypatch):
    data_dir, models_dir, db_path = _seed_source_tree(tmp_path)
    monkeypatch.setattr(backup_engine, "DATA_DIR", data_dir)
    monkeypatch.setattr(backup_engine, "MODELS_DIR", models_dir)
    monkeypatch.setattr(backup_engine, "DB_PATH", db_path)
    return tmp_path


@pytest.mark.db
class TestRunBackup:
    def test_creates_archive_with_all_components_no_db_duplication(self, isolated_source):
        with patch("backup_engine.load_config", return_value=_cfg(isolated_source)):
            result = backup_engine.run_backup(trigger_type="manual")

        assert result["status"] == "success"
        archive_path = isolated_source / "backups" / result["filename"]
        assert archive_path.exists()

        with tarfile.open(archive_path, "r:gz") as tar:
            names = tar.getnames()
        assert "data/historical/AAPL.parquet" in names
        assert "models/ml_ensemble.joblib" in names
        assert names.count("data/analysis.db") == 1, "DB must appear exactly once, not duplicated by the data/db overlap"

    def test_skips_when_no_components_selected(self, isolated_source):
        cfg = _cfg(isolated_source, INCLUDE_DATA=False, INCLUDE_MODELS=False, INCLUDE_DATABASE=False)
        with patch("backup_engine.load_config", return_value=cfg):
            result = backup_engine.run_backup(trigger_type="manual")
        assert result["status"] == "skipped"
        assert not (isolated_source / "backups").exists() or list((isolated_source / "backups").glob("*.tar.gz")) == []

    def test_nfs_without_server_or_path_returns_error(self, isolated_source):
        cfg = _cfg(isolated_source, LOCATION="nfs", NFS_SERVER="", NFS_PATH="")
        with patch("backup_engine.load_config", return_value=cfg):
            result = backup_engine.run_backup(trigger_type="manual")
        assert result["status"] == "error"
        assert "NFS" in result["message"]

    def test_records_history_row_on_success(self, isolated_source):
        with patch("backup_engine.load_config", return_value=_cfg(isolated_source)):
            result = backup_engine.run_backup(trigger_type="manual")

        conn = None
        try:
            conn = get_connection()
            row = conn.execute(
                "SELECT * FROM backup_history WHERE filename = ?", (result["filename"],)
            ).fetchone()
        finally:
            if conn:
                conn.close()
        assert row is not None
        assert row["status"] == "success"
        assert row["trigger_type"] == "manual"


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

    def test_list_backups_sorted_newest_first(self, tmp_path):
        backup_dir = tmp_path / "backups"
        backup_dir.mkdir()
        (backup_dir / "backup_20260101_000000.tar.gz").write_text("x")
        (backup_dir / "backup_20260201_000000.tar.gz").write_text("xx")

        cfg = _cfg(tmp_path)
        with patch("backup_engine.load_config", return_value=cfg):
            backups = backup_engine.list_backups()

        assert [b["filename"] for b in backups] == ["backup_20260201_000000.tar.gz", "backup_20260101_000000.tar.gz"]
        assert backups[0]["size_bytes"] == 2


@pytest.mark.db
class TestGetBackupStatus:
    def test_aggregates_last_backup_and_stored_files(self, isolated_source):
        with patch("backup_engine.load_config", return_value=_cfg(isolated_source)):
            backup_engine.run_backup(trigger_type="manual")
            status = backup_engine.get_backup_status()

        assert status["last_backup"]["status"] == "success"
        assert status["stored_count"] == 1
        assert status["stored_size_bytes"] > 0


@pytest.mark.db
class TestRestoreBackup:
    def test_rejects_path_traversal_filename(self):
        with pytest.raises(ValueError):
            backup_engine.restore_backup("../../etc/passwd")

    def test_rejects_filename_with_slash(self):
        with pytest.raises(ValueError):
            backup_engine.restore_backup("sub/dir/backup.tar.gz")

    def test_round_trip_restores_files_into_base_dir(self, isolated_source, tmp_path, monkeypatch):
        with patch("backup_engine.load_config", return_value=_cfg(isolated_source)):
            result = backup_engine.run_backup(trigger_type="manual")
            assert result["status"] == "success"

            restore_root = tmp_path / "restored"
            restore_root.mkdir()
            monkeypatch.setattr(backup_engine, "BASE_DIR", restore_root)

            restore_result = backup_engine.restore_backup(result["filename"])

        assert restore_result["status"] == "success"
        assert (restore_root / "data" / "historical" / "AAPL.parquet").read_text() == "fake-parquet"
        assert (restore_root / "models" / "ml_ensemble.joblib").read_text() == "fake-model"
        assert (restore_root / "data" / "analysis.db").read_text() == "fake-sqlite-db"

    def test_missing_file_returns_error_dict(self, isolated_source):
        with patch("backup_engine.load_config", return_value=_cfg(isolated_source)):
            result = backup_engine.restore_backup("backup_does_not_exist.tar.gz")
        assert result["status"] == "error"


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
