"""Tests for the built-in Accounts API endpoints (api_routes_accounts.py)."""
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))


def _json(resp) -> dict:
    try:
        return resp.json()
    except Exception as exc:
        raise AssertionError(
            f"Response is not valid JSON.\nStatus: {resp.status_code}\nBody: {resp.text[:500]}"
        ) from exc


def _create_account(client, **overrides) -> int:
    body = {"name": "API Test Account", "currency": "GBP", "initial_cash": 1000.0, "note": "test"}
    body.update(overrides)
    resp = client.post("/api/accounts", json=body)
    return _json(resp)["id"]


@pytest.mark.api
def test_create_account_returns_id(client):
    resp = client.post("/api/accounts", json={"name": "ISA", "currency": "GBP", "initial_cash": 500.0})
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert "id" in data

    import database as _db
    _db.soft_delete_account(data["id"])


@pytest.mark.api
def test_list_accounts_includes_created(client):
    account_id = _create_account(client, name="List Test Account")
    resp = client.get("/api/accounts")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert any(a["id"] == account_id for a in data["accounts"])

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_update_account(client):
    account_id = _create_account(client)
    resp = client.put(f"/api/accounts/{account_id}", json={
        "name": "Renamed Account", "currency": "USD", "initial_cash": 250.0, "note": None,
    })
    assert resp.status_code == 200
    assert _json(resp)["status"] == "success"

    import database as _db
    acc = _db.get_account(account_id)
    assert acc["name"] == "Renamed Account"
    assert acc["currency"] == "USD"
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_update_nonexistent_account_returns_404(client):
    resp = client.put("/api/accounts/999999", json={"name": "X", "currency": "GBP"})
    assert resp.status_code == 404


@pytest.mark.api
def test_delete_account_excludes_from_list(client):
    account_id = _create_account(client)
    resp = client.delete(f"/api/accounts/{account_id}")
    assert resp.status_code == 200
    assert _json(resp)["status"] == "success"

    list_resp = _json(client.get("/api/accounts"))
    assert not any(a["id"] == account_id for a in list_resp["accounts"])


@pytest.mark.api
def test_delete_nonexistent_account_returns_404(client):
    resp = client.delete("/api/accounts/999998")
    assert resp.status_code == 404


@pytest.mark.api
def test_create_transaction_for_unknown_account_returns_404(client):
    resp = client.post("/api/accounts/999997/transactions", json={
        "txn_type": "Cash", "txn_date": "2026-01-01", "update_cash": True,
    })
    assert resp.status_code == 404


@pytest.mark.api
def test_create_transaction_rejects_invalid_txn_type(client):
    account_id = _create_account(client)
    resp = client.post(f"/api/accounts/{account_id}/transactions", json={
        "txn_type": "NotAType", "txn_date": "2026-01-01",
    })
    assert resp.status_code == 422

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_create_and_list_buy_transaction(client):
    account_id = _create_account(client)
    with patch("api_routes_accounts.update_single_profile") as mock_profile:
        resp = client.post(f"/api/accounts/{account_id}/transactions", json={
            "txn_type": "Buy", "txn_date": "2026-01-15", "ticker": "AAPL",
            "currency": "USD", "quantity": 10, "unit_price": 150.0, "fee": 1.5,
            "exchange_rate": 0.8, "update_cash": True,
        })
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    txn_id = data["id"]

    list_resp = _json(client.get(f"/api/accounts/{account_id}/transactions"))
    assert any(t["id"] == txn_id and t["ticker"] == "AAPL" and t["exchange_rate"] == 0.8 for t in list_resp["transactions"])

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_create_transaction_preserves_gbp_pence_currency_case(client):
    """Currency must not be uppercased server-side — 'GBp' (pence) and 'GBP' (pounds) are
    distinct codes and uppercasing would silently break the pence-conversion logic."""
    account_id = _create_account(client)
    with patch("api_routes_accounts.update_single_profile"):
        resp = client.post(f"/api/accounts/{account_id}/transactions", json={
            "txn_type": "Buy", "txn_date": "2026-01-15", "ticker": "VOD.L",
            "currency": "GBp", "quantity": 10, "unit_price": 150.0, "exchange_rate": 1.0,
        })
    assert resp.status_code == 200
    txn_id = _json(resp)["id"]

    import database as _db
    txn = _db.get_transaction(txn_id)
    assert txn["currency"] == "GBp"
    assert bool(txn["price_in_pence"]) is True
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_create_transaction_with_unknown_ticker_triggers_profile_update(client):
    account_id = _create_account(client)
    with patch("api_routes_accounts.update_single_profile") as mock_profile:
        resp = client.post(f"/api/accounts/{account_id}/transactions", json={
            "txn_type": "Buy", "txn_date": "2026-01-15", "ticker": "ZZZNOTREAL",
            "currency": "USD", "quantity": 1, "unit_price": 1.0, "exchange_rate": 1.0,
        })
        assert resp.status_code == 200
        mock_profile.assert_called_once_with("ZZZNOTREAL")

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_create_transaction_blank_exchange_rate_is_auto_filled(client):
    account_id = _create_account(client)
    with (
        patch("api_routes_accounts.update_single_profile"),
        patch("api_routes_accounts.fx_rate_on_date", return_value=1.27) as mock_fx,
    ):
        resp = client.post(f"/api/accounts/{account_id}/transactions", json={
            "txn_type": "Buy", "txn_date": "2026-01-15", "ticker": "AAPL",
            "currency": "USD", "quantity": 1, "unit_price": 100.0,
        })
    assert resp.status_code == 200
    txn_id = _json(resp)["id"]
    mock_fx.assert_called_once_with("USD", "2026-01-15")

    import database as _db
    txn = _db.get_transaction(txn_id)
    assert txn["exchange_rate"] == 1.27
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_update_transaction(client):
    account_id = _create_account(client)
    with patch("api_routes_accounts.update_single_profile"):
        create_resp = client.post(f"/api/accounts/{account_id}/transactions", json={
            "txn_type": "Buy", "txn_date": "2026-01-15", "ticker": "MSFT",
            "currency": "USD", "quantity": 5, "unit_price": 300.0, "exchange_rate": 0.8,
        })
    txn_id = _json(create_resp)["id"]

    resp = client.put(f"/api/accounts/{account_id}/transactions/{txn_id}", json={
        "txn_type": "Buy", "txn_date": "2026-01-15", "ticker": "MSFT",
        "currency": "USD", "quantity": 8, "unit_price": 300.0, "exchange_rate": 0.8,
    })
    assert resp.status_code == 200
    assert _json(resp)["status"] == "success"

    import database as _db
    txn = _db.get_transaction(txn_id)
    assert txn["quantity"] == 8
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_update_transaction_for_wrong_account_returns_404(client):
    account_a = _create_account(client, name="Account A")
    account_b = _create_account(client, name="Account B")
    with patch("api_routes_accounts.update_single_profile"):
        create_resp = client.post(f"/api/accounts/{account_a}/transactions", json={
            "txn_type": "Cash", "txn_date": "2026-01-01", "currency": "GBP",
        })
    txn_id = _json(create_resp)["id"]

    resp = client.put(f"/api/accounts/{account_b}/transactions/{txn_id}", json={
        "txn_type": "Cash", "txn_date": "2026-01-01", "currency": "GBP",
    })
    assert resp.status_code == 404

    import database as _db
    _db.soft_delete_account(account_a)
    _db.soft_delete_account(account_b)


@pytest.mark.api
def test_delete_transaction(client):
    account_id = _create_account(client)
    with patch("api_routes_accounts.update_single_profile"):
        create_resp = client.post(f"/api/accounts/{account_id}/transactions", json={
            "txn_type": "Cash", "txn_date": "2026-01-01", "currency": "GBP",
        })
    txn_id = _json(create_resp)["id"]

    resp = client.delete(f"/api/accounts/{account_id}/transactions/{txn_id}")
    assert resp.status_code == 200
    assert _json(resp)["status"] == "success"

    list_resp = _json(client.get(f"/api/accounts/{account_id}/transactions"))
    assert not any(t["id"] == txn_id for t in list_resp["transactions"])

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_delete_transaction_nonexistent_returns_404(client):
    account_id = _create_account(client)
    resp = client.delete(f"/api/accounts/{account_id}/transactions/999996")
    assert resp.status_code == 404

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_ticker_lookup_returns_expected_shape(client):
    with patch("api_routes_accounts.yahoo_engine.get_ticker_info", return_value={
        "longName": "Apple Inc.", "currency": "USD", "quoteType": "EQUITY",
    }):
        resp = client.get("/api/ticker-lookup?q=aapl")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert data["found"] is True
    assert data["ticker"] == "AAPL"
    assert data["company_name"] == "Apple Inc."
    assert data["currency"] == "USD"
    assert data["quote_type"] == "EQUITY"


@pytest.mark.api
def test_ticker_lookup_not_found(client):
    with patch("api_routes_accounts.yahoo_engine.get_ticker_info", return_value=None):
        resp = client.get("/api/ticker-lookup?q=ZZZNOTREAL")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert data["found"] is False


@pytest.mark.api
def test_ticker_lookup_missing_q_returns_422(client):
    resp = client.get("/api/ticker-lookup")
    assert resp.status_code == 422
