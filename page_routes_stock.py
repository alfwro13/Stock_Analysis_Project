from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates

from config import load_config
from database import get_watchlist_tickers
from market_pulse import get_all_cached_pulse, get_index_tickers
from utils import normalize_ticker, time_request_call
from constants import CSS_VERSION
from page_helpers import _load_fundamentals_extra, get_unread_count, get_all_scope_heat_tier
from page_data_stock import data_freshness, load_stock_row, parse_json_field
from page_data_stock_panels import (
    anomaly_panel,
    bubble_detail,
    earnings_timing,
    fx_breakdown_for,
    is_dip_monitored,
    position_sizing_for,
    position_targets,
    price_chart_panels,
    score_panels,
    ticker_notes_for_display,
    ticker_risk_row,
)

page_router_stock = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["css_version"] = CSS_VERSION


@page_router_stock.get("/stock/{ticker}", response_class=HTMLResponse)
def stock_detail(request: Request, ticker: str, embed: bool = False, embed_token: str = ""):
    ticker = normalize_ticker(ticker)
    registry_ticker = next((t for t in get_index_tickers() if t == ticker), None)
    if registry_ticker:
        return RedirectResponse(f"/index/{registry_ticker}", status_code=302)
    is_in_watchlist = ticker in get_watchlist_tickers()

    stock_data, earnings_vol = load_stock_row(ticker)
    fundamentals_extra = _load_fundamentals_extra(ticker)
    data_status, last_updated_str = data_freshness(stock_data)
    top_holdings = parse_json_field(stock_data, 'top_holdings', ticker)
    sector_weightings = parse_json_field(stock_data, 'sector_weightings', ticker)
    days_to_earnings, volatility_date = earnings_timing(stock_data, ticker)

    from db_helpers import get_latest_resolved_earnings_drift
    latest_resolved_drift = get_latest_resolved_earnings_drift(ticker)

    from db_etf import get_etf_predictor_config_by_ticker
    etf_predictor_config = get_etf_predictor_config_by_ticker(ticker)

    from accounts_engine import get_combined_holdings
    user_asset = get_combined_holdings().get(ticker)
    portfolio_math, target_accounts, holding_price_limits = position_targets(ticker, stock_data, user_asset, is_in_watchlist)
    ticker_notes = ticker_notes_for_display(ticker)
    fx_breakdown = fx_breakdown_for(ticker, stock_data, portfolio_math)

    charts = price_chart_panels(ticker, stock_data.get("currency", "USD"))

    config_data = load_config()
    position_sizing_context = position_sizing_for(config_data, stock_data, user_asset)
    anomaly_chart_html, anomaly_percentile, anomaly_radar_html = anomaly_panel(ticker, stock_data, config_data)
    pillar_confluence, regime_weighted, buy_recommendation = score_panels(ticker)

    return time_request_call("template", templates.TemplateResponse,
        request=request, name="stock_detail.html",
        context={
            "stock": stock_data,
            "top_holdings": top_holdings,
            "sector_weightings": sector_weightings,
            "macro_html": charts["macro_html"],
            "intraday_html": charts["intraday_html"],
            "intraday_revision": charts["intraday_revision"],
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
            "price_action": charts["price_action"],
            "unread_count": get_unread_count(),
            "embed": embed,
            "embed_token": embed_token,
            "config": load_config(),
            "cached_pulse": get_all_cached_pulse(),
            "is_in_watchlist": is_in_watchlist,
            "is_dip_monitored": is_dip_monitored(ticker),
            "data_status": data_status,
            "last_updated_str": last_updated_str,
            "position_sizing": position_sizing_context,
            "earnings_vol": earnings_vol,
            "latest_resolved_drift": latest_resolved_drift,
            "fundamentals_extra": fundamentals_extra,
            "bubble_data": bubble_detail(ticker),
            "ticker_risk": ticker_risk_row(ticker),
            "pillar_confluence": pillar_confluence,
            "regime_weighted_score": regime_weighted,
            "buy_recommendation": buy_recommendation,
            "risk_paused": get_all_scope_heat_tier() == "RED",
            "etf_predictor_config": etf_predictor_config,
        }
    )
