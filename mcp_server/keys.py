import hashlib
import logging
import secrets
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import List, Optional

import db_mcp_keys
import time_engine
from constants import MCP_KEYS_MAX, MCP_KEY_LAST_USED_WRITE_SECONDS

logger = logging.getLogger(__name__)

_STORED_FORMAT = "%Y-%m-%d %H:%M:%S"
_lock = threading.Lock()
_hashes: Optional[List[tuple]] = None
_last_written: dict = {}
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-key-use")


class KeyLimitReached(Exception):
    pass


class DuplicateKeyName(Exception):
    pass


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


def invalidate() -> None:
    global _hashes
    with _lock:
        _hashes = None
        _last_written.clear()


def _cached_hashes() -> List[tuple]:
    global _hashes
    with _lock:
        if _hashes is None:
            _hashes = db_mcp_keys.key_hashes()
        return _hashes


def has_keys() -> bool:
    return bool(_cached_hashes())


def match(token: str) -> Optional[int]:
    presented = hash_key(token)
    found = None
    for key_id, stored in _cached_hashes():
        if secrets.compare_digest(presented, stored):
            found = key_id
    return found


def _write_use(key_id: int, used_at: str) -> None:
    try:
        db_mcp_keys.touch_key(key_id, used_at)
    except Exception as e:
        logger.warning("Could not record the last use of MCP key %s: %s", key_id, e)


def record_use(key_id: int) -> None:
    now = time.monotonic()
    with _lock:
        previous = _last_written.get(key_id)
        if previous is not None and now - previous < MCP_KEY_LAST_USED_WRITE_SECONDS:
            return
        _last_written[key_id] = now
    _executor.submit(_write_use, key_id, datetime.now(timezone.utc).strftime(_STORED_FORMAT))


def list_keys() -> List[dict]:
    return [
        {"id": row["id"], "name": row["name"], "created_at": time_engine.fmt_stored_utc(row["created_at"]), "last_used_at": time_engine.fmt_stored_utc(row["last_used_at"])}
        for row in db_mcp_keys.list_keys()
    ]


def create_key(name: str) -> dict:
    existing = db_mcp_keys.list_keys()
    if len(existing) >= MCP_KEYS_MAX:
        raise KeyLimitReached
    if any(row["name"].casefold() == name.casefold() for row in existing):
        raise DuplicateKeyName
    key = secrets.token_hex(32)
    key_id = db_mcp_keys.add_key(name, hash_key(key), datetime.now(timezone.utc).strftime(_STORED_FORMAT))
    if key_id is None:
        raise DuplicateKeyName
    invalidate()
    return {"id": key_id, "name": name, "key": key}


def delete_key(key_id: int) -> bool:
    deleted = db_mcp_keys.delete_key(key_id)
    invalidate()
    return deleted
