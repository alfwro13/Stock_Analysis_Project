import logging
from concurrent.futures import ThreadPoolExecutor

from constants import MCP_AUTH_FAILURES_MAX, MCP_AUTH_LOCKOUT_SECONDS, MCP_NOTIFY_COOLDOWN_SECONDS
from notification_engine import notify
from utils import clean_display_text

from . import limits

logger = logging.getLogger(__name__)

SOURCE = "mcp_remote_access_alert"
MESSAGE_TYPE = "MCP Remote Access"
_ADDRESS_MAX_CHARS = 64
_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="mcp-remote-notify")


def _send(text: str, level: str) -> None:
    try:
        notify(SOURCE, MESSAGE_TYPE, text, nextcloud_text=f"{MESSAGE_TYPE}: {text}", level=level)
    except Exception as e:
        logger.warning("Could not send an MCP Remote Access notification: %s", e)


def _emit(text: str, level: str = "info") -> None:
    # Off the caller's thread: a Nextcloud Talk call must never delay an OAuth or /mcp response (rule 10).
    _executor.submit(_send, text, level)


def _address(address: str) -> str:
    return clean_display_text(address, _ADDRESS_MAX_CHARS) or "unknown"


def request_waiting(display_code: str, address: str) -> None:
    _emit(f"A connection request (code {display_code}) from {_address(address)} is waiting for approval.")


def request_approved(display_code: str) -> None:
    _emit(f"Connection request {display_code} was approved.")


def refresh_reused() -> None:
    _emit("A refresh token was used twice; the connected app was signed out.", "warning")


def lockout_tripped(address: str) -> None:
    if limits.notify_due(("lockout", address), MCP_NOTIFY_COOLDOWN_SECONDS):
        minutes = MCP_AUTH_LOCKOUT_SECONDS // 60
        _emit(f"{_address(address)} was locked out for {minutes} minutes after {MCP_AUTH_FAILURES_MAX} wrong credentials.", "warning")


def off_range_request(address: str) -> None:
    if limits.notify_due(("off_range", address), MCP_NOTIFY_COOLDOWN_SECONDS):
        _emit(f"A request from {_address(address)} was refused: outside the allowed address range.", "warning")


def window_closed_unused() -> None:
    _emit("The pairing window closed without a connection request.")
