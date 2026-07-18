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

import numpy as np
import pandas as pd
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


def _seed_bare_history_row(ticker, phase, scan_date, direction_correct_14d=None):
    """A row with no feature columns set — the shape a pre-migration/backfill-candidate row has."""
    conn = db.get_connection()
    try:
        conn.execute(
            """INSERT INTO trap_phase_history (ticker, phase, scan_date, scan_ts, close_price, direction_correct_14d)
               VALUES (?, ?, ?, ?, 100.0, ?)""",
            (ticker, phase, scan_date, f"{scan_date} 12:00:00", direction_correct_14d),
        )
        conn.commit()
    finally:
        conn.close()


def _active_selloff_df(end_date: str) -> pd.DataFrame:
    """Mirrors test_bull_bear_trap_engine.py's _make_active_selloff_df, dated to end exactly
    on end_date so it can double as a historical parquet fixture for backfill tests."""
    stable = list(np.full(8, 100.0))
    prices = stable[:]
    p = 100.0
    for i in range(17):
        p = p + 0.5 if i % 3 == 1 else p - 2.5
        prices.append(p)
    prices = np.array(prices)
    prices[-1] = prices[-2] - 1.0
    idx = pd.date_range(end=end_date, periods=25, freq="D")
    return pd.DataFrame({
        "Open": prices, "High": prices + 0.5, "Low": prices - 0.5, "Close": prices,
        "Volume": np.full(25, 1_000_000.0),
    }, index=idx)


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

    def test_backfill_available_counts_unbackfilled_resolved_rows(self):
        _seed_bare_history_row("BARE1", "BULL_TRAP_RISK", "2026-01-01", direction_correct_14d=1)
        _seed_bare_history_row("BARE2", "ACTIVE_SELLOFF", "2026-01-02", direction_correct_14d=0)
        status = are.readiness_status(_ENGINE)
        assert status["current"] == 0
        assert status["backfill_available"] == 2

    def test_backfill_available_excludes_unresolved_bare_rows(self):
        _seed_bare_history_row("BARE3", "BULL_TRAP_RISK", "2026-01-01", direction_correct_14d=None)
        status = are.readiness_status(_ENGINE)
        assert status["backfill_available"] == 0

    def test_can_train_after_backfill_true_when_backfill_would_cross_hard_min(self):
        for i in range(are._HARD_MIN_SAMPLES):
            _seed_bare_history_row(f"BARE{i}", "BULL_TRAP_RISK", f"2026-01-{(i % 28) + 1:02d}", direction_correct_14d=1)
        status = are.readiness_status(_ENGINE)
        assert status["can_train"] is False
        assert status["backfill_available"] == are._HARD_MIN_SAMPLES
        assert status["can_train_after_backfill"] is True

    def test_can_train_after_backfill_false_when_still_insufficient(self):
        _seed_bare_history_row("BARE4", "BULL_TRAP_RISK", "2026-01-01", direction_correct_14d=1)
        status = are.readiness_status(_ENGINE)
        assert status["can_train"] is False
        assert status["can_train_after_backfill"] is False


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


class TestBackfillHistoricalFeatures:
    def test_no_candidates_is_a_cheap_noop(self):
        result = are.backfill_historical_features()
        assert result == {"status": "done", "updated": 0, "skipped": 0, "total_candidates": 0}

    def test_skips_ticker_without_parquet(self, tmp_path):
        _seed_bare_history_row("NOPARQ1", "ACTIVE_SELLOFF", "2026-01-25")
        with patch("alert_referee_engine.HISTORICAL_DIR", tmp_path):
            result = are.backfill_historical_features()
        assert result["updated"] == 0
        assert result["skipped"] == 1
        assert are.training_sample_count(_ENGINE) == 0

    def test_fills_features_when_recomputed_phase_matches(self, tmp_path):
        end_date = "2026-01-25"
        _seed_bare_history_row("BACKFILL1", "ACTIVE_SELLOFF", end_date)
        _active_selloff_df(end_date).to_parquet(tmp_path / "BACKFILL1.parquet", engine="pyarrow")

        with patch("alert_referee_engine.HISTORICAL_DIR", tmp_path):
            result = are.backfill_historical_features()

        assert result["updated"] == 1
        assert result["skipped"] == 0

        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT rsi, ema_distance FROM trap_phase_history WHERE ticker='BACKFILL1'"
            ).fetchone()
        finally:
            conn.close()
        assert row["rsi"] is not None
        assert row["ema_distance"] is not None

    def test_skips_row_when_recomputed_phase_mismatches(self, tmp_path):
        end_date = "2026-01-25"
        # Row claims BULL_TRAP_RISK, but the parquet fixture actually reproduces ACTIVE_SELLOFF —
        # a stale/revised-data scenario that must never get backfilled with mismatched features.
        _seed_bare_history_row("MISMATCH1", "BULL_TRAP_RISK", end_date)
        _active_selloff_df(end_date).to_parquet(tmp_path / "MISMATCH1.parquet", engine="pyarrow")

        with patch("alert_referee_engine.HISTORICAL_DIR", tmp_path):
            result = are.backfill_historical_features()

        assert result["updated"] == 0
        assert result["skipped"] == 1

        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT ema_distance FROM trap_phase_history WHERE ticker='MISMATCH1'"
            ).fetchone()
        finally:
            conn.close()
        assert row["ema_distance"] is None

    def test_is_idempotent(self, tmp_path):
        end_date = "2026-01-25"
        _seed_bare_history_row("BACKFILL2", "ACTIVE_SELLOFF", end_date)
        _active_selloff_df(end_date).to_parquet(tmp_path / "BACKFILL2.parquet", engine="pyarrow")

        with patch("alert_referee_engine.HISTORICAL_DIR", tmp_path):
            first = are.backfill_historical_features()
            second = are.backfill_historical_features()

        assert first["updated"] == 1
        assert second["total_candidates"] == 0

    def test_already_resolved_row_counts_toward_readiness_after_backfill(self, tmp_path):
        end_date = "2026-01-25"
        _seed_bare_history_row("BACKFILL3", "ACTIVE_SELLOFF", end_date, direction_correct_14d=1)
        _active_selloff_df(end_date).to_parquet(tmp_path / "BACKFILL3.parquet", engine="pyarrow")

        with patch("alert_referee_engine.HISTORICAL_DIR", tmp_path):
            are.backfill_historical_features()

        assert are.training_sample_count(_ENGINE) == 1


class TestTrainRefereeModelBackfillWiring:
    def test_train_referee_model_backfills_first(self):
        with patch("alert_referee_engine.backfill_historical_features") as mock_backfill:
            are.train_referee_model(_ENGINE)
        mock_backfill.assert_called_once()


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
