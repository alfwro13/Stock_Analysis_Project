"""
test_api_routes.py — Integration smoke-test for api_routes.py.

Requires a running server. Start it first:
    python main.py

Then run this script from the project root:
    python debug_scripts/test_api_routes.py [--base-url http://localhost:8090]

Covers:
  - Happy-path response shapes for all read endpoints
  - Structured error envelope consistency ({"status": "error", "message": ...})
  - Pydantic validation rejection (422) for bad payloads
  - Ticker path-parameter regex enforcement
  - Options payoff edge cases (zero strike, negative premium)
  - Notification lifecycle (latest → mark-read → purge)
  - Settings schema validation
  - Watchlist add / remove round-trip
"""

import sys
import argparse
import traceback
import requests

sys.path.insert(0, ".")

# ── CLI ───────────────────────────────────────────────────────────────────────

parser = argparse.ArgumentParser()
parser.add_argument("--base-url", default="http://localhost:8090", help="Base URL of running server")
args = parser.parse_args()
BASE = args.base_url.rstrip("/") + "/api"

# ── Helpers ───────────────────────────────────────────────────────────────────

PASS  = "\033[92mPASS\033[0m"
FAIL  = "\033[91mFAIL\033[0m"
SKIP  = "\033[93mSKIP\033[0m"
results: list[tuple[str, bool | None]] = []


def check(name: str, condition: bool, detail: str = "") -> None:
    status = PASS if condition else FAIL
    print(f"  [{status}] {name}" + (f" — {detail}" if detail else ""))
    results.append((name, condition))


def skip(name: str, reason: str = "") -> None:
    print(f"  [{SKIP}] {name}" + (f" — {reason}" if reason else ""))
    results.append((name, None))


def get(path: str, **kwargs):
    return requests.get(f"{BASE}{path}", timeout=10, **kwargs)


def post(path: str, **kwargs):
    return requests.post(f"{BASE}{path}", timeout=10, **kwargs)


def section(title: str) -> None:
    print(f"\n{'─' * 60}")
    print(f"  {title}")
    print(f"{'─' * 60}")


def has_error_envelope(r) -> bool:
    """Response body must be {"status": "error", "message": <str>}."""
    try:
        body = r.json()
        return (
            isinstance(body, dict)
            and body.get("status") == "error"
            and isinstance(body.get("message"), str)
            and len(body["message"]) > 0
        )
    except Exception:
        return False


def server_up() -> bool:
    try:
        requests.get(f"{BASE}/screener-data", timeout=3)
        return True
    except requests.exceptions.ConnectionError:
        return False


# ── Pre-flight ────────────────────────────────────────────────────────────────

print(f"\nTarget: {BASE}")
if not server_up():
    print(f"\n[{FAIL}] Cannot reach {BASE} — start the server first.\n")
    sys.exit(1)
print("Server is up.\n")


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 1 — Read-only GET endpoints
# ─────────────────────────────────────────────────────────────────────────────
section("1. Read-only GET endpoints")

try:
    r = get("/screener-data")
    check("GET /screener-data — HTTP 200", r.status_code == 200)
    body = r.json()
    check("GET /screener-data — body has 'data' list", isinstance(body.get("data"), list))
except Exception as e:
    check("GET /screener-data", False, str(e))

for report in [
    "/reports/quality-compounders",
    "/reports/garp-tenbaggers",
    "/reports/sectors",
    "/reports/leaders",
]:
    try:
        r = get(report)
        check(f"GET {report} — HTTP 200", r.status_code == 200)
        check(f"GET {report} — body has 'data' key", "data" in r.json())
    except Exception as e:
        check(f"GET {report}", False, str(e))

try:
    r = get("/reports/mean-reversion", params={"max_rsi": 35, "min_sma_distance": 0.0})
    check("GET /reports/mean-reversion — HTTP 200", r.status_code == 200)
    check("GET /reports/mean-reversion — body has 'data' key", "data" in r.json())
except Exception as e:
    check("GET /reports/mean-reversion", False, str(e))

try:
    r = get("/reports/dividends", params={"min_yield": 0.02, "min_score": 50})
    check("GET /reports/dividends — HTTP 200", r.status_code == 200)
    check("GET /reports/dividends — body has 'data' key", "data" in r.json())
except Exception as e:
    check("GET /reports/dividends", False, str(e))

try:
    r = get("/system/metrics")
    check("GET /system/metrics — HTTP 200", r.status_code == 200)
    body = r.json()
    check("GET /system/metrics — status == success", body.get("status") == "success")
    for key in ("universe", "ml", "infra", "state"):
        check(f"GET /system/metrics — has '{key}' key", key in body)
    # Verify notification counts use correct column (is_read, not status)
    state = body.get("state", {})
    check(
        "GET /system/metrics — notes_pending is int (not always-zero)",
        isinstance(state.get("notes_pending"), int),
        f"value={state.get('notes_pending')}",
    )
    check(
        "GET /system/metrics — notes_sent is int",
        isinstance(state.get("notes_sent"), int),
        f"value={state.get('notes_sent')}",
    )
except Exception as e:
    check("GET /system/metrics", False, str(e))

try:
    r = get("/notifications/latest", params={"last_id": 0})
    check("GET /notifications/latest — HTTP 200", r.status_code == 200)
    body = r.json()
    check("GET /notifications/latest — status == success", body.get("status") == "success")
    check("GET /notifications/latest — has notifications list", isinstance(body.get("notifications"), list))
except Exception as e:
    check("GET /notifications/latest", False, str(e))

try:
    r = get("/universe/profiler-status")
    check("GET /universe/profiler-status — HTTP 200", r.status_code == 200)
except Exception as e:
    check("GET /universe/profiler-status", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 2 — Ticker path-parameter validation (regex enforcement)
# ─────────────────────────────────────────────────────────────────────────────
section("2. Ticker path-parameter regex — must reject invalid tickers")

INVALID_TICKERS = [
    ("../etc/passwd",  "path traversal"),
    ("A" * 21,         "too long (21 chars)"),
    ("aapl",           "lowercase letters"),
    ("AAPL!",          "illegal character !"),
    ("AAPL SPACE",     "space in ticker"),
]

for ticker, reason in INVALID_TICKERS:
    try:
        r = get(f"/ai-prompt/{ticker}")
        check(
            f"GET /ai-prompt/{ticker!r} ({reason}) — 422",
            r.status_code == 422,
            f"got {r.status_code}",
        )
    except Exception as e:
        check(f"GET /ai-prompt/{ticker!r} — 422", False, str(e))

VALID_TICKERS = ["AAPL", "BRK.B", "GBP=X", "^GSPC", "VOD.L"]
for ticker in VALID_TICKERS:
    try:
        r = get(f"/ai-prompt/{ticker}")
        check(
            f"GET /ai-prompt/{ticker} — not 422 (valid format accepted)",
            r.status_code != 422,
            f"got {r.status_code}",
        )
    except Exception as e:
        check(f"GET /ai-prompt/{ticker} — not 422", False, str(e))

# Same checks for /options/chain/{ticker}
for ticker, reason in [("../x", "traversal"), ("aapl!", "bad chars")]:
    try:
        r = get(f"/options/chain/{ticker}")
        check(
            f"GET /options/chain/{ticker!r} ({reason}) — 422",
            r.status_code == 422,
            f"got {r.status_code}",
        )
    except Exception as e:
        check(f"GET /options/chain/{ticker!r} — 422", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 3 — Settings POST — Pydantic schema validation
# ─────────────────────────────────────────────────────────────────────────────
section("3. POST /settings — schema validation")

# Invalid: PORT as a string → 422
try:
    r = post("/settings", json={"PORT": "not-a-number"})
    check("POST /settings PORT='string' — 422", r.status_code == 422, f"got {r.status_code}")
except Exception as e:
    check("POST /settings PORT='string' — 422", False, str(e))

# Invalid: UI_PREFERENCES.REFRESH_RATE as a string → 422
try:
    r = post("/settings", json={"UI_PREFERENCES": {"REFRESH_RATE": "sixty"}})
    check("POST /settings REFRESH_RATE='string' — 422", r.status_code == 422, f"got {r.status_code}")
except Exception as e:
    check("POST /settings REFRESH_RATE='string' — 422", False, str(e))

# Invalid: completely alien key (extra fields) — FastAPI/Pydantic v2 ignores
# unknowns by default, so this should pass (200) not fail
try:
    r = post("/settings", json={"UNKNOWN_KEY_XYZ": True})
    check(
        "POST /settings unknown extra key — 200 (extra fields ignored)",
        r.status_code == 200,
        f"got {r.status_code}",
    )
except Exception as e:
    check("POST /settings unknown extra key", False, str(e))

# Valid partial update — should succeed
try:
    r = post("/settings", json={"BASE_CURRENCY": "GBP"})
    check("POST /settings valid partial update — 200", r.status_code == 200, f"got {r.status_code}")
    check("POST /settings valid partial update — success envelope", r.json().get("status") == "success")
except Exception as e:
    check("POST /settings valid partial update", False, str(e))

# Valid scheduling sub-key
try:
    r = post("/settings", json={"SCHEDULING": {"MAINTENANCE": {"ENABLED": True, "TIME": "02:00"}}})
    check("POST /settings SCHEDULING sub-key — 200", r.status_code == 200, f"got {r.status_code}")
except Exception as e:
    check("POST /settings SCHEDULING sub-key", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 4 — Options payoff error handling
# ─────────────────────────────────────────────────────────────────────────────
section("4. POST /options/payoff — edge cases and error handling")

VALID_PAYOFF = {
    "current_price": 150.0,
    "legs": [
        {"type": "call", "strike": 155.0, "premium": 2.50, "position": "long", "quantity": 1}
    ],
}

try:
    r = post("/options/payoff", json=VALID_PAYOFF)
    check("POST /options/payoff — valid input HTTP 200", r.status_code == 200, f"got {r.status_code}")
except Exception as e:
    check("POST /options/payoff — valid input", False, str(e))

# Zero strike — should return 422 or 500 with structured envelope, not crash
try:
    bad = {**VALID_PAYOFF, "legs": [{"type": "call", "strike": 0.0, "premium": 2.50, "position": "long", "quantity": 1}]}
    r = post("/options/payoff", json=bad)
    check(
        "POST /options/payoff zero strike — structured error (not raw 500)",
        r.status_code in (422, 500) and has_error_envelope(r),
        f"status={r.status_code} body={r.text[:80]}",
    )
except Exception as e:
    check("POST /options/payoff zero strike", False, str(e))

# Negative premium — structured error or 200 depending on engine tolerance
try:
    bad = {**VALID_PAYOFF, "legs": [{"type": "call", "strike": 155.0, "premium": -5.0, "position": "long", "quantity": 1}]}
    r = post("/options/payoff", json=bad)
    check(
        "POST /options/payoff negative premium — response received (not crash)",
        r.status_code in (200, 422, 500),
        f"status={r.status_code}",
    )
    if r.status_code in (422, 500):
        check(
            "POST /options/payoff negative premium — structured error envelope",
            has_error_envelope(r),
            r.text[:80],
        )
except Exception as e:
    check("POST /options/payoff negative premium", False, str(e))

# Missing required field → 422
try:
    r = post("/options/payoff", json={"current_price": 150.0})
    check("POST /options/payoff missing 'legs' — 422", r.status_code == 422, f"got {r.status_code}")
except Exception as e:
    check("POST /options/payoff missing 'legs'", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 5 — Notification lifecycle
# ─────────────────────────────────────────────────────────────────────────────
section("5. Notification lifecycle — latest → mark-read → purge")

try:
    r = get("/notifications/latest", params={"last_id": 0})
    check("GET /notifications/latest — 200", r.status_code == 200, f"got {r.status_code}")
    notifications = r.json().get("notifications", [])
    check("GET /notifications/latest — list type", isinstance(notifications, list))
    if notifications:
        n = notifications[0]
        check(
            "notification item has required keys",
            all(k in n for k in ("id", "type", "text", "timestamp")),
            str(list(n.keys())),
        )
except Exception as e:
    check("GET /notifications/latest", False, str(e))

try:
    r = post("/notifications/mark-read")
    check("POST /notifications/mark-read — 200", r.status_code == 200, f"got {r.status_code}")
    check("POST /notifications/mark-read — success envelope", r.json().get("status") == "success")
except Exception as e:
    check("POST /notifications/mark-read", False, str(e))

try:
    r = post("/notifications/purge")
    check("POST /notifications/purge — 200", r.status_code == 200, f"got {r.status_code}")
    check("POST /notifications/purge — success envelope", r.json().get("status") == "success")
except Exception as e:
    check("POST /notifications/purge", False, str(e))

# After purge, latest should return empty list
try:
    r = get("/notifications/latest", params={"last_id": 0})
    check(
        "GET /notifications/latest after purge — empty list",
        r.json().get("notifications") == [],
        str(r.json().get("notifications")),
    )
except Exception as e:
    check("GET /notifications/latest after purge", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 6 — Watchlist add / remove round-trip
# ─────────────────────────────────────────────────────────────────────────────
section("6. Watchlist add / remove round-trip")

TEST_TICKER = "MSFT"

try:
    r = post("/watchlist/add", json={"ticker": TEST_TICKER})
    check(
        f"POST /watchlist/add {TEST_TICKER} — 200 or structured error",
        r.status_code in (200, 500),
        f"got {r.status_code}",
    )
    if r.status_code == 200:
        check("POST /watchlist/add — success envelope", r.json().get("status") == "success")
    else:
        check("POST /watchlist/add failure — structured error envelope", has_error_envelope(r), r.text[:80])
except Exception as e:
    check(f"POST /watchlist/add {TEST_TICKER}", False, str(e))

try:
    r = post("/watchlist/remove", json={"ticker": TEST_TICKER})
    check(
        f"POST /watchlist/remove {TEST_TICKER} — 200 or structured error",
        r.status_code in (200, 500),
        f"got {r.status_code}",
    )
    if r.status_code != 200:
        check("POST /watchlist/remove failure — structured error envelope", has_error_envelope(r), r.text[:80])
except Exception as e:
    check(f"POST /watchlist/remove {TEST_TICKER}", False, str(e))

# Invalid ticker body → 422
try:
    r = post("/watchlist/add", json={"ticker": 12345})
    # Pydantic coerces int to str, so 422 is not guaranteed here — just check no crash
    check("POST /watchlist/add int ticker — no crash", r.status_code in (200, 422, 500), f"got {r.status_code}")
except Exception as e:
    check("POST /watchlist/add int ticker — no crash", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 7 — Error envelope consistency across all endpoints
# ─────────────────────────────────────────────────────────────────────────────
section("7. Error envelope consistency — all 5xx must return {status, message}")

# Force a 500 by hitting data/refresh-single with a ticker that won't exist
try:
    r = post("/data/refresh-single", json={"ticker": "ZZZNOTREAL"})
    if r.status_code == 500:
        check(
            "POST /data/refresh-single unknown ticker 500 — structured envelope",
            has_error_envelope(r),
            r.text[:120],
        )
    else:
        skip("POST /data/refresh-single 500 envelope", f"server returned {r.status_code} instead")
except Exception as e:
    check("POST /data/refresh-single 500 envelope", False, str(e))

# Ghostfolio discover with bad credentials should return structured error
try:
    r = post("/ghostfolio/discover")
    if r.status_code != 200:
        check(
            "POST /ghostfolio/discover failure — structured envelope",
            has_error_envelope(r),
            r.text[:120],
        )
    else:
        skip("POST /ghostfolio/discover envelope check", "auth succeeded unexpectedly")
except Exception as e:
    check("POST /ghostfolio/discover structured error", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 8 — Response envelope shape consistency (no bare dicts)
# ─────────────────────────────────────────────────────────────────────────────
section("8. Response envelope — trigger endpoints return JSONResponse (not bare dict)")

for endpoint in ["/update", "/sync-ghostfolio"]:
    try:
        r = post(endpoint)
        check(
            f"POST {endpoint} — Content-Type is application/json",
            "application/json" in r.headers.get("content-type", ""),
            r.headers.get("content-type"),
        )
        check(
            f"POST {endpoint} — body is dict with 'status' key",
            isinstance(r.json(), dict) and "status" in r.json(),
            r.text[:60],
        )
    except Exception as e:
        check(f"POST {endpoint} envelope", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# SECTION 9 — Market pulse GET
# ─────────────────────────────────────────────────────────────────────────────
section("9. GET /market-pulse")

try:
    r = get("/market-pulse")
    check("GET /market-pulse — 200", r.status_code == 200, f"got {r.status_code}")
    body = r.json()
    check("GET /market-pulse — status == success", body.get("status") == "success")
    check("GET /market-pulse — has 'data' key", "data" in body)
except Exception as e:
    check("GET /market-pulse", False, str(e))


# ─────────────────────────────────────────────────────────────────────────────
# Summary
# ─────────────────────────────────────────────────────────────────────────────
print(f"\n{'═' * 60}")
passed  = sum(1 for _, v in results if v is True)
failed  = sum(1 for _, v in results if v is False)
skipped = sum(1 for _, v in results if v is None)
total   = len(results)

print(f"  Results: {passed}/{total - skipped} passed  |  {failed} failed  |  {skipped} skipped")

if failed:
    print(f"\n  [{FAIL}] Failed tests:")
    for name, v in results:
        if v is False:
            print(f"    • {name}")

print(f"{'═' * 60}\n")
sys.exit(0 if failed == 0 else 1)
