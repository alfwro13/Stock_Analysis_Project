# network_engine.py
import logging
import urllib3
import requests
from contextlib import contextmanager

from database import get_connection
from config import load_config
from nextcloud_talk import send_text_message

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - NETWORK_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)


class IPv6SourceAddressAdapter(requests.adapters.HTTPAdapter):
    """
    A custom HTTP adapter that binds all outbound socket connections 
    to a specific IPv6 address.
    """
    def __init__(self, source_address: str, **kwargs):
        self.source_address = source_address
        super().__init__(**kwargs)

    def init_poolmanager(self, connections, maxsize, block=False, **pool_kwargs):
        self.poolmanager = urllib3.poolmanager.PoolManager(
            num_pools=connections,
            maxsize=maxsize,
            block=block,
            source_address=(self.source_address, 0),
            **pool_kwargs
        )

    def proxy_manager_for(self, proxy, **proxy_kwargs):
        proxy_kwargs['source_address'] = (self.source_address, 0)
        return super().proxy_manager_for(proxy, **proxy_kwargs)


class YahooFailoverSession(requests.Session):
    """
    A resilient Requests Session that attempts IPv6 binding but gracefully 
    falls back to native OS routing (IPv4) upon encountering socket/timeout errors.
    """
    def __init__(self, ipv6_address: str, action_context: str):
        super().__init__()
        self.ipv6_address = ipv6_address
        self.action_context = action_context
        self.fallback_triggered = False
        
        # Bind the custom IPv6 adapter
        adapter = IPv6SourceAddressAdapter(source_address=ipv6_address)
        self.mount("https://", adapter)
        self.mount("http://", adapter)

    def request(self, method, url, **kwargs):
        try:
            return super().request(method, url, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            if self.fallback_triggered:
                # If we already fell back to standard routing and it still failed, raise normally
                raise e
            
            logger.error(f"IPv6 Socket Error during '{self.action_context}': {e}")
            self._trigger_fallback_alert(str(e))
            
            # Graceful Fallback: Drop the IPv6 adapter entirely
            logger.info("Dropping custom IPv6 adapter. Reverting to standard native OS routing (IPv4)...")
            self.mount("https://", requests.adapters.HTTPAdapter())
            self.mount("http://", requests.adapters.HTTPAdapter())
            self.fallback_triggered = True
            
            # Retry the exact same request seamlessly using the standard network route
            return super().request(method, url, **kwargs)

    def _trigger_fallback_alert(self, error_msg: str):
        config = load_config()
        
        # 1. Database Persistence
        try:
            conn = get_connection()
            cursor = conn.cursor()
            msg = f"IPv6 network fault on {self.ipv6_address} while accessing Yahoo Finance for '{self.action_context}'."
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
            f"The custom IPv6 socket binding (`{self.ipv6_address}`) failed while attempting to fetch data for `{self.action_context}`.\n"
            f"**Error:** {error_msg}\n\n"
            f"🔄 *System is automatically falling back to standard IPv4 routing to prevent pipeline crash.*"
        )
        
        # Fire and forget to Nextcloud
        try:
            send_text_message(alert_msg, config)
        except Exception as nc_e:
            logger.error(f"Failed to dispatch Nextcloud alert for network fault: {nc_e}")


@contextmanager
def yahoo_connection_boundary(action_context: str):
    """
    Context manager that yields a robust, self-healing Requests Session 
    for use with yfinance. Uses IPv6 if configured, else standard routing.
    
    Usage:
        with yahoo_connection_boundary("Daily Download") as session:
            df = yf.download("AAPL", session=session)
    """
    config = load_config()
    ipv6_addr = config.get("YAHOO_IPV6_ADDRESS", "").strip()

    if not ipv6_addr:
        # Bypass custom socket binding if no IPv6 is configured in the UI
        yield requests.Session()
        return

    # Initialize the self-healing session
    session = YahooFailoverSession(ipv6_address=ipv6_addr, action_context=action_context)
    
    try:
        yield session
    finally:
        session.close()