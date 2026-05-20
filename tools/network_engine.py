# tools/network_engine.py
import time
import random
import logging
from contextlib import contextmanager
from curl_cffi import requests as cffi_requests

from database import get_connection
from config import load_config
from nextcloud_talk import send_text_message

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - NETWORK_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


def _trigger_fallback_alert(ipv6_address: str, action_context: str, error_msg: str) -> None:
    config = load_config()
    
    # 1. Database Persistence
    try:
        conn = get_connection()
        cursor = conn.cursor()
        msg = f"Network fault or IP Ban on {ipv6_address} while accessing Yahoo Finance for '{action_context}'."
        cursor.execute(
            "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)",
            ("Network Fault", msg)
        )
        conn.commit()
        conn.close()
    except Exception as db_e:
        logger.error(f"Failed to log network fault to SQLite: {db_e}")

    # 2. Nextcloud Talk Alert
    alert_msg = (
        f"🚨 **NETWORK FAULT / IP BAN: YAHOO FINANCE** 🚨\n\n"
        f"The custom IPv6 socket (`{ipv6_address}`) failed or was rate-limited (HTTP 429) while fetching data for `{action_context}`.\n"
        f"**Error Details:** {error_msg}\n\n"
        f"🔄 *System is automatically dropping the IPv6 interface and hopping to standard IPv4 routing to rescue the pipeline.*"
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
    session = cffi_requests.Session(impersonate="chrome", interface=ipv6_address)
    session.fallback_triggered = False
    
    # Store the original underlying request logic
    original_request = session.request

    def failover_request(method, url, **kwargs):
        max_retries = 3
        base_delay = 2.0
        
        for attempt in range(max_retries + 1):
            try:
                response = original_request(method, url, **kwargs)
                
                # Intercept HTTP 429 (Too Many Requests) BEFORE yfinance sees it
                if response.status_code == 429:
                    if attempt < max_retries:
                        # Exponential backoff (2s, 4s, 8s) + random jitter (0.5s to 1.5s)
                        sleep_time = (base_delay ** attempt) + random.uniform(0.5, 1.5)
                        logger.warning(f"[HTTP 429] Rate limited by Yahoo during '{action_context}'. Backing off for {sleep_time:.2f}s (Attempt {attempt + 1}/{max_retries}).")
                        time.sleep(sleep_time)
                        continue  # Retry the loop
                    else:
                        # We exhausted retries on IPv6. Raise an exception to trigger the IPv4 failover!
                        raise Exception("HTTP 429 Max Retries Exceeded on IPv6 Interface.")
                        
                return response

            except Exception as e:
                # Capture curl_cffi.requests.errors.RequestsError, socket faults, and our 429 exception
                if getattr(session, 'fallback_triggered', False):
                    # If we already fell back to standard routing and it STILL failed, we are hard-banned or offline. Raise normally.
                    raise e
                
                logger.error(f"Network/Socket Fault during '{action_context}': {e}")
                _trigger_fallback_alert(ipv6_address, action_context, str(e))
                
                # Graceful Fallback: Drop the interface binding natively in libcurl
                logger.info("Dropping custom IPv6 interface. Reverting to standard native OS routing (IPv4)...")
                session.interface = None
                session.fallback_triggered = True
                
                # Rescue the pipeline by executing the request over IPv4
                return original_request(method, url, **kwargs)

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