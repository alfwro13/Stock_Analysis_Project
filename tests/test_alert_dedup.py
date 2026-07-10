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

The db_conn fixture provides a single SQLite connection per test that is passed
to the methods that now require an explicit connection (M2 refactor).
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
        "MACRO_ALERTS": {
            "COOLDOWN_MINUTES": 120.0,
            "RETRIGGER_PERCENT": 2.0,
            "REARM_PERCENT": 3.0,
        },
        "TRAP_MONITOR_ALERTS": {
            "COOLDOWN_MINUTES": 120.0,
            "RETRIGGER_PERCENT": 3.0,
            "REARM_PERCENT": 5.0,
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


# ── fixtures ──────────────────────────────────────────────────────────────────

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


@pytest.fixture
def db_conn():
    """Provides a real SQLite connection for the duration of one test.

    Methods that previously opened their own connection now accept an explicit
    conn so the entire scan shares one connection (M2 refactor). Tests use this
    fixture to satisfy that requirement; the connection is closed after each test.
    """
    conn = _conn()
    yield conn
    conn.close()


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

    def test_macro_reads_dedicated_macro_alerts_block(self, orch):
        # Macro must use MACRO_ALERTS, not silently inherit MOONSHOT_ALERTS.
        orch.config = {
            "NOTIFICATIONS": {
                "MACRO_ALERTS": {"COOLDOWN_MINUTES": 240.0, "RETRIGGER_PERCENT": 1.5, "REARM_PERCENT": 2.0},
                "MOONSHOT_ALERTS": {"COOLDOWN_MINUTES": 999.0},  # must NOT be used
            }
        }
        s = orch._dedup_settings("Macro")
        assert s["cooldown_minutes"] == 240.0
        assert s["retrigger_percent"] == 1.5

    def test_market_stress_reads_market_stress_alerts_block(self, orch):
        orch.config = {
            "NOTIFICATIONS": {
                "MARKET_STRESS_ALERTS": {"COOLDOWN_MINUTES": 60.0, "RETRIGGER_PERCENT": 1.0, "REARM_PERCENT": 1.5},
                "MACRO_ALERTS": {"COOLDOWN_MINUTES": 999.0},  # must NOT be used
            }
        }
        s = orch._dedup_settings("MarketStress")
        assert s["cooldown_minutes"] == 60.0
        assert s["retrigger_percent"] == 1.0
        assert s["rearm_percent"] == 1.5

    def test_trap_monitor_reads_trap_monitor_alerts_block(self, orch):
        # TrapMonitor must use its own dedicated block, not silently inherit MACRO_ALERTS.
        orch.config = {
            "NOTIFICATIONS": {
                "TRAP_MONITOR_ALERTS": {"COOLDOWN_MINUTES": 90.0, "RETRIGGER_PERCENT": 3.0, "REARM_PERCENT": 5.0},
                "MACRO_ALERTS": {"COOLDOWN_MINUTES": 999.0},  # must NOT be used
            }
        }
        s = orch._dedup_settings("TrapMonitor")
        assert s["cooldown_minutes"] == 90.0
        assert s["retrigger_percent"] == 3.0
        assert s["rearm_percent"] == 5.0

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

    def test_case1_no_prior_state_fires(self, orch, db_conn):
        """Case 1: no row exists → gate clears the alert."""
        assert orch._evaluate_alert_gate("Crash", TEST_TICKER, 100.0, CRASH_REASON, db_conn) is False

    def test_case1b_stale_row_from_yesterday_same_price_suppressed(self, orch, db_conn):
        """Regression: a UTC day rollover must NOT auto-fire an unchanged condition carried
        over from a prior day - only genuine deterioration should retrigger it."""
        fp = orch._condition_fingerprint(CRASH_REASON)
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, YESTERDAY + " 10:00:00", 0, YESTERDAY)
        assert orch._evaluate_alert_gate("Crash", TEST_TICKER, 100.0, CRASH_REASON, db_conn) is True

    def test_case1b_stale_row_from_yesterday_worsened_fires(self, orch, db_conn):
        """A condition carried over from a prior day still fires once cooldown has elapsed
        AND the price has genuinely worsened further."""
        fp = orch._condition_fingerprint(CRASH_REASON)
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, YESTERDAY + " 10:00:00", 0, YESTERDAY)
        # 100 → 97 = -3% further decline, exceeds RETRIGGER 2.0%; cooldown (>1 day) long elapsed
        assert orch._evaluate_alert_gate("Crash", TEST_TICKER, 97.0, CRASH_REASON, db_conn) is False

    def test_case2_different_fingerprint_fires(self, orch, db_conn):
        """Case 2: a different condition class fires even if still in cooldown."""
        # Seed a suppressed row for CRASH_REASON
        fp = orch._condition_fingerprint(CRASH_REASON)
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, TODAY + " 10:00:00", 0, TODAY)
        # Ask the gate about a completely different reason
        assert orch._evaluate_alert_gate("Crash", TEST_TICKER, 100.0, OTHER_REASON, db_conn) is False

    def test_case3_same_fingerprint_armed_fires(self, orch, db_conn):
        """Case 3: same fingerprint but armed=1 → fires (first fire of a new event)."""
        fp = orch._condition_fingerprint(CRASH_REASON)
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, TODAY + " 10:00:00", 1, TODAY)
        assert orch._evaluate_alert_gate("Crash", TEST_TICKER, 100.0, CRASH_REASON, db_conn) is False

    def test_case4a_recovery_suppresses_and_rearms(self, orch, db_conn):
        """Case 4a: price recovered >= REARM_PERCENT (3%) → suppress + flip armed=1."""
        fp = orch._condition_fingerprint(CRASH_REASON)
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, TODAY + " 10:00:00", 0, TODAY)
        # 100 → 103.5 = +3.5% recovery for Crash (price rose back), exceeds REARM 3.0%
        result = orch._evaluate_alert_gate("Crash", TEST_TICKER, 103.5, CRASH_REASON, db_conn)
        assert result is True  # suppressed this scan
        row = _read_alert_state("Crash", TEST_TICKER)
        assert row["armed"] == 1  # re-armed for next genuine breach

    def test_case4a_borderline_recovery_just_below_rearm_stays_suppressed(self, orch, db_conn):
        """Case 4a boundary: 2.9% recovery < REARM 3.0% → stays in case 4b/4c, not re-armed."""
        fp = orch._condition_fingerprint(CRASH_REASON)
        recent_ts = (datetime.utcnow() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, recent_ts, 0, TODAY)
        # +2.9% recovery, just below REARM threshold — does NOT re-arm
        result = orch._evaluate_alert_gate("Crash", TEST_TICKER, 102.9, CRASH_REASON, db_conn)
        assert result is True
        row = _read_alert_state("Crash", TEST_TICKER)
        assert row["armed"] == 0  # still suppressed, not re-armed

    def test_case4b_cooldown_elapsed_and_worsened_fires(self, orch, db_conn):
        """Case 4b: cooldown elapsed AND price fell further >= RETRIGGER (2%) → fires."""
        fp = orch._condition_fingerprint(CRASH_REASON)
        old_ts = (datetime.utcnow() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, old_ts, 0, TODAY)
        # 100 → 97 = -3.0% for Crash (worsened), exceeds RETRIGGER 2.0%
        assert orch._evaluate_alert_gate("Crash", TEST_TICKER, 97.0, CRASH_REASON, db_conn) is False

    def test_case4b_worsened_but_cooldown_not_elapsed_suppresses(self, orch, db_conn):
        """Case 4c: price worsened enough but cooldown hasn't elapsed → still suppressed."""
        fp = orch._condition_fingerprint(CRASH_REASON)
        recent_ts = (datetime.utcnow() - timedelta(minutes=30)).strftime("%Y-%m-%d %H:%M:%S")
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, recent_ts, 0, TODAY)
        # Price fell 5% — clearly worsened — but only 30 min elapsed (< 120 min cooldown)
        assert orch._evaluate_alert_gate("Crash", TEST_TICKER, 95.0, CRASH_REASON, db_conn) is True

    def test_case4c_cooldown_elapsed_but_insufficient_deterioration_suppresses(self, orch, db_conn):
        """Case 4c: cooldown elapsed but price only moved 1% (< RETRIGGER 2%) → suppressed."""
        fp = orch._condition_fingerprint(CRASH_REASON)
        old_ts = (datetime.utcnow() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, old_ts, 0, TODAY)
        # 100 → 99 = only 1% worse, less than RETRIGGER 2.0%
        assert orch._evaluate_alert_gate("Crash", TEST_TICKER, 99.0, CRASH_REASON, db_conn) is True

    def test_no_price_with_suppressed_state_stays_suppressed(self, orch, db_conn):
        """Case 4 fallback: current_price=None when suppressed → suppress (safe side)."""
        fp = orch._condition_fingerprint(CRASH_REASON)
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, TODAY + " 10:00:00", 0, TODAY)
        assert orch._evaluate_alert_gate("Crash", TEST_TICKER, None, CRASH_REASON, db_conn) is True


class TestEvaluateAlertGateMoonshot:
    """Directional logic is mirrored for Moonshot: 'worsened' means price rose further."""

    def test_moonshot_worsened_is_price_rising(self, orch, db_conn):
        """Moonshot: price rising further past cooldown+retrigger threshold fires."""
        fp = orch._condition_fingerprint(MOON_REASON)
        old_ts = (datetime.utcnow() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        _seed_alert_state("Moonshot", TEST_TICKER, fp, 100.0, old_ts, 0, TODAY)
        # 100 → 103 = +3.0% further spike, exceeds RETRIGGER 2.0%
        assert orch._evaluate_alert_gate("Moonshot", TEST_TICKER, 103.0, MOON_REASON, db_conn) is False

    def test_moonshot_recovered_is_price_falling(self, orch, db_conn):
        """Moonshot: price falling back >= REARM_PERCENT → suppress + re-arm."""
        fp = orch._condition_fingerprint(MOON_REASON)
        _seed_alert_state("Moonshot", TEST_TICKER, fp, 100.0, TODAY + " 10:00:00", 0, TODAY)
        # 100 → 96.5 = -3.5% fall-back for Moonshot, exceeds REARM 3.0%
        result = orch._evaluate_alert_gate("Moonshot", TEST_TICKER, 96.5, MOON_REASON, db_conn)
        assert result is True
        row = _read_alert_state("Moonshot", TEST_TICKER)
        assert row["armed"] == 1

    def test_moonshot_no_prior_state_fires(self, orch, db_conn):
        assert orch._evaluate_alert_gate("Moonshot", TEST_TICKER, 150.0, MOON_REASON, db_conn) is False


class TestEvaluateAlertGateTrapMonitor:
    """TrapMonitor passes ema_distance (an already-signed percentage) instead of a price,
    so worsening/recovery is a raw point delta, not a relative pct-of-pct change."""

    TRAP_REASON = "TRAP MONITOR ACTIVE SELLOFF"

    def test_no_prior_state_fires(self, orch, db_conn):
        assert orch._evaluate_alert_gate("TrapMonitor", TEST_TICKER, -4.5, self.TRAP_REASON, db_conn) is False

    def test_unchanged_ema_distance_stays_suppressed(self, orch, db_conn):
        """No new data (ema_distance essentially unchanged) must not retrigger."""
        fp = orch._condition_fingerprint(self.TRAP_REASON)
        old_ts = (datetime.utcnow() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        _seed_alert_state("TrapMonitor", TEST_TICKER, fp, -4.5, old_ts, 0, TODAY)
        assert orch._evaluate_alert_gate("TrapMonitor", TEST_TICKER, -4.53, self.TRAP_REASON, db_conn) is True

    def test_deeper_ema_breach_past_cooldown_fires(self, orch, db_conn):
        fp = orch._condition_fingerprint(self.TRAP_REASON)
        old_ts = (datetime.utcnow() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        _seed_alert_state("TrapMonitor", TEST_TICKER, fp, -4.5, old_ts, 0, TODAY)
        # -4.5 → -8.0 = 3.5-point deeper breach, exceeds TRAP_MONITOR_ALERTS RETRIGGER (3.0 points)
        assert orch._evaluate_alert_gate("TrapMonitor", TEST_TICKER, -8.0, self.TRAP_REASON, db_conn) is False

    def test_recovery_suppresses_and_rearms(self, orch, db_conn):
        fp = orch._condition_fingerprint(self.TRAP_REASON)
        _seed_alert_state("TrapMonitor", TEST_TICKER, fp, -4.5, TODAY + " 10:00:00", 0, TODAY)
        # -4.5 → +1.0 = 5.5-point recovery, exceeds REARM (5.0 points)
        result = orch._evaluate_alert_gate("TrapMonitor", TEST_TICKER, 1.0, self.TRAP_REASON, db_conn)
        assert result is True
        row = _read_alert_state("TrapMonitor", TEST_TICKER)
        assert row["armed"] == 1


# ── record_alert_fired ────────────────────────────────────────────────────────

class TestRecordAlertFired:
    def test_creates_row_on_first_fire(self, orch, db_conn):
        orch.record_alert_fired("Crash", TEST_TICKER, 100.0, CRASH_REASON, db_conn)
        row = _read_alert_state("Crash", TEST_TICKER)
        assert row is not None
        assert row["armed"] == 0
        assert row["fire_count"] == 1
        assert row["last_price"] == 100.0
        assert row["state_date"] == TODAY

    def test_fingerprint_stored_matches_reason(self, orch, db_conn):
        orch.record_alert_fired("Crash", TEST_TICKER, 100.0, CRASH_REASON, db_conn)
        row = _read_alert_state("Crash", TEST_TICKER)
        assert row["fingerprint"] == orch._condition_fingerprint(CRASH_REASON)

    def test_increments_fire_count_on_same_day(self, orch, db_conn):
        orch.record_alert_fired("Crash", TEST_TICKER, 100.0, CRASH_REASON, db_conn)
        orch.record_alert_fired("Crash", TEST_TICKER, 97.0, CRASH_REASON, db_conn)
        row = _read_alert_state("Crash", TEST_TICKER)
        assert row["fire_count"] == 2
        assert row["last_price"] == 97.0

    def test_resets_fire_count_on_new_trading_day(self, orch, db_conn):
        fp = orch._condition_fingerprint(CRASH_REASON)
        _seed_alert_state("Crash", TEST_TICKER, fp, 100.0, YESTERDAY + " 10:00:00", 0, YESTERDAY, fire_count=5)
        orch.record_alert_fired("Crash", TEST_TICKER, 95.0, CRASH_REASON, db_conn)
        row = _read_alert_state("Crash", TEST_TICKER)
        assert row["fire_count"] == 1
        assert row["state_date"] == TODAY

    def test_last_fired_utc_is_recent(self, orch, db_conn):
        before = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        orch.record_alert_fired("Crash", TEST_TICKER, 100.0, CRASH_REASON, db_conn)
        after = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")
        row = _read_alert_state("Crash", TEST_TICKER)
        assert before <= row["last_fired_utc"] <= after

    def test_gate_suppresses_after_record(self, orch, db_conn):
        """End-to-end: fire → record → gate returns suppressed on next scan."""
        orch.record_alert_fired("Crash", TEST_TICKER, 100.0, CRASH_REASON, db_conn)
        # Same price, same reason, immediately after → must be suppressed
        result = orch._evaluate_alert_gate("Crash", TEST_TICKER, 100.0, CRASH_REASON, db_conn)
        assert result is True


# ── HoldingLimit (Set Targets) dedup + dispatch ─────────────────────────────────

HOLDING_LIMIT_ACCOUNT_ID = 999001
HOLDING_KEY_LOW = f"{HOLDING_LIMIT_ACCOUNT_ID}:{TEST_TICKER}:low"
HOLDING_KEY_HIGH = f"{HOLDING_LIMIT_ACCOUNT_ID}:{TEST_TICKER}:high"
LOW_REASON = "LOW TARGET REACHED"
HIGH_REASON = "HIGH TARGET REACHED"


def _clear_holding_limit_alert_state():
    conn = _conn()
    conn.execute(
        "DELETE FROM alert_state WHERE engine = 'HoldingLimit' AND ticker IN (?, ?)",
        (HOLDING_KEY_LOW, HOLDING_KEY_HIGH),
    )
    conn.commit()
    conn.close()


class TestEvaluateDailyAlertGate:
    """HoldingLimit uses a simple once-per-UTC-day gate rather than Crash/Moonshot's
    worsened/recovered/cooldown model — a price target is a static threshold that can
    legitimately be crossed back and forth several times in one session, and the user wants
    at most one notification per (account, ticker, direction) per calendar day, re-arming
    automatically at day rollover rather than on price recovery (requested 2026-07-10)."""

    def teardown_method(self):
        _clear_holding_limit_alert_state()

    def test_no_prior_state_fires(self, orch, db_conn):
        assert orch._evaluate_daily_alert_gate("HoldingLimit", HOLDING_KEY_LOW, db_conn) is False

    def test_already_fired_today_suppresses(self, orch, db_conn):
        orch.record_alert_fired("HoldingLimit", HOLDING_KEY_LOW, 100.0, LOW_REASON, db_conn)
        assert orch._evaluate_daily_alert_gate("HoldingLimit", HOLDING_KEY_LOW, db_conn) is True

    def test_fired_yesterday_fires_again_today(self, orch, db_conn):
        fp = orch._condition_fingerprint(LOW_REASON)
        _seed_alert_state("HoldingLimit", HOLDING_KEY_LOW, fp, 100.0, YESTERDAY + " 10:00:00", 0, YESTERDAY)
        assert orch._evaluate_daily_alert_gate("HoldingLimit", HOLDING_KEY_LOW, db_conn) is False

    def test_repeated_crossings_same_day_suppressed_regardless_of_price(self, orch, db_conn):
        """Price oscillating back and forth across the target several times in one day must
        still produce only the first notification — no worsened-price retrigger for this engine."""
        orch.record_alert_fired("HoldingLimit", HOLDING_KEY_LOW, 100.0, LOW_REASON, db_conn)
        # A much bigger breach later the same day must still be suppressed.
        assert orch._evaluate_daily_alert_gate("HoldingLimit", HOLDING_KEY_LOW, db_conn) is True

    def test_low_and_high_keys_track_independent_state(self, orch, db_conn):
        """Same underlying ticker; low and high targets must not share dedup state."""
        orch.record_alert_fired("HoldingLimit", HOLDING_KEY_LOW, 100.0, LOW_REASON, db_conn)
        assert orch._evaluate_daily_alert_gate("HoldingLimit", HOLDING_KEY_HIGH, db_conn) is False


class TestDispatchHoldingLimitAlerts:
    """_dispatch_holding_limit_alerts: (key, ticker, account_name, direction, limit_price,
    current_price, currency) tuples -> notify() + record_alert_fired() on success only."""

    def teardown_method(self):
        _clear_holding_limit_alert_state()

    def test_successful_notify_records_alert_state(self, orch, db_conn):
        alert_tuples = [(HOLDING_KEY_LOW, TEST_TICKER, "Trading", "low", 90.0, 85.0, "USD")]
        with patch("intraday_orchestrator.notify", return_value=True) as mock_notify:
            orch._dispatch_holding_limit_alerts(alert_tuples, db_conn)

        assert mock_notify.call_args.args[0] == "holding_limit_alert"
        row = _read_alert_state("HoldingLimit", HOLDING_KEY_LOW)
        assert row is not None
        assert row["last_price"] == 85.0

    def test_failed_notify_does_not_record_alert_state(self, orch, db_conn):
        alert_tuples = [(HOLDING_KEY_HIGH, TEST_TICKER, "Trading", "high", 200.0, 210.0, "USD")]
        with patch("intraday_orchestrator.notify", return_value=False):
            orch._dispatch_holding_limit_alerts(alert_tuples, db_conn)

        assert _read_alert_state("HoldingLimit", HOLDING_KEY_HIGH) is None


class TestCheckHoldingLimits:
    """_check_holding_limits: shared by the main portfolio loop and the target-only loop
    (watchlist tickers with a target but no holding) so the price-target check itself is
    never duplicated between the two call sites."""

    def teardown_method(self):
        _clear_holding_limit_alert_state()

    def test_low_breach_appends_alert(self, orch, db_conn):
        alerts = []
        limits_by_ticker = {TEST_TICKER: {HOLDING_LIMIT_ACCOUNT_ID: {"low_limit": 90.0, "high_limit": None}}}
        names = {HOLDING_LIMIT_ACCOUNT_ID: "Watchlist"}
        orch._check_holding_limits(TEST_TICKER, 85.0, "USD", limits_by_ticker, names, db_conn, alerts)

        assert len(alerts) == 1
        key, ticker, account_name, direction, limit_price, current_price, currency = alerts[0]
        assert direction == "low"
        assert account_name == "Watchlist"
        assert current_price == 85.0

    def test_high_breach_appends_alert(self, orch, db_conn):
        alerts = []
        limits_by_ticker = {TEST_TICKER: {HOLDING_LIMIT_ACCOUNT_ID: {"low_limit": None, "high_limit": 100.0}}}
        names = {HOLDING_LIMIT_ACCOUNT_ID: "Watchlist"}
        orch._check_holding_limits(TEST_TICKER, 110.0, "USD", limits_by_ticker, names, db_conn, alerts)

        assert len(alerts) == 1
        assert alerts[0][3] == "high"

    def test_no_limits_for_ticker_appends_nothing(self, orch, db_conn):
        alerts = []
        orch._check_holding_limits(TEST_TICKER, 100.0, "USD", {}, {}, db_conn, alerts)
        assert alerts == []

    def test_price_within_range_appends_nothing(self, orch, db_conn):
        alerts = []
        limits_by_ticker = {TEST_TICKER: {HOLDING_LIMIT_ACCOUNT_ID: {"low_limit": 50.0, "high_limit": 150.0}}}
        names = {HOLDING_LIMIT_ACCOUNT_ID: "Watchlist"}
        orch._check_holding_limits(TEST_TICKER, 100.0, "USD", limits_by_ticker, names, db_conn, alerts)
        assert alerts == []

    def test_unknown_account_id_skipped(self, orch, db_conn):
        """A holding_price_limits row for a soft-deleted account (missing from account_names)
        must not fire, even if its price threshold is technically breached."""
        alerts = []
        limits_by_ticker = {TEST_TICKER: {999999: {"low_limit": 90.0, "high_limit": None}}}
        orch._check_holding_limits(TEST_TICKER, 85.0, "USD", limits_by_ticker, {}, db_conn, alerts)
        assert alerts == []


class TestComputeTargetOnlyTickers:
    """_compute_target_only_tickers: the set of tickers with an active target that aren't
    already in the held set — e.g. a Watchlist-only ticker with a Position Target set."""

    def test_ticker_with_target_not_held_is_included(self, orch):
        held = set()
        limits_by_ticker = {"ZZWATCH": {1: {"low_limit": 10.0, "high_limit": None}}}
        assert orch._compute_target_only_tickers(held, limits_by_ticker) == ["ZZWATCH"]

    def test_ticker_with_target_already_held_is_excluded(self, orch):
        held = {"AAPL"}
        limits_by_ticker = {"AAPL": {1: {"low_limit": 10.0, "high_limit": None}}}
        assert orch._compute_target_only_tickers(held, limits_by_ticker) == []

    def test_no_targets_returns_empty(self, orch):
        assert orch._compute_target_only_tickers({"AAPL"}, {}) == []

    def test_mixed_held_and_target_only(self, orch):
        held = {"AAPL"}
        limits_by_ticker = {
            "AAPL": {1: {"low_limit": 10.0, "high_limit": None}},
            "ZZWATCH": {2: {"low_limit": 5.0, "high_limit": None}},
        }
        assert orch._compute_target_only_tickers(held, limits_by_ticker) == ["ZZWATCH"]


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

    def test_writes_row_with_status_sent(self, orch, db_conn):
        orch.log_notification_feed("Crash", f"Test message {self.MSG_MARKER}", db_conn)
        conn = _conn()
        row = conn.execute(
            "SELECT status FROM system_notifications WHERE message_text LIKE ? LIMIT 1",
            (f"%{self.MSG_MARKER}%",),
        ).fetchone()
        conn.close()
        assert row is not None
        assert row["status"] == "sent"

    def test_does_not_affect_alert_state(self, orch, db_conn):
        """Feed writes must be completely decoupled from the suppression ledger."""
        orch.log_notification_feed("Crash", f"Test message {self.MSG_MARKER}", db_conn)
        row = _read_alert_state("Crash", TEST_TICKER)
        assert row is None  # alert_state untouched

    def test_gate_still_fires_after_feed_write(self, orch, db_conn):
        """Writing to the feed must not suppress subsequent alerts."""
        orch.log_notification_feed("Crash", f"Test message {self.MSG_MARKER}", db_conn)
        result = orch._evaluate_alert_gate("Crash", TEST_TICKER, 100.0, CRASH_REASON, db_conn)
        assert result is False  # still fires; feed write has no bearing on gate


# ── N2: alert_state pruning ───────────────────────────────────────────────────

class TestPruneAlertState:
    OLD_TICKER = "_PRUNE_OLD"
    RECENT_TICKER = "_PRUNE_RECENT"

    def teardown_method(self):
        conn = _conn()
        for t in (self.OLD_TICKER, self.RECENT_TICKER, TEST_TICKER):
            conn.execute("DELETE FROM alert_state WHERE ticker = ?", (t,))
        conn.commit()
        conn.close()

    def test_deletes_rows_older_than_7_days(self, orch, db_conn):
        eight_days_ago = (datetime.utcnow() - timedelta(days=8)).strftime("%Y-%m-%d")
        _seed_alert_state("Crash", self.OLD_TICKER, "abc", 100.0,
                          eight_days_ago + " 10:00:00", 0, eight_days_ago)
        orch._prune_alert_state(db_conn)
        assert _read_alert_state("Crash", self.OLD_TICKER) is None

    def test_retains_rows_within_7_days(self, orch, db_conn):
        three_days_ago = (datetime.utcnow() - timedelta(days=3)).strftime("%Y-%m-%d")
        _seed_alert_state("Crash", self.RECENT_TICKER, "abc", 100.0,
                          three_days_ago + " 10:00:00", 0, three_days_ago)
        orch._prune_alert_state(db_conn)
        assert _read_alert_state("Crash", self.RECENT_TICKER) is not None

    def test_retains_todays_rows(self, orch, db_conn):
        _seed_alert_state("Crash", TEST_TICKER, "abc", 100.0, TODAY + " 10:00:00", 0, TODAY)
        orch._prune_alert_state(db_conn)
        assert _read_alert_state("Crash", TEST_TICKER) is not None

    def test_prune_on_empty_table_does_not_raise(self, orch, db_conn):
        orch._prune_alert_state(db_conn)  # must not throw


# ── N3: Macro engine uses dedicated config and explicit direction ──────────────

class TestMacroEngine:
    MACRO_REASON = "YIELD SURGE ^TYX"

    def teardown_method(self):
        _clear_alert_state()

    def test_macro_gate_fires_on_no_prior_state(self, orch, db_conn):
        assert orch._evaluate_alert_gate("Macro", TEST_TICKER, 4.5, self.MACRO_REASON, db_conn) is False

    def test_macro_rising_yield_counts_as_worsened(self, orch, db_conn):
        """Macro: yield rising further past cooldown+retrigger fires (worsening = higher yield)."""
        fp = orch._condition_fingerprint(self.MACRO_REASON)
        old_ts = (datetime.utcnow() - timedelta(hours=3)).strftime("%Y-%m-%d %H:%M:%S")
        _seed_alert_state("Macro", TEST_TICKER, fp, 4.5, old_ts, 0, TODAY)
        # 4.5 → 4.62 = +2.67% rise in yield, exceeds RETRIGGER 2.0%
        assert orch._evaluate_alert_gate("Macro", TEST_TICKER, 4.62, self.MACRO_REASON, db_conn) is False

    def test_macro_falling_yield_triggers_rearm(self, orch, db_conn):
        """Macro: yield falling back >= REARM_PERCENT suppresses and re-arms."""
        fp = orch._condition_fingerprint(self.MACRO_REASON)
        _seed_alert_state("Macro", TEST_TICKER, fp, 4.5, TODAY + " 10:00:00", 0, TODAY)
        # 4.5 → 4.36 = -3.1% fall, exceeds REARM 3.0% → re-arm
        result = orch._evaluate_alert_gate("Macro", TEST_TICKER, 4.36, self.MACRO_REASON, db_conn)
        assert result is True
        row = _read_alert_state("Macro", TEST_TICKER)
        assert row["armed"] == 1

    def test_macro_uses_macro_alerts_config_not_moonshot(self, orch):
        """Macro cooldown must come from MACRO_ALERTS, not MOONSHOT_ALERTS."""
        orch.config = {
            "NOTIFICATIONS": {
                "MACRO_ALERTS": {"COOLDOWN_MINUTES": 240.0, "RETRIGGER_PERCENT": 1.5, "REARM_PERCENT": 2.0},
                "MOONSHOT_ALERTS": {"COOLDOWN_MINUTES": 999.0},
            }
        }
        s = orch._dedup_settings("Macro")
        assert s["cooldown_minutes"] == 240.0, "Macro is reading MOONSHOT_ALERTS instead of MACRO_ALERTS"


# ── N4: IntradayBottomEngine._disarm_alert stores parseable last_fired_utc ────

class TestDipRadarDisarmAlert:
    """
    REGRESSION: _disarm_alert previously used datetime.utcnow().isoformat() which
    produces "2026-06-07T15:30:00.123456". _evaluate_alert_gate parses with
    strptime(..., "%Y-%m-%d %H:%M:%S"), which raises ValueError on that format,
    so last_fired was always None and cooldowns never worked.
    """

    DIP_TICKER = "_DIP_DISARM_TEST"

    def teardown_method(self):
        conn = _conn()
        conn.execute("DELETE FROM alert_state WHERE ticker = ?", (self.DIP_TICKER,))
        conn.commit()
        conn.close()

    def test_disarm_stores_parseable_last_fired_utc(self):
        """last_fired_utc written by _disarm_alert must parse with %Y-%m-%d %H:%M:%S."""
        from intraday_bottom_engine import IntradayBottomEngine
        import database as _db_mod
        engine = IntradayBottomEngine.__new__(IntradayBottomEngine)
        engine._get_connection = lambda: _conn()
        # Arm first so there is a row to disarm
        engine.arm_alert(self.DIP_TICKER)
        engine._disarm_alert(self.DIP_TICKER)
        row = _read_alert_state("dip_radar", self.DIP_TICKER)
        assert row is not None
        assert row["armed"] == 0
        # The critical assertion: value must parse with the format _evaluate_alert_gate uses
        from datetime import datetime
        parsed = datetime.strptime(row["last_fired_utc"], "%Y-%m-%d %H:%M:%S")
        assert parsed is not None

    def test_disarm_uses_utc_date_for_state_date(self):
        """state_date must match today's UTC date, not local-clock date."""
        from datetime import datetime, timezone
        from intraday_bottom_engine import IntradayBottomEngine
        engine = IntradayBottomEngine.__new__(IntradayBottomEngine)
        engine._get_connection = lambda: _conn()
        engine.arm_alert(self.DIP_TICKER)
        engine._disarm_alert(self.DIP_TICKER)
        row = _read_alert_state("dip_radar", self.DIP_TICKER)
        expected = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        assert row["state_date"] == expected
