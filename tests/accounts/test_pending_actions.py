"""Pending account actions: the registry collector, GET /api/accounts/pending-actions, the tile field and the navbar badge."""
import pytest

import treasury_bill_engine as tbe
from accounts.pending_actions import PENDING_ACTION_KINDS, pending_account_actions
from db import database as _db
from db.account_reminders import create_pending_reminder


def _delete_pending_rows(account_ids):
    conn = None
    try:
        conn = _db.get_connection()
        for account_id in account_ids:
            conn.execute("DELETE FROM account_autotopup_pending WHERE account_id = ?", (account_id,))
            conn.execute("DELETE FROM account_reminder_pending WHERE account_id = ?", (account_id,))
        conn.commit()
    finally:
        if conn:
            conn.close()


@pytest.fixture
def accounts_with_pending():
    trading = _db.create_account("Pending Trading", "GBP", initial_cash=2000.0)
    pension = _db.create_account("Pending Pension", "GBP", account_type="Pension")
    topup_id = _db.create_pending_topup(trading, "2026-06-26", 200.0)
    tbe.buy_treasury_bill(trading, "2026-07-01", 501.43, 500.0, "2026-07-29", indicative_ytm=3.72)
    create_pending_reminder({"id": 987001, "account_id": pension, "reminder_type": "pay_in"}, "2026-11-01")
    yield {"trading": trading, "pension": pension, "topup_id": topup_id}
    _delete_pending_rows([trading, pension])
    _db.soft_delete_account(trading)
    _db.soft_delete_account(pension)


@pytest.mark.db
def test_every_registered_kind_is_described_and_readable():
    assert set(PENDING_ACTION_KINDS) == {"autotopup", "pension_reminder", "treasury_bill_ytm"}
    for spec in PENDING_ACTION_KINDS.values():
        assert spec["label"] and callable(spec["read"])


@pytest.mark.db
def test_collector_returns_one_item_per_pending_action_across_kinds(accounts_with_pending):
    trading, pension = accounts_with_pending["trading"], accounts_with_pending["pension"]

    trading_items = pending_account_actions(trading)
    assert sorted(i["kind"] for i in trading_items) == ["autotopup", "treasury_bill_ytm"]
    assert {i["account_name"] for i in trading_items} == {"Pending Trading"}
    topup = next(i for i in trading_items if i["kind"] == "autotopup")
    assert topup["title"] == "Auto Top-up"
    assert topup["detail"] == "Due 2026-06-26, expected 200.00 GBP."

    pension_items = pending_account_actions(pension)
    assert [(i["kind"], i["title"]) for i in pension_items] == [("pension_reminder", "November 2026 Pay In")]

    everything = pending_account_actions()
    assert {i["account_id"] for i in everything} >= {trading, pension}
    assert len([i for i in everything if i["account_id"] in (trading, pension)]) == 3


@pytest.mark.db
def test_pending_items_of_a_deleted_account_are_not_counted(accounts_with_pending):
    trading = accounts_with_pending["trading"]
    _db.soft_delete_account(trading)
    assert pending_account_actions(trading) == []
    assert all(i["account_id"] != trading for i in pending_account_actions())


@pytest.mark.api
def test_endpoint_count_matches_items_and_drops_once_resolved(client, accounts_with_pending):
    trading = accounts_with_pending["trading"]
    body = client.get("/api/accounts/pending-actions").json()
    assert body["status"] == "success"
    assert body["count"] == len(body["items"])
    assert len([i for i in body["items"] if i["account_id"] == trading]) == 2

    resp = client.post(f"/api/accounts/{trading}/autotopup/dismiss", json={"pending_id": accounts_with_pending["topup_id"]})
    assert resp.status_code == 200
    after = client.get("/api/accounts/pending-actions").json()
    assert after["count"] == body["count"] - 1
    assert [i["kind"] for i in after["items"] if i["account_id"] == trading] == ["treasury_bill_ytm"]


@pytest.mark.api
def test_account_list_carries_pending_actions_per_tile(client, accounts_with_pending):
    accounts = client.get("/api/accounts").json()["accounts"]
    by_id = {a["id"]: a for a in accounts}
    trading = by_id[accounts_with_pending["trading"]]
    assert sorted(i["kind"] for i in trading["pending_actions"]) == ["autotopup", "treasury_bill_ytm"]
    assert [i["title"] for i in by_id[accounts_with_pending["pension"]]["pending_actions"]] == ["November 2026 Pay In"]
    assert "pending_topups" not in trading and "pending_reminders" not in by_id[accounts_with_pending["pension"]]


@pytest.mark.api
def test_every_page_carries_the_accounts_badge_and_the_poller(client):
    html = client.get("/accounts").text
    assert 'id="nav-accounts-badge"' in html
    js = client.get("/static/js/navbar.js").text
    assert "/api/accounts/pending-actions" in js
