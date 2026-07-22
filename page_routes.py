import email.utils
import ipaddress
import json
import logging
import os
import re
from pathlib import Path

import markdown as _markdown
import pandas as pd

logger = logging.getLogger(__name__)
from datetime import datetime, timedelta, timezone
from html import escape as html_escape
from typing import Dict, Any, List
from urllib.parse import urlparse

from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates

from config import load_config, HISTORICAL_DIR, INTRADAY_DIR, BASE_CURRENCY, ACCOUNT_CURRENCIES
import time_engine
from database import get_connection, get_watchlist_tickers
from market_pulse import get_all_cached_pulse, get_index_tickers
from utils import normalize_ticker, ignored_tickers_set
from fundamentals_helpers import compute_quality_grade
from visuals import (
    create_macro_chart,
    create_intraday_chart,
    intraday_market_tz,
    EXCHANGE_DELAYS,
    create_anomaly_score_chart,
    create_anomaly_feature_radar,
)
from visuals_etf import (
    create_etf_correlation_chart,
    create_etf_prediction_chart,
    create_etf_contributions_chart,
    create_etf_overlay_chart,
)
from visuals_ai import (
    create_ai_contagion_performance_chart,
    create_ai_contagion_correlation_heatmap,
)
from portfolio_service import get_rate_to_base, get_rate_from_base
from fx_drag_engine import compute_fx_breakdown, portfolio_fx_breakdown, portfolio_lifetime_fx_breakdown
from quant_signals import get_candlestick_patterns
import table_columns_helpers
from constants import PREDICTION_HORIZON_DAYS, PREDICTION_RETURN_THRESHOLD, CSS_VERSION
from page_helpers import (
    _load_fundamentals_extra,
    _utc_str_to_local,
    _build_position_sizing_context,
    get_unread_count,
    calculate_pnl,
    compute_badge_tags,
    get_pattern_tags_by_ticker,
    get_all_scope_heat_tier,
    get_portfolio_heat_row,
)
from page_routes_macro import page_router_macro

page_router = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["css_version"] = CSS_VERSION


@page_router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


@page_router.get("/logout")
async def logout():
    response = RedirectResponse("/login", status_code=302)
    response.delete_cookie("session")
    return response


@page_router.get("/change-password", response_class=HTMLResponse)
async def change_password_page(request: Request):
    return templates.TemplateResponse(request=request, name="change_password.html",
                                      context={"confirm_token": os.environ.get("ADMIN_CONFIRM_TOKEN", "")})


@page_router.get("/reset-password", response_class=HTMLResponse)
async def reset_password_page(request: Request):
    token = request.query_params.get("token", "")
    return templates.TemplateResponse(request=request, name="reset_password.html", context={"token": token})


@page_router.get("/admin-reset-password", response_class=HTMLResponse)
async def admin_reset_password_page(request: Request):
    if not load_config().get("FORCE_PASSWORD_RESET", False):
        return RedirectResponse("/login", status_code=302)
    return templates.TemplateResponse(request=request, name="admin_reset_password.html")



@page_router.get("/settings", response_class=HTMLResponse)
async def settings_page(request: Request):
    from scheduler_engine import scheduler_display_names
    from notification_engine import build_routing_panel
    config_data = load_config()
    auction_sched = config_data.get("SCHEDULING", {}).get("MACRO_AUCTIONS", {})
    auction_am_input = auction_sched.get("AM_TIME", time_engine.fmt_et_time_value("13:15"))
    auction_pm_input = auction_sched.get("PM_TIME", time_engine.fmt_et_time_value("15:30"))
    return templates.TemplateResponse(
        request=request,
        name="settings.html",
        context={
            "config": config_data,
            "scheduler_job_labels": scheduler_display_names(),
            "notification_routing": build_routing_panel(config_data),
            "auction_am_input": auction_am_input,
            "auction_pm_input": auction_pm_input,
            "exchange_list": sorted(time_engine.EXCHANGE_HOURS.keys()),
            "unread_count": get_unread_count(),
            "dashboard_username": os.environ.get("DASHBOARD_USERNAME", "admin"),
            "api_key": os.environ.get("API_KEY", ""),
            "embed_token": os.environ.get("EMBED_TOKEN", ""),
            "confirm_token": os.environ.get("ADMIN_CONFIRM_TOKEN", ""),
            "nextcloud_url": os.environ.get("NEXTCLOUD_URL", ""),
            "nextcloud_bot_username": os.environ.get("NEXTCLOUD_BOT_USERNAME", ""),
            "nextcloud_app_password": os.environ.get("NEXTCLOUD_APP_PASSWORD", ""),
            "nextcloud_conversation_token": os.environ.get("NEXTCLOUD_CONVERSATION_TOKEN", ""),
            "ghostfolio_url": os.environ.get("GHOSTFOLIO_URL", ""),
            "ghostfolio_token": os.environ.get("GHOSTFOLIO_TOKEN", ""),
            "fred_api_key": os.environ.get("FRED_API_KEY", ""),
            "hf_token": os.environ.get("HF_TOKEN", ""),
            "account_email": os.environ.get("ACCOUNT_EMAIL", ""),
            "smtp_host": os.environ.get("SMTP_HOST", ""),
            "smtp_port": os.environ.get("SMTP_PORT", "587"),
            "smtp_user": os.environ.get("SMTP_USER", ""),
            "smtp_pass": os.environ.get("SMTP_PASS", ""),
            "smtp_from": os.environ.get("SMTP_FROM", ""),
        }
    )


@page_router.get("/options-sandbox", response_class=HTMLResponse)
async def options_sandbox_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="options_sandbox.html",
        context={
            "unread_count": get_unread_count(),
            "config": load_config(),
            "cached_pulse": get_all_cached_pulse()
        }
    )


@page_router.get("/notifications", response_class=HTMLResponse)
async def notifications_page(request: Request):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM system_notifications ORDER BY timestamp DESC LIMIT 100")
        rows = cursor.fetchall()
    finally:
        conn.close()
    notifications = []
    for row in rows:
        note = dict(row)
        note["timestamp"] = _utc_str_to_local(note["timestamp"])
        notifications.append(note)
    return templates.TemplateResponse(
        request=request,
        name="notifications.html",
        context={"notifications": notifications, "unread_count": get_unread_count()}
    )


_ASSETS_DIR = Path(__file__).parent / "assets"
_MD = _markdown.Markdown(extensions=["tables", "fenced_code"])


def _render_asset_docs() -> list[dict]:
    docs = []
    for md_path in sorted(_ASSETS_DIR.glob("*.md")):
        raw = md_path.read_text(encoding="utf-8")
        title = md_path.stem.replace("_", " ").title()
        for line in raw.splitlines():
            if line.startswith("# "):
                title = line[2:].strip().strip("*").strip()
                break
        _MD.reset()
        html = _MD.convert(raw)
        slug = "doc-" + md_path.stem.lower().replace("_", "-")
        docs.append({"title": title, "slug": slug, "html": html})
    return docs


@page_router.get("/glossary", response_class=HTMLResponse)
async def glossary(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="glossary.html",
        context={
            "unread_count": get_unread_count(),
            "prediction_horizon": PREDICTION_HORIZON_DAYS,
            "prediction_threshold_pct": int(PREDICTION_RETURN_THRESHOLD * 100),
            "asset_docs": _render_asset_docs(),
        }
    )


@page_router.get("/glossary/learn", response_class=HTMLResponse)
async def glossary_learn(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="learn.html",
        context={"unread_count": get_unread_count(), "config": load_config()}
    )


@page_router.get("/", response_class=RedirectResponse)
async def home():
    return RedirectResponse(url="/portfolio")


@page_router.get("/portfolio", response_class=HTMLResponse)
async def portfolio_page(request: Request, background_tasks: BackgroundTasks, account_id: str = "all", embed: bool = False, embed_token: str = "", xray: bool = False):
    from xray_engine import BENCHMARK_SYMBOL

    conn = get_connection()
    try:
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
        """, (BENCHMARK_SYMBOL,))
        db_rows = cursor.fetchall()

        cursor.execute("SELECT * FROM macro_regimes ORDER BY date DESC LIMIT 1")
        macro_row = cursor.fetchone()
        macro_regime = dict(macro_row) if macro_row else None

        cursor.execute("SELECT MAX(last_updated) as global_updated FROM stock_signals")
        global_update_val = cursor.fetchone()['global_updated']
        global_updated = global_update_val if global_update_val else "Awaiting initial update..."
    finally:
        conn.close()

    config_data = load_config()
    active_accounts = config_data.get("GHOSTFOLIO_ACCOUNTS", {}).get("active", [])
    discovered_accounts = config_data.get("GHOSTFOLIO_ACCOUNTS", {}).get("discovered", [])
    position_sizing_context = _build_position_sizing_context(config_data, db_rows)
    account_options = [{"id": "all", "name": "Global (All Accounts)"}]
    for acc in discovered_accounts:
        if acc["id"] in active_accounts:
            account_options.append({"id": acc["id"], "name": acc["name"]})

    from accounts_engine import get_combined_holdings
    from database import get_accounts
    for acc in get_accounts():
        if acc["account_type"] == "Trading":
            account_options.append({"id": f"acct:{acc['id']}", "name": acc["name"]})

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
    portfolio_tickers = [t for t in portfolio_tickers if normalize_ticker(t) not in ignored_tickers]

    portfolio_data = []
    summary_math = {"value": 0.0, "cost": 0.0, "pnl": 0.0, "pnl_pct": 0.0}
    pattern_tags_by_ticker = get_pattern_tags_by_ticker(portfolio_tickers)

    from score_analysis import (
        compute_regime_weighted_score_batch,
        evaluate_pillar_confluence_batch,
        pillar_confluence_label,
    )
    confluence_by_ticker = evaluate_pillar_confluence_batch(portfolio_tickers)
    regime_score_by_ticker = compute_regime_weighted_score_batch(portfolio_tickers)

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
    anchor_closes = get_period_anchor_closes(list(set(portfolio_tickers)))

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
            exchange_rate = get_rate_to_base(row_dict['currency'])
            val_in_base = (shares * current_price) * exchange_rate
            row_dict['market_value_base'] = round(val_in_base, 2)
            row_dict['global_market_value'] = round(val_in_base, 2)
            row_dict['live_shares'] = shares
            row_dict['live_cost_base'] = cost_in_base
            row_dict['live_fx_rate'] = exchange_rate

            summary_math["value"] += val_in_base
            summary_math["cost"] += cost_in_base

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

        row_dict['optional_cols'] = table_columns_helpers.build_optional_column_cells(row_dict, "portfolio")

    if summary_math["cost"] > 0:
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

    return templates.TemplateResponse(
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


@page_router.get("/accounts", response_class=HTMLResponse)
async def accounts_page(request: Request):
    return templates.TemplateResponse(
        request=request, name="accounts.html",
        context={
            "base_currency": BASE_CURRENCY,
            "account_currencies": ACCOUNT_CURRENCIES,
            "unread_count": get_unread_count(),
        }
    )


@page_router.get("/accounts/{account_id}", response_class=HTMLResponse)
async def account_detail_page(request: Request, account_id: int):
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


@page_router.get("/accounts/{account_id}/pension", response_class=HTMLResponse)
async def pension_account_detail_page(request: Request, account_id: int):
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


@page_router.get("/accounts/{account_id}/house", response_class=HTMLResponse)
async def house_account_detail_page(request: Request, account_id: int):
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


@page_router.get("/watchlist", response_class=HTMLResponse)
async def watchlist_page(request: Request, embed: bool = False, embed_token: str = ""):
    from xray_engine import BENCHMARK_SYMBOL

    conn = get_connection()
    try:
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
        """, (BENCHMARK_SYMBOL,))
        db_rows = cursor.fetchall()

        cursor.execute("SELECT MAX(last_updated) as global_updated FROM stock_signals")
        global_update_val = cursor.fetchone()['global_updated']
        global_updated = global_update_val if global_update_val else "Awaiting initial update..."
    finally:
        conn.close()

    watchlist_tickers = get_watchlist_tickers()

    from db_accounts import get_all_holding_price_limits, get_watchlist_account
    watchlist_account_id = get_watchlist_account()['id']
    all_holding_limits = get_all_holding_price_limits()

    from price_history_helpers import get_period_anchor_closes, pct_from_anchor, CHANGE_PERIODS
    anchor_closes = get_period_anchor_closes(list(set(watchlist_tickers)))

    change_period = request.cookies.get("watchlist_change_period", "1d")
    if change_period not in CHANGE_PERIODS:
        change_period = "1d"

    cached_pulse = get_all_cached_pulse()

    watchlist_data = []
    pattern_tags_by_ticker = get_pattern_tags_by_ticker(watchlist_tickers)

    from score_analysis import (
        compute_regime_weighted_score_batch,
        evaluate_pillar_confluence_batch,
        pillar_confluence_label,
    )
    confluence_by_ticker = evaluate_pillar_confluence_batch(watchlist_tickers)
    regime_score_by_ticker = compute_regime_weighted_score_batch(watchlist_tickers)

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

            limits = all_holding_limits.get((watchlist_account_id, row_dict['ticker']), {})
            row_dict['low_target'] = limits.get('low_limit')
            row_dict['high_target'] = limits.get('high_limit')

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
    position_sizing_context = _build_position_sizing_context(config_data, db_rows)
    optional_columns = table_columns_helpers.columns_for_page("watchlist")
    column_prefs = table_columns_helpers.resolve_column_prefs(config_data, "watchlist")
    views = table_columns_helpers.resolve_views(config_data, "watchlist")

    return templates.TemplateResponse(
        request=request, name="watchlist.html",
        context={
            "watchlist": watchlist_data,
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


@page_router.get("/news", response_class=HTMLResponse)
async def news_page(request: Request):
    config_data = load_config()
    return templates.TemplateResponse(
        request=request,
        name="news.html",
        context={
            "unread_count": get_unread_count(),
            "config": config_data,
        },
    )


@page_router.get("/earnings-volatility", response_class=HTMLResponse)
async def earnings_volatility_page(request: Request):
    today_str = time_engine.now_local().strftime('%Y-%m-%d')

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        query = """
            SELECT * FROM earnings_volatility
            WHERE next_earnings_date >= ?
            ORDER BY next_earnings_date ASC, edge_score DESC
        """
        cursor.execute(query, (today_str,))
        rows = cursor.fetchall()
        earnings_data = [dict(row) for row in rows]
    finally:
        if conn:
            conn.close()

    from db_helpers import get_latest_quantile_bands
    quant_bands = {r["ticker"]: r for r in get_latest_quantile_bands([r["ticker"] for r in earnings_data])}
    for row in earnings_data:
        row["quant_band"] = quant_bands.get(row["ticker"])

    return templates.TemplateResponse(
        request=request,
        name="earnings_volatility.html",
        context={
            "earnings_data": earnings_data,
            "unread_count": get_unread_count(),
            "config": load_config()
        }
    )


@page_router.get("/earnings-volatility/accuracy", response_class=HTMLResponse)
async def earnings_volatility_accuracy_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="earnings_volatility_accuracy.html",
        context={
            "unread_count": get_unread_count(),
            "config": load_config()
        }
    )


@page_router.get("/market-screener", response_class=HTMLResponse)
async def market_screener_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="market_screener.html",
        context={
            "unread_count": get_unread_count(),
            "config": load_config(),
            "prediction_horizon": PREDICTION_HORIZON_DAYS,
            "prediction_threshold_pct": int(PREDICTION_RETURN_THRESHOLD * 100),
        }
    )


@page_router.get("/quality-compounders", response_class=HTMLResponse)
async def quality_compounders_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="quality_compounders.html",
        context={"unread_count": get_unread_count(), "config": load_config()}
    )


@page_router.get("/garp-tenbaggers", response_class=HTMLResponse)
async def garp_tenbaggers_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="garp_tenbaggers.html",
        context={"unread_count": get_unread_count(), "config": load_config()}
    )


@page_router.get("/quality-on-sale", response_class=HTMLResponse)
async def quality_on_sale_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="quality_on_sale.html",
        context={"unread_count": get_unread_count(), "config": load_config()}
    )


@page_router.get("/sector-trends", response_class=HTMLResponse)
async def sector_trends_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="sector_trends.html",
        context={"unread_count": get_unread_count(), "config": load_config()}
    )


@page_router.get("/relative-strength-leaders", response_class=HTMLResponse)
async def relative_strength_leaders_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="relative_strength_leaders.html",
        context={"unread_count": get_unread_count(), "config": load_config()}
    )


@page_router.get("/mean-reversion", response_class=HTMLResponse)
async def mean_reversion_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="mean_reversion.html",
        context={"unread_count": get_unread_count(), "config": load_config()}
    )


@page_router.get("/dividend-harvest", response_class=HTMLResponse)
async def dividend_harvest_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dividend_harvest.html",
        context={"unread_count": get_unread_count(), "config": load_config()}
    )


@page_router.get("/markets", response_class=HTMLResponse)
async def markets_page(request: Request):
    default_view = request.cookies.get("markets_view", "dynamic")
    if default_view not in ("dynamic", "static"):
        default_view = "dynamic"
    hide_us_futures = request.cookies.get("markets_hide_us_futures") == "1"
    return templates.TemplateResponse(
        request=request,
        name="markets.html",
        context={
            "unread_count": get_unread_count(), "default_view": default_view,
            "hide_us_futures": hide_us_futures, "config": load_config(),
        },
    )


@page_router.get("/tools", response_class=HTMLResponse)
async def tools_page(request: Request):
    lse_open_utc, _ = time_engine.market_window_utc("LSE")
    lse_open_dt = datetime.combine(datetime.now(timezone.utc).date(), lse_open_utc, tzinfo=timezone.utc)
    lse_open_str = time_engine.fmt_time(lse_open_dt)
    return templates.TemplateResponse(
        request=request,
        name="tools.html",
        context={"unread_count": get_unread_count(), "lse_open_time": lse_open_str},
    )


@page_router.get("/reports", response_class=HTMLResponse)
async def reports_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="reports.html",
        context={"unread_count": get_unread_count()},
    )


@page_router.get("/yahoo-api-usage", response_class=HTMLResponse)
async def yahoo_api_usage_page(request: Request, date: str = Query(default=None, pattern=r"^\d{4}-\d{2}-\d{2}$")):
    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    return templates.TemplateResponse(
        request=request,
        name="yahoo_api_usage.html",
        context={"unread_count": get_unread_count(), "usage_date": date},
    )


@page_router.get("/dip-radar", response_class=HTMLResponse)
async def dip_radar_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="dip_radar_summary.html",
        context={"unread_count": get_unread_count()},
    )


@page_router.get("/bubble-radar", response_class=HTMLResponse)
async def bubble_radar_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="bubble_radar.html",
        context={
            "unread_count": get_unread_count(),
            "config": load_config(),
        },
    )


@page_router.get("/trap-monitor", response_class=HTMLResponse)
async def trap_monitor_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="trap_monitor.html",
        context={
            "unread_count": get_unread_count(),
            "config": load_config(),
        },
    )


@page_router.get("/portfolio-heat-index", response_class=HTMLResponse)
async def portfolio_heat_index_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="portfolio_heat_index.html",
        context={
            "unread_count": get_unread_count(),
            "config": load_config(),
        },
    )


@page_router.get("/pairs-spread", response_class=HTMLResponse)
async def pairs_spread_monitor_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="pairs_spread_monitor.html",
        context={
            "unread_count": get_unread_count(),
            "config": load_config(),
        },
    )


@page_router.get("/head-shoulders", response_class=RedirectResponse)
async def head_shoulders_page_redirect():
    return RedirectResponse(url="/pattern-detection", status_code=302)


@page_router.get("/pattern-detection", response_class=HTMLResponse)
async def pattern_detection_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="pattern_detection.html",
        context={
            "unread_count": get_unread_count(),
            "config": load_config(),
        },
    )


@page_router.get("/pattern-detection/{ticker}", response_class=HTMLResponse)
async def pattern_detection_detail_page(request: Request, ticker: str):
    ticker = normalize_ticker(ticker)
    return templates.TemplateResponse(
        request=request,
        name="pattern_detection_detail.html",
        context={
            "ticker": ticker,
            "unread_count": get_unread_count(),
            "config": load_config(),
        },
    )


@page_router.get("/predicted-movers", response_class=HTMLResponse)
async def predicted_movers_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="predicted_movers.html",
        context={
            "unread_count": get_unread_count(),
            "config": load_config(),
        },
    )


@page_router.get("/ticker-notes", response_class=HTMLResponse)
async def ticker_notes_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="ticker_notes.html",
        context={
            "unread_count": get_unread_count(),
            "config": load_config(),
        },
    )


@page_router.get("/predicted-movers/accuracy", response_class=HTMLResponse)
async def predicted_movers_accuracy_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="predicted_movers_accuracy.html",
        context={
            "unread_count": get_unread_count(),
            "config": load_config(),
        },
    )


@page_router.get("/forensic-screener", response_class=HTMLResponse)
async def forensic_screener_page(request: Request):
    from scheduler_engine import get_all_job_last_runs
    job_last_runs = get_all_job_last_runs()
    return templates.TemplateResponse(
        request=request,
        name="forensic_screener.html",
        context={
            "unread_count": get_unread_count(),
            "config": load_config(),
            "fetch_last_run": _utc_str_to_local((job_last_runs.get("forensic_quarterly_fetch_job") or {}).get("last_run", "")),
            "score_last_run": _utc_str_to_local((job_last_runs.get("forensic_scores_job") or {}).get("last_run", "")),
        },
    )


@page_router.get("/fx-drag", response_class=HTMLResponse)
async def fx_drag_page(request: Request):
    initial_data = portfolio_lifetime_fx_breakdown()
    return templates.TemplateResponse(
        request=request,
        name="fx_drag.html",
        context={
            "unread_count": get_unread_count(),
            "config": load_config(),
            "initial_data": initial_data,
            "initial_period": "lifetime",
            "css_version": CSS_VERSION,
        },
    )


@page_router.get("/monte-carlo", response_class=HTMLResponse)
async def monte_carlo_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="monte_carlo.html",
        context={"unread_count": get_unread_count()},
    )


@page_router.get("/performance-analytics", response_class=HTMLResponse)
async def performance_analytics_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="performance_analytics.html",
        context={"unread_count": get_unread_count()},
    )


@page_router.get("/portfolio-optimizer", response_class=HTMLResponse)
async def portfolio_optimizer_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="portfolio_optimizer.html",
        context={"unread_count": get_unread_count()},
    )


@page_router.get("/treasury-auctions", response_class=HTMLResponse)
async def treasury_auctions_page(request: Request):
    conn = None
    rows = []
    summary = None
    try:
        conn = get_connection()
        raw = conn.execute("""
            SELECT
                cusip, maturity_label, auction_date, high_yield, bid_to_cover, tail_bp,
                direct_pct, indirect_pct, dealer_pct, offering_amt, alert_fired,
                AVG(bid_to_cover) OVER (
                    PARTITION BY maturity_label ORDER BY auction_date
                    ROWS BETWEEN 6 PRECEDING AND 1 PRECEDING
                ) AS baseline_btc,
                AVG(tail_bp) OVER (
                    PARTITION BY maturity_label ORDER BY auction_date
                    ROWS BETWEEN 6 PRECEDING AND 1 PRECEDING
                ) AS baseline_tail
            FROM treasury_auction_results
            ORDER BY auction_date DESC, maturity_label ASC
            LIMIT 100
        """).fetchall()
        rows = [dict(r) for r in raw]

        stats = conn.execute("""
            SELECT COUNT(*) AS total,
                   MIN(auction_date) AS first_date,
                   MAX(auction_date) AS last_check,
                   SUM(alert_fired) AS weak_count
            FROM treasury_auction_results
        """).fetchone()
        if stats and stats["total"]:
            summary = dict(stats)
    except Exception:
        pass
    finally:
        if conn:
            conn.close()

    latest_weak_message = None
    latest_weak_row = next((r for r in rows if r.get("alert_fired")), None)
    if latest_weak_row:
        from treasury_auction_engine import is_weak, format_weakness_message
        weak, weak_btc, weak_tail = is_weak(
            latest_weak_row["bid_to_cover"], latest_weak_row["baseline_btc"],
            latest_weak_row["tail_bp"], latest_weak_row["baseline_tail"],
        )
        latest_weak_message = format_weakness_message(
            latest_weak_row["maturity_label"], latest_weak_row["auction_date"],
            latest_weak_row["bid_to_cover"], latest_weak_row["baseline_btc"],
            latest_weak_row["tail_bp"], latest_weak_row["baseline_tail"],
            weak_btc, weak_tail,
        )

    cfg = load_config()
    auction_sched = cfg.get("SCHEDULING", {}).get("MACRO_AUCTIONS", {})
    auction_am_input = auction_sched.get("AM_TIME", time_engine.fmt_et_time_value("13:15"))
    auction_pm_input = auction_sched.get("PM_TIME", time_engine.fmt_et_time_value("15:30"))
    return templates.TemplateResponse(
        request=request,
        name="treasury_auctions.html",
        context={
            "unread_count": get_unread_count(),
            "config": cfg,
            "rows": rows,
            "summary": summary,
            "css_version": CSS_VERSION,
            "auction_am_input": auction_am_input,
            "auction_pm_input": auction_pm_input,
            "latest_weak_message": latest_weak_message,
        },
    )


@page_router.get("/market-regime", response_class=HTMLResponse)
async def market_regime_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="market_regime.html",
        context={
            "unread_count": get_unread_count(),
            "config": load_config(),
        },
    )


@page_router.get("/etf-predictor", response_class=HTMLResponse)
async def etf_predictor_index_page(request: Request):
    from database import get_etf_predictor_configs, get_etf_accuracy
    configs = get_etf_predictor_configs()
    tiles = []
    for cfg in configs:
        accuracy = get_etf_accuracy(cfg["id"])
        rows = accuracy["next_open"]["rows"]
        last_row = rows[0] if rows else None
        last_resolved = next((r for r in rows if r.get("actual_open") is not None), None)
        tiles.append({
            "config": cfg,
            "last_prediction": last_row,
            "last_resolved": last_resolved,
            "summary": accuracy["next_open"]["summary"],
        })
    return templates.TemplateResponse(
        request=request,
        name="etf_predictor.html",
        context={
            "tiles": tiles,
            "unread_count": get_unread_count(),
            "config": load_config(),
        },
    )


@page_router.get("/etf-predictor/{config_id}", response_class=HTMLResponse)
async def etf_predictor_detail_page(request: Request, config_id: int):
    from database import get_etf_predictor_config
    from etf_predictor_engine import (
        detect_etf_info, run_prediction,
        get_etf_correlation_data, get_etf_intraday_overlay_data,
    )
    cfg = get_etf_predictor_config(config_id)
    if cfg is None:
        return RedirectResponse("/etf-predictor", status_code=302)

    error_html = "<p class='error-text'>Data unavailable — please try again later.</p>"
    etf_info = detect_etf_info(cfg["etf_ticker"])
    constituent_tickers = [h["ticker"] for h in cfg["constituents"]]

    try:
        prediction = run_prediction(config_id)
    except Exception as exc:
        logger.error("etf_predictor_detail run_prediction failed: %s", exc)
        prediction = {"status": "error", "error": str(exc), "predicted_price": None}

    correlation_chart_html = error_html
    prediction_chart_html = error_html
    contributions_chart_html = ""
    overlay_chart_html = error_html

    try:
        corr_data = get_etf_correlation_data(cfg, days=60)
        if not corr_data["normalized_df"].empty:
            correlation_chart_html = create_etf_correlation_chart(
                cfg["etf_ticker"],
                constituent_tickers,
                corr_data["normalized_df"],
                corr_data["rolling_corr"],
            )
    except Exception as exc:
        logger.warning("etf_predictor_detail corr chart failed: %s", exc)

    try:
        raw_df = corr_data.get("raw_df", pd.DataFrame())
        etf_hist = None
        if not raw_df.empty and cfg["etf_ticker"] in raw_df.columns:
            etf_hist = raw_df[cfg["etf_ticker"]].dropna().tail(25)
        prediction_chart_html = create_etf_prediction_chart(
            cfg["etf_ticker"], etf_info["currency"], etf_hist, prediction
        )
        if prediction.get("holdings_engine") and prediction["holdings_engine"].get("contributions"):
            contributions_chart_html = create_etf_contributions_chart(
                cfg["etf_ticker"], prediction["holdings_engine"]["contributions"]
            )
    except Exception as exc:
        logger.warning("etf_predictor_detail pred charts failed: %s", exc)

    try:
        overlay_data = get_etf_intraday_overlay_data(cfg, prediction)
        overlay_chart_html = create_etf_overlay_chart(
            cfg["etf_ticker"],
            etf_info["exchange"],
            overlay_data["constituent_exchanges"],
            overlay_data["etf_series"],
            overlay_data["constituent_series"],
            overlay_data["etf_last_close"],
            prediction=overlay_data["prediction"],
            next_open_date=overlay_data["next_open_date"],
            constituent_prev_closes=overlay_data.get("constituent_prev_closes"),
            now_utc=overlay_data.get("now_utc"),
            trading_date=overlay_data.get("trading_date"),
            session_relationship=overlay_data.get("session_relationship", "behind"),
        )
    except Exception as exc:
        logger.warning("etf_predictor_detail overlay chart failed: %s", exc)

    etf_pnl = None
    try:
        from accounts_engine import get_combined_holdings
        position = get_combined_holdings().get(cfg["etf_ticker"])
        if position and prediction.get("status") == "success":
            shares = float(position.get("global_shares", 0))
            avg_buy = float(position.get("global_buy_price", 0))
            last_close = prediction.get("last_etf_close", 0)
            pred_price = prediction.get("predicted_price", 0)
            if shares > 0 and pred_price and last_close:
                predicted_value = shares * pred_price
                current_value = shares * last_close
                cost_basis = shares * avg_buy
                etf_pnl = {
                    "shares": round(shares, 4),
                    "avg_buy_price": round(avg_buy, 4),
                    "current_value": round(current_value, 2),
                    "predicted_value": round(predicted_value, 2),
                    "predicted_pnl_open": round(predicted_value - current_value, 2),
                    "total_unrealised_pnl": round(predicted_value - cost_basis, 2),
                }
    except Exception:
        pass

    return templates.TemplateResponse(
        request=request,
        name="etf_predictor_detail.html",
        context={
            "cfg": cfg,
            "etf_info": etf_info,
            "prediction": prediction,
            "correlation_chart_html": correlation_chart_html,
            "prediction_chart_html": prediction_chart_html,
            "contributions_chart_html": contributions_chart_html,
            "overlay_chart_html": overlay_chart_html,
            "etf_pnl": etf_pnl,
            "unread_count": get_unread_count(),
            "config": load_config(),
        },
    )


@page_router.get("/stress-test", response_class=HTMLResponse)
async def stress_test_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="stress_test.html",
        context={
            "unread_count": get_unread_count(),
            "config": load_config(),
        },
    )


@page_router.get("/ai-contagion", response_class=HTMLResponse)
async def ai_contagion_page(request: Request):
    from ai_contagion_engine import get_ai_contagion_data
    error_html = "<p class='error-text'>Data unavailable — please try again later.</p>"
    try:
        data = get_ai_contagion_data(days=30)
        daily_dfs = data["daily_dfs"]
        intraday_dfs = data["intraday_dfs"]

        perf_daily_html = create_ai_contagion_performance_chart(daily_dfs, period_label="30-Day")
        perf_intraday_html = create_ai_contagion_performance_chart(intraday_dfs, period_label="Intraday") if intraday_dfs else ""
        corr_html = create_ai_contagion_correlation_heatmap(daily_dfs, window=20)
    except Exception as exc:
        logger.error("ai_contagion_page failed: %s", exc)
        perf_daily_html = error_html
        perf_intraday_html = ""
        corr_html = error_html

    return templates.TemplateResponse(
        request=request,
        name="ai_contagion.html",
        context={
            "perf_daily_html": perf_daily_html,
            "perf_intraday_html": perf_intraday_html,
            "corr_html": corr_html,
            "unread_count": get_unread_count(),
        },
    )


@page_router.get("/score-history", response_class=HTMLResponse)
async def score_history_page(request: Request, filter: str = "all", ref: str = ""):
    from score_analysis import get_score_analysis
    valid_filters = {"all", "portfolio", "watchlist"}
    active_filter = filter if filter in valid_filters else "all"
    data = get_score_analysis(active_filter)
    return templates.TemplateResponse(
        request=request,
        name="score_history.html",
        context={
            "data": data,
            "active_filter": active_filter,
            "back_url": ref if ref else None,
            "unread_count": get_unread_count(),
            "config": load_config(),
        }
    )


@page_router.get("/stock/{ticker}", response_class=HTMLResponse)
async def stock_detail(request: Request, ticker: str, embed: bool = False, embed_token: str = ""):
    ticker = normalize_ticker(ticker)
    if ticker in get_index_tickers():
        return RedirectResponse(f"/index/{ticker}", status_code=302)
    is_in_watchlist = ticker in get_watchlist_tickers()

    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute('''
            SELECT s.*, p.business_summary,
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
                   q.atr_pct, q.volume, q.volume_surge, q.bullish_cross,
                   q.macd, q.macd_signal, q.macd_hist,
                   q.sma_50, q.sma_200,
                   q.mom_1m, q.mom_3m, q.mom_6m, q.mom_12m_skip1m,
                   q.hist_vol_20, q.rel_strength_5d, q.rel_strength_20d,
                   q.anomaly_score, q.close_price as quant_close_price,
                   q.vp_poc, q.vp_val, q.vp_vah, q.vp_entry_zone, q.vp_exit_zone,
                   q.kc_z_score, q.kc_entry_signal, q.kc_exit_signal,
                   q.price_q10, q.price_q90,
                   mu.industry, mu.index_membership,
                   cno.display_name as name_override,
                   tmeta.market_cap,
                   trap.phase as trap_phase,
                   (SELECT flag FROM bubble_radar_metrics
                    WHERE ticker = s.ticker ORDER BY scan_date DESC LIMIT 1) AS bubble_flag,
                   COALESCE(
                       cno.display_name,
                       NULLIF(p.company_name, s.ticker),
                       NULLIF(mu.company_name, s.ticker),
                       s.company_name,
                       s.ticker
                   ) as resolved_company_name
            FROM stock_signals s
            LEFT JOIN asset_profiles p ON s.ticker = p.ticker
            LEFT JOIN market_universe mu ON s.ticker = mu.ticker
            LEFT JOIN company_name_overrides cno ON s.ticker = cno.ticker
            LEFT JOIN quant_signals q ON s.ticker = q.ticker
                AND q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = s.ticker)
            LEFT JOIN ticker_metadata tmeta ON s.ticker = tmeta.ticker
            LEFT JOIN trap_monitor_results trap ON s.ticker = trap.ticker
            WHERE s.ticker = ?
        ''', (ticker,))
        stock_data = cursor.fetchone()

        if stock_data:
            stock_data = dict(stock_data)
            _cp = stock_data.get("current_price") or 0.0
            stock_data["trend_50d"] = "UP" if stock_data.get("sma_50") and _cp > stock_data["sma_50"] else "DOWN"
            stock_data["trend_200d"] = "UP" if stock_data.get("sma_200") and _cp > stock_data["sma_200"] else "DOWN"
            # Resolve best available display name — mutual funds often have no shortName
            # from yfinance; fall back through asset_profiles → market_universe
            stock_data['company_name'] = (
                stock_data.get('resolved_company_name')
                or stock_data.get('company_name')
                or ticker
            )
            stock_data['company_name'] = (
                stock_data['company_name']
                .replace(" - Common Stock", "")
                .replace(" Common Stock", "")
                .strip()
            )
            stock_data['has_name_override'] = bool(stock_data.get('name_override'))
            stock_data['pattern_detections'] = get_pattern_tags_by_ticker([ticker]).get(ticker, [])
            stock_data.update(compute_badge_tags(stock_data))
        else:
            cursor.execute('''
                SELECT q.*,
                       cno.display_name as name_override,
                       COALESCE(cno.display_name, p.company_name, m.company_name, q.ticker) as company_name,
                       COALESCE(p.sector, 'Unclassified') as sector,
                       COALESCE(p.currency, 'USD') as currency,
                       COALESCE(p.quote_type, 'EQUITY') as quote_type,
                       p.business_summary,
                       m.industry, m.index_membership
                FROM quant_signals q
                LEFT JOIN market_universe m ON q.ticker = m.ticker
                LEFT JOIN asset_profiles p ON q.ticker = p.ticker
                LEFT JOIN company_name_overrides cno ON q.ticker = cno.ticker
                WHERE q.ticker = ? ORDER BY q.date DESC LIMIT 1
            ''', (ticker,))
            q_data = cursor.fetchone()

            if q_data:
                q_data = dict(q_data)
                company_name = q_data.get("company_name") or ticker
                company_name = company_name.replace(" - Common Stock", "").replace(" Common Stock", "").strip()

                c_price = q_data.get("close_price")
                c_price = float(c_price) if c_price is not None else 0.0

                stock_data = {
                    "ticker": ticker,
                    "company_name": company_name,
                    "sector": q_data.get("sector") or "Unclassified",
                    "quote_type": q_data.get("quote_type") or "EQUITY",
                    "currency": q_data.get("currency") or "USD",
                    "current_price": c_price,
                    "overall_signal": "UNIVERSE SCAN ONLY",
                    "composite_score": "N/A",
                    "educational_notes": "This asset is part of the broader market universe scan. Add it to your Ghostfolio or Watchlist to trigger a deep, institutional fundamental evaluation.",
                    "business_summary": q_data.get("business_summary"),
                    "next_earnings_date": "Unknown",
                    "target_price": None,
                    "trend_50d": "UP" if q_data.get("sma_50") and c_price > q_data.get("sma_50") else "DOWN",
                    "trend_200d": "UP" if q_data.get("sma_200") and c_price > q_data.get("sma_200") else "DOWN",
                    "rsi_14": q_data.get("rsi_14"),
                    "atr_pct": q_data.get("atr_pct"),
                    "atr_stop_loss": None,
                    "last_updated": None,
                    "country": None,
                    "fifty_two_week_low": None,
                    "fifty_two_week_high": None,
                    "ma_50_day": q_data.get("sma_50"),
                    "ma_200_day": q_data.get("sma_200"),
                    "ml_confidence_score": q_data.get("ml_confidence_score"),
                    "var_95": q_data.get("var_95"),
                    "cvar_95": q_data.get("cvar_95"),
                    "sentiment_score": q_data.get("sentiment_score"),
                    "yield_correlation": None,
                    "trailing_pe": None,
                    "debt_to_equity": None,
                    "forward_pe": None,
                    "peg_ratio": None,
                    "peter_lynch_peg": None,
                    "price_to_book": None,
                    "profit_margin": None,
                    "roe": None,
                    "revenue_growth": None,
                    "current_ratio": None,
                    "operating_cash_flow": None,
                    "short_interest": None,
                    "institutional_ownership": None,
                    "beta": None,
                    "expense_ratio": None,
                    "ytd_return": None,
                    "total_assets": None,
                    "nav_price": None,
                    "dividend_yield": None,
                    "top_holdings": None,
                    "sector_weightings": None,
                    "volume": q_data.get("volume"),
                    "volume_surge": q_data.get("volume_surge"),
                    "bullish_cross": q_data.get("bullish_cross"),
                    "macd": q_data.get("macd"),
                    "macd_signal": q_data.get("macd_signal"),
                    "macd_hist": q_data.get("macd_hist"),
                    "sma_50": q_data.get("sma_50"),
                    "sma_200": q_data.get("sma_200"),
                    "mom_1m": q_data.get("mom_1m"),
                    "mom_3m": q_data.get("mom_3m"),
                    "mom_6m": q_data.get("mom_6m"),
                    "mom_12m_skip1m": q_data.get("mom_12m_skip1m"),
                    "hist_vol_20": q_data.get("hist_vol_20"),
                    "rel_strength_5d": q_data.get("rel_strength_5d"),
                    "rel_strength_20d": q_data.get("rel_strength_20d"),
                    "anomaly_score": q_data.get("anomaly_score"),
                    "industry": q_data.get("industry"),
                    "index_membership": q_data.get("index_membership"),
                    "has_name_override": bool(q_data.get("name_override")),
                    "vp_poc": q_data.get("vp_poc"),
                    "vp_val": q_data.get("vp_val"),
                    "vp_vah": q_data.get("vp_vah"),
                    "vp_entry_zone": q_data.get("vp_entry_zone"),
                    "vp_exit_zone": q_data.get("vp_exit_zone"),
                    "kc_z_score": q_data.get("kc_z_score"),
                    "kc_entry_signal": q_data.get("kc_entry_signal"),
                    "kc_exit_signal": q_data.get("kc_exit_signal"),
                    "price_q10": q_data.get("price_q10"),
                    "price_q90": q_data.get("price_q90"),
                }
            else:
                stock_data = {
                    "ticker": ticker,
                    "company_name": ticker,
                    "has_name_override": False,
                    "sector": "Unknown",
                    "quote_type": "UNKNOWN",
                    "currency": "USD",
                    "current_price": 0.0,
                    "overall_signal": "UNKNOWN",
                    "composite_score": "N/A",
                    "educational_notes": "Data not found. Asset may not be tracked.",
                    "business_summary": None,
                    "next_earnings_date": "Unknown",
                    "target_price": None,
                    "trend_50d": "N/A",
                    "trend_200d": "N/A",
                    "rsi_14": None,
                    "atr_pct": None,
                    "atr_stop_loss": None,
                    "last_updated": None,
                    "ml_confidence_score": None,
                    "var_95": None,
                    "cvar_95": None,
                    "sentiment_score": None,
                    "yield_correlation": None,
                    "trailing_pe": None,
                    "debt_to_equity": None,
                    "forward_pe": None,
                    "peg_ratio": None,
                    "peter_lynch_peg": None,
                    "price_to_book": None,
                    "profit_margin": None,
                    "roe": None,
                    "revenue_growth": None,
                    "current_ratio": None,
                    "operating_cash_flow": None,
                    "short_interest": None,
                    "institutional_ownership": None,
                    "beta": None,
                    "expense_ratio": None,
                    "ytd_return": None,
                    "total_assets": None,
                    "nav_price": None,
                    "dividend_yield": None,
                    "top_holdings": None,
                    "sector_weightings": None,
                    "volume": None, "volume_surge": None, "bullish_cross": None,
                    "macd": None, "macd_signal": None, "macd_hist": None,
                    "sma_50": None, "sma_200": None,
                    "mom_1m": None, "mom_3m": None, "mom_6m": None, "mom_12m_skip1m": None,
                    "hist_vol_20": None, "rel_strength_5d": None, "rel_strength_20d": None,
                    "anomaly_score": None,
                    "industry": None, "index_membership": None,
                    "vp_poc": None, "vp_val": None, "vp_vah": None,
                    "vp_entry_zone": None, "vp_exit_zone": None,
                    "kc_z_score": None, "kc_entry_signal": None, "kc_exit_signal": None,
                    "price_q10": None, "price_q90": None,
                }

        earnings_vol: dict = {}
        if stock_data:
            cursor.execute('''
                SELECT implied_move_pct, historical_avg_move_pct, edge_score, options_volume
                FROM earnings_volatility WHERE ticker = ?
            ''', (ticker,))
            ev_row = cursor.fetchone()
            if ev_row:
                earnings_vol = dict(ev_row)

    finally:
        conn.close()

    fundamentals_extra = _load_fundamentals_extra(ticker)

    data_status = 'red'
    last_updated_str = "Never"
    if stock_data and stock_data.get('last_updated'):
        last_updated_str = stock_data['last_updated']
        try:
            lu_date = datetime.strptime(last_updated_str, "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            if datetime.now(timezone.utc) - lu_date < timedelta(hours=24):
                data_status = 'green'
            else:
                data_status = 'yellow'
        except Exception:
            data_status = 'red'

    top_holdings = []
    sector_weightings = []
    if stock_data and stock_data.get('top_holdings'):
        try:
            top_holdings = json.loads(stock_data['top_holdings'])
        except Exception:
            logger.warning("Failed to parse top_holdings JSON for %s", ticker, exc_info=True)
    if stock_data and stock_data.get('sector_weightings'):
        try:
            sector_weightings = json.loads(stock_data['sector_weightings'])
        except Exception:
            logger.warning("Failed to parse sector_weightings JSON for %s", ticker, exc_info=True)

    days_to_earnings = None
    volatility_date = None
    if stock_data and stock_data.get('next_earnings_date') and stock_data['next_earnings_date'] != 'Unknown':
        try:
            e_date = datetime.strptime(stock_data['next_earnings_date'], '%Y-%m-%d').date()
            today = time_engine.now_local().date()
            days_to_earnings = (e_date - today).days
            volatility_date = (e_date - timedelta(days=7)).strftime('%Y-%m-%d')
        except Exception:
            logger.warning("Could not parse next_earnings_date for %s: %s", ticker, stock_data.get('next_earnings_date'))

    from accounts_engine import get_combined_holdings, current_price_map
    user_asset = get_combined_holdings().get(ticker)

    portfolio_math = None
    target_accounts = []
    holding_price_limits = {}
    if user_asset and stock_data and stock_data.get('current_price'):
        priced = current_price_map([ticker]).get(ticker)
        live_current_price = priced[0] if priced and priced[0] else stock_data['current_price']
        exchange_rate = get_rate_from_base(stock_data['currency'])
        price_in_pence = user_asset.get('price_in_pence', False)

        global_math = calculate_pnl(
            user_asset.get('global_shares', 0),
            user_asset.get('global_buy_price', 0),
            exchange_rate,
            live_current_price,
            price_in_pence,
        )
        account_maths = []
        for acc in user_asset.get('accounts', []):
            acc_m = calculate_pnl(
                acc.get('shares', 0),
                acc.get('buy_price', 0),
                exchange_rate,
                live_current_price,
                price_in_pence,
            )
            if acc_m:
                acc_m["name"] = acc.get("name", "Unknown Account")
                account_maths.append(acc_m)

            acc_raw_id = acc.get("id", "")
            if isinstance(acc_raw_id, str) and acc_raw_id.startswith("acct:"):
                target_accounts.append({
                    "account_id": int(acc_raw_id[len("acct:"):]),
                    "name": acc.get("name", "Unknown Account"),
                })

        if global_math:
            portfolio_math = {"global": global_math, "accounts": account_maths}

    if is_in_watchlist:
        from db_accounts import get_watchlist_account
        watchlist_account = get_watchlist_account()
        if watchlist_account:
            target_accounts.append({
                "account_id": watchlist_account["id"],
                "name": "Watchlist",
            })

    if target_accounts:
        from db_accounts import get_holding_price_limits_for_ticker
        holding_price_limits = get_holding_price_limits_for_ticker(ticker)

    from db_helpers import get_ticker_notes
    ticker_notes = get_ticker_notes(ticker)
    for note in ticker_notes:
        note["created_display"] = time_engine.fmt_datetime(
            datetime.strptime(note["created_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
        )
        note["updated_display"] = None
        if note.get("updated_at"):
            note["updated_display"] = time_engine.fmt_datetime(
                datetime.strptime(note["updated_at"], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
            )

    fx_breakdown = None
    if portfolio_math and stock_data and stock_data.get("currency") == "USD":
        now = datetime.now(timezone.utc)
        ytd_days = (now.date() - now.date().replace(month=1, day=1)).days or 1
        fx_breakdown = compute_fx_breakdown(ticker, ytd_days)

    price_action = None
    try:
        df_macro = pd.read_parquet(HISTORICAL_DIR / f"{ticker}.parquet")

        currency = stock_data.get('currency', 'USD') if stock_data else 'USD'
        if ticker.endswith('.L') or currency in ['GBp', 'GBP']:
            try:
                df_baseline = pd.read_parquet(HISTORICAL_DIR / "FTSE_BASELINE.parquet")
            except Exception:
                df_baseline = None
        else:
            try:
                df_baseline = pd.read_parquet(HISTORICAL_DIR / "SP500_BASELINE.parquet")
            except Exception:
                df_baseline = None

        macro_html = create_macro_chart(df_macro, df_baseline, ticker)

        if not df_macro.empty:
            last_day = df_macro.iloc[-1]
            prev_day = df_macro.iloc[-2] if len(df_macro) > 1 else last_day
            last_21 = df_macro.tail(21)

            P = (prev_day['High'] + prev_day['Low'] + prev_day['Close']) / 3
            price_action = {
                "day_low": last_day['Low'],
                "day_high": last_day['High'],
                "month_low": last_21['Low'].min(),
                "month_high": last_21['High'].max(),
                "s1": (P * 2) - prev_day['High'],
                "s2": P - (prev_day['High'] - prev_day['Low'])
            }
    except FileNotFoundError:
        df_macro = pd.DataFrame()
        macro_html = "<div class='chart-ph chart-ph--lg'><span class='chart-ph__icon'>📭</span><span class='chart-ph__title'>No historical data yet</span><span class='chart-ph__hint'>Press <strong>Refresh</strong> above to fetch price history for this asset.</span></div>"
    except Exception as e:
        df_macro = pd.DataFrame()
        macro_html = f"<div class='chart-ph chart-ph--lg chart-ph--gap-sm'><span class='chart-ph__icon'>⚠️</span><span class='chart-ph__title'>Chart unavailable</span><span class='chart-ph__hint'>{type(e).__name__}: {e}</span></div>"

    live_pattern_name = live_pattern_tooltip = live_pattern_score = None
    try:
        df_intraday = pd.read_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet")
        if not df_intraday.empty and not df_macro.empty and len(df_macro) >= 2:
            curr_pseudo = pd.Series({
                'Open': df_intraday['Open'].iloc[0],
                'High': df_intraday['High'].max(),
                'Low': df_intraday['Low'].min(),
                'Close': df_intraday['Close'].iloc[-1]
            })
            live_patterns = get_candlestick_patterns(df_macro.iloc[-2], df_macro.iloc[-1], curr_pseudo)
            if live_patterns:
                live_pattern_name = live_patterns[0]["name"]
                live_pattern_tooltip = live_patterns[0]["tooltip"]
                live_pattern_score = live_patterns[0]["score"]

        s1_val = price_action['s1'] if price_action else None
        s2_val = price_action['s2'] if price_action else None
        mkt_tz = intraday_market_tz(ticker, currency)
        delay_min = EXCHANGE_DELAYS.get(currency, 0)
        intraday_html = create_intraday_chart(
            df_intraday, ticker, s1=s1_val, s2=s2_val,
            live_pattern_name=live_pattern_name,
            live_pattern_tooltip=live_pattern_tooltip,
            live_pattern_score=live_pattern_score,
            market_tz=mkt_tz,
            data_delay_minutes=delay_min,
        )
    except FileNotFoundError:
        intraday_html = "<div class='chart-ph chart-ph--sm'><span class='chart-ph__icon'>📭</span><span class='chart-ph__title'>No intraday data yet</span><span class='chart-ph__hint'>Press <strong>Refresh</strong> above to fetch today's intraday data.</span></div>"
    except Exception:
        intraday_html = "<div class='chart-ph chart-ph--sm chart-ph--gap-sm'><span class='chart-ph__icon'>⚠️</span><span class='chart-ph__title'>Intraday data unavailable</span></div>"

    config_data = load_config()
    fake_rows = [{"currency": stock_data.get("currency", "USD")}]
    position_sizing_context = _build_position_sizing_context(config_data, fake_rows)
    anomaly_chart_html = (
        "<div class='chart-ph chart-ph--lg'>"
        "<span class='chart-ph__icon'>📊</span>"
        "<span class='chart-ph__title'>No anomaly data yet</span>"
        "<span class='chart-ph__hint'>Scores are written during market hours once models are trained.</span>"
        "</div>"
    )
    anomaly_percentile = None
    anomaly_radar_html = None
    try:
        conn_a = get_connection()
        anomaly_rows = conn_a.execute(
            "SELECT date, anomaly_score, close_price FROM quant_signals "
            "WHERE ticker = ? AND anomaly_score IS NOT NULL "
            "ORDER BY date DESC LIMIT 90",
            (ticker,),
        ).fetchall()
        conn_a.close()
        if anomaly_rows:
            df_anomaly = pd.DataFrame(
                [(r["date"], r["anomaly_score"], r["close_price"]) for r in anomaly_rows],
                columns=["date", "anomaly_score", "close_price"],
            )
            df_anomaly["date"] = pd.to_datetime(df_anomaly["date"])
            df_anomaly.set_index("date", inplace=True)
            df_anomaly.sort_index(inplace=True)  # DESC fetch → re-sort ASC for chart
            anomaly_threshold = float(
                config_data.get("NOTIFICATIONS", {}).get("ANOMALY_ALERTS", {}).get("THRESHOLD", 0.7)
            )
            anomaly_chart_html = create_anomaly_score_chart(df_anomaly, ticker, threshold=anomaly_threshold)

            latest_score = df_anomaly["anomaly_score"].iloc[-1]
            history = df_anomaly["anomaly_score"]
            anomaly_percentile = round(float((history <= latest_score).mean() * 100), 1)

            current_price = stock_data.get("current_price") or 0.0
            sma_50 = stock_data.get("sma_50") or current_price
            sma50_dist_pct = ((current_price - sma_50) / sma_50 * 100) if sma_50 else 0.0
            radar_features = {
                "volume_ratio":     stock_data.get("volume_surge") or 1.0,
                "rsi_14":           stock_data.get("rsi_14") or 50.0,
                "daily_return_pct": stock_data.get("mom_1m") or 0.0,
                "sma50_dist_pct":   sma50_dist_pct,
                "hist_vol_20":      stock_data.get("hist_vol_20") or 0.2,
                "beta":             stock_data.get("beta") or 1.0,
            }
            anomaly_radar_html = create_anomaly_feature_radar(radar_features, ticker)
    except Exception:
        pass  # fallback placeholder already set

    is_dip_monitored = False
    conn_dip = None
    try:
        conn_dip = get_connection()
        _today = datetime.now(timezone.utc).date().isoformat()
        dip_row = conn_dip.execute(
            "SELECT 1 FROM intraday_monitors WHERE ticker = ? AND is_active = 1 AND expire_date >= ?",
            (ticker, _today),
        ).fetchone()
        is_dip_monitored = bool(dip_row)
    except Exception:
        pass
    finally:
        if conn_dip:
            conn_dip.close()

    bubble_data = None
    try:
        from bubble_radar_engine import get_bubble_ticker_detail
        bubble_data = get_bubble_ticker_detail(ticker)
    except Exception:
        pass

    ticker_risk = None
    conn_risk = None
    try:
        conn_risk = get_connection()
        row = conn_risk.execute(
            "SELECT * FROM ticker_risk_contribution WHERE ticker = ?", (ticker,)
        ).fetchone()
        ticker_risk = dict(row) if row else None
    finally:
        if conn_risk:
            conn_risk.close()

    from score_analysis import compute_regime_weighted_score, evaluate_pillar_confluence
    pillar_confluence = evaluate_pillar_confluence(ticker)
    regime_weighted = compute_regime_weighted_score(ticker)

    return templates.TemplateResponse(
        request=request, name="stock_detail.html",
        context={
            "stock": stock_data,
            "top_holdings": top_holdings,
            "sector_weightings": sector_weightings,
            "macro_html": macro_html,
            "intraday_html": intraday_html,
            "anomaly_chart_html": anomaly_chart_html,
            "anomaly_radar_html": anomaly_radar_html,
            "anomaly_percentile": anomaly_percentile,
            "portfolio_math": portfolio_math,
            "target_accounts": target_accounts,
            "holding_price_limits": holding_price_limits,
            "ticker_notes": ticker_notes,
            "fx_breakdown": fx_breakdown,
            "days_to_earnings": days_to_earnings,
            "volatility_date": volatility_date,
            "price_action": price_action,
            "unread_count": get_unread_count(),
            "embed": embed,
            "embed_token": embed_token,
            "config": load_config(),
            "cached_pulse": get_all_cached_pulse(),
            "is_in_watchlist": is_in_watchlist,
            "is_dip_monitored": is_dip_monitored,
            "data_status": data_status,
            "last_updated_str": last_updated_str,
            "position_sizing": position_sizing_context,
            "earnings_vol": earnings_vol,
            "fundamentals_extra": fundamentals_extra,
            "bubble_data": bubble_data,
            "ticker_risk": ticker_risk,
            "pillar_confluence": pillar_confluence,
            "regime_weighted_score": regime_weighted,
            "risk_paused": get_all_scope_heat_tier() == "RED",
        }
    )


def _build_rss_base_url(server_url: str, port: int) -> str:
    base = str(server_url).rstrip('/')
    parsed = urlparse(base if "://" in base else f"http://{base}")
    hostname = parsed.hostname or ""
    is_local = hostname == "localhost"
    if not is_local:
        try:
            ipaddress.ip_address(hostname)
            is_local = True
        except ValueError:
            pass
    if is_local and parsed.port is None:
        return f"{base}:{port}"
    return base


@page_router.get("/rss/alerts.xml")
async def rss_alerts_feed():
    cfg = load_config()
    if not cfg.get("NOTIFICATIONS", {}).get("RSS_FEED", {}).get("ENABLED", False):
        return Response(status_code=404)

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT id, message_type, message_text, timestamp FROM system_notifications "
            "WHERE message_type IN ('Crash', 'Moonshot') ORDER BY id DESC LIMIT 50"
        )
        rows = cursor.fetchall()
    finally:
        if conn:
            conn.close()

    base_url = _build_rss_base_url(
        cfg.get("SERVER_URL", "http://localhost"),
        cfg.get("PORT", 8090)
    )
    now_str = email.utils.formatdate(usegmt=True)

    items = []
    for row in rows:
        try:
            dt = datetime.strptime(row["timestamp"][:19], "%Y-%m-%d %H:%M:%S")
            pub_date = dt.replace(tzinfo=timezone.utc).strftime("%a, %d %b %Y %H:%M:%S +0000")
        except Exception:
            pub_date = now_str

        msg_type = row["message_type"]
        msg_text = row["message_text"] or ""

        m = re.search(r"triggered for ([A-Z0-9.\-\^=]+)\.", msg_text)
        ticker = m.group(1) if m else "Unknown"

        title = html_escape(f"{msg_type} Alert — {ticker}")
        desc = html_escape(msg_text.replace("**", ""))
        link = html_escape(f"{base_url}/stock/{ticker}")

        items.append(
            f"    <item>\n"
            f"      <title>{title}</title>\n"
            f"      <description>{desc}</description>\n"
            f"      <link>{link}</link>\n"
            f"      <pubDate>{pub_date}</pubDate>\n"
            f"      <guid isPermaLink=\"false\">alert-{row['id']}</guid>\n"
            f"    </item>"
        )

    feed_xml = (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<rss version="2.0">\n'
        '  <channel>\n'
        '    <title>Quantamental Dashboard &#8212; Crash &amp; Moonshot Alerts</title>\n'
        f'    <link>{html_escape(base_url)}</link>\n'
        '    <description>Real-time intraday crash and moonshot alerts from your portfolio scanner</description>\n'
        f'    <lastBuildDate>{now_str}</lastBuildDate>\n'
        + ("\n".join(items) + "\n" if items else "")
        + '  </channel>\n'
        '</rss>'
    )

    return Response(content=feed_xml, media_type="application/rss+xml")


@page_router.get("/log-viewer", response_class=HTMLResponse)
async def log_viewer_page(request: Request):
    cfg = load_config()
    fl = cfg.get("FILE_LOGGING", {})
    logging_enabled = fl.get("ENABLED", False)
    return templates.TemplateResponse(
        request=request,
        name="log_viewer.html",
        context={"logging_enabled": logging_enabled},
    )


page_router.include_router(page_router_macro)
