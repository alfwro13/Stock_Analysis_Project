import contextlib
import hashlib
import logging
import os
import re
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Optional
from urllib.parse import quote

from config import BASE_DIR, DATA_DIR, DB_PATH, load_config
from database import get_connection
from notification_engine import notify

logger = logging.getLogger(__name__)

MODELS_DIR = BASE_DIR / "models"
_NFS_MOUNT_POINT = BASE_DIR / ".nfs_backup_mount"
_ARCHIVE_NAME_RE = re.compile(r"backup_\d{8}_\d{6}\.tar\.gz")
_NFS_SERVER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9.\-]*")
_NFS_PATH_RE = re.compile(r"/[A-Za-z0-9._\-/]*")
_DB_MEMBER = "data/analysis.db"
_DB_SIDECARS = ("-wal", "-shm")
_DB_FILE_NAMES = {"analysis.db", *("analysis.db" + suffix for suffix in _DB_SIDECARS)}
_RESTORE_ROOTS = ("data", "models")
_HISTORY_COLUMNS = (
    "started_at", "finished_at", "trigger_type", "location_type", "destination",
    "components", "filename", "size_bytes", "status", "error_message", "sha256",
)


class RestoreRefused(ValueError):
    pass


def _exclude_db_filter(tarinfo: tarfile.TarInfo):
    if Path(tarinfo.name).name in _DB_FILE_NAMES:
        return None
    return tarinfo


def _snapshot_database(destination: Path) -> None:
    source = target = None
    try:
        source = sqlite3.connect(DB_PATH, timeout=20.0)
        target = sqlite3.connect(destination)
        source.backup(target)
    finally:
        for conn in (target, source):
            if conn:
                conn.close()


def check_local_backup_path(raw: Optional[str]) -> Path:
    target = Path((raw or "").strip() or "backups")
    if not target.is_absolute():
        target = BASE_DIR / target
    resolved = target.resolve()
    if not resolved.is_relative_to(BASE_DIR.resolve()):
        raise ValueError("Local backup folder must be inside the application directory.")
    for archived_dir in (DATA_DIR, MODELS_DIR):
        if resolved.is_relative_to(archived_dir.resolve()):
            raise ValueError("Local backup folder must not be inside data/ or models/, which the archive itself contains.")
    return resolved


def check_nfs_server(raw: Optional[str]) -> str:
    server = (raw or "").strip()
    if server and not _NFS_SERVER_RE.fullmatch(server):
        raise ValueError("NFS server must be a host name or IP address.")
    return server


def check_nfs_path(raw: Optional[str]) -> str:
    share_path = (raw or "").strip()
    if share_path and (not _NFS_PATH_RE.fullmatch(share_path) or ".." in share_path.split("/")):
        raise ValueError("NFS export path must be absolute and use only letters, digits, '.', '_', '-' and '/'.")
    return share_path


def _resolve_backup_dir(cfg: dict) -> Path:
    if cfg.get("LOCATION") == "nfs":
        server = check_nfs_server(cfg.get("NFS_SERVER"))
        share_path = check_nfs_path(cfg.get("NFS_PATH"))
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

    target = check_local_backup_path(cfg.get("LOCAL_PATH"))
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


def _list_archives(backup_dir: Path) -> list:
    return sorted(
        (p for p in backup_dir.glob("backup_*.tar.gz")
         if _ARCHIVE_NAME_RE.fullmatch(p.name) and p.is_file() and not p.is_symlink()),
        key=lambda p: p.name, reverse=True,
    )


def _sha256_file(path: Path, copy_to: Optional[Path] = None) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as src, (open(copy_to, "wb") if copy_to else contextlib.nullcontext()) as dst:
        while chunk := src.read(1 << 20):
            digest.update(chunk)
            if dst:
                dst.write(chunk)
    return digest.hexdigest()


def _enforce_retention(backup_dir: Path, retention_count: int) -> None:
    archives = _list_archives(backup_dir)
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
            "components, filename, size_bytes, status, error_message, sha256) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                fields["started_at"], fields.get("finished_at"), fields["trigger_type"], fields["location_type"],
                fields.get("destination"), fields.get("components"), fields.get("filename"),
                fields.get("size_bytes"), fields["status"], fields.get("error_message"), fields.get("sha256"),
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
        with tempfile.TemporaryDirectory(dir=BASE_DIR, prefix=".backup_") as work_dir, \
                tarfile.open(archive_path, "w:gz") as tar:
            if "data" in components and DATA_DIR.exists():
                tar.add(DATA_DIR, arcname="data", filter=_exclude_db_filter)
            if "models" in components and MODELS_DIR.exists():
                tar.add(MODELS_DIR, arcname="models")
            if "database" in components and DB_PATH.exists():
                snapshot = Path(work_dir) / "analysis.db"
                _snapshot_database(snapshot)
                tar.add(snapshot, arcname=_DB_MEMBER)

        size_bytes = archive_path.stat().st_size
        sha256 = _sha256_file(archive_path)
        _enforce_retention(backup_dir, int(cfg.get("RETENTION_COUNT", 7) or 7))
        destination = _destination_label(cfg, backup_dir)

        finished_at = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        _record_backup_history(
            started_at=started_at, finished_at=finished_at, trigger_type=trigger_type,
            location_type=location_type, destination=destination, components=",".join(components),
            filename=filename, size_bytes=size_bytes, status="success", error_message=None, sha256=sha256,
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
        hashed = _hashed_filenames()
        backups = []
        for p in _list_archives(backup_dir):
            stat = p.stat()
            backups.append({
                "filename": p.name,
                "size_bytes": stat.st_size,
                "mtime": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
                "hash_recorded": p.name in hashed,
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
        cursor.execute(
            "SELECT * FROM backup_history WHERE trigger_type IN ('scheduled', 'manual') ORDER BY id DESC LIMIT 1"
        )
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


def _hashed_filenames() -> set:
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT DISTINCT filename FROM backup_history WHERE sha256 IS NOT NULL AND status = 'success'"
        ).fetchall()
        return {r["filename"] for r in rows}
    finally:
        if conn:
            conn.close()


def _recorded_sha256(filename: str) -> Optional[str]:
    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT sha256 FROM backup_history WHERE filename = ? AND sha256 IS NOT NULL AND status = 'success' "
            "ORDER BY id DESC LIMIT 1",
            (filename,),
        ).fetchone()
        return row["sha256"] if row else None
    finally:
        if conn:
            conn.close()


def _vet_members(tar: tarfile.TarFile) -> list:
    members = tar.getmembers()
    for m in members:
        parts = PurePosixPath(m.name).parts
        if not parts or m.name.startswith("/") or ".." in parts or parts[0] not in _RESTORE_ROOTS:
            raise RestoreRefused("The archive contains files outside data/ and models/; restore refused.")
        if not (m.isreg() or m.isdir()) or (len(parts) == 1 and not m.isdir()):
            raise RestoreRefused("The archive contains links or special files; restore refused.")
    return members


def _stage_database(tar: tarfile.TarFile, member: tarfile.TarInfo) -> Path:
    fd, name = tempfile.mkstemp(dir=DB_PATH.parent, prefix=".restore_", suffix=".db")
    staged = Path(name)
    try:
        with os.fdopen(fd, "wb") as out, tar.extractfile(member) as src:
            while chunk := src.read(1 << 20):
                out.write(chunk)
        conn = None
        try:
            conn = sqlite3.connect(f"file:{quote(str(staged))}?mode=ro&immutable=1", uri=True)
            verdict = conn.execute("PRAGMA integrity_check").fetchone()
        except sqlite3.DatabaseError:
            verdict = None
        finally:
            if conn:
                conn.close()
        if not verdict or verdict[0] != "ok":
            raise RestoreRefused("The database in the archive failed the SQLite integrity check; restore refused.")
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    return staged


def _swap_database(staged: Path) -> None:
    sidecars = [Path(str(DB_PATH) + suffix) for suffix in _DB_SIDECARS]
    for sidecar in sidecars:
        sidecar.unlink(missing_ok=True)
    os.replace(staged, DB_PATH)
    for sidecar in sidecars:
        sidecar.unlink(missing_ok=True)


def _read_backup_history() -> list:
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(f"SELECT {', '.join(_HISTORY_COLUMNS)} FROM backup_history ORDER BY id").fetchall()
        return [dict(r) for r in rows]
    finally:
        if conn:
            conn.close()


def _merge_backup_history(rows: list) -> None:
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=20.0)
        columns = {r[1] for r in conn.execute("PRAGMA table_info(backup_history)")}
        if "sha256" not in columns:
            conn.execute("ALTER TABLE backup_history ADD COLUMN sha256 TEXT")
        known = {(r[0], r[1]) for r in conn.execute("SELECT started_at, filename FROM backup_history")}
        for row in rows:
            if (row["started_at"], row["filename"]) not in known:
                conn.execute(
                    f"INSERT INTO backup_history ({', '.join(_HISTORY_COLUMNS)}) "
                    f"VALUES ({', '.join('?' * len(_HISTORY_COLUMNS))})",
                    [row.get(c) for c in _HISTORY_COLUMNS],
                )
        conn.commit()
    finally:
        if conn:
            conn.close()


def _verify_archive_hash(filename: str, digest: str, trust_unhashed: bool) -> bool:
    recorded = _recorded_sha256(filename)
    if recorded is None:
        if not trust_unhashed:
            raise RestoreRefused(
                "No SHA-256 is recorded for this archive, so it cannot be verified. "
                "Tick 'Trust this archive' to restore it anyway."
            )
        logger.warning("Restoring %s without a recorded hash on operator override (sha256=%s)", filename, digest)
        return True
    if recorded != digest:
        raise RestoreRefused("The archive's SHA-256 does not match the one recorded when it was created; restore refused.")
    return False


def _apply_archive(local_copy: Path, adopted_row: Optional[dict]) -> None:
    with tarfile.open(local_copy, "r:gz") as tar:
        members = _vet_members(tar)
        db_member = next((m for m in members if m.name == _DB_MEMBER and m.isreg()), None)
        skipped = {_DB_MEMBER, *(_DB_MEMBER + suffix for suffix in _DB_SIDECARS)}
        to_extract = [m for m in members if m.name not in skipped]
        staged = _stage_database(tar, db_member) if db_member else None
        try:
            history = _read_backup_history() if staged else []
            tar.extractall(path=BASE_DIR, members=to_extract, filter="data")
            if staged:
                _swap_database(staged)
                staged = None
                _merge_backup_history(history + ([adopted_row] if adopted_row else []))
            elif adopted_row:
                _record_backup_history(**adopted_row)
        finally:
            if staged:
                staged.unlink(missing_ok=True)


def restore_backup(filename: str, trust_unhashed: bool = False) -> dict:
    if not _ARCHIVE_NAME_RE.fullmatch(filename or ""):
        raise ValueError("Invalid backup filename.")

    cfg = load_config().get("SCHEDULING", {}).get("BACKUP", {})
    try:
        backup_dir = _resolve_backup_dir(cfg)
        archive_path = next((p for p in _list_archives(backup_dir) if p.name == filename), None)
        if archive_path is None:
            raise FileNotFoundError(f"Backup file not found: {filename}")

        with tempfile.TemporaryDirectory(dir=BASE_DIR, prefix=".restore_") as work_dir:
            local_copy = Path(work_dir) / "archive.tar.gz"
            digest = _sha256_file(archive_path, copy_to=local_copy)
            adopted_row = None
            if _verify_archive_hash(filename, digest, trust_unhashed):
                now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                adopted_row = {
                    "started_at": now, "finished_at": now, "trigger_type": "adopted",
                    "location_type": cfg.get("LOCATION", "local"),
                    "destination": _destination_label(cfg, backup_dir), "components": None,
                    "filename": filename, "size_bytes": local_copy.stat().st_size,
                    "status": "success", "error_message": None, "sha256": digest,
                }
            _apply_archive(local_copy, adopted_row)

        msg = f"Restore completed from {filename}. Restart the service so all in-memory caches reload the restored data."
        logger.info("Restore completed from %s", filename)
        notify("backup_status", "Success", msg, level="info")
        return {"status": "success", "message": msg}
    except RestoreRefused as e:
        logger.warning("Restore of %s refused: %s", filename, e)
        notify("backup_status", "Error", f"Restore of {filename} refused: {e}", level="error")
        raise
    except Exception as e:
        logger.error("Restore failed: %s", e, exc_info=True)
        notify("backup_status", "Error", f"Restore failed: {e}", level="error")
        return {"status": "error", "message": "Restore failed. Check server logs for details."}
    finally:
        _release_backup_dir(cfg)
