# tools/network_engine.py
import json
import time
import random
import logging
import traceback
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from curl_cffi import requests as cffi_requests

from database import get_connection
from config import load_config, DATA_DIR
from nextcloud_talk import send_text_message

_IPV6_FAULT_FLAG = DATA_DIR / "ipv6_fault.flag"

logger = logging.getLogger(__name__)

# is_failing is a one-time latch — once True it stays True for the process lifetime; callers skip IPv6 on sight.
GLOBAL_IPV6_STATUS = {
    "is_failing": False,
    "last_error": "",
    "last_fail_time": 0.0,
}
_ipv6_status_lock = threading.Lock()
_latch_initialized = False
_latch_init_lock = threading.Lock()


def _update_ipv6_status(failing: bool, error: str = "", fail_time: float = 0.0) -> None:
    with _ipv6_status_lock:
        GLOBAL_IPV6_STATUS["is_failing"] = failing
        GLOBAL_IPV6_STATUS["last_error"] = error
        GLOBAL_IPV6_STATUS["last_fail_time"] = fail_time


def _maybe_restore_latch() -> None:
    # Pre-sets the latch from the fault flag file so a restarted process silently skips IPv6 during an active fault window.
    global _latch_initialized
    with _latch_init_lock:
        if _latch_initialized:
            return
        _latch_initialized = True
    try:
        if not _IPV6_FAULT_FLAG.exists():
            return
        data = json.loads(_IPV6_FAULT_FLAG.read_text())
        fault_ts = data.get("timestamp", 0)
        age_secs = datetime.now(timezone.utc).timestamp() - fault_ts
        if age_secs < 3600:
            age_mins = int(age_secs // 60)
            _update_ipv6_status(
                failing=True,
                error=f"Latch restored from flag — last hard fault {age_mins}m ago.",
                fail_time=fault_ts,
            )
            logger.warning("IPv6 latch pre-set from flag file: last hard fault was %dm ago — skipping IPv6 this session.", age_mins)
    except Exception as e:
        logger.debug("Could not restore IPv6 latch from flag file: %s", e)


def _clear_yfinance_crumb() -> None:
    """Clear yfinance's singleton crumb/cookie so the next fetch re-authenticates with the current session's cookies.

    Required whenever the underlying curl_cffi session is replaced mid-use: the YfData singleton keeps the old
    crumb (tied to the old session's cookies) and every subsequent Yahoo request returns 401 Invalid Crumb,
    which yfinance misreports as "possibly delisted" for every ticker.
    """
    try:
        from yfinance.data import YfData
        for inst in YfData._instances.values():
            with inst._cookie_lock:
                inst._crumb = None
                inst._cookie = None
        logger.debug("Cleared yfinance crumb cache after session change.")
    except Exception as e:
        logger.debug("Could not clear yfinance crumb cache: %s", e)


def _trigger_fallback_alert(ipv6_address: str, action_context: str, error_summary: str, detailed_trace: str, config: dict) -> None:
    # Atomically check-and-set ensures only the first concurrent caller fires the alert; all others see is_failing=True and return.
    with _ipv6_status_lock:
        if GLOBAL_IPV6_STATUS["is_failing"]:
            logger.warning(f"IPv6 already disabled — suppressing duplicate alert for: {error_summary}")
            return
        GLOBAL_IPV6_STATUS["is_failing"] = True
        GLOBAL_IPV6_STATUS["last_error"] = error_summary
        GLOBAL_IPV6_STATUS["last_fail_time"] = time.time()

    # Survives process restarts; read by _maybe_restore_latch on next startup.
    try:
        _IPV6_FAULT_FLAG.write_text(json.dumps({
            "timestamp": GLOBAL_IPV6_STATUS["last_fail_time"],
            "error": error_summary,
        }))
    except Exception as flag_e:
        logger.debug("Could not write IPv6 fault flag: %s", flag_e)

    try:
        conn = get_connection()
        cursor = conn.cursor()
        msg = (
            f"Critical Fault on {ipv6_address} while accessing Yahoo Finance for '{action_context}'.\n"
            f"Summary: {error_summary}\n"
            f"Details:\n{detailed_trace}"
        )
        cursor.execute(
            "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)",
            ("Network Fault", msg)
        )
        conn.commit()
        conn.close()
    except Exception as db_e:
        logger.error(f"Failed to log network fault to SQLite: {db_e}")

    if config.get("NETWORK_FAULT_NOTIFY_NEXTCLOUD", False):
        alert_msg = (
            f"🚨 **CRITICAL NETWORK FAULT: YAHOO FINANCE** 🚨\n\n"
            f"The custom IPv6 socket (`{ipv6_address}`) experienced a hard failure while fetching data for `{action_context}`.\n"
            f"**Error:** {error_summary}\n\n"
            f"🔄 *IPv6 interface permanently disabled for this session. All subsequent requests will use standard IPv4 routing.*\n\n"
            f"*(Full stack trace has been written to the SQLite system_notifications table.)*"
        )
        try:
            send_text_message(alert_msg, config)
        except Exception as nc_e:
            logger.error(f"Failed to dispatch Nextcloud alert for network fault: {nc_e}")


def _patch_session_with_retries(
    session: cffi_requests.Session,
    action_context: str,
    timeout: int = 30,
    max_retries: int = 3,
) -> None:
    # Applied to the non-IPv6 path to give it the same 429-backoff and transient-retry resilience as the failover session.
    original_request = session.request
    base_delay = 2.0

    def wrapped_request(method, url, **kwargs):
        kwargs.setdefault("timeout", timeout)
        for attempt in range(max_retries + 1):
            try:
                response = original_request(method, url, **kwargs)
                if response.status_code == 429:
                    if attempt < max_retries:
                        sleep_time = (5 * (2 ** attempt)) + random.uniform(0.5, 1.5)
                        logger.warning(
                            f"[HTTP 429] Rate limited by Yahoo during '{action_context}'. "
                            f"Backing off for {sleep_time:.2f}s (Attempt {attempt + 1}/{max_retries})."
                        )
                        time.sleep(sleep_time)
                        continue
                    raise Exception(f"HTTP 429 Max Retries Exceeded. URL: {url}")
                return response
            except Exception as e:
                error_str = str(e)
                is_transient = (
                    type(e).__name__.lower() in {"timeout", "connectiontimeout", "connectionerror"}
                    or any(term in error_str.lower() for term in [
                        "timeout", "timed out", "connection reset", "curl: (28)", "(28)",
                    ])
                )
                if is_transient and attempt < max_retries:
                    sleep_time = (base_delay ** attempt) + random.uniform(0.5, 1.5)
                    logger.warning(
                        f"Transient network error during '{action_context}': {error_str}. "
                        f"Retrying in {sleep_time:.2f}s (Attempt {attempt + 1}/{max_retries})."
                    )
                    time.sleep(sleep_time)
                    continue
                raise

    session.request = wrapped_request


def create_failover_session(ipv6_address: str, action_context: str, config: dict) -> cffi_requests.Session:
    # Impersonates Chrome to bypass Yahoo TLS fingerprinting; monkey-patches request to catch IPv6 bind faults and HTTP 429s.
    session = cffi_requests.Session(impersonate="chrome", interface=ipv6_address)
    session.fallback_triggered = False
    original_request = session.request

    def failover_request(method, url, **kwargs):
        # nonlocal required: without it Python treats `session`/`original_request` as locals (UnboundLocalError at first read). Has bitten us twice.
        nonlocal session, original_request
        global GLOBAL_IPV6_STATUS
        kwargs.setdefault("timeout", 30)
        max_retries = 3
        base_delay = 2.0
        
        for attempt in range(max_retries + 1):
            try:
                response = original_request(method, url, **kwargs)
                
                if response.status_code == 429:
                    if attempt < max_retries:
                        # longer backoff than transient errors: 5s → 10s → 20s
                        sleep_time = (5 * (2 ** attempt)) + random.uniform(0.5, 1.5)
                        logger.warning(f"[HTTP 429] Rate limited by Yahoo on IPv6 '{ipv6_address}'. Backing off for {sleep_time:.2f}s (Attempt {attempt + 1}/{max_retries}).")
                        time.sleep(sleep_time)
                        continue
                    else:
                        raise Exception(f"HTTP 429 Max Retries Exceeded on IPv6 Interface. URL: {url}")
                        
                if not getattr(session, 'fallback_triggered', False):
                    _update_ipv6_status(failing=False)
                    
                return response

            except Exception as e:
                error_str = str(e)
                
                if "Session is closed" in error_str:
                    logger.warning(f"Closed session detected. Rebuilding IPv6 session for '{action_context}' ({ipv6_address})...")
                    session = cffi_requests.Session(impersonate="chrome", interface=ipv6_address)
                    original_request = session.request
                    _clear_yfinance_crumb()
                    continue
                
                # If we already fell back to standard routing and it STILL failed, we are completely offline or hard-banned.
                if getattr(session, 'fallback_triggered', False):
                    raise e
                
                # bind errors (errno 99) are a startup race — IPv6 interface may still be in DAD; retry before hard-faulting.
                is_transient = (
                    type(e).__name__.lower() in {"timeout", "connectiontimeout", "connectionerror"}
                    or any(term in error_str.lower() for term in [
                        "timeout", "timed out", "connection reset", "curl: (28)", "(28)",
                        "bind failed", "cannot assign requested address", "errno 99",
                    ])
                )
                if is_transient and attempt < max_retries:
                    sleep_time = (base_delay ** attempt) + random.uniform(0.5, 1.5)
                    logger.warning(f"Transient network error on IPv6 '{ipv6_address}': {error_str}. Retrying in {sleep_time:.2f}s (Attempt {attempt + 1}/{max_retries}).")
                    time.sleep(sleep_time)
                    continue

                error_summary = f"{type(e).__name__}: {error_str}"
                detailed_trace = (
                    f"Target URL: {method} {url}\n"
                    f"Attempt: {attempt + 1} of {max_retries + 1}\n"
                    f"Stack Trace:\n{traceback.format_exc()}"
                )
                
                logger.error(f"Critical IPv6 Fault during '{action_context}': {error_summary}\n{detailed_trace}")
                # duplicate concurrent callers are no-ops: latch check-and-set is atomic.
                _trigger_fallback_alert(ipv6_address, action_context, error_summary, detailed_trace, config)

                # Build fresh unbound session — mutating session.interface on a live curl_cffi is undocumented and may be silently ignored by libcurl.
                logger.warning(f"Exhausted IPv6 recovery options. Dropping custom interface {ipv6_address} and reverting to OS default routing (IPv4)...")
                session.fallback_triggered = True
                _clear_yfinance_crumb()
                fallback_session = cffi_requests.Session(impersonate="chrome")
                try:
                    return fallback_session.request(method, url, **kwargs)
                except Exception as fallback_exc:
                    logger.error(f"[TOTAL FAILURE] IPv4 fallback also failed for '{action_context}': {fallback_exc}")
                    raise fallback_exc
                finally:
                    fallback_session.close()

    session.request = failover_request
    return session


@contextmanager
def yahoo_connection_boundary(action_context: str):
    # Yields a curl_cffi Session for yfinance: IPv6-bound if configured and healthy, otherwise standard routing.
    _maybe_restore_latch()

    config = load_config()
    ipv6_addr = config.get("YAHOO_IPV6_ADDRESS", "").strip()

    # Skip IPv6 entirely if the latch was tripped by a previous failure this session.
    if GLOBAL_IPV6_STATUS["is_failing"]:
        ipv6_addr = ""

    if not ipv6_addr:
        session = cffi_requests.Session(impersonate="chrome")
        _patch_session_with_retries(session, action_context)
        try:
            yield session
        finally:
            session.close()
        return

    session = create_failover_session(ipv6_addr, action_context, config)
    try:
        yield session
    finally:
        session.close()
