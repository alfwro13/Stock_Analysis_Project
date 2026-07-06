"""Tests for ETF predictor DB tables and helper functions."""
import sys
import pytest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db


@pytest.mark.db
def test_etf_predictor_configs_table_exists():
    conn = _db.get_connection()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='etf_predictor_configs'"
        ).fetchall()
        assert rows, "etf_predictor_configs table was not created"
    finally:
        conn.close()


@pytest.mark.db
def test_etf_predictor_predictions_table_exists():
    conn = _db.get_connection()
    try:
        rows = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='etf_predictor_predictions'"
        ).fetchall()
        assert rows, "etf_predictor_predictions table was not created"
    finally:
        conn.close()


@pytest.mark.db
def test_etf_predictor_configs_has_required_columns():
    conn = _db.get_connection()
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(etf_predictor_configs)").fetchall()}
        required = {
            "id", "name", "etf_ticker", "constituents",
            "enabled", "auto_schedule", "pre_run_time", "post_run_time",
            "deleted_at", "created_at",
        }
        missing = required - cols
        assert not missing, f"etf_predictor_configs missing columns: {missing}"
    finally:
        conn.close()


@pytest.mark.db
def test_etf_predictor_predictions_has_required_columns():
    conn = _db.get_connection()
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(etf_predictor_predictions)").fetchall()}
        required = {
            "id", "config_id", "run_at", "prediction_date", "target_date",
            "prediction_type", "predicted_price", "actual_open",
            "predicted_change_pct", "actual_change_pct", "last_etf_close",
            "holdings_predicted_price", "regression_predicted_price",
            "bias_corrected_price", "bias_corrected_change_pct",
            "blended_price", "blended_change_pct",
            "signal_source", "data_source", "fx_rate", "r_squared",
            "absolute_error", "pct_error", "direction_correct",
            "constituent_snapshot", "created_at",
        }
        missing = required - cols
        assert not missing, f"etf_predictor_predictions missing columns: {missing}"
    finally:
        conn.close()


@pytest.mark.db
def test_create_and_get_config_roundtrip():
    constituents = [{"ticker": "AAPL", "weight": 0.6}, {"ticker": "MSFT", "weight": 0.4}]
    config_id = _db.create_etf_predictor_config(
        name="Test Predictor",
        etf_ticker="TEST.L",
        constituents=constituents,
        enabled=True,
        auto_schedule=False,
        pre_run_time="13:30",
        post_run_time="22:00",
    )
    assert config_id is not None

    cfg = _db.get_etf_predictor_config(config_id)
    assert cfg is not None
    assert cfg["etf_ticker"] == "TEST.L"
    assert cfg["name"] == "Test Predictor"
    assert len(cfg["constituents"]) == 2
    assert cfg["constituents"][0]["ticker"] == "AAPL"
    assert abs(cfg["constituents"][0]["weight"] - 0.6) < 0.001

    # cleanup
    _db.soft_delete_etf_predictor_config(config_id)


@pytest.mark.db
def test_list_configs_excludes_deleted():
    cid = _db.create_etf_predictor_config(
        name="To Delete", etf_ticker="DEL.L",
        constituents=[{"ticker": "X", "weight": 1.0}],
    )
    assert cid is not None
    _db.soft_delete_etf_predictor_config(cid)

    configs = _db.get_etf_predictor_configs()
    ids = [c["id"] for c in configs]
    assert cid not in ids


@pytest.mark.db
def test_update_config():
    cid = _db.create_etf_predictor_config(
        name="Before", etf_ticker="UPD.L",
        constituents=[{"ticker": "A", "weight": 1.0}],
    )
    assert cid is not None
    _db.update_etf_predictor_config(cid, name="After", enabled=False)
    cfg = _db.get_etf_predictor_config(cid)
    assert cfg["name"] == "After"
    assert cfg["enabled"] == 0
    _db.soft_delete_etf_predictor_config(cid)


@pytest.mark.db
def test_update_config_rejects_unknown_columns():
    """Column whitelist must block keys not in _ALLOWED_ETF_CONFIG_COLUMNS."""
    cid = _db.create_etf_predictor_config(
        name="WL Test", etf_ticker="WL.L",
        constituents=[{"ticker": "A", "weight": 1.0}],
    )
    assert cid is not None
    result = _db.update_etf_predictor_config(cid, deleted_at="2000-01-01", name="Injected")
    assert result is False
    cfg = _db.get_etf_predictor_config(cid)
    assert cfg["name"] == "WL Test"  # unchanged — update was rejected
    _db.soft_delete_etf_predictor_config(cid)


@pytest.mark.db
def test_log_etf_prediction_and_idempotency():
    cid = _db.create_etf_predictor_config(
        name="Log Test", etf_ticker="LOG.L",
        constituents=[{"ticker": "A", "weight": 1.0}],
    )
    assert cid is not None
    result = {
        "status": "success",
        "predicted_price": 123.45,
        "predicted_change_pct": 1.5,
        "last_etf_close": 121.5,
        "signal_source": "daily_close",
        "data_source": "holdings",
        "fx_rate": 1.26,
        "holdings_engine": {"predicted_price": 123.45},
        "regression_engine": {"predicted_price": 122.0, "r_squared": 0.7},
        "constituent_snapshot": '[{"ticker":"A","weight":1.0}]',
        "as_of_utc": "2099-06-01 12:00 UTC",
        "next_open_date": "2099-06-02",
    }
    _db.log_etf_prediction(cid, result)
    _db.log_etf_prediction(cid, result)  # second call must not raise or duplicate

    conn = _db.get_connection()
    try:
        count = conn.execute(
            "SELECT COUNT(*) FROM etf_predictor_predictions WHERE config_id=? AND target_date=?",
            (cid, "2099-06-02")
        ).fetchone()[0]
        assert count == 1, "ON CONFLICT DO NOTHING violated — duplicate row inserted"
    finally:
        conn.execute("DELETE FROM etf_predictor_predictions WHERE config_id=?", (cid,))
        conn.commit()
        conn.close()
    _db.soft_delete_etf_predictor_config(cid)


@pytest.mark.db
def test_fill_etf_actual_computes_metrics():
    cid = _db.create_etf_predictor_config(
        name="Fill Test", etf_ticker="FIL.L",
        constituents=[{"ticker": "A", "weight": 1.0}],
    )
    result = {
        "predicted_price": 100.0,
        "predicted_change_pct": 1.0,
        "last_etf_close": 99.0,
        "signal_source": "daily_close",
        "data_source": "holdings",
        "fx_rate": 1.0,
        "holdings_engine": {"predicted_price": 100.0},
        "regression_engine": None,
        "constituent_snapshot": "[]",
        "as_of_utc": "2099-07-01 10:00 UTC",
        "next_open_date": "2099-07-02",
    }
    _db.log_etf_prediction(cid, result)
    _db.fill_etf_actual(cid, "2099-07-02", 102.0, "next_open")

    conn = _db.get_connection()
    try:
        row = conn.execute(
            "SELECT actual_open, absolute_error, pct_error, direction_correct "
            "FROM etf_predictor_predictions WHERE config_id=? AND target_date=?",
            (cid, "2099-07-02")
        ).fetchone()
        assert row is not None
        assert abs(row["actual_open"] - 102.0) < 0.001
        assert abs(row["absolute_error"] - 2.0) < 0.001
        assert row["direction_correct"] == 1   # predicted +1% vs last close, actual +3%
    finally:
        conn.execute("DELETE FROM etf_predictor_predictions WHERE config_id=?", (cid,))
        conn.commit()
        conn.close()
    _db.soft_delete_etf_predictor_config(cid)


@pytest.mark.db
def test_get_etf_accuracy_returns_expected_shape():
    cid = _db.create_etf_predictor_config(
        name="Accuracy Test", etf_ticker="ACC.L",
        constituents=[{"ticker": "A", "weight": 1.0}],
    )
    accuracy = _db.get_etf_accuracy(cid)
    assert "next_open" in accuracy
    assert "us_open_impact" in accuracy
    assert "rows" in accuracy["next_open"]
    assert "summary" in accuracy["next_open"]
    assert "bias_corrected" in accuracy["next_open"]["summary"]
    assert "blended" in accuracy["next_open"]["summary"]
    assert accuracy["next_open"]["summary"]["bias_corrected"]["resolved_count"] == 0
    _db.soft_delete_etf_predictor_config(cid)


@pytest.mark.db
def test_log_etf_prediction_persists_bias_and_blend_columns():
    cid = _db.create_etf_predictor_config(
        name="Variant Log Test", etf_ticker="VAR.L",
        constituents=[{"ticker": "A", "weight": 1.0}],
    )
    result = {
        "predicted_price": 100.0,
        "predicted_change_pct": 1.0,
        "last_etf_close": 99.0,
        "signal_source": "daily_close",
        "data_source": "holdings",
        "fx_rate": 1.0,
        "holdings_engine": {"predicted_price": 100.0},
        "regression_engine": {"predicted_price": 99.5, "r_squared": 0.6},
        "bias_corrected_price": 100.5,
        "bias_corrected_change_pct": 1.5,
        "blended_price": 100.2,
        "blended_change_pct": 1.2,
        "constituent_snapshot": "[]",
        "as_of_utc": "2099-08-01 10:00 UTC",
        "next_open_date": "2099-08-02",
    }
    _db.log_etf_prediction(cid, result)

    conn = _db.get_connection()
    try:
        row = conn.execute(
            "SELECT bias_corrected_price, bias_corrected_change_pct, blended_price, blended_change_pct "
            "FROM etf_predictor_predictions WHERE config_id=? AND target_date=?",
            (cid, "2099-08-02")
        ).fetchone()
        assert row is not None
        assert abs(row["bias_corrected_price"] - 100.5) < 0.001
        assert abs(row["bias_corrected_change_pct"] - 1.5) < 0.001
        assert abs(row["blended_price"] - 100.2) < 0.001
        assert abs(row["blended_change_pct"] - 1.2) < 0.001
    finally:
        conn.execute("DELETE FROM etf_predictor_predictions WHERE config_id=?", (cid,))
        conn.commit()
        conn.close()
    _db.soft_delete_etf_predictor_config(cid)


@pytest.mark.db
def test_get_recent_prediction_errors_only_returns_resolved_rows_for_type():
    cid = _db.create_etf_predictor_config(
        name="Errors Test", etf_ticker="ERR.L",
        constituents=[{"ticker": "A", "weight": 1.0}],
    )
    base = {
        "predicted_change_pct": 1.0, "last_etf_close": 99.0,
        "signal_source": "daily_close", "data_source": "holdings", "fx_rate": 1.0,
        "holdings_engine": {"predicted_price": 100.0},
        "regression_engine": None, "constituent_snapshot": "[]",
    }
    resolved = {**base, "predicted_price": 100.0, "as_of_utc": "2099-09-01 10:00 UTC", "next_open_date": "2099-09-02"}
    unresolved = {**base, "predicted_price": 101.0, "as_of_utc": "2099-09-02 10:00 UTC", "next_open_date": "2099-09-03"}
    _db.log_etf_prediction(cid, resolved)
    _db.log_etf_prediction(cid, unresolved)
    _db.fill_etf_actual(cid, "2099-09-02", 102.0, "next_open")

    from db_etf import get_recent_prediction_errors
    rows = get_recent_prediction_errors(cid, "next_open", limit=10)
    try:
        assert len(rows) == 1
        assert abs(rows[0]["actual_open"] - 102.0) < 0.001

        other_type_rows = get_recent_prediction_errors(cid, "us_open_impact", limit=10)
        assert other_type_rows == []
    finally:
        conn = _db.get_connection()
        conn.execute("DELETE FROM etf_predictor_predictions WHERE config_id=?", (cid,))
        conn.commit()
        conn.close()
    _db.soft_delete_etf_predictor_config(cid)
