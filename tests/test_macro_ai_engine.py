"""
tests/test_macro_ai_engine.py  ── MACRO AI ENGINE

Tests focused on the parts of MacroAIEngine that are deterministic and
do not require a trained ML model:

  _extract_numeric()          — pure string→float conversion
  _remap_hmm_states()         — state index permutation
  _log_training_score()       — DB write
  _ensure_training_log_table()— DB schema
  training guards             — early-return paths when data is insufficient
  run_macro_inference()       — no-events and untrained-model paths
  consensus-miss training     — scale-free feature, time order, skip-and-discard, stacked scoring
  _load_persisted_models()    — joblib load-on-init round trip
"""

import sys
import sqlite3
from pathlib import Path
from unittest.mock import MagicMock, patch

import joblib
import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import macro_ai_engine
from macro_ai_engine import MacroAIEngine


# ──────────────────────────────────────────────────────────────────────────────
# Fixture: in-memory engine (no real DB I/O, no ML libraries called)
# ──────────────────────────────────────────────────────────────────────────────

@pytest.fixture
def engine(tmp_path):
    """MacroAIEngine backed by an in-memory SQLite — no network, no disk."""
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS market_regimes (
            date TEXT PRIMARY KEY, vix_close REAL, ai_hmm_state INTEGER
        );
        CREATE TABLE IF NOT EXISTS macro_indicators (
            date TEXT PRIMARY KEY,
            us_m2 REAL, us_jobless_claims REAL,
            us_high_yield_spread REAL, us_yield_curve REAL
        );
        CREATE TABLE IF NOT EXISTS macro_calendar (
            event_id INTEGER PRIMARY KEY,
            event_date TEXT,
            forecast_val TEXT,
            previous_val TEXT,
            actual_val TEXT,
            post_event_spy_gap REAL,
            is_event_passed INTEGER DEFAULT 0,
            ai_volatility_warning REAL,
            ai_consensus_miss_prob REAL
        );
        CREATE TABLE IF NOT EXISTS model_training_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            model_name      TEXT    NOT NULL,
            trained_at      TEXT    NOT NULL,
            n_samples       INTEGER,
            cv_score_mean   REAL,
            cv_score_std    REAL,
            score_metric    TEXT
        );
    """)
    conn.commit()

    # Isolate from any real trained models on disk so fixture state is deterministic.
    with patch("macro_ai_engine.get_connection", return_value=conn), \
         patch.object(macro_ai_engine, "MACRO_HMM_PATH", tmp_path / "no_hmm.joblib"), \
         patch.object(macro_ai_engine, "MACRO_RF_PATH", tmp_path / "no_rf.joblib"), \
         patch.object(macro_ai_engine, "MACRO_XGB_PATH", tmp_path / "no_xgb.joblib"):
        eng = MacroAIEngine()
    eng.conn = conn  # ensure fixture connection is used
    yield eng
    conn.close()


# ──────────────────────────────────────────────────────────────────────────────
# 1. _extract_numeric()
# ──────────────────────────────────────────────────────────────────────────────

class TestExtractNumeric:

    @pytest.mark.parametrize("val, expected", [
        ("5.0%",    5.0),
        ("-1.2",   -1.2),
        ("1,234.5", 1234.5),
        ("1.2e3",   1200.0),
        ("$3.50",   3.50),
        ("2.5K",    2.5),
        ("0",       0.0),
        (".75",     0.75),
    ])
    def test_valid_inputs(self, engine, val, expected):
        assert engine._extract_numeric(val) == pytest.approx(expected)

    @pytest.mark.parametrize("val", [None, "", "   ", "N/A", "TBD"])
    def test_invalid_inputs_return_nan(self, engine, val):
        import pandas as pd
        result = engine._extract_numeric(val)
        assert pd.isna(result), f"Expected NaN for {val!r}, got {result}"

    def test_nan_scalar_returns_nan(self, engine):
        import pandas as pd
        result = engine._extract_numeric(float('nan'))
        assert pd.isna(result)


# ──────────────────────────────────────────────────────────────────────────────
# 2. _remap_hmm_states()
# ──────────────────────────────────────────────────────────────────────────────

class TestRemapHmmStates:

    def test_identity_when_no_order_set(self, engine):
        """Falls back to identity mapping when hmm_state_order is None."""
        engine.hmm_state_order = None
        raw = np.array([0, 1, 2, 0, 2])
        result = engine._remap_hmm_states(raw)
        np.testing.assert_array_equal(result, raw)

    def test_remaps_correctly(self, engine):
        """
        If raw state 2 is expansion (lowest spread) and raw state 0 is recession,
        argsort gives order [2, 1, 0] → canonical 0 maps to raw 2, canonical 2 maps to raw 0.
        """
        # Simulate: argsort of means column gives [2, 1, 0]
        # i.e. raw state 2 has lowest spread (→ canonical 0 = expansion)
        engine.hmm_state_order = np.array([2, 1, 0])
        raw = np.array([0, 1, 2])
        result = engine._remap_hmm_states(raw)
        # inv[hmm_state_order] = [0,1,2]  →  inv[2]=0, inv[1]=1, inv[0]=2
        # result[0] = inv[0] = 2  (raw 0 = recession → canonical 2)
        # result[1] = inv[1] = 1  (raw 1 = neutral   → canonical 1)
        # result[2] = inv[2] = 0  (raw 2 = expansion → canonical 0)
        np.testing.assert_array_equal(result, [2, 1, 0])

    def test_identity_order_is_no_op(self, engine):
        """If hmm_state_order is identity [0,1,2], remapping is a no-op."""
        engine.hmm_state_order = np.array([0, 1, 2])
        raw = np.array([0, 1, 2, 1, 0])
        result = engine._remap_hmm_states(raw)
        np.testing.assert_array_equal(result, raw)


# ──────────────────────────────────────────────────────────────────────────────
# 3. _ensure_training_log_table() + _log_training_score()
# ──────────────────────────────────────────────────────────────────────────────

class TestTrainingLog:

    def test_table_created(self, engine):
        """model_training_log table must exist (created by init_db / fixture schema)."""
        row = engine.conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='model_training_log'"
        ).fetchone()
        assert row is not None

    def test_log_training_score_inserts_row(self, engine):
        """_log_training_score() writes one row per call."""
        engine._log_training_score("test_model", 50, 0.85, 0.03, "accuracy")
        row = engine.conn.execute(
            "SELECT * FROM model_training_log WHERE model_name='test_model'"
        ).fetchone()
        assert row is not None
        assert row["n_samples"] == 50
        assert row["cv_score_mean"] == pytest.approx(0.85)
        assert row["cv_score_std"] == pytest.approx(0.03)
        assert row["score_metric"] == "accuracy"

    def test_log_training_score_none_std(self, engine):
        """cv_score_std may be None (HMM uses log-likelihood, no std)."""
        engine._log_training_score("hmm_regime", 100, -1.23, None, "log_likelihood_per_sample")
        row = engine.conn.execute(
            "SELECT cv_score_std FROM model_training_log WHERE model_name='hmm_regime'"
        ).fetchone()
        assert row is not None
        assert row["cv_score_std"] is None

    def test_log_training_score_db_error_does_not_raise(self, engine):
        """DB write failure must be swallowed, not propagated."""
        engine.conn.close()  # provoke OperationalError on next write
        engine._log_training_score("x", 1, 0.5, 0.1, "acc")  # must not raise


# ──────────────────────────────────────────────────────────────────────────────
# 4. Training guard — insufficient data early returns
# ──────────────────────────────────────────────────────────────────────────────

class TestTrainingGuards:

    def test_hmm_skips_with_no_data(self, engine):
        """train_regime_clustering() returns without error when macro_indicators is empty."""
        engine.train_regime_clustering()  # empty DB — must not raise
        assert engine.hmm_model is None  # model stays untrained

    def test_rf_skips_with_no_data(self, engine):
        """train_consensus_miss_probability() returns without error when macro_calendar is empty."""
        engine.train_consensus_miss_probability()
        assert engine.rf_model is None

    def test_xgb_skips_with_no_data(self, engine):
        """train_volatility_magnitude() returns without error when no SPY gap rows exist."""
        engine.train_volatility_magnitude()
        assert engine.xgb_model is None


# ──────────────────────────────────────────────────────────────────────────────
# 5. run_macro_inference() guard paths
# ──────────────────────────────────────────────────────────────────────────────

class TestRunMacroInference:

    def test_no_events_returns_early(self, engine):
        """run_macro_inference() exits cleanly when no upcoming events exist."""
        engine.run_macro_inference("2099-01-01")  # far-future date, no events in DB

    def test_untrained_xgb_skips_inference(self, engine):
        """
        When xgb_model is None but events exist, inference is skipped gracefully.
        No rows should be updated.
        """
        engine.conn.execute(
            "INSERT INTO macro_calendar (event_id, event_date, forecast_val, previous_val, is_event_passed) "
            "VALUES (1, '2026-06-05', '4.5', '4.2', 0)"
        )
        engine.conn.commit()
        engine.xgb_model = None
        engine.run_macro_inference("2026-06-05")
        row = engine.conn.execute(
            "SELECT ai_volatility_warning FROM macro_calendar WHERE event_id=1"
        ).fetchone()
        assert row["ai_volatility_warning"] is None


# ──────────────────────────────────────────────────────────────────────────────
# 5b. Consensus Miss model: scale-free feature, skip-and-discard, stacked scoring
# ──────────────────────────────────────────────────────────────────────────────

class TestExpectedChange:

    def test_percent_releases_payrolls_and_rates_share_one_scale(self, engine):
        out = engine._expected_change([0.3, 150_000.0, 4.0], [0.2, 100_000.0, 4.0])
        np.testing.assert_allclose(out, [1 / 3, 1 / 3, 0.0])

    def test_zero_forecast_and_previous_is_no_change_not_nan(self, engine):
        np.testing.assert_array_equal(engine._expected_change([0.0, 0.0], [0.0, -0.1]), [0.0, 1.0])

    def test_a_negative_previous_stays_inside_minus_two_and_two(self, engine):
        out = engine._expected_change([0.1, -0.1], [-0.1, 0.1])
        np.testing.assert_allclose(out, [2.0, -2.0])


def _seed_rows(engine, rows):
    engine.conn.executemany(
        "INSERT INTO macro_calendar (event_id, event_date, forecast_val, previous_val, actual_val, is_event_passed) VALUES (?, ?, ?, ?, ?, 1)",
        [(i, date, f, p, a) for i, (date, f, p, a) in enumerate(rows)],
    )
    engine.conn.commit()


def _persisted(path):
    joblib.dump("stale", path)
    (path.parent / f"{path.name}.sklearn-version").write_text("0")
    return path


class TestConsensusMissTraining:

    def _rows(self, n):
        return [(f"2026-06-{i + 1:02d}", str(3.0 + (i % 4)), "3.0", str(3.0 + (i % 3))) for i in range(n)]

    def test_trains_on_expected_change_in_event_date_order_and_persists(self, engine, tmp_path):
        rows = self._rows(24)
        _seed_rows(engine, list(reversed(rows)))
        rf_path = tmp_path / "macro_rf.joblib"
        with patch.object(macro_ai_engine, "MACRO_RF_PATH", rf_path), \
                patch.object(macro_ai_engine, "cross_val_score", return_value=np.array([0.6, 0.5, 0.7])) as cv:
            engine.train_consensus_miss_probability()
        X = cv.call_args.args[1]
        assert X.shape == (24, 1)
        np.testing.assert_allclose(X[:, 0], engine._expected_change([float(r[1]) for r in rows], [float(r[2]) for r in rows]))
        assert rf_path.exists() and engine.rf_model is not None
        assert engine.rf_model.n_features_in_ == 1
        assert engine.conn.execute("SELECT n_samples FROM model_training_log WHERE model_name = 'rf_consensus_miss'").fetchone()[0] == 24

    def test_too_few_labeled_rows_skip_training_and_discard_the_persisted_model(self, engine, tmp_path):
        _seed_rows(engine, self._rows(3))
        rf_path = _persisted(tmp_path / "macro_rf.joblib")
        engine.rf_model = "stale"
        with patch.object(macro_ai_engine, "MACRO_RF_PATH", rf_path):
            engine.train_consensus_miss_probability()
        assert engine.rf_model is None
        assert list(tmp_path.iterdir()) == []

    def test_a_single_outcome_class_skips_training_and_discards_the_persisted_model(self, engine, tmp_path):
        _seed_rows(engine, [(f"2026-06-{i + 1:02d}", "3.0", "3.0", "3.0") for i in range(15)])
        rf_path = _persisted(tmp_path / "macro_rf.joblib")
        with patch.object(macro_ai_engine, "MACRO_RF_PATH", rf_path):
            engine.train_consensus_miss_probability()
        assert engine.rf_model is None
        assert not rf_path.exists()

    def test_a_failed_read_keeps_the_persisted_model(self, engine, tmp_path):
        rf_path = _persisted(tmp_path / "macro_rf.joblib")
        engine.conn.execute("DROP TABLE macro_calendar")
        with patch.object(macro_ai_engine, "MACRO_RF_PATH", rf_path):
            engine.train_consensus_miss_probability()
        assert rf_path.exists()

    def test_volatility_training_with_too_few_gap_rows_discards_the_persisted_model(self, engine, tmp_path):
        engine.conn.execute(
            "INSERT INTO macro_calendar (event_id, event_date, forecast_val, previous_val, post_event_spy_gap, is_event_passed) VALUES (1, '2026-06-01', '3.0', '3.1', 0.5, 1)"
        )
        engine.conn.commit()
        xgb_path = _persisted(tmp_path / "macro_xgb.joblib")
        engine.xgb_model = "stale"
        with patch.object(macro_ai_engine, "MACRO_XGB_PATH", xgb_path):
            engine.train_volatility_magnitude()
        assert engine.xgb_model is None
        assert not xgb_path.exists()


class TestVolatilityTraining:

    def _seed(self, engine, rows):
        engine.conn.executemany(
            "INSERT INTO macro_calendar (event_id, event_date, forecast_val, previous_val, post_event_spy_gap, is_event_passed) VALUES (?, ?, ?, ?, ?, 1)",
            [(i, f"2026-06-{i + 1:02d}", f, p, 0.2 + 0.1 * (i % 5)) for i, (f, p) in enumerate(rows)],
        )
        engine.conn.commit()

    def test_stacks_the_consensus_miss_probability_of_the_scale_free_feature_and_persists(self, engine, tmp_path, caplog):
        self._seed(engine, [(str(3.0 + (i % 3)), "3.0") for i in range(14)])
        engine.rf_model = MagicMock()
        engine.rf_model.predict_proba.side_effect = lambda X: np.column_stack([1 - X[:, 0] ** 2, X[:, 0] ** 2])
        xgb_path = tmp_path / "macro_xgb.joblib"
        with patch.object(macro_ai_engine, "MACRO_XGB_PATH", xgb_path), caplog.at_level("INFO", logger="macro_ai_engine"):
            engine.train_volatility_magnitude()
        assert xgb_path.exists() and engine.xgb_model is not None
        assert engine.rf_model.predict_proba.call_args.args[0].shape == (14, 1)
        assert engine.xgb_model.n_features_in_ == 4
        assert "VIX join" in caplog.text
        assert engine.conn.execute("SELECT n_samples FROM model_training_log WHERE model_name = 'xgb_volatility'").fetchone()[0] == 14

    def test_the_event_features_are_the_expected_change_not_the_raw_forecast_and_previous(self, engine, tmp_path):
        rows = [("0.3", "0.2"), ("150000", "100000"), ("4.0", "4.0")] * 5
        self._seed(engine, rows)
        engine.rf_model = MagicMock()
        engine.rf_model.predict_proba.return_value = np.tile([0.4, 0.6], (len(rows), 1))
        with patch.object(macro_ai_engine, "MACRO_XGB_PATH", tmp_path / "macro_xgb.joblib"), \
                patch.object(macro_ai_engine, "cross_val_score", return_value=np.array([0.1, 0.2, 0.3])) as cv:
            engine.train_volatility_magnitude()
        X = cv.call_args.args[1].astype(float)
        assert X.shape == (15, 4)
        np.testing.assert_allclose(X[:3, 0], [1 / 3, 1 / 3, 0.0])
        np.testing.assert_allclose(X[:, 1], macro_ai_engine.MACRO_VIX_DEFAULT)
        np.testing.assert_allclose(X[:, 3], 0.6)
        assert np.abs(X[:, 0]).max() <= 2.0


class TestStackedScoring:

    def _event(self, engine):
        engine.conn.execute(
            "INSERT INTO macro_calendar (event_id, event_date, forecast_val, previous_val, is_event_passed) VALUES (1, '2026-06-05', '0.3', '0.2', 0)"
        )
        engine.conn.commit()

    def test_an_untrained_consensus_miss_model_leaves_the_event_unscored(self, engine):
        self._event(engine)
        engine.xgb_model = MagicMock()
        engine.rf_model = None
        engine.run_macro_inference("2026-06-05")
        row = engine.conn.execute("SELECT ai_volatility_warning, ai_consensus_miss_prob FROM macro_calendar WHERE event_id = 1").fetchone()
        assert (row["ai_volatility_warning"], row["ai_consensus_miss_prob"]) == (None, None)
        engine.xgb_model.predict.assert_not_called()

    def test_both_models_score_the_event_from_the_scale_free_feature(self, engine):
        self._event(engine)
        engine.rf_model = MagicMock()
        engine.rf_model.predict_proba.return_value = np.array([[0.3, 0.7]])
        engine.xgb_model = MagicMock()
        engine.xgb_model.predict.return_value = np.array([np.log1p(1.5)])
        engine.run_macro_inference("2026-06-05")
        row = engine.conn.execute("SELECT ai_volatility_warning, ai_consensus_miss_prob FROM macro_calendar WHERE event_id = 1").fetchone()
        assert row["ai_consensus_miss_prob"] == pytest.approx(0.7)
        assert row["ai_volatility_warning"] == pytest.approx(1.5)
        np.testing.assert_allclose(engine.rf_model.predict_proba.call_args.args[0], [[1 / 3]])
        np.testing.assert_allclose(
            engine.xgb_model.predict.call_args.args[0],
            [[1 / 3, macro_ai_engine.MACRO_VIX_DEFAULT, 0, 0.7]],
        )

    def test_without_the_volatility_model_the_probability_is_scored_and_the_warning_is_null(self, engine):
        engine.conn.execute(
            "INSERT INTO macro_calendar (event_id, event_date, forecast_val, previous_val, ai_volatility_warning, is_event_passed) VALUES (1, '2026-06-05', '0.3', '0.2', 1.8, 0)"
        )
        engine.conn.commit()
        engine.rf_model = MagicMock()
        engine.rf_model.predict_proba.return_value = np.array([[0.3, 0.7]])
        engine.xgb_model = None
        engine.run_macro_inference("2026-06-05")
        row = engine.conn.execute("SELECT ai_volatility_warning, ai_consensus_miss_prob FROM macro_calendar WHERE event_id = 1").fetchone()
        assert row["ai_consensus_miss_prob"] == pytest.approx(0.7)
        assert row["ai_volatility_warning"] is None


class TestDiscardPersistedModels:

    def test_removes_the_consensus_miss_and_volatility_models_with_their_sidecars_and_keeps_the_hmm(self, tmp_path):
        for name in ("macro_rf.joblib", "macro_xgb.joblib", "macro_hmm.joblib"):
            _persisted(tmp_path / name)
        (tmp_path / "macro_xgb.joblib.xgboost-version").write_text("0")
        macro_ai_engine.discard_persisted_models(tmp_path)
        assert sorted(p.name for p in tmp_path.iterdir()) == ["macro_hmm.joblib", "macro_hmm.joblib.sklearn-version"]
        macro_ai_engine.discard_persisted_models(tmp_path)

    def test_given_files_are_the_only_ones_removed(self, tmp_path):
        for name in ("macro_rf.joblib", "macro_xgb.joblib"):
            _persisted(tmp_path / name)
        macro_ai_engine.discard_persisted_models(tmp_path, (macro_ai_engine.MACRO_XGB_FILE,))
        assert sorted(p.name for p in tmp_path.iterdir()) == ["macro_rf.joblib", "macro_rf.joblib.sklearn-version"]


# ──────────────────────────────────────────────────────────────────────────────
# 6. _load_persisted_models() — models/*.joblib load-on-init round trip
# ──────────────────────────────────────────────────────────────────────────────

class TestLoadPersistedModels:

    def test_loads_hmm_bundle_when_file_present(self, engine, tmp_path):
        hmm_path = tmp_path / "macro_hmm.joblib"
        joblib.dump({"model": "fake_hmm", "scaler": "fake_scaler", "state_order": np.array([1, 0, 2])}, hmm_path)
        with patch.object(macro_ai_engine, "MACRO_HMM_PATH", hmm_path):
            engine.hmm_model = None
            engine._load_persisted_models()
        assert engine.hmm_model == "fake_hmm"
        assert engine.hmm_scaler == "fake_scaler"
        np.testing.assert_array_equal(engine.hmm_state_order, [1, 0, 2])

    def test_loads_rf_and_xgb_when_files_present(self, engine, tmp_path):
        rf_path = tmp_path / "macro_rf.joblib"
        xgb_path = tmp_path / "macro_xgb.joblib"
        joblib.dump("fake_rf", rf_path)
        joblib.dump("fake_xgb", xgb_path)
        with patch.object(macro_ai_engine, "MACRO_RF_PATH", rf_path), \
             patch.object(macro_ai_engine, "MACRO_XGB_PATH", xgb_path):
            engine._load_persisted_models()
        assert engine.rf_model == "fake_rf"
        assert engine.xgb_model == "fake_xgb"

    def test_no_op_when_no_files_present(self, engine, tmp_path):
        with patch.object(macro_ai_engine, "MACRO_HMM_PATH", tmp_path / "missing_hmm.joblib"), \
             patch.object(macro_ai_engine, "MACRO_RF_PATH", tmp_path / "missing_rf.joblib"), \
             patch.object(macro_ai_engine, "MACRO_XGB_PATH", tmp_path / "missing_xgb.joblib"):
            engine._load_persisted_models()
        assert engine.hmm_model is None
        assert engine.rf_model is None
        assert engine.xgb_model is None

    def test_corrupt_file_logs_and_does_not_raise(self, engine, tmp_path):
        bad_path = tmp_path / "macro_xgb.joblib"
        bad_path.write_text("not a real joblib file")
        with patch.object(macro_ai_engine, "MACRO_XGB_PATH", bad_path):
            engine._load_persisted_models()  # must not raise
        assert engine.xgb_model is None
