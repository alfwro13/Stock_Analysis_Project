import pytest

import treasury_bill_engine as tbe
from database import create_account, get_account, get_connection


@pytest.mark.db
def test_buy_treasury_bill_creates_unique_ticker_and_spends_cash():
    aid = create_account("TBillAcc", "GBP", initial_cash=2000.0)
    result = tbe.buy_treasury_bill(aid, "2026-07-01", 1000.0, 996.16, "2026-07-29")
    assert "bill_id" in result
    assert result["ticker"] == f"TBILL-{result['txn_id']}"

    acc = get_account(aid)
    from accounts_engine import cash_balance
    assert cash_balance(aid) == pytest.approx(2000.0 - 996.16)


@pytest.mark.db
def test_buy_treasury_bill_rejects_non_trading_account():
    aid = create_account("TBillHouseAcc", "GBP", account_type="House")
    result = tbe.buy_treasury_bill(aid, "2026-07-01", 1000.0, 996.16, "2026-07-29")
    assert "error" in result


@pytest.mark.db
def test_buy_treasury_bill_rejects_purchase_price_above_face_value():
    aid = create_account("TBillBadPriceAcc", "GBP", initial_cash=2000.0)
    result = tbe.buy_treasury_bill(aid, "2026-07-01", 1000.0, 1000.0, "2026-07-29")
    assert "error" in result


@pytest.mark.db
def test_buy_treasury_bill_rejects_maturity_before_purchase():
    aid = create_account("TBillBadDateAcc", "GBP", initial_cash=2000.0)
    result = tbe.buy_treasury_bill(aid, "2026-07-29", 1000.0, 996.16, "2026-07-01")
    assert "error" in result


def test_accreted_price_at_purchase_mid_and_maturity():
    bill = {"purchase_price": 996.16, "face_value": 1000.0,
            "purchase_date": "2026-07-01", "maturity_date": "2026-07-29"}
    assert tbe.accreted_price(bill, "2026-07-01") == pytest.approx(996.16)
    assert tbe.accreted_price(bill, "2026-07-29") == pytest.approx(1000.0)
    assert tbe.accreted_price(bill, "2026-07-15") == pytest.approx(998.079, abs=0.01)


def test_accreted_price_clamps_past_maturity():
    bill = {"purchase_price": 996.16, "face_value": 1000.0,
            "purchase_date": "2026-07-01", "maturity_date": "2026-07-29"}
    assert tbe.accreted_price(bill, "2026-09-01") == pytest.approx(1000.0)


def test_estimate_face_value_matches_hand_computed_example():
    # £500 paid, 3.72% indicative YTM, 28-day window (09/07 -> 06/08) -> ~£501.43 estimated redemption.
    est = tbe.estimate_face_value(500.0, 3.72, "2026-07-09", "2026-08-06")
    assert est == pytest.approx(500.0 * (1 + 0.0372 * 28 / 365))
    assert est == pytest.approx(501.4268, abs=0.001)


def test_estimate_face_value_zero_or_negative_window_returns_purchase_price():
    assert tbe.estimate_face_value(500.0, 3.72, "2026-07-09", "2026-07-09") == 500.0
    assert tbe.estimate_face_value(500.0, 3.72, "2026-07-09", "2026-07-01") == 500.0


@pytest.mark.db
def test_buy_treasury_bill_stores_indicative_ytm_for_later_display():
    aid = create_account("TBillYTMAcc", "GBP", initial_cash=2000.0)
    result = tbe.buy_treasury_bill(aid, "2026-07-09", 501.43, 500.0, "2026-08-06", indicative_ytm=3.72)
    bill = tbe.get_treasury_bill(result["bill_id"])
    assert bill["indicative_ytm"] == pytest.approx(3.72)


@pytest.mark.db
def test_buy_treasury_bill_indicative_ytm_is_optional():
    aid = create_account("TBillNoYTMAcc", "GBP", initial_cash=2000.0)
    result = tbe.buy_treasury_bill(aid, "2026-07-09", 501.43, 500.0, "2026-08-06")
    bill = tbe.get_treasury_bill(result["bill_id"])
    assert bill["indicative_ytm"] is None


@pytest.mark.db
def test_two_concurrent_bills_do_not_blend_cost_basis():
    """Regression guard: concurrently-held bills must get different tickers so
    _ledger_for_account never averages their discount prices together."""
    aid = create_account("TBillConcurrentAcc", "GBP", initial_cash=5000.0)
    r1 = tbe.buy_treasury_bill(aid, "2026-07-01", 1000.0, 996.16, "2026-07-29")
    r2 = tbe.buy_treasury_bill(aid, "2026-07-08", 2000.0, 1990.00, "2026-08-05")
    assert r1["ticker"] != r2["ticker"]

    from accounts_engine import holdings_with_market_value
    holdings = {h["ticker"]: h for h in holdings_with_market_value(aid)}
    assert holdings[r1["ticker"]]["buy_price"] == pytest.approx(996.16)
    assert holdings[r2["ticker"]]["buy_price"] == pytest.approx(1990.00)


@pytest.mark.db
def test_current_price_map_dispatches_tbill_ticker_to_accretion():
    from accounts_engine import current_price_map
    aid = create_account("TBillPriceMapAcc", "GBP", initial_cash=2000.0)
    result = tbe.buy_treasury_bill(aid, "2026-07-01", 1000.0, 996.16, "2026-07-29")
    priced = current_price_map([result["ticker"]])
    assert result["ticker"] in priced
    price, currency = priced[result["ticker"]]
    assert 996.16 <= price <= 1000.0
    assert currency == "GBP"


@pytest.mark.db
def test_sweep_matured_bills_closes_position_and_credits_cash():
    aid = create_account("TBillSweepAcc", "GBP", initial_cash=2000.0)
    result = tbe.buy_treasury_bill(aid, "2026-05-01", 1000.0, 996.16, "2026-05-29")

    tbe.sweep_matured_bills()

    from accounts_engine import cash_balance, holdings_with_market_value
    assert cash_balance(aid) == pytest.approx(2000.0 - 996.16 + 1000.0)
    open_tickers = {h["ticker"] for h in holdings_with_market_value(aid)}
    assert result["ticker"] not in open_tickers

    bill = tbe.get_treasury_bill(result["bill_id"])
    assert bill["status"] == "Matured"
    assert bill["maturity_txn_id"] is not None


@pytest.mark.db
def test_sweep_matured_bills_is_idempotent():
    aid = create_account("TBillSweepIdempotentAcc", "GBP", initial_cash=2000.0)
    result = tbe.buy_treasury_bill(aid, "2026-05-01", 1000.0, 996.16, "2026-05-29")
    tbe.sweep_matured_bills()
    bill_after_first_sweep = tbe.get_treasury_bill(result["bill_id"])
    tbe.sweep_matured_bills()
    bill_after_second_sweep = tbe.get_treasury_bill(result["bill_id"])
    # A second sweep must not post a duplicate maturity transaction for an already-matured bill.
    assert bill_after_first_sweep["maturity_txn_id"] == bill_after_second_sweep["maturity_txn_id"]


@pytest.mark.db
def test_sweep_matured_bills_fires_reminder_notification_when_auto_reinvest():
    from unittest.mock import patch
    aid = create_account("TBillReminderAcc", "GBP", initial_cash=2000.0)
    tbe.buy_treasury_bill(aid, "2026-05-01", 1000.0, 996.16, "2026-05-29", auto_reinvest=True)

    with patch("treasury_bill_engine.notify") as mock_notify:
        tbe.sweep_matured_bills()
    mock_notify.assert_called_once()
    args, _kwargs = mock_notify.call_args
    assert args[0] == "treasury_bill_reminder"


@pytest.mark.db
def test_sweep_matured_bills_no_reminder_without_auto_reinvest():
    from unittest.mock import patch
    aid = create_account("TBillNoReminderAcc", "GBP", initial_cash=2000.0)
    result = tbe.buy_treasury_bill(aid, "2026-05-01", 1000.0, 996.16, "2026-05-29", auto_reinvest=False)

    with patch("treasury_bill_engine.notify") as mock_notify:
        tbe.sweep_matured_bills()
    assert all(call.args[0] != "treasury_bill_reminder" for call in mock_notify.call_args_list)
    bill = tbe.get_treasury_bill(result["bill_id"])
    assert bill["status"] == "Matured"


@pytest.mark.db
def test_run_treasury_bill_maturity_sweep_job_runner_is_wired():
    """scheduler_jobs.run_treasury_bill_maturity_sweep must actually call
    treasury_bill_engine.sweep_matured_bills — a regression guard for the job runner's
    import wiring, not just the engine function in isolation."""
    import scheduler_jobs
    aid = create_account("TBillJobRunnerAcc", "GBP", initial_cash=2000.0)
    tbe.buy_treasury_bill(aid, "2026-05-01", 1000.0, 996.16, "2026-05-29")

    scheduler_jobs.run_treasury_bill_maturity_sweep()

    from accounts_engine import cash_balance
    assert cash_balance(aid) == pytest.approx(2000.0 - 996.16 + 1000.0)

    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT last_run FROM scheduler_run_log WHERE job_id = 'treasury_bill_maturity_sweep_job'"
        ).fetchone()
    finally:
        conn.close()
    assert row is not None and row["last_run"] is not None


@pytest.mark.db
def test_delete_treasury_bill_removes_bill_and_linked_transactions():
    aid = create_account("TBillDeleteAcc", "GBP", initial_cash=2000.0)
    result = tbe.buy_treasury_bill(aid, "2026-07-01", 1000.0, 996.16, "2026-07-29")

    outcome = tbe.delete_treasury_bill(result["bill_id"])
    assert outcome.get("deleted") is True
    assert tbe.get_treasury_bill(result["bill_id"]) is None

    from database import get_transaction
    assert get_transaction(result["txn_id"]) is None


@pytest.mark.db
def test_delete_treasury_bill_removes_maturity_leg_too():
    aid = create_account("TBillDeleteMaturedAcc", "GBP", initial_cash=2000.0)
    result = tbe.buy_treasury_bill(aid, "2026-05-01", 1000.0, 996.16, "2026-05-29")
    tbe.sweep_matured_bills()
    bill = tbe.get_treasury_bill(result["bill_id"])
    maturity_txn_id = bill["maturity_txn_id"]

    tbe.delete_treasury_bill(result["bill_id"])

    from database import get_transaction
    assert get_transaction(maturity_txn_id) is None
