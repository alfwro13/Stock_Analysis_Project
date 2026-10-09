import email.utils
import ipaddress
import logging
import os
import re
from pathlib import Path

import markdown as _markdown
import pandas as pd

logger = logging.getLogger(__name__)
from datetime import datetime, timezone
from html import escape as html_escape
from urllib.parse import urlparse

from fastapi import APIRouter, Query, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from config import load_config
import time_engine
from database import get_connection, get_watchlist_tickers
from market_pulse import get_all_cached_pulse
from utils import normalize_ticker
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
from fx_drag_engine import portfolio_lifetime_fx_breakdown
from constants import PREDICTION_HORIZON_DAYS, PREDICTION_RETURN_THRESHOLD, CSS_VERSION
from page_helpers import get_unread_count, _utc_str_to_local
from page_routes_macro import page_router_macro
from page_routes_portfolio import page_router_portfolio
from page_routes_stock import page_router_stock

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
    from scheduler_engine import CONFIG_KEY_TO_JOB, job_label
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
            "scheduler_job_ids": CONFIG_KEY_TO_JOB,
            "scheduler_job_label": job_label,
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
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM system_notifications ORDER BY timestamp DESC LIMIT 100")
        rows = cursor.fetchall()
    finally:
        if conn:
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

    from accounts_engine import get_combined_holdings
    portfolio_tickers = {str(t).upper() for t in get_combined_holdings().keys()}
    watchlist_tickers = {str(t).upper() for t in get_watchlist_tickers()}

    return templates.TemplateResponse(
        request=request,
        name="earnings_volatility.html",
        context={
            "earnings_data": earnings_data,
            "portfolio_tickers": portfolio_tickers,
            "watchlist_tickers": watchlist_tickers,
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
def markets_page(request: Request):
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


@page_router.get("/sector-relative-momentum", response_class=HTMLResponse)
async def sector_relative_momentum_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="sector_relative_momentum.html",
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


@page_router.get("/strategy-backtester", response_class=HTMLResponse)
async def strategy_backtester_page(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="strategy_backtester.html",
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
        detect_etf_info, run_prediction, fetch_shared_prediction_data,
        get_etf_correlation_data, get_etf_intraday_overlay_data,
    )
    cfg = get_etf_predictor_config(config_id)
    if cfg is None:
        return RedirectResponse("/etf-predictor", status_code=302)

    error_html = "<p class='error-text'>Data unavailable — please try again later.</p>"
    etf_info = detect_etf_info(cfg["etf_ticker"])
    constituent_tickers = [h["ticker"] for h in cfg["constituents"]]

    # Fetched once here (off the event loop) and reused by run_prediction/correlation/overlay
    # below instead of each independently re-fetching the same tickers.
    try:
        daily_df, intraday_data = await run_in_threadpool(fetch_shared_prediction_data, cfg)
    except Exception as exc:
        logger.warning("etf_predictor_detail shared fetch failed: %s", exc)
        daily_df, intraday_data = None, None

    try:
        prediction = await run_in_threadpool(run_prediction, config_id, daily_df, intraday_data)
    except Exception as exc:
        logger.error("etf_predictor_detail run_prediction failed: %s", exc)
        prediction = {"status": "error", "error": str(exc), "predicted_price": None}

    correlation_chart_html = error_html
    prediction_chart_html = error_html
    contributions_chart_html = ""
    overlay_chart_html = error_html

    try:
        corr_data = await run_in_threadpool(get_etf_correlation_data, cfg, 60, daily_df)
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
        overlay_data = await run_in_threadpool(
            get_etf_intraday_overlay_data, cfg, prediction, intraday_data, daily_df
        )
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
        data = await run_in_threadpool(get_ai_contagion_data, 30)
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
page_router.include_router(page_router_portfolio)
page_router.include_router(page_router_stock)
