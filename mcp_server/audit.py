import json
import logging
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional, Tuple

import db_mcp_audit
from constants import MCP_ACTIVITY_ROWS, MCP_AUDIT_ADDRESS_MAX_CHARS, MCP_AUDIT_QUEUE_MAX, MCP_AUDIT_TEXT_MAX_CHARS, MCP_AUDIT_UNAUTH_ROWS_PER_MINUTE
from utils import clean_display_text

logger = logging.getLogger(__name__)

_STORED_FORMAT = "%Y-%m-%d %H:%M:%S"
ACTION_METHOD = "dashboard"
_NAMED_METHODS = {"tools/call": "name", "prompts/get": "name", "resources/read": "uri"}
_WINDOW_SECONDS = 60
_lock = threading.Lock()
_pending = 0
_dropped = 0
_window_started: Optional[float] = None
_window_stamp = ""
_window_rows = 0
_suppressed = 0
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-audit")


def describe_request(body: bytes) -> Tuple[Optional[str], Optional[str]]:
    """JSON-RPC method and the tool, prompt or resource name only; argument values are never read."""
    try:
        message = json.loads(body)
    except (ValueError, RecursionError):
        return None, None
    if isinstance(message, list):
        return "batch", None
    if not isinstance(message, dict) or not isinstance(message.get("method"), str):
        return None, None
    method = message["method"]
    params = message.get("params")
    field = _NAMED_METHODS.get(method)
    name = params.get(field) if field and isinstance(params, dict) else None
    return method, name if isinstance(name, str) else None


def _write(entry: dict) -> None:
    global _pending
    try:
        db_mcp_audit.add_entry(entry)
    except Exception as e:
        logger.warning("Could not write an MCP audit entry: %s", e)
    finally:
        with _lock:
            _pending -= 1


def _now() -> float:
    return time.monotonic()


def _submit(entry: dict) -> None:
    global _pending, _dropped
    with _lock:
        if _pending >= MCP_AUDIT_QUEUE_MAX:
            _dropped += 1
            if _dropped == 1 or _dropped % 100 == 0:
                logger.warning("MCP audit queue is full: %d entries dropped so far", _dropped)
            return
        _pending += 1
    _executor.submit(_write, entry)


def _roll_window_locked(now: float, stamp: str) -> Optional[dict]:
    """Starts a new counting window when the last one ended; returns the summary row of the closed window if it suppressed any row."""
    global _window_started, _window_stamp, _window_rows, _suppressed
    if _window_started is not None and now - _window_started < _WINDOW_SECONDS:
        return None
    summary = None
    if _suppressed:
        summary = {
            "logged_at": _window_stamp, "credential_kind": "none", "key_id": None, "client_address": None, "mcp_method": "audit",
            "mcp_name": f"{_suppressed} rows without a credential not stored", "status": 0, "duration_ms": 0, "response_bytes": 0,
        }
    _window_started, _window_stamp, _window_rows, _suppressed = now, stamp, 0, 0
    return summary


def _admit_locked(unauthenticated: bool) -> bool:
    global _window_rows, _suppressed
    if not unauthenticated:
        return True
    if _window_rows < MCP_AUDIT_UNAUTH_ROWS_PER_MINUTE:
        _window_rows += 1
        return True
    if not _suppressed:
        logger.warning(
            "More than %d MCP requests without a valid credential in a minute: the rest are counted in one audit row, not stored",
            MCP_AUDIT_UNAUTH_ROWS_PER_MINUTE,
        )
    _suppressed += 1
    return False


def reset() -> None:
    global _window_started, _window_stamp, _window_rows, _suppressed
    with _lock:
        _window_started, _window_stamp, _window_rows, _suppressed = None, "", 0, 0


def _store(entry: dict) -> None:
    with _lock:
        summary = _roll_window_locked(_now(), entry["logged_at"])
        admitted = _admit_locked(entry["credential_kind"] == "none")
    if summary is not None:
        _submit(summary)
    if admitted:
        _submit(entry)


def record(
    key_id: Optional[int], address: str, method: Optional[str], name: Optional[str], status: int, duration_ms: int, response_bytes: int,
    grant_id: Optional[int] = None,
) -> None:
    _store({
        "logged_at": datetime.now(timezone.utc).strftime(_STORED_FORMAT),
        "credential_kind": "oauth" if grant_id is not None else "key" if key_id is not None else "none",
        "key_id": grant_id if grant_id is not None else key_id,
        "client_address": clean_display_text(address, MCP_AUDIT_ADDRESS_MAX_CHARS),
        "mcp_method": clean_display_text(method, MCP_AUDIT_TEXT_MAX_CHARS),
        "mcp_name": clean_display_text(name, MCP_AUDIT_TEXT_MAX_CHARS),
        "status": status,
        "duration_ms": duration_ms,
        "response_bytes": response_bytes,
    })


def record_action(name: str, address: Optional[str] = None, grant_id: Optional[int] = None) -> None:
    """A dashboard action on remote access (revoke, restore). Kind `session` is never counted against the cap on rows without a credential."""
    _store({
        "logged_at": datetime.now(timezone.utc).strftime(_STORED_FORMAT),
        "credential_kind": "session",
        "key_id": grant_id,
        "client_address": clean_display_text(address, MCP_AUDIT_ADDRESS_MAX_CHARS) if address else None,
        "mcp_method": ACTION_METHOD,
        "mcp_name": clean_display_text(name, MCP_AUDIT_TEXT_MAX_CHARS),
        "status": 200,
        "duration_ms": 0,
        "response_bytes": 0,
    })


def recent_events() -> list:
    return db_mcp_audit.recent_entries(MCP_ACTIVITY_ROWS)
