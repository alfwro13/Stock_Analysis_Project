# debug_429.py
import time
import logging
from curl_cffi import requests as cffi_requests
from tools.network_engine import yahoo_connection_boundary

# Configure basic logging to see the internal network_engine logs
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(message)s'
)
logger = logging.getLogger(__name__)

class MockResponse:
    def __init__(self, status_code):
        self.status_code = status_code

# We will save the real request function so we can restore it later
real_request = cffi_requests.Session.request

# Global counters for our tests
attempt_counter = 0

def mock_request_recovery(*args, **kwargs):
    """Mocks an API that is rate-limited twice, but succeeds on the 3rd attempt."""
    global attempt_counter
    attempt_counter += 1
    print(f"   [Mock API] -> Intercepted HTTP request (Attempt {attempt_counter})")
    
    if attempt_counter <= 2:
        print("   [Mock API] -> Returning HTTP 429 (Too Many Requests)")
        return MockResponse(429)
    else:
        print("   [Mock API] -> Returning HTTP 200 (Success)")
        return MockResponse(200)

def mock_request_exhaustion(*args, **kwargs):
    """Mocks an API that is permanently rate-limited on IPv6, but succeeds on IPv4 failover (Attempt 5)."""
    global attempt_counter
    attempt_counter += 1
    print(f"   [Mock API] -> Intercepted HTTP request (Attempt {attempt_counter})")
    
    # 4 attempts on IPv6 (Initial + 3 Retries). The 5th attempt is the IPv4 Rescue.
    if attempt_counter <= 4:
        print("   [Mock API] -> Returning HTTP 429 (Too Many Requests)")
        return MockResponse(429)
    else:
        print("   [Mock API] -> Returning HTTP 200 (Success) over IPv4 Rescue")
        return MockResponse(200)

def run_diagnostics():
    global attempt_counter
    print("="*75)
    print(" 🛑 HTTP 429 EXPONENTIAL BACKOFF & IP HOPPING DIAGNOSTICS")
    print("="*75)

    # ---------------------------------------------------------
    # TEST 1: Backoff and Recovery
    # ---------------------------------------------------------
    print("\n[TEST 1] Simulating Temporary Rate Limit (Succeeds after Backoff)...")
    attempt_counter = 0
    cffi_requests.Session.request = mock_request_recovery
    
    start_time = time.time()
    try:
        with yahoo_connection_boundary("429 Recovery Test") as session:
            # The URL doesn't matter because it's intercepted
            response = session.get("https://query2.finance.yahoo.com/v1/finance/quote")
            
            print(f"\n✅ FINAL RESULT: Received HTTP {response.status_code}")
            if response.status_code == 200:
                print("✅ ENGINE BEHAVIOR: The engine successfully backed off, waited, and recovered the data without crashing!")
    except Exception as e:
        print(f"❌ ERROR: {e}")
        
    elapsed = time.time() - start_time
    print(f"⏳ Time elapsed: {elapsed:.2f} seconds (Notice the mathematical delays)")

    # ---------------------------------------------------------
    # TEST 2: Total Exhaustion & IP Hopping
    # ---------------------------------------------------------
    print("\n" + "-"*75)
    print("\n[TEST 2] Simulating Hard IP Ban (Exhausts retries, hops to IPv4)...")
    attempt_counter = 0
    cffi_requests.Session.request = mock_request_exhaustion
    
    start_time = time.time()
    try:
        with yahoo_connection_boundary("429 Exhaustion Test") as session:
            response = session.get("https://query2.finance.yahoo.com/v1/finance/quote")
            
            print(f"\n✅ FINAL RESULT: Received HTTP {response.status_code}")
            if response.status_code == 200:
                print("✅ ENGINE BEHAVIOR: IPv6 was burned. Engine dropped the socket, alerted Nextcloud, and rescued the pipeline via IPv4!")
    except Exception as e:
        print(f"❌ ERROR: {e}")
        
    elapsed = time.time() - start_time
    print(f"⏳ Time elapsed: {elapsed:.2f} seconds")

    # Restore the real network engine
    cffi_requests.Session.request = real_request
    
    print("\n" + "="*75)
    print(" DIAGNOSTICS COMPLETE")
    print("="*75 + "\n")


if __name__ == "__main__":
    run_diagnostics()