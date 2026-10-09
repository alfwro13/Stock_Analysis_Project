"""
tests/test_ml_training_engine.py — ML training unit tests

Covers:
  • _load_training_history reads in batches without changing rows and closes its connection on failure
  • train_global_ml_model / train_quantile_models complete after releasing intermediates
  • train_global_ml_model stages: search budget by RAM, target labels, embargoed temporal split,
    walk-forward folds and the saved feature statistics
"""

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db_module


@pytest.fixture
def training_history(fake_inference_df):
    tickers = [f"MEMTEST{i:02d}" for i in range(12)]
    dates = pd.date_range("2025-01-01", periods=220, freq="B").strftime("%Y-%m-%d")
    frame = fake_inference_df(tickers * len(dates))
    frame["date"] = np.repeat(dates, len(tickers))
    from ml_features import FUNDAMENTAL_FEATURES

    rows = frame.drop(columns=FUNDAMENTAL_FEATURES + ["sector"])
    conn = None
    try:
        conn = _db_module.get_connection()
        rows.to_sql("quant_signals", conn, if_exists="append", index=False)
        yield frame
    finally:
        if conn:
            conn.execute("DELETE FROM quant_signals WHERE ticker LIKE 'MEMTEST%'")
            conn.commit()
            conn.close()


def test_training_load_uses_batches_without_changing_rows(training_history):
    import ml_training_engine as engine

    read_sql = pd.read_sql_query
    queries = []

    def read_batches(query, conn, **kwargs):
        assert kwargs["chunksize"] == 10_000
        queries.append(query)
        return read_sql(query, conn, chunksize=137)

    with patch.object(engine.pd, "read_sql_query", side_effect=read_batches):
        actual = engine._load_training_history()
    conn = None
    try:
        conn = _db_module.get_connection()
        expected = read_sql(queries[0], conn)
    finally:
        if conn:
            conn.close()
    pd.testing.assert_frame_equal(actual, expected)
    assert actual.index.equals(pd.RangeIndex(len(actual)))
    assert set(training_history["ticker"]) <= set(actual["ticker"])


def test_training_load_closes_connection_after_partial_read_failure():
    import sqlite3
    import ml_training_engine as engine

    connections = []
    get_connection = engine.get_connection

    def opened_connection():
        conn = get_connection()
        connections.append(conn)
        return conn

    def failing_batches(*args, **kwargs):
        yield pd.DataFrame({"ticker": ["TEST"]})
        raise RuntimeError("read failed")

    with patch.object(engine, "get_connection", side_effect=opened_connection), \
         patch.object(engine.pd, "read_sql_query", side_effect=failing_batches):
        with pytest.raises(RuntimeError, match="read failed"):
            engine._load_training_history()
    with pytest.raises(sqlite3.ProgrammingError, match="closed"):
        connections[0].execute("SELECT 1")


@pytest.mark.parametrize("kind", ["classifier", "quantile"])
def test_training_completes_after_releasing_intermediates(training_history, tmp_path, kind):
    import ml_training_engine as engine

    search_class = engine.RandomizedSearchCV
    regressor_class = engine.XGBRegressor

    def small_search(**kwargs):
        kwargs["param_distributions"] = {"n_estimators": [2], "max_depth": [2]}
        kwargs["n_iter"] = 1
        return search_class(**kwargs)

    def small_regressor(**kwargs):
        kwargs["n_estimators"] = 2
        kwargs["max_depth"] = 2
        return regressor_class(**kwargs)

    paths = ["MODEL_PATH", "FEATURE_STATS_PATH", "QUANTILE_Q10_PATH", "QUANTILE_Q90_PATH"]
    from contextlib import ExitStack

    with ExitStack() as stack:
        for name in paths:
            stack.enter_context(patch.object(engine, name, tmp_path / (name + ".joblib")))
        stack.enter_context(patch.object(engine, "RandomizedSearchCV", side_effect=small_search))
        stack.enter_context(patch.object(engine, "XGBRegressor", side_effect=small_regressor))
        stack.enter_context(patch.object(engine, "log_notification"))
        stack.enter_context(patch.object(engine.psutil, "virtual_memory", return_value=MagicMock(available=8 * 1024**3)))
        if kind == "classifier":
            engine.train_global_ml_model()
            model = engine.joblib.load(engine.MODEL_PATH)
            scores = model.predict_proba(pd.DataFrame(np.zeros((2, len(engine.FEATURE_COLS))), columns=engine.FEATURE_COLS))
            assert np.isfinite(scores).all()
            assert engine.FEATURE_STATS_PATH.exists()
        else:
            engine.train_quantile_models()
            for path in [engine.QUANTILE_Q10_PATH, engine.QUANTILE_Q90_PATH]:
                model = engine.joblib.load(path)
                scores = model.predict(pd.DataFrame(np.zeros((2, len(engine.FEATURE_COLS))), columns=engine.FEATURE_COLS))
                assert np.isfinite(scores).all()


@pytest.mark.parametrize("available_gb, expected", [(0.5, None), (1.0, 4), (8.0, 10)])
def test_search_iterations_follow_available_memory(available_gb, expected):
    import ml_training_engine as engine

    with patch.object(engine.psutil, "virtual_memory", return_value=MagicMock(available=available_gb * 1024**3)), \
         patch.object(engine, "log_notification") as notify:
        assert engine._search_iterations() == expected
    assert notify.called is (expected is None)


def test_classifier_target_labels_the_entry_to_exit_return_and_drops_unresolved_rows():
    import ml_training_engine as engine
    from constants import PREDICTION_HORIZON_DAYS

    n = 30
    jump = np.where(np.arange(n) >= 13, 120.0, 100.0)
    df = pd.DataFrame({
        "ticker": ["UP"] * n + ["FLAT"] * n,
        "date": list(range(n)) * 2,
        "close_price": np.concatenate([jump, np.full(n, 100.0)]),
    })
    result = engine._add_classifier_target(df)

    assert len(result) == 2 * (n - PREDICTION_HORIZON_DAYS)
    up = result[result["ticker"] == "UP"]["target"].tolist()
    assert up[:6] == [0, 0, 0, 1, 1, 1]
    assert result[result["ticker"] == "FLAT"]["target"].sum() == 0


def test_temporal_split_regions_are_ordered_and_embargoed():
    import ml_training_engine as engine
    from constants import PREDICTION_HORIZON_DAYS

    dates = [f"2025-{i // 28 + 1:02d}-{i % 28 + 1:02d}" for i in range(100)]
    rng = np.random.default_rng(0)
    df = pd.DataFrame({"date": np.repeat(dates, 3), "target": rng.integers(0, 2, 300)})
    for col in engine.FEATURE_COLS:
        df[col] = rng.normal(size=300)
    df = df.sample(frac=1.0, random_state=1)

    split = engine._temporal_split(df)

    assert len(split.X_train) == 3 * (60 - PREDICTION_HORIZON_DAYS)
    assert len(split.X_calib) == 3 * (20 - PREDICTION_HORIZON_DAYS)
    assert len(split.X_test) == 3 * 20
    assert len(split.train_dates) == len(split.X_train)
    assert split.train_dates.index.equals(pd.RangeIndex(len(split.X_train)))
    assert split.train_dates.max() == dates[60 - PREDICTION_HORIZON_DAYS - 1]
    assert dates[60 - PREDICTION_HORIZON_DAYS] not in set(split.train_dates)


def test_walk_forward_folds_keep_the_label_embargo_between_train_and_test():
    import ml_training_engine as engine
    from constants import PREDICTION_HORIZON_DAYS

    n_dates = 60
    train_dates = pd.Series(np.repeat(np.arange(n_dates), 2))
    folds = engine._walk_forward_splits(train_dates)

    assert len(folds) == 4
    for train_idx, test_idx in folds:
        gap = train_dates.iloc[test_idx].min() - train_dates.iloc[train_idx].max()
        assert gap > PREDICTION_HORIZON_DAYS


def test_feature_stats_record_universe_size_and_feature_moments(tmp_path):
    import joblib
    import ml_training_engine as engine

    rng = np.random.default_rng(2)
    df = pd.DataFrame({
        "ticker": ["A", "B", "C", "A", "B"],
        "date": ["d1", "d1", "d1", "d2", "d2"],
    })
    for col in engine.CONTINUOUS_FEATURES:
        df[col] = rng.normal(size=5)
    path = tmp_path / "stats.joblib"
    with patch.object(engine, "FEATURE_STATS_PATH", path):
        engine._save_feature_stats(df)

    stats = joblib.load(path)
    assert stats["_meta"]["train_universe_size"] == 2
    assert set(stats["features"]) == set(engine.CONTINUOUS_FEATURES)
    assert stats["features"]["rsi_14"]["mean"] == pytest.approx(float(df["rsi_14"].mean()))
