# debug_network.py
import logging
import yfinance as yf
from config import load_config
from tools.network_engine import IPv6SourceAddressAdapter, yahoo_connection_boundary
import requests

# Set logging to see the internal network_engine logs during the test
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def run_diagnostics():
    print("="*70)
    print(" 🌐 IPv6 NETWORK & FAILOVER DIAGNOSTICS")
    print("="*70)

    config = load_config()
    ipv6_addr = config.get("YAHOO_IPV6_ADDRESS", "").strip()

    # ---------------------------------------------------------
    # TEST 1: Configuration Check
    # ---------------------------------------------------------
    print("\n[TEST 1] Checking Configuration...")
    if ipv6_addr:
        print(f"✅ Found YAHOO_IPV6_ADDRESS in config: {ipv6_addr}")
    else:
        print("⚠️ No IPv6 address configured in settings.")
        print("👉 The system will currently default to standard IPv4 OS routing.")
        print("👉 To test IPv6, add an address in the Web UI Settings tab.")

    # ---------------------------------------------------------
    # TEST 2: Strict Socket Binding Test (If Configured)
    # ---------------------------------------------------------
    if ipv6_addr:
        print(f"\n[TEST 2] Testing Strict IPv6 Socket Binding to {ipv6_addr}...")
        test_session = requests.Session()
        adapter = IPv6SourceAddressAdapter(source_address=ipv6_addr)
        test_session.mount("https://", adapter)
        test_session.mount("http://", adapter)
        
        try:
            # Force a short timeout so we don't hang forever if the route is dead
            tk = yf.Ticker("SPY", session=test_session)
            df = tk.history(period="1d")
            
            if not df.empty:
                print(f"✅ SUCCESS: Data downloaded successfully using strictly {ipv6_addr}.")
            else:
                print("❌ WARNING: Connection established, but Yahoo returned empty data.")
        except Exception as e:
            print(f"❌ ERROR: Strict binding failed. Your OS cannot route traffic through {ipv6_addr}.")
            print(f"   Details: {e}")
        finally:
            test_session.close()
    else:
        print("\n[TEST 2] Skipped (No IPv6 address configured).")

    # ---------------------------------------------------------
    # TEST 3: The Self-Healing Failover Simulation
    # ---------------------------------------------------------
    print("\n[TEST 3] Simulating a Network Fault to Test Self-Healing Failover...")
    
    # We temporarily inject a universally dead "documentation" IPv6 address into the config 
    # to guarantee a connection failure and force the failover logic to trigger.
    dead_ipv6 = "2001:db8::dead:beef"
    config["YAHOO_IPV6_ADDRESS"] = dead_ipv6 
    
    print(f"   -> Forcing engine to use dead IP: {dead_ipv6}")
    
    # We must patch the load_config function temporarily for this test scope
    # so the connection boundary reads our dead IP.
    import config as config_module
    original_load = config_module.load_config
    config_module.load_config = lambda: config

    try:
        # This SHOULD trigger the fallback, drop the dead IPv6, print an error to the log,
        # send you a Nextcloud alert, and then successfully download the data via IPv4.
        with yahoo_connection_boundary("Diagnostic Failover Test") as session:
            tk = yf.Ticker("QQQ", session=session)
            df = tk.history(period="1d")
            
            if not df.empty:
                print("✅ FAILOVER SUCCESS: The engine caught the dead IPv6 address, dropped it, and rescued the download via standard routing!")
                print("   -> Check your Nextcloud Talk app; you should have received a Network Fault alert.")
            else:
                print("❌ FAILOVER WARNING: The failover executed, but the rescue download returned empty data.")
    except Exception as e:
        print(f"❌ FAILOVER FATAL ERROR: The system crashed and failed to self-heal.")
        print(f"   Details: {e}")
    finally:
        # Restore original config behavior
        config_module.load_config = original_load

    print("\n" + "="*70)
    print(" DIAGNOSTICS COMPLETE")
    print("="*70 + "\n")


if __name__ == "__main__":
    run_diagnostics()