"""
tests/test_01_database_schema.py  ── DATABASE SCHEMA & INTEGRITY

Verifies that:
  • Every expected table was created by init_db()
  • Each critical table has the columns the rest of the code depends on
  • Basic read/write round-trips work (INSERT → SELECT)
  • The database is accessible and not corrupted

These tests run against the session-level temp DB created in conftest.py.
No network access required.
"""

import sys
import sqlite3
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

# conftest.py redirects database.DB_PATH to the temp test file before this runs.
# We use database.get_connection() so tests share the same redirected path.
import database as _db


# ── helpers ───────────────────────────────────────────────────────────────────

def _conn():
    """Return a connection to the test DB (path is set by conftest.py)."""
    conn = sqlite3.connect(_db.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _tables() -> set:
    conn = _conn()
    try:
        rows = conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        return {r["name"] for r in rows}
    finally:
        conn.close()


def _columns(table: str) -> set:
    conn = _conn()
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {r["name"] for r in rows}
    finally:
        conn.close()


# ── Table existence ───────────────────────────────────────────────────────────

EXPECTED_TABLES = [
    "stock_signals",
    "system_notifications",
    "market_pulse_cache",
    "quant_signals",
    "quant_scan_states",
    "earnings_volatility",
    "market_universe",
    "asset_profiles",
    "market_regimes",
    "macro_regimes",
]


@pytest.mark.db
@pytest.mark.parametrize("table", EXPECTED_TABLES)
def test_table_exists(table):
    """Every table created by init_db() must exist in the database."""
    assert table in _tables(), f"Table '{table}' is missing from the database"


# ── Column presence on critical tables ───────────────────────────────────────

@pytest.mark.db
def test_stock_signals_has_required_columns():
    """stock_signals must have ticker, composite_score, overall_signal, ml_confidence."""
    cols = _columns("stock_signals")
    required = {"ticker", "composite_score", "overall_signal", "ml_confidence", "rsi_14", "current_price"}
    missing = required - cols
    assert not missing, f"stock_signals missing columns: {missing}"


@pytest.mark.db
def test_quant_signals_has_required_columns():
    """quant_signals must have the full technical + ML + risk column set."""
    cols = _columns("quant_signals")
    required = {
        "ticker", "date", "rsi_14", "macd", "sma_50", "sma_200",
        "ml_confidence_score", "var_95", "cvar_95", "composite_score",
    }
    missing = required - cols
    assert not missing, f"quant_signals missing columns: {missing}"


@pytest.mark.db
def test_system_notifications_has_required_columns():
    """Notification table must have id, message_type, message_text, is_read, status."""
    cols = _columns("system_notifications")
    required = {"id", "message_type", "message_text", "is_read", "status", "timestamp"}
    missing = required - cols
    assert not missing, f"system_notifications missing columns: {missing}"


@pytest.mark.db
def test_market_universe_has_required_columns():
    """market_universe must have ticker, sector, exchange, is_freetrade, is_index."""
    cols = _columns("market_universe")
    required = {"ticker", "company_name", "sector", "exchange", "is_freetrade", "is_index"}
    missing = required - cols
    assert not missing, f"market_universe missing columns: {missing}"


@pytest.mark.db
def test_asset_profiles_has_required_columns():
    """asset_profiles must have ticker, sector, currency, quote_type."""
    cols = _columns("asset_profiles")
    required = {"ticker", "sector", "currency", "quote_type", "country", "exchange"}
    missing = required - cols
    assert not missing, f"asset_profiles missing columns: {missing}"


@pytest.mark.db
def test_market_regimes_has_required_columns():
    """market_regimes must record VIX, volatility and regime label for US and UK."""
    cols = _columns("market_regimes")
    required = {"date", "vix_close", "spy_volatility", "us_regime_label", "uk_regime_label"}
    missing = required - cols
    assert not missing, f"market_regimes missing columns: {missing}"


@pytest.mark.db
def test_macro_regimes_has_required_columns():
    """macro_regimes must record yield curve, DXY, and threat levels."""
    cols = _columns("macro_regimes")
    required = {"date", "tyx_close", "tnx_close", "dxy_close", "us_threat_level", "uk_threat_level"}
    missing = required - cols
    assert not missing, f"macro_regimes missing columns: {missing}"


# ── Read / Write round-trips ──────────────────────────────────────────────────

@pytest.mark.db
def test_can_insert_and_read_notification():
    """Basic INSERT + SELECT on system_notifications must round-trip correctly."""
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)",
            ("test", "regression-test notification"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT message_text FROM system_notifications WHERE message_type = 'test' LIMIT 1"
        ).fetchone()
        assert row is not None, "Inserted notification not found"
        assert row["message_text"] == "regression-test notification"
    finally:
        conn.execute("DELETE FROM system_notifications WHERE message_type = 'test'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_can_insert_and_read_market_universe():
    """Basic INSERT + SELECT on market_universe must round-trip correctly."""
    conn = _conn()
    try:
        conn.execute(
            """INSERT OR REPLACE INTO market_universe
               (ticker, company_name, sector, exchange, last_updated)
               VALUES (?, ?, ?, ?, ?)""",
            ("TEST.L", "Test Corp", "Technology", "LSE", "2024-01-01"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT company_name FROM market_universe WHERE ticker = 'TEST.L'"
        ).fetchone()
        assert row is not None, "Inserted universe row not found"
        assert row["company_name"] == "Test Corp"
    finally:
        conn.execute("DELETE FROM market_universe WHERE ticker = 'TEST.L'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_quant_scan_states_composite_pk():
    """quant_scan_states uses a composite primary key (scan_date, scan_type)."""
    conn = _conn()
    try:
        conn.execute(
            "INSERT INTO quant_scan_states (scan_date, scan_type, status) VALUES (?, ?, ?)",
            ("2024-01-01", "daily", "complete"),
        )
        conn.commit()
        # Duplicate insert must REPLACE, not error
        conn.execute(
            "INSERT OR REPLACE INTO quant_scan_states (scan_date, scan_type, status) VALUES (?, ?, ?)",
            ("2024-01-01", "daily", "updated"),
        )
        conn.commit()
        row = conn.execute(
            "SELECT status FROM quant_scan_states WHERE scan_date='2024-01-01' AND scan_type='daily'"
        ).fetchone()
        assert row["status"] == "updated", "Composite PK upsert did not update the existing row"
    finally:
        conn.execute("DELETE FROM quant_scan_states WHERE scan_date='2024-01-01'")
        conn.commit()
        conn.close()


@pytest.mark.db
def test_database_file_is_accessible():
    """The database file must be readable and have a positive size."""
    db_path = _db.DB_PATH
    assert db_path.exists(), f"Database file does not exist at {db_path}"
    assert db_path.stat().st_size > 0, "Database file is empty"
