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
def test_map_buy_activity_uses_fx_engine_not_ghostfolio_ratio(monkeypatch):
    """exchange_rate must come from accounts_engine.fx_rate_on_date (our own trusted, independently
    tested FX engine) — not from Ghostfolio's unitPrice/unitPriceInAssetProfileCurrency ratio, which
    silently assumed the source Ghostfolio account itself was denominated in BASE_CURRENCY."""
    monkeypatch.setattr(accounts_engine, "fx_rate_on_date", lambda currency, date: 0.79)
    act = _activity("BUY", ticker="ZZAAPL", unit_price=80.0, unit_price_native=100.0, currency="USD")
    mapped = accounts_engine._map_ghostfolio_activity(act)
    assert mapped["txn_type"] == "Buy"
    assert mapped["ticker"] == "ZZAAPL"
    assert mapped["currency"] == "USD"
    assert mapped["unit_price"] == 100.0
    assert mapped["exchange_rate"] == 0.79
    assert mapped["update_cash"] is True
    assert mapped["ghostfolio_ref"] == "gf-1"


@pytest.mark.db
def test_map_usd_denominated_ghostfolio_account_still_converts_to_base(monkeypatch):
    """Regression for the reported bug: when the source Ghostfolio account is itself USD-denominated,
    Ghostfolio reports unitPrice == unitPriceInAssetProfileCurrency (both USD, e.g. AMD). The old
    ratio-based exchange_rate collapsed to 1.0 in that case, leaving cost basis in USD while the
    Portfolio/Holdings page treats it as already-converted base currency — corrupting buy price,
    market value comparison, and performance %. The fix must apply a real FX rate regardless."""
    monkeypatch.setattr(accounts_engine, "fx_rate_on_date", lambda currency, date: 0.79)
    act = _activity("BUY", ticker="ZZAMD", unit_price=436.19, unit_price_native=436.19, currency="USD")
    mapped = accounts_engine._map_ghostfolio_activity(act)
    assert mapped["unit_price"] == 436.19
    assert mapped["exchange_rate"] == 0.79


@pytest.mark.db
def test_map_fee_converted_via_ghostfolio_price_ratio():
    """Fee is reported by Ghostfolio in the same (account-side) currency as unitPrice, so it is
    converted to native currency using Ghostfolio's own price ratio for this activity — independent
    of the exchange_rate (which now comes from our own FX engine, not this ratio)."""
    act = _activity("BUY", ticker="ZZFEE", unit_price=8.0, unit_price_native=10.0, currency="USD", fee=0.8)
    mapped = accounts_engine._map_ghostfolio_activity(act)
    assert mapped["fee"] == pytest.approx(1.0)  # native_to_acct_rate = 8/10 = 0.8 -> 0.8/0.8 = 1.0


@pytest.mark.db
def test_map_prefers_cached_asset_profile_currency_over_ghostfolio(monkeypatch):
    """Regression: an LSE pence (GBp) stock must use the app's own cached asset_profiles currency,
    not Ghostfolio's self-reported SymbolProfile.currency — Ghostfolio has been observed to report
    plain 'GBP' for pence stocks, which would silently drop the /100 pence conversion and corrupt the
    cost basis 100x (the reported NatWest Group realized P&L bug)."""
    monkeypatch.setattr(accounts_engine, "_cached_ticker_currency", lambda ticker: "GBp")
    monkeypatch.setattr(accounts_engine, "fx_rate_on_date", lambda currency, date: 0.01 if currency == "GBp" else 1.0)
    act = _activity("BUY", ticker="ZZNWG", unit_price=2.50, unit_price_native=2.50, currency="GBP")
    mapped = accounts_engine._map_ghostfolio_activity(act)
    assert mapped["currency"] == "GBp"
    assert mapped["price_in_pence"] is True
    assert mapped["exchange_rate"] == 0.01


@pytest.mark.db
def test_map_skips_draft_and_unsupported_type():
    assert accounts_engine._map_ghostfolio_activity(_activity("BUY", is_draft=True)) is None
    assert accounts_engine._map_ghostfolio_activity(_activity("ITEM")) is None


@pytest.mark.db
def test_map_eur_activity(monkeypatch):
    monkeypatch.setattr(accounts_engine, "fx_rate_on_date", lambda currency, date: 0.85)
    act = _activity("BUY", ticker="ZZSAP", unit_price=85.0, unit_price_native=100.0, currency="EUR")
    mapped = accounts_engine._map_ghostfolio_activity(act)
    assert mapped["currency"] == "EUR"
    assert mapped["exchange_rate"] == 0.85


@pytest.mark.db
def test_import_buy_and_sell_net_to_zero_lands_in_closed_positions(monkeypatch):
    monkeypatch.setattr(accounts_engine, "fx_rate_on_date", lambda currency, date: 0.79)
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
        result = accounts_engine.import_ghostfolio_activities(aid, "gf-acc-1")

    mock_engine.fetch_activities.assert_called_once_with(account_id="gf-acc-1")
    assert result == {"imported": 2, "skipped": 0}
    open_holdings, closed, realized = accounts_engine._ledger_for_account(aid)
    assert "ZZNFLX" not in open_holdings
    assert len(closed) == 1
    assert closed[0]["ticker"] == "ZZNFLX"
    assert closed[0]["realized_pnl"] == pytest.approx(realized)
    assert realized == pytest.approx(5 * (100.0 - 90.0) * 0.79)


@pytest.mark.db
def test_import_affects_cash_balance_like_any_other_transaction(monkeypatch):
    """Imported Buy/Sell/Dividend/Interest/Fee rows move cash the same way a manually-entered
    transaction would (update_cash=True universally). This is only accurate when the operator also
    records their real deposit/withdrawal history via Cash/Transfer — verified here purely as
    ledger math, independent of that assumption."""
    monkeypatch.setattr(accounts_engine, "fx_rate_on_date", lambda currency, date: 0.79)
    aid = create_account("CashAffectedAcc", "GBP", initial_cash=500.0)
    activities = [
        _activity("BUY", ticker="ZZCSH2", quantity=10, unit_price=72.0, unit_price_native=90.0,
                   currency="USD", date="2026-01-05", act_id="gf-buy-cash"),
        _activity("DIVIDEND", ticker="ZZCSH2", quantity=1, unit_price=12.0, currency="USD", act_id="gf-div-cash"),
    ]
    mock_engine = MagicMock(is_configured=True)
    mock_engine.fetch_activities.return_value = activities

    with patch("ghostfolio_sync.GhostfolioSyncEngine", return_value=mock_engine):
        accounts_engine.import_ghostfolio_activities(aid, "gf-acc-1")

    # 500 - (10 * 90 * 0.79) [Buy] + (1 * 12 * 0.79) [Dividend]
    assert accounts_engine.cash_balance(aid) == pytest.approx(500.0 - 711.0 + 9.48)


@pytest.mark.db
def test_import_dedups_on_reimport(monkeypatch):
    monkeypatch.setattr(accounts_engine, "fx_rate_on_date", lambda currency, date: 0.79)
    aid = create_account("DedupAcc", "GBP")
    activities = [_activity("DIVIDEND", ticker="ZZKO", quantity=1, unit_price=12.0, currency="USD", act_id="gf-div-1")]
    mock_engine = MagicMock(is_configured=True)
    mock_engine.fetch_activities.return_value = activities

    with patch("ghostfolio_sync.GhostfolioSyncEngine", return_value=mock_engine):
        first = accounts_engine.import_ghostfolio_activities(aid, "gf-acc-1")
        second = accounts_engine.import_ghostfolio_activities(aid, "gf-acc-1")

    assert first == {"imported": 1, "skipped": 0}
    assert second == {"imported": 0, "skipped": 1}
    assert len(get_transactions(aid)) == 1


@pytest.mark.db
def test_import_not_configured_returns_error():
    aid = create_account("UnconfiguredAcc", "GBP")
    mock_engine = MagicMock(is_configured=False)

    with patch("ghostfolio_sync.GhostfolioSyncEngine", return_value=mock_engine):
        result = accounts_engine.import_ghostfolio_activities(aid, "gf-acc-1")

    assert result["imported"] == 0
    assert "error" in result


@pytest.mark.db
def test_import_only_pulls_selected_ghostfolio_account():
    """Regression: importing must filter to the chosen Ghostfolio account, not every account the
    user has on Ghostfolio. fetch_activities() is the only call site, so passing account_id through
    to it is what fixes the bug — verified here at the engine boundary."""
    aid = create_account("ScopedImportAcc", "GBP")
    mock_engine = MagicMock(is_configured=True)
    mock_engine.fetch_activities.return_value = []

    with patch("ghostfolio_sync.GhostfolioSyncEngine", return_value=mock_engine):
        accounts_engine.import_ghostfolio_activities(aid, "gf-acc-42")

    mock_engine.fetch_activities.assert_called_once_with(account_id="gf-acc-42")
