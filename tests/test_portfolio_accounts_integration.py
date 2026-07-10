"""Portfolio page integration coverage for built-in accounts coexisting with Ghostfolio."""

import json
import re
import time
from unittest.mock import patch

import pytest

from database import get_connection, create_account, add_transaction


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


def _seed_market_pulse(ticker: str, price: float) -> None:
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT OR REPLACE INTO market_pulse_cache (ticker, name, price, change_pts, change_pct, "
            "is_positive, last_updated) VALUES (?, ?, ?, 0, 0, 1, ?)",
            (ticker, ticker, price, time.time()),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def _global_market_value(html: str, ticker: str) -> float:
    """global_market_value is the 5th <td data-sort> cell in the row — see portfolio.html."""
    row = re.search(rf'data-ticker="{ticker}".*?</tr>', html, re.DOTALL)
    assert row, f"no row found for ticker {ticker}"
    cells = re.findall(r'<td data-sort="([^"]*)"', row.group(0))
    assert len(cells) >= 5, f"expected global_market_value as the 5th <td data-sort> cell, row had {cells}"
    return float(cells[4])


@pytest.mark.pages
def test_portfolio_page_shows_builtin_holdings(client):
    aid = create_account("Integ Builtin", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZPGI1", company_name="Integ One",
                     currency="GBP", quantity=4, unit_price=100, exchange_rate=1.0)
    _seed_stock_signal("ZZPGI1", 100.0, "GBP")

    resp = client.get(f"/portfolio?account_id=acct:{aid}")
    assert resp.status_code == 200
    assert 'data-ticker="ZZPGI1"' in resp.text


@pytest.mark.pages
def test_portfolio_page_triggers_background_refresh_for_stale_held_ticker(client):
    """Loading /portfolio must itself trigger a live price refresh for a stale held ticker, the
    same way the Home Assistant-polled JSON endpoints already do (api_routes_accounts.py's
    maybe_trigger_price_refresh) — previously the page only ever showed whatever a background
    scan had last written, so it could sit on yesterday's close until that scan next ran."""
    aid = create_account("Integ PageRefresh", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZPGREFRESH", company_name="Page Refresh Co",
                     currency="GBP", quantity=2, unit_price=50, exchange_rate=1.0)

    with patch("api_routes_accounts.fetch_and_save_pulse") as mock_fetch, \
         patch("accounts_engine.market_pulse.is_exchange_open", return_value=True):
        resp = client.get("/portfolio")
    assert resp.status_code == 200
    mock_fetch.assert_called_once()
    assert "ZZPGREFRESH" in mock_fetch.call_args[0][0]


@pytest.mark.pages
def test_account_dropdown_includes_builtin_accounts(client):
    aid = create_account("Integ Dropdown", "GBP")

    resp = client.get("/portfolio")
    assert resp.status_code == 200
    assert f'value="acct:{aid}"' in resp.text
    assert "Integ Dropdown" in resp.text


@pytest.mark.pages
def test_account_dropdown_excludes_non_trading_account(client):
    aid = create_account("Integ House", "GBP", account_type="House")

    resp = client.get("/portfolio")
    assert resp.status_code == 200
    assert f'value="acct:{aid}"' not in resp.text


@pytest.mark.pages
def test_summary_math_correct_for_builtin_account(client):
    aid = create_account("Integ Summary", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZPGI2", company_name="Integ Two",
                     currency="GBP", quantity=3, unit_price=80, exchange_rate=1.0)
    _seed_stock_signal("ZZPGI2", 80.0, "GBP")

    resp = client.get(f"/portfolio?account_id=acct:{aid}")
    assert resp.status_code == 200
    assert "summary-mv-val" in resp.text
    cost_val = re.search(r'id="summary-cost-val"[^>]*>([^<]*)<', resp.text)
    mv_val = re.search(r'id="summary-mv-val"[^>]*>([^<]*)<', resp.text)
    assert cost_val and "240.00" in cost_val.group(1)
    assert mv_val and "240.00" in mv_val.group(1)


@pytest.mark.pages
def test_same_ticker_coexistence_sums(client, tmp_path, monkeypatch):
    portfolio_json_path = tmp_path / "portfolio.json"
    portfolio_json_path.write_text(json.dumps({
        "ZZCOEX": {
            "ticker": "ZZCOEX", "company_name": "Coex Co", "currency": "GBP",
            "price_in_pence": False,
            "global_shares": 2.0, "global_buy_price": 100.0,
            "accounts": [{"id": "gf:1", "name": "GF Acc", "shares": 2.0,
                          "buy_price": 100.0, "total_investment": 200.0}],
        }
    }))
    monkeypatch.setattr("accounts_engine.PORTFOLIO_PATH", portfolio_json_path)

    aid = create_account("Integ Coex", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZCOEX", company_name="Coex Co",
                     currency="GBP", quantity=3, unit_price=50, exchange_rate=1.0)
    _seed_stock_signal("ZZCOEX", 70.0, "GBP")

    resp = client.get("/portfolio")
    assert resp.status_code == 200
    mv = _global_market_value(resp.text, "ZZCOEX")
    assert mv == pytest.approx(350.0)


@pytest.mark.pages
def test_change_period_defaults_to_1d_and_renders_change_header(client):
    aid = create_account("Integ ChangeDef", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZCHG1", company_name="Change One",
                     currency="GBP", quantity=1, unit_price=100, exchange_rate=1.0)
    _seed_stock_signal("ZZCHG1", 100.0, "GBP")
    _seed_market_pulse("ZZCHG1", 110.0)

    resp = client.get(f"/portfolio?account_id=acct:{aid}")
    assert resp.status_code == 200
    assert 'window.PORTFOLIO_CHANGE_PERIOD = "1d";' in resp.text
    assert "<th>Change</th>" in resp.text
    assert "<th>Daily Change</th>" not in resp.text


@pytest.mark.pages
def test_change_period_invalid_cookie_falls_back_to_1d(client):
    resp = client.get("/portfolio", cookies={"portfolio_change_period": "bogus"})
    assert resp.status_code == 200
    assert 'window.PORTFOLIO_CHANGE_PERIOD = "1d";' in resp.text


@pytest.mark.pages
def test_change_period_cookie_reflects_anchor_close_not_1d(client, monkeypatch):
    aid = create_account("Integ Change6M", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZCHG6M", company_name="Change Six Month",
                     currency="GBP", quantity=1, unit_price=100, exchange_rate=1.0)
    _seed_stock_signal("ZZCHG6M", 100.0, "GBP")
    _seed_market_pulse("ZZCHG6M", 120.0)  # live price used as the numerator for every period

    monkeypatch.setattr(
        "price_history_helpers.get_period_anchor_closes",
        lambda tickers: {t: {"5d": None, "1m": None, "6m": 80.0, "ytd": None, "1y": None} for t in tickers},
    )

    resp = client.get(f"/portfolio?account_id=acct:{aid}", cookies={"portfolio_change_period": "6m"})
    assert resp.status_code == 200
    assert 'window.PORTFOLIO_CHANGE_PERIOD = "6m";' in resp.text
    assert 'data-close6m="80.0"' in resp.text
    row = re.search(r'data-ticker="ZZCHG6M".*?</tr>', resp.text, re.DOTALL).group(0)
    # (120 - 80) / 80 * 100 == 50.00 — must reflect the 6M anchor, not the 1D change_pct (0 seeded above).
    assert "+50.00%" in row


@pytest.mark.pages
def test_ignored_ticker_excluded_from_period_anchor_fetch(client, monkeypatch):
    """A ticker on the Ignored Tickers list must never reach price_history_helpers'
    Yahoo-touching anchor-close lookup, even though it's a genuine held position."""
    from config import load_config as _real_load_config

    aid = create_account("Integ IgnoredAnchor", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZKEEP", company_name="Keep Co",
                     currency="GBP", quantity=1, unit_price=100, exchange_rate=1.0)
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZDROP", company_name="Drop Co",
                     currency="GBP", quantity=1, unit_price=50, exchange_rate=1.0)
    _seed_stock_signal("ZZKEEP", 100.0, "GBP")

    merged_config = {**_real_load_config(), "IGNORED_TICKERS": ["ZZDROP"]}
    monkeypatch.setattr("page_routes.load_config", lambda: merged_config)

    captured = {}
    def _fake_anchor_closes(tickers):
        captured["tickers"] = tickers
        return {t: {"5d": None, "1m": None, "6m": None, "ytd": None, "1y": None} for t in tickers}
    monkeypatch.setattr("price_history_helpers.get_period_anchor_closes", _fake_anchor_closes)

    resp = client.get(f"/portfolio?account_id=acct:{aid}")
    assert resp.status_code == 200
    assert "ZZKEEP" in captured["tickers"]
    assert "ZZDROP" not in captured["tickers"]


@pytest.mark.pages
def test_change_period_missing_history_renders_na(client, monkeypatch):
    aid = create_account("Integ ChangeNA", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZCHGNA", company_name="Change NA",
                     currency="GBP", quantity=1, unit_price=100, exchange_rate=1.0)
    _seed_stock_signal("ZZCHGNA", 100.0, "GBP")
    _seed_market_pulse("ZZCHGNA", 120.0)

    monkeypatch.setattr(
        "price_history_helpers.get_period_anchor_closes",
        lambda tickers: {t: {"5d": None, "1m": None, "6m": None, "ytd": None, "1y": None} for t in tickers},
    )

    resp = client.get(f"/portfolio?account_id=acct:{aid}", cookies={"portfolio_change_period": "1y"})
    assert resp.status_code == 200
    row = re.search(r'data-ticker="ZZCHGNA".*?</tr>', resp.text, re.DOTALL).group(0)
    assert "N/A" in row
    assert 'data-close1y=""' in row


@pytest.mark.pages
def test_stock_detail_position_value_matches_portfolio_page_live_price(client):
    """Regression: stock_detail's 'Your Position' math must use the same live price as the
    Portfolio page (a fresher market_pulse_cache row), not the stale stock_signals.current_price
    — previously it ignored market_pulse_cache entirely, disagreeing with every other page."""
    aid = create_account("Integ LivePx", "GBP")
    add_transaction(aid, "Buy", "2026-01-05", ticker="ZZLIVEPX", company_name="Live Price Co",
                     currency="GBP", quantity=10, unit_price=80, exchange_rate=1.0)
    _seed_stock_signal("ZZLIVEPX", 80.0, "GBP")
    _seed_market_pulse("ZZLIVEPX", 100.0)

    portfolio_resp = client.get(f"/portfolio?account_id=acct:{aid}")
    assert portfolio_resp.status_code == 200
    portfolio_mv = _global_market_value(portfolio_resp.text, "ZZLIVEPX")
    assert portfolio_mv == pytest.approx(1000.0)

    detail_resp = client.get("/stock/ZZLIVEPX")
    assert detail_resp.status_code == 200
    match = re.search(r'Current Value:</span>\s*<strong>([^<]*)</strong>', detail_resp.text)
    assert match, "Current Value not found on stock detail page"
    detail_value = float(match.group(1).replace(",", "").replace("GBP", "").strip())
    assert detail_value == pytest.approx(portfolio_mv)
