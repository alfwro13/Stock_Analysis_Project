"""Position Targets exist only for what an account holds or watches: a full sale (or an edit or deletion that closes the position) and a ticker leaving the Watchlist clear its targets, a partial sale and other accounts' targets are untouched, a deleted account loses its targets, and a failing listener never fails the ledger write."""
import logging

import pytest

from accounts.accounts_engine import clear_orphaned_position_targets
from db import account_store, schema as db_schema
from db.database import get_connection
from db.account_store import (
    add_transaction, add_watchlist_item, create_account, delete_transaction, delete_watchlist_items, get_all_holding_price_limits,
    get_watchlist_account, get_watchlist_items, remove_watchlist_ticker, soft_delete_account, update_transaction, upsert_holding_price_limit,
)

pytestmark = pytest.mark.restore_tables("accounts", "account_transactions", "holding_price_limits", "watchlist_items", "data_migrations")

TICKER, OTHER = "ZZTGT", "ZZTGO"


def _buy(account, ticker=TICKER, quantity=10, day="2026-01-05"):
    return add_transaction(account, "Buy", day, ticker=ticker, currency="GBP", quantity=quantity, unit_price=100, exchange_rate=1.0)


def _sell(account, ticker=TICKER, quantity=10, day="2026-02-05"):
    return add_transaction(account, "Sell", day, ticker=ticker, currency="GBP", quantity=quantity, unit_price=110, exchange_rate=1.0)


def _limits():
    return get_all_holding_price_limits()


@pytest.fixture
def book():
    first = create_account("TargetClearA", "GBP", initial_cash=0.0)
    second = create_account("TargetClearB", "GBP", initial_cash=0.0)
    watchlist = get_watchlist_account()["id"]
    for account in (first, second):
        _buy(account)
        _buy(account, OTHER)
        upsert_holding_price_limit(account, TICKER, low_limit=90.0, high_limit=120.0)
        upsert_holding_price_limit(account, OTHER, low_limit=1.0, high_limit=None)
    add_watchlist_item(watchlist, TICKER, currency="GBP", quote_type="EQUITY")
    add_watchlist_item(watchlist, OTHER, currency="GBP", quote_type="EQUITY")
    upsert_holding_price_limit(watchlist, TICKER, low_limit=80.0)
    upsert_holding_price_limit(watchlist, OTHER, high_limit=5.0)
    return first, second, watchlist


def test_a_full_sale_clears_that_accounts_targets_for_the_ticker_only(book):
    first, second, watchlist = book
    _sell(first)
    limits = _limits()
    assert (first, TICKER) not in limits
    assert (first, OTHER) in limits
    assert (second, TICKER) in limits
    assert (watchlist, TICKER) in limits


def test_a_partial_sale_keeps_the_targets(book):
    first, _, _ = book
    _sell(first, quantity=4)
    assert _limits()[(first, TICKER)] == {"low_limit": 90.0, "high_limit": 120.0}


def test_selling_in_two_steps_clears_only_when_the_position_reaches_zero(book):
    first, _, _ = book
    _sell(first, quantity=4)
    assert (first, TICKER) in _limits()
    _sell(first, quantity=6, day="2026-02-06")
    assert (first, TICKER) not in _limits()


def test_buying_again_after_a_full_close_starts_without_a_target(book):
    first, _, _ = book
    _sell(first)
    _buy(first, day="2026-03-01")
    assert (first, TICKER) not in _limits()


def test_deleting_the_sale_does_not_bring_the_target_back(book):
    first, _, _ = book
    sale = _sell(first)
    delete_transaction(sale)
    assert (first, TICKER) not in _limits()


def test_editing_a_sale_up_to_the_full_quantity_clears_the_targets(book):
    first, _, _ = book
    sale = _sell(first, quantity=3)
    assert (first, TICKER) in _limits()
    update_transaction(sale, quantity=10)
    assert (first, TICKER) not in _limits()


def test_deleting_the_buy_that_opened_a_position_clears_its_targets(book):
    first, second, _ = book
    lonely = _buy(first, "ZZTGL")
    upsert_holding_price_limit(first, "ZZTGL", high_limit=7.0)
    delete_transaction(lonely)
    assert (first, "ZZTGL") not in _limits()
    assert (second, TICKER) in _limits()


def test_a_dividend_does_not_run_the_listeners(book, monkeypatch):
    first, _, _ = book
    seen = []
    monkeypatch.setattr(account_store, "_ledger_listeners", [seen.append])
    add_transaction(first, "Dividend", "2026-03-01", ticker=TICKER, currency="GBP", quantity=None, unit_price=5.0, exchange_rate=1.0)
    assert seen == []
    _buy(first, day="2026-03-02")
    assert seen == [first]


def test_a_failing_listener_does_not_fail_the_write(book, monkeypatch, caplog):
    first, _, _ = book

    def broken(account_id):
        raise RuntimeError("boom")

    monkeypatch.setattr(account_store, "_ledger_listeners", [broken])
    with caplog.at_level(logging.ERROR, logger="db.account_store"):
        assert _buy(first, "ZZTGB") is not None
    assert "Ledger listener broken failed" in caplog.text


def test_soft_deleting_an_account_clears_all_its_targets(book):
    first, second, watchlist = book
    soft_delete_account(first)
    limits = _limits()
    assert not [key for key in limits if key[0] == first]
    assert (second, TICKER) in limits and (watchlist, TICKER) in limits


def test_removing_a_ticker_from_the_watchlist_clears_its_watchlist_target_only(book):
    first, _, watchlist = book
    assert remove_watchlist_ticker(watchlist, TICKER)
    limits = _limits()
    assert (watchlist, TICKER) not in limits
    assert (watchlist, OTHER) in limits and (first, TICKER) in limits


def test_removing_watchlist_items_in_bulk_clears_their_targets(book):
    first, _, watchlist = book
    ids = [item["id"] for item in get_watchlist_items(watchlist) if item["ticker"] == TICKER]
    assert delete_watchlist_items(watchlist, ids) == 1
    limits = _limits()
    assert (watchlist, TICKER) not in limits
    assert (watchlist, OTHER) in limits and (first, TICKER) in limits


def test_removing_a_ticker_that_is_not_watched_changes_nothing(book):
    _, _, watchlist = book
    before = _limits()
    assert remove_watchlist_ticker(watchlist, "ZZNOTWATCHED") is False
    assert _limits() == before


def test_each_cleared_target_is_logged_once_and_an_empty_row_is_silent(book, caplog):
    first, _, _ = book
    upsert_holding_price_limit(first, "ZZTGE", low_limit=None, high_limit=None)
    _buy(first, "ZZTGE")
    with caplog.at_level(logging.INFO, logger="db.account_store"):
        _sell(first)
        _sell(first, "ZZTGE")
    messages = [r.getMessage() for r in caplog.records if "Cleared Position Targets" in r.getMessage()]
    assert len(messages) == 1
    assert TICKER in messages[0] and "low 90.0, high 120.0" in messages[0] and "the position is closed" in messages[0]
    assert (first, "ZZTGE") not in _limits()


def test_the_removal_is_logged_with_its_reason(book, caplog):
    _, _, watchlist = book
    with caplog.at_level(logging.INFO, logger="db.account_store"):
        remove_watchlist_ticker(watchlist, TICKER)
    assert any("the ticker left the Watchlist" in r.getMessage() and "low 80.0, high None" in r.getMessage() for r in caplog.records)


def _orphan_the_book(first, watchlist):
    _sell(first)
    upsert_holding_price_limit(first, TICKER, low_limit=90.0, high_limit=120.0)
    assert remove_watchlist_ticker(watchlist, OTHER)
    upsert_holding_price_limit(watchlist, OTHER, high_limit=5.0)


def test_the_one_time_pass_clears_targets_written_after_a_close_and_after_a_watchlist_removal(book, caplog):
    first, second, watchlist = book
    _orphan_the_book(first, watchlist)
    assert (first, TICKER) in _limits() and (watchlist, OTHER) in _limits()
    with caplog.at_level(logging.INFO, logger="db.account_store"):
        clear_orphaned_position_targets()
    limits = _limits()
    assert (first, TICKER) not in limits and (watchlist, OTHER) not in limits
    assert (first, OTHER) in limits and (second, TICKER) in limits and (watchlist, TICKER) in limits
    assert any("the position is closed" in r.getMessage() for r in caplog.records)
    assert any("the ticker left the Watchlist" in r.getMessage() for r in caplog.records)


def test_the_one_time_pass_clears_targets_of_a_deleted_account(book):
    first, second, _ = book
    soft_delete_account(first)
    upsert_holding_price_limit(first, TICKER, high_limit=130.0)
    clear_orphaned_position_targets()
    assert not [key for key in _limits() if key[0] == first]
    assert (second, TICKER) in _limits()


def test_migrate_db_runs_the_pass_once(book):
    first, _, watchlist = book
    _orphan_the_book(first, watchlist)
    conn = get_connection()
    try:
        conn.execute("DELETE FROM data_migrations WHERE name = 'position_targets_follow_positions'")
        conn.commit()
        db_schema.migrate_db(conn, conn.cursor())
        assert (first, TICKER) not in _limits()
        assert conn.execute("SELECT COUNT(*) FROM data_migrations WHERE name = 'position_targets_follow_positions'").fetchone()[0] == 1
        upsert_holding_price_limit(first, TICKER, low_limit=1.0)
        db_schema.migrate_db(conn, conn.cursor())
        assert (first, TICKER) in _limits()
    finally:
        conn.close()
