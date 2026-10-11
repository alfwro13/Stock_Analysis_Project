"""
tests/test_profile_engine.py — Profile Engine Tests

Regression guard for the missing `import time` / `import random` bug that
caused the Fundamentals Profiler APScheduler job to crash with NameError
at the `time.sleep(random.uniform(...))` call inside run_profile_audit.

Covers:
  • Module imports cleanly — no NameError for `time` or `random`
  • run_profile_audit: empty DB → exits immediately, no crash, no sleep calls
  • update_single_profile: blacklisted ticker → returns False without DB touch
  • update_single_profile: an empty Yahoo answer is a strike; the 5th consecutive one blacklists
  • update_single_profile: a failed Yahoo call (exception) never blacklists or strikes
  • update_single_profile: a valid payload clears the ticker's strikes
  • update_single_profile: valid Yahoo payload → upserts to asset_profiles, True
  • count_pending_profiles: empty DB → 0
  • get_profiler_queue_breakdown: empty DB → all counts 0
  • queue: blacklisted/synthetic/ignored tickers are neither counted nor picked
  • batch: Portfolio/Watchlist first, then never-profiled, then oldest verification
  • breakdown: runs/days to clear from batch size and schedule
  • scheduler_jobs.run_fundamentals_profiler() — runner-level wiring
  • blacklist purge: leftover parquet/fundamentals files and re-created rows are removed,
    protected (held/watched/ledger/registry) tickers are not
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
            "excluded_count", "refreshed_24h_count", "batch_size",
            "runs_to_clear", "days_to_clear",
        }

    def test_queue_breakdown_all_values_non_negative(self):
        from profile_engine import get_profiler_queue_breakdown
        result = get_profiler_queue_breakdown()
        for key, val in result.items():
            if key == "days_to_clear" and val is None:
                continue
            assert isinstance(val, int) and val >= 0, f"{key}={val} is not a non-negative int"

    def test_queue_breakdown_firewall_active_is_bool_like(self):
        from profile_engine import get_profiler_queue_breakdown
        result = get_profiler_queue_breakdown()
        assert result["firewall_active"] in (0, 1)


# ── update_single_profile ─────────────────────────────────────────────────────

def _get_strikes(ticker):
    import database
    conn = database.get_connection()
    try:
        row = conn.execute("SELECT strikes FROM profile_empty_strikes WHERE ticker = ?", (ticker,)).fetchone()
        return row["strikes"] if row else None
    finally:
        conn.close()


def _set_strikes(ticker, strikes):
    import database
    conn = database.get_connection()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO profile_empty_strikes (ticker, strikes, last_empty_at) VALUES (?, ?, '2026-01-01 00:00:00')",
            (ticker, strikes),
        )
        conn.commit()
    finally:
        conn.close()


def _clear_strikes(ticker):
    import database
    conn = database.get_connection()
    try:
        conn.execute("DELETE FROM profile_empty_strikes WHERE ticker = ?", (ticker,))
        conn.commit()
    finally:
        conn.close()


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

    def test_empty_answers_below_threshold_strike_without_blacklisting(self, tmp_path):
        """Each empty answer is a logged strike; the ticker stays out of the blacklist and keeps its rows."""
        import profile_engine
        bl = tmp_path / "freetrade_blacklist.json"
        bl.write_text("[]")
        ticker = "STRIKE_BELOW_XYZ"
        try:
            with patch("profile_engine.BLACKLIST_PATH", bl), \
                 patch("profile_engine.yahoo_engine") as mock_ye:
                mock_ye.get_ticker_info.return_value = {}
                for expected in range(1, profile_engine.EMPTY_ANSWERS_TO_BLACKLIST):
                    assert profile_engine.update_single_profile(ticker) is False
                    assert _get_strikes(ticker) == expected
            assert ticker not in json.loads(bl.read_text())
        finally:
            _clear_strikes(ticker)

    def test_strike_message_names_the_count(self, tmp_path, caplog):
        import profile_engine
        bl = tmp_path / "freetrade_blacklist.json"
        bl.write_text("[]")
        ticker = "STRIKE_LOG_XYZ"
        try:
            with patch("profile_engine.BLACKLIST_PATH", bl), \
                 patch("profile_engine.yahoo_engine") as mock_ye, \
                 caplog.at_level("WARNING", logger="profile_engine"):
                mock_ye.get_ticker_info.return_value = None
                profile_engine.update_single_profile(ticker)
            assert f"Empty call strike 1/{profile_engine.EMPTY_ANSWERS_TO_BLACKLIST} for {ticker}" in caplog.text
        finally:
            _clear_strikes(ticker)

    def test_fifth_consecutive_empty_answer_blacklists_and_clears_strikes(self, tmp_path):
        import profile_engine
        bl = tmp_path / "freetrade_blacklist.json"
        bl.write_text("[]")
        ticker = "STRIKE_FIFTH_XYZ"
        try:
            with patch("profile_engine.BLACKLIST_PATH", bl), \
                 patch("profile_engine.yahoo_engine") as mock_ye:
                mock_ye.get_ticker_info.return_value = {}
                for _ in range(profile_engine.EMPTY_ANSWERS_TO_BLACKLIST):
                    assert profile_engine.update_single_profile(ticker) is False
            assert ticker in json.loads(bl.read_text())
            assert _get_strikes(ticker) is None
        finally:
            _clear_strikes(ticker)

    def test_none_and_identityless_payloads_count_as_empty_answers(self, tmp_path):
        """None (Yahoo's 404) and a payload with no identity field (`{'trailingPegRatio': None}`) strike the same way."""
        import profile_engine
        bl = tmp_path / "freetrade_blacklist.json"
        bl.write_text("[]")
        ticker = "STRIKE_KINDS_XYZ"
        try:
            with patch("profile_engine.BLACKLIST_PATH", bl), \
                 patch("profile_engine.yahoo_engine") as mock_ye:
                mock_ye.get_ticker_info.return_value = None
                profile_engine.update_single_profile(ticker)
                mock_ye.get_ticker_info.return_value = {"trailingPegRatio": None}
                profile_engine.update_single_profile(ticker)
            assert _get_strikes(ticker) == 2
        finally:
            _clear_strikes(ticker)

    def test_failed_yahoo_call_never_blacklists_or_strikes(self, tmp_path):
        """A swallowed 429/timeout used to look like a delisting; now it is retried next run with strikes untouched."""
        import profile_engine
        bl = tmp_path / "freetrade_blacklist.json"
        bl.write_text("[]")
        ticker = "STRIKE_EXC_XYZ"
        _set_strikes(ticker, profile_engine.EMPTY_ANSWERS_TO_BLACKLIST - 1)
        try:
            with patch("profile_engine.BLACKLIST_PATH", bl), \
                 patch("profile_engine.yahoo_engine") as mock_ye:
                mock_ye.get_ticker_info.side_effect = RuntimeError("HTTP 429")
                assert profile_engine.update_single_profile(ticker) is False
                mock_ye.get_ticker_info.assert_called_once_with(ticker, raise_errors=True)
            assert ticker not in json.loads(bl.read_text())
            assert _get_strikes(ticker) == profile_engine.EMPTY_ANSWERS_TO_BLACKLIST - 1
        finally:
            _clear_strikes(ticker)

    def test_valid_payload_resets_strikes(self, tmp_path):
        """Strikes are consecutive: one good answer between two empty ones starts the count over."""
        import profile_engine
        bl = tmp_path / "freetrade_blacklist.json"
        bl.write_text("[]")
        ticker = "STRIKE_RESET_XYZ"
        _set_strikes(ticker, profile_engine.EMPTY_ANSWERS_TO_BLACKLIST - 1)
        try:
            with patch("profile_engine.BLACKLIST_PATH", bl), \
                 patch("profile_engine.yahoo_engine") as mock_ye:
                mock_ye.get_ticker_info.return_value = {"shortName": "Reset Corp"}
                assert profile_engine.update_single_profile(ticker) is True
                assert _get_strikes(ticker) is None
                mock_ye.get_ticker_info.return_value = {}
                profile_engine.update_single_profile(ticker)
            assert _get_strikes(ticker) == 1
            assert ticker not in json.loads(bl.read_text())
        finally:
            _clear_strikes(ticker)
            _clear_queue([ticker])

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


# ── queue definition, batch order, ETA, runner ───────────────────────────────

_QUEUE_PREFIX = "PQTEST_"


def _seed_queue(profiles):
    """profiles: {ticker: last_verified_date or None}; every ticker gets a stock_signals row."""
    import database
    conn = database.get_connection()
    try:
        for ticker, verified in profiles.items():
            conn.execute("INSERT OR REPLACE INTO stock_signals (ticker) VALUES (?)", (ticker,))
            if verified is not None:
                conn.execute(
                    "INSERT OR REPLACE INTO asset_profiles (ticker, company_name, last_verified_date) VALUES (?, ?, ?)",
                    (ticker, ticker, verified),
                )
        conn.commit()
    finally:
        conn.close()


def _clear_queue(tickers):
    import database
    conn = database.get_connection()
    try:
        for ticker in tickers:
            conn.execute("DELETE FROM stock_signals WHERE ticker = ?", (ticker,))
            conn.execute("DELETE FROM asset_profiles WHERE ticker = ?", (ticker,))
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def empty_blacklist(tmp_path):
    bl = tmp_path / "freetrade_blacklist.json"
    bl.write_text("[]")
    with patch("profile_engine.BLACKLIST_PATH", bl):
        yield bl


class TestProfilerQueueDefinition:

    def test_blacklisted_synthetic_and_ignored_tickers_are_not_pending(self, empty_blacklist):
        """The worker never fetches these, so counting them as pending leaves the tile stuck above zero forever."""
        import profile_engine
        tickers = {
            f"{_QUEUE_PREFIX}OK": None,
            f"{_QUEUE_PREFIX}BLACKLISTED": None,
            f"{_QUEUE_PREFIX}IGNORED": None,
            "TBILL-987654": None,
        }
        empty_blacklist.write_text(json.dumps([f"{_QUEUE_PREFIX}BLACKLISTED"]))
        real_config = profile_engine.load_config()
        config = {**real_config, "IGNORED_TICKERS": [f"{_QUEUE_PREFIX}IGNORED"]}
        _seed_queue(tickers)
        try:
            with patch("profile_engine.load_config", return_value=config):
                rows, excluded, _ = profile_engine._profile_queue()
                breakdown = profile_engine.get_profiler_queue_breakdown()
        finally:
            _clear_queue(tickers)

        queued = {r[0] for r in rows}
        assert f"{_QUEUE_PREFIX}OK" in queued
        assert not queued & {f"{_QUEUE_PREFIX}BLACKLISTED", f"{_QUEUE_PREFIX}IGNORED", "TBILL-987654"}
        assert excluded >= 3
        assert breakdown["excluded_count"] >= 3

    def test_pending_is_missing_plus_stale(self, empty_blacklist):
        import profile_engine
        before = profile_engine.get_profiler_queue_breakdown()
        tickers = {
            f"{_QUEUE_PREFIX}NEVER": None,
            f"{_QUEUE_PREFIX}STALE": "2000-01-01 00:00:00",
            f"{_QUEUE_PREFIX}FRESH": "2999-01-01 00:00:00",
        }
        _seed_queue(tickers)
        try:
            after = profile_engine.get_profiler_queue_breakdown()
        finally:
            _clear_queue(tickers)

        assert after["eligible_count"] - before["eligible_count"] == 3
        assert after["profiled_count"] - before["profiled_count"] == 2
        assert after["stale_count"] - before["stale_count"] == 1
        assert after["pending_count"] - before["pending_count"] == 2


class TestProfilerBatchOrder:

    def test_priority_then_never_profiled_then_oldest(self, empty_blacklist):
        import profile_engine
        held_old = f"{_QUEUE_PREFIX}ZHELD_OLD"
        held_never = f"{_QUEUE_PREFIX}ZHELD_NEVER"
        other_never = f"{_QUEUE_PREFIX}AOTHER_NEVER"
        other_oldest = f"{_QUEUE_PREFIX}BOTHER_OLDEST"
        other_older = f"{_QUEUE_PREFIX}COTHER_OLDER"
        tickers = {
            held_old: "2001-01-01 00:00:00",
            held_never: None,
            other_never: None,
            other_oldest: "2000-01-01 00:00:00",
            other_older: "2002-01-01 00:00:00",
        }
        _seed_queue(tickers)
        fetched = []
        try:
            with patch("profile_engine.get_portfolio_watchlist_tickers", return_value=[held_old, held_never]), \
                 patch("profile_engine.update_single_profile", side_effect=lambda t: fetched.append(t) or True), \
                 patch("profile_engine.time.sleep"):
                profile_engine.run_profile_audit(limit=1_000_000)
        finally:
            _clear_queue(tickers)

        ours = [t for t in fetched if t.startswith(_QUEUE_PREFIX)]
        assert ours == [held_never, held_old, other_never, other_oldest, other_older]

    def test_limit_caps_the_batch(self, empty_blacklist):
        import profile_engine
        fetched = []
        with patch("profile_engine.get_portfolio_watchlist_tickers", return_value=[]), \
             patch("profile_engine.update_single_profile", side_effect=lambda t: fetched.append(t) or True), \
             patch("profile_engine.time.sleep"):
            profile_engine.run_profile_audit(limit=2)
        assert len(fetched) <= 2

    def test_blacklisted_ticker_does_not_use_a_batch_slot(self, empty_blacklist):
        import profile_engine
        tickers = {f"{_QUEUE_PREFIX}BL": None}
        empty_blacklist.write_text(json.dumps([f"{_QUEUE_PREFIX}BL"]))
        _seed_queue(tickers)
        fetched = []
        try:
            with patch("profile_engine.get_portfolio_watchlist_tickers", return_value=[]), \
                 patch("profile_engine.update_single_profile", side_effect=lambda t: fetched.append(t) or True), \
                 patch("profile_engine.time.sleep"):
                profile_engine.run_profile_audit(limit=1_000_000)
        finally:
            _clear_queue(tickers)
        assert f"{_QUEUE_PREFIX}BL" not in fetched


class TestProfilerEta:

    def test_runs_and_days_to_clear_follow_batch_and_schedule(self, empty_blacklist):
        import profile_engine
        schedule = {"enabled": True, "days": ["mon", "wed"], "batch_size": 1}
        with patch("profile_engine.profiler_schedule", return_value=schedule):
            result = profile_engine.get_profiler_queue_breakdown()
        pending = result["pending_count"]
        assert result["batch_size"] == 1
        assert result["runs_to_clear"] == pending
        assert result["days_to_clear"] == -(-pending * 7 // 2)

    def test_days_to_clear_is_none_when_schedule_disabled(self, empty_blacklist):
        import profile_engine
        schedule = {"enabled": False, "days": ["mon"], "batch_size": 250}
        with patch("profile_engine.profiler_schedule", return_value=schedule):
            result = profile_engine.get_profiler_queue_breakdown()
        assert result["days_to_clear"] is None


class TestFundamentalsProfilerRunner:

    def test_runner_passes_configured_batch_size_to_audit(self):
        import scheduler_jobs
        schedule = {"enabled": True, "days": ["mon"], "batch_size": 37}
        with patch("profile_engine.profiler_schedule", return_value=schedule), \
             patch("profile_engine.run_profile_audit") as audit:
            scheduler_jobs.run_fundamentals_profiler()
        audit.assert_called_once_with(limit=37)

    def test_status_endpoint_reports_last_run_and_eta_fields(self, client):
        response = client.get("/api/universe/profiler-status")
        assert response.status_code == 200
        body = response.json()
        assert set(body["last_run"]) == {"display", "status", "duration_sec"}
        for key in ("excluded_count", "refreshed_24h_count", "batch_size", "runs_to_clear", "days_to_clear"):
            assert key in body["breakdown"]


# ── blacklist purge: files and re-created rows ───────────────────────────────

@pytest.fixture
def ticker_dirs(tmp_path):
    history = tmp_path / "historical"
    fundamentals = tmp_path / "fundamentals"
    history.mkdir()
    fundamentals.mkdir()
    with patch("profile_engine.HISTORICAL_DIR", history), patch("profile_engine.FUNDAMENTALS_DIR", fundamentals):
        yield history, fundamentals


def _touch_ticker_files(ticker, history, fundamentals):
    (history / f"{ticker}.parquet").write_bytes(b"x")
    (fundamentals / f"{ticker}.json").write_text("{}")


class TestBlacklistPurgeFiles:

    def test_blacklisting_removes_history_and_fundamentals_files(self, tmp_path, ticker_dirs):
        import profile_engine
        history, fundamentals = ticker_dirs
        bl = tmp_path / "freetrade_blacklist.json"
        bl.write_text("[]")
        _touch_ticker_files("PQPURGE", history, fundamentals)
        _set_strikes("PQPURGE", profile_engine.EMPTY_ANSWERS_TO_BLACKLIST - 1)
        with patch("profile_engine.BLACKLIST_PATH", bl), \
             patch("profile_engine.yahoo_engine") as mock_ye, \
             patch("profile_engine._protected_tickers", return_value=set()):
            mock_ye.get_ticker_info.return_value = {}
            assert profile_engine.update_single_profile("PQPURGE") is False
        assert not (history / "PQPURGE.parquet").exists()
        assert not (fundamentals / "PQPURGE.json").exists()

    def test_protected_ticker_keeps_its_files_when_blacklisted(self, tmp_path, ticker_dirs):
        import profile_engine
        history, fundamentals = ticker_dirs
        bl = tmp_path / "freetrade_blacklist.json"
        bl.write_text("[]")
        _touch_ticker_files("PQHELD", history, fundamentals)
        _set_strikes("PQHELD", profile_engine.EMPTY_ANSWERS_TO_BLACKLIST - 1)
        with patch("profile_engine.BLACKLIST_PATH", bl), \
             patch("profile_engine.yahoo_engine") as mock_ye, \
             patch("profile_engine._protected_tickers", return_value={"PQHELD"}):
            mock_ye.get_ticker_info.return_value = {}
            profile_engine.update_single_profile("PQHELD")
        assert (history / "PQHELD.parquet").exists()
        assert (fundamentals / "PQHELD.json").exists()

    def test_unsafe_ticker_name_never_touches_files_outside_the_directories(self, ticker_dirs, tmp_path):
        import profile_engine
        history, _ = ticker_dirs
        outside = tmp_path / "escape.parquet"
        outside.write_bytes(b"x")
        assert profile_engine._delete_ticker_files("../escape", set()) == 0
        assert outside.exists()

    def test_sweep_removes_recreated_rows_and_files_but_not_protected_or_unlisted(self, tmp_path, ticker_dirs):
        import database
        import profile_engine
        history, fundamentals = ticker_dirs
        bl = tmp_path / "freetrade_blacklist.json"
        bl.write_text(json.dumps(["PQSWEEP", "PQHELD2"]))
        tickers = ["PQSWEEP", "PQHELD2", "PQOTHER"]
        for t in tickers:
            _touch_ticker_files(t, history, fundamentals)
        _seed_queue({"PQSWEEP": "2026-01-01 00:00:00", "PQHELD2": None, "PQOTHER": None})
        try:
            with patch("profile_engine.BLACKLIST_PATH", bl), \
                 patch("profile_engine._protected_tickers", return_value={"PQHELD2"}):
                removed = profile_engine.purge_blacklisted_tickers()
            conn = database.get_connection()
            try:
                left = {r["ticker"] for r in conn.execute(
                    "SELECT ticker FROM stock_signals WHERE ticker IN ('PQSWEEP','PQHELD2','PQOTHER')").fetchall()}
                profile_left = conn.execute("SELECT 1 FROM asset_profiles WHERE ticker = 'PQSWEEP'").fetchone()
            finally:
                conn.close()
        finally:
            _clear_queue(tickers)

        assert removed == 4
        assert left == {"PQHELD2", "PQOTHER"}
        assert profile_left is None
        assert not (history / "PQSWEEP.parquet").exists()
        assert not (fundamentals / "PQSWEEP.json").exists()
        assert (history / "PQHELD2.parquet").exists()
        assert (history / "PQOTHER.parquet").exists()

    def test_sweep_with_empty_blacklist_is_a_no_op(self, tmp_path, ticker_dirs):
        import profile_engine
        bl = tmp_path / "freetrade_blacklist.json"
        bl.write_text("[]")
        with patch("profile_engine.BLACKLIST_PATH", bl):
            assert profile_engine.purge_blacklisted_tickers() == 0

    def test_audit_runs_the_sweep_before_picking_a_batch(self, empty_blacklist):
        import profile_engine
        calls = []
        with patch("profile_engine.purge_blacklisted_tickers", side_effect=lambda: calls.append("sweep") or 0), \
             patch("profile_engine.get_portfolio_watchlist_tickers", return_value=[]), \
             patch("profile_engine.update_single_profile", side_effect=lambda t: calls.append("fetch") or True), \
             patch("profile_engine.time.sleep"):
            profile_engine.run_profile_audit(limit=1)
        assert calls and calls[0] == "sweep"
