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
def test_value_history_unknown_account_returns_404(client):
    resp = client.get("/api/accounts/999998/value-history")
    assert resp.status_code == 404


@pytest.mark.api
def test_value_history_filters_by_period(client):
    from datetime import datetime, timedelta, timezone
    import database as _db

    account_id = _create_account(client)
    today = datetime.now(timezone.utc).date()
    _db.upsert_value_snapshot(account_id, (today - timedelta(days=400)).isoformat(), 100, 100, 0, 0)
    _db.upsert_value_snapshot(account_id, today.isoformat(), 130, 130, 0, 0)

    resp = client.get(f"/api/accounts/{account_id}/value-history?period=1y")
    data = _json(resp)
    assert data["status"] == "success"
    assert data["period"] == "1y"
    assert len(data["data"]) == 1
    assert data["data"][0]["total_value"] == 130

    resp_max = _json(client.get(f"/api/accounts/{account_id}/value-history?period=max"))
    assert len(resp_max["data"]) == 2

    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_live_performance_unknown_account_returns_404(client):
    resp = client.get("/api/accounts/999996/live-performance")
    assert resp.status_code == 404


@pytest.mark.api
def test_live_performance_rejects_non_trading_account_type(client):
    account_id = _create_account(client, account_type="House")
    resp = client.get(f"/api/accounts/{account_id}/live-performance")
    assert resp.status_code == 400


@pytest.mark.api
def test_live_performance_returns_cached_row_without_recomputing(client):
    import database as _db

    account_id = _create_account(client)
    _db.upsert_performance_cache(
        account_id, total_value=999.0, equity_value=0.0, cash_balance=999.0,
        unrealized_pnl=0.0, return_1d=1.0, return_1w=2.0, return_1m=3.0,
        return_3m=4.0, return_6m=5.0, return_1y=6.0, mwrr=7.0, last_updated=123.0,
    )

    resp = client.get(f"/api/accounts/{account_id}/live-performance")
    data = _json(resp)

    assert resp.status_code == 200
    assert data["status"] == "success"
    assert data["total_value"] == 999.0        # proves it served the seeded cache row, not a recompute
    assert data["mwrr"] == 7.0

    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_live_performance_computes_on_first_view_when_no_cache_row_yet(client):
    import database as _db

    account_id = _create_account(client)
    assert _db.get_performance_cache(account_id) is None

    resp = client.get(f"/api/accounts/{account_id}/live-performance")
    data = _json(resp)

    assert resp.status_code == 200
    assert data["status"] == "success"
    assert data["total_value"] == 1000.0        # initial_cash from _create_account's default body
    assert _db.get_performance_cache(account_id) is not None

    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_reconcile_cash_unknown_account_returns_404(client):
    resp = client.post("/api/accounts/999996/reconcile-cash", json={"actual_balance": 100.0})
    assert resp.status_code == 404


@pytest.mark.api
def test_reconcile_cash_books_adjustment(client):
    import database as _db

    account_id = _create_account(client)
    resp = client.post(f"/api/accounts/{account_id}/reconcile-cash", json={"actual_balance": 1005.0})
    data = _json(resp)
    assert data["status"] == "success"
    assert data["delta"] == 5.0
    assert data["txn_id"] is not None

    list_resp = _json(client.get(f"/api/accounts/{account_id}/transactions"))
    adj = next(t for t in list_resp["transactions"] if t["id"] == data["txn_id"])
    assert adj["txn_type"] == "Cash"
    assert bool(adj["is_adjustment"]) is True

    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_reconcile_cash_noop_when_already_balanced(client):
    import database as _db

    account_id = _create_account(client)
    resp = client.post(f"/api/accounts/{account_id}/reconcile-cash", json={"actual_balance": 1000.0})
    data = _json(resp)
    assert data["status"] == "success"
    assert data["delta"] == 0.0
    assert "txn_id" not in data or data.get("txn_id") is None

    list_resp = _json(client.get(f"/api/accounts/{account_id}/transactions"))
    assert list_resp["transactions"] == []

    _db.soft_delete_account(account_id)


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
def test_create_transaction_with_explicit_fee_currency_and_rate(client):
    account_id = _create_account(client)
    with patch("api_routes_accounts.update_single_profile"):
        resp = client.post(f"/api/accounts/{account_id}/transactions", json={
            "txn_type": "Buy", "txn_date": "2026-01-15", "ticker": "AAPL",
            "currency": "USD", "quantity": 10, "unit_price": 150.0, "fee": 1.54,
            "exchange_rate": 0.8, "fee_currency": "GBP", "fee_exchange_rate": 1.0,
        })
    assert resp.status_code == 200
    txn_id = _json(resp)["id"]

    import database as _db
    txn = _db.get_transaction(txn_id)
    assert txn["fee_currency"] == "GBP"
    assert txn["fee_exchange_rate"] == 1.0
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_create_transaction_fee_currency_defaults_to_trade_currency(client):
    """No fee_currency supplied — must default to the trade currency/rate, matching pre-existing
    behaviour where the fee was always assumed to share the trade's currency."""
    account_id = _create_account(client)
    with patch("api_routes_accounts.update_single_profile"):
        resp = client.post(f"/api/accounts/{account_id}/transactions", json={
            "txn_type": "Buy", "txn_date": "2026-01-15", "ticker": "AAPL",
            "currency": "USD", "quantity": 10, "unit_price": 150.0, "fee": 1.54,
            "exchange_rate": 0.8,
        })
    assert resp.status_code == 200
    txn_id = _json(resp)["id"]

    import database as _db
    txn = _db.get_transaction(txn_id)
    assert txn["fee_currency"] == "USD"
    assert txn["fee_exchange_rate"] == 0.8
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
def test_create_transaction_with_unknown_ticker_triggers_price_fetch(client):
    """Regression test: a brand-new ticker must also get an immediate price-history fetch,
    not just a metadata update — otherwise it stays unpriced until the next nightly run."""
    account_id = _create_account(client)
    with (
        patch("api_routes_accounts.update_single_profile"),
        patch("api_routes_accounts.fetch_and_save_single_ticker") as mock_fetch,
    ):
        resp = client.post(f"/api/accounts/{account_id}/transactions", json={
            "txn_type": "Buy", "txn_date": "2026-01-15", "ticker": "ZZZNOTREAL2",
            "currency": "USD", "quantity": 1, "unit_price": 1.0, "exchange_rate": 1.0,
        })
        assert resp.status_code == 200
        mock_fetch.assert_called_once_with("ZZZNOTREAL2")

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
    import database as _db
    before = _db.get_price_history(account_id)   # House creation seeds a purchase-value row — not the Test action's concern

    with patch("requests.get", return_value=_mock_html_resp('<div id="gf-price">487000</div>')):
        resp = client.post(f"/api/accounts/{account_id}/scraper/test", json={
            "url": "http://example.test/house.html", "selector": "#gf-price",
        })
    assert resp.status_code == 200
    assert _json(resp)["price"] == 487000.0

    assert _db.get_price_history(account_id) == before
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


@pytest.mark.api
def test_pension_ticker_label_create_and_update_roundtrip(client):
    resp = client.post("/api/accounts", json={
        "name": "PensionLabelApiAcc", "currency": "GBP", "account_type": "Pension",
        "pension_ticker_label": "My Workplace Pension",
    })
    account_id = _json(resp)["id"]

    resp = client.get("/api/accounts")
    acc = next(a for a in _json(resp)["accounts"] if a["id"] == account_id)
    assert acc["pension_ticker_label"] == "My Workplace Pension"

    client.put(f"/api/accounts/{account_id}", json={
        "name": "PensionLabelApiAcc", "currency": "GBP", "account_type": "Pension",
        "pension_ticker_label": "Renamed Pension",
    })
    resp = client.get("/api/accounts")
    acc = next(a for a in _json(resp)["accounts"] if a["id"] == account_id)
    assert acc["pension_ticker_label"] == "Renamed Pension"

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_list_accounts_includes_scraper_last_status(client):
    account_id = _create_account(client, account_type="House")
    client.put(f"/api/accounts/{account_id}/scraper-config", json={
        "scraper_url": "http://example.com", "scraper_selector": "#price", "scraper_enabled": True,
    })

    resp = client.get("/api/accounts")
    acc = next(a for a in _json(resp)["accounts"] if a["id"] == account_id)
    assert acc["scraper_last_status"] is None   # scraper job has never run yet

    import database as _db
    conn = _db.get_connection()
    conn.execute(
        "INSERT INTO scheduler_run_log (job_id, last_run, last_status) VALUES (?, datetime('now'), 'success')",
        (f"account_scraper_{account_id}_job",)
    )
    conn.commit()
    conn.close()
    resp = client.get("/api/accounts")
    acc = next(a for a in _json(resp)["accounts"] if a["id"] == account_id)
    assert acc["scraper_last_status"] == "success"

    conn = _db.get_connection()
    conn.execute(
        "UPDATE scheduler_run_log SET last_status = 'error' WHERE job_id = ?",
        (f"account_scraper_{account_id}_job",)
    )
    conn.commit()
    conn.close()
    resp = client.get("/api/accounts")
    acc = next(a for a in _json(resp)["accounts"] if a["id"] == account_id)
    assert acc["scraper_last_status"] == "error"

    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_list_accounts_includes_current_balance_for_pension_and_house(client):
    """The tile shows the live equity value ('Current Balance'), not the static initial_cash —
    computed for Pension and House, where the account's own scraped price is the source of truth."""
    pension_id = _create_account(client, account_type="Pension", opening_balance_units=100.0, initial_cash=200.0)
    house_id = _create_account(client, account_type="House", initial_cash=300000.0)
    trading_id = _create_account(client, account_type="Trading")

    resp = client.get("/api/accounts")
    accounts = _json(resp)["accounts"]
    pension_acc = next(a for a in accounts if a["id"] == pension_id)
    house_acc = next(a for a in accounts if a["id"] == house_id)
    trading_acc = next(a for a in accounts if a["id"] == trading_id)

    assert pension_acc["current_balance"] == 200.0   # no scraped price yet — falls back to cost basis
    assert house_acc["current_balance"] == 300000.0  # House's purchase value seeds account_price_history
    assert "current_balance" not in trading_acc

    import database as _db
    _db.soft_delete_account(pension_id)
    _db.soft_delete_account(house_id)
    _db.soft_delete_account(trading_id)


@pytest.mark.api
def test_list_accounts_includes_holdings_count_equity_value_cash_balance_for_trading(client):
    trading_id = _create_account(client, account_type="Trading", initial_cash=1000.0)
    client.post(f"/api/accounts/{trading_id}/transactions", json={
        "txn_type": "Buy", "txn_date": "2026-01-01", "ticker": "AAPL", "currency": "USD",
        "quantity": 5, "unit_price": 100.0, "exchange_rate": 1.0,
    })

    resp = client.get("/api/accounts")
    acc = next(a for a in _json(resp)["accounts"] if a["id"] == trading_id)
    assert acc["holdings_count"] == 1
    assert "equity_value" in acc
    assert "cash_balance" in acc

    import database as _db
    _db.soft_delete_account(trading_id)


@pytest.mark.api
def test_list_accounts_includes_watchlist_count_and_breakdown(client):
    import database as _db
    wl = _db.get_watchlist_account()
    _db.add_watchlist_item(wl["id"], "AAPL", quote_type="EQUITY")
    _db.add_watchlist_item(wl["id"], "VUSA.L", quote_type="ETF")

    resp = client.get("/api/accounts")
    acc = next(a for a in _json(resp)["accounts"] if a["id"] == wl["id"])
    assert acc["watchlist_count"] == 2
    assert acc["watchlist_breakdown"] == {"equity": 1, "etf": 1, "fund": 0, "other": 0}

    _db.remove_watchlist_ticker(wl["id"], "AAPL")
    _db.remove_watchlist_ticker(wl["id"], "VUSA.L")


@pytest.mark.api
def test_creating_pension_account_with_opening_balance_creates_real_holding(client):
    """Regression: opening_balance_units entered via the API must actually show up as units held,
    not just sit on the account row — otherwise Admin Fee has nothing to deduct from."""
    resp = client.post("/api/accounts", json={
        "name": "OpeningBalanceHoldingAcc", "currency": "GBP", "account_type": "Pension",
        "initial_cash": 70000.0, "opening_balance_units": 70000.0, "opened_date": "2024-01-01",
    })
    account_id = _json(resp)["id"]

    resp = client.get(f"/api/accounts/{account_id}/pension/units-as-of?date=2024-01-01")
    assert _json(resp)["units"] == 70000.0

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_editing_pension_opening_balance_updates_holding_not_duplicates(client):
    account_id = _create_account(client, account_type="Pension", initial_cash=1000.0, opening_balance_units=1000.0)

    resp = client.put(f"/api/accounts/{account_id}", json={
        "name": "API Test Account", "currency": "GBP", "account_type": "Pension",
        "initial_cash": 2000.0, "opening_balance_units": 1000.0,
    })
    assert resp.status_code == 200

    resp = client.get(f"/api/accounts/{account_id}/transactions")
    buys = [t for t in _json(resp)["transactions"] if t["txn_type"] == "Buy"]
    assert len(buys) == 1
    assert buys[0]["unit_price"] == 2.0

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_creating_house_account_seeds_purchase_price_history(client):
    resp = client.post("/api/accounts", json={
        "name": "HousePurchaseApiAcc", "currency": "GBP", "account_type": "House",
        "initial_cash": 300000.0, "opened_date": "2020-03-15",
    })
    account_id = _json(resp)["id"]

    import database as _db
    history = _db.get_price_history(account_id)
    assert len(history) == 1
    assert history[0]["price_date"] == "2020-03-15"
    assert history[0]["price"] == 300000.0
    assert history[0]["source"] == "purchase"

    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_editing_house_purchase_value_updates_price_history_in_place(client):
    account_id = _create_account(client, account_type="House", initial_cash=300000.0, opened_date="2020-03-15")

    client.put(f"/api/accounts/{account_id}", json={
        "name": "API Test Account", "currency": "GBP", "account_type": "House",
        "initial_cash": 325000.0, "opened_date": "2020-03-15",
    })

    import database as _db
    history = _db.get_price_history(account_id)
    assert len(history) == 1
    assert history[0]["price"] == 325000.0

    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_update_account_rejects_changing_account_type_between_real_types(client):
    """account_type is immutable after creation for every type, not just Watchlist — changing it
    could silently corrupt the ledger (e.g. House->Pension would expose a non-existent synthetic
    holding ticker)."""
    account_id = _create_account(client, account_type="Trading")
    resp = client.put(f"/api/accounts/{account_id}", json={
        "name": "API Test Account", "currency": "GBP", "account_type": "House",
    })
    assert resp.status_code == 400

    resp = client.get("/api/accounts")
    acc = next(a for a in _json(resp)["accounts"] if a["id"] == account_id)
    assert acc["account_type"] == "Trading"   # unchanged

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_autotopup_config_rejects_non_trading_account(client):
    account_id = _create_account(client, account_type="House")
    resp = client.put(f"/api/accounts/{account_id}/autotopup-config", json={
        "enabled": True, "amount": 100.0, "frequency": "monthly", "day_of_month": 26,
    })
    assert resp.status_code == 400

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_autotopup_config_validates_required_fields_when_enabling(client):
    account_id = _create_account(client)
    resp = client.put(f"/api/accounts/{account_id}/autotopup-config", json={"enabled": True})
    assert resp.status_code == 400

    resp = client.put(f"/api/accounts/{account_id}/autotopup-config", json={
        "enabled": True, "amount": 50.0, "frequency": "weekly", "day_of_week": 6,
    })
    assert resp.status_code == 400

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_autotopup_config_save_registers_and_unregister_disable(client):
    account_id = _create_account(client)
    resp = client.put(f"/api/accounts/{account_id}/autotopup-config", json={
        "enabled": True, "amount": 300.0, "frequency": "monthly", "day_of_month": 26,
    })
    assert resp.status_code == 200

    import scheduler_engine
    live_ids = {j.id for j in scheduler_engine.scheduler.get_jobs()}
    assert f"account_autotopup_{account_id}_job" in live_ids

    resp = client.put(f"/api/accounts/{account_id}/autotopup-config", json={"enabled": False})
    assert resp.status_code == 200
    live_ids = {j.id for j in scheduler_engine.scheduler.get_jobs()}
    assert f"account_autotopup_{account_id}_job" not in live_ids

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_autotopup_job_runner_creates_pending_not_a_transaction(client):
    """Direct runner-level test per AGENTS.md — exercising only the engine wouldn't catch a
    wiring bug in the job function itself. notify() is mocked, same as
    test_run_account_value_snapshot_notifies_on_success_and_failure, so this doesn't fire a real
    Nextcloud message — account_autotopup_status defaults to nextcloud_talk=True."""
    account_id = _create_account(client, initial_cash=500.0)
    client.put(f"/api/accounts/{account_id}/autotopup-config", json={
        "enabled": True, "amount": 200.0, "frequency": "monthly", "day_of_month": 26,
    })

    import scheduler_jobs
    with patch("scheduler_jobs.notify"):
        scheduler_jobs._run_account_topup_job(account_id)

    import database as _db
    pending = _db.get_unresolved_pending_topups(account_id)
    assert len(pending) == 1
    assert pending[0]["expected_amount"] == 200.0

    from accounts_engine import account_summary
    assert account_summary(account_id)["cash_balance"] == 500.0   # unchanged until confirmed

    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_autotopup_confirm_posts_cash_transaction_and_updates_balance(client):
    account_id = _create_account(client, initial_cash=1000.0)
    import database as _db
    pending_id = _db.create_pending_topup(account_id, "2026-06-26", 250.0)

    resp = client.post(f"/api/accounts/{account_id}/autotopup/confirm", json={
        "pending_id": pending_id, "amount": 252.0, "txn_date": "2026-06-27",
    })
    assert resp.status_code == 200
    txn_id = _json(resp)["txn_id"]

    from accounts_engine import account_summary
    assert account_summary(account_id)["cash_balance"] == 1252.0

    pending = _db.get_pending_topup(pending_id)
    assert pending["status"] == "confirmed"
    assert pending["txn_id"] == txn_id
    assert pending["confirmed_amount"] == 252.0

    assert _db.get_unresolved_pending_topups(account_id) == []
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_autotopup_confirm_rejects_already_resolved_pending(client):
    account_id = _create_account(client)
    import database as _db
    pending_id = _db.create_pending_topup(account_id, "2026-06-26", 100.0)
    client.post(f"/api/accounts/{account_id}/autotopup/dismiss", json={"pending_id": pending_id})

    resp = client.post(f"/api/accounts/{account_id}/autotopup/confirm", json={
        "pending_id": pending_id, "amount": 100.0, "txn_date": "2026-06-27",
    })
    assert resp.status_code == 400

    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_autotopup_confirm_rejects_pending_id_belonging_to_another_account(client):
    """A pending row created for one account must not be confirmable/dismissable through a
    different account's URL — the route's account_id must be cross-checked, not just used for
    the Trading-type gate."""
    account_a = _create_account(client, initial_cash=0.0)
    account_b = _create_account(client, initial_cash=0.0)
    import database as _db
    pending_id = _db.create_pending_topup(account_a, "2026-06-26", 100.0)

    resp = client.post(f"/api/accounts/{account_b}/autotopup/confirm", json={
        "pending_id": pending_id, "amount": 100.0, "txn_date": "2026-06-27",
    })
    assert resp.status_code == 400

    resp = client.post(f"/api/accounts/{account_b}/autotopup/dismiss", json={"pending_id": pending_id})
    assert resp.status_code == 400

    assert _db.get_pending_topup(pending_id)["status"] == "pending"

    _db.soft_delete_account(account_a)
    _db.soft_delete_account(account_b)


@pytest.mark.api
def test_autotopup_dismiss_does_not_post_a_transaction(client):
    account_id = _create_account(client, initial_cash=1000.0)
    import database as _db
    pending_id = _db.create_pending_topup(account_id, "2026-06-26", 250.0)

    resp = client.post(f"/api/accounts/{account_id}/autotopup/dismiss", json={"pending_id": pending_id})
    assert resp.status_code == 200

    from accounts_engine import account_summary
    assert account_summary(account_id)["cash_balance"] == 1000.0
    assert _db.get_pending_topup(pending_id)["status"] == "dismissed"

    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_autotopup_pending_topups_stack_and_resolve_independently(client):
    account_id = _create_account(client)
    import database as _db
    pid1 = _db.create_pending_topup(account_id, "2026-05-26", 100.0)
    pid2 = _db.create_pending_topup(account_id, "2026-06-26", 100.0)

    unresolved = _db.get_unresolved_pending_topups(account_id)
    assert [p["id"] for p in unresolved] == [pid1, pid2]   # oldest first

    client.post(f"/api/accounts/{account_id}/autotopup/dismiss", json={"pending_id": pid1})
    unresolved = _db.get_unresolved_pending_topups(account_id)
    assert [p["id"] for p in unresolved] == [pid2]

    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_list_accounts_includes_pending_topups_for_trading(client):
    account_id = _create_account(client)
    import database as _db
    _db.create_pending_topup(account_id, "2026-06-26", 100.0)

    resp = client.get("/api/accounts")
    acc = next(a for a in _json(resp)["accounts"] if a["id"] == account_id)
    assert len(acc["pending_topups"]) == 1

    _db.soft_delete_account(account_id)


# ── Home Assistant integration endpoints ──────────────────────────────────────

@pytest.mark.api
def test_portfolio_totals_happy_path_with_trading_account(client):
    with patch("api_routes_accounts.resnapshot_account"):
        account_id = _create_account(client, name="PortfolioTotalsAcc")

    resp = client.get("/api/accounts/portfolio-totals")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    for key in (
        "account_count", "base_currency", "current_value", "total_investment",
        "portfolio_gain", "portfolio_gain_fx", "unrealized_pnl", "twr_pct",
        "twr_fx_pct", "portfolio_dividends",
    ):
        assert key in data, f"Missing '{key}' in portfolio-totals response: {data}"
    assert data["account_count"] >= 1

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_portfolio_totals_zero_trading_accounts_returns_all_zero_shape(client):
    import database as _db
    for acc in _db.get_accounts():
        if acc["account_type"] == "Trading":
            _db.soft_delete_account(acc["id"])

    resp = client.get("/api/accounts/portfolio-totals")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert data["account_count"] == 0
    assert data["current_value"] == 0.0
    assert data["total_investment"] == 0.0
    assert data["portfolio_gain"] == 0.0
    assert data["portfolio_gain_pct"] is None
    assert data["twr_pct"] is None


@pytest.mark.api
def test_refresh_now_returns_queued_immediately(client):
    with patch("api_routes_accounts.fetch_and_save_pulse") as mock_fetch, \
         patch("api_routes_accounts.refresh_performance_cache") as mock_refresh:
        resp = client.post("/api/accounts/refresh-now")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "queued"
    mock_fetch.assert_called_once()
    mock_refresh.assert_not_called()  # no Trading accounts created in this test


@pytest.mark.api
def test_refresh_now_invokes_background_task_functions(client):
    with patch("api_routes_accounts.resnapshot_account"):
        account_id = _create_account(client, name="RefreshNowAcc")

    with patch("api_routes_accounts.fetch_and_save_pulse") as mock_fetch, \
         patch("api_routes_accounts.refresh_performance_cache") as mock_refresh, \
         patch("api_routes_accounts.notify") as mock_notify:
        resp = client.post("/api/accounts/refresh-now")
    assert resp.status_code == 200
    assert _json(resp)["status"] == "queued"
    mock_fetch.assert_called_once()
    mock_refresh.assert_called_once_with(account_id)
    mock_notify.assert_called_once()
    assert mock_notify.call_args[0][0] == "ha_refresh_now_status"

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_holdings_list_triggers_background_refresh_for_stale_held_ticker(client):
    """Regression test: polling GET /accounts/holdings-list (what the Home Assistant
    integration polls) must itself trigger a real Yahoo Finance refresh for a held ticker
    whose market_pulse_cache is due — not just read whatever's already cached."""
    with patch("api_routes_accounts.resnapshot_account"), \
         patch("api_routes_accounts.update_single_profile"):
        account_id = _create_account(client, name="HoldingsListRefreshTriggerAcc")
        client.post(f"/api/accounts/{account_id}/transactions", json={
            "txn_type": "Buy", "txn_date": "2026-01-15", "ticker": "ZZTRIGGERREF",
            "currency": "GBP", "quantity": 5, "unit_price": 100.0, "exchange_rate": 1.0,
            "update_cash": True,
        })

    with patch("api_routes_accounts.fetch_and_save_pulse") as mock_fetch, \
         patch("accounts_engine.time_engine.is_trading_session", return_value=True):
        resp = client.get("/api/accounts/holdings-list")
    assert resp.status_code == 200
    mock_fetch.assert_called_once()
    assert "ZZTRIGGERREF" in mock_fetch.call_args[0][0]

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_holdings_list_does_not_trigger_refresh_when_market_closed(client):
    with patch("api_routes_accounts.resnapshot_account"), \
         patch("api_routes_accounts.update_single_profile"):
        account_id = _create_account(client, name="HoldingsListNoRefreshAcc")
        client.post(f"/api/accounts/{account_id}/transactions", json={
            "txn_type": "Buy", "txn_date": "2026-01-15", "ticker": "ZZNOTRIGGERREF",
            "currency": "GBP", "quantity": 5, "unit_price": 100.0, "exchange_rate": 1.0,
            "update_cash": True,
        })

    with patch("api_routes_accounts.fetch_and_save_pulse") as mock_fetch, \
         patch("accounts_engine.time_engine.is_trading_session", return_value=False):
        resp = client.get("/api/accounts/holdings-list")
    assert resp.status_code == 200
    mock_fetch.assert_not_called()

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_portfolio_totals_triggers_background_refresh_for_stale_held_ticker(client):
    with patch("api_routes_accounts.resnapshot_account"), \
         patch("api_routes_accounts.update_single_profile"):
        account_id = _create_account(client, name="PortfolioTotalsRefreshTriggerAcc")
        client.post(f"/api/accounts/{account_id}/transactions", json={
            "txn_type": "Buy", "txn_date": "2026-01-15", "ticker": "ZZPTTRIGGERREF",
            "currency": "GBP", "quantity": 5, "unit_price": 100.0, "exchange_rate": 1.0,
            "update_cash": True,
        })

    with patch("api_routes_accounts.fetch_and_save_pulse") as mock_fetch, \
         patch("accounts_engine.time_engine.is_trading_session", return_value=True):
        resp = client.get("/api/accounts/portfolio-totals")
    assert resp.status_code == 200
    mock_fetch.assert_called_once()
    assert "ZZPTTRIGGERREF" in mock_fetch.call_args[0][0]

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_list_with_metrics_triggers_background_refresh_for_stale_held_ticker(client):
    with patch("api_routes_accounts.resnapshot_account"), \
         patch("api_routes_accounts.update_single_profile"):
        account_id = _create_account(client, name="ListWithMetricsRefreshTriggerAcc")
        client.post(f"/api/accounts/{account_id}/transactions", json={
            "txn_type": "Buy", "txn_date": "2026-01-15", "ticker": "ZZLWMTRIGGERREF",
            "currency": "GBP", "quantity": 5, "unit_price": 100.0, "exchange_rate": 1.0,
            "update_cash": True,
        })

    with patch("api_routes_accounts.fetch_and_save_pulse") as mock_fetch, \
         patch("accounts_engine.time_engine.is_trading_session", return_value=True):
        resp = client.get("/api/accounts/list-with-metrics")
    assert resp.status_code == 200
    mock_fetch.assert_called_once()
    assert "ZZLWMTRIGGERREF" in mock_fetch.call_args[0][0]

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_refresh_now_notifies_error_on_failure(client):
    with patch("api_routes_accounts.resnapshot_account"):
        account_id = _create_account(client, name="RefreshNowFailAcc")

    with patch("api_routes_accounts.fetch_and_save_pulse", side_effect=RuntimeError("boom")), \
         patch("api_routes_accounts.notify") as mock_notify:
        resp = client.post("/api/accounts/refresh-now")
    assert resp.status_code == 200
    assert _json(resp)["status"] == "queued"
    mock_notify.assert_called_once()
    assert mock_notify.call_args[0][0] == "ha_refresh_now_status"
    assert mock_notify.call_args[0][1] == "Error"

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_accounts_list_with_metrics_happy_path_with_trading_account(client):
    with patch("api_routes_accounts.resnapshot_account"):
        account_id = _create_account(client, name="AcctListMetricsAcc")

    resp = client.get("/api/accounts/list-with-metrics")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert data["base_currency"]
    assert isinstance(data["accounts"], list)
    row = next(r for r in data["accounts"] if r["account_id"] == account_id)
    for key in (
        "account_id", "name", "cash_balance", "equity_value", "unrealized_pnl",
        "realized_pnl", "dividend_income", "interest_income",
        "gain_1d", "gain_1w", "gain_1m", "gain_3m", "gain_1y", "mwrr_pct",
    ):
        assert key in row, f"Missing '{key}' in list-with-metrics account row: {row}"

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_accounts_list_with_metrics_zero_trading_accounts_returns_empty_list(client):
    import database as _db
    for acc in _db.get_accounts():
        if acc["account_type"] == "Trading":
            _db.soft_delete_account(acc["id"])

    resp = client.get("/api/accounts/list-with-metrics")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert data["accounts"] == []


@pytest.mark.api
def test_api_holdings_list_status_success(client):
    with patch("api_routes_accounts.resnapshot_account"), \
         patch("api_routes_accounts.update_single_profile"):
        account_id = _create_account(client, name="HoldingsListAcc")
        client.post(f"/api/accounts/{account_id}/transactions", json={
            "txn_type": "Buy", "txn_date": "2026-01-15", "ticker": "ZZHOLDLIST",
            "currency": "GBP", "quantity": 5, "unit_price": 100.0, "exchange_rate": 1.0,
            "update_cash": True,
        })

    resp = client.get("/api/accounts/holdings-list")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert data["base_currency"]
    row = next(r for r in data["holdings"] if r["ticker"] == "ZZHOLDLIST" and r["account_id"] == account_id)
    for key in (
        "account_id", "account_name", "ticker", "shares", "market_value", "total_investment",
        "gain_value", "gain_pct", "profit_and_loss", "accumulated_dividends", "trend_vs_buy",
        "asset_class", "data_source", "market_change_24h", "market_change_pct_24h", "rsi",
        "trend_50d", "trend_200d", "next_earnings_date", "low_limit_set", "high_limit_set",
        "low_limit_reached", "high_limit_reached",
    ):
        assert key in row, f"Missing '{key}' in holdings-list row: {row}"

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_api_other_accounts_list_status_success(client):
    account_id = _create_account(client, name="OtherAcctsApiPension", account_type="Pension")
    client.post(f"/api/accounts/{account_id}/pension/contribution", json={
        "txn_date": "2026-06-01", "amount": 150.0, "unit_price": 1.5,
    })

    resp = client.get("/api/accounts/other-accounts-list")
    assert resp.status_code == 200
    data = _json(resp)
    assert data["status"] == "success"
    assert data["base_currency"]
    row = next(r for r in data["accounts"] if r["account_id"] == account_id)
    for key in ("account_id", "name", "account_type", "currency", "current_value", "performance", "last_updated"):
        assert key in row, f"Missing '{key}' in other-accounts-list row: {row}"
    assert row["account_type"] == "Pension"

    import database as _db
    _db.soft_delete_account(account_id)


@pytest.mark.api
def test_api_set_holding_price_limit_low_only_does_not_clear_high(client):
    with patch("api_routes_accounts.resnapshot_account"):
        account_id = _create_account(client, name="LimitApiAcc")

    resp1 = client.post("/api/accounts/holding-price-limit", json={
        "account_id": account_id, "ticker": "ZZLIMITAPI", "low_limit": 10.0,
    })
    assert resp1.status_code == 200
    assert _json(resp1)["status"] == "success"

    resp2 = client.post("/api/accounts/holding-price-limit", json={
        "account_id": account_id, "ticker": "ZZLIMITAPI", "high_limit": 20.0,
    })
    assert resp2.status_code == 200
    assert _json(resp2)["status"] == "success"

    from db_accounts import get_all_holding_price_limits
    limits = get_all_holding_price_limits()[(account_id, "ZZLIMITAPI")]
    assert limits["low_limit"] == 10.0
    assert limits["high_limit"] == 20.0

    import database as _db
    _db.soft_delete_account(account_id)
