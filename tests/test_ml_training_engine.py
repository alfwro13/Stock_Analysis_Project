"""
tests/test_ml_training_engine.py — ML training unit tests

Covers:
  • _load_training_history reads in batches without changing rows and closes its connection on failure
  • train_global_ml_model / train_quantile_models complete after releasing intermediates
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
