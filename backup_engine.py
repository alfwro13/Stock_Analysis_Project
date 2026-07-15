import logging
import os
import subprocess
import tarfile
from datetime import datetime, timezone
from pathlib import Path

from config import BASE_DIR, DATA_DIR, DB_PATH, load_config
from database import get_connection
from notification_engine import notify

logger = logging.getLogger(__name__)

MODELS_DIR = BASE_DIR / "models"
_NFS_MOUNT_POINT = DATA_DIR / ".nfs_backup_mount"
_ARCHIVE_GLOB = "backup_*.tar.gz"


def _exclude_db_filter(tarinfo: tarfile.TarInfo):
    if Path(tarinfo.name).name == "analysis.db":
        return None
    return tarinfo


def _resolve_backup_dir(cfg: dict) -> Path:
    if cfg.get("LOCATION") == "nfs":
        server = (cfg.get("NFS_SERVER") or "").strip()
        share_path = (cfg.get("NFS_PATH") or "").strip()
        if not server or not share_path:
            raise ValueError("NFS server and path must both be configured.")
        _NFS_MOUNT_POINT.mkdir(parents=True, exist_ok=True)
        if not os.path.ismount(_NFS_MOUNT_POINT):
            result = subprocess.run(
                ["sudo", "-n", "/usr/local/sbin/quant-backup-nfs-mount", f"{server}:{share_path}", str(_NFS_MOUNT_POINT)],
                capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                raise RuntimeError(f"NFS mount failed: {(result.stderr or result.stdout).strip()}")
        return _NFS_MOUNT_POINT

    local_path = (cfg.get("LOCAL_PATH") or "backups").strip() or "backups"
    target = Path(local_path)
    if not target.is_absolute():
        target = BASE_DIR / target
    target.mkdir(parents=True, exist_ok=True)
    return target


def _destination_label(cfg: dict, backup_dir: Path) -> str:
    """User-facing destination — the NFS share, not the internal scratch mountpoint, since the latter means nothing to the operator."""
    if cfg.get("LOCATION") == "nfs":
        return f"NFS {(cfg.get('NFS_SERVER') or '').strip()}:{(cfg.get('NFS_PATH') or '').strip()}"
    return str(backup_dir)


def _release_backup_dir(cfg: dict) -> None:
    if cfg.get("LOCATION") == "nfs" and os.path.ismount(_NFS_MOUNT_POINT):
        subprocess.run(
            ["sudo", "-n", "/usr/local/sbin/quant-backup-nfs-umount", str(_NFS_MOUNT_POINT)],
            capture_output=True, text=True, timeout=30,
        )


def _enforce_retention(backup_dir: Path, retention_count: int) -> None:
    archives = sorted(backup_dir.glob(_ARCHIVE_GLOB), key=lambda p: p.name, reverse=True)
    for stale in archives[max(retention_count, 0):]:
        try:
            stale.unlink()
            logger.info("Removed backup beyond retention limit: %s", stale.name)
        except OSError as e:
            logger.warning("Failed to remove old backup %s: %s", stale.name, e)


def _record_backup_history(**fields) -> None:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO backup_history (started_at, finished_at, trigger_type, location_type, destination, "
            "components, filename, size_bytes, status, error_message) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fields["started_at"], fields.get("finished_at"), fields["trigger_type"], fields["location_type"],
                fields.get("destination"), fields.get("components"), fields.get("filename"),
                fields.get("size_bytes"), fields["status"], fields.get("error_message"),
            ),
        )
        conn.commit()
    except Exception as e:
        logger.error("Failed to record backup history: %s", e)
    finally:
        if conn:
            conn.close()


def run_backup(trigger_type: str = "scheduled") -> dict:
    cfg = load_config().get("SCHEDULING", {}).get("BACKUP", {})
    started_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    location_type = cfg.get("LOCATION", "local")
    components = [
        name for name, flag in (
            ("data", cfg.get("INCLUDE_DATA", True)),
            ("models", cfg.get("INCLUDE_MODELS", True)),
            ("database", cfg.get("INCLUDE_DATABASE", True)),
        ) if flag
    ]

    if not components:
        msg = "Backup skipped: no components selected (Data/Models/Database are all unchecked)."
        logger.warning(msg)
        notify("backup_status", "Warning", msg, level="warning")
        return {"status": "skipped", "message": msg}

    try:
        backup_dir = _resolve_backup_dir(cfg)
    except Exception as e:
        msg = f"Backup failed to resolve destination: {e}"
        logger.error("Backup failed to resolve destination: %s", e)
        _record_backup_history(
            started_at=started_at, finished_at=started_at, trigger_type=trigger_type,
            location_type=location_type, destination=None, components=",".join(components),
            filename=None, size_bytes=None, status="error", error_message=str(e),
        )
        notify("backup_status", "Error", msg, level="error")
        return {"status": "error", "message": msg}

    filename = f"backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}.tar.gz"
    archive_path = backup_dir / filename

    try:
        with tarfile.open(archive_path, "w:gz") as tar:
            if "data" in components and DATA_DIR.exists():
                tar.add(DATA_DIR, arcname="data", filter=_exclude_db_filter)
            if "models" in components and MODELS_DIR.exists():
                tar.add(MODELS_DIR, arcname="models")
            if "database" in components and DB_PATH.exists():
                tar.add(DB_PATH, arcname="data/analysis.db")

        size_bytes = archive_path.stat().st_size
        _enforce_retention(backup_dir, int(cfg.get("RETENTION_COUNT", 7) or 7))
        destination = _destination_label(cfg, backup_dir)

        finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        _record_backup_history(
            started_at=started_at, finished_at=finished_at, trigger_type=trigger_type,
            location_type=location_type, destination=destination, components=",".join(components),
            filename=filename, size_bytes=size_bytes, status="success", error_message=None,
        )

        size_mb = size_bytes / (1024 * 1024)
        msg = f"Backup completed: {filename} ({size_mb:.1f} MB) -> {destination}"
        logger.info("Backup completed: %s (%.1f MB) -> %s", filename, size_mb, destination)
        notify("backup_status", "Success", msg, level="info")
        return {"status": "success", "filename": filename, "size_bytes": size_bytes}

    except Exception as e:
        if archive_path.exists():
            try:
                archive_path.unlink()
            except OSError:
                pass
        msg = f"Backup failed: {e}"
        logger.error("Backup failed: %s", e)
        _record_backup_history(
            started_at=started_at, finished_at=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            trigger_type=trigger_type, location_type=location_type, destination=_destination_label(cfg, backup_dir),
            components=",".join(components), filename=None, size_bytes=None,
            status="error", error_message=str(e),
        )
        notify("backup_status", "Error", msg, level="error")
        return {"status": "error", "message": msg}
    finally:
        _release_backup_dir(cfg)


def list_backups() -> list:
    cfg = load_config().get("SCHEDULING", {}).get("BACKUP", {})
    try:
        backup_dir = _resolve_backup_dir(cfg)
    except Exception as e:
        logger.warning("Could not list backups: %s", e)
        return []
    try:
        backups = []
        for p in sorted(backup_dir.glob(_ARCHIVE_GLOB), reverse=True):
            stat = p.stat()
            backups.append({
                "filename": p.name,
                "size_bytes": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            })
        return backups
    finally:
        _release_backup_dir(cfg)


def get_backup_status() -> dict:
    conn = None
    last_backup = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM backup_history ORDER BY id DESC LIMIT 1")
        row = cursor.fetchone()
        last_backup = dict(row) if row else None
    except Exception as e:
        logger.error("Failed to read backup history: %s", e)
    finally:
        if conn:
            conn.close()

    backups = list_backups()
    return {
        "last_backup": last_backup,
        "stored_count": len(backups),
        "stored_size_bytes": sum(b["size_bytes"] for b in backups),
        "backups": backups,
    }


def restore_backup(filename: str) -> dict:
    if not filename or "/" in filename or "\\" in filename or filename in (".", ".."):
        raise ValueError("Invalid backup filename.")

    cfg = load_config().get("SCHEDULING", {}).get("BACKUP", {})
    try:
        backup_dir = _resolve_backup_dir(cfg)
        archive_path = (backup_dir / filename).resolve()
        if not archive_path.is_relative_to(backup_dir.resolve()):
            raise ValueError("Invalid backup filename.")
        if not archive_path.exists():
            raise FileNotFoundError(f"Backup file not found: {filename}")

        with tarfile.open(archive_path, "r:gz") as tar:
            tar.extractall(path=BASE_DIR)

        msg = f"Restore completed from {filename}. Restart the service so all in-memory caches reload the restored data."
        logger.info("Restore completed from %s", filename)
        notify("backup_status", "Success", msg, level="info")
        return {"status": "success", "message": msg}
    except Exception as e:
        logger.error("Restore failed: %s", e, exc_info=True)
        notify("backup_status", "Error", f"Restore failed: {e}", level="error")
        return {"status": "error", "message": "Restore failed. Check server logs for details."}
    finally:
        _release_backup_dir(cfg)
