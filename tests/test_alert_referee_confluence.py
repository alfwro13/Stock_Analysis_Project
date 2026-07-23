"""
tests/test_alert_referee_confluence.py — Cross-Engine Alert Referee (Confluence engine) Tests

Covers the CONFLUENCE_ENGINE generalization of alert_referee_engine.py (Buy-Signal Confluence
Pipeline Part C):
  • backfill_historical_confluence_features() — as-of reconstruction into trap_phase_history /
    pattern_detection_history's new pillar/regime columns
  • training_sample_count()/readiness_status() — union query across both source tables, with
    pattern_detection_history's 14d AND 30d resolved outcomes each counted separately
  • train_referee_model() — Confluence's compact pillar-vote/regime-score feature set
  • evaluate_alert() dispatch — per-engine config/model isolation from the Trap Monitor pilot
  • scheduler_jobs.run_confluence_referee_training_job() — runner-level wiring
"""

import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
import alert_referee_engine as are

_ENGINE = are.CONFLUENCE_ENGINE
_CFG_KEY = "ALERT_REFEREE_TRAINING_CONFLUENCE"


def _clear_confluence_tables():
    conn = db.get_connection()
    try:
        conn.execute("DELETE FROM trap_phase_history")
        conn.execute("DELETE FROM pattern_detection_history")
        conn.execute("DELETE FROM alert_referee_models")
        conn.execute("DELETE FROM alert_referee_log")
        conn.commit()
    finally:
        conn.close()


def _seed_trap_row(ticker, direction_correct_14d, pillar_technical="up", pillar_statistical="up",
                    pillar_ml="up", regime_weighted_score=70.0, scan_date="2026-01-01",
                    confluence_features_ts="2026-01-01 12:00:00", phase="BEAR_TRAP_RISK"):
    conn = db.get_connection()
    try:
        conn.execute(
            """INSERT INTO trap_phase_history
               (ticker, phase, scan_date, scan_ts, close_price, direction_correct_14d,
                pillar_technical, pillar_statistical, pillar_ml, regime_weighted_score, confluence_features_ts)
               VALUES (?, ?, ?, ?, 100.0, ?, ?, ?, ?, ?, ?)""",
            (ticker, phase, scan_date, f"{scan_date} 12:00:00", direction_correct_14d,
             pillar_technical, pillar_statistical, pillar_ml, regime_weighted_score, confluence_features_ts),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_pattern_row(ticker, direction_correct_14d=None, direction_correct_30d=None,
                       pillar_technical="up", pillar_statistical="up", pillar_ml="up",
                       regime_weighted_score=70.0, scan_date="2026-01-01",
                       confluence_features_ts="2026-01-01 12:00:00", phase="CONFIRMED"):
    conn = db.get_connection()
    try:
        conn.execute(
            """INSERT INTO pattern_detection_history
               (ticker, pattern_family, pattern_type, phase, scan_date, scan_ts, close_price,
                direction_correct_14d, direction_correct_30d,
                pillar_technical, pillar_statistical, pillar_ml, regime_weighted_score, confluence_features_ts)
               VALUES (?, 'flag', 'bull_flag', ?, ?, ?, 100.0, ?, ?, ?, ?, ?, ?, ?)""",
            (ticker, phase, scan_date, f"{scan_date} 12:00:00", direction_correct_14d, direction_correct_30d,
             pillar_technical, pillar_statistical, pillar_ml, regime_weighted_score, confluence_features_ts),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_balanced_confluence_set(n_per_class=20):
    for i in range(n_per_class):
        _seed_trap_row(f"POS{i}", 1, pillar_technical="up", pillar_statistical="up", pillar_ml="up",
                        regime_weighted_score=80.0 + i * 0.1, scan_date=f"2026-01-{(i % 27) + 1:02d}")
        _seed_trap_row(f"NEG{i}", 0, pillar_technical="down", pillar_statistical="down", pillar_ml="down",
                        regime_weighted_score=20.0 + i * 0.1, scan_date=f"2026-02-{(i % 27) + 1:02d}")


@pytest.fixture(autouse=True)
def _isolate_confluence_tables():
    _clear_confluence_tables()
    yield
    _clear_confluence_tables()


class TestConfluenceReadinessStatus:
    def test_no_data_reports_zero_current(self):
        status = are.readiness_status(_ENGINE)
        assert status["current"] == 0
        assert status["can_train"] is False

    def test_unions_trap_and_pattern_resolved_rows(self):
        _seed_trap_row("T1", 1)
        _seed_pattern_row("P1", direction_correct_14d=1, direction_correct_30d=None)
        assert are.training_sample_count(_ENGINE) == 2

    def test_pattern_row_with_both_horizons_resolved_counts_twice(self):
        """direction_correct_14d and direction_correct_30d each become their own training row —
        same point-in-time features, a different-horizon label."""
        _seed_pattern_row("P2", direction_correct_14d=1, direction_correct_30d=0)
        assert are.training_sample_count(_ENGINE) == 2

    def test_rows_without_confluence_features_ts_are_not_counted(self):
        _seed_trap_row("T2", 1, confluence_features_ts=None)
        assert are.training_sample_count(_ENGINE) == 0

    def test_neutral_trap_phase_excluded(self):
        _seed_trap_row("T3", 1, phase="NEUTRAL")
        assert are.training_sample_count(_ENGINE) == 0

    def test_forming_pattern_phase_excluded(self):
        _seed_pattern_row("P3", direction_correct_14d=1, phase="FORMING")
        assert are.training_sample_count(_ENGINE) == 0

    def test_backfill_available_counts_unbackfilled_resolved_rows(self):
        _seed_trap_row("T4", 1, confluence_features_ts=None)
        _seed_pattern_row("P4", direction_correct_14d=1, confluence_features_ts=None)
        status = are.readiness_status(_ENGINE)
        assert status["current"] == 0
        assert status["backfill_available"] == 2

    def test_uses_confluence_specific_config_block(self):
        _seed_balanced_confluence_set(n_per_class=10)  # 20 trap rows total
        with patch("alert_referee_engine.load_config", return_value={
            "SCHEDULING": {_CFG_KEY: {"MIN_TRAINING_SAMPLES": 15}}
        }):
            status = are.readiness_status(_ENGINE)
        assert status["target"] == 15
        assert status["ready_for_active"] is True


class TestBackfillHistoricalConfluenceFeatures:
    def test_no_candidates_is_a_cheap_noop(self):
        result = are.backfill_historical_confluence_features()
        assert result == {"status": "done", "updated": 0, "total_candidates": 0}

    def test_reconstructs_features_for_unbackfilled_rows(self):
        _seed_trap_row("BF1", 1, confluence_features_ts=None, pillar_technical=None,
                        pillar_statistical=None, pillar_ml=None, regime_weighted_score=None)
        with patch("score_analysis.evaluate_pillar_confluence_as_of",
                   return_value={"bullish_pillars": ["technical", "ml"], "bearish_pillars": [], "confluence": True, "direction": "bullish"}), \
             patch("score_analysis.compute_regime_weighted_score_as_of", return_value={"score": 72.5, "regime": "Bull", "components": {}}):
            result = are.backfill_historical_confluence_features()

        assert result["updated"] == 1
        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT pillar_technical, pillar_statistical, pillar_ml, regime_weighted_score, confluence_features_ts "
                "FROM trap_phase_history WHERE ticker='BF1'"
            ).fetchone()
        finally:
            conn.close()
        assert row["pillar_technical"] == "up"
        assert row["pillar_statistical"] is None
        assert row["pillar_ml"] == "up"
        assert row["regime_weighted_score"] == 72.5
        assert row["confluence_features_ts"] is not None

    def test_is_idempotent(self):
        _seed_trap_row("BF2", 1, confluence_features_ts=None)
        with patch("score_analysis.evaluate_pillar_confluence_as_of",
                   return_value={"bullish_pillars": [], "bearish_pillars": [], "confluence": False, "direction": None}), \
             patch("score_analysis.compute_regime_weighted_score_as_of", return_value=None):
            first = are.backfill_historical_confluence_features()
            second = are.backfill_historical_confluence_features()
        assert first["updated"] == 1
        assert second["total_candidates"] == 0

    def test_covers_both_trap_and_pattern_tables(self):
        _seed_trap_row("BF3", 1, confluence_features_ts=None)
        _seed_pattern_row("BF4", direction_correct_14d=1, confluence_features_ts=None)
        with patch("score_analysis.evaluate_pillar_confluence_as_of",
                   return_value={"bullish_pillars": [], "bearish_pillars": [], "confluence": False, "direction": None}), \
             patch("score_analysis.compute_regime_weighted_score_as_of", return_value=None):
            result = are.backfill_historical_confluence_features()
        assert result["updated"] == 2


class TestTrainConfluenceRefereeModel:
    def test_insufficient_data_below_hard_minimum(self):
        _seed_balanced_confluence_set(n_per_class=2)
        result = are.train_referee_model(_ENGINE)
        assert result["status"] == "insufficient_data"
        assert result["sample_count"] == 4

    def test_trains_successfully_with_enough_balanced_data(self):
        _seed_balanced_confluence_set(n_per_class=20)
        with patch("alert_referee_engine.load_config", return_value={
            "SCHEDULING": {_CFG_KEY: {"MODE": "active", "MIN_TRAINING_SAMPLES": 200, "VETO_THRESHOLD": 0.3}}
        }):
            result = are.train_referee_model(_ENGINE)
        assert result["status"] == "trained"
        assert result["sample_count"] == 40
        assert result["effective_mode"] == "shadow"
        assert are._MODEL_PATHS[_ENGINE].exists()

        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM alert_referee_models WHERE engine=? ORDER BY id DESC LIMIT 1", (_ENGINE,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row["sample_count"] == 40

    def test_trained_model_path_is_independent_of_trap_monitor(self):
        assert are._MODEL_PATHS[are.CONFLUENCE_ENGINE] != are._MODEL_PATHS[are.TRAP_MONITOR_ENGINE]

    def test_train_referee_model_backfills_confluence_first(self):
        with patch("alert_referee_engine.backfill_historical_confluence_features") as mock_backfill:
            are.train_referee_model(_ENGINE)
        mock_backfill.assert_called_once()


class TestConfluenceEvaluateAlert:
    def setup_method(self):
        if are._MODEL_PATHS[_ENGINE].exists():
            are._MODEL_PATHS[_ENGINE].unlink()

    def _bullish_row(self):
        return {"pillar_technical": "up", "pillar_statistical": "up", "pillar_ml": "up", "regime_weighted_score": 85.0}

    def _bearish_row(self):
        return {"pillar_technical": "down", "pillar_statistical": "down", "pillar_ml": "down", "regime_weighted_score": 15.0}

    def test_disabled_never_evaluates(self):
        with patch("alert_referee_engine.load_config", return_value={"SCHEDULING": {_CFG_KEY: {"ENABLED": False}}}):
            conn = db.get_connection()
            try:
                verdict = are.evaluate_alert(_ENGINE, "AAPL", "bullish", self._bullish_row(), conn)
            finally:
                conn.close()
        assert verdict.mode == "off"
        assert verdict.model_available is False

    def test_active_mode_vetoes_low_confidence_signal(self):
        _seed_balanced_confluence_set(n_per_class=20)
        cfg = {"SCHEDULING": {_CFG_KEY: {"ENABLED": True, "MODE": "active", "MIN_TRAINING_SAMPLES": 10, "VETO_THRESHOLD": 0.5}}}
        with patch("alert_referee_engine.load_config", return_value=cfg):
            are.train_referee_model(_ENGINE)
            conn = db.get_connection()
            try:
                verdict = are.evaluate_alert(_ENGINE, "AAPL", "bearish", self._bearish_row(), conn)
            finally:
                conn.close()
        assert verdict.mode == "active"
        assert verdict.vetoed is True

    def test_trap_monitor_config_is_isolated_from_confluence(self):
        """Enabling only the Confluence config block must not make TrapMonitor evaluate, and
        vice versa — each engine's ENABLED/MODE/threshold is independently configured."""
        cfg = {"SCHEDULING": {_CFG_KEY: {"ENABLED": True, "MODE": "shadow"}}}
        with patch("alert_referee_engine.load_config", return_value=cfg):
            conn = db.get_connection()
            try:
                trap_verdict = are.evaluate_alert(are.TRAP_MONITOR_ENGINE, "AAPL", "BULL_TRAP_RISK", {}, conn)
            finally:
                conn.close()
        assert trap_verdict.mode == "off"


class TestRunConfluenceRefereeTrainingJob:
    def test_runner_completes_with_insufficient_data(self):
        import scheduler_jobs
        scheduler_jobs.run_confluence_referee_training_job()

    def test_runner_trains_model_when_enough_data_seeded(self):
        import scheduler_jobs
        _seed_balanced_confluence_set(n_per_class=20)
        scheduler_jobs.run_confluence_referee_training_job()
        conn = db.get_connection()
        try:
            row = conn.execute(
                "SELECT * FROM alert_referee_models WHERE engine=? ORDER BY id DESC LIMIT 1", (_ENGINE,)
            ).fetchone()
        finally:
            conn.close()
        assert row is not None
        assert row["sample_count"] == 40
