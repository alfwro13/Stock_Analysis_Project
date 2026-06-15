"""
tests/test_08_database_helpers.py  ── DATABASE HELPER FUNCTION TESTS

Exercises the business logic in database.py helper functions:
  - log_score_event: upsert with COALESCE on close_price
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

