# tools/network_engine.py
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
        msg = f"IPv6 network fault on {ipv6_address} while accessing Yahoo Finance for '{action_context}'."
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
        f"🚨 **NETWORK FAULT: YAHOO FINANCE IPv6** 🚨\n\n"
        f"The custom IPv6 socket binding (`{ipv6_address}`) failed while attempting to fetch data for `{action_context}`.\n"
        f"**Error:** {error_msg}\n\n"
        f"🔄 *System is automatically falling back to standard IPv4 routing to prevent pipeline crash.*"
    )
    
    # Fire and forget to Nextcloud
    try:
        send_text_message(alert_msg, config)
    except Exception as nc_e:
        logger.error(f"Failed to dispatch Nextcloud alert for network fault: {nc_e}")


def create_failover_session(ipv6_address: str, action_context: str) -> cffi_requests.Session:
    """
    Builds a true curl_cffi Session to satisfy yfinance >= 1.x requirements,
    while monkey-patching the request method to intercept binding faults.
    Impersonates Chrome to bypass Yahoo TLS fingerprinting.
    """
    # Instantiate pure session to pass strict yfinance type checks
    session = cffi_requests.Session(impersonate="chrome", interface=ipv6_address)
    session.fallback_triggered = False
    
    # Store the original underlying request logic
    original_request = session.request

    def failover_request(method, url, **kwargs):
        try:
            return original_request(method, url, **kwargs)
        except Exception as e:
            # Capture curl_cffi.requests.errors.RequestsError and socket faults
            if getattr(session, 'fallback_triggered', False):
                # If we already fell back to standard routing and it still failed, raise normally
                raise e
            
            logger.error(f"IPv6 Socket Error during '{action_context}': {e}")
            _trigger_fallback_alert(ipv6_address, action_context, str(e))
            
            # Graceful Fallback: Drop the interface binding natively in libcurl
            logger.info("Dropping custom IPv6 interface. Reverting to standard native OS routing (IPv4)...")
            session.interface = None
            session.fallback_triggered = True
            
            # Retry the exact same request seamlessly using the standard network route
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