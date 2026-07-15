"""
tests/test_profile_engine.py — Profile Engine Tests

Regression guard for the missing `import time` / `import random` bug that
caused the Fundamentals Profiler APScheduler job to crash with NameError
at the `time.sleep(random.uniform(...))` call inside run_profile_audit.

Covers:
  • Module imports cleanly — no NameError for `time` or `random`
  • run_profile_audit: empty DB → exits immediately, no crash, no sleep calls
  • update_single_profile: blacklisted ticker → returns False without DB touch
  • update_single_profile: Yahoo returns empty → blacklists ticker, returns False
  • update_single_profile: valid Yahoo payload → upserts to asset_profiles, True
  • count_pending_profiles: empty DB → 0
  • get_profiler_queue_breakdown: empty DB → all counts 0
"""

import inspect
import json
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


# ── Regression: module must import without NameError ─────────────────────────

class TestModuleImports:

    def test_time_module_is_imported(self):
        """profile_engine source must contain `import time` — regression for the NameError bug."""
        import profile_engine
        src = inspect.getsource(profile_engine)
        assert "import time" in src, "profile_engine is missing `import time`"

    def test_random_module_is_imported(self):
        """profile_engine source must contain `import random`."""
        import profile_engine
        src = inspect.getsource(profile_engine)
        assert "import random" in src, "profile_engine is missing `import random`"

    def test_time_sleep_is_callable_from_module(self):
        """time.sleep must be reachable from profile_engine's namespace at runtime."""
        import profile_engine
        import time as _time
        assert profile_engine.time is _time
        assert callable(profile_engine.time.sleep)

    def test_random_uniform_is_callable_from_module(self):
        """random.uniform must be reachable from profile_engine's namespace at runtime."""
        import profile_engine
        import random as _random
        assert profile_engine.random is _random
        assert callable(profile_engine.random.uniform)


# ── run_profile_audit ─────────────────────────────────────────────────────────

class TestRunProfileAudit:

    def test_empty_db_returns_cleanly(self):
        """With no tickers in the DB the audit logs 'up-to-date' and returns without error."""
        from profile_engine import run_profile_audit
        run_profile_audit(limit=10)

    def test_empty_queue_does_not_call_sleep(self):
        """When the DB query returns no tickers to update, time.sleep must never be invoked."""
        from unittest.mock import MagicMock
        from profile_engine import run_profile_audit
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.fetchall.return_value = []
        with patch("profile_engine.get_connection", return_value=mock_conn), \
             patch("profile_engine.time") as mock_time:
            run_profile_audit(limit=10)
            mock_time.sleep.assert_not_called()

    def test_empty_db_does_not_fetch_yahoo(self):
        """With no tickers to process, yahoo_engine.get_ticker_info must never be called.

        Mocks get_connection (like test_empty_queue_does_not_call_sleep above) rather than
        relying on the real shared session DB being empty: other test modules insert rows into
        stock_signals/market_universe/quant_signals without cleaning up, so this test was flaky
        depending on run order (found 2026-07-15)."""
        from unittest.mock import MagicMock
        from profile_engine import run_profile_audit
        mock_conn = MagicMock()
        mock_conn.cursor.return_value.fetchall.return_value = []
        with patch("profile_engine.get_connection", return_value=mock_conn), \
             patch("profile_engine.yahoo_engine") as mock_ye:
            run_profile_audit(limit=10)
            mock_ye.get_ticker_info.assert_not_called()


# ── count_pending_profiles / get_profiler_queue_breakdown ────────────────────

class TestProfilingQueueCounts:

    def test_pending_profiles_returns_non_negative_int(self):
        from profile_engine import count_pending_profiles
        result = count_pending_profiles()
        assert isinstance(result, int)
        assert result >= 0

    def test_queue_breakdown_has_correct_keys(self):
        from profile_engine import get_profiler_queue_breakdown
        result = get_profiler_queue_breakdown()
        assert set(result.keys()) == {
            "eligible_count", "pending_count", "profiled_count",
            "stale_count", "total_profiles", "firewall_active",
        }

    def test_queue_breakdown_all_values_non_negative(self):
        from profile_engine import get_profiler_queue_breakdown
        result = get_profiler_queue_breakdown()
        for key, val in result.items():
            assert isinstance(val, int) and val >= 0, f"{key}={val} is not a non-negative int"

    def test_queue_breakdown_firewall_active_is_bool_like(self):
        from profile_engine import get_profiler_queue_breakdown
        result = get_profiler_queue_breakdown()
        assert result["firewall_active"] in (0, 1)


# ── update_single_profile ─────────────────────────────────────────────────────

class TestUpdateSingleProfile:

    def test_blacklisted_ticker_returns_false(self, tmp_path):
        """A ticker already in the blacklist is skipped without touching Yahoo or the DB."""
        bl = tmp_path / "freetrade_blacklist.json"
        bl.write_text(json.dumps(["SKIP_ME"]))
        with patch("profile_engine.BLACKLIST_PATH", bl), \
             patch("profile_engine.yahoo_engine") as mock_ye:
            from profile_engine import update_single_profile
            result = update_single_profile("SKIP_ME")
        assert result is False
        mock_ye.get_ticker_info.assert_not_called()

    def test_empty_yahoo_payload_blacklists_ticker(self, tmp_path):
        """Yahoo returning {} causes the ticker to be written to the blacklist."""
        bl = tmp_path / "freetrade_blacklist.json"
        bl.write_text("[]")
        with patch("profile_engine.BLACKLIST_PATH", bl), \
             patch("profile_engine.yahoo_engine") as mock_ye:
            mock_ye.get_ticker_info.return_value = {}
            from profile_engine import update_single_profile
            result = update_single_profile("DEAD_TICKER")
        assert result is False
        saved = json.loads(bl.read_text())
        assert "DEAD_TICKER" in saved

    def test_none_yahoo_payload_blacklists_ticker(self, tmp_path):
        """Yahoo returning None is handled the same way as an empty dict."""
        bl = tmp_path / "freetrade_blacklist.json"
        bl.write_text("[]")
        with patch("profile_engine.BLACKLIST_PATH", bl), \
             patch("profile_engine.yahoo_engine") as mock_ye:
            mock_ye.get_ticker_info.return_value = None
            from profile_engine import update_single_profile
            result = update_single_profile("NULL_TICKER")
        assert result is False
        saved = json.loads(bl.read_text())
        assert "NULL_TICKER" in saved

    def test_valid_yahoo_payload_upserts_and_returns_true(self, tmp_path):
        """A full Yahoo payload upserts a row into asset_profiles and returns True."""
        bl = tmp_path / "freetrade_blacklist.json"
        bl.write_text("[]")
        mock_info = {
            "shortName": "Test Corp",
            "sector": "Technology",
            "industry": "Software",
            "country": "USA",
            "exchange": "NMS",
            "currency": "USD",
            "quoteType": "EQUITY",
            "longBusinessSummary": "A synthetic test company.",
        }
        with patch("profile_engine.BLACKLIST_PATH", bl), \
             patch("profile_engine.yahoo_engine") as mock_ye:
            mock_ye.get_ticker_info.return_value = mock_info
            from profile_engine import update_single_profile
            result = update_single_profile("TEST_CORP_XYZ")
        assert result is True

    def test_valid_payload_row_persisted_in_db(self, tmp_path):
        """The upserted row must be readable back from asset_profiles."""
        import database
        bl = tmp_path / "freetrade_blacklist.json"
        bl.write_text("[]")
        mock_info = {
            "shortName": "Persist Corp",
            "sector": "Finance",
            "industry": "Banking",
            "country": "UK",
            "exchange": "LSE",
            "currency": "GBP",
            "quoteType": "EQUITY",
            "longBusinessSummary": "A persistent test company.",
        }
        with patch("profile_engine.BLACKLIST_PATH", bl), \
             patch("profile_engine.yahoo_engine") as mock_ye:
            mock_ye.get_ticker_info.return_value = mock_info
            from profile_engine import update_single_profile
            update_single_profile("PERSIST_XYZ")

        conn = database.get_connection()
        row = conn.execute(
            "SELECT company_name, sector FROM asset_profiles WHERE ticker = ?",
            ("PERSIST_XYZ",),
        ).fetchone()
        conn.close()

        assert row is not None
        assert row["company_name"] == "Persist Corp"
        assert row["sector"] == "Finance"
