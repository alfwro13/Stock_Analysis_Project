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
def test_create_account_defaults_to_trading_type(client):
    account_id = _create_account(client)
    resp = client.get("/api/accounts")
    acc = next(a for a in _json(resp)["accounts"] if a["id"] == account_id)
    assert acc["account_type"] == "Trading"

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_create_account_with_each_valid_account_type(client):
    import database as _db
    for account_type in ("Trading", "House", "Pension"):
        account_id = _create_account(client, name=f"{account_type} Acc", account_type=account_type)
        resp = client.get("/api/accounts")
        acc = next(a for a in _json(resp)["accounts"] if a["id"] == account_id)
        assert acc["account_type"] == account_type
        _db.soft_delete_account(account_id)


@pytest.mark.api
def test_create_account_rejects_watchlist_type(client):
    resp = client.post("/api/accounts", json={
        "name": "Sneaky Watchlist", "currency": "GBP", "account_type": "Watchlist",
    })
    assert resp.status_code == 400
    assert _json(resp)["status"] == "error"


@pytest.mark.api
def test_update_account_rejects_converting_into_watchlist(client):
    account_id = _create_account(client)
    resp = client.put(f"/api/accounts/{account_id}", json={
        "name": "Renamed", "currency": "GBP", "account_type": "Watchlist",
    })
    assert resp.status_code == 400
    assert _json(resp)["status"] == "error"

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_update_account_rejects_converting_watchlist_out(client):
    import database as _db
    wl = _db.get_watchlist_account()
    resp = client.put(f"/api/accounts/{wl['id']}", json={
        "name": wl["name"], "currency": wl["currency"], "account_type": "Trading",
    })
    assert resp.status_code == 400
    assert _json(resp)["status"] == "error"


@pytest.mark.api
def test_delete_watchlist_account_rejected(client):
    import database as _db
    wl = _db.get_watchlist_account()
    resp = client.delete(f"/api/accounts/{wl['id']}")
    assert resp.status_code == 400
    assert _json(resp)["status"] == "error"
    assert _db.get_account(wl["id"]) is not None


@pytest.mark.api
def test_create_account_rejects_invalid_account_type(client):
    resp = client.post("/api/accounts", json={
        "name": "Bad Type Acc", "currency": "GBP", "account_type": "Bogus",
    })
    assert resp.status_code == 400
    assert _json(resp)["status"] == "error"


@pytest.mark.api
def test_update_account_rejects_invalid_account_type(client):
    account_id = _create_account(client)
    resp = client.put(f"/api/accounts/{account_id}", json={
        "name": "Renamed", "currency": "GBP", "account_type": "Bogus",
    })
    assert resp.status_code == 400
    assert _json(resp)["status"] == "error"

    import database as _db
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
def test_create_and_update_transaction_isin_roundtrip(client):
    account_id = _create_account(client)
    with patch("api_routes_accounts.update_single_profile"):
        create_resp = client.post(f"/api/accounts/{account_id}/transactions", json={
            "txn_type": "Buy", "txn_date": "2026-01-15", "ticker": "FGP.L", "isin": "GB0003452173",
            "currency": "GBP", "quantity": 6, "unit_price": 0.73,
        })
    txn_id = _json(create_resp)["id"]

    import database as _db
    assert _db.get_transaction(txn_id)["isin"] == "GB0003452173"

    resp = client.put(f"/api/accounts/{account_id}/transactions/{txn_id}", json={
        "txn_type": "Buy", "txn_date": "2026-01-15", "ticker": "FGP.L", "isin": "GB00B15FWH70",
        "currency": "GBP", "quantity": 6, "unit_price": 0.73,
    })
    assert resp.status_code == 200
    assert _db.get_transaction(txn_id)["isin"] == "GB00B15FWH70"
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
    assert resp.text.startswith("Title,Type,Timestamp,Account Currency")
    assert ",TOP_UP," in resp.text

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


@pytest.mark.api
def test_watchlist_account_auto_created_and_singular(client):
    import database as _db
    wl = _db.get_watchlist_account()
    assert wl is not None
    assert wl["account_type"] == "Watchlist"
    accounts = _db.get_accounts()
    assert sum(1 for a in accounts if a["account_type"] == "Watchlist") == 1

    from db_schema import _ensure_watchlist_account
    _ensure_watchlist_account()
    accounts_after = _db.get_accounts()
    assert sum(1 for a in accounts_after if a["account_type"] == "Watchlist") == 1


@pytest.mark.api
def test_ticker_search_returns_mapped_fields(client):
    fake_quotes = [{"symbol": "AAPL", "longname": "Apple Inc.", "quoteType": "EQUITY"}]
    with patch("api_routes_accounts.yahoo_engine.search_ticker", return_value=[
        {"ticker": q["symbol"], "company_name": q["longname"], "quote_type": q["quoteType"]} for q in fake_quotes
    ]) as mock_search:
        resp = client.get("/api/ticker-search?q=Apple")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert data["results"] == [{"ticker": "AAPL", "company_name": "Apple Inc.", "quote_type": "EQUITY"}]
    mock_search.assert_called_once_with("Apple")


@pytest.mark.api
def test_ticker_search_requires_query(client):
    resp = client.get("/api/ticker-search?q=")
    assert resp.status_code == 422


@pytest.mark.api
def test_watchlist_items_add_list_and_bulk_delete(client):
    import database as _db
    wl_id = _db.get_watchlist_account()["id"]

    with patch("api_routes_accounts.resolve_watchlist_metadata", return_value={
        "company_name": "Apple Inc.", "currency": "USD", "quote_type": "EQUITY", "exchange": "NYSE",
    }):
        resp = client.post(f"/api/accounts/{wl_id}/watchlist-items", json={"ticker": "AAPL"})
    assert resp.status_code == 200
    item_id = _json(resp)["id"]

    resp = client.get(f"/api/accounts/{wl_id}/watchlist-items")
    assert resp.status_code == 200
    items = _json(resp)["items"]
    added = next(i for i in items if i["id"] == item_id)
    assert added["ticker"] == "AAPL"
    assert added["company_name"] == "Apple Inc."
    assert added["exchange"] == "NYSE"

    resp = client.post(f"/api/accounts/{wl_id}/watchlist-items/bulk-delete", json={"ids": [item_id]})
    assert resp.status_code == 200
    assert _json(resp)["deleted"] == 1

    resp = client.get(f"/api/accounts/{wl_id}/watchlist-items")
    assert not any(i["id"] == item_id for i in _json(resp)["items"])


@pytest.mark.api
def test_watchlist_item_readd_is_idempotent(client):
    import database as _db
    wl_id = _db.get_watchlist_account()["id"]

    with patch("api_routes_accounts.resolve_watchlist_metadata", return_value={
        "company_name": "Microsoft Corp.", "currency": "USD", "quote_type": "EQUITY", "exchange": "NYSE",
    }):
        first = _json(client.post(f"/api/accounts/{wl_id}/watchlist-items", json={"ticker": "MSFT"}))
        second = _json(client.post(f"/api/accounts/{wl_id}/watchlist-items", json={"ticker": "MSFT"}))
    assert first["id"] == second["id"]

    _db.delete_watchlist_items(wl_id, [first["id"]])


@pytest.mark.api
def test_watchlist_items_endpoints_reject_non_watchlist_account(client):
    account_id = _create_account(client)
    resp = client.get(f"/api/accounts/{account_id}/watchlist-items")
    assert resp.status_code == 400

    resp = client.post(f"/api/accounts/{account_id}/watchlist-items", json={"ticker": "AAPL"})
    assert resp.status_code == 400

    import database as _db
    _db.soft_delete_account(account_id)


def _mock_html_resp(text: str):
    from unittest.mock import MagicMock
    m = MagicMock()
    m.status_code = 200
    m.text = text
    m.raise_for_status = MagicMock()
    return m


@pytest.mark.api
def test_scraper_config_endpoints_reject_trading_account(client):
    account_id = _create_account(client)
    resp = client.put(f"/api/accounts/{account_id}/scraper-config", json={
        "scraper_url": "http://example.test/x.html", "scraper_selector": "#gf-price",
    })
    assert resp.status_code == 400

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_scraper_test_endpoint_does_not_persist(client):
    account_id = _create_account(client, account_type="House")
    with patch("requests.get", return_value=_mock_html_resp('<div id="gf-price">487000</div>')):
        resp = client.post(f"/api/accounts/{account_id}/scraper/test", json={
            "url": "http://example.test/house.html", "selector": "#gf-price",
        })
    assert resp.status_code == 200
    assert _json(resp)["price"] == 487000.0

    import database as _db
    assert _db.get_price_history(account_id) == []
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_scraper_save_and_run_now_round_trip(client):
    account_id = _create_account(client, account_type="House")
    resp = client.put(f"/api/accounts/{account_id}/scraper-config", json={
        "scraper_url": "http://example.test/house.html", "scraper_selector": "#gf-price",
        "scrape_time": "03:00", "scraper_enabled": True,
    })
    assert resp.status_code == 200

    with patch("requests.get", return_value=_mock_html_resp('<div id="gf-price">512345</div>')):
        resp = client.post(f"/api/accounts/{account_id}/scraper/run-now")
    assert resp.status_code == 200
    assert _json(resp)["price"] == 512345.0

    import database as _db
    history = _db.get_price_history(account_id)
    assert history and history[-1]["price"] == 512345.0

    import scheduler_engine
    live_ids = {j.id for j in scheduler_engine.scheduler.get_jobs()}
    assert f"account_scraper_{account_id}_job" in live_ids

    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_scraper_config_save_with_disabled_unregisters_job(client):
    account_id = _create_account(client, account_type="House")
    client.put(f"/api/accounts/{account_id}/scraper-config", json={
        "scraper_url": "http://example.test/house.html", "scraper_selector": "#gf-price",
        "scraper_enabled": True,
    })
    resp = client.put(f"/api/accounts/{account_id}/scraper-config", json={
        "scraper_url": "http://example.test/house.html", "scraper_selector": "#gf-price",
        "scraper_enabled": False,
    })
    assert resp.status_code == 200

    import scheduler_engine
    live_ids = {j.id for j in scheduler_engine.scheduler.get_jobs()}
    assert f"account_scraper_{account_id}_job" not in live_ids

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_price_history_import_csv_endpoint(client):
    account_id = _create_account(client, account_type="Pension")
    resp = client.post(f"/api/accounts/{account_id}/price-history/import-csv", json={
        "csv_text": "date;marketPrice\n2026-01-01;1.50\n2026-01-02;1.55\n",
    })
    assert resp.status_code == 200
    data = _json(resp)
    assert data["imported"] == 2

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_pension_contribution_and_fee_endpoints(client):
    account_id = _create_account(client, account_type="Pension")
    client.post(f"/api/accounts/{account_id}/price-history/import-csv", json={
        "csv_text": "date;marketPrice\n2026-01-01;1.00\n",
    })
    resp = client.post(f"/api/accounts/{account_id}/pension/contribution", json={
        "txn_date": "2026-01-01", "amount": 500.0,
    })
    assert resp.status_code == 200
    data = _json(resp)
    assert data["units"] == 500.0

    resp = client.post(f"/api/accounts/{account_id}/pension/fee", json={
        "txn_date": "2026-01-01", "units_after": 498.0,
    })
    assert resp.status_code == 200
    fee_data = _json(resp)
    assert fee_data["units_removed"] == 2.0

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_pension_fee_endpoint_accepts_units_removed_directly(client):
    account_id = _create_account(client, account_type="Pension")
    client.post(f"/api/accounts/{account_id}/price-history/import-csv", json={
        "csv_text": "date;marketPrice\n2026-01-01;1.00\n",
    })
    client.post(f"/api/accounts/{account_id}/pension/contribution", json={
        "txn_date": "2026-01-01", "amount": 500.0,
    })

    resp = client.post(f"/api/accounts/{account_id}/pension/fee", json={
        "txn_date": "2026-01-01", "units_removed": 2.0,
    })
    assert resp.status_code == 200
    assert _json(resp)["units_removed"] == 2.0

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_pension_fee_endpoint_rejects_neither_or_both_units_args(client):
    account_id = _create_account(client, account_type="Pension")
    client.post(f"/api/accounts/{account_id}/price-history/import-csv", json={
        "csv_text": "date;marketPrice\n2026-01-01;1.00\n",
    })
    client.post(f"/api/accounts/{account_id}/pension/contribution", json={
        "txn_date": "2026-01-01", "amount": 500.0,
    })

    resp = client.post(f"/api/accounts/{account_id}/pension/fee", json={"txn_date": "2026-01-01"})
    assert resp.status_code == 422

    resp = client.post(f"/api/accounts/{account_id}/pension/fee", json={
        "txn_date": "2026-01-01", "units_after": 498.0, "units_removed": 2.0,
    })
    assert resp.status_code == 422

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_pension_endpoints_reject_non_pension_account(client):
    account_id = _create_account(client, account_type="House")
    resp = client.post(f"/api/accounts/{account_id}/pension/contribution", json={
        "txn_date": "2026-01-01", "amount": 100.0, "unit_price": 1.0,
    })
    assert resp.status_code == 400

    resp = client.post(f"/api/accounts/{account_id}/pension/fee", json={
        "txn_date": "2026-01-01", "units_after": 1.0, "unit_price": 1.0,
    })
    assert resp.status_code == 400

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_price_at_date_endpoint(client):
    account_id = _create_account(client, account_type="Pension")
    client.post(f"/api/accounts/{account_id}/price-history/import-csv", json={
        "csv_text": "date;marketPrice\n2026-01-01;1.50\n2026-01-10;1.60\n",
    })
    resp = client.get(f"/api/accounts/{account_id}/price-history/at-date?date=2026-01-05")
    assert resp.status_code == 200
    assert _json(resp)["price"] == 1.5

    resp = client.get(f"/api/accounts/{account_id}/price-history/at-date?date=2025-01-01")
    assert _json(resp)["price"] is None

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_price_at_date_endpoint_rejects_trading_account(client):
    account_id = _create_account(client)
    resp = client.get(f"/api/accounts/{account_id}/price-history/at-date?date=2026-01-05")
    assert resp.status_code == 400

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_pension_units_as_of_endpoint(client):
    account_id = _create_account(client, account_type="Pension")
    client.post(f"/api/accounts/{account_id}/pension/contribution", json={
        "txn_date": "2026-01-01", "amount": 500.0, "unit_price": 1.0,
    })
    resp = client.get(f"/api/accounts/{account_id}/pension/units-as-of?date=2026-01-01")
    assert resp.status_code == 200
    assert _json(resp)["units"] == 500.0

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_pension_start_date_create_and_update_roundtrip(client):
    resp = client.post("/api/accounts", json={
        "name": "PensionStartDateApiAcc", "currency": "GBP", "account_type": "Pension",
        "pension_start_date": "2015-03-01",
    })
    account_id = _json(resp)["id"]

    resp = client.get("/api/accounts")
    acc = next(a for a in _json(resp)["accounts"] if a["id"] == account_id)
    assert acc["pension_start_date"] == "2015-03-01"

    client.put(f"/api/accounts/{account_id}", json={
        "name": "PensionStartDateApiAcc", "currency": "GBP", "account_type": "Pension",
        "pension_start_date": "2016-04-01",
    })
    resp = client.get("/api/accounts")
    acc = next(a for a in _json(resp)["accounts"] if a["id"] == account_id)
    assert acc["pension_start_date"] == "2016-04-01"

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_opening_balance_units_create_and_update_roundtrip(client):
    resp = client.post("/api/accounts", json={
        "name": "OpeningBalanceUnitsApiAcc", "currency": "GBP", "account_type": "Pension",
        "opening_balance_units": 3125.5,
    })
    account_id = _json(resp)["id"]

    resp = client.get("/api/accounts")
    acc = next(a for a in _json(resp)["accounts"] if a["id"] == account_id)
    assert acc["opening_balance_units"] == 3125.5

    client.put(f"/api/accounts/{account_id}", json={
        "name": "OpeningBalanceUnitsApiAcc", "currency": "GBP", "account_type": "Pension",
        "opening_balance_units": 4000.0,
    })
    resp = client.get("/api/accounts")
    acc = next(a for a in _json(resp)["accounts"] if a["id"] == account_id)
    assert acc["opening_balance_units"] == 4000.0

    import database as _db
    _db.soft_delete_account(account_id)
