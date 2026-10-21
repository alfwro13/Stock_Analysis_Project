import json
import logging
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Optional, Tuple

import db_mcp_audit
from constants import MCP_AUDIT_ADDRESS_MAX_CHARS, MCP_AUDIT_QUEUE_MAX, MCP_AUDIT_TEXT_MAX_CHARS
from utils import clean_display_text

logger = logging.getLogger(__name__)

_STORED_FORMAT = "%Y-%m-%d %H:%M:%S"
_NAMED_METHODS = {"tools/call": "name", "prompts/get": "name", "resources/read": "uri"}
_lock = threading.Lock()
_pending = 0
_dropped = 0
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


def record(key_id: Optional[int], address: str, method: Optional[str], name: Optional[str], status: int, duration_ms: int, response_bytes: int) -> None:
    global _pending, _dropped
    entry = {
        "logged_at": datetime.now(timezone.utc).strftime(_STORED_FORMAT),
        "credential_kind": "key" if key_id is not None else "none",
        "key_id": key_id,
        "client_address": clean_display_text(address, MCP_AUDIT_ADDRESS_MAX_CHARS),
        "mcp_method": clean_display_text(method, MCP_AUDIT_TEXT_MAX_CHARS),
        "mcp_name": clean_display_text(name, MCP_AUDIT_TEXT_MAX_CHARS),
        "status": status,
        "duration_ms": duration_ms,
        "response_bytes": response_bytes,
    }
    with _lock:
        if _pending >= MCP_AUDIT_QUEUE_MAX:
            _dropped += 1
            if _dropped == 1 or _dropped % 100 == 0:
                logger.warning("MCP audit queue is full: %d entries dropped so far", _dropped)
            return
        _pending += 1
    _executor.submit(_write, entry)
