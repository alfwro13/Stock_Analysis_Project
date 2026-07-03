"""Portfolio page integration coverage for built-in accounts coexisting with Ghostfolio."""

import json
import re
import time

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
