"""
tests/test_08_database_helpers.py  ── DATABASE HELPER FUNCTION TESTS

Exercises the business logic in database.py helper functions:
  - log_score_event: upsert with COALESCE on close_price
  - fill_smgb_actual: error metric computation
  - get_smgb_accuracy: summary statistics and window queries
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db


# ── helpers ───────────────────────────────────────────────────────────────────

def _conn():
    return _db.get_connection()


# ── log_score_event ───────────────────────────────────────────────────────────

@pytest.mark.db
def test_log_score_event_inserts_row():
    """log_score_event must insert a new row into score_history."""
    conn = _conn()
    try:
        _db.log_score_event("TST_DB", "2099-01-01", 75, "BUY", 150.0)
        row = conn.execute(
            "SELECT score, signal, close_price FROM score_history "
            "WHERE ticker='TST_DB' AND date='2099-01-01'"
        ).fetchone()
        assert row is not None
        assert row["score"] == 75
        assert row["signal"] == "BUY"
        assert abs(row["close_price"] - 150.0) < 0.001
    finally:
        conn.execute("DELETE FROM score_history WHERE ticker='TST_DB'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_log_score_event_upserts_existing_row():
    """log_score_event must update score/signal on conflict."""
    conn = _conn()
    try:
        _db.log_score_event("TST_DB", "2099-01-02", 60, "HOLD", 100.0)
        _db.log_score_event("TST_DB", "2099-01-02", 80, "BUY", 110.0)
        row = conn.execute(
            "SELECT score, signal, close_price FROM score_history "
            "WHERE ticker='TST_DB' AND date='2099-01-02'"
        ).fetchone()
        assert row["score"] == 80
        assert row["signal"] == "BUY"
        assert abs(row["close_price"] - 110.0) < 0.001
    finally:
        conn.execute("DELETE FROM score_history WHERE ticker='TST_DB'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_log_score_event_coalesce_preserves_existing_close_price():
    """When close_price is None on the second write, the existing value must be kept."""
    conn = _conn()
    try:
        _db.log_score_event("TST_DB", "2099-01-03", 55, "HOLD", 200.0)
        _db.log_score_event("TST_DB", "2099-01-03", 65, "BUY", None)
        row = conn.execute(
            "SELECT close_price FROM score_history "
            "WHERE ticker='TST_DB' AND date='2099-01-03'"
        ).fetchone()
        # COALESCE(excluded.close_price, score_history.close_price) — None must not overwrite
        assert row["close_price"] is not None
        assert abs(row["close_price"] - 200.0) < 0.001
    finally:
        conn.execute("DELETE FROM score_history WHERE ticker='TST_DB'")
        conn.commit()
        conn.close()


# ── fill_smgb_actual ──────────────────────────────────────────────────────────

def _seed_smgb(target_date: str, predicted: float, last_close: float):
    """Insert a bare prediction row so fill_smgb_actual has something to update."""
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO smgb_predictions "
            "(prediction_date, target_date, predicted_price, last_smgb_close) "
            "VALUES (?, ?, ?, ?)",
            ("2099-01-01", target_date, predicted, last_close),
        )
        conn.commit()
    finally:
        conn.close()


def _read_smgb(target_date: str) -> dict:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT * FROM smgb_predictions WHERE target_date=?", (target_date,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


@pytest.mark.db
def test_fill_smgb_actual_computes_error_metrics():
    """fill_smgb_actual must write actual_open and compute absolute_error, pct_error."""
    _seed_smgb("2099-02-01", predicted=5.0, last_close=4.9)
    _db.fill_smgb_actual("2099-02-01", 5.1)
    row = _read_smgb("2099-02-01")
    assert abs(row["actual_open"] - 5.1) < 0.001
    assert abs(row["absolute_error"] - abs(5.0 - 5.1)) < 0.001
    assert row["pct_error"] is not None
    # direction: predicted 5.0 vs last 4.9 → positive change; actual 5.1 vs 4.9 → positive
    assert row["direction_correct"] == 1

    conn = _conn()
    conn.execute("DELETE FROM smgb_predictions WHERE target_date='2099-02-01'")
    conn.commit()
    conn.close()


@pytest.mark.db
def test_fill_smgb_actual_wrong_direction():
    """fill_smgb_actual marks direction_correct=0 when prediction and actual move opposite."""
    _seed_smgb("2099-02-02", predicted=5.0, last_close=5.1)  # predicted DOWN
    _db.fill_smgb_actual("2099-02-02", 5.2)                  # actual UP
    row = _read_smgb("2099-02-02")
    assert row["direction_correct"] == 0

    conn = _conn()
    conn.execute("DELETE FROM smgb_predictions WHERE target_date='2099-02-02'")
    conn.commit()
    conn.close()


@pytest.mark.db
def test_fill_smgb_actual_missing_row_does_not_raise():
    """fill_smgb_actual must silently return (no exception) if the row doesn't exist."""
    _db.fill_smgb_actual("2099-99-99", 1.0)  # must not raise


# ── get_smgb_accuracy ─────────────────────────────────────────────────────────

@pytest.mark.db
def test_get_smgb_accuracy_empty_returns_structure():
    """get_smgb_accuracy must return a dict with 'next_open' and 'us_open_impact' sections."""
    result = _db.get_smgb_accuracy()
    for section in ("next_open", "us_open_impact"):
        assert section in result
        assert "rows" in result[section]
        assert "summary" in result[section]
        assert isinstance(result[section]["rows"], list)
        assert isinstance(result[section]["summary"], dict)


@pytest.mark.db
def test_get_smgb_accuracy_summary_fields_present():
    """summary dict in each section must have all expected keys regardless of data."""
    result = _db.get_smgb_accuracy()
    for section in ("next_open", "us_open_impact"):
        summary = result[section]["summary"]
        for key in ("total_predictions", "resolved_count", "direction_accuracy_pct",
                    "mae_gbp", "mape_pct", "last_10_direction_pct", "last_30_direction_pct"):
            assert key in summary, f"Missing key '{key}' in {section} summary"


@pytest.mark.db
def test_get_smgb_accuracy_counts_resolved_rows():
    """resolved_count in next_open section must reflect rows with actual_open populated."""
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR IGNORE INTO smgb_predictions "
            "(prediction_date, target_date, prediction_type, predicted_price, actual_open) "
            "VALUES ('2099-03-01', '2099-03-02', 'next_open', 5.0, 5.1)",
        )
        conn.commit()
        result = _db.get_smgb_accuracy()
        assert result["next_open"]["summary"]["resolved_count"] >= 1
    finally:
        conn.execute("DELETE FROM smgb_predictions WHERE target_date='2099-03-02'")
        conn.commit()
        conn.close()
