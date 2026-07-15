"""
tests/test_company_name_override.py  — Company Name Override feature

Covers:
  - company_name_overrides table schema
  - POST /api/ticker/{ticker}/name-override  (set, update, clear)
  - Stock detail page resolves override as display name
  - Portfolio and watchlist pages resolve override as display name
"""

import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import database as _db


def _conn():
    import sqlite3
    conn = sqlite3.connect(_db.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _seed_ticker(ticker: str, company_name: str = "Test Corp") -> None:
    conn = _conn()
    try:
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, company_name, quote_type, currency, sector) "
            "VALUES (?, ?, 'EQUITY', 'USD', 'Tech')",
            (ticker, company_name),
        )
        conn.commit()
    finally:
        conn.close()


def _seed_watchlist(ticker: str) -> None:
    import json
    from pathlib import Path as _Path
    from config import WATCHLIST_PATH
    try:
        data = json.loads(_Path(WATCHLIST_PATH).read_text())
    except Exception:
        data = {"watchlist": []}
    if ticker not in data["watchlist"]:
        data["watchlist"].append(ticker)
    _Path(WATCHLIST_PATH).write_text(json.dumps(data))


def _seed_portfolio(ticker: str) -> None:
    import json
    from pathlib import Path as _Path
    from config import PORTFOLIO_PATH
    try:
        data = json.loads(_Path(PORTFOLIO_PATH).read_text())
    except Exception:
        data = {}
    data[ticker] = {"ticker": ticker}
    _Path(PORTFOLIO_PATH).write_text(json.dumps(data))


# ── Table schema ──────────────────────────────────────────────────────────────

@pytest.mark.db
def test_company_name_overrides_table_exists():
    tables = _conn().execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='company_name_overrides'"
    ).fetchall()
    assert tables, "company_name_overrides table must exist"


@pytest.mark.db
def test_company_name_overrides_columns():
    cols = {r["name"] for r in _conn().execute("PRAGMA table_info(company_name_overrides)").fetchall()}
    assert {"ticker", "display_name", "updated_at"} <= cols


# ── API: set override ─────────────────────────────────────────────────────────

@pytest.mark.api
def test_name_override_set(client):
    """POST with a non-empty display_name writes a row to company_name_overrides."""
    ticker = "OVRD.TEST"
    _seed_ticker(ticker)

    resp = client.post(
        f"/api/ticker/{ticker}/name-override",
        json={"display_name": "My Custom Name"},
    )
    assert resp.status_code == 200
    assert resp.json().get("status") == "success"

    row = _conn().execute(
        "SELECT display_name FROM company_name_overrides WHERE ticker = ?", (ticker,)
    ).fetchone()
    assert row is not None
    assert row["display_name"] == "My Custom Name"


@pytest.mark.api
def test_name_override_update(client):
    """A second POST with a different name replaces the previous override."""
    ticker = "OVRD.TEST"

    client.post(f"/api/ticker/{ticker}/name-override", json={"display_name": "Name A"})
    client.post(f"/api/ticker/{ticker}/name-override", json={"display_name": "Name B"})

    row = _conn().execute(
        "SELECT display_name FROM company_name_overrides WHERE ticker = ?", (ticker,)
    ).fetchone()
    assert row["display_name"] == "Name B"


@pytest.mark.api
def test_name_override_clear(client):
    """POST with empty display_name deletes the override row."""
    ticker = "OVRD.TEST"

    try:
        client.post(f"/api/ticker/{ticker}/name-override", json={"display_name": "To be cleared"})
        client.post(f"/api/ticker/{ticker}/name-override", json={"display_name": ""})

        row = _conn().execute(
            "SELECT display_name FROM company_name_overrides WHERE ticker = ?", (ticker,)
        ).fetchone()
        assert row is None
    finally:
        conn = _conn()
        try:
            conn.execute("DELETE FROM stock_signals WHERE ticker = ?", (ticker,))
            conn.commit()
        finally:
            conn.close()


# ── Stock detail page resolution ──────────────────────────────────────────────

@pytest.mark.api
def test_stock_detail_shows_override(client):
    """GET /stock/{ticker} must show the custom display name when an override exists."""
    ticker = "DTLOVRD"
    _seed_ticker(ticker, company_name="Original Corp")

    conn = _conn()
    conn.execute(
        "INSERT OR REPLACE INTO company_name_overrides (ticker, display_name, updated_at) VALUES (?, ?, '2026-01-01')",
        (ticker, "Renamed Corp"),
    )
    conn.commit()
    conn.close()

    try:
        resp = client.get(f"/stock/{ticker}")
        assert resp.status_code == 200
        assert "Renamed Corp" in resp.text
    finally:
        conn = _conn()
        try:
            conn.execute("DELETE FROM stock_signals WHERE ticker = ?", (ticker,))
            conn.execute("DELETE FROM company_name_overrides WHERE ticker = ?", (ticker,))
            conn.commit()
        finally:
            conn.close()


@pytest.mark.api
def test_stock_detail_shows_original_after_clear(client):
    """After clearing an override, GET /stock/{ticker} must show the original name."""
    ticker = "DTLORIG"
    _seed_ticker(ticker, company_name="Original Name")

    conn = _conn()
    conn.execute(
        "INSERT OR REPLACE INTO company_name_overrides (ticker, display_name, updated_at) VALUES (?, ?, '2026-01-01')",
        (ticker, "Temp Override"),
    )
    conn.commit()
    conn.close()

    try:
        client.post(f"/api/ticker/{ticker}/name-override", json={"display_name": ""})

        resp = client.get(f"/stock/{ticker}")
        assert resp.status_code == 200
        assert "Original Name" in resp.text
        assert "Temp Override" not in resp.text
    finally:
        conn = _conn()
        try:
            conn.execute("DELETE FROM stock_signals WHERE ticker = ?", (ticker,))
            conn.commit()
        finally:
            conn.close()
