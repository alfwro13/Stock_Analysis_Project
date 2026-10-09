import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from starlette.concurrency import run_in_threadpool

from config import load_config
from visuals_etf import (
    create_etf_correlation_chart,
    create_etf_prediction_chart,
    create_etf_contributions_chart,
    create_etf_overlay_chart,
)
from constants import CSS_VERSION
from page_helpers import get_unread_count

logger = logging.getLogger(__name__)

page_router_etf = APIRouter()
templates = Jinja2Templates(directory="templates")
templates.env.globals["css_version"] = CSS_VERSION


@page_router_etf.get("/etf-predictor", response_class=HTMLResponse)
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


@page_router_etf.get("/etf-predictor/{config_id}", response_class=HTMLResponse)
async def etf_predictor_detail_page(request: Request, config_id: int):
    from database import get_etf_predictor_config
    from etf_predictor_engine import (
        detect_etf_info, run_prediction, fetch_shared_prediction_data,
        get_etf_correlation_data, get_etf_intraday_overlay_data, get_etf_recent_closes,
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
        etf_hist = await run_in_threadpool(get_etf_recent_closes, cfg["etf_ticker"], daily_df)
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
    except Exception as exc:
        logger.warning("etf_predictor_detail pnl failed: %s", exc)

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
