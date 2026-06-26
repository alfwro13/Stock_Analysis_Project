import pytest

from database import (
    create_account,
    get_account,
    get_accounts,
    update_account,
    soft_delete_account,
    add_transaction,
    get_transaction,
    get_transactions,
    update_transaction,
    delete_transaction,
    upsert_value_snapshot,
    get_value_history,
)


@pytest.mark.db
def test_create_and_get_account_roundtrip():
    aid = create_account("Roundtrip ISA", "GBP", initial_cash=1500.0, note="hello")
    acc = get_account(aid)
    assert acc["name"] == "Roundtrip ISA"
    assert acc["currency"] == "GBP"
    assert acc["initial_cash"] == 1500.0
    assert acc["note"] == "hello"
    assert acc["deleted_at"] is None


@pytest.mark.db
def test_create_account_with_opened_date_roundtrip():
    aid = create_account("HistoricalAcc", "GBP", opened_date="2020-03-15")
    assert get_account(aid)["opened_date"] == "2020-03-15"


@pytest.mark.db
def test_create_account_without_opened_date_defaults_to_none():
    aid = create_account("NoOpenedDateAcc", "GBP")
    assert get_account(aid)["opened_date"] is None


@pytest.mark.db
def test_update_account_opened_date():
    aid = create_account("UpdateOpenedDateAcc", "GBP")
    assert update_account(aid, opened_date="2019-01-01") is True
    assert get_account(aid)["opened_date"] == "2019-01-01"


@pytest.mark.db
def test_update_account_rejects_unknown_columns():
    aid = create_account("Guard", "GBP")
    assert update_account(aid, name="Renamed") is True
    assert get_account(aid)["name"] == "Renamed"
    assert update_account(aid, deleted_at="2020-01-01") is False     # not an allowed column
    assert update_account(aid, bogus=1) is False


@pytest.mark.db
def test_soft_delete_excludes_account_from_list():
    aid = create_account("ToDelete", "GBP")
    assert any(a["id"] == aid for a in get_accounts())
    assert soft_delete_account(aid) is True
    assert all(a["id"] != aid for a in get_accounts())
    assert get_account(aid) is None
    assert any(a["id"] == aid for a in get_accounts(include_deleted=True))


@pytest.mark.db
def test_transaction_add_update_delete_roundtrip():
    aid = create_account("TxnAcc", "GBP")
    tid = add_transaction(aid, "Buy", "2026-01-05", ticker="ZDBT", currency="GBP",
                          quantity=3, unit_price=100, fee=1.0, exchange_rate=1.0)
    txn = get_transaction(tid)
    assert txn["txn_type"] == "Buy"
    assert txn["quantity"] == 3
    assert txn["update_cash"] == 1

    assert update_transaction(tid, unit_price=120, notes="adjusted") is True
    assert get_transaction(tid)["unit_price"] == 120
    assert update_transaction(tid, account_id=999) is False           # not an allowed column

    assert len(get_transactions(aid)) == 1
    assert delete_transaction(tid) is True
    assert get_transaction(tid) is None
    assert get_transactions(aid) == []


@pytest.mark.db
def test_transaction_isin_roundtrip():
    aid = create_account("IsinAcc", "GBP")
    tid = add_transaction(aid, "Buy", "2026-01-05", ticker="ZISIN", isin="GB0003452173",
                          currency="GBP", quantity=1, unit_price=10)
    assert get_transaction(tid)["isin"] == "GB0003452173"

    assert update_transaction(tid, isin="GB00B15FWH70") is True
    assert get_transaction(tid)["isin"] == "GB00B15FWH70"


@pytest.mark.db
def test_transaction_isin_defaults_to_none():
    aid = create_account("NoIsinAcc", "GBP")
    tid = add_transaction(aid, "Cash", "2026-01-05", unit_price=50)
    assert get_transaction(tid)["isin"] is None


@pytest.mark.db
def test_value_snapshot_upsert_is_idempotent():
    aid = create_account("SnapAcc", "GBP")
    upsert_value_snapshot(aid, "2026-01-05", 3000.0, 1000.0, 2000.0)
    upsert_value_snapshot(aid, "2026-01-05", 3500.0, 1200.0, 2300.0)   # same date → updated, not duplicated
    history = get_value_history(aid)
    assert len(history) == 1
    assert history[0]["total_value"] == 3500.0
    assert history[0]["equity_value"] == 2300.0
