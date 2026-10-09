import pandas as pd

from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import load_config, BASE_CURRENCY, ACCOUNT_CURRENCIES
from database import get_connection, get_watchlist_tickers
from market_pulse import get_all_cached_pulse
from utils import normalize_ticker, ignored_tickers_set, measure_request_stage, time_request_call
from fundamentals_helpers import compute_quality_grade
from portfolio_service import get_rate_to_base, get_fx_cache_status
import table_columns_helpers
from constants import CSS_VERSION
from page_helpers import (
    _build_position_sizing_context,
    get_unread_count,
    compute_badge_tags,
    get_pattern_tags_by_ticker,
    get_all_scope_heat_tier,
    get_portfolio_heat_row,
)

page_router_portfolio = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["css_version"] = CSS_VERSION


def _fetch_portfolio_signal_rows(benchmark_symbol: str, tickers: list[str]):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        db_rows = []
        # Leave room for the benchmark parameter under SQLite's older 999-variable limit.
        for start in range(0, len(tickers), 900):
            batch = tickers[start:start + 900]
            placeholders = ",".join("?" for _ in batch)
            cursor.execute(f"""
            SELECT s.*,
                   (SELECT ml_confidence_score FROM quant_signals
                    WHERE ticker = s.ticker AND ml_confidence_score IS NOT NULL
                    ORDER BY date DESC LIMIT 1) AS ml_confidence_score,
                   (SELECT var_95 FROM quant_signals
                    WHERE ticker = s.ticker AND var_95 IS NOT NULL
                    ORDER BY date DESC LIMIT 1) AS var_95,
                   (SELECT cvar_95 FROM quant_signals
                    WHERE ticker = s.ticker AND cvar_95 IS NOT NULL
                    ORDER BY date DESC LIMIT 1) AS cvar_95,
                   (SELECT sentiment_score FROM quant_signals
                    WHERE ticker = s.ticker AND sentiment_score IS NOT NULL
                    ORDER BY date DESC LIMIT 1) AS sentiment_score,
                   q.atr_pct,
                   q.close_price as quant_close_price,
                   q.vp_entry_zone,
                   q.vp_exit_zone,
                   q.macd, q.macd_signal, q.macd_hist, q.sma_200,
                   q.week52_pct, q.anomaly_score, q.vp_poc, q.vp_val, q.vp_vah,
                   q.kc_z_score, q.kc_entry_signal, q.kc_exit_signal,
                   q.price_q10, q.price_q90, q.mom_1m, q.mom_3m, q.mom_6m, q.mom_12m_skip1m,
                   q.rel_strength_5d, q.rel_strength_20d, q.hist_vol_20, q.volume,
                   ap.industry, mu.index_membership,
                   tmeta.market_cap,
                   xrisk.beta AS xray_beta, xrisk.annualized_vol AS xray_annualized_vol,
                   (SELECT dividend_yield_pct FROM xray_dividend_cache
                    WHERE ticker = s.ticker ORDER BY last_updated DESC LIMIT 1) AS xray_dividend_yield,
                   ev.edge_score AS earnings_edge_score, ev.implied_move_pct AS earnings_implied_move,
                   trap.phase as trap_phase,
                   rc.risk_tier AS heat_index_tier,
                   (SELECT flag FROM bubble_radar_metrics
                    WHERE ticker = s.ticker ORDER BY scan_date DESC LIMIT 1) AS bubble_flag,
                   COALESCE(
                       cno.display_name,
                       NULLIF(ap.company_name, s.ticker),
                       NULLIF(mu.company_name, s.ticker),
                       s.company_name,
                       s.ticker
                   ) as resolved_company_name
            FROM stock_signals s
            LEFT JOIN asset_profiles ap ON s.ticker = ap.ticker
            LEFT JOIN market_universe mu ON s.ticker = mu.ticker
            LEFT JOIN company_name_overrides cno ON s.ticker = cno.ticker
            LEFT JOIN ticker_metadata tmeta ON s.ticker = tmeta.ticker
            LEFT JOIN quant_signals q ON s.ticker = q.ticker
            AND q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = s.ticker)
            LEFT JOIN xray_risk_cache xrisk ON s.ticker = xrisk.ticker AND xrisk.benchmark = ?
            LEFT JOIN earnings_volatility ev ON s.ticker = ev.ticker
            LEFT JOIN trap_monitor_results trap ON s.ticker = trap.ticker
            LEFT JOIN ticker_risk_contribution rc ON s.ticker = rc.ticker
            WHERE s.ticker IN ({placeholders})
        """, (benchmark_symbol, *batch))
            db_rows.extend(cursor.fetchall())

        cursor.execute("SELECT * FROM macro_regimes ORDER BY date DESC LIMIT 1")
        macro_row = cursor.fetchone()
        macro_regime = dict(macro_row) if macro_row else None

        cursor.execute("SELECT MAX(last_updated) as global_updated FROM stock_signals")
        global_update_val = cursor.fetchone()['global_updated']
        global_updated = global_update_val if global_update_val else "Awaiting initial update..."
    finally:
        if conn:
            conn.close()

    return db_rows, macro_regime, global_updated


@page_router_portfolio.get("/portfolio", response_class=HTMLResponse)
def portfolio_page(request: Request, background_tasks: BackgroundTasks, account_id: str = "all", embed: bool = False, embed_token: str = "", xray: bool = False):
    from xray_engine import BENCHMARK_SYMBOL

    config_data = load_config()
    from accounts_engine import get_combined_holdings
    portfolio_json = get_combined_holdings()
    portfolio_tickers = []

    for key, data in portfolio_json.items():
        if "ticker" in data:
            if account_id == "all":
                portfolio_tickers.append(data["ticker"])
            else:
                for acc in data.get("accounts", []):
                    if acc["id"] == account_id:
                        portfolio_tickers.append(data["ticker"])
                        break

    ignored_tickers = ignored_tickers_set(config_data)
    portfolio_tickers = list(dict.fromkeys(
        t for t in portfolio_tickers if normalize_ticker(t) not in ignored_tickers
    ))

    with measure_request_stage("sql"):
        db_rows, macro_regime, global_updated = _fetch_portfolio_signal_rows(BENCHMARK_SYMBOL, portfolio_tickers)
    with measure_request_stage("fx_context"):
        position_sizing_context = _build_position_sizing_context(config_data, db_rows)

    active_accounts = config_data.get("GHOSTFOLIO_ACCOUNTS", {}).get("active", [])
    discovered_accounts = config_data.get("GHOSTFOLIO_ACCOUNTS", {}).get("discovered", [])
    account_options = [{"id": "all", "name": "Global (All Accounts)"}]
    for acc in discovered_accounts:
        if acc["id"] in active_accounts:
            account_options.append({"id": acc["id"], "name": acc["name"]})

    from database import get_accounts
    for acc in get_accounts():
        if acc["account_type"] == "Trading":
            account_options.append({"id": f"acct:{acc['id']}", "name": acc["name"]})

    portfolio_data = []
    missing_fx = False
    summary_math = {"value": 0.0, "cost": 0.0, "pnl": 0.0, "pnl_pct": 0.0}
    pattern_tags_by_ticker = get_pattern_tags_by_ticker(portfolio_tickers)

    from score_analysis import (
        buy_recommendation_label,
        compute_regime_weighted_score_batch,
        evaluate_buy_recommendation_batch,
        evaluate_pillar_confluence_batch,
        pillar_confluence_label,
    )
    confluence_by_ticker = evaluate_pillar_confluence_batch(portfolio_tickers)
    regime_score_by_ticker = compute_regime_weighted_score_batch(portfolio_tickers)
    buy_recommendation_by_ticker = evaluate_buy_recommendation_batch(
        portfolio_tickers, confluence_by_ticker=confluence_by_ticker,
        regime_score_by_ticker=regime_score_by_ticker,
    )
    from sector_relative_momentum_engine import get_column_values as get_sector_momentum_values
    sector_momentum_by_ticker = get_sector_momentum_values(sorted(portfolio_tickers))
    from stable_shortlist_reads import get_column_values as get_shortlist_values
    shortlist_by_ticker = get_shortlist_values(sorted(portfolio_tickers))

    for row in db_rows:
        row_dict = dict(row)
        if row_dict['ticker'] in portfolio_tickers:
            # Resolve best available display name — mutual funds often have no shortName
            # from yfinance; fall back through asset_profiles → market_universe
            row_dict['company_name'] = (
                row_dict.get('resolved_company_name')
                or row_dict.get('company_name')
                or row_dict['ticker']
            )
            row_dict['pattern_detections'] = pattern_tags_by_ticker.get(row_dict['ticker'], [])
            row_dict.update(compute_badge_tags(row_dict))
            row_dict['heat_index'] = (row_dict.get('heat_index_tier') or '').capitalize() or None
            row_dict['pillar_confluence_result'] = confluence_by_ticker.get(row_dict['ticker'])
            row_dict['pillar_confluence'] = pillar_confluence_label(row_dict['pillar_confluence_result'])
            row_dict['regime_weighted_result'] = regime_score_by_ticker.get(row_dict['ticker'])
            row_dict['regime_weighted_score'] = (row_dict['regime_weighted_result'] or {}).get('score')
            row_dict['buy_recommendation_result'] = buy_recommendation_by_ticker.get(row_dict['ticker'])
            row_dict['buy_recommendation'] = buy_recommendation_label(row_dict['buy_recommendation_result'])

            portfolio_data.append(row_dict)

    portfolio_data.sort(key=lambda x: x['ticker'])

    present_tags = set()
    present_pattern_tags = set()
    for row_dict in portfolio_data:
        if row_dict.get('trap_phase_label'):
            present_tags.add(row_dict['trap_phase_label'])
        if row_dict.get('bubble_flag_label'):
            present_tags.add(row_dict['bubble_flag_label'])
        for tag in row_dict.get('pattern_tags', []):
            present_tags.add(tag['label'])
            present_pattern_tags.add(tag['label'])
    present_pattern_tags = sorted(present_pattern_tags)

    live_pulse = get_all_cached_pulse()

    from accounts_engine import current_price_map
    from api_routes_accounts import maybe_trigger_price_refresh
    maybe_trigger_price_refresh(background_tasks)
    price_map = current_price_map(list(set(portfolio_tickers)))

    from price_history_helpers import get_period_anchor_closes, pct_from_anchor, CHANGE_PERIODS
    with measure_request_stage("history_anchors"):
        anchor_closes = get_period_anchor_closes(list(set(portfolio_tickers)), cache_only=True)

    change_period = request.cookies.get("portfolio_change_period", "1d")
    if change_period not in CHANGE_PERIODS:
        change_period = "1d"
    show_extended = request.cookies.get("portfolio_show_extended", "false") == "true"

    from db_accounts import get_all_holding_price_limits
    all_holding_limits = get_all_holding_price_limits()

    for row_dict in portfolio_data:
        row_dict['market_value_base'] = None
        row_dict['global_market_value'] = None
        row_dict['global_unrealized_pnl'] = None
        row_dict['global_unrealized_pnl_pct'] = None
        row_dict['live_shares'] = None
        row_dict['live_cost_base'] = None
        row_dict['live_fx_rate'] = None
        asset = next((d for d in portfolio_json.values() if d.get("ticker") == row_dict['ticker']), None)
        priced = price_map.get(row_dict['ticker'])
        current_price = priced[0] if priced and priced[0] else row_dict['current_price']

        cp = live_pulse.get(row_dict['ticker'])
        row_dict['period_anchors'] = anchor_closes.get(row_dict['ticker'], {})
        if change_period == "1d":
            row_dict['change_pct'] = cp['change_pct'] if cp else None
            row_dict['change_is_positive'] = cp['is_positive'] if cp else None
        else:
            display_price = cp['price'] if cp and cp.get('price') is not None else row_dict['current_price']
            pct = pct_from_anchor(display_price, row_dict['period_anchors'].get(change_period))
            row_dict['change_pct'] = pct
            row_dict['change_is_positive'] = (pct >= 0) if pct is not None else None

        if asset and current_price:
            shares = 0
            buy_price_base = 0

            if account_id == "all":
                shares = asset.get('global_shares', 0)
                buy_price_base = asset.get('global_buy_price', 0)
            else:
                for acc in asset.get('accounts', []):
                    if acc['id'] == account_id:
                        shares = acc.get('shares', 0)
                        buy_price_base = acc.get('buy_price', 0)
                        break

            cost_in_base = shares * buy_price_base
            with measure_request_stage("fx_rate"):
                exchange_rate = position_sizing_context["fx_rates"].get(row_dict['currency'])
                if exchange_rate is None:
                    exchange_rate = get_rate_to_base(row_dict['currency'], cache_only=True)
            row_dict["live_shares"] = shares
            row_dict["live_cost_base"] = cost_in_base
            summary_math["cost"] += cost_in_base
            if exchange_rate is None:
                missing_fx = True
            else:
                val_in_base = (shares * current_price) * exchange_rate
                row_dict['market_value_base'] = round(val_in_base, 2)
                row_dict['global_market_value'] = round(val_in_base, 2)
                row_dict['live_shares'] = shares
                row_dict['live_cost_base'] = cost_in_base
                row_dict['live_fx_rate'] = exchange_rate

                summary_math["value"] += val_in_base

                pnl_in_base = val_in_base - cost_in_base
                row_dict['global_unrealized_pnl'] = round(pnl_in_base, 2)
                row_dict['global_unrealized_pnl_pct'] = round((pnl_in_base / cost_in_base) * 100, 2) if cost_in_base else None


        row_dict['quality_grade'] = compute_quality_grade(row_dict)

        acct_ids = []
        if asset:
            for acc in asset.get('accounts', []):
                raw_id = acc.get('id', '')
                if isinstance(raw_id, str) and raw_id.startswith('acct:') and (account_id == "all" or raw_id == account_id):
                    try:
                        acct_ids.append(int(raw_id[len('acct:'):]))
                    except ValueError:
                        pass
        lows = {all_holding_limits.get((aid, row_dict['ticker']), {}).get('low_limit') for aid in acct_ids}
        lows.discard(None)
        highs = {all_holding_limits.get((aid, row_dict['ticker']), {}).get('high_limit') for aid in acct_ids}
        highs.discard(None)
        row_dict['low_target'] = next(iter(lows)) if len(lows) == 1 else None
        row_dict['high_target'] = next(iter(highs)) if len(highs) == 1 else None

        row_dict.update(sector_momentum_by_ticker.get(row_dict['ticker'], {}))
        row_dict.update(shortlist_by_ticker.get(row_dict['ticker'], {}))
        row_dict['optional_cols'] = table_columns_helpers.build_optional_column_cells(row_dict, "portfolio")

    if missing_fx:
        formatted_summary = {
            "value": "Unavailable — missing FX",
            "cost": f"{summary_math['cost']:,.2f} {BASE_CURRENCY}",
            "pnl": "Unavailable",
            "pnl_pct": "—",
            "is_positive": False,
        }
    elif summary_math["cost"] > 0:
        summary_math["pnl"] = summary_math["value"] - summary_math["cost"]
        summary_math["pnl_pct"] = (summary_math["pnl"] / summary_math["cost"]) * 100
        formatted_summary = {
            "value": f"{summary_math['value']:,.2f} {BASE_CURRENCY}",
            "cost": f"{summary_math['cost']:,.2f} {BASE_CURRENCY}",
            "pnl": f"{'+' if summary_math['pnl'] > 0 else ''}{summary_math['pnl']:,.2f} {BASE_CURRENCY}",
            "pnl_pct": f"{summary_math['pnl_pct']:.2f}",
            "is_positive": summary_math["pnl"] > 0
        }
    else:
        formatted_summary = None

    optional_columns = table_columns_helpers.columns_for_page("portfolio")
    column_prefs = table_columns_helpers.resolve_column_prefs(config_data, "portfolio")
    views = table_columns_helpers.resolve_views(config_data, "portfolio")

    return time_request_call("template", templates.TemplateResponse,
        request=request, name="portfolio.html",
        context={
            "portfolio": portfolio_data,
            "global_updated": global_updated,
            "embed": embed,
            "embed_token": embed_token,
            "unread_count": get_unread_count(),
            "account_options": account_options,
            "selected_account": account_id,
            "auto_xray": xray,
            "summary_math": formatted_summary,
            "fx_incomplete": missing_fx,
            "config": config_data,
            "cached_pulse": live_pulse,
            "macro_regime": macro_regime,
            "position_sizing": position_sizing_context,
            "change_period": change_period,
            "show_extended": show_extended,
            "optional_columns": optional_columns,
            "all_columns": table_columns_helpers.all_columns_for_page("portfolio"),
            "column_prefs": column_prefs,
            "views": views,
            "present_tags": present_tags,
            "present_pattern_tags": present_pattern_tags,
            "portfolio_heat": get_portfolio_heat_row("all"),
            "risk_paused": get_all_scope_heat_tier() == "RED",
        }
    )


@page_router_portfolio.get("/accounts", response_class=HTMLResponse)
def accounts_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="accounts.html",
        context={
            "base_currency": BASE_CURRENCY,
            "account_currencies": ACCOUNT_CURRENCIES,
            "unread_count": get_unread_count(),
        }
    )


@page_router_portfolio.get("/accounts/{account_id}", response_class=HTMLResponse)
def account_detail_page(request: Request, account_id: int):
    from accounts_engine import (
        account_summary, cash_history, closed_positions, filter_value_history_by_period,
        holdings_with_market_value, is_unresolved_ticker, refresh_performance_cache,
        stale_pricing_warning, transaction_total_base, VALUE_CHART_PERIODS,
    )
    from database import (
        get_account, get_performance_cache, get_transactions, get_unresolved_pending_topups,
        get_value_history, get_watchlist_items,
    )
    from treasury_bill_engine import bills_pending_ytm_confirmation, list_treasury_bills

    acc = get_account(account_id)
    if acc is None:
        return RedirectResponse("/accounts", status_code=302)

    if acc["account_type"] == "Pension":
        return RedirectResponse(f"/accounts/{account_id}/pension", status_code=302)

    if acc["account_type"] == "House":
        return RedirectResponse(f"/accounts/{account_id}/house", status_code=302)

    if acc["account_type"] == "Watchlist":
        return templates.TemplateResponse(
            request=request, name="watchlist_account_detail.html",
            context={
                "account": acc,
                "items": get_watchlist_items(acc["id"]),
                "unread_count": get_unread_count(),
            }
        )

    activities = get_transactions(account_id)
    for a in activities:
        a["total_base"] = transaction_total_base(a)
        a["needs_review"] = is_unresolved_ticker(a.get("ticker"))

    chart_period = request.cookies.get("acct_chart_period", "max")
    if chart_period not in VALUE_CHART_PERIODS:
        chart_period = "max"
    chart_initial = filter_value_history_by_period(get_value_history(account_id), chart_period)

    holdings = holdings_with_market_value(account_id)
    pricing_warning = stale_pricing_warning(holdings)

    performance = get_performance_cache(account_id)
    if performance is None:
        refresh_performance_cache(account_id)
        performance = get_performance_cache(account_id)

    return templates.TemplateResponse(
        request=request, name="account_detail.html",
        context={
            "account": acc,
            "summary": account_summary(account_id),
            "holdings": holdings,
            "pricing_warning": pricing_warning,
            "closed_positions": closed_positions(account_id),
            "activities": activities,
            "cash_history": cash_history(account_id),
            "chart_initial": chart_initial,
            "chart_period": chart_period,
            "base_currency": BASE_CURRENCY,
            "account_currencies": ACCOUNT_CURRENCIES,
            "unread_count": get_unread_count(),
            "pending_topups": get_unresolved_pending_topups(account_id),
            "performance": performance,
            "treasury_bills": list_treasury_bills(account_id),
            "treasury_bills_pending_ytm": bills_pending_ytm_confirmation(account_id),
            "config": load_config(),
            "portfolio_heat": get_portfolio_heat_row(f"acct:{account_id}"),
        }
    )


@page_router_portfolio.get("/accounts/{account_id}/pension", response_class=HTMLResponse)
def pension_account_detail_page(request: Request, account_id: int):
    from accounts_engine import (
        account_summary, pension_activities, pension_benchmark_overlay, pension_display_label,
        scraped_price_performance,
    )
    from database import get_account, get_price_history, get_value_history
    from visuals import create_pension_unit_price_chart, create_pension_value_chart

    acc = get_account(account_id)
    if acc is None or acc["account_type"] != "Pension":
        return RedirectResponse("/accounts", status_code=302)

    price_history = get_price_history(account_id)
    if price_history:
        price_df = pd.DataFrame(price_history).set_index("price_date")
        price_df.index = pd.to_datetime(price_df.index)
        price_chart_html = create_pension_unit_price_chart(price_df)
    else:
        price_chart_html = "<p class='text-muted'>No unit price history yet — scrape or import one to see this chart.</p>"

    value_history = get_value_history(account_id)
    if value_history:
        value_df = pd.DataFrame(value_history).set_index("snapshot_date")
        value_df.index = pd.to_datetime(value_df.index)
        benchmark_series = pension_benchmark_overlay(account_id, value_df)
        value_chart_html = create_pension_value_chart(value_df, benchmark_series)
    else:
        value_chart_html = "<p class='text-muted'>No value history yet — check back after the next nightly snapshot.</p>"

    summary = account_summary(account_id)
    return templates.TemplateResponse(
        request=request, name="account_detail_pension.html",
        context={
            "account": acc,
            "ticker_label": pension_display_label(acc),
            "summary": summary,
            "performance": scraped_price_performance(account_id),
            "activities": pension_activities(account_id),
            "price_chart_html": price_chart_html,
            "value_chart_html": value_chart_html,
            "base_currency": BASE_CURRENCY,
            "unread_count": get_unread_count(),
        }
    )


@page_router_portfolio.get("/accounts/{account_id}/house", response_class=HTMLResponse)
def house_account_detail_page(request: Request, account_id: int):
    from database import get_account, get_price_history

    acc = get_account(account_id)
    if acc is None or acc["account_type"] != "House":
        return RedirectResponse("/accounts", status_code=302)

    price_history = get_price_history(account_id)
    if price_history:
        from visuals import create_house_value_chart
        price_df = pd.DataFrame(price_history).set_index("price_date")
        price_df.index = pd.to_datetime(price_df.index)
        chart_html = create_house_value_chart(price_df)
    else:
        chart_html = "<p class='text-muted'>No value history yet — scrape or import one to see this chart.</p>"

    return templates.TemplateResponse(
        request=request, name="account_detail_house.html",
        context={
            "account": acc,
            "chart_html": chart_html,
            "base_currency": BASE_CURRENCY,
            "unread_count": get_unread_count(),
        }
    )


def _fetch_watchlist_signal_rows(benchmark_symbol: str):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT s.*,
                   (SELECT ml_confidence_score FROM quant_signals
                    WHERE ticker = s.ticker AND ml_confidence_score IS NOT NULL
                    ORDER BY date DESC LIMIT 1) AS ml_confidence_score,
                   (SELECT var_95 FROM quant_signals
                    WHERE ticker = s.ticker AND var_95 IS NOT NULL
                    ORDER BY date DESC LIMIT 1) AS var_95,
                   (SELECT cvar_95 FROM quant_signals
                    WHERE ticker = s.ticker AND cvar_95 IS NOT NULL
                    ORDER BY date DESC LIMIT 1) AS cvar_95,
                   (SELECT sentiment_score FROM quant_signals
                    WHERE ticker = s.ticker AND sentiment_score IS NOT NULL
                    ORDER BY date DESC LIMIT 1) AS sentiment_score,
                   q.atr_pct,
                   q.close_price as quant_close_price,
                   q.vp_entry_zone,
                   q.vp_exit_zone,
                   q.sma_200,
                   q.macd, q.macd_signal, q.macd_hist,
                   q.week52_pct, q.anomaly_score, q.vp_poc, q.vp_val, q.vp_vah,
                   q.kc_z_score, q.kc_entry_signal, q.kc_exit_signal,
                   q.price_q10, q.price_q90, q.mom_1m, q.mom_3m, q.mom_6m, q.mom_12m_skip1m,
                   q.rel_strength_5d, q.rel_strength_20d, q.hist_vol_20, q.volume,
                   ap.industry, m.index_membership,
                   m.is_freetrade,
                   tmeta.market_cap,
                   xrisk.beta AS xray_beta, xrisk.annualized_vol AS xray_annualized_vol,
                   (SELECT dividend_yield_pct FROM xray_dividend_cache
                    WHERE ticker = s.ticker ORDER BY last_updated DESC LIMIT 1) AS xray_dividend_yield,
                   ev.edge_score AS earnings_edge_score, ev.implied_move_pct AS earnings_implied_move,
                   trap.phase as trap_phase,
                   (SELECT flag FROM bubble_radar_metrics
                    WHERE ticker = s.ticker ORDER BY scan_date DESC LIMIT 1) AS bubble_flag,
                   COALESCE(
                       cno.display_name,
                       NULLIF(ap.company_name, s.ticker),
                       NULLIF(m.company_name, s.ticker),
                       s.company_name,
                       s.ticker
                   ) as resolved_company_name
            FROM stock_signals s
            LEFT JOIN quant_signals q ON s.ticker = q.ticker
            AND q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = s.ticker)
            LEFT JOIN market_universe m ON s.ticker = m.ticker
            LEFT JOIN asset_profiles ap ON s.ticker = ap.ticker
            LEFT JOIN company_name_overrides cno ON s.ticker = cno.ticker
            LEFT JOIN ticker_metadata tmeta ON s.ticker = tmeta.ticker
            LEFT JOIN trap_monitor_results trap ON s.ticker = trap.ticker
            LEFT JOIN xray_risk_cache xrisk ON s.ticker = xrisk.ticker AND xrisk.benchmark = ?
            LEFT JOIN earnings_volatility ev ON s.ticker = ev.ticker
        """, (benchmark_symbol,))
        db_rows = cursor.fetchall()

        cursor.execute("SELECT MAX(last_updated) as global_updated FROM stock_signals")
        global_update_val = cursor.fetchone()['global_updated']
        global_updated = global_update_val if global_update_val else "Awaiting initial update..."
    finally:
        if conn:
            conn.close()

    return db_rows, global_updated


@page_router_portfolio.get("/watchlist", response_class=HTMLResponse)
def watchlist_page(request: Request, embed: bool = False, embed_token: str = ""):
    from xray_engine import BENCHMARK_SYMBOL

    with measure_request_stage("sql"):
        db_rows, global_updated = _fetch_watchlist_signal_rows(BENCHMARK_SYMBOL)

    watchlist_tickers = get_watchlist_tickers()

    from db_accounts import get_all_holding_price_limits, get_watchlist_account
    watchlist_account_id = get_watchlist_account()['id']
    all_holding_limits = get_all_holding_price_limits()

    from price_history_helpers import get_period_anchor_closes, pct_from_anchor, CHANGE_PERIODS
    with measure_request_stage("history_anchors"):
        anchor_closes = get_period_anchor_closes(list(set(watchlist_tickers)), cache_only=True)

    change_period = request.cookies.get("watchlist_change_period", "1d")
    if change_period not in CHANGE_PERIODS:
        change_period = "1d"

    cached_pulse = get_all_cached_pulse()

    watchlist_data = []
    pattern_tags_by_ticker = get_pattern_tags_by_ticker(watchlist_tickers)

    from score_analysis import (
        buy_recommendation_label,
        compute_regime_weighted_score_batch,
        evaluate_buy_recommendation_batch,
        evaluate_pillar_confluence_batch,
        pillar_confluence_label,
    )
    confluence_by_ticker = evaluate_pillar_confluence_batch(watchlist_tickers)
    regime_score_by_ticker = compute_regime_weighted_score_batch(watchlist_tickers)
    buy_recommendation_by_ticker = evaluate_buy_recommendation_batch(
        watchlist_tickers, confluence_by_ticker=confluence_by_ticker,
        regime_score_by_ticker=regime_score_by_ticker,
    )
    from sector_relative_momentum_engine import get_column_values as get_sector_momentum_values
    sector_momentum_by_ticker = get_sector_momentum_values(sorted(watchlist_tickers))
    from stable_shortlist_reads import get_column_values as get_shortlist_values
    shortlist_by_ticker = get_shortlist_values(sorted(watchlist_tickers))

    for row in db_rows:
        row_dict = dict(row)
        if row_dict['ticker'] in watchlist_tickers:
            row_dict['company_name'] = (
                row_dict.get('resolved_company_name')
                or row_dict.get('company_name')
                or row_dict['ticker']
            )
            row_dict['pattern_detections'] = pattern_tags_by_ticker.get(row_dict['ticker'], [])
            row_dict.update(compute_badge_tags(row_dict))
            row_dict['pillar_confluence_result'] = confluence_by_ticker.get(row_dict['ticker'])
            row_dict['pillar_confluence'] = pillar_confluence_label(row_dict['pillar_confluence_result'])
            row_dict['regime_weighted_result'] = regime_score_by_ticker.get(row_dict['ticker'])
            row_dict['regime_weighted_score'] = (row_dict['regime_weighted_result'] or {}).get('score')
            row_dict['buy_recommendation_result'] = buy_recommendation_by_ticker.get(row_dict['ticker'])
            row_dict['buy_recommendation'] = buy_recommendation_label(row_dict['buy_recommendation_result'])

            limits = all_holding_limits.get((watchlist_account_id, row_dict['ticker']), {})
            row_dict['low_target'] = limits.get('low_limit')
            row_dict['high_target'] = limits.get('high_limit')

            row_dict.update(sector_momentum_by_ticker.get(row_dict['ticker'], {}))
            row_dict.update(shortlist_by_ticker.get(row_dict['ticker'], {}))
            row_dict['optional_cols'] = table_columns_helpers.build_optional_column_cells(row_dict, "watchlist")

            cp = cached_pulse.get(row_dict['ticker'])
            row_dict['period_anchors'] = anchor_closes.get(row_dict['ticker'], {})
            if change_period == "1d":
                row_dict['change_pct'] = cp['change_pct'] if cp else None
                row_dict['change_is_positive'] = cp['is_positive'] if cp else None
            else:
                display_price = cp['price'] if cp and cp.get('price') is not None else row_dict['current_price']
                pct = pct_from_anchor(display_price, row_dict['period_anchors'].get(change_period))
                row_dict['change_pct'] = pct
                row_dict['change_is_positive'] = (pct >= 0) if pct is not None else None

            watchlist_data.append(row_dict)

    watchlist_data.sort(key=lambda x: x['ticker'])
    sectors = sorted({row['sector'] or 'Unclassified' for row in watchlist_data})

    present_signals = {row['overall_signal'].upper() for row in watchlist_data if row.get('overall_signal')}

    present_tags = set()
    present_pattern_tags = set()
    for row in watchlist_data:
        for tag in row.get('setup_tags_list') or []:
            present_tags.add(tag['name'])
        for tag in row.get('report_tags') or []:
            present_tags.add(tag['name'])
        if row.get('trap_phase_label'):
            present_tags.add(row['trap_phase_label'])
        if row.get('bubble_flag_label'):
            present_tags.add(row['bubble_flag_label'])
        for tag in row.get('pattern_tags', []):
            present_tags.add(tag['label'])
            present_pattern_tags.add(tag['label'])
        if row.get('quality_grade'):
            present_tags.add('Grade ' + row['quality_grade'])
    present_pattern_tags = sorted(present_pattern_tags)

    present_score_buckets = set()
    for row in watchlist_data:
        score = row.get('composite_score')
        if score is None:
            continue
        if score >= 75:
            present_score_buckets.add('75')
        elif score >= 60:
            present_score_buckets.add('60')
        elif score >= 40:
            present_score_buckets.add('40')
        else:
            present_score_buckets.add('0')

    config_data = load_config()
    freetrade_only = config_data.get("UI_PREFERENCES", {}).get("FREETRADE_ONLY_MODE", False)
    with measure_request_stage("fx_context"):
        position_sizing_context = _build_position_sizing_context(config_data, db_rows)
    fx_currencies = sorted({row["currency"] for row in db_rows if row["currency"]})
    position_sizing_context["fx_status"] = get_fx_cache_status(fx_currencies, include_fresh=True)
    optional_columns = table_columns_helpers.columns_for_page("watchlist")
    column_prefs = table_columns_helpers.resolve_column_prefs(config_data, "watchlist")
    views = table_columns_helpers.resolve_views(config_data, "watchlist")

    return time_request_call("template", templates.TemplateResponse,
        request=request, name="watchlist.html",
        context={
            "watchlist": watchlist_data,
            "fx_currencies": fx_currencies,
            "sectors": sectors,
            "present_signals": present_signals,
            "present_tags": present_tags,
            "present_pattern_tags": present_pattern_tags,
            "present_score_buckets": present_score_buckets,
            "global_updated": global_updated,
            "embed": embed,
            "embed_token": embed_token,
            "unread_count": get_unread_count(),
            "config": config_data,
            "cached_pulse": cached_pulse,
            "freetrade_only": freetrade_only,
            "position_sizing": position_sizing_context,
            "optional_columns": optional_columns,
            "all_columns": table_columns_helpers.all_columns_for_page("watchlist"),
            "views": views,
            "column_prefs": column_prefs,
            "change_period": change_period,
            "risk_paused": get_all_scope_heat_tier() == "RED",
        }
    )
