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
         patch("accounts_engine.market_session_helpers.is_exchange_open", return_value=True):
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
    from config import load_config
    monkeypatch.setattr("accounts_engine.load_config", lambda: {**load_config(), "GHOSTFOLIO_ENABLED": True})

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
    assert '<th data-col-key="change">Change</th>' in resp.text
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
        lambda tickers, **kwargs: {t: {"5d": None, "1m": None, "6m": 80.0, "ytd": None, "1y": None} for t in tickers},
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
    monkeypatch.setattr("page_routes_portfolio.load_config", lambda: merged_config)

    captured = {}
    def _fake_anchor_closes(tickers, **kwargs):
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
        lambda tickers, **kwargs: {t: {"5d": None, "1m": None, "6m": None, "ytd": None, "1y": None} for t in tickers},
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


def test_portfolio_signal_query_scopes_enrichment_and_keeps_global_freshness():
    from page_data_signal_rows import fetch_portfolio_signal_rows

    held = "ZZSTEP2HELD"
    unrelated = [f"ZZSTEP2UNRELATED{i:04d}" for i in range(1200)]
    conn = get_connection()
    try:
        conn.execute(
            "INSERT INTO stock_signals (ticker, last_updated, company_name, currency, current_price) "
            "VALUES (?, ?, ?, ?, ?)",
            (held, "2026-10-01 10:00:00", "Signal Name", "USD", 100.0),
        )
        conn.executemany(
            "INSERT INTO stock_signals (ticker, last_updated, currency) VALUES (?, ?, ?)",
            [(ticker, "9999-01-01 00:00:00", "JPY") for ticker in unrelated],
        )
        conn.execute(
            "INSERT INTO company_name_overrides (ticker, display_name) VALUES (?, ?)",
            (held, "Preferred Name"),
        )
        conn.executemany(
            "INSERT INTO quant_signals (ticker, date, ml_confidence_score, var_95, atr_pct) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (held, "2026-09-29", 0.62, 4.0, 1.0),
                (held, "2026-09-30", None, 5.0, 2.0),
            ],
        )
        conn.commit()

        rows, macro, updated = fetch_portfolio_signal_rows("SPY", [*(f"ZZSTEP2MISSING{i:04d}" for i in range(900)), held])
        assert [row["ticker"] for row in rows] == [held]
        assert rows[0]["resolved_company_name"] == "Preferred Name"
        assert rows[0]["ml_confidence_score"] == pytest.approx(0.62)
        assert rows[0]["var_95"] == pytest.approx(5.0)
        assert rows[0]["atr_pct"] == pytest.approx(2.0)
        assert updated == "9999-01-01 00:00:00"
        assert macro is None or isinstance(macro, dict)

        empty_rows, _, empty_updated = fetch_portfolio_signal_rows("SPY", [])
        assert empty_rows == []
        assert empty_updated == updated
    finally:
        conn.execute("DELETE FROM quant_signals WHERE ticker = ?", (held,))
        conn.execute("DELETE FROM company_name_overrides WHERE ticker = ?", (held,))
        conn.execute("DELETE FROM stock_signals WHERE ticker = ? OR ticker LIKE 'ZZSTEP2UNRELATED%'", (held,))
        conn.commit()
        conn.close()


@pytest.mark.pages
def test_portfolio_scope_limits_sql_and_fx_to_displayed_holdings(client, tmp_path, monkeypatch):
    import accounts_engine
    import page_routes_portfolio
    from config import load_config

    first = create_account("Step2 First", "GBP")
    second = create_account("Step2 Second", "GBP")
    holdings = [
        (first, "ZZS2USD", "USD", 100.0, 2.0),
        (first, "ZZS2GBPENCE", "GBp", 500.0, 3.0),
        (first, "ZZS2IGNORED", "JPY", 300.0, 1.0),
        (first, "ZZS2MISSING", "GBP", None, 1.0),
        (second, "ZZS2EUR", "EUR", 50.0, 4.0),
    ]
    for aid, ticker, currency, price, quantity in holdings:
        add_transaction(aid, "Buy", "2026-01-05", ticker=ticker, company_name=ticker,
                        currency="GBP", quantity=quantity, unit_price=10, exchange_rate=1.0)
        if price is not None:
            _seed_stock_signal(ticker, price, currency)
    _seed_stock_signal("ZZS2UNRELATED", 100.0, "ZZZ")
    _seed_stock_signal("ZZS2GHOST", 40.0, "GBP")

    portfolio_path = tmp_path / "portfolio.json"
    portfolio_path.write_text(json.dumps({
        "ZZS2USD": {
            "ticker": "ZZS2USD", "currency": "USD", "global_shares": 1.0,
            "global_buy_price": 10.0,
            "accounts": [{"id": "gf:step2", "name": "Ghostfolio Step2", "shares": 1.0,
                          "buy_price": 10.0, "total_investment": 10.0}],
        },
        "ZZS2GHOST": {
            "ticker": "ZZS2GHOST", "currency": "GBP", "global_shares": 1.0,
            "global_buy_price": 10.0,
            "accounts": [{"id": "gf:step2", "name": "Ghostfolio Step2", "shares": 1.0,
                          "buy_price": 10.0, "total_investment": 10.0}],
        },
    }))
    config = {**load_config(), "GHOSTFOLIO_ENABLED": True, "IGNORED_TICKERS": ["ZZS2IGNORED"]}
    monkeypatch.setattr(accounts_engine, "PORTFOLIO_PATH", portfolio_path)
    monkeypatch.setattr(accounts_engine, "load_config", lambda: config)
    monkeypatch.setattr(page_routes_portfolio, "load_config", lambda: config)
    monkeypatch.setattr("price_history_helpers.get_period_anchor_closes", lambda tickers, **kwargs: {})

    rates = {"GBP": 1.0, "GBp": 0.01, "USD": 0.8, "EUR": 0.9}
    original_fetch = page_routes_portfolio.fetch_portfolio_signal_rows
    with patch("page_routes_portfolio.fetch_portfolio_signal_rows", wraps=original_fetch) as fetch, \
         patch("page_helpers.get_rate_to_base", side_effect=lambda currency, **kwargs: rates.get(currency, 1.0)) as fx, \
         patch("page_data_portfolio.get_rate_to_base", return_value=1.0) as valuation_fx:
        all_response = client.get("/portfolio")
        assert all_response.status_code == 200
        assert {"ZZS2USD", "ZZS2GBPENCE", "ZZS2EUR", "ZZS2GHOST", "ZZS2MISSING"} <= set(fetch.call_args.args[1])
        assert "ZZS2IGNORED" not in fetch.call_args.args[1]
        assert "ZZS2UNRELATED" not in fetch.call_args.args[1]
        for ticker in ("ZZS2USD", "ZZS2GBPENCE", "ZZS2EUR", "ZZS2GHOST"):
            assert f'data-ticker="{ticker}"' in all_response.text
        for ticker in ("ZZS2IGNORED", "ZZS2MISSING", "ZZS2UNRELATED"):
            assert f'data-ticker="{ticker}"' not in all_response.text
        assert _global_market_value(all_response.text, "ZZS2USD") == pytest.approx(240.0)
        assert _global_market_value(all_response.text, "ZZS2GBPENCE") == pytest.approx(15.0)
        all_fx_currencies = [call.args[0] for call in fx.call_args_list]
        assert {"EUR", "GBP", "GBp", "USD"} <= set(all_fx_currencies)
        assert "ZZZ" not in all_fx_currencies
        assert len(all_fx_currencies) == len(set(all_fx_currencies))

        fx.reset_mock()
        valuation_fx.reset_mock()
        selected = client.get(f"/portfolio?account_id=acct:{first}")
        assert selected.status_code == 200
        assert set(fetch.call_args.args[1]) == {"ZZS2USD", "ZZS2GBPENCE", "ZZS2MISSING"}
        assert _global_market_value(selected.text, "ZZS2USD") == pytest.approx(160.0)
        assert 'data-ticker="ZZS2EUR"' not in selected.text
        assert 'data-ticker="ZZS2GHOST"' not in selected.text
        assert sorted(call.args[0] for call in fx.call_args_list) == ["GBP", "GBp", "USD"]
        assert not valuation_fx.called

        fx.reset_mock()
        valuation_fx.reset_mock()
        ghost = client.get("/portfolio?account_id=gf:step2")
        assert ghost.status_code == 200
        assert set(fetch.call_args.args[1]) == {"ZZS2USD", "ZZS2GHOST"}
        assert _global_market_value(ghost.text, "ZZS2USD") == pytest.approx(80.0)
        assert sorted(call.args[0] for call in fx.call_args_list) == ["GBP", "USD"]
        assert not valuation_fx.called

        fx.reset_mock()
        empty = client.get("/portfolio?account_id=acct:999999")
        assert empty.status_code == 200
        assert fetch.call_args.args[1] == []
        assert sorted(call.args[0] for call in fx.call_args_list) == ["GBP"]


@pytest.mark.pages
def test_cached_navigation_completes_while_upstream_is_blocked(client, tmp_path, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor
    import cache_refresh_helpers as refresh_helpers
    import data_engine
    from db_helpers import upsert_fx_quote
    from yahoo_engine import yahoo_engine

    ticker = "ZZSTEP3CACHED"
    _seed_stock_signal(ticker, 100.0, "USD")
    conn = None
    try:
        conn = get_connection()
        conn.execute("DELETE FROM market_pulse_cache WHERE ticker IN ('USDGBP=X', 'GBPUSD=X')")
        conn.commit()
    finally:
        if conn:
            conn.close()
    upsert_fx_quote("USDGBP=X", 0.8, time.time() - 3600)
    holdings = {ticker: {"ticker": ticker, "global_shares": 2.0, "global_buy_price": 50.0, "accounts": []}}
    monkeypatch.setattr("accounts_engine.get_combined_holdings", lambda: holdings)
    monkeypatch.setattr(data_engine, "HISTORICAL_DIR", tmp_path)
    monkeypatch.setattr(refresh_helpers, "request_cache_refresh", refresh_helpers.submit_cache_refresh)
    started, release = threading.Event(), threading.Event()
    def stalled(*args, **kwargs):
        started.set()
        release.wait(10)
        return None
    monkeypatch.setattr(yahoo_engine, "_fetch_fx_rate", stalled)
    monkeypatch.setattr(data_engine, "_fetch_daily_history", stalled)
    try:
        started_at = time.perf_counter()
        with ThreadPoolExecutor(max_workers=1) as requests:
            portfolio = requests.submit(client.get, "/portfolio").result(timeout=5)
            assert started.wait(2)
            assert not release.is_set()
            detail = requests.submit(client.get, "/stock/" + ticker).result(timeout=5)
            unrelated = requests.submit(client.get, "/api/accounts/other-accounts-list").result(timeout=5)
        elapsed = time.perf_counter() - started_at
        assert portfolio.status_code == detail.status_code == unrelated.status_code == 200
        assert _global_market_value(portfolio.text, ticker) == pytest.approx(160.0)
        assert "using cached rate" in portfolio.text
        assert "using cached rate" in detail.text
        assert not release.is_set()
        print(f"Cached Portfolio + Detail + HA completed before upstream release in {elapsed:.3f}s")
        for label, response in (("Portfolio", portfolio), ("Detail", detail), ("HA", unrelated)):
            print(label, response.headers.get("Server-Timing"))
    finally:
        release.set()
        with refresh_helpers._lock:
            pending = list(refresh_helpers._pending.values())
        for future in pending:
            future.result(timeout=5)


@pytest.mark.pages
def test_missing_fx_marks_portfolio_total_unavailable(client, monkeypatch):
    ticker = "ZZSTEP3MISSING"
    _seed_stock_signal(ticker, 100.0, "BRL")
    monkeypatch.setattr("accounts_engine.get_combined_holdings", lambda: {
        ticker: {"ticker": ticker, "global_shares": 2.0, "global_buy_price": 50.0, "accounts": []}
    })
    response = client.get("/portfolio")
    assert response.status_code == 200
    assert "Unavailable — missing FX" in response.text
    assert "conversion unavailable" in response.text
    assert 'window.FX_INCOMPLETE = true;' in response.text
    assert 'data-fx-rate=""' in response.text


@pytest.mark.pages
@pytest.mark.parametrize("quote_currency,trade_currency,pence_flag", [
    ("GBp", "GBP", False),
    ("GBp", "GBp", True),
    ("GBP", "GBp", True),
])
def test_stock_detail_pnl_uses_quote_units_across_accounts(
    client, quote_currency, trade_currency, pence_flag,
):
    ticker = f"ZZPNL{int(quote_currency == 'GBp')}{int(trade_currency == 'GBp')}{int(pence_flag)}.L"
    trade_scale = 100 if trade_currency == "GBp" else 1
    for name, shares, cost in (("First", 10, 10), ("Second", 5, 10)):
        aid = create_account(f"Pnl {name} {ticker}", "GBP")
        add_transaction(
            aid, "Buy", "2026-01-05", ticker=ticker, currency=trade_currency,
            quantity=shares, unit_price=cost * trade_scale,
            exchange_rate=1 / trade_scale, price_in_pence=pence_flag,
        )
    _seed_stock_signal(ticker, 800 if quote_currency == "GBp" else 8, quote_currency)
    _seed_market_pulse(ticker, 900 if quote_currency == "GBp" else 9)

    portfolio = client.get("/portfolio")
    assert portfolio.status_code == 200
    assert _global_market_value(portfolio.text, ticker) == pytest.approx(135)
    row = re.search(rf'data-ticker="{ticker}".*?</tr>', portfolio.text, re.DOTALL)
    assert "-15.00" in row.group(0)

    detail = client.get(f"/stock/{ticker}")
    assert detail.status_code == 200
    position = detail.text.split("Your Position (Global Aggregation)", 1)[1].split("Account Breakdown", 1)[0]
    position = " ".join(position.split())
    assert re.search(r"Current Value:</span>\s*<strong>\s*135.00 GBP\s*</strong>", position)
    assert "-15.00 GBP (-10.0%)" in position
    breakdown = detail.text.split("Account Breakdown", 1)[1].split("Position Targets", 1)[0]
    breakdown = " ".join(breakdown.split())
    assert "-10.00 GBP (-10.0%)" in breakdown
    assert "-5.00 GBP (-10.0%)" in breakdown
