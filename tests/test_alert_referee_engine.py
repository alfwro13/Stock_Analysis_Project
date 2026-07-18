"""
tests/test_alert_referee_engine.py — Alert Confidence Referee (Trap Monitor pilot) Tests

Covers:
  • readiness_status()      — sample counting, target/hard-min gating, ETA projection
  • train_referee_model()   — insufficient-data refusal vs. a real trained model
  • evaluate_alert()        — disabled / no-model / shadow-forced-below-target / active paths
  • log_veto_evaluation() + get_referee_summary() — shadow log persistence and aggregation
  • scheduler_jobs.run_alert_referee_training_job() — runner-level wiring
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
import alert_referee_engine as are

_ENGINE = are.TRAP_MONITOR_ENGINE


def _clear_referee_tables():
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM trap_phase_history")
        conn.execute("DELETE FROM alert_referee_models")
        conn.execute("DELETE FROM alert_referee_log")
        conn.commit()
    finally:
        conn.close()


def _seed_history_row(ticker, phase, direction_correct_14d, rsi=40.0, ema_distance=-3.0,
                       scan_date="2026-01-01"):
    conn = db.get_connection()
    try:
        conn.execute(
            """INSERT INTO trap_phase_history
               (ticker, phase, scan_date, scan_ts, close_price, rsi, ema_distance,
                bull_trap_vol_ratio, cap_vol_zscore, wyckoff_bb_width, direction_correct_14d)
               VALUES (?, ?, ?, ?, 100.0, ?, ?, 0.5, 1.0, 2.0, ?)""",
            (ticker, phase, scan_date, f"{scan_date} 12:00:00", rsi, ema_distance, direction_correct_14d),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_balanced_training_set(n_per_class=20):
    for i in range(n_per_class):
        _seed_history_row(f"POS{i}", "BULL_TRAP_RISK", 1, rsi=35.0 + i * 0.1, ema_distance=-4.0)
        _seed_history_row(f"NEG{i}", "ACTIVE_SELLOFF", 0, rsi=55.0 + i * 0.1, ema_distance=1.0)


@pytest.fixture(autouse=True)
def _isolate_referee_tables():
    """trap_phase_history is shared with test_bull_bear_trap_engine.py's own aggregate-accuracy
    tests, so seeded rows here must not survive past this file's tests (they'd skew phase-level
    accuracy percentages computed over the whole table there)."""
    _clear_referee_tables()
    yield
    _clear_referee_tables()


class TestReadinessStatus:
    def test_no_data_reports_zero_current(self):
        status = are.readiness_status(_ENGINE)
        assert status["current"] == 0
        assert status["can_train"] is False
        assert status["ready_for_active"] is False
        assert status["eta_days"] is None

    def test_rows_missing_features_are_not_counted(self):
        conn = db.get_connection()
        try:
            conn.execute(
                """INSERT INTO trap_phase_history (ticker, phase, scan_date, scan_ts, direction_correct_14d)
                   VALUES ('OLD1', 'BULL_TRAP_RISK', '2026-01-01', '2026-01-01 12:00:00', 1)"""
            )
            conn.commit()
        finally:
            conn.close()
        assert are.training_sample_count(_ENGINE) == 0

    def test_unresolved_rows_are_not_counted(self):
        conn = db.get_connection()
        try:
            conn.execute(
                """INSERT INTO trap_phase_history
                   (ticker, phase, scan_date, scan_ts, rsi, ema_distance)
                   VALUES ('PENDING1', 'BULL_TRAP_RISK', '2026-01-01', '2026-01-01 12:00:00', 40.0, -3.0)"""
            )
            conn.commit()
        finally:
            conn.close()
        assert are.training_sample_count(_ENGINE) == 0

    def test_current_reflects_resolved_feature_bearing_rows(self):
        _seed_balanced_training_set(n_per_class=5)
        with patch("alert_referee_engine.load_config", return_value={
            "SCHEDULING": {"ALERT_REFEREE_TRAINING": {"MIN_TRAINING_SAMPLES": 200}}
        }):
            status = are.readiness_status(_ENGINE)
        assert status["current"] == 10
        assert status["target"] == 200
        assert status["ready_for_active"] is False

    def test_ready_for_active_once_target_reached(self):
        _seed_balanced_training_set(n_per_class=5)
        with patch("alert_referee_engine.load_config", return_value={
            "SCHEDULING": {"ALERT_REFEREE_TRAINING": {"MIN_TRAINING_SAMPLES": 10}}
        }):
            status = are.readiness_status(_ENGINE)
        assert status["ready_for_active"] is True


class TestTrainRefereeModel:
    def test_insufficient_data_below_hard_minimum(self):
        _seed_balanced_training_set(n_per_class=2)
        result = are.train_referee_model(_ENGINE)
        assert result["status"] == "insufficient_data"
        assert result["sample_count"] == 4

    def test_single_class_refuses_training(self):
        for i in range(are._HARD_MIN_SAMPLES):
            _seed_history_row(f"SAME{i}", "BULL_TRAP_RISK", 1)
        result = are.train_referee_model(_ENGINE)
        assert result["status"] == "insufficient_data"
        assert "class" in result["message"]

    def test_trains_successfully_with_enough_balanced_data(self):
        _seed_balanced_training_set(n_per_class=20)
        with patch("alert_referee_engine.load_config", return_value={
            "SCHEDULING": {"ALERT_REFEREE_TRAINING": {"MODE": "active", "MIN_TRAINING_SAMPLES": 200,
                                                       "VETO_THRESHOLD": 0.3}}
        }):
            result = are.train_referee_model(_ENGINE)
        assert result["status"] == "trained"
        assert result["sample_count"] == 40
        # 40 < configured MIN_TRAINING_SAMPLES (200) so the effective mode must stay shadow
        # regardless of the configured "active" mode.
        assert result["effective_mode"] == "shadow"
        assert are._MODEL_PATH.exists()

        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM alert_referee_models WHERE engine=? ORDER BY id DESC LIMIT 1", (_ENGINE,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row["sample_count"] == 40

    def test_effective_mode_is_active_once_target_reached(self):
        _seed_balanced_training_set(n_per_class=20)
        with patch("alert_referee_engine.load_config", return_value={
            "SCHEDULING": {"ALERT_REFEREE_TRAINING": {"MODE": "active", "MIN_TRAINING_SAMPLES": 10,
                                                       "VETO_THRESHOLD": 0.3}}
        }):
            result = are.train_referee_model(_ENGINE)
        assert result["status"] == "trained"
        assert result["effective_mode"] == "active"


class TestEvaluateAlert:
    def setup_method(self):
        if are._MODEL_PATH.exists():
            are._MODEL_PATH.unlink()

    def _row(self):
        return {"rsi": 38.0, "ema_distance": -5.0, "bull_trap_vol_ratio": 0.4,
                "cap_vol_zscore": None, "wyckoff_bb_width": None}

    def _miss_pattern_row(self):
        # Matches _seed_balanced_training_set's "NEG"/direction_correct=0 class (ACTIVE_SELLOFF,
        # rsi ~55-60, ema_distance ~1.0) so a trained model assigns this a low fire_probability.
        return {"rsi": 57.0, "ema_distance": 1.0, "bull_trap_vol_ratio": None,
                "cap_vol_zscore": None, "wyckoff_bb_width": None}

    def test_disabled_never_evaluates(self):
        with patch("alert_referee_engine.load_config", return_value={
            "SCHEDULING": {"ALERT_REFEREE_TRAINING": {"ENABLED": False}}
        }):
            conn = db.get_connection()
            try:
                verdict = are.evaluate_alert(_ENGINE, "AAPL", "BULL_TRAP_RISK", self._row(), conn)
            finally:
                conn.close()
        assert verdict.mode == "off"
        assert verdict.vetoed is False
        assert verdict.model_available is False
        assert are.get_recent_evaluations(_ENGINE) == []

    def test_enabled_but_no_model_never_vetoes(self):
        with patch("alert_referee_engine.load_config", return_value={
            "SCHEDULING": {"ALERT_REFEREE_TRAINING": {"ENABLED": True, "MODE": "active"}}
        }):
            conn = db.get_connection()
            try:
                verdict = are.evaluate_alert(_ENGINE, "AAPL", "BULL_TRAP_RISK", self._row(), conn)
            finally:
                conn.close()
        assert verdict.model_available is False
        assert verdict.vetoed is False

    def test_shadow_mode_never_vetoes_but_logs_would_veto(self):
        _seed_balanced_training_set(n_per_class=20)
        cfg = {"SCHEDULING": {"ALERT_REFEREE_TRAINING": {
            "ENABLED": True, "MODE": "active", "MIN_TRAINING_SAMPLES": 200, "VETO_THRESHOLD": 0.5,
        }}}
        with patch("alert_referee_engine.load_config", return_value=cfg):
            are.train_referee_model(_ENGINE)
            conn = db.get_connection()
            try:
                verdict = are.evaluate_alert(_ENGINE, "AAPL", "ACTIVE_SELLOFF", self._miss_pattern_row(), conn)
            finally:
                conn.close()
        # 40 samples < MIN_TRAINING_SAMPLES (200) so mode is forced to shadow — never suppresses.
        assert verdict.mode == "shadow"
        assert verdict.vetoed is False
        assert verdict.model_available is True
        assert verdict.fire_probability < 0.5

        logged = are.get_recent_evaluations(_ENGINE)
        assert len(logged) == 1
        assert logged[0]["ticker"] == "AAPL"
        assert bool(logged[0]["vetoed"]) is True  # would-veto flag, not enforced

    def test_active_mode_vetoes_when_below_threshold(self):
        _seed_balanced_training_set(n_per_class=20)
        cfg = {"SCHEDULING": {"ALERT_REFEREE_TRAINING": {
            "ENABLED": True, "MODE": "active", "MIN_TRAINING_SAMPLES": 10, "VETO_THRESHOLD": 0.5,
        }}}
        with patch("alert_referee_engine.load_config", return_value=cfg):
            are.train_referee_model(_ENGINE)
            conn = db.get_connection()
            try:
                verdict = are.evaluate_alert(_ENGINE, "AAPL", "ACTIVE_SELLOFF", self._miss_pattern_row(), conn)
            finally:
                conn.close()
        assert verdict.mode == "active"
        assert verdict.vetoed is True


class TestGetRefereeSummary:
    def setup_method(self):
        if are._MODEL_PATH.exists():
            are._MODEL_PATH.unlink()

    def test_summary_reflects_log_and_model_state(self):
        _seed_balanced_training_set(n_per_class=20)
        cfg = {"SCHEDULING": {"ALERT_REFEREE_TRAINING": {
            "MODE": "shadow", "MIN_TRAINING_SAMPLES": 200, "VETO_THRESHOLD": 0.3, "ENABLED": True,
        }}}
        with patch("alert_referee_engine.load_config", return_value=cfg):
            are.train_referee_model(_ENGINE)
            conn = db.get_connection()
            try:
                are.evaluate_alert(_ENGINE, "AAPL", "BULL_TRAP_RISK", {"rsi": 38.0, "ema_distance": -5.0}, conn)
            finally:
                conn.close()
            summary = are.get_referee_summary(_ENGINE)

        assert summary["enabled"] is True
        assert summary["latest_model"] is not None
        assert summary["latest_model"]["sample_count"] == 40
        assert summary["log_total"] == 1
        assert len(summary["recent_log"]) == 1


class TestRunAlertRefereeTrainingJob:
    def test_runner_completes_with_insufficient_data(self):
        import scheduler_jobs
        # No seeded rows — the runner must complete cleanly, not raise.
        scheduler_jobs.run_alert_referee_training_job()

    def test_runner_trains_model_when_enough_data_seeded(self):
        import scheduler_jobs
        _seed_balanced_training_set(n_per_class=20)
        scheduler_jobs.run_alert_referee_training_job()
        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM alert_referee_models WHERE engine=? ORDER BY id DESC LIMIT 1",
                (are.TRAP_MONITOR_ENGINE,),
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row["sample_count"] == 40
