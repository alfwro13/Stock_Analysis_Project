"""Tests for GIA/broker-CSV activity import into a built-in account (accounts_engine.import_csv_activities)."""
from unittest.mock import patch

import pytest

import accounts_engine
from accounts_engine import cash_balance
from database import create_account


_HEADER = (
    "Title,Type,Timestamp,Account Currency,Total Amount in Account Currency,Buy / Sell,Ticker,ISIN,"
    "Price per Share in Account Currency,Stamp Duty,Quantity,Instrument Currency,"
    "Total Amount in Instrument Currency,Price per Share,FX Rate,Base FX Rate,FX Fee (BPS),FX Fee Amount,"
    "Dividend Ex Date,Dividend Pay Date,Dividend Eligible Quantity,Dividend Amount Per Share,"
    "Dividend Gross Distribution Amount,Dividend Net Distribution Amount,"
    "Dividend Withheld Tax Percentage,Dividend Withheld Tax Amount\n"
)


def _csv(*rows: str) -> str:
    return _HEADER + "".join(row + "\n" for row in rows)


def _row(d: dict) -> dict:
    base = {h: "" for h in _HEADER.strip().split(",")}
    base.update(d)
    return base


@pytest.mark.db
def test_map_gbp_buy_exchange_rate_is_one():
    row = _row({
        "Title": "FirstGroup", "Type": "ORDER", "Timestamp": "01/02/2021", "Account Currency": "GBP",
        "Total Amount in Account Currency": "4.41", "Buy / Sell": "BUY", "Ticker": "FGP.L",
        "Price per Share in Account Currency": "0.731667", "Stamp Duty": "0.02", "Quantity": "6",
        "Instrument Currency": "GBP", "Price per Share": "0.731667",
    })
    mapped, reason, _ = accounts_engine._map_csv_row(row)
    assert reason is None
    assert mapped["txn_type"] == "Buy"
    assert mapped["ticker"] == "FGP.L"
    assert mapped["exchange_rate"] == pytest.approx(1.0)
    assert mapped["fee"] == pytest.approx(0.02)


@pytest.mark.db
def test_map_captures_isin_for_order_and_dividend_blank_for_cash():
    order_row = _row({
        "Title": "FirstGroup", "Type": "ORDER", "Timestamp": "01/02/2021", "Account Currency": "GBP",
        "Total Amount in Account Currency": "4.41", "Buy / Sell": "BUY", "Ticker": "FGP.L",
        "ISIN": "GB0003452173", "Price per Share in Account Currency": "0.731667", "Stamp Duty": "0.02",
        "Quantity": "6", "Instrument Currency": "GBP", "Price per Share": "0.731667",
    })
    mapped, _, _ = accounts_engine._map_csv_row(order_row)
    assert mapped["isin"] == "GB0003452173"

    dividend_row = _row({
        "Title": "GSK.L", "Type": "DIVIDEND", "Timestamp": "13/04/2023", "Account Currency": "GBP",
        "Total Amount in Account Currency": "0.13", "Ticker": "GSK.L", "ISIN": "GB00BN7SWP63",
        "Instrument Currency": "GBP", "Dividend Eligible Quantity": "1", "Dividend Amount Per Share": "0.1375",
        "Dividend Net Distribution Amount": "0.1375", "Dividend Withheld Tax Amount": "0.00",
    })
    mapped, _, _ = accounts_engine._map_csv_row(dividend_row)
    assert mapped["isin"] == "GB00BN7SWP63"

    top_up_row = _row({"Title": "Top up", "Type": "TOP_UP", "Timestamp": "01/02/2021",
                        "Account Currency": "GBP", "Total Amount in Account Currency": "50.00"})
    mapped, _, _ = accounts_engine._map_csv_row(top_up_row)
    assert "isin" not in mapped


@pytest.mark.db
def test_map_fx_buy_derives_rate_from_dual_currency_prices_and_converts_fee():
    """SPCE buy from the sample file: Price per Share in Account Currency / Price per Share gives the
    mid-market GBP rate; Stamp Duty + FX Fee Amount (both in GBP) must be divided by that rate to land
    back in native (USD) currency, since add_transaction()/_cash_delta() multiplies fee by exchange_rate."""
    row = _row({
        "Title": "Virgin Galactic", "Type": "ORDER", "Timestamp": "01/02/2021", "Account Currency": "GBP",
        "Total Amount in Account Currency": "38.94", "Buy / Sell": "BUY", "Ticker": "SPCE",
        "Price per Share in Account Currency": "764.27896", "Stamp Duty": "0.00", "Quantity": "0.05072755",
        "Instrument Currency": "USD", "Price per Share": "1044.60", "FX Fee Amount": "0.17",
    })
    mapped, reason, _ = accounts_engine._map_csv_row(row)
    assert reason is None
    assert mapped["currency"] == "USD"
    assert mapped["exchange_rate"] == pytest.approx(764.27896 / 1044.60, rel=1e-6)
    assert mapped["fee"] == pytest.approx(0.17 / (764.27896 / 1044.60), rel=1e-4)


@pytest.mark.db
def test_map_buy_overrides_to_pence_when_cache_disagrees_with_csv_currency():
    """Regression for a reported bug: Rolls-Royce (RR.L) is reported by the broker as a GBP trade
    (price already divided by 100), but Yahoo/asset_profiles quotes RR.L in GBp pence — the only
    convention that matters for later market-value lookups. Without this override, the historical
    value chart (backfill_value_history) treats Yahoo's pence price as pounds and inflates the
    chart ~100x, while the cash/cost-basis math (which only cares about qty*price*fx) must stay
    numerically identical before and after the override."""
    row = _row({
        "Title": "Rolls-Royce", "Type": "ORDER", "Timestamp": "02/04/2024", "Account Currency": "GBP",
        "Total Amount in Account Currency": "21.04", "Buy / Sell": "BUY", "Ticker": "RR.L",
        "Price per Share in Account Currency": "4.188", "Stamp Duty": "0.10", "Quantity": "5",
        "Instrument Currency": "GBP", "Price per Share": "4.188",
    })
    with patch.object(accounts_engine, "_cached_ticker_currency", return_value="GBp"):
        mapped, reason, _ = accounts_engine._map_csv_row(row)
    assert reason is None
    assert mapped["currency"] == "GBp"
    assert mapped["price_in_pence"] is True
    assert mapped["unit_price"] == pytest.approx(418.8)
    assert mapped["fee"] == pytest.approx(10.0)
    assert mapped["exchange_rate"] == pytest.approx(0.01)
    assert mapped["quantity"] == pytest.approx(5.0)

    gross_base = mapped["quantity"] * mapped["unit_price"] * mapped["exchange_rate"]
    fee_base = mapped["fee"] * mapped["exchange_rate"]
    assert gross_base == pytest.approx(5 * 4.188 * 1.0)
    assert fee_base == pytest.approx(0.10)


@pytest.mark.db
def test_map_dividend_overrides_to_pence_when_cache_disagrees_with_csv_currency():
    row = _row({
        "Title": "Greencoat UK", "Type": "DIVIDEND", "Timestamp": "27/05/2022", "Account Currency": "GBP",
        "Total Amount in Account Currency": "0.15", "Ticker": "UKW.L", "Instrument Currency": "GBP",
        "Dividend Eligible Quantity": "8", "Dividend Amount Per Share": "0.0193",
        "Dividend Net Distribution Amount": "0.15", "Dividend Withheld Tax Amount": "0.00",
    })
    with patch.object(accounts_engine, "_cached_ticker_currency", return_value="GBp"):
        mapped, reason, _ = accounts_engine._map_csv_row(row)
    assert reason is None
    assert mapped["currency"] == "GBp"
    assert mapped["price_in_pence"] is True
    assert mapped["unit_price"] == pytest.approx(1.93)
    assert mapped["exchange_rate"] == pytest.approx(0.01)
    gross_base = mapped["quantity"] * mapped["unit_price"] * mapped["exchange_rate"]
    assert gross_base == pytest.approx(8 * 0.0193 * 1.0)


@pytest.mark.db
def test_map_sell_uses_sell_type():
    row = _row({
        "Title": "Petrofac", "Type": "ORDER", "Timestamp": "19/04/2021", "Account Currency": "GBP",
        "Total Amount in Account Currency": "2.58", "Buy / Sell": "SELL", "Ticker": "PFC.L",
        "Price per Share in Account Currency": "1.29", "Quantity": "2", "Instrument Currency": "GBP",
        "Price per Share": "1.29",
    })
    mapped, reason, _ = accounts_engine._map_csv_row(row)
    assert reason is None
    assert mapped["txn_type"] == "Sell"


@pytest.mark.db
def test_map_dividend_derives_rate_from_total_and_net_and_keeps_tax_native():
    """AAPL dividend from the sample file: exchange_rate = Total Amount in Account Currency / Dividend
    Net Distribution Amount, so cash_delta (qty*unit_price*fx - fee*fx) reproduces the broker's own
    reported GBP net amount, and Withheld Tax Amount is passed through unconverted (already USD)."""
    row = _row({
        "Title": "Apple", "Type": "DIVIDEND", "Timestamp": "11/02/2021", "Account Currency": "GBP",
        "Total Amount in Account Currency": "0.04", "Ticker": "AAPL", "Instrument Currency": "USD",
        "Dividend Eligible Quantity": "0.282854", "Dividend Amount Per Share": "0.205",
        "Dividend Net Distribution Amount": "0.05", "Dividend Withheld Tax Amount": "0.01",
    })
    mapped, reason, _ = accounts_engine._map_csv_row(row)
    assert reason is None
    assert mapped["txn_type"] == "Dividend"
    assert mapped["exchange_rate"] == pytest.approx(0.04 / 0.05)
    assert mapped["fee"] == pytest.approx(0.01)
    assert mapped["quantity"] == pytest.approx(0.282854)
    assert mapped["unit_price"] == pytest.approx(0.205)


@pytest.mark.db
def test_map_gbp_dividend_exchange_rate_is_one():
    row = _row({
        "Title": "GSK.L", "Type": "DIVIDEND", "Timestamp": "13/04/2023", "Account Currency": "GBP",
        "Total Amount in Account Currency": "0.13", "Ticker": "GSK.L", "Instrument Currency": "GBP",
        "Dividend Eligible Quantity": "1", "Dividend Amount Per Share": "0.1375",
        "Dividend Net Distribution Amount": "0.1375", "Dividend Withheld Tax Amount": "0.00",
    })
    mapped, reason, _ = accounts_engine._map_csv_row(row)
    assert reason is None
    assert mapped["exchange_rate"] == pytest.approx(1.0)


@pytest.mark.db
def test_map_top_up_and_interest():
    top_up = _row({"Title": "Top up", "Type": "TOP_UP", "Timestamp": "01/02/2021",
                    "Account Currency": "GBP", "Total Amount in Account Currency": "50.00"})
    mapped, reason, _ = accounts_engine._map_csv_row(top_up)
    assert reason is None
    assert mapped["txn_type"] == "Cash"
    assert mapped["unit_price"] == pytest.approx(50.0)
    assert mapped["exchange_rate"] == pytest.approx(1.0)

    interest = _row({"Title": "Interest", "Type": "INTEREST_FROM_CASH", "Timestamp": "15/05/2024",
                      "Account Currency": "GBP", "Total Amount in Account Currency": "0.01"})
    mapped, reason, _ = accounts_engine._map_csv_row(interest)
    assert reason is None
    assert mapped["txn_type"] == "Interest"


@pytest.mark.db
def test_map_internal_transfer_is_ignored_blank_row_and_unknown_type():
    transfer = _row({"Title": "Internal Transfer to ISA", "Type": "INTERNAL_TRANSFER", "Timestamp": "16/02/2026",
                      "Account Currency": "GBP"})
    mapped, reason, _ = accounts_engine._map_csv_row(transfer)
    assert mapped is None and reason == "ignored"

    blank = _row({"Type": ""})
    mapped, reason, _ = accounts_engine._map_csv_row(blank)
    assert mapped is None and reason == "blank_row"

    unknown = _row({"Title": "X", "Type": "WEIRD_TYPE", "Timestamp": "01/01/2025", "Account Currency": "GBP"})
    mapped, reason, _ = accounts_engine._map_csv_row(unknown)
    assert mapped is None and reason == "unknown_type"


@pytest.mark.db
def test_map_dividend_with_blank_ticker_is_skipped():
    row = _row({"Title": "Apple", "Type": "DIVIDEND", "Timestamp": "13/05/2021", "Account Currency": "GBP",
                "Total Amount in Account Currency": "0.05", "Ticker": ""})
    mapped, reason, key = accounts_engine._map_csv_row(row)
    assert mapped is None
    assert reason == "no_ticker"
    assert key == "(no ticker)"


@pytest.mark.db
def test_import_rejects_csv_missing_required_column():
    aid = create_account("MissingColAcc", "GBP")
    bad_csv = "Title,Type,Timestamp\nTop up,TOP_UP,01/02/2021\n"
    result = accounts_engine.import_csv_activities(aid, bad_csv)
    assert "error" in result
    assert "missing required column" in result["error"].lower()


@pytest.mark.db
def test_import_end_to_end_with_duplicate_top_ups_and_a_trade():
    aid = create_account("CsvImportAcc", "GBP")
    csv_text = _csv(
        "Top up,TOP_UP,01/02/2021,GBP,50.00,,,,,,,,,,,,,,,,,,,,,",
        "Top up,TOP_UP,01/02/2021,GBP,50.00,,,,,,,,,,,,,,,,,,,,,",
        "Top up,TOP_UP,01/02/2021,GBP,50.00,,,,,,,,,,,,,,,,,,,,,",
        "FirstGroup,ORDER,01/02/2021,GBP,4.41,BUY,FGP.L,GB0003452173,0.731667,0.02,6,GBP,4.39,0.731667,,,,,,,,,,,,",
    )
    with patch.object(accounts_engine, "_ticker_known", return_value=True):
        result = accounts_engine.import_csv_activities(aid, csv_text)

    assert result["imported"] == 4
    assert result["skipped"] == 0
    assert result["ignored"] == 0
    assert result["skipped_rows"] == []
    assert cash_balance(aid) == pytest.approx(150.0 - 4.41)


@pytest.mark.db
def test_import_reimport_same_file_is_idempotent():
    aid = create_account("ReimportAcc", "GBP")
    csv_text = _csv(
        "Top up,TOP_UP,01/02/2021,GBP,50.00,,,,,,,,,,,,,,,,,,,,,",
        "Top up,TOP_UP,01/02/2021,GBP,50.00,,,,,,,,,,,,,,,,,,,,,",
    )
    with patch.object(accounts_engine, "_ticker_known", return_value=True):
        first = accounts_engine.import_csv_activities(aid, csv_text)
        second = accounts_engine.import_csv_activities(aid, csv_text)

    assert first["imported"] == 2
    assert second["imported"] == 0
    assert second["skipped"] == 2
    assert cash_balance(aid) == pytest.approx(100.0)


@pytest.mark.db
def test_ticker_resolvable_retries_once_after_transient_lookup_failure():
    """Regression for a reported false-negative: a real, resolvable mutual fund ticker
    (0P0001RI3X.L) was skipped during import. `get_ticker_info` swallows every exception
    including a transient HTTP 429, so a one-off network blip looks identical to a genuinely
    bad ticker without a retry."""
    with patch.object(accounts_engine, "_ticker_known", return_value=False), \
         patch.object(accounts_engine.yahoo_engine, "get_ticker_info", side_effect=[None, {"longName": "HL Fund"}]) as mock_info:
        assert accounts_engine._ticker_resolvable("0P0001RI3X.L") is True
    assert mock_info.call_count == 2


@pytest.mark.db
def test_ticker_resolvable_returns_false_after_two_consecutive_failures():
    with patch.object(accounts_engine, "_ticker_known", return_value=False), \
         patch.object(accounts_engine.yahoo_engine, "get_ticker_info", return_value=None) as mock_info:
        assert accounts_engine._ticker_resolvable("ZZZNOPE") is False
    assert mock_info.call_count == 2


@pytest.mark.db
def test_import_recovers_ticker_after_one_transient_lookup_failure():
    aid = create_account("TransientFailAcc", "GBP")
    csv_text = _csv(
        "HL Fund,ORDER,01/02/2021,GBP,10.00,BUY,0P0001RI3X.L,GB00B4NXY349,5.00,0.00,2,GBP,10.00,5.00,,,,,,,,,,,,",
    )
    with patch.object(accounts_engine, "_ticker_known", return_value=False), \
         patch.object(accounts_engine.yahoo_engine, "get_ticker_info", side_effect=[None, {"longName": "HL Fund"}]):
        result = accounts_engine.import_csv_activities(aid, csv_text)

    assert result["imported"] == 1
    assert result["skipped"] == 0
    assert result["skipped_rows"] == []


@pytest.mark.db
def test_import_skips_and_reports_unresolved_ticker_with_date():
    aid = create_account("UnresolvedTickerAcc", "GBP")
    csv_text = _csv(
        "Delisted Co,ORDER,01/02/2021,GBP,10.00,BUY,ZZZNOPE,XX0000000000,5.00,0.00,2,GBP,10.00,5.00,,,,,,,,,,,,",
    )
    with patch.object(accounts_engine, "_ticker_known", return_value=False), \
         patch.object(accounts_engine.yahoo_engine, "get_ticker_info", return_value=None):
        result = accounts_engine.import_csv_activities(aid, csv_text)

    assert result["imported"] == 0
    assert result["skipped"] == 1
    assert result["skipped_rows"] == [{
        "date": "2021-02-01", "ticker": "ZZZNOPE",
        "reason": "ticker not found (possibly delisted or mistyped)",
    }]


@pytest.mark.db
def test_map_unparseable_timestamp_is_skipped_not_crashed():
    """A truncated/malformed row (e.g. a hand-edited export) must be skipped, not raise and abort
    the whole import — `Timestamp` is a required column but a single row's value can still be
    missing/garbled without the rest of the file being lost."""
    row = _row({"Title": "X", "Type": "ORDER", "Timestamp": "not-a-date", "Account Currency": "GBP"})
    mapped, reason, _ = accounts_engine._map_csv_row(row)
    assert mapped is None
    assert reason == "bad_date"


@pytest.mark.db
def test_import_counts_internal_transfer_as_ignored():
    aid = create_account("IgnoredTransferAcc", "GBP")
    csv_text = _csv(
        "Internal Transfer to ISA,INTERNAL_TRANSFER,16/02/2026,GBP,94.39,,,,,,,,,,,,,,,,,,,,,",
    )
    result = accounts_engine.import_csv_activities(aid, csv_text)
    assert result == {"imported": 0, "skipped": 0, "ignored": 1, "skipped_rows": []}
