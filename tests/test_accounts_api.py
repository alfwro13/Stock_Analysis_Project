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
def test_create_and_update_account_opened_date_roundtrip(client):
    account_id = _create_account(client, opened_date="2020-03-15")

    import database as _db
    assert _db.get_account(account_id)["opened_date"] == "2020-03-15"

    resp = client.put(f"/api/accounts/{account_id}", json={
        "name": "Renamed", "currency": "GBP", "initial_cash": 0, "opened_date": "2019-01-01",
    })
    assert resp.status_code == 200
    assert _db.get_account(account_id)["opened_date"] == "2019-01-01"
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


@pytest.mark.api
def test_account_value_snapshot_trigger_queues_job(client):
    with patch("api_routes_accounts.run_account_value_snapshot") as mock_run:
        resp = client.post("/api/accounts/value-snapshot/trigger")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "queued"


@pytest.mark.api
def test_create_account_triggers_backfill_in_background(client):
    with patch("api_routes_accounts.resnapshot_account") as mock_resnapshot:
        account_id = _create_account(client, name="BackfillTriggerAcc")
    mock_resnapshot.assert_called_once_with(account_id)

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_import_ghostfolio_returns_counts(client):
    with patch("api_routes_accounts.resnapshot_account"):
        account_id = _create_account(client, name="ImportApiAcc")
    with (
        patch("api_routes_accounts.import_ghostfolio_activities", return_value={"imported": 2, "skipped": 1}) as mock_import,
        patch("api_routes_accounts.resnapshot_account"),
    ):
        resp = client.post(f"/api/accounts/{account_id}/import-ghostfolio", json={"ghostfolio_account_id": "gf-acc-1"})
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert data["imported"] == 2
    assert data["skipped"] == 1
    mock_import.assert_called_once_with(account_id, "gf-acc-1")

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_import_ghostfolio_skips_profile_fetch_for_uuid_ticker(client):
    """Ghostfolio reports a raw asset UUID as the symbol for custom/manual assets — queueing a
    profile fetch for that string only produces a guaranteed Yahoo Finance failure and permanently
    blacklists the UUID. The import endpoint must not queue update_single_profile for it."""
    with patch("api_routes_accounts.resnapshot_account"):
        account_id = _create_account(client, name="ImportApiUuidAcc")
    import database as _db
    _db.add_transaction(
        account_id=account_id, txn_type="Buy", txn_date="2026-01-10",
        ticker="507f6948-db0b-4877-bec0-030a6996431d", quantity=1, unit_price=1.0,
    )
    with (
        patch("api_routes_accounts.import_ghostfolio_activities", return_value={"imported": 0, "skipped": 0}),
        patch("api_routes_accounts.resnapshot_account"),
        patch("api_routes_accounts.update_single_profile") as mock_profile,
    ):
        resp = client.post(f"/api/accounts/{account_id}/import-ghostfolio", json={"ghostfolio_account_id": "gf-acc-1"})
    assert resp.status_code == 200
    mock_profile.assert_not_called()

    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_import_ghostfolio_not_configured_returns_400(client):
    with patch("api_routes_accounts.resnapshot_account"):
        account_id = _create_account(client, name="ImportApiNotConfiguredAcc")
    with patch("api_routes_accounts.import_ghostfolio_activities", return_value={"imported": 0, "skipped": 0, "error": "Ghostfolio is not configured."}):
        resp = client.post(f"/api/accounts/{account_id}/import-ghostfolio", json={"ghostfolio_account_id": "gf-acc-1"})
    assert resp.status_code == 400
    assert _json(resp)["status"] == "error"

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_import_ghostfolio_unknown_account_returns_404(client):
    resp = client.post("/api/accounts/999999/import-ghostfolio", json={"ghostfolio_account_id": "gf-acc-1"})
    assert resp.status_code == 404


@pytest.mark.api
def test_import_ghostfolio_missing_account_id_returns_422(client):
    """Regression guard: importing without naming a single Ghostfolio account must be rejected,
    not silently fall back to pulling every Ghostfolio account's activities."""
    with patch("api_routes_accounts.resnapshot_account"):
        account_id = _create_account(client, name="ImportApiMissingFieldAcc")
    resp = client.post(f"/api/accounts/{account_id}/import-ghostfolio", json={})
    assert resp.status_code == 422

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_import_csv_returns_counts(client):
    with patch("api_routes_accounts.resnapshot_account"):
        account_id = _create_account(client, name="CsvImportApiAcc")
    csv_bytes = b"Title,Type,Timestamp\nTop up,TOP_UP,01/02/2021\n"
    with (
        patch("api_routes_accounts.import_csv_activities", return_value={
            "imported": 1, "skipped": 0, "ignored": 0, "skipped_rows": [],
        }) as mock_import,
        patch("api_routes_accounts.resnapshot_account"),
    ):
        resp = client.post(
            f"/api/accounts/{account_id}/import-csv",
            files={"file": ("activity.csv", csv_bytes, "text/csv")},
        )
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert data["imported"] == 1
    assert data["skipped_rows"] == []
    mock_import.assert_called_once_with(account_id, "Title,Type,Timestamp\nTop up,TOP_UP,01/02/2021\n")

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_import_csv_reports_skipped_rows_with_date_and_ticker(client):
    with patch("api_routes_accounts.resnapshot_account"):
        account_id = _create_account(client, name="CsvImportApiUnresolvedAcc")
    csv_bytes = b"Title,Type,Timestamp\nDelisted,ORDER,01/02/2021\n"
    skipped_rows = [{"date": "2021-02-01", "ticker": "ZZZNOPE", "reason": "ticker not found (possibly delisted or mistyped)"}]
    with (
        patch("api_routes_accounts.import_csv_activities", return_value={
            "imported": 0, "skipped": 1, "ignored": 0, "skipped_rows": skipped_rows,
        }),
        patch("api_routes_accounts.resnapshot_account"),
        patch("api_routes_accounts.notification_engine.notify") as mock_notify,
    ):
        resp = client.post(
            f"/api/accounts/{account_id}/import-csv",
            files={"file": ("activity.csv", csv_bytes, "text/csv")},
        )
    assert resp.status_code == 200
    data = _json(resp)
    assert data["skipped_rows"] == skipped_rows
    assert "Notifications panel" in data["message"]
    mock_notify.assert_called_once()
    notify_args = mock_notify.call_args[0]
    assert notify_args[0] == "accounts_csv_import"
    assert "ZZZNOPE" in notify_args[2]
    assert "2021-02-01" in notify_args[2]

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_import_csv_missing_column_returns_422(client):
    with patch("api_routes_accounts.resnapshot_account"):
        account_id = _create_account(client, name="CsvImportApiBadColAcc")
    csv_bytes = b"Title,Type,Timestamp\nTop up,TOP_UP,01/02/2021\n"
    with patch("api_routes_accounts.import_csv_activities", return_value={
        "error": "CSV is missing required column(s): Account Currency"
    }):
        resp = client.post(
            f"/api/accounts/{account_id}/import-csv",
            files={"file": ("activity.csv", csv_bytes, "text/csv")},
        )
    assert resp.status_code == 422
    assert _json(resp)["status"] == "error"

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_import_csv_unknown_account_returns_404(client):
    csv_bytes = b"Title,Type,Timestamp\n"
    resp = client.post(
        "/api/accounts/999999/import-csv",
        files={"file": ("activity.csv", csv_bytes, "text/csv")},
    )
    assert resp.status_code == 404


@pytest.mark.api
def test_list_ghostfolio_accounts_returns_active_discovered_accounts(client):
    fake_config = {
        "GHOSTFOLIO_ACCOUNTS": {
            "discovered": [
                {"id": "gf-1", "name": "ISA", "currency": "GBP"},
                {"id": "gf-2", "name": "Excluded", "currency": "GBP"},
            ],
            "active": ["gf-1"],
        }
    }
    with patch("api_routes_accounts.load_config", return_value=fake_config):
        resp = client.get("/api/accounts/ghostfolio-accounts")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert data["accounts"] == [{"id": "gf-1", "name": "ISA", "currency": "GBP"}]


@pytest.mark.api
def test_create_transfer_moves_cash_between_accounts(client):
    src = _create_account(client, name="TransferApiSrc", initial_cash=1000.0)
    dst = _create_account(client, name="TransferApiDst", initial_cash=0.0)

    resp = client.post(f"/api/accounts/{src}/transfer", json={
        "to_account_id": dst, "amount": 250.0, "txn_date": "2026-01-10", "fee": 0,
    })
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert "out_txn_id" in data and "in_txn_id" in data

    import database as _db
    assert _db.get_account(src) is not None
    import accounts_engine
    assert accounts_engine.cash_balance(src) == 750.0
    assert accounts_engine.cash_balance(dst) == 250.0
    _db.soft_delete_account(src)
    _db.soft_delete_account(dst)


@pytest.mark.api
def test_create_transfer_rejects_unknown_destination(client):
    src = _create_account(client, name="TransferApiBadDst")
    resp = client.post(f"/api/accounts/{src}/transfer", json={
        "to_account_id": 999999, "amount": 100.0, "txn_date": "2026-01-10",
    })
    assert resp.status_code == 404

    import database as _db
    _db.soft_delete_account(src)


@pytest.mark.api
def test_create_transfer_rejects_nonpositive_amount(client):
    src = _create_account(client, name="TransferApiZeroAmt")
    dst = _create_account(client, name="TransferApiZeroAmtDst")
    resp = client.post(f"/api/accounts/{src}/transfer", json={
        "to_account_id": dst, "amount": 0, "txn_date": "2026-01-10",
    })
    assert resp.status_code == 422

    import database as _db
    _db.soft_delete_account(src)
    _db.soft_delete_account(dst)


@pytest.mark.api
def test_create_transaction_rejects_transfer_type(client):
    aid = _create_account(client, name="DirectTransferRejectAcc")
    resp = client.post(f"/api/accounts/{aid}/transactions", json={
        "txn_type": "Transfer", "txn_date": "2026-01-10", "unit_price": 100,
    })
    assert resp.status_code == 422

    import database as _db
    _db.soft_delete_account(aid)


@pytest.mark.api
def test_delete_transaction_cascades_transfer_pair(client):
    src = _create_account(client, name="TransferDeleteCascadeSrc", initial_cash=500.0)
    dst = _create_account(client, name="TransferDeleteCascadeDst")
    xfer = _json(client.post(f"/api/accounts/{src}/transfer", json={
        "to_account_id": dst, "amount": 100.0, "txn_date": "2026-01-10",
    }))

    resp = client.delete(f"/api/accounts/{src}/transactions/{xfer['out_txn_id']}")
    assert resp.status_code == 200

    import database as _db
    assert _db.get_transaction(xfer["out_txn_id"]) is None
    assert _db.get_transaction(xfer["in_txn_id"]) is None
    _db.soft_delete_account(src)
    _db.soft_delete_account(dst)


@pytest.mark.api
def test_update_transaction_rejects_transfer_edit(client):
    src = _create_account(client, name="TransferEditRejectSrc")
    dst = _create_account(client, name="TransferEditRejectDst")
    xfer = _json(client.post(f"/api/accounts/{src}/transfer", json={
        "to_account_id": dst, "amount": 100.0, "txn_date": "2026-01-10",
    }))

    resp = client.put(f"/api/accounts/{src}/transactions/{xfer['out_txn_id']}", json={
        "txn_type": "Transfer", "txn_date": "2026-01-11", "unit_price": -200,
    })
    assert resp.status_code == 422

    import database as _db
    _db.soft_delete_account(src)
    _db.soft_delete_account(dst)


@pytest.mark.api
def test_create_transaction_triggers_resnapshot_in_background(client):
    account_id = _create_account(client, name="ResnapCreateAcc")
    with patch("api_routes_accounts.resnapshot_account") as mock_resnapshot:
        resp = client.post(f"/api/accounts/{account_id}/transactions", json={
            "txn_type": "Cash", "txn_date": "2026-01-01", "unit_price": 100,
        })
    assert resp.status_code == 200
    mock_resnapshot.assert_called_once_with(account_id)

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_update_transaction_triggers_resnapshot_in_background(client):
    account_id = _create_account(client, name="ResnapUpdateAcc")
    create_resp = client.post(f"/api/accounts/{account_id}/transactions", json={
        "txn_type": "Cash", "txn_date": "2026-01-01", "unit_price": 100,
    })
    txn_id = _json(create_resp)["id"]

    with patch("api_routes_accounts.resnapshot_account") as mock_resnapshot:
        resp = client.put(f"/api/accounts/{account_id}/transactions/{txn_id}", json={
            "txn_type": "Cash", "txn_date": "2026-01-01", "unit_price": 200,
        })
    assert resp.status_code == 200
    mock_resnapshot.assert_called_once_with(account_id)

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_delete_transaction_triggers_resnapshot_in_background(client):
    account_id = _create_account(client, name="ResnapDeleteAcc")
    create_resp = client.post(f"/api/accounts/{account_id}/transactions", json={
        "txn_type": "Cash", "txn_date": "2026-01-01", "unit_price": 100,
    })
    txn_id = _json(create_resp)["id"]

    with patch("api_routes_accounts.resnapshot_account") as mock_resnapshot:
        resp = client.delete(f"/api/accounts/{account_id}/transactions/{txn_id}")
    assert resp.status_code == 200
    mock_resnapshot.assert_called_once_with(account_id)

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_create_transfer_triggers_resnapshot_for_both_accounts(client):
    src = _create_account(client, name="ResnapTransferSrc")
    dst = _create_account(client, name="ResnapTransferDst")

    with patch("api_routes_accounts.resnapshot_account") as mock_resnapshot:
        resp = client.post(f"/api/accounts/{src}/transfer", json={
            "to_account_id": dst, "amount": 50.0, "txn_date": "2026-01-10",
        })
    assert resp.status_code == 200
    mock_resnapshot.assert_any_call(src)
    mock_resnapshot.assert_any_call(dst)
    assert mock_resnapshot.call_count == 2

    import database as _db
    _db.soft_delete_account(src)
    _db.soft_delete_account(dst)


@pytest.mark.api
def test_export_transactions_returns_csv(client):
    account_id = _create_account(client, name="ExportApiAcc")
    with patch("api_routes_accounts.update_single_profile"):
        client.post(f"/api/accounts/{account_id}/transactions", json={
            "txn_type": "Cash", "txn_date": "2026-01-01", "unit_price": 100,
        })

    resp = client.get(f"/api/accounts/{account_id}/export")
    assert resp.status_code == 200
    assert resp.headers["content-type"].startswith("text/csv")
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.text.startswith("ticker,type,qty,price,total_original_currency")
    assert ",Cash," in resp.text

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_export_transactions_unknown_account_returns_404(client):
    resp = client.get("/api/accounts/999999/export")
    assert resp.status_code == 404


@pytest.mark.api
def test_fx_rate_endpoint_returns_rate(client):
    with patch("api_routes_accounts.fx_rate_on_date", return_value=0.79) as mock_fx:
        resp = client.get("/api/fx-rate?currency=USD&date=2026-01-15")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert data["rate"] == 0.79
    mock_fx.assert_called_once_with("USD", "2026-01-15")
