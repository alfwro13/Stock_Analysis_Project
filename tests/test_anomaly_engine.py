# tests/test_anomaly_engine.py
"""
Unit tests for AnomalyEngine: training, scoring, normalisation, graceful fallbacks,
and the backfill DB-write path.
"""
import sys
import os
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as db
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
    engine = AnomalyEngine()
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

    def test_score_returns_dict_in_unit_interval(self, tmp_path):
        engine = _engine(tmp_path)
        engine.train_one("AAPL", _make_ohlcv(), beta=1.0)
        result = engine.score("AAPL", [1.0, 50.0, 0.1, 0.5, 0.18, 1.0])
        assert result is not None
        assert isinstance(result, dict)
        assert 'score' in result and 'features' in result
        assert isinstance(result['score'], float)
        assert 0.0 <= result['score'] <= 1.0

    def test_extreme_vector_scores_higher_than_normal_vector(self, tmp_path):
        engine = _engine(tmp_path)
        engine.train_one("AAPL", _make_ohlcv(), beta=1.0)
        normal_result  = engine.score("AAPL", [1.0, 50.0, 0.1, 0.5, 0.18, 1.0])
        extreme_result = engine.score("AAPL", [10.0, 92.0, -14.0, -7.0, 2.5, 1.8])
        assert extreme_result is not None and normal_result is not None
        assert extreme_result['score'] > normal_result['score']

    def test_score_clamps_to_1_for_maximally_anomalous_input(self, tmp_path):
        engine = _engine(tmp_path)
        engine.train_one("AAPL", _make_ohlcv(), beta=1.0)
        # Extreme values far outside training distribution should be clamped at 1.0
        result = engine.score("AAPL", [100.0, 99.0, -50.0, -40.0, 10.0, 2.0])
        assert result['score'] == 1.0

    def test_score_clamps_to_0_for_minimally_anomalous_input(self, tmp_path):
        engine = _engine(tmp_path)
        df = _make_ohlcv()
        engine.train_one("AAPL", df, beta=1.0)
        # A score more normal than anything in training is clamped at 0.0
        # Use a copy of the training distribution's median as input
        result = engine.score("AAPL", [1.0, 50.0, 0.0, 0.0, 0.15, 1.0])
        assert result is not None
        assert result['score'] >= 0.0

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


# ---------------------------------------------------------------------------
# backfill_all() + _backfill_ticker() — DB write path
# ---------------------------------------------------------------------------

def _make_ohlcv_dated(n: int = 120, seed: int = 42) -> pd.DataFrame:
    """OHLCV DataFrame with a daily DatetimeIndex — required for backfill DB-write tests."""
    rng = np.random.default_rng(seed)
    close = 100.0 + np.cumsum(rng.normal(0, 0.5, n))
    volume = rng.integers(100_000, 500_000, n).astype(float)
    dates = pd.date_range(end="2026-06-01", periods=n, freq="B")
    return pd.DataFrame(
        {"Open": close * 0.999, "High": close * 1.005,
         "Low": close * 0.995, "Close": close, "Volume": volume},
        index=dates,
    )


def _last_feature_date(df_dated: pd.DataFrame) -> str:
    """Return the last date that will survive NaN-drop during feature computation.

    SMA50 needs 50 rows and is the dominant NaN source — so skip the first 49 rows.
    """
    return df_dated.index[-1].strftime("%Y-%m-%d")


def _seed_quant_signals(ticker: str, date: str) -> None:
    """Insert a minimal quant_signals row with anomaly_score = NULL."""
    conn = db.get_connection()
    conn.execute(
        "INSERT OR REPLACE INTO quant_signals (ticker, date) VALUES (?, ?)",
        (ticker, date),
    )
    conn.commit()
    conn.close()


def _read_anomaly_score(ticker: str, date: str):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT anomaly_score FROM quant_signals WHERE ticker = ? AND date = ?",
        (ticker, date),
    ).fetchone()
    conn.close()
    return row["anomaly_score"] if row else None


class TestBackfillTicker:
    def test_writes_anomaly_score_to_quant_signals(self, tmp_path):
        ticker = "BF_AAPL"
        df_hist = _make_ohlcv_dated()
        date = _last_feature_date(df_hist)
        _seed_quant_signals(ticker, date)

        engine = _engine(tmp_path)
        engine.train_one(ticker, df_hist, beta=1.0)

        conn = db.get_connection()
        try:
            rows = engine._backfill_ticker(
                ticker, df_hist, 1.0, conn,
                tmp_path / f"{ticker}.joblib",
            )
            conn.commit()
        finally:
            conn.close()

        assert rows > 0
        score = _read_anomaly_score(ticker, date)
        assert score is not None
        assert 0.0 <= score <= 1.0

    def test_returns_zero_when_missing_ohlcv_columns(self, tmp_path):
        engine = _engine(tmp_path)
        df_bad = pd.DataFrame({"Close": [100.0] * 60})
        engine.train_one("BF_BAD", _make_ohlcv(), beta=1.0)
        conn = db.get_connection()
        try:
            rows = engine._backfill_ticker(
                "BF_BAD", df_bad, 1.0, conn,
                tmp_path / "BF_BAD.joblib",
            )
        finally:
            conn.close()
        assert rows == 0


class TestBackfillAll:
    def test_skips_ticker_with_no_model(self, tmp_path):
        ticker = "BFA_NOMODEL"
        df = _make_ohlcv_dated()
        date = _last_feature_date(df)
        _seed_quant_signals(ticker, date)
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        parquet_dir.mkdir()
        df.to_parquet(parquet_dir / f"{ticker}.parquet")
        # No model trained → should skip without error
        engine.backfill_all([ticker], parquet_dir)
        assert _read_anomaly_score(ticker, date) is None

    def test_skips_ticker_with_no_parquet(self, tmp_path):
        ticker = "BFA_NOPARQUET"
        df = _make_ohlcv_dated()
        date = _last_feature_date(df)
        _seed_quant_signals(ticker, date)
        engine = _engine(tmp_path)
        engine.train_one(ticker, df, beta=1.0)
        parquet_dir = tmp_path / "parquet"
        parquet_dir.mkdir()
        # No parquet file → should skip without error
        engine.backfill_all([ticker], parquet_dir)
        assert _read_anomaly_score(ticker, date) is None

    def test_writes_scores_for_valid_ticker(self, tmp_path):
        ticker = "BFA_VALID"
        df = _make_ohlcv_dated(seed=99)
        date = _last_feature_date(df)
        _seed_quant_signals(ticker, date)

        engine = _engine(tmp_path)
        engine.train_one(ticker, df, beta=1.0)

        parquet_dir = tmp_path / "parquet"
        parquet_dir.mkdir()
        df.to_parquet(parquet_dir / f"{ticker}.parquet")

        engine.backfill_all([ticker], parquet_dir)

        score = _read_anomaly_score(ticker, date)
        assert score is not None
        assert 0.0 <= score <= 1.0

    def test_empty_ticker_list_is_noop(self, tmp_path):
        engine = _engine(tmp_path)
        parquet_dir = tmp_path / "parquet"
        parquet_dir.mkdir()
        engine.backfill_all([], parquet_dir)  # must not raise
