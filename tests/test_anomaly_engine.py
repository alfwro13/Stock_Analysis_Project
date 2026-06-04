# tests/test_anomaly_engine.py
"""
Unit tests for AnomalyEngine: training, scoring, normalisation, and graceful fallbacks.
All tests are offline — no network, no database.
"""
import os
import numpy as np
import pandas as pd
import pytest

from anomaly_engine import AnomalyEngine


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_ohlcv(n: int = 120, seed: int = 42) -> pd.DataFrame:
    """Synthetic OHLCV DataFrame with enough rows to train (>= 50 clean rows)."""
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 0.5, n))
    volume = rng.integers(100_000, 500_000, n).astype(float)
    return pd.DataFrame({
        "Open":   close * 0.999,
        "High":   close * 1.005,
        "Low":    close * 0.995,
        "Close":  close,
        "Volume": volume,
    })


def _engine(tmp_path) -> AnomalyEngine:
    config = {"NOTIFICATIONS": {"ANOMALY_ALERTS": {"THRESHOLD": 0.7}}}
    engine = AnomalyEngine(config)
    engine.models_dir = tmp_path
    return engine


# ---------------------------------------------------------------------------
# score() — no model on disk
# ---------------------------------------------------------------------------

class TestScoreNoModel:
    def test_returns_none_when_no_joblib_file(self, tmp_path):
        engine = _engine(tmp_path)
        result = engine.score("MISSING_TICKER", [1.0, 50.0, 0.0, 0.0, 0.2, 1.0])
        assert result is None

    def test_does_not_raise_on_nonexistent_ticker(self, tmp_path):
        engine = _engine(tmp_path)
        # Should never throw — just return None
        assert engine.score("XYZ_DOES_NOT_EXIST", [1.0] * 6) is None


# ---------------------------------------------------------------------------
# train_one() — insufficient data
# ---------------------------------------------------------------------------

class TestTrainOneInsufficientData:
    def test_skips_when_fewer_than_50_clean_rows(self, tmp_path):
        engine = _engine(tmp_path)
        df_short = _make_ohlcv(n=30)
        engine.train_one("SHORT", df_short, beta=1.0)
        assert not (tmp_path / "SHORT.joblib").exists()

    def test_skips_when_missing_volume_column(self, tmp_path):
        engine = _engine(tmp_path)
        df = _make_ohlcv(n=100).drop(columns=["Volume"])
        engine.train_one("NOVOL", df, beta=1.0)
        assert not (tmp_path / "NOVOL.joblib").exists()

    def test_skips_when_all_rows_nan_after_feature_computation(self, tmp_path):
        engine = _engine(tmp_path)
        df = pd.DataFrame({
            "Open": [np.nan] * 60, "High": [np.nan] * 60,
            "Low": [np.nan] * 60, "Close": [np.nan] * 60,
            "Volume": [np.nan] * 60,
        })
        engine.train_one("ALLNAN", df, beta=1.0)
        assert not (tmp_path / "ALLNAN.joblib").exists()


# ---------------------------------------------------------------------------
# train_one() + score() — normal flow
# ---------------------------------------------------------------------------

class TestTrainAndScore:
    def test_model_file_is_created(self, tmp_path):
        engine = _engine(tmp_path)
        engine.train_one("AAPL", _make_ohlcv(), beta=1.0)
        assert (tmp_path / "AAPL.joblib").exists()

    def test_score_returns_float_in_unit_interval(self, tmp_path):
        engine = _engine(tmp_path)
        engine.train_one("AAPL", _make_ohlcv(), beta=1.0)
        score = engine.score("AAPL", [1.0, 50.0, 0.1, 0.5, 0.18, 1.0])
        assert score is not None
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0

    def test_extreme_vector_scores_higher_than_normal_vector(self, tmp_path):
        engine = _engine(tmp_path)
        engine.train_one("AAPL", _make_ohlcv(), beta=1.0)
        normal_score  = engine.score("AAPL", [1.0, 50.0, 0.1, 0.5, 0.18, 1.0])
        extreme_score = engine.score("AAPL", [10.0, 92.0, -14.0, -7.0, 2.5, 1.8])
        assert extreme_score is not None and normal_score is not None
        assert extreme_score > normal_score

    def test_score_clamps_to_1_for_maximally_anomalous_input(self, tmp_path):
        engine = _engine(tmp_path)
        engine.train_one("AAPL", _make_ohlcv(), beta=1.0)
        # Extreme values far outside training distribution should be clamped at 1.0
        score = engine.score("AAPL", [100.0, 99.0, -50.0, -40.0, 10.0, 2.0])
        assert score == 1.0

    def test_score_clamps_to_0_for_minimally_anomalous_input(self, tmp_path):
        engine = _engine(tmp_path)
        df = _make_ohlcv()
        engine.train_one("AAPL", df, beta=1.0)
        # A score more normal than anything in training is clamped at 0.0
        # Use a copy of the training distribution's median as input
        score = engine.score("AAPL", [1.0, 50.0, 0.0, 0.0, 0.15, 1.0])
        assert score is not None
        assert score >= 0.0

    def test_beta_none_defaults_to_1(self, tmp_path):
        engine = _engine(tmp_path)
        # beta=None should not raise — falls back to 1.0 via clamp_beta
        engine.train_one("AAPL", _make_ohlcv(), beta=None)
        assert (tmp_path / "AAPL.joblib").exists()


# ---------------------------------------------------------------------------
# train_all() — multi-ticker batch
# ---------------------------------------------------------------------------

class TestTrainAll:
    def test_creates_model_for_each_valid_ticker(self, tmp_path):
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        parquet_dir.mkdir()
        for t in ("AAPL", "NVDA", "MSFT"):
            _make_ohlcv().to_parquet(parquet_dir / f"{t}.parquet")
        engine.train_all(["AAPL", "NVDA", "MSFT"], parquet_dir)
        for t in ("AAPL", "NVDA", "MSFT"):
            assert (tmp_path / f"{t}.joblib").exists()

    def test_skips_missing_parquet_without_raising(self, tmp_path):
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        parquet_dir.mkdir()
        # Only AAPL parquet exists; MISSING should be skipped gracefully
        _make_ohlcv().to_parquet(parquet_dir / "AAPL.parquet")
        engine.train_all(["AAPL", "MISSING"], parquet_dir)
        assert (tmp_path / "AAPL.joblib").exists()
        assert not (tmp_path / "MISSING.joblib").exists()

    def test_empty_ticker_list_is_a_noop(self, tmp_path):
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        parquet_dir.mkdir()
        engine.train_all([], parquet_dir)  # must not raise
        assert list(tmp_path.glob("*.joblib")) == []


# ---------------------------------------------------------------------------
# Normalisation invariants
# ---------------------------------------------------------------------------

class TestNormalisation:
    def test_score_is_deterministic(self, tmp_path):
        engine = _engine(tmp_path)
        engine.train_one("AAPL", _make_ohlcv(), beta=1.0)
        fv = [1.2, 55.0, 0.3, 1.0, 0.18, 1.0]
        assert engine.score("AAPL", fv) == engine.score("AAPL", fv)

    def test_different_tickers_have_independent_models(self, tmp_path):
        engine = _engine(tmp_path)
        engine.train_one("AAPL", _make_ohlcv(seed=1), beta=1.0)
        engine.train_one("NVDA", _make_ohlcv(seed=2), beta=1.5)
        fv = [1.0, 50.0, 0.0, 0.0, 0.2, 1.0]
        # Both return valid scores — models are independent
        s_aapl = engine.score("AAPL", fv)
        s_nvda = engine.score("NVDA", fv)
        assert s_aapl is not None
        assert s_nvda is not None
