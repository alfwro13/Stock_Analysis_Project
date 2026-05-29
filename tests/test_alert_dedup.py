"""
tests/test_alert_dedup.py  ── ALERT DEDUPLICATION SYSTEM

Tests for the alert_state-based deduplication logic added to IntradayOrchestrator:

  _condition_fingerprint   - stable hash of condition class, ignoring numeric detail
  _dedup_settings          - config knob resolution with safe fallbacks
  _evaluate_alert_gate     - all 7 suppression decision branches
  record_alert_fired       - state persistence after confirmed dispatch
  log_notification_feed    - feed write decoupled from suppression ledger

All tests use the session-level temp DB created by conftest.py (init_db() has
already run, so alert_state exists). Engine instances are mocked to avoid any
network calls; load_config is patched to a controlled dict so knob values are
deterministic across environments.
"""

import sys
import sqlite3
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db


# ── shared helpers ────────────────────────────────────────────────────────────

TEST_TICKER = "_DEDUP_TEST"   # unlikely to collide with real data

TODAY = datetime.utcnow().strftime("%Y-%m-%d")
YESTERDAY = (datetime.utcnow() - timedelta(days=1)).strftime("%Y-%m-%d")

TEST_CONFIG = {
    "NOTIFICATIONS": {
        "CRASH_ALERTS": {
            "COOLDOWN_MINUTES": 120.0,
            "RETRIGGER_PERCENT": 2.0,
            "REARM_PERCENT": 3.0,
        },
        "MOONSHOT_ALERTS": {
            "COOLDOWN_MINUTES": 120.0,
            "RETRIGGER_PERCENT": 2.0,
            "REARM_PERCENT": 3.0,
        },
    }
}


def _conn():
    conn = sqlite3.connect(_db.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_alert_state(engine, ticker, fingerprint, last_price, last_fired_utc, armed,
                      state_date, fire_count=1):
    conn = _conn()
    conn.execute(
        "INSERT OR REPLACE INTO alert_state "
        "(engine, ticker, fingerprint, last_price, last_fired_utc, armed, fire_count, state_date) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (engine, ticker, fingerprint, last_price, last_fired_utc, armed, fire_count, state_date),
    )
    conn.commit()
    conn.close()


def _read_alert_state(engine, ticker):
    conn = _conn()
    row = conn.execute(
        "SELECT * FROM alert_state WHERE engine = ? AND ticker = ?", (engine, ticker)
    ).fetchone()
    conn.close()
    return dict(row) if row else None


def _clear_alert_state():
    conn = _conn()
    conn.execute("DELETE FROM alert_state WHERE ticker = ?", (TEST_TICKER,))
    conn.commit()
    conn.close()


# ── fixture ───────────────────────────────────────────────────────────────────

@pytest.fixture
def orch():
    """IntradayOrchestrator with engines mocked and a controlled test config.

    Patches are held open for the duration of the test so that any accidental
    engine call during test execution is intercepted rather than hitting the
    network. Alert-state rows for TEST_TICKER are cleaned up after each test.
    """
    with (
        patch("intraday_orchestrator.CrashEngine"),
        patch("intraday_orchestrator.MoonshotEngine"),
        patch("intraday_orchestrator.load_config", return_value=TEST_CONFIG),
    ):
        from intraday_orchestrator import IntradayOrchestrator
        o = IntradayOrchestrator()
        yield o
    _clear_alert_state()


# ── _condition_fingerprint ────────────────────────────────────────────────────

class TestConditionFingerprint:
    CRASH_REASON = "SESSION CRASH: -5.2% from open | ATR Stop Breached"

    def test_same_reason_is_deterministic(self, orch):
        fp1 = orch._condition_fingerprint(self.CRASH_REASON)
        fp2 = orch._condition_fingerprint(self.CRASH_REASON)
        assert fp1 == fp2

    def test_numeric_variation_does_not_change_fingerprint(self, orch):
        """Price fluctuations in the reason string must NOT change the fingerprint."""
        r1 = "SESSION CRASH: -5.2% from open"
        r2 = "SESSION CRASH: -7.8% from open"
        assert orch._condition_fingerprint(r1) == orch._condition_fingerprint(r2)

    def test_different_condition_class_gives_different_fingerprint(self, orch):
        r1 = "SESSION CRASH: -5.2% from open"
        r2 = "FLASH CRASH: exceeded ATR threshold today"
        assert orch._condition_fingerprint(r1) != orch._condition_fingerprint(r2)

    def test_empty_reason_returns_generic(self, orch):
        assert orch._condition_fingerprint("") == "generic"

    def test_result_is_16_hex_chars(self, orch):
        fp = orch._condition_fingerprint(self.CRASH_REASON)
        assert len(fp) == 16
        assert all(c in "0123456789abcdef" for c in fp)


# ── _dedup_settings ───────────────────────────────────────────────────────────

class TestDedupSettings:
    def test_crash_reads_crash_alerts_block(self, orch):
        s = orch._dedup_settings("Crash")
        assert s["cooldown_minutes"] == 120.0
        assert s["retrigger_percent"] == 2.0
        assert s["rearm_percent"] == 3.0

    def test_moonshot_reads_moonshot_alerts_block(self, orch):
        s = orch._dedup_settings("Moonshot")
        assert s["cooldown_minutes"] == 120.0
        assert s["retrigger_percent"] == 2.0
        assert s["rearm_percent"] == 3.0

    def test_unknown_engine_falls_back_to_moonshot_block(self, orch):
        # "Macro" is not "Crash", so the else branch uses MOONSHOT_ALERTS
        s = orch._dedup_settings("Macro")
        assert s["cooldown_minutes"] == 120.0

    def test_missing_key_uses_safe_fallback(self, orch):
        orch.config = {"NOTIFICATIONS": {"CRASH_ALERTS": {}}}
        s = orch._dedup_settings("Crash")
        assert s["cooldown_minutes"] == 120.0
        assert s["retrigger_percent"] == 2.0
        assert s["rearm_percent"] == 3.0


# ── _evaluate_alert_gate ──────────────────────────────────────────────────────

CRASH_REASON = "SESSION CRASH from open today"
MOON_REASON = "FLASH SPIKE from session open"
OTHER_REASON = "FLASH CRASH exceeded ATR threshold"


class TestEvaluateAlertGateCrash:
    """All gate branches for the Crash engine."""

    def test_case1_no_prior_state_fires(self, orch):
        """Case 1: no row exists → gate clears the alert."""
        assert orch._evaluate_alert_gate("Crash", TEST_TICKER, 100.0, CRASH_REASON) is False

    def test_case1b_stale_row_from_yesterday_fires(self, orch):
        """Case 1b: row exists but belongs to a prior trading day → treat as new day."""
        fp = orch._condition_fingerprint(CRASH_REASON)
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, YESTERDAY + " 10:00:00", 0, YESTERDAY)
        assert orch._evaluate_alert_gate("Crash", TEST_TICKER, 100.0, CRASH_REASON) is False

    def test_case2_different_fingerprint_fires(self, orch):
        """Case 2: a different condition class fires even if still in cooldown."""
        # Seed a suppressed row for CRASH_REASON
        fp = orch._condition_fingerprint(CRASH_REASON)
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, TODAY + " 10:00:00", 0, TODAY)
        # Ask the gate about a completely different reason
        assert orch._evaluate_alert_gate("Crash", TEST_TICKER, 100.0, OTHER_REASON) is False

    def test_case3_same_fingerprint_armed_fires(self, orch):
        """Case 3: same fingerprint but armed=1 → fires (first fire of a new event)."""
        fp = orch._condition_fingerprint(CRASH_REASON)
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, TODAY + " 10:00:00", 1, TODAY)
        assert orch._evaluate_alert_gate("Crash", TEST_TICKER, 100.0, CRASH_REASON) is False

    def test_case4a_recovery_suppresses_and_rearms(self, orch):
        """Case 4a: price recovered >= REARM_PERCENT (3%) → suppress + flip armed=1."""
        fp = orch._condition_fingerprint(CRASH_REASON)
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, TODAY + " 10:00:00", 0, TODAY)
        # 100 → 103.5 = +3.5% recovery for Crash (price rose back), exceeds REARM 3.0%
        result = orch._evaluate_alert_gate("Crash", TEST_TICKER, 103.5, CRASH_REASON)
        assert result is True  # suppressed this scan
        row = _read_alert_state("Crash", TEST_TICKER)
        assert row["armed"] == 1  # re-armed for next genuine breach

    def test_case4a_borderline_recovery_just_below_rearm_stays_suppressed(self, orch):
        """Case 4a boundary: 2.9% recovery < REARM 3.0% → stays in case 4b/4c, not re-armed."""
        fp = orch._condition_fingerprint(CRASH_REASON)
        recent_ts = (datetime.utcnow() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, recent_ts, 0, TODAY)
        # +2.9% recovery, just below REARM threshold — does NOT re-arm
        result = orch._evaluate_alert_gate("Crash", TEST_TICKER, 102.9, CRASH_REASON)
        assert result is True
        row = _read_alert_state("Crash", TEST_TICKER)
        assert row["armed"] == 0  # still suppressed, not re-armed

    def test_case4b_cooldown_elapsed_and_worsened_fires(self, orch):
        """Case 4b: cooldown elapsed AND price fell further >= RETRIGGER (2%) → fires."""
        fp = orch._condition_fingerprint(CRASH_REASON)
        old_ts = (datetime.utcnow() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, old_ts, 0, TODAY)
        # 100 → 97 = -3.0% for Crash (worsened), exceeds RETRIGGER 2.0%
        assert orch._evaluate_alert_gate("Crash", TEST_TICKER, 97.0, CRASH_REASON) is False

    def test_case4b_worsened_but_cooldown_not_elapsed_suppresses(self, orch):
        """Case 4c: price worsened enough but cooldown hasn't elapsed → still suppressed."""
        fp = orch._condition_fingerprint(CRASH_REASON)
        recent_ts = (datetime.utcnow() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, recent_ts, 0, TODAY)
        # Price fell 5% — clearly worsened — but only 30 min elapsed (< 120 min cooldown)
        assert orch._evaluate_alert_gate("Crash", TEST_TICKER, 95.0, CRASH_REASON) is True

    def test_case4c_cooldown_elapsed_but_insufficient_deterioration_suppresses(self, orch):
        """Case 4c: cooldown elapsed but price only moved 1% (< RETRIGGER 2%) → suppressed."""
        fp = orch._condition_fingerprint(CRASH_REASON)
        old_ts = (datetime.utcnow() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, old_ts, 0, TODAY)
        # 100 → 99 = only 1% worse, less than RETRIGGER 2.0%
        assert orch._evaluate_alert_gate("Crash", TEST_TICKER, 99.0, CRASH_REASON) is True

    def test_no_price_with_suppressed_state_stays_suppressed(self, orch):
        """Case 4 fallback: current_price=None when suppressed → suppress (safe side)."""
        fp = orch._condition_fingerprint(CRASH_REASON)
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, TODAY + " 10:00:00", 0, TODAY)
        assert orch._evaluate_alert_gate("Crash", TEST_TICKER, None, CRASH_REASON) is True


class TestEvaluateAlertGateMoonshot:
    """Directional logic is mirrored for Moonshot: 'worsened' means price rose further."""

    def test_moonshot_worsened_is_price_rising(self, orch):
        """Moonshot: price rising further past cooldown+retrigger threshold fires."""
        fp = orch._condition_fingerprint(MOON_REASON)
        old_ts = (datetime.utcnow() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        _seed_alert_state("Moonshot", TEST_TICKER, fp, 100.0, old_ts, 0, TODAY)
        # 100 → 103 = +3.0% further spike, exceeds RETRIGGER 2.0%
        assert orch._evaluate_alert_gate("Moonshot", TEST_TICKER, 103.0, MOON_REASON) is False

    def test_moonshot_recovered_is_price_falling(self, orch):
        """Moonshot: price falling back >= REARM_PERCENT → suppress + re-arm."""
        fp = orch._condition_fingerprint(MOON_REASON)
        _seed_alert_state("Moonshot", TEST_TICKER, fp, 100.0, TODAY + " 10:00:00", 0, TODAY)
        # 100 → 96.5 = -3.5% fall-back for Moonshot, exceeds REARM 3.0%
        result = orch._evaluate_alert_gate("Moonshot", TEST_TICKER, 96.5, MOON_REASON)
        assert result is True
        row = _read_alert_state("Moonshot", TEST_TICKER)
        assert row["armed"] == 1

    def test_moonshot_no_prior_state_fires(self, orch):
        assert orch._evaluate_alert_gate("Moonshot", TEST_TICKER, 150.0, MOON_REASON) is False


# ── record_alert_fired ────────────────────────────────────────────────────────

class TestRecordAlertFired:
    def test_creates_row_on_first_fire(self, orch):
        orch.record_alert_fired("Crash", TEST_TICKER, 100.0, CRASH_REASON)
        row = _read_alert_state("Crash", TEST_TICKER)
        assert row is not None
        assert row["armed"] == 0
        assert row["fire_count"] == 1
        assert row["last_price"] == 100.0
        assert row["state_date"] == TODAY

    def test_fingerprint_stored_matches_reason(self, orch):
        orch.record_alert_fired("Crash", TEST_TICKER, 100.0, CRASH_REASON)
        row = _read_alert_state("Crash", TEST_TICKER)
        assert row["fingerprint"] == orch._condition_fingerprint(CRASH_REASON)

    def test_increments_fire_count_on_same_day(self, orch):
        orch.record_alert_fired("Crash", TEST_TICKER, 100.0, CRASH_REASON)
        orch.record_alert_fired("Crash", TEST_TICKER, 97.0, CRASH_REASON)
        row = _read_alert_state("Crash", TEST_TICKER)
        assert row["fire_count"] == 2
        assert row["last_price"] == 97.0

    def test_resets_fire_count_on_new_trading_day(self, orch):
        fp = orch._condition_fingerprint(CRASH_REASON)
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, YESTERDAY + " 10:00:00", 0, YESTERDAY, fire_count=5)
        orch.record_alert_fired("Crash", TEST_TICKER, 95.0, CRASH_REASON)
        row = _read_alert_state("Crash", TEST_TICKER)
        assert row["fire_count"] == 1
        assert row["state_date"] == TODAY

    def test_last_fired_utc_is_recent(self, orch):
        before = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        orch.record_alert_fired("Crash", TEST_TICKER, 100.0, CRASH_REASON)
        after = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        row = _read_alert_state("Crash", TEST_TICKER)
        assert before <= row["last_fired_utc"] <= after

    def test_gate_suppresses_after_record(self, orch):
        """End-to-end: fire → record → gate returns suppressed on next scan."""
        orch.record_alert_fired("Crash", TEST_TICKER, 100.0, CRASH_REASON)
        # Same price, same reason, immediately after → must be suppressed
        result = orch._evaluate_alert_gate("Crash", TEST_TICKER, 100.0, CRASH_REASON)
        assert result is True


# ── log_notification_feed ─────────────────────────────────────────────────────

class TestLogNotificationFeed:
    MSG_MARKER = "__dedup_test_feed_marker__"

    def teardown_method(self):
        conn = _conn()
        conn.execute(
            "DELETE FROM system_notifications WHERE message_text LIKE ?",
            (f"%{self.MSG_MARKER}%",),
        )
        conn.commit()
        conn.close()

    def test_writes_row_with_status_sent(self, orch):
        orch.log_notification_feed("Crash", f"Test message {self.MSG_MARKER}")
        conn = _conn()
        row = conn.execute(
            "SELECT status FROM system_notifications WHERE message_text LIKE ? LIMIT 1",
            (f"%{self.MSG_MARKER}%",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["status"] == "sent"

    def test_does_not_affect_alert_state(self, orch):
        """Feed writes must be completely decoupled from the suppression ledger."""
        orch.log_notification_feed("Crash", f"Test message {self.MSG_MARKER}")
        row = _read_alert_state("Crash", TEST_TICKER)
        assert row is None  # alert_state untouched

    def test_gate_still_fires_after_feed_write(self, orch):
        """Writing to the feed must not suppress subsequent alerts."""
        orch.log_notification_feed("Crash", f"Test message {self.MSG_MARKER}")
        result = orch._evaluate_alert_gate("Crash", TEST_TICKER, 100.0, CRASH_REASON)
        assert result is False  # still fires; feed write has no bearing on gate
