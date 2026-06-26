"""Tests for Ghostfolio activity import into a built-in account (accounts_engine.import_ghostfolio_activities)."""
from unittest.mock import MagicMock, patch

import pytest

import accounts_engine
from database import create_account, get_transactions


def _activity(
    act_type: str,
    ticker: str = "AAPL",
    quantity: float = 10.0,
    unit_price: float = 100.0,
    unit_price_native: float = None,
    currency: str = "USD",
    fee: float = 0.0,
    date: str = "2026-01-10",
    act_id: str = "gf-1",
    is_draft: bool = False,
    company_name: str = "Apple Inc.",
) -> dict:
    return {
        "id": act_id,
        "type": act_type,
        "isDraft": is_draft,
        "date": f"{date}T00:00:00.000Z",
        "quantity": quantity,
        "unitPrice": unit_price,
        "unitPriceInAssetProfileCurrency": unit_price_native if unit_price_native is not None else unit_price,
        "currency": "GBP",
        "fee": fee,
        "SymbolProfile": {"symbol": ticker, "name": company_name, "currency": currency},
    }


@pytest.mark.db
def test_map_buy_activity_computes_native_unit_price_and_fx():
    act = _activity("BUY", ticker="ZZAAPL", unit_price=80.0, unit_price_native=100.0, currency="USD")
    mapped = accounts_engine._map_ghostfolio_activity(act)
    assert mapped["txn_type"] == "Buy"
    assert mapped["ticker"] == "ZZAAPL"
    assert mapped["currency"] == "USD"
    assert mapped["unit_price"] == 100.0
    assert mapped["exchange_rate"] == pytest.approx(0.8)
    assert mapped["ghostfolio_ref"] == "gf-1"


@pytest.mark.db
def test_map_skips_draft_and_unsupported_type():
    assert accounts_engine._map_ghostfolio_activity(_activity("BUY", is_draft=True)) is None
    assert accounts_engine._map_ghostfolio_activity(_activity("ITEM")) is None


@pytest.mark.db
def test_map_eur_activity():
    act = _activity("BUY", ticker="ZZSAP", unit_price=85.0, unit_price_native=100.0, currency="EUR")
    mapped = accounts_engine._map_ghostfolio_activity(act)
    assert mapped["currency"] == "EUR"
    assert mapped["exchange_rate"] == pytest.approx(0.85)


@pytest.mark.db
def test_import_buy_and_sell_net_to_zero_lands_in_closed_positions():
    aid = create_account("ImportAcc", "GBP")
    activities = [
        _activity("BUY", ticker="ZZNFLX", quantity=5, unit_price=72.0, unit_price_native=90.0,
                   currency="USD", date="2026-01-05", act_id="gf-buy-1"),
        _activity("SELL", ticker="ZZNFLX", quantity=5, unit_price=80.0, unit_price_native=100.0,
                   currency="USD", date="2026-02-05", act_id="gf-sell-1"),
    ]
    mock_engine = MagicMock(is_configured=True)
    mock_engine.fetch_activities.return_value = activities

    with patch("ghostfolio_sync.GhostfolioSyncEngine", return_value=mock_engine):
        result = accounts_engine.import_ghostfolio_activities(aid)

    assert result == {"imported": 2, "skipped": 0}
    open_holdings, closed, realized = accounts_engine._ledger_for_account(aid)
    assert "ZZNFLX" not in open_holdings
    assert len(closed) == 1
    assert closed[0]["ticker"] == "ZZNFLX"
    assert closed[0]["realized_pnl"] == pytest.approx(realized)
    assert realized > 0


@pytest.mark.db
def test_import_dedups_on_reimport():
    aid = create_account("DedupAcc", "GBP")
    activities = [_activity("DIVIDEND", ticker="ZZKO", quantity=1, unit_price=12.0, currency="USD", act_id="gf-div-1")]
    mock_engine = MagicMock(is_configured=True)
    mock_engine.fetch_activities.return_value = activities

    with patch("ghostfolio_sync.GhostfolioSyncEngine", return_value=mock_engine):
        first = accounts_engine.import_ghostfolio_activities(aid)
        second = accounts_engine.import_ghostfolio_activities(aid)

    assert first == {"imported": 1, "skipped": 0}
    assert second == {"imported": 0, "skipped": 1}
    assert len(get_transactions(aid)) == 1


@pytest.mark.db
def test_import_not_configured_returns_error():
    aid = create_account("UnconfiguredAcc", "GBP")
    mock_engine = MagicMock(is_configured=False)

    with patch("ghostfolio_sync.GhostfolioSyncEngine", return_value=mock_engine):
        result = accounts_engine.import_ghostfolio_activities(aid)

    assert result["imported"] == 0
    assert "error" in result
