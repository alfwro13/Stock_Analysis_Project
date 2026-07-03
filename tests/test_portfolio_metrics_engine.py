import pytest

import accounts_engine
import portfolio_metrics_engine
from database import (
    get_connection,
    create_account,
    add_transaction,
)


def _seed_stock_signal(ticker: str, price: float, currency: str, last_updated: str = None) -> None:
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO stock_signals (ticker, current_price, currency, last_updated) VALUES (?, ?, ?, ?)",
            (ticker, price, currency, last_updated),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def test_holdings_with_metrics_all_accounts_two_accounts_same_ticker_not_merged():
    """Core Phase 3 regression: the same ticker held in two Trading accounts must produce two
    separate rows, never merged into one — the Home Assistant integration surfaces each account
    as its own device, so a merged row would silently lose the per-account distinction."""
    _seed_stock_signal("ZZDUP", 100.0, "GBP")
    aid1 = create_account("DupAcc1", "GBP")
    aid2 = create_account("DupAcc2", "GBP")
    add_transaction(aid1, "Buy", "2026-01-05", ticker="ZZDUP", currency="GBP",
                     quantity=10, unit_price=80, exchange_rate=1.0)
    add_transaction(aid2, "Buy", "2026-01-06", ticker="ZZDUP", currency="GBP",
                     quantity=4, unit_price=90, exchange_rate=1.0)

    result = portfolio_metrics_engine.holdings_with_metrics_all_accounts()
    assert result["base_currency"]
    rows = [r for r in result["holdings"] if r["ticker"] == "ZZDUP"]
    assert len(rows) == 2
    by_account = {r["account_id"]: r for r in rows}
    assert by_account[aid1]["shares"] == 10
    assert by_account[aid2]["shares"] == 4
    assert by_account[aid1]["market_value"] == 1000.0
    assert by_account[aid2]["market_value"] == 400.0


@pytest.mark.db
def test_holdings_with_metrics_all_accounts_zero_trading_accounts_returns_empty_list():
    import database as _db
    for acc in _db.get_accounts():
        if acc["account_type"] == "Trading":
            _db.soft_delete_account(acc["id"])

    result = portfolio_metrics_engine.holdings_with_metrics_all_accounts()
    assert result["holdings"] == []


@pytest.mark.db
def test_holdings_with_metrics_all_accounts_includes_technicals_and_limits():
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            """INSERT OR REPLACE INTO stock_signals
               (ticker, current_price, currency, quote_type, rsi_14, trend_50d, trend_200d, next_earnings_date)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
            ("ZZTECH", 100.0, "GBP", "EQUITY", 55.5, "up", "down", "2026-08-01"),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()

    aid = create_account("TechAcc", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZTECH", currency="GBP",
                     quantity=10, unit_price=80, exchange_rate=1.0)
    portfolio_metrics_engine.set_holding_price_limit(aid, "ZZTECH", low_limit=50.0)

    row = next(r for r in portfolio_metrics_engine.holdings_with_metrics_all_accounts()["holdings"] if r["ticker"] == "ZZTECH")
    assert row["asset_class"] == "EQUITY"
    assert row["rsi"] == 55.5
    assert row["trend_50d"] == "up"
    assert row["trend_200d"] == "down"
    assert row["next_earnings_date"] == "2026-08-01"
    assert row["low_limit"] == 50.0
    assert row["low_limit_set"] is True
    assert row["high_limit_set"] is False
    assert row["gain_value"] == row["market_value"] - row["total_investment"]
    assert row["profit_and_loss"] == row["gain_value"]


@pytest.mark.db
def test_set_holding_price_limit_partial_update_preserves_other_field():
    aid = create_account("LimitPartialAcc", "GBP")
    portfolio_metrics_engine.set_holding_price_limit(aid, "ZZLIMIT", low_limit=10.0)
    portfolio_metrics_engine.set_holding_price_limit(aid, "ZZLIMIT", high_limit=20.0)

    from db_accounts import get_all_holding_price_limits
    limits = get_all_holding_price_limits()[(aid, "ZZLIMIT")]
    assert limits["low_limit"] == 10.0
    assert limits["high_limit"] == 20.0


@pytest.mark.db
def test_portfolio_totals_sums_multiple_trading_accounts():
    _seed_stock_signal("ZZPT1", 100.0, "GBP")
    _seed_stock_signal("ZZPT2", 50.0, "GBP")
    aid1 = create_account("PortTotalsAcc1", "GBP", initial_cash=100.0)
    aid2 = create_account("PortTotalsAcc2", "GBP", initial_cash=50.0)
    add_transaction(aid1, "Buy", "2026-01-05", ticker="ZZPT1", currency="GBP",
                     quantity=10, unit_price=80, exchange_rate=1.0)
    add_transaction(aid2, "Buy", "2026-01-06", ticker="ZZPT2", currency="GBP",
                     quantity=4, unit_price=40, exchange_rate=1.0)

    totals = portfolio_metrics_engine.portfolio_totals()
    assert totals["account_count"] >= 2
    assert totals["base_currency"] == "GBP"
    aid1_total = accounts_engine.total_value(aid1)
    aid2_total = accounts_engine.total_value(aid2)
    assert totals["current_value"] >= round(aid1_total + aid2_total, 2) - 0.01


@pytest.mark.db
def test_portfolio_totals_zero_trading_accounts_returns_all_zero_shape(monkeypatch):
    """Isolated from the shared session DB via a monkeypatched _trading_accounts() — asserting
    on a real "no Trading accounts left" DB state would require soft-deleting every Trading
    account other test files in the same session depend on."""
    monkeypatch.setattr(portfolio_metrics_engine, "_trading_accounts", lambda: [])

    totals = portfolio_metrics_engine.portfolio_totals()
    assert totals["account_count"] == 0
    assert totals["current_value"] == 0.0
    assert totals["total_investment"] == 0.0
    assert totals["portfolio_gain_pct"] is None
    assert totals["portfolio_gain_fx_pct"] is None
    assert totals["unrealized_pnl_pct"] is None
    assert totals["twr_pct"] is None
    assert totals["twr_fx_pct"] is None


@pytest.mark.db
def test_portfolio_totals_only_counts_trading_accounts():
    _seed_stock_signal("ZZPTMIX", 100.0, "GBP")
    trading_aid = create_account("PortTotalsMixTrading", "GBP")
    pension_aid = create_account("PortTotalsMixPension", "GBP", account_type="Pension")
    house_aid = create_account("PortTotalsMixHouse", "GBP", account_type="House")
    add_transaction(trading_aid, "Buy", "2026-01-05", ticker="ZZPTMIX", currency="GBP",
                     quantity=5, unit_price=80, exchange_rate=1.0)

    totals = portfolio_metrics_engine.portfolio_totals()
    trading_ids = {a["id"] for a in accounts_engine.get_accounts() if a["account_type"] == "Trading"}
    assert pension_aid not in trading_ids
    assert house_aid not in trading_ids
    assert trading_aid in trading_ids
    assert totals["account_count"] == len(trading_ids)


@pytest.mark.db
def test_account_metrics_list_fields_map_to_correct_source(monkeypatch):
    """Regression test: account_metrics_list() reads every field from the single
    account_performance_cache snapshot — each of the 12 fields must come from its own correct
    cache column, not a swapped/aliased one (the same bug class portfolio_totals()'s FX-leg
    regression test guards against), and none may fall back to a live-computed value that could
    diverge from the cache (fixed 2026-07-03 — see account_performance_cache realized_pnl/
    dividend_income/interest_income columns)."""
    from database import get_performance_cache

    _seed_stock_signal("ZZACCTMET", 120.0, "GBP")
    aid = create_account("AcctMetricsAcc", "GBP", initial_cash=1000.0)
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZACCTMET", currency="GBP",
                     quantity=10, unit_price=100, exchange_rate=1.0)
    add_transaction(aid, "Dividend", "2026-01-06", ticker="ZZACCTMET", unit_price=15)
    add_transaction(aid, "Interest", "2026-01-07", unit_price=7)

    monkeypatch.setattr(portfolio_metrics_engine, "_trading_accounts", lambda: [accounts_engine.get_account(aid)])

    result = portfolio_metrics_engine.account_metrics_list()
    assert result["base_currency"] == "GBP"
    assert len(result["accounts"]) == 1
    row = result["accounts"][0]
    cached = get_performance_cache(aid)
    summary = accounts_engine.account_summary(aid)

    assert row["account_id"] == aid
    assert row["name"] == "AcctMetricsAcc"
    assert row["cash_balance"] == cached["cash_balance"]
    assert row["equity_value"] == cached["equity_value"]
    assert row["unrealized_pnl"] == cached["unrealized_pnl"]
    assert row["gain_1d"] == cached["return_1d"]
    assert row["gain_1w"] == cached["return_1w"]
    assert row["gain_1m"] == cached["return_1m"]
    assert row["gain_3m"] == cached["return_3m"]
    assert row["gain_1y"] == cached["return_1y"]
    assert row["mwrr_pct"] == cached["mwrr"]
    assert row["realized_pnl"] == cached["realized_pnl"] == summary["realized_pnl"]
    assert row["dividend_income"] == cached["dividend_income"] == summary["dividend"] == 15.0
    assert row["interest_income"] == cached["interest_income"] == summary["interest"] == 7.0


@pytest.mark.db
def test_account_metrics_list_zero_trading_accounts_returns_empty_list(monkeypatch):
    monkeypatch.setattr(portfolio_metrics_engine, "_trading_accounts", lambda: [])

    result = portfolio_metrics_engine.account_metrics_list()
    assert result["accounts"] == []
    assert result["base_currency"] == "GBP"


@pytest.mark.db
def test_account_metrics_list_only_includes_trading_accounts():
    _seed_stock_signal("ZZACCTMIX", 100.0, "GBP")
    trading_aid = create_account("AcctMetricsMixTrading", "GBP")
    pension_aid = create_account("AcctMetricsMixPension", "GBP", account_type="Pension")
    add_transaction(trading_aid, "Buy", "2026-01-05", ticker="ZZACCTMIX", currency="GBP",
                     quantity=5, unit_price=80, exchange_rate=1.0)

    result = portfolio_metrics_engine.account_metrics_list()
    account_ids = {row["account_id"] for row in result["accounts"]}
    assert trading_aid in account_ids
    assert pension_aid not in account_ids


@pytest.mark.db
def test_other_accounts_list_includes_pension_and_house(monkeypatch):
    from account_scraper_engine import import_price_csv

    pension_aid = create_account("OtherAcctsPension", "GBP", account_type="Pension")
    import_price_csv(pension_aid, "date;marketPrice\n2026-06-01;1.50\n")
    house_aid = create_account("OtherAcctsHouse", "GBP", account_type="House")
    import_price_csv(house_aid, "date;marketPrice\n2026-06-01;350000\n")
    trading_aid = create_account("OtherAcctsTrading", "GBP")

    monkeypatch.setattr(
        portfolio_metrics_engine, "_other_accounts",
        lambda: [accounts_engine.get_account(pension_aid), accounts_engine.get_account(house_aid)],
    )

    result = portfolio_metrics_engine.other_accounts_list()
    assert result["base_currency"] == "GBP"
    account_ids = {row["account_id"] for row in result["accounts"]}
    assert account_ids == {pension_aid, house_aid}
    assert trading_aid not in account_ids

    by_id = {row["account_id"]: row for row in result["accounts"]}
    assert by_id[pension_aid]["account_type"] == "Pension"
    assert by_id[pension_aid]["current_value"] == accounts_engine.account_summary(pension_aid)["equity_value"]
    assert by_id[pension_aid]["last_updated"] == "2026-06-01"
    assert by_id[house_aid]["account_type"] == "House"
    assert by_id[house_aid]["current_value"] == 350000.0


@pytest.mark.db
def test_other_accounts_list_house_current_value_excludes_initial_cash():
    """Regression test: current_value must use equity_value, not total_value() — total_value()
    adds cash_balance(), which for House starts from initial_cash (the purchase price memo, not
    real cash) and would double-count it against the scraped valuation."""
    from account_scraper_engine import import_price_csv

    aid = create_account("OtherAcctsHousePhantomCash", "GBP", account_type="House", initial_cash=300000.0)
    import_price_csv(aid, "date;marketPrice\n2026-06-01;350000\n")

    result = portfolio_metrics_engine.other_accounts_list()
    row = next(r for r in result["accounts"] if r["account_id"] == aid)
    assert row["current_value"] == 350000.0


@pytest.mark.db
def test_other_accounts_list_empty_when_no_pension_or_house_accounts(monkeypatch):
    monkeypatch.setattr(portfolio_metrics_engine, "_other_accounts", lambda: [])

    result = portfolio_metrics_engine.other_accounts_list()
    assert result["accounts"] == []
    assert result["base_currency"] == "GBP"


@pytest.mark.db
def test_portfolio_gain_fx_decomposition_base_currency_holding_matches():
    _seed_stock_signal("ZZFXBASE", 120.0, "GBP")
    aid = create_account("FxDecompBaseAcc", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZFXBASE", currency="GBP",
                     quantity=10, unit_price=100, exchange_rate=1.0)

    gain_ex_fx, gain_actual = portfolio_metrics_engine.portfolio_gain_fx_decomposition([aid])
    assert gain_ex_fx == gain_actual == 200.0


@pytest.mark.db
def test_portfolio_gain_fx_decomposition_foreign_currency_rate_moved(monkeypatch):
    _seed_stock_signal("ZZFXMOVE", 100.0, "USD")
    aid = create_account("FxDecompMoveAcc", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZFXMOVE", currency="USD",
                     quantity=10, unit_price=100, exchange_rate=0.80)

    def fake_rate(currency):
        if currency == "USD":
            return 0.90
        return 1.0
    monkeypatch.setattr(portfolio_metrics_engine, "get_rate_to_base", fake_rate)

    gain_ex_fx, gain_actual = portfolio_metrics_engine.portfolio_gain_fx_decomposition([aid])
    # market value at live rate: 10*100*0.90=900, cost basis 10*100*0.80=800 -> gain_actual=100
    assert gain_actual == 100.0
    # market value at avg purchase rate: 10*100*0.80=800, cost basis 800 -> gain_ex_fx=0
    assert gain_ex_fx == 0.0
    assert gain_ex_fx != gain_actual


@pytest.mark.db
def test_portfolio_gain_fx_decomposition_excludes_unpriced_holding():
    aid = create_account("FxDecompUnpricedAcc", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZFXUNPRICED", currency="GBP",
                     quantity=10, unit_price=100, exchange_rate=1.0)

    gain_ex_fx, gain_actual = portfolio_metrics_engine.portfolio_gain_fx_decomposition([aid])
    assert gain_ex_fx == 0.0
    assert gain_actual == 0.0


@pytest.mark.db
def test_portfolio_twr_fx_fewer_than_two_snapshots_returns_none():
    aid = create_account("TwrFxTooFewAcc", "GBP", initial_cash=100.0)
    accounts_engine.resnapshot_account(aid)
    from database import get_connection
    conn = None
    try:
        conn = get_connection()
        conn.execute("DELETE FROM account_value_history WHERE account_id = ? AND snapshot_date != (SELECT MIN(snapshot_date) FROM account_value_history WHERE account_id = ?)", (aid, aid))
        conn.commit()
    finally:
        if conn:
            conn.close()
    assert portfolio_metrics_engine.portfolio_twr_fx([aid]) is None


@pytest.mark.db
def test_portfolio_twr_fx_geometric_linking_hand_computed():
    from database import upsert_value_snapshot
    aid = create_account("TwrFxHandCompAcc", "GBP")
    upsert_value_snapshot(aid, "2026-01-01", 100.0, 100.0, 0.0, 0.0)
    upsert_value_snapshot(aid, "2026-01-02", 110.0, 110.0, 0.0, 0.0)
    upsert_value_snapshot(aid, "2026-01-03", 121.0, 121.0, 0.0, 0.0)

    twr = portfolio_metrics_engine.portfolio_twr_fx([aid])
    assert twr == 21.0


@pytest.mark.db
def test_portfolio_twr_fx_skips_zero_start_subperiod():
    from database import upsert_value_snapshot
    aid = create_account("TwrFxZeroStartAcc", "GBP")
    upsert_value_snapshot(aid, "2026-01-01", 0.0, 0.0, 0.0, 0.0)
    upsert_value_snapshot(aid, "2026-01-02", 100.0, 100.0, 0.0, 100.0)
    upsert_value_snapshot(aid, "2026-01-03", 110.0, 110.0, 0.0, 100.0)

    twr = portfolio_metrics_engine.portfolio_twr_fx([aid])
    assert twr == 10.0


@pytest.mark.db
def test_portfolio_twr_ex_fx_matches_twr_fx_when_only_base_currency():
    from database import upsert_value_snapshot, upsert_value_snapshot_currency
    aid = create_account("TwrExFxBaseOnlyAcc", "GBP")
    upsert_value_snapshot(aid, "2026-01-01", 100.0, 0.0, 100.0, 0.0)
    upsert_value_snapshot(aid, "2026-01-02", 110.0, 0.0, 110.0, 0.0)
    upsert_value_snapshot(aid, "2026-01-03", 121.0, 0.0, 121.0, 0.0)
    upsert_value_snapshot_currency(aid, "2026-01-01", "GBP", 100.0, 100.0, 1.0)
    upsert_value_snapshot_currency(aid, "2026-01-02", "GBP", 110.0, 110.0, 1.0)
    upsert_value_snapshot_currency(aid, "2026-01-03", "GBP", 121.0, 121.0, 1.0)

    twr_fx = portfolio_metrics_engine.portfolio_twr_fx([aid])
    twr_ex_fx = portfolio_metrics_engine.portfolio_twr_ex_fx([aid])
    assert twr_fx == twr_ex_fx == 21.0


@pytest.mark.db
def test_portfolio_twr_ex_fx_differs_when_fx_rate_moves():
    from database import upsert_value_snapshot, upsert_value_snapshot_currency
    aid = create_account("TwrExFxMovesAcc", "GBP")
    # Native equity value constant at 100 units of foreign currency; base value swings purely
    # because fx_rate itself moves from 1.0 -> 2.0 -> the ex-fx series should stay flat (0%),
    # while the fx-actual series shows the full 100% swing baked in by the live rate.
    upsert_value_snapshot(aid, "2026-01-01", 100.0, 0.0, 100.0, 0.0)
    upsert_value_snapshot(aid, "2026-01-02", 200.0, 0.0, 200.0, 0.0)
    upsert_value_snapshot_currency(aid, "2026-01-01", "USD", 100.0, 100.0, 1.0)
    upsert_value_snapshot_currency(aid, "2026-01-02", "USD", 100.0, 200.0, 2.0)

    twr_fx = portfolio_metrics_engine.portfolio_twr_fx([aid])
    twr_ex_fx = portfolio_metrics_engine.portfolio_twr_ex_fx([aid])
    assert twr_fx == 100.0
    assert twr_ex_fx == 0.0
    assert twr_ex_fx != twr_fx


@pytest.mark.db
def test_portfolio_totals_gain_fields_map_to_correct_fx_leg(monkeypatch):
    """Regression test: portfolio_totals() must assign the ex-FX leg to 'portfolio_gain' and the
    actual/FX-inclusive leg to 'portfolio_gain_fx' — these were previously swapped, which silently
    passed every existing test because none of them asserted portfolio_totals()'s field mapping,
    only portfolio_gain_fx_decomposition()'s raw tuple order."""
    _seed_stock_signal("ZZPTFXMAP", 100.0, "USD")
    aid = create_account("PortTotalsFxMapAcc", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZPTFXMAP", currency="USD",
                     quantity=10, unit_price=100, exchange_rate=0.80)

    def fake_rate(currency):
        return 0.90 if currency == "USD" else 1.0
    # portfolio_totals() sources unrealized_pnl from accounts_engine.holdings_with_market_value()
    # and portfolio_gain_fx/portfolio_gain from this module's own portfolio_gain_fx_decomposition()
    # — each holds its own get_rate_to_base binding, so both must be patched for a consistent
    # live rate across the whole response.
    monkeypatch.setattr(portfolio_metrics_engine, "get_rate_to_base", fake_rate)
    monkeypatch.setattr(accounts_engine, "get_rate_to_base", fake_rate)
    monkeypatch.setattr(portfolio_metrics_engine, "_trading_accounts", lambda: [accounts_engine.get_account(aid)])

    totals = portfolio_metrics_engine.portfolio_totals()
    # actual (live rate 0.90): 10*100*0.90=900 - 800 = 100; ex-fx (purchase rate 0.80): 800-800=0
    assert totals["portfolio_gain"] == 0.0
    assert totals["portfolio_gain_fx"] == 100.0
    assert totals["unrealized_pnl"] == totals["portfolio_gain_fx"]


@pytest.mark.db
def test_portfolio_totals_twr_fields_map_to_correct_fx_leg(monkeypatch):
    """Regression test: 'twr_pct' must be the FX-neutral (ex-fx) chain-linked return and
    'twr_fx_pct' the FX-actual one — these were previously swapped in the same way as the gain
    fields, only detectable by asserting portfolio_totals()'s output against the two underlying
    functions directly rather than trusting the dict-literal assignment order."""
    from database import upsert_value_snapshot, upsert_value_snapshot_currency
    aid = create_account("PortTotalsTwrMapAcc", "GBP")
    upsert_value_snapshot(aid, "2026-01-01", 100.0, 0.0, 100.0, 0.0)
    upsert_value_snapshot(aid, "2026-01-02", 200.0, 0.0, 200.0, 0.0)
    upsert_value_snapshot_currency(aid, "2026-01-01", "USD", 100.0, 100.0, 1.0)
    upsert_value_snapshot_currency(aid, "2026-01-02", "USD", 100.0, 200.0, 2.0)
    monkeypatch.setattr(portfolio_metrics_engine, "_trading_accounts", lambda: [accounts_engine.get_account(aid)])

    totals = portfolio_metrics_engine.portfolio_totals()
    assert totals["twr_pct"] == 0.0      # ex-fx: native value unchanged -> 0% local return
    assert totals["twr_fx_pct"] == 100.0  # actual: fx_rate doubled -> 100% base-currency return


@pytest.mark.db
def test_portfolio_twr_ex_fx_falls_back_to_actual_equity_when_currency_row_missing():
    """Regression test for a real bug found via manual smoke-testing: when a date has an
    account_value_history row but NO matching account_value_history_currency row (e.g. a ticker's
    price history doesn't reach that far back, or the one-time historical backfill hasn't been run
    for a pre-existing account), the missing date must fall back to that date's actual equity_value
    rather than silently treating uncovered equity as 0 — the latter fabricates a near-total-loss
    sub-period that permanently collapses the whole chain-linked product to -100%."""
    from database import upsert_value_snapshot, upsert_value_snapshot_currency
    aid = create_account("TwrExFxMissingCurrencyRowAcc", "GBP")
    upsert_value_snapshot(aid, "2026-01-01", 100.0, 0.0, 100.0, 0.0)
    upsert_value_snapshot(aid, "2026-01-02", 110.0, 0.0, 110.0, 0.0)  # no currency-breakdown row for this date
    upsert_value_snapshot(aid, "2026-01-03", 121.0, 0.0, 121.0, 0.0)
    upsert_value_snapshot_currency(aid, "2026-01-01", "GBP", 100.0, 100.0, 1.0)
    upsert_value_snapshot_currency(aid, "2026-01-03", "GBP", 121.0, 121.0, 1.0)

    twr_ex_fx = portfolio_metrics_engine.portfolio_twr_ex_fx([aid])
    assert twr_ex_fx is not None
    assert twr_ex_fx > -50.0  # must not collapse toward -100% just because one date lacks coverage
    assert twr_ex_fx == 21.0  # falls back to the real equity_value series, matching the fully-covered case
