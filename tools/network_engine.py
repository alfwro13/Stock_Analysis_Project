# tools/network_engine.py
import time
import random
import logging
import traceback
from contextlib import contextmanager
from curl_cffi import requests as cffi_requests

from database import get_connection
from config import load_config
from nextcloud_talk import send_text_message

logger = logging.getLogger(__name__)

# Global state to track IPv6 health for the UI Settings page
GLOBAL_IPV6_STATUS = {
    "is_failing": False,
    "last_error": "",
    "last_fail_time": 0.0
}

def _trigger_fallback_alert(ipv6_address: str, action_context: str, error_summary: str, detailed_trace: str) -> None:
    """
    Handles logging the network fault to SQLite and dispatching an alert to Nextcloud.
    Separates the concise summary for chat from the heavy traceback for the database.
    """
    config = load_config()
    
    # 1. Database Persistence (Heavy Traceback Logging)
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

    # 2. Nextcloud Talk Alert (Concise, Markdown formatted)
    alert_msg = (
        f"🚨 **CRITICAL NETWORK FAULT: YAHOO FINANCE** 🚨\n\n"
        f"The custom IPv6 socket (`{ipv6_address}`) experienced a hard failure while fetching data for `{action_context}`.\n"
        f"**Error:** {error_summary}\n\n"
        f"🔄 *System is dropping the IPv6 interface and hopping to standard IPv4 routing to rescue the pipeline.*\n\n"
        f"*(Note: Full stack trace and URL details have been written to the SQLite system_notifications table for debugging.)*"
    )
    
    # Fire and forget to Nextcloud
    try:
        send_text_message(alert_msg, config)
    except Exception as nc_e:
        logger.error(f"Failed to dispatch Nextcloud alert for network fault: {nc_e}")


def create_failover_session(ipv6_address: str, action_context: str) -> cffi_requests.Session:
    """
    Builds a true curl_cffi Session to satisfy yfinance >= 1.x requirements,
    while monkey-patching the request method to intercept binding faults AND HTTP 429s.
    Impersonates Chrome to bypass Yahoo TLS fingerprinting.
    """
    global GLOBAL_IPV6_STATUS
    session = cffi_requests.Session(impersonate="chrome", interface=ipv6_address)
    session.fallback_triggered = False
    
    # Store the original underlying request logic
    original_request = session.request

    def failover_request(method, url, **kwargs):
        global GLOBAL_IPV6_STATUS
        max_retries = 3
        base_delay = 2.0
        
        for attempt in range(max_retries + 1):
            try:
                response = original_request(method, url, **kwargs)
                
                # Intercept HTTP 429 (Too Many Requests) BEFORE yfinance sees it
                if response.status_code == 429:
                    if attempt < max_retries:
                        # 429-specific backoff: 5s, 10s, 20s — longer than transient errors
                        sleep_time = (5 * (2 ** attempt)) + random.uniform(0.5, 1.5)
                        logger.warning(f"[HTTP 429] Rate limited by Yahoo on IPv6 '{ipv6_address}'. Backing off for {sleep_time:.2f}s (Attempt {attempt + 1}/{max_retries}).")
                        time.sleep(sleep_time)
                        continue
                    else:
                        raise Exception(f"HTTP 429 Max Retries Exceeded on IPv6 Interface. URL: {url}")
                        
                # If we succeed on IPv6, mark the global status as healthy
                if not session.fallback_triggered:
                    GLOBAL_IPV6_STATUS["is_failing"] = False
                    
                return response

            except Exception as e:
                error_str = str(e)
                
                # --- 1. STRICT IPv6 LAZY-LOAD RESCUE ---
                if "Session is closed" in error_str:
                    logger.warning(f"Closed session detected. Rebuilding IPv6 session for '{action_context}' ({ipv6_address})...")
                    session = cffi_requests.Session(impersonate="chrome", interface=ipv6_address)
                    original_request = session.request
                    continue
                
                # If we already fell back to standard routing and it STILL failed, we are completely offline or hard-banned.
                if getattr(session, 'fallback_triggered', False):
                    raise e
                
                # --- 2. TRANSIENT TIMEOUT HANDLING ---
                # Do NOT drop the IPv6 interface for a simple timeout. Retry on IPv6.
                is_transient = any(term in error_str.lower() for term in ["timeout", "connection reset", "code: 28"])
                if is_transient and attempt < max_retries:
                    sleep_time = (base_delay ** attempt) + random.uniform(0.5, 1.5)
                    logger.warning(f"Transient network error on IPv6 '{ipv6_address}': {error_str}. Retrying in {sleep_time:.2f}s (Attempt {attempt + 1}/{max_retries}).")
                    time.sleep(sleep_time)
                    continue

                # --- 3. HARD FAULT / IPv4 FAILOVER ---
                # We only reach this point if it's a definitive binding error, or if retries are exhausted.
                error_summary = f"{type(e).__name__}: {error_str}"
                detailed_trace = (
                    f"Target URL: {method} {url}\n"
                    f"Attempt: {attempt + 1} of {max_retries + 1}\n"
                    f"Stack Trace:\n{traceback.format_exc()}"
                )
                
                logger.error(f"Critical IPv6 Fault during '{action_context}': {error_summary}\n{detailed_trace}")
                _trigger_fallback_alert(ipv6_address, action_context, error_summary, detailed_trace)
                
                # Update Global Status for UI Dashboard
                GLOBAL_IPV6_STATUS["is_failing"] = True
                GLOBAL_IPV6_STATUS["last_error"] = error_summary
                GLOBAL_IPV6_STATUS["last_fail_time"] = time.time()
                
                # Graceful Fallback: Drop the interface binding natively in libcurl
                logger.warning(f"Exhausted IPv6 recovery options. Dropping custom interface {ipv6_address} and reverting to OS default routing (IPv4)...")
                session.interface = None
                session.fallback_triggered = True
                
                # Rescue the pipeline by executing the request over standard routing
                try:
                    return original_request(method, url, **kwargs)
                except Exception as fallback_exc:
                    logger.error(f"[TOTAL FAILURE] IPv4 fallback also failed for '{action_context}': {fallback_exc}")
                    raise fallback_exc

    # Monkey-patch the request method to our self-healing wrapper
    session.request = failover_request
    return session


@contextmanager
def yahoo_connection_boundary(action_context: str):
    """
    Context manager that yields a robust, self-healing curl_cffi Session 
    for use with yfinance. Uses IPv6 if configured, else standard routing.
    
    Usage:
        with yahoo_connection_boundary("Daily Download") as session:
            df = yf.download("AAPL", session=session)
    """
    config = load_config()
    ipv6_addr = config.get("YAHOO_IPV6_ADDRESS", "").strip()

    if not ipv6_addr:
        # Bypass custom socket binding if no IPv6 is configured in the UI
        session = cffi_requests.Session(impersonate="chrome")
        try:
            yield session
        finally:
            session.close()
        return

    # Initialize the self-healing patched session
    session = create_failover_session(ipv6_addr, action_context)
    try:
        yield session
    finally:
        session.close()
