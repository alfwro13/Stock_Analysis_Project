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
"""

import sys
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

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
        patch("main.start_scheduler"),
        patch("main.reload_scheduler"),
        patch("main.shutdown_scheduler"),
    ):
        import main as _main_module
        with TestClient(_main_module.app, raise_server_exceptions=False) as c:
            yield c
