"""
tests/conftest.py

Session-level fixtures shared by all test modules.

The application uses a real SQLite file at config.DB_PATH.  Here we redirect
that path to a temporary file and initialise the full schema before any test
runs, so every test that reads the database finds a clean, valid schema
(but no data rows, unless a test seeds them itself).

External startup side-effects that would require live network access are
patched out:
  - run_yfinance_smoke_test  (Yahoo Finance connectivity check)
  - start_scheduler          (APScheduler background threads)
  - reload_scheduler         (reads DB and launches cron jobs)
  - shutdown_scheduler       (teardown hook)

Also patched for the lifetime of the `client` fixture: `api_routes_accounts.fetch_and_save_pulse`
— the accounts-API self-triggered live-price refresh (portfolio-totals/list-with-metrics/
holdings-list) would otherwise fire a real Yahoo Finance background fetch for any held test
ticker with no market_pulse_cache row whenever a test happens to run during real market hours.
Individual tests that want to assert on this call still work as before via their own local
`patch("api_routes_accounts.fetch_and_save_pulse")`, which layers over this one for the
duration of their own `with` block.
"""

import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# ── Auth bypass for tests ─────────────────────────────────────────────────────
#    The auth middleware accepts requests with a valid X-API-Key header.
#    Setting API_KEY here (before main.py loads) lets the TestClient pass all
#    auth checks without a session cookie, which also avoids CSRF validation
#    (the CSRF middleware only fires when the "session" cookie is present).
_TEST_API_KEY = "test-api-key-do-not-use-in-production"
os.environ["API_KEY"] = _TEST_API_KEY

_TEST_CONFIRM_TOKEN = "test-confirm-token-do-not-use-in-production"
os.environ["ADMIN_CONFIRM_TOKEN"] = _TEST_CONFIRM_TOKEN

_TEST_USERNAME = "testadmin"
_TEST_PASSWORD = "TestPassword123"
os.environ["DASHBOARD_USERNAME"] = _TEST_USERNAME
os.environ["DASHBOARD_PASSWORD"] = _TEST_PASSWORD
os.environ["DASHBOARD_PASSWORD_HASH"] = ""
os.environ.setdefault("APP_SECRET_KEY", "test-app-secret-key-do-not-use-in-production")

# ── 1. Make the project root importable ──────────────────────────────────────
PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

# ── 2. Create a temp DB file and redirect DB_PATH before any app code runs ───
#    Python caches modules, so we must mutate the module-level attribute that
#    `get_connection()` reads at call time.  Setting it here (before TestClient
#    triggers the lifespan) is sufficient because `get_connection` resolves
#    `database.DB_PATH` dynamically on every call.
_tmpdb = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
TEST_DB_PATH = Path(_tmpdb.name)
_tmpdb.close()

import database as _db_module        # noqa: E402
_db_module.DB_PATH = TEST_DB_PATH    # redirect before init_db()

import config as _config_module      # noqa: E402
_config_module.DB_PATH = TEST_DB_PATH

# db_schema.WATCHLIST_PATH is bound at import time from config; point it at a
# nonexistent path so init_db()'s one-time watchlist.json import is a no-op
# during tests instead of pulling in the real data/watchlist.json.
import db_schema as _db_schema_module  # noqa: E402
_db_schema_module.WATCHLIST_PATH = TEST_DB_PATH.with_suffix(".watchlist.json")

# ── 3. Initialise the schema in the temp DB ───────────────────────────────────
_db_module.init_db()

# ── 4. TestClient fixture ─────────────────────────────────────────────────────
from fastapi.testclient import TestClient  # noqa: E402


@pytest.fixture(scope="session")
def client():
    """
    A session-scoped FastAPI TestClient with network/scheduler side-effects
    mocked out.  The client shares the temp DB initialised above.
    """
    with (
        patch("main.run_yfinance_smoke_test"),
        patch("utils.ensure_workflow_assets"),
        patch("main.start_scheduler"),
        patch("main.reload_scheduler"),
        patch("main.shutdown_scheduler"),
        patch("main.resume_interrupted_scans"),
        patch("api_routes_accounts.fetch_and_save_pulse"),
    ):
        import main as _main_module
        with TestClient(
            _main_module.app,
            raise_server_exceptions=False,
            headers={"X-API-Key": _TEST_API_KEY},
        ) as c:
            yield c


@pytest.fixture(scope="session")
def confirm_token():
    """Returns the ADMIN_CONFIRM_TOKEN used in tests, for endpoints that require it."""
    return _TEST_CONFIRM_TOKEN


@pytest.fixture(scope="session")
def raw_client():
    """
    Session-scoped unauthenticated client — no X-API-Key, redirects NOT followed.
    Use for auth middleware tests and other tests where you need to see 302s.
    Do NOT use for tests that trigger a server-set session cookie; those should
    create their own client via _fresh_client() to avoid cookie-jar contamination.
    """
    with (
        patch("main.run_yfinance_smoke_test"),
        patch("utils.ensure_workflow_assets"),
        patch("main.start_scheduler"),
        patch("main.reload_scheduler"),
        patch("main.shutdown_scheduler"),
        patch("main.resume_interrupted_scans"),
    ):
        import main as _main_module
        with TestClient(_main_module.app, raise_server_exceptions=False, follow_redirects=False) as c:
            yield c


@pytest.fixture(scope="session")
def test_username():
    return _TEST_USERNAME


@pytest.fixture(scope="session")
def test_password():
    return _TEST_PASSWORD
