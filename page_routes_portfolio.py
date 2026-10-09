from fastapi import APIRouter, BackgroundTasks, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import load_config, BASE_CURRENCY, ACCOUNT_CURRENCIES
from database import get_watchlist_tickers
from macro_data_engine import yield_gdp_link
from market_pulse import get_all_cached_pulse
from utils import ignored_tickers_set, measure_request_stage, time_request_call
from portfolio_service import get_fx_cache_status
import table_columns_helpers
from constants import CSS_VERSION
from page_helpers import (
    _build_position_sizing_context,
    get_unread_count,
    get_all_scope_heat_tier,
    get_portfolio_heat_row,
)
from page_data_signal_rows import fetch_portfolio_signal_rows, fetch_watchlist_signal_rows
from page_data_portfolio import (
    build_portfolio_rows,
    build_watchlist_rows,
    finalize_portfolio_rows,
    format_portfolio_summary,
    portfolio_account_options,
    portfolio_present_tags,
    portfolio_scope_tickers,
    watchlist_present_tags,
    watchlist_score_buckets,
)
from page_data_accounts import house_account_context, pension_account_context, trading_account_context

page_router_portfolio = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["css_version"] = CSS_VERSION


@page_router_portfolio.get("/portfolio", response_class=HTMLResponse)
def portfolio_page(request: Request, background_tasks: BackgroundTasks, account_id: str = "all", embed: bool = False, embed_token: str = "", xray: bool = False):
    from xray_engine import BENCHMARK_SYMBOL
    from accounts_engine import current_price_map, get_combined_holdings
    from api_routes_accounts import maybe_trigger_price_refresh
    from db_accounts import get_all_holding_price_limits
    from price_history_helpers import get_period_anchor_closes, CHANGE_PERIODS

    config_data = load_config()
    portfolio_json = get_combined_holdings()
    portfolio_tickers = portfolio_scope_tickers(portfolio_json, account_id, ignored_tickers_set(config_data))

    with measure_request_stage("sql"):
        db_rows, macro_regime, global_updated = fetch_portfolio_signal_rows(BENCHMARK_SYMBOL, portfolio_tickers)
    with measure_request_stage("fx_context"):
        position_sizing_context = _build_position_sizing_context(config_data, db_rows)

    account_options = portfolio_account_options(config_data)
    portfolio_data = build_portfolio_rows(db_rows, portfolio_tickers)
    present_tags, present_pattern_tags = portfolio_present_tags(portfolio_data)

    live_pulse = get_all_cached_pulse()

    maybe_trigger_price_refresh(background_tasks)
    price_map = current_price_map(list(set(portfolio_tickers)))

    with measure_request_stage("history_anchors"):
        anchor_closes = get_period_anchor_closes(list(set(portfolio_tickers)), cache_only=True)

    change_period = request.cookies.get("portfolio_change_period", "1d")
    if change_period not in CHANGE_PERIODS:
        change_period = "1d"
    show_extended = request.cookies.get("portfolio_show_extended", "false") == "true"

    totals = finalize_portfolio_rows(
        portfolio_data,
        portfolio_json=portfolio_json,
        account_id=account_id,
        price_map=price_map,
        live_pulse=live_pulse,
        anchor_closes=anchor_closes,
        change_period=change_period,
        position_sizing_context=position_sizing_context,
        all_holding_limits=get_all_holding_price_limits(),
    )

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
            "summary_math": format_portfolio_summary(totals),
            "fx_incomplete": totals["missing_fx"],
            "config": config_data,
            "cached_pulse": live_pulse,
            "macro_regime": macro_regime,
            "gdp_link": yield_gdp_link(macro_regime),
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
    from database import get_account, get_watchlist_items

    acc = get_account(account_id)
    if acc is None:
        return RedirectResponse("/accounts", status_code=302)

    if acc["account_type"] == "Pension":
        return RedirectResponse(f"/accounts/{acc['id']}/pension", status_code=302)

    if acc["account_type"] == "House":
        return RedirectResponse(f"/accounts/{acc['id']}/house", status_code=302)

    if acc["account_type"] == "Watchlist":
        return templates.TemplateResponse(
            request=request, name="watchlist_account_detail.html",
            context={
                "account": acc,
                "items": get_watchlist_items(acc["id"]),
                "unread_count": get_unread_count(),
            }
        )

    return templates.TemplateResponse(
        request=request, name="account_detail.html",
        context=trading_account_context(acc, request.cookies.get("acct_chart_period", "max"))
    )


@page_router_portfolio.get("/accounts/{account_id}/pension", response_class=HTMLResponse)
def pension_account_detail_page(request: Request, account_id: int):
    from database import get_account

    acc = get_account(account_id)
    if acc is None or acc["account_type"] != "Pension":
        return RedirectResponse("/accounts", status_code=302)

    return templates.TemplateResponse(
        request=request, name="account_detail_pension.html", context=pension_account_context(acc)
    )


@page_router_portfolio.get("/accounts/{account_id}/house", response_class=HTMLResponse)
def house_account_detail_page(request: Request, account_id: int):
    from database import get_account

    acc = get_account(account_id)
    if acc is None or acc["account_type"] != "House":
        return RedirectResponse("/accounts", status_code=302)

    return templates.TemplateResponse(
        request=request, name="account_detail_house.html", context=house_account_context(acc)
    )


@page_router_portfolio.get("/watchlist", response_class=HTMLResponse)
def watchlist_page(request: Request, embed: bool = False, embed_token: str = ""):
    from xray_engine import BENCHMARK_SYMBOL
    from db_accounts import get_all_holding_price_limits, get_watchlist_account
    from price_history_helpers import get_period_anchor_closes, CHANGE_PERIODS

    with measure_request_stage("sql"):
        db_rows, global_updated = fetch_watchlist_signal_rows(BENCHMARK_SYMBOL)

    watchlist_tickers = get_watchlist_tickers()

    with measure_request_stage("history_anchors"):
        anchor_closes = get_period_anchor_closes(list(set(watchlist_tickers)), cache_only=True)

    change_period = request.cookies.get("watchlist_change_period", "1d")
    if change_period not in CHANGE_PERIODS:
        change_period = "1d"

    cached_pulse = get_all_cached_pulse()

    watchlist_data = build_watchlist_rows(
        db_rows,
        watchlist_tickers,
        watchlist_account_id=get_watchlist_account()['id'],
        all_holding_limits=get_all_holding_price_limits(),
        cached_pulse=cached_pulse,
        anchor_closes=anchor_closes,
        change_period=change_period,
    )
    present_tags, present_pattern_tags = watchlist_present_tags(watchlist_data)

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
            "sectors": sorted({row['sector'] or 'Unclassified' for row in watchlist_data}),
            "present_signals": {row['overall_signal'].upper() for row in watchlist_data if row.get('overall_signal')},
            "present_tags": present_tags,
            "present_pattern_tags": present_pattern_tags,
            "present_score_buckets": watchlist_score_buckets(watchlist_data),
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
