"""Tests for the Markets ticker registry CRUD API endpoints."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _json(resp) -> dict:
    try:
        return resp.json()
    except Exception as exc:
        raise AssertionError(
            f"Response is not valid JSON.\nStatus: {resp.status_code}\nBody: {resp.text[:500]}"
        ) from exc


_SAMPLE_BODY = {
    "ticker": "^TSTIDX",
    "display_name": "Test Index",
    "region": "Europe",
    "asset_type": "Index",
    "exchange": "LSE",
    "currency": "GBP",
    "invert_color": False,
    "is_pulse_tile": False,
    "sort_order": 99,
}


def _cleanup(ticker: str):
    import database as _db
    conn = _db.get_connection()
    conn.execute("DELETE FROM market_ticker_registry WHERE ticker = ?", (ticker,))
    conn.commit()
    conn.close()
    import market_pulse
    market_pulse.reload_ticker_registry()


@pytest.mark.api
def test_create_ticker_registry_row(client):
    resp = client.post("/api/markets/registry", json=_SAMPLE_BODY)
    try:
        assert resp.status_code == 200
        assert _json(resp).get("status") == "success"
        import database as _db
        row = _db.get_ticker_registry_row("^TSTIDX")
        assert row is not None
        assert row["display_name"] == "Test Index"
    finally:
        _cleanup("^TSTIDX")


@pytest.mark.api
def test_create_rejects_missing_ticker(client):
    resp = client.post("/api/markets/registry", json={**_SAMPLE_BODY, "ticker": None})
    assert resp.status_code == 422


@pytest.mark.api
def test_create_rejects_duplicate_ticker(client):
    resp = client.post("/api/markets/registry", json={**_SAMPLE_BODY, "ticker": "^GSPC"})
    assert resp.status_code == 409


@pytest.mark.api
def test_create_rejects_unrecognized_exchange(client):
    """A non-empty exchange that isn't a known time_engine.EXCHANGE_HOURS key must be rejected
    loudly, not silently fall back to "always open" (found 2026-07-09 on a manually-added
    ^KS200 row saved with an exchange value that didn't match "KRX" exactly)."""
    resp = client.post("/api/markets/registry", json={**_SAMPLE_BODY, "exchange": "korea"})
    assert resp.status_code == 422
    assert "korea" in _json(resp)["message"]


@pytest.mark.api
def test_create_accepts_blank_exchange(client):
    resp = client.post("/api/markets/registry", json={**_SAMPLE_BODY, "exchange": None})
    try:
        assert resp.status_code == 200
    finally:
        _cleanup("^TSTIDX")


@pytest.mark.api
def test_create_accepts_recognized_exchange(client):
    resp = client.post("/api/markets/registry", json={**_SAMPLE_BODY, "exchange": "KRX"})
    try:
        assert resp.status_code == 200
        import database as _db
        row = _db.get_ticker_registry_row("^TSTIDX")
        assert row["exchange"] == "KRX"
    finally:
        _cleanup("^TSTIDX")


@pytest.mark.api
def test_update_rejects_unrecognized_exchange(client):
    client.post("/api/markets/registry", json=_SAMPLE_BODY)
    try:
        resp = client.put("/api/markets/registry/^TSTIDX", json={**_SAMPLE_BODY, "exchange": "krx"})
        assert resp.status_code == 422
        import database as _db
        row = _db.get_ticker_registry_row("^TSTIDX")
        assert row["exchange"] == "LSE"
    finally:
        _cleanup("^TSTIDX")


@pytest.mark.api
def test_update_ticker_registry_row(client):
    client.post("/api/markets/registry", json=_SAMPLE_BODY)
    try:
        updated = {**_SAMPLE_BODY, "display_name": "Updated Test Index"}
        resp = client.put("/api/markets/registry/^TSTIDX", json=updated)
        assert resp.status_code == 200
        import database as _db
        row = _db.get_ticker_registry_row("^TSTIDX")
        assert row["display_name"] == "Updated Test Index"
    finally:
        _cleanup("^TSTIDX")


@pytest.mark.api
def test_update_nonexistent_ticker_returns_404(client):
    resp = client.put("/api/markets/registry/^NOPE_TICK", json=_SAMPLE_BODY)
    assert resp.status_code == 404


@pytest.mark.api
def test_delete_ticker_registry_row_soft_deletes(client):
    client.post("/api/markets/registry", json=_SAMPLE_BODY)
    try:
        resp = client.delete("/api/markets/registry/^TSTIDX")
        assert resp.status_code == 200
        import database as _db
        row = _db.get_ticker_registry_row("^TSTIDX")
        assert row is not None
        assert row["enabled"] == 0
    finally:
        _cleanup("^TSTIDX")


@pytest.mark.api
def test_delete_nonexistent_ticker_returns_404(client):
    resp = client.delete("/api/markets/registry/^NOPE_TICK")
    assert resp.status_code == 404


@pytest.mark.api
def test_new_ticker_appears_on_markets_page_without_restart(client):
    """Registry writes must bust market_pulse's ticker cache immediately."""
    client.post("/api/markets/registry", json=_SAMPLE_BODY)
    try:
        resp = client.get("/api/markets?view=static")
        data = _json(resp)["data"]
        europe = next(r for r in data["regions"] if r["region"] == "Europe")
        tickers = {t["ticker"] for t in europe["tiles"]}
        assert "^TSTIDX" in tickers
    finally:
        _cleanup("^TSTIDX")
