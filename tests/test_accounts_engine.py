import pandas as pd
import pytest

import accounts_engine
from database import (
    get_connection,
    create_account,
    add_transaction,
)


def _seed_stock_signal(ticker: str, price: float, currency: str) -> None:
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency) VALUES (?, ?, ?)",
            (ticker, price, currency),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


@pytest.mark.db
def test_average_cost_basis_in_base_currency():
    aid = create_account("AvgCost", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZAPL", company_name="Apple",
                    currency="USD", quantity=10, unit_price=200, exchange_rate=0.80)

    holdings = accounts_engine.derive_account_holdings(aid)
    assert "ZZAPL" in holdings
    h = holdings["ZZAPL"]
    assert h["global_shares"] == 10
    assert h["global_buy_price"] == 160.0          # 200 USD * 0.80 → base
    assert h["accounts"][0]["id"] == f"acct:{aid}"
    assert h["accounts"][0]["total_investment"] == 1600.0


@pytest.mark.db
def test_eur_transaction_converts_to_base():
    aid = create_account("EurAcc", "GBP")
    add_transaction(aid, "Buy", "2026-02-01", ticker="ZZSAP", company_name="SAP",
                    currency="EUR", quantity=5, unit_price=100, exchange_rate=0.85)

    holdings = accounts_engine.derive_account_holdings(aid)
    assert holdings["ZZSAP"]["global_buy_price"] == 85.0      # 100 EUR * 0.85
    assert holdings["ZZSAP"]["accounts"][0]["total_investment"] == 425.0


@pytest.mark.db
def test_gbp_pence_holding():
    aid = create_account("PenceAcc", "GBP")
    add_transaction(aid, "Buy", "2026-01-10", ticker="ZZGBX", company_name="Tesco",
                    currency="GBp", quantity=100, unit_price=250, exchange_rate=0.01,
                    price_in_pence=True)

    h = accounts_engine.derive_account_holdings(aid)["ZZGBX"]
    assert h["price_in_pence"] is True
    assert h["global_buy_price"] == 2.5            # 250 pence * 0.01 → £2.50
    assert h["accounts"][0]["total_investment"] == 250.0


@pytest.mark.db
def test_partial_sell_realizes_pnl_and_reduces_basis():
    aid = create_account("PartialSell", "GBP")
    add_transaction(aid, "Buy", "2026-01-01", ticker="ZZPRT", currency="GBP",
                    quantity=10, unit_price=100, exchange_rate=1.0)
    add_transaction(aid, "Sell", "2026-02-01", ticker="ZZPRT", currency="GBP",
                    quantity=4, unit_price=150, exchange_rate=1.0)

    holdings = accounts_engine.derive_account_holdings(aid)
    assert holdings["ZZPRT"]["global_shares"] == 6
    assert holdings["ZZPRT"]["global_buy_price"] == 100.0         # avg cost unchanged
    assert holdings["ZZPRT"]["accounts"][0]["total_investment"] == 600.0

    closed = accounts_engine.closed_positions(aid)
    row = next(c for c in closed if c["ticker"] == "ZZPRT")
    assert row["sold_qty"] == 4
    assert row["remaining_qty"] == 6
    assert row["realized_pnl"] == 200.0            # 4 * (150 - 100)


@pytest.mark.db
def test_fully_sold_ticker_drops_from_holdings_appears_closed():
    aid = create_account("FullSell", "GBP")
    add_transaction(aid, "Buy", "2026-01-01", ticker="ZZFULL", currency="GBP",
                    quantity=10, unit_price=100, exchange_rate=1.0)
    add_transaction(aid, "Sell", "2026-03-01", ticker="ZZFULL", currency="GBP",
                    quantity=10, unit_price=130, exchange_rate=1.0)

    holdings = accounts_engine.derive_account_holdings(aid)
    assert "ZZFULL" not in holdings

    closed = accounts_engine.closed_positions(aid)
    row = next(c for c in closed if c["ticker"] == "ZZFULL")
    assert row["remaining_qty"] == 0
    assert row["sold_qty"] == 10
    assert row["realized_pnl"] == 300.0            # 10 * (130 - 100)

    assert accounts_engine.account_summary(aid)["realized_pnl"] == 300.0


@pytest.mark.db
def test_cash_balance_across_all_transaction_types():
    aid = create_account("CashAcc", "GBP", initial_cash=1000.0)
    add_transaction(aid, "Cash", "2026-01-01", unit_price=500)                                   # +500
    add_transaction(aid, "Buy", "2026-01-02", ticker="ZZCSH", currency="GBP",
                    quantity=10, unit_price=50, fee=2, exchange_rate=1.0)                          # -502
    add_transaction(aid, "Sell", "2026-01-03", ticker="ZZCSH", currency="GBP",
                    quantity=5, unit_price=60, fee=1, exchange_rate=1.0)                           # +299
    add_transaction(aid, "Dividend", "2026-01-04", ticker="ZZCSH", unit_price=20)                 # +20
    add_transaction(aid, "Interest", "2026-01-05", unit_price=5)                                  # +5
    add_transaction(aid, "Fee", "2026-01-06", unit_price=3)                                       # -3

    assert accounts_engine.cash_balance(aid) == 1319.0


@pytest.mark.db
def test_update_cash_flag_excludes_transaction_from_cash_but_not_holdings():
    aid = create_account("NoCashImpact", "GBP", initial_cash=1000.0)
    add_transaction(aid, "Buy", "2026-01-02", ticker="ZZNCB", currency="GBP",
                    quantity=4, unit_price=25, exchange_rate=1.0, update_cash=False)

    assert accounts_engine.cash_balance(aid) == 1000.0          # cash untouched
    assert accounts_engine.derive_account_holdings(aid)["ZZNCB"]["global_shares"] == 4


@pytest.mark.db
def test_account_summary_equity_dividend_interest():
    _seed_stock_signal("ZZEQT", 180.0, "GBP")
    aid = create_account("SummaryAcc", "GBP")
    add_transaction(aid, "Buy", "2026-01-02", ticker="ZZEQT", currency="GBP",
                    quantity=10, unit_price=160, exchange_rate=1.0)
    add_transaction(aid, "Dividend", "2026-01-03", ticker="ZZEQT", unit_price=25)
    add_transaction(aid, "Interest", "2026-01-04", unit_price=10)

    summary = accounts_engine.account_summary(aid)
    assert summary["equity_value"] == 1800.0       # 10 * 180 GBP
    assert summary["dividend"] == 25.0
    assert summary["interest"] == 10.0
    assert summary["activity_count"] == 3


@pytest.mark.db
def test_get_combined_holdings_sums_ghostfolio_and_builtin(monkeypatch):
    ghost = {
        "merge_co": {
            "ticker": "ZZMRG", "price_in_pence": False,
            "global_shares": 5.0, "global_buy_price": 160.0,
            "accounts": [{"id": "gf-1", "name": "FreeTrade", "shares": 5.0,
                          "buy_price": 160.0, "total_investment": 800.0}],
        },
        "ghost_only": {
            "ticker": "ZZGHO", "price_in_pence": False,
            "global_shares": 2.0, "global_buy_price": 50.0,
            "accounts": [{"id": "gf-1", "name": "FreeTrade", "shares": 2.0,
                          "buy_price": 50.0, "total_investment": 100.0}],
        },
    }
    monkeypatch.setattr(accounts_engine, "_read_portfolio_json", lambda: ghost)

    aid = create_account("MergeAcc", "GBP")
    add_transaction(aid, "Buy", "2026-01-02", ticker="ZZMRG", currency="GBP",
                    quantity=10, unit_price=160, exchange_rate=1.0)
    add_transaction(aid, "Buy", "2026-01-02", ticker="ZZDBO", currency="GBP",
                    quantity=3, unit_price=20, exchange_rate=1.0)

    combined = accounts_engine.get_combined_holdings()

    assert combined["ZZMRG"]["global_shares"] == 15            # 5 ghost + 10 built-in
    assert combined["ZZMRG"]["global_buy_price"] == 160.0      # (800 + 1600) / 15
    assert len(combined["ZZMRG"]["accounts"]) == 2
    assert {a["id"] for a in combined["ZZMRG"]["accounts"]} == {"gf-1", f"acct:{aid}"}
    assert combined["ZZGHO"]["global_shares"] == 2.0           # ghost-only survives
    assert combined["ZZDBO"]["global_shares"] == 3.0           # built-in-only survives


@pytest.mark.db
def test_derive_account_holdings_all_accounts_excludes_non_trading_types():
    trading_aid = create_account("TradingAcc", "GBP", account_type="Trading")
    house_aid = create_account("HouseAcc", "GBP", account_type="House")
    add_transaction(trading_aid, "Buy", "2026-01-02", ticker="ZZTRD", currency="GBP",
                    quantity=4, unit_price=50, exchange_rate=1.0)
    add_transaction(house_aid, "Buy", "2026-01-02", ticker="ZZHSE", currency="GBP",
                    quantity=1, unit_price=300000, exchange_rate=1.0)

    all_holdings = accounts_engine.derive_account_holdings(None)
    assert "ZZTRD" in all_holdings
    assert "ZZHSE" not in all_holdings

    house_only = accounts_engine.derive_account_holdings(house_aid)
    assert "ZZHSE" in house_only


@pytest.mark.db
def test_fx_rate_on_date_base_and_pence_shortcuts():
    assert accounts_engine.BASE_CURRENCY == "GBP"
    assert accounts_engine.fx_rate_on_date("GBP", "2026-01-01") == 1.0
    assert accounts_engine.fx_rate_on_date("", "2026-01-01") == 1.0
    assert accounts_engine.fx_rate_on_date("GBp", "2026-01-01") == 0.01


@pytest.mark.db
def test_fx_rate_on_date_historical_lookup(monkeypatch):
    idx = pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-09"])
    df = pd.DataFrame({"Close": [1.10, 1.12, 1.15]}, index=idx)
    monkeypatch.setattr(
        accounts_engine.yahoo_engine, "get_price_history",
        lambda tickers, period="5y", interval="1d": {"EURGBP=X": df},
    )
    assert accounts_engine.fx_rate_on_date("EUR", "2026-01-06") == 1.12   # last close on/before date


@pytest.mark.db
def test_run_account_value_snapshot_job_runner_is_wired():
    """scheduler_jobs.run_account_value_snapshot must actually call accounts_engine.snapshot_all_accounts —
    a regression guard for the job runner's import wiring, not just the engine function in isolation."""
    import scheduler_jobs
    aid = create_account("JobRunnerWiringAcc", "GBP", initial_cash=42.0)
    scheduler_jobs.run_account_value_snapshot()

    from database import get_value_history
    history = get_value_history(aid)
    assert history, "run_account_value_snapshot did not write a snapshot row"
    assert history[-1]["cash_value"] == 42.0


@pytest.mark.db
def test_run_account_scraper_job_runner_is_wired():
    """scheduler_jobs._run_account_scraper_job must actually call account_scraper_engine.run_scrape_for_account
    (which persists the scraped price) and then accounts_engine.resnapshot_account — a regression guard for
    the runner's import wiring, not just the engine functions in isolation."""
    from unittest.mock import MagicMock, patch
    import scheduler_jobs
    from database import update_account
    aid = create_account("ScraperJobRunnerWiringAcc", "GBP", account_type="House")
    update_account(aid, scraper_url="http://example.test/x.html", scraper_selector="#gf-price")

    resp = MagicMock()
    resp.status_code = 200
    resp.text = '<div id="gf-price">123456.0</div>'
    resp.raise_for_status = MagicMock()
    with patch("requests.get", return_value=resp):
        scheduler_jobs._run_account_scraper_job(aid)

    from database import get_value_history
    history = get_value_history(aid)
    assert history, "_run_account_scraper_job did not trigger a resnapshot"
    assert history[-1]["equity_value"] == 123456.0


@pytest.mark.db
def test_holdings_with_market_value_allocation_sums_to_100():
    _seed_stock_signal("ZZHMV1", 100.0, "GBP")
    _seed_stock_signal("ZZHMV2", 50.0, "GBP")
    aid = create_account("HoldingsValAcc", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZHMV1", currency="GBP",
                     quantity=10, unit_price=80, exchange_rate=1.0)
    add_transaction(aid, "Buy", "2026-01-06", ticker="ZZHMV2", currency="GBP",
                     quantity=4, unit_price=40, exchange_rate=1.0)

    rows = accounts_engine.holdings_with_market_value(aid)
    by_ticker = {r["ticker"]: r for r in rows}
    assert by_ticker["ZZHMV1"]["market_value"] == 1000.0          # 10 * 100
    assert by_ticker["ZZHMV2"]["market_value"] == 200.0            # 4 * 50
    assert by_ticker["ZZHMV1"]["first_activity"] == "2026-01-05"
    assert round(sum(r["allocation_pct"] for r in rows), 1) == 100.0
    assert by_ticker["ZZHMV1"]["performance_pct"] == 25.0          # (1000/800 - 1) * 100
    assert by_ticker["ZZHMV1"]["currency"] == "GBP"


@pytest.mark.db
def test_holdings_with_market_value_empty_account_returns_empty_list():
    aid = create_account("NoHoldingsAcc", "GBP")
    assert accounts_engine.holdings_with_market_value(aid) == []


@pytest.mark.db
def test_holdings_with_market_value_flags_priced_at_cost_when_no_price_data():
    """A holding with no stock_signals row (no Parquet/price fetched yet) must be flagged
    priced_at_cost=True and valued at cost basis — this is the exact ISA/XUKX.L bug scenario."""
    aid = create_account("NoPriceDataAcc", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZNOPRICE", currency="GBP",
                     quantity=10, unit_price=80, exchange_rate=1.0)

    rows = accounts_engine.holdings_with_market_value(aid)
    row = next(r for r in rows if r["ticker"] == "ZZNOPRICE")
    assert row["priced_at_cost"] is True
    assert row["market_value"] == row["total_investment"] == 800.0


@pytest.mark.db
def test_holdings_with_market_value_priced_at_cost_false_when_priced():
    _seed_stock_signal("ZZPRICED", 100.0, "GBP")
    aid = create_account("PricedAcc", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZPRICED", currency="GBP",
                     quantity=10, unit_price=80, exchange_rate=1.0)

    rows = accounts_engine.holdings_with_market_value(aid)
    row = next(r for r in rows if r["ticker"] == "ZZPRICED")
    assert row["priced_at_cost"] is False


@pytest.mark.db
def test_stale_pricing_warning_none_when_all_holdings_priced():
    _seed_stock_signal("ZZALLPRICED", 100.0, "GBP")
    aid = create_account("AllPricedAcc", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZALLPRICED", currency="GBP",
                     quantity=10, unit_price=80, exchange_rate=1.0)

    rows = accounts_engine.holdings_with_market_value(aid)
    assert accounts_engine.stale_pricing_warning(rows) is None


@pytest.mark.db
def test_stale_pricing_warning_names_unpriced_tickers():
    aid = create_account("WarnAcc", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZWARN1", currency="GBP",
                     quantity=10, unit_price=80, exchange_rate=1.0)

    rows = accounts_engine.holdings_with_market_value(aid)
    warning = accounts_engine.stale_pricing_warning(rows)
    assert warning is not None
    assert "ZZWARN1" in warning
    assert "1 holding" in warning


@pytest.mark.db
def test_snapshot_all_accounts_writes_row_per_account():
    _seed_stock_signal("ZZSNAP", 120.0, "GBP")
    aid = create_account("SnapshotAcc", "GBP", initial_cash=500.0)
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZSNAP", currency="GBP",
                     quantity=5, unit_price=100, exchange_rate=1.0)

    accounts_engine.snapshot_all_accounts()

    from database import get_value_history
    history = get_value_history(aid)
    assert history, "expected at least one snapshot row"
    today = history[-1]
    assert today["equity_value"] == 600.0       # 5 * 120
    assert today["cash_value"] == 0.0            # 500 - 500 spent on the buy
    assert today["total_value"] == 600.0


@pytest.mark.db
def test_backfill_value_history_returns_zero_with_no_transactions():
    aid = create_account("BackfillEmptyAcc", "GBP")
    assert accounts_engine.backfill_value_history(aid) == 0


@pytest.mark.db
def test_backfill_value_history_writes_rows_from_parquet(monkeypatch, tmp_path):
    idx = pd.date_range("2025-01-01", "2025-01-10", freq="D")
    df = pd.DataFrame({"Close": [100.0 + i for i in range(len(idx))]}, index=idx)
    df.to_parquet(tmp_path / "ZZBACKFILL.parquet")
    monkeypatch.setattr(accounts_engine, "HISTORICAL_DIR", tmp_path)

    aid = create_account("BackfillAcc", "GBP", initial_cash=1000.0)
    add_transaction(aid, "Buy", "2025-01-03", ticker="ZZBACKFILL", currency="GBP",
                     quantity=2, unit_price=100, exchange_rate=1.0)

    written = accounts_engine.backfill_value_history(aid)
    assert written > 0

    from database import get_value_history
    history = {row["snapshot_date"]: row for row in get_value_history(aid)}
    assert "2025-01-03" in history
    assert history["2025-01-03"]["equity_value"] == 204.0          # 2 * 102 (Close on 2025-01-03)
    assert history["2025-01-03"]["cash_value"] == 800.0            # 1000 - 2*100
    assert "2025-01-02" not in history                             # backfill starts at the first transaction


@pytest.mark.db
def test_cash_history_opening_row_has_no_txn_id():
    aid = create_account("CashHistOpenAcc", "GBP", initial_cash=200.0)
    history = accounts_engine.cash_history(aid)
    assert len(history) == 1
    assert history[0]["txn_id"] is None
    assert history[0]["txn_type"] is None
    assert history[0]["balance"] == 200.0


@pytest.mark.db
def test_cash_history_opening_date_uses_opened_date_when_set():
    aid = create_account("CashHistOpenedDateAcc", "GBP", initial_cash=100.0, opened_date="2018-06-01")
    history = accounts_engine.cash_history(aid)
    assert history[0]["date"] == "2018-06-01"


@pytest.mark.db
def test_cash_history_opening_date_falls_back_to_created_at():
    aid = create_account("CashHistNoOpenedDateAcc", "GBP", initial_cash=100.0)
    acc = accounts_engine.get_account(aid)
    history = accounts_engine.cash_history(aid)
    assert history[0]["date"] == acc["created_at"][:10]


@pytest.mark.db
def test_cash_history_exposes_txn_id_for_editing_and_deleting():
    aid = create_account("CashHistTxnAcc", "GBP", initial_cash=0.0)
    tid = add_transaction(aid, "Cash", "2026-01-05", unit_price=300)
    history = accounts_engine.cash_history(aid)
    assert history[0]["txn_id"] is None              # opening balance row
    assert history[1]["txn_id"] == tid
    assert history[1]["txn_type"] == "Cash"
    assert history[1]["balance"] == 300.0


@pytest.mark.db
def test_cash_history_skips_transactions_with_update_cash_false():
    aid = create_account("CashHistSkipAcc", "GBP", initial_cash=100.0)
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZCHS", currency="GBP",
                     quantity=1, unit_price=50, exchange_rate=1.0, update_cash=False)
    history = accounts_engine.cash_history(aid)
    assert len(history) == 1                          # only the opening row — the Buy never touched cash
    assert history[0]["balance"] == 100.0


@pytest.mark.db
def test_create_transfer_creates_linked_pair_with_correct_cash_effect():
    aid_a = create_account("TransferSrc", "GBP", initial_cash=1000.0)
    aid_b = create_account("TransferDst", "GBP", initial_cash=200.0)

    result = accounts_engine.create_transfer(aid_a, aid_b, 300.0, "2026-01-10", fee=5.0)
    assert "out_txn_id" in result and "in_txn_id" in result

    assert accounts_engine.cash_balance(aid_a) == 1000.0 - 300.0 - 5.0
    assert accounts_engine.cash_balance(aid_b) == 200.0 + 300.0

    from database import get_transaction
    out_txn = get_transaction(result["out_txn_id"])
    in_txn = get_transaction(result["in_txn_id"])
    assert out_txn["txn_type"] == "Transfer"
    assert out_txn["unit_price"] == -300.0
    assert out_txn["linked_txn_id"] == result["in_txn_id"]
    assert in_txn["unit_price"] == 300.0
    assert in_txn["linked_txn_id"] == result["out_txn_id"]


@pytest.mark.db
def test_create_transfer_rejects_same_account():
    aid = create_account("TransferSelfAcc", "GBP")
    result = accounts_engine.create_transfer(aid, aid, 100.0, "2026-01-10")
    assert "error" in result


@pytest.mark.db
def test_create_transfer_rejects_unknown_account():
    aid = create_account("TransferUnknownDstAcc", "GBP")
    result = accounts_engine.create_transfer(aid, 999999, 100.0, "2026-01-10")
    assert "error" in result


@pytest.mark.db
def test_delete_transaction_with_pair_removes_both_legs():
    aid_a = create_account("DeletePairSrc", "GBP", initial_cash=500.0)
    aid_b = create_account("DeletePairDst", "GBP", initial_cash=0.0)
    result = accounts_engine.create_transfer(aid_a, aid_b, 100.0, "2026-01-10")

    from database import get_transaction, get_transactions
    assert accounts_engine.delete_transaction_with_pair(result["out_txn_id"]) is True
    assert get_transaction(result["out_txn_id"]) is None
    assert get_transaction(result["in_txn_id"]) is None
    assert get_transactions(aid_a) == []
    assert get_transactions(aid_b) == []


@pytest.mark.db
def test_delete_transaction_with_pair_on_non_transfer_deletes_only_itself():
    aid = create_account("DeleteSingleAcc", "GBP")
    tid = add_transaction(aid, "Cash", "2026-01-05", unit_price=100)
    assert accounts_engine.delete_transaction_with_pair(tid) is True

    from database import get_transaction
    assert get_transaction(tid) is None


@pytest.mark.db
def test_net_contributions_counts_cash_and_transfer_only():
    aid_a = create_account("ContribSrc", "GBP", initial_cash=1000.0)
    aid_b = create_account("ContribDst", "GBP", initial_cash=0.0)
    add_transaction(aid_a, "Buy", "2026-01-05", ticker="ZZCTRB", currency="GBP",
                     quantity=10, unit_price=50, exchange_rate=1.0)             # cash -500, NOT a contribution
    add_transaction(aid_a, "Cash", "2026-01-06", unit_price=200)                # +200 cash AND contribution
    accounts_engine.create_transfer(aid_a, aid_b, 100.0, "2026-01-07")          # -100 cash AND contribution (src)

    assert accounts_engine.cash_balance(aid_a) == 1000.0 - 500.0 + 200.0 - 100.0
    assert accounts_engine.net_contributions(aid_a) == 1000.0 + 200.0 - 100.0   # Buy excluded
    assert accounts_engine.net_contributions(aid_b) == 0.0 + 100.0


@pytest.mark.db
def test_resnapshot_account_writes_todays_row_with_contributions():
    aid = create_account("ResnapshotAcc", "GBP", initial_cash=500.0)
    add_transaction(aid, "Cash", "2026-01-05", unit_price=300)

    accounts_engine.resnapshot_account(aid)

    from database import get_value_history
    history = get_value_history(aid)
    assert history, "expected at least one snapshot row"
    today = history[-1]
    assert today["cash_value"] == 800.0
    assert today["net_contributions"] == 800.0
    assert today["total_value"] == 800.0


@pytest.mark.db
def test_transaction_total_base_handles_pence_and_cash_rows():
    aid = create_account("TotalBaseAcc", "GBP")
    buy_id = add_transaction(aid, "Buy", "2026-01-05", ticker="ZZTB", currency="GBp",
                              quantity=10, unit_price=250, exchange_rate=0.01, price_in_pence=True)
    cash_id = add_transaction(aid, "Cash", "2026-01-06", unit_price=100)

    from database import get_transaction
    assert accounts_engine.transaction_total_base(get_transaction(buy_id)) == 25.0       # 10 * 250 * 0.01
    assert accounts_engine.transaction_total_base(get_transaction(cash_id)) == 100.0      # qty defaults to 1


@pytest.mark.db
def test_export_transactions_csv_shape_and_position_status():
    import csv
    import io

    aid = create_account("ExportAcc", "GBP")
    add_transaction(aid, "Buy", "2026-01-01", ticker="ZZEXPOPEN", currency="GBP",
                     company_name="OpenCo", isin="GB0000000001",
                     quantity=10, unit_price=100, exchange_rate=1.0)
    add_transaction(aid, "Buy", "2026-01-02", ticker="ZZEXPPARTIAL", currency="GBP",
                     quantity=10, unit_price=50, exchange_rate=1.0)
    add_transaction(aid, "Sell", "2026-01-03", ticker="ZZEXPPARTIAL", currency="GBP",
                     quantity=4, unit_price=70, exchange_rate=1.0)
    add_transaction(aid, "Buy", "2026-01-04", ticker="ZZEXPCLOSED", currency="GBP",
                     quantity=5, unit_price=50, exchange_rate=1.0)
    sell_id = add_transaction(aid, "Sell", "2026-01-05", ticker="ZZEXPCLOSED", currency="GBP",
                               quantity=5, unit_price=60, exchange_rate=1.0)
    add_transaction(aid, "Cash", "2026-01-06", currency="GBP", unit_price=200, notes="Initial deposit")
    add_transaction(aid, "Dividend", "2026-01-07", ticker="ZZEXPOPEN", currency="GBP",
                     quantity=10, unit_price=2, fee=0.5, exchange_rate=1.0)

    csv_text = accounts_engine.export_transactions_csv(aid)
    lines = [l.rstrip("\r") for l in csv_text.strip().split("\n")]
    assert lines[0] == (
        "Title,Type,Timestamp,Account Currency,Total Amount in Account Currency,Buy / Sell,"
        "Ticker,ISIN,Price per Share in Account Currency,Fee,Quantity,Instrument Currency,"
        "Price per Share,Dividend Net Amount,FX Rate,Position,Total Amount in Instrument Currency,"
        "Realized P&L (Account Currency),Notes,Account Name,Transaction ID"
    )

    rows = list(csv.DictReader(io.StringIO(csv_text)))
    by_key = {(r["Ticker"], r["Type"], r["Buy / Sell"]): r for r in rows}

    open_row = by_key[("ZZEXPOPEN", "ORDER", "BUY")]
    assert open_row["Title"] == "OpenCo"
    assert open_row["ISIN"] == "GB0000000001"
    assert open_row["Position"] == ""
    assert open_row["Total Amount in Account Currency"] == "1000.0"

    partial_buy = by_key[("ZZEXPPARTIAL", "ORDER", "BUY")]
    partial_sell = by_key[("ZZEXPPARTIAL", "ORDER", "SELL")]
    assert partial_buy["Position"] == ""      # still holds 6 shares — not fully exited
    assert partial_sell["Position"] == ""

    closed_buy = by_key[("ZZEXPCLOSED", "ORDER", "BUY")]
    closed_sell = by_key[("ZZEXPCLOSED", "ORDER", "SELL")]
    assert closed_buy["Position"] == "closed"
    assert closed_sell["Position"] == "closed"
    assert closed_sell["Realized P&L (Account Currency)"] == "50.0"
    assert closed_sell["Transaction ID"] == str(sell_id)

    cash_row = next(r for r in rows if r["Type"] == "TOP_UP")
    assert cash_row["Title"] == "Initial deposit"    # falls back to notes — no company_name
    assert cash_row["Ticker"] == ""
    assert cash_row["Position"] == ""
    assert cash_row["Total Amount in Account Currency"] == "200.0"

    dividend_row = next(r for r in rows if r["Type"] == "DIVIDEND")
    assert dividend_row["Ticker"] == "ZZEXPOPEN"
    assert dividend_row["Position"] == ""            # ZZEXPOPEN is still held
    assert dividend_row["Dividend Net Amount"] == "19.5"    # 10*2 - 0.5


@pytest.mark.db
def test_house_account_summary_equity_value_from_scraped_price():
    from account_scraper_engine import import_price_csv
    aid = create_account("HouseEquityAcc", "GBP", account_type="House")
    import_price_csv(aid, "date;marketPrice\n2026-02-01;487000\n")

    summary = accounts_engine.account_summary(aid)
    assert summary["equity_value"] == 487000.0


@pytest.mark.db
def test_house_account_with_no_scraped_price_has_zero_equity():
    aid = create_account("HouseNoPriceAcc", "GBP", account_type="House")
    summary = accounts_engine.account_summary(aid)
    assert summary["equity_value"] == 0.0


@pytest.mark.db
def test_snapshot_all_accounts_writes_house_equity_from_scraped_price():
    from account_scraper_engine import import_price_csv
    aid = create_account("HouseSnapAcc", "GBP", account_type="House")
    import_price_csv(aid, "date;marketPrice\n2026-02-01;300000\n")

    accounts_engine.snapshot_all_accounts()

    from database import get_value_history
    history = get_value_history(aid)
    assert history[-1]["equity_value"] == 300000.0
    assert history[-1]["cash_value"] == 0.0


@pytest.mark.db
def test_resnapshot_account_writes_house_equity_from_scraped_price():
    from account_scraper_engine import import_price_csv
    aid = create_account("HouseResnapAcc", "GBP", account_type="House")
    import_price_csv(aid, "date;marketPrice\n2026-02-01;250000\n")

    accounts_engine.resnapshot_account(aid)

    from database import get_value_history
    history = get_value_history(aid)
    assert history[-1]["equity_value"] == 250000.0


@pytest.mark.db
def test_backfill_value_history_for_house_uses_scraped_price_series():
    from account_scraper_engine import import_price_csv
    aid = create_account("HouseBackfillAcc", "GBP", account_type="House")
    import_price_csv(aid, "date;marketPrice\n2026-01-01;400000\n2026-01-05;410000\n")

    written = accounts_engine.backfill_value_history(aid)
    assert written > 0

    from database import get_value_history
    history = {row["snapshot_date"]: row for row in get_value_history(aid)}
    assert history["2026-01-01"]["equity_value"] == 400000.0
    assert history["2026-01-06"]["equity_value"] == 410000.0   # carries forward last known price


@pytest.mark.db
def test_pension_holdings_market_value_uses_scraped_price():
    from account_scraper_engine import import_price_csv, pension_ticker
    aid = create_account("PensionHoldingsAcc", "GBP", account_type="Pension")
    import_price_csv(aid, "date;marketPrice\n2026-01-10;1.60\n")
    add_transaction(aid, "Buy", "2026-01-10", ticker=pension_ticker(aid), currency="GBP",
                     quantity=100, unit_price=1.50, exchange_rate=1.0, update_cash=False)

    rows = accounts_engine.holdings_with_market_value(aid)
    assert len(rows) == 1
    assert rows[0]["market_value"] == 160.0   # 100 units * 1.60 latest scraped price


@pytest.mark.db
def test_pension_buy_with_update_cash_false_does_not_touch_cash_balance():
    from account_scraper_engine import pension_ticker
    aid = create_account("PensionCashAcc", "GBP", account_type="Pension", initial_cash=0.0)
    add_transaction(aid, "Buy", "2026-01-10", ticker=pension_ticker(aid), currency="GBP",
                     quantity=100, unit_price=1.50, exchange_rate=1.0, update_cash=False)
    assert accounts_engine.cash_balance(aid) == 0.0


@pytest.mark.db
def test_record_pension_contribution_computes_units_from_price():
    from account_scraper_engine import import_price_csv
    aid = create_account("PensionPayInAcc", "GBP", account_type="Pension")
    import_price_csv(aid, "date;marketPrice\n2026-01-10;2.00\n")

    result = accounts_engine.record_pension_contribution(aid, "2026-01-10", 200.0)
    assert result["units"] == 100.0
    assert result["unit_price"] == 2.0
    assert accounts_engine.cash_balance(aid) == 0.0   # update_cash=False — never touches cash

    rows = accounts_engine.holdings_with_market_value(aid)
    assert rows[0]["shares"] == 100.0


@pytest.mark.db
def test_record_pension_contribution_rejects_non_pension_account():
    aid = create_account("NotAPensionAcc", "GBP", account_type="House")
    result = accounts_engine.record_pension_contribution(aid, "2026-01-10", 100.0, unit_price=1.0)
    assert "error" in result


@pytest.mark.db
def test_record_pension_contribution_requires_a_resolvable_price():
    aid = create_account("PensionNoPriceAcc", "GBP", account_type="Pension")
    result = accounts_engine.record_pension_contribution(aid, "2026-01-10", 100.0)
    assert "error" in result


@pytest.mark.db
def test_record_pension_fee_computes_units_removed_and_cost():
    aid = create_account("PensionFeeAcc", "GBP", account_type="Pension")
    accounts_engine.record_pension_contribution(aid, "2026-01-01", 1000.0, unit_price=1.00)
    # 1000 units held; portal now shows 995 remaining after the admin fee
    result = accounts_engine.record_pension_fee(aid, "2026-02-01", 995.0, unit_price=1.10)
    assert result["units_removed"] == 5.0
    assert result["fee_cost"] == 5.5   # 5 units * 1.10

    rows = accounts_engine.holdings_with_market_value(aid)
    assert rows[0]["shares"] == 995.0


@pytest.mark.db
def test_record_pension_fee_rejects_units_after_not_lower_than_current():
    aid = create_account("PensionFeeBadAcc", "GBP", account_type="Pension")
    accounts_engine.record_pension_contribution(aid, "2026-01-01", 1000.0, unit_price=1.00)
    result = accounts_engine.record_pension_fee(aid, "2026-02-01", 1000.0, unit_price=1.0)
    assert "error" in result


@pytest.mark.db
def test_record_pension_fee_accepts_units_removed_directly():
    aid = create_account("PensionFeeUnitsRemovedAcc", "GBP", account_type="Pension")
    accounts_engine.record_pension_contribution(aid, "2026-01-01", 1000.0, unit_price=1.00)
    # Provider states 5 units were deducted as the fee, rather than showing the new balance.
    result = accounts_engine.record_pension_fee(aid, "2026-02-01", units_removed=5.0, unit_price=1.10)
    assert result["units_removed"] == 5.0
    assert result["fee_cost"] == 5.5

    rows = accounts_engine.holdings_with_market_value(aid)
    assert rows[0]["shares"] == 995.0


@pytest.mark.db
def test_record_pension_fee_rejects_neither_or_both_units_args():
    aid = create_account("PensionFeeBothArgsAcc", "GBP", account_type="Pension")
    accounts_engine.record_pension_contribution(aid, "2026-01-01", 1000.0, unit_price=1.00)

    result = accounts_engine.record_pension_fee(aid, "2026-02-01", unit_price=1.0)
    assert "error" in result

    result = accounts_engine.record_pension_fee(
        aid, "2026-02-01", units_after=995.0, units_removed=5.0, unit_price=1.0
    )
    assert "error" in result


@pytest.mark.db
def test_record_pension_fee_rejects_units_removed_exceeding_units_held():
    aid = create_account("PensionFeeTooManyUnitsAcc", "GBP", account_type="Pension")
    accounts_engine.record_pension_contribution(aid, "2026-01-01", 1000.0, unit_price=1.00)
    result = accounts_engine.record_pension_fee(aid, "2026-02-01", units_removed=1500.0, unit_price=1.0)
    assert "error" in result


@pytest.mark.db
def test_pension_units_as_of_reflects_ledger_at_date():
    aid = create_account("PensionUnitsAsOfAcc", "GBP", account_type="Pension")
    assert accounts_engine.pension_units_as_of(aid, "2026-01-01") == 0.0
    accounts_engine.record_pension_contribution(aid, "2026-01-01", 1000.0, unit_price=1.00)
    assert accounts_engine.pension_units_as_of(aid, "2026-01-01") == 1000.0
    accounts_engine.record_pension_contribution(aid, "2026-02-01", 500.0, unit_price=1.25)
    assert accounts_engine.pension_units_as_of(aid, "2026-01-15") == 1000.0
    assert accounts_engine.pension_units_as_of(aid, "2026-02-01") == 1400.0


@pytest.mark.db
def test_pension_start_date_round_trips_via_create_and_update():
    aid = create_account("PensionStartDateAcc", "GBP", account_type="Pension", pension_start_date="2015-03-01")
    from database import get_account, update_account
    assert get_account(aid)["pension_start_date"] == "2015-03-01"
    update_account(aid, pension_start_date="2016-04-01")
    assert get_account(aid)["pension_start_date"] == "2016-04-01"


@pytest.mark.db
def test_opening_balance_units_round_trips_via_create_and_update():
    aid = create_account("OpeningBalanceUnitsAcc", "GBP", account_type="Pension", opening_balance_units=3125.5)
    from database import get_account, update_account
    assert get_account(aid)["opening_balance_units"] == 3125.5
    update_account(aid, opening_balance_units=4000.0)
    assert get_account(aid)["opening_balance_units"] == 4000.0


@pytest.mark.db
def test_pension_ticker_label_round_trips_via_create_and_update():
    aid = create_account("PensionLabelAcc", "GBP", account_type="Pension", pension_ticker_label="My Workplace Pension")
    from database import get_account, update_account
    assert get_account(aid)["pension_ticker_label"] == "My Workplace Pension"
    update_account(aid, pension_ticker_label="Renamed Pension")
    assert get_account(aid)["pension_ticker_label"] == "Renamed Pension"


@pytest.mark.db
def test_pension_display_label_falls_back_to_internal_ticker():
    from account_scraper_engine import pension_ticker
    from database import get_account
    aid = create_account("PensionNoLabelAcc", "GBP", account_type="Pension")
    assert accounts_engine.pension_display_label(get_account(aid)) == pension_ticker(aid)

    aid2 = create_account("PensionLabelAcc2", "GBP", account_type="Pension", pension_ticker_label="Friendly Name")
    assert accounts_engine.pension_display_label(get_account(aid2)) == "Friendly Name"


@pytest.mark.db
def test_watchlist_summary_buckets_by_quote_type():
    from database import add_watchlist_item, get_watchlist_account
    wl = get_watchlist_account()
    add_watchlist_item(wl["id"], "AAPL", quote_type="EQUITY")
    add_watchlist_item(wl["id"], "MSFT", quote_type="EQUITY")
    add_watchlist_item(wl["id"], "VUSA.L", quote_type="ETF")
    add_watchlist_item(wl["id"], "FUNDX", quote_type="MUTUALFUND")
    add_watchlist_item(wl["id"], "WEIRD", quote_type="CRYPTOCURRENCY")

    result = accounts_engine.watchlist_summary(wl["id"])
    assert result["count"] == 5
    assert result["by_type"] == {"equity": 2, "etf": 1, "fund": 1, "other": 1}

    for ticker in ("AAPL", "MSFT", "VUSA.L", "FUNDX", "WEIRD"):
        from database import remove_watchlist_ticker
        remove_watchlist_ticker(wl["id"], ticker)


@pytest.mark.db
def test_watchlist_summary_empty():
    from database import get_watchlist_account
    wl = get_watchlist_account()
    result = accounts_engine.watchlist_summary(wl["id"])
    assert result == {"count": 0, "by_type": {"equity": 0, "etf": 0, "fund": 0, "other": 0}}


@pytest.mark.db
def test_account_summary_includes_holdings_count():
    aid = create_account("HoldingsCountAcc", "GBP", account_type="Trading")
    add_transaction(aid, "Buy", "2026-01-01", ticker="AAPL", currency="USD", quantity=10, unit_price=100, exchange_rate=1.0)
    add_transaction(aid, "Buy", "2026-01-02", ticker="MSFT", currency="USD", quantity=5, unit_price=200, exchange_rate=1.0)
    summary = accounts_engine.account_summary(aid)
    assert summary["holdings_count"] == 2


@pytest.mark.db
def test_pension_performance_returns_none_without_enough_history():
    from account_scraper_engine import import_price_csv
    aid = create_account("PensionPerfShortAcc", "GBP", account_type="Pension")
    import_price_csv(aid, "date;marketPrice\n2026-06-27;1.00\n2026-06-28;1.10\n")
    result = accounts_engine.pension_performance(aid)
    # Only 1-2 days of history exist, so there's no price as far back as 1 month/YTD/1 year ago.
    assert result["1m"] is None
    assert result["1y"] is None


@pytest.mark.db
def test_pension_performance_returns_none_with_no_history():
    aid = create_account("PensionPerfEmptyAcc", "GBP", account_type="Pension")
    result = accounts_engine.pension_performance(aid)
    assert result == {"1m": None, "ytd": None, "1y": None}


@pytest.mark.db
def test_pension_performance_computes_pct_change_from_unit_price():
    from account_scraper_engine import import_price_csv
    aid = create_account("PensionPerfAcc", "GBP", account_type="Pension")
    import_price_csv(aid, "date;marketPrice\n2025-06-28;1.00\n2026-06-28;1.50\n")
    result = accounts_engine.pension_performance(aid)
    assert result["1y"] == 50.0   # (1.50 - 1.00) / 1.00 * 100


@pytest.mark.db
def test_pension_activities_running_units_tracks_buys_and_sells():
    aid = create_account("PensionActivitiesAcc", "GBP", account_type="Pension")
    accounts_engine.record_pension_contribution(aid, "2026-01-01", 1000.0, unit_price=1.00)
    accounts_engine.record_pension_fee(aid, "2026-02-01", units_removed=5.0, unit_price=1.10)
    accounts_engine.record_pension_contribution(aid, "2026-03-01", 500.0, unit_price=1.25)

    rows = accounts_engine.pension_activities(aid)
    assert [r["running_units"] for r in rows] == [1000.0, 995.0, 1395.0]


@pytest.mark.db
def test_sync_pension_opening_balance_creates_holding():
    """Regression: opening_balance_units/initial_cash were stored but never materialized into the
    ledger, so units_as_of stayed 0 and Admin Fee could never deduct from a pre-existing balance."""
    from database import get_account, get_transactions, update_account
    aid = create_account("SyncOpeningBalanceAcc", "GBP", account_type="Pension")
    update_account(aid, initial_cash=70000.0, opening_balance_units=70000.0, opened_date="2024-01-01")

    accounts_engine.sync_pension_opening_balance(aid)

    assert accounts_engine.pension_units_as_of(aid, "2024-01-01") == 70000.0
    rows = accounts_engine.holdings_with_market_value(aid)
    assert rows[0]["shares"] == 70000.0
    assert rows[0]["buy_price"] == 1.0   # 70000 / 70000

    acc = get_account(aid)
    txn_id = acc["opening_balance_txn_id"]
    assert txn_id is not None
    txns = get_transactions(aid)
    assert any(t["id"] == txn_id and t["txn_date"] == "2024-01-01" for t in txns)


@pytest.mark.db
def test_sync_pension_opening_balance_updates_in_place_not_duplicated():
    from database import get_account, get_transactions, update_account
    aid = create_account("SyncOpeningBalanceUpdateAcc", "GBP", account_type="Pension")
    update_account(aid, initial_cash=1000.0, opening_balance_units=1000.0, opened_date="2024-01-01")
    accounts_engine.sync_pension_opening_balance(aid)
    first_txn_id = get_account(aid)["opening_balance_txn_id"]

    update_account(aid, initial_cash=2000.0, opening_balance_units=1000.0)
    accounts_engine.sync_pension_opening_balance(aid)

    acc = get_account(aid)
    assert acc["opening_balance_txn_id"] == first_txn_id   # same transaction, updated in place
    assert accounts_engine.pension_units_as_of(aid, "2024-01-01") == 1000.0
    rows = accounts_engine.holdings_with_market_value(aid)
    assert rows[0]["buy_price"] == 2.0   # 2000 / 1000
    assert len([t for t in get_transactions(aid) if t["txn_type"] == "Buy"]) == 1


@pytest.mark.db
def test_sync_pension_opening_balance_removed_when_fields_cleared():
    from database import get_account, update_account
    aid = create_account("SyncOpeningBalanceClearAcc", "GBP", account_type="Pension")
    update_account(aid, initial_cash=1000.0, opening_balance_units=1000.0, opened_date="2024-01-01")
    accounts_engine.sync_pension_opening_balance(aid)
    assert get_account(aid)["opening_balance_txn_id"] is not None

    update_account(aid, opening_balance_units=None)
    accounts_engine.sync_pension_opening_balance(aid)

    assert get_account(aid)["opening_balance_txn_id"] is None
    assert accounts_engine.pension_units_as_of(aid, "2024-01-01") == 0.0


@pytest.mark.db
def test_sync_pension_opening_balance_noop_for_house_account():
    from database import get_account
    aid = create_account("SyncOpeningBalanceHouseAcc", "GBP", account_type="House")
    accounts_engine.sync_pension_opening_balance(aid)
    assert get_account(aid)["opening_balance_txn_id"] is None


@pytest.mark.db
def test_sync_house_purchase_price_seeds_first_price_history_row():
    from database import get_price_history, update_account
    aid = create_account("SyncHousePurchaseAcc", "GBP", account_type="House")
    update_account(aid, initial_cash=300000.0, opened_date="2020-03-15")

    accounts_engine.sync_house_purchase_price(aid)

    history = get_price_history(aid)
    assert history == [{
        "id": history[0]["id"], "account_id": aid, "price_date": "2020-03-15",
        "price": 300000.0, "source": "purchase", "created_at": history[0]["created_at"],
    }]


@pytest.mark.db
def test_sync_house_purchase_price_updates_in_place_not_duplicated():
    from database import get_price_history, update_account
    aid = create_account("SyncHousePurchaseUpdateAcc", "GBP", account_type="House")
    update_account(aid, initial_cash=300000.0, opened_date="2020-03-15")
    accounts_engine.sync_house_purchase_price(aid)

    update_account(aid, initial_cash=325000.0)
    accounts_engine.sync_house_purchase_price(aid)

    history = get_price_history(aid)
    assert len(history) == 1
    assert history[0]["price"] == 325000.0
    assert history[0]["price_date"] == "2020-03-15"


@pytest.mark.db
def test_sync_house_purchase_price_noop_for_pension_account_or_unset_cash():
    from database import get_price_history
    aid = create_account("SyncHousePurchaseNoopAcc", "GBP", account_type="Pension")
    accounts_engine.sync_house_purchase_price(aid)
    assert get_price_history(aid) == []

    aid2 = create_account("SyncHousePurchaseNoCashAcc", "GBP", account_type="House")
    accounts_engine.sync_house_purchase_price(aid2)
    assert get_price_history(aid2) == []
