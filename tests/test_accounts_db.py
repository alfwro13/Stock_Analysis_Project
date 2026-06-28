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
    get_all_account_tickers,
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
def test_create_account_defaults_account_type_to_trading():
    aid = create_account("DefaultTypeAcc", "GBP")
    assert get_account(aid)["account_type"] == "Trading"


@pytest.mark.db
def test_create_account_with_explicit_account_type_roundtrip():
    aid = create_account("HouseAcc", "GBP", account_type="House")
    assert get_account(aid)["account_type"] == "House"


@pytest.mark.db
def test_update_account_type():
    aid = create_account("RetypeAcc", "GBP")
    assert update_account(aid, account_type="Pension") is True
    assert get_account(aid)["account_type"] == "Pension"


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


@pytest.mark.db
def test_get_all_account_tickers_returns_distinct_tickers():
    aid = create_account("AllTickersAcc", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZAATX", currency="GBP",
                     quantity=10, unit_price=50, exchange_rate=1.0)
    add_transaction(aid, "Buy", "2026-01-06", ticker="ZZAATX", currency="GBP",
                     quantity=5, unit_price=55, exchange_rate=1.0)
    add_transaction(aid, "Buy", "2026-01-07", ticker="ZZAATY", currency="GBP",
                     quantity=3, unit_price=20, exchange_rate=1.0)

    tickers = get_all_account_tickers()

    assert tickers.count("ZZAATX") == 1
    assert "ZZAATY" in tickers


@pytest.mark.db
def test_get_all_account_tickers_excludes_cash_rows_and_pension_synthetic_ticker():
    aid = create_account("AllTickersCashAcc", "GBP")
    add_transaction(aid, "Cash", "2026-01-05", unit_price=100)
    add_transaction(aid, "Buy", "2026-01-06", ticker="PENSION-99999", currency="GBP",
                     quantity=1, unit_price=1, exchange_rate=1.0, update_cash=False)

    tickers = get_all_account_tickers()

    assert "PENSION-99999" not in tickers
    assert None not in tickers


@pytest.mark.db
def test_get_all_account_tickers_excludes_non_holding_ticker_values():
    """Interest/Dividend/Fee/Cash rows can carry non-ticker values (e.g. a CSV-imported
    transaction GUID) in the ticker column — only Buy/Sell represent an actual holding."""
    aid = create_account("InterestGuidAcc", "GBP")
    add_transaction(aid, "Interest", "2026-01-05", ticker="055c6097-2e06-4f56-9467-26d555b04178",
                     unit_price=1.50)

    tickers = get_all_account_tickers()

    assert "055c6097-2e06-4f56-9467-26d555b04178" not in tickers


@pytest.mark.db
def test_get_all_account_tickers_excludes_soft_deleted_accounts():
    aid = create_account("SoftDeletedTickerAcc", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZDELETEDACC", currency="GBP",
                     quantity=1, unit_price=10, exchange_rate=1.0)
    soft_delete_account(aid)

    tickers = get_all_account_tickers()

    assert "ZZDELETEDACC" not in tickers
