# api_routes.py
import asyncio
import os
import time_engine
import shutil
import sqlite3
import json
import time
import signal
import subprocess
import joblib
import pandas as pd
import logging
import requests
from datetime import datetime, timezone, timedelta
from typing import Any, List, Optional
from pathlib import Path

from fastapi import APIRouter, Request, BackgroundTasks, HTTPException, Query, Path as PathParam, Response
from fastapi.responses import JSONResponse, PlainTextResponse, StreamingResponse
from api_deps import limiter, require_confirm_token, _error_500
from pydantic import BaseModel, Field

from log_config import configure_file_logging
from config import (
    load_config,
    update_config_atomic,
    SECRETS_PATH,
    DATA_DIR,
    BASE_DIR,
    DB_PATH,
    PORTFOLIO_PATH,
    FUNDAMENTALS_DIR,
    HISTORICAL_DIR,
    INTRADAY_DIR
)
from database import (
    get_connection, get_universe_tickers, get_watchlist_account, add_watchlist_item, remove_watchlist_ticker,
)
from db_helpers import add_ticker_note, update_ticker_note, delete_ticker_note, get_all_ticker_notes_grouped, get_company_names
from accounts_engine import resolve_watchlist_metadata, list_scope_accounts_with_values, _ticker_known, _has_stock_signals_row
from scheduler_engine import run_update_pipeline, run_ghostfolio_sync, run_freetrade_sync, reload_scheduler, run_sentiment_scan, run_index_scraper, run_fundamentals_profiler, run_universe_deep_sync_job, get_all_job_last_runs, run_xray_risk_cache_job, run_anomaly_training_job, record_job_run, run_maintenance_engine, build_workflow_graph, detect_workflow_conflicts, CONFIG_KEY_TO_JOB
from maintenance_engine import MaintenanceEngine
from xray_engine import assemble_xray_report
from performance_analytics_engine import assemble_performance_report
from fx_drag_engine import portfolio_fx_breakdown, portfolio_lifetime_fx_breakdown
from market_pulse import get_cached_pulse_from_db, fetch_and_save_pulse
from sentiment_engine import run_nextcloud_alert
from huggingface_engine import update_all_sentiment
from earnings_engine import run_earnings_alert
from insider_engine import run_insider_alert
from ai_engine import AIPromptEngine
from ai_regime_engine import AIRegimePromptEngine
from ai_sentiment_engine import AISentimentPromptEngine
from news_feed_engine import run_news_feed_job
from intraday_bottom_engine import IntradayBottomEngine
from data_engine import DataEngine, fetch_and_save_single_ticker
import glossary_learn_engine
from utils import normalize_ticker
from quant_signals import QuantEngine
from quant_engine import run_daily_quant_scan
from fundamentals_helpers import compute_quality_grade, get_earnings_days
from earnings_vol_engine import run_earnings_vol_scan
from universe_engine import update_market_universe
from reports_engine import get_sector_trends, get_mean_reversion_setups, get_leaders_laggards, get_dividend_harvest_setups, get_quality_compounders, get_garp_tenbaggers, get_quality_on_sale
from options_engine import fetch_options_chain, calculate_payoff_matrix
from ai_prediction_engine import train_global_ml_model, update_daily_ml_predictions, run_historical_backfill
from risk_engine import update_all_tail_risks
from profile_engine import get_profiler_queue_breakdown, update_single_profile
from tools.network_engine import GLOBAL_IPV6_STATUS
from yahoo_engine import yahoo_engine
# Import curl_cffi for resilient IPv6 socket testing
from curl_cffi import requests as cffi_requests
from macro_calendar_engine import update_macro_calendar
from macro_data_engine import update_macro_indicators
from macro_ai_engine import MacroAIEngine
from visuals import create_intraday_chart, intraday_market_tz, EXCHANGE_DELAYS
from quant_signals import get_candlestick_patterns
from monte_carlo_engine import run_simulation as _run_mc_simulation
from portfolio_optimizer_engine import list_candidates as _po_list_candidates, optimize_portfolio as _po_optimize_portfolio

logger = logging.getLogger(__name__)

api_router = APIRouter(prefix="/api")



class TickerRequest(BaseModel):
    ticker: str

class DipRadarAddRequest(BaseModel):
    ticker: str
    days: int = 1

class OptionLeg(BaseModel):
    type: str
    strike: float
    premium: float
    position: str
    quantity: int = 1

class PayoffRequest(BaseModel):
    current_price: float
    legs: List[OptionLeg]

class NameOverrideRequest(BaseModel):
    display_name: str

class TickerNoteRequest(BaseModel):
    note_text: str = Field(..., min_length=1, max_length=1000)


@api_router.post("/watchlist/add")
async def api_watchlist_add(req: TickerRequest, background_tasks: BackgroundTasks):
    ticker = normalize_ticker(req.ticker)
    wl = get_watchlist_account()
    if wl is None:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Watchlist account not found."})
    meta = await asyncio.to_thread(resolve_watchlist_metadata, ticker)
    item_id = await asyncio.to_thread(
        add_watchlist_item, wl["id"], ticker, meta["company_name"], meta["currency"], meta["quote_type"], meta["exchange"]
    )
    if item_id is not None:
        if not await asyncio.to_thread(_ticker_known, ticker):
            background_tasks.add_task(update_single_profile, ticker)
            background_tasks.add_task(fetch_and_save_single_ticker, ticker)
        if not await asyncio.to_thread(_has_stock_signals_row, ticker):
            background_tasks.add_task(QuantEngine().analyze_ticker, ticker)
        return JSONResponse(content={"status": "success"})
    return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to add to watchlist."})

@api_router.post("/watchlist/remove")
async def api_watchlist_remove(req: TickerRequest):
    ticker = normalize_ticker(req.ticker)
    wl = get_watchlist_account()
    if wl is None:
        return JSONResponse(status_code=500, content={"status": "error", "message": "Watchlist account not found."})
    removed = await asyncio.to_thread(remove_watchlist_ticker, wl["id"], ticker)
    if removed:
        return JSONResponse(content={"status": "success"})
    return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to remove from watchlist."})

@api_router.post("/data/refresh-single")
async def api_data_refresh_single(req: TickerRequest):
    try:
        update_single_profile(req.ticker)
        data_engine = DataEngine()
        quant_engine = QuantEngine()
        if not data_engine.fetch_and_save_data(req.ticker):
            return JSONResponse(status_code=500, content={"status": "error", "message": "Data fetch failed."})
        quant_engine.analyze_ticker(req.ticker)
        target_list = [req.ticker]
        update_daily_ml_predictions(target_list)
        update_all_tail_risks(target_list)
        update_all_sentiment(target_list)
        return JSONResponse(content={"status": "success"})
    except Exception as e:
        logger.exception("refresh-single failed for %s", req.ticker)
        return _error_500(e)


@api_router.post("/ticker/{ticker}/name-override")
async def api_set_name_override(ticker: str, req: NameOverrideRequest):
    ticker = normalize_ticker(ticker)
    conn = None
    try:
        conn = get_connection()
        name = req.display_name.strip()
        if name:
            conn.execute(
                "INSERT OR REPLACE INTO company_name_overrides (ticker, display_name, updated_at) VALUES (?, ?, ?)",
                (ticker, name, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"))
            )
        else:
            conn.execute("DELETE FROM company_name_overrides WHERE ticker = ?", (ticker,))
        conn.commit()
        return JSONResponse(content={"status": "success"})
    except Exception as e:
        return _error_500(e)
    finally:
        if conn:
            conn.close()


@api_router.post("/ticker/{ticker}/notes")
async def api_add_ticker_note(ticker: str, req: TickerNoteRequest):
    ticker = normalize_ticker(ticker)
    try:
        note_id = add_ticker_note(ticker, req.note_text.strip())
        if note_id is None:
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to save note."})
        return JSONResponse(content={"status": "success", "id": note_id})
    except Exception as e:
        logger.exception("add_ticker_note failed for %s", ticker)
        return _error_500(e)


@api_router.put("/ticker/{ticker}/notes/{note_id}")
async def api_update_ticker_note(ticker: str, note_id: int, req: TickerNoteRequest):
    ticker = normalize_ticker(ticker)
    try:
        updated = update_ticker_note(note_id, ticker, req.note_text.strip())
        if not updated:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Note not found."})
        return JSONResponse(content={"status": "success"})
    except Exception as e:
        logger.exception("update_ticker_note failed for id %s", note_id)
        return _error_500(e)


@api_router.delete("/ticker/{ticker}/notes/{note_id}")
async def api_delete_ticker_note(ticker: str, note_id: int):
    ticker = normalize_ticker(ticker)
    try:
        deleted = delete_ticker_note(note_id, ticker)
        if not deleted:
            return JSONResponse(status_code=404, content={"status": "error", "message": "Note not found."})
        return JSONResponse(content={"status": "success"})
    except Exception as e:
        logger.exception("delete_ticker_note failed for id %s", note_id)
        return _error_500(e)


@api_router.get("/ticker-notes")
async def api_get_all_ticker_notes():
    try:
        entries = get_all_ticker_notes_grouped()
        company_names = get_company_names([e["ticker"] for e in entries])
        for entry in entries:
            entry["company_name"] = company_names.get(entry["ticker"])
        return JSONResponse(content={"status": "success", "tickers": entries})
    except Exception as e:
        logger.exception("get_all_ticker_notes_grouped failed")
        return _error_500(e)


@api_router.post("/index/refresh")
async def api_index_refresh(req: TickerRequest):
    ticker = normalize_ticker(req.ticker)
    try:
        fetch_and_save_pulse([ticker])
        data_engine = DataEngine()
        quant_engine = QuantEngine()
        if not data_engine.fetch_and_save_data(ticker):
            return JSONResponse(status_code=500, content={"status": "error", "message": "Data fetch failed."})
        quant_engine.analyze_ticker(ticker)
        target_list = [ticker]
        update_all_tail_risks(target_list)
        update_all_sentiment(target_list)
        return JSONResponse(content={"status": "success"})
    except Exception as e:
        logger.exception("index/refresh failed for %s", ticker)
        return _error_500(e)


@api_router.get("/options/chain/{ticker}")
@limiter.limit("10/minute")
async def api_options_chain(request: Request, ticker: str = PathParam(..., pattern=r"^[A-Z0-9.\-\^=]{1,20}$")):
    data = fetch_options_chain(ticker)
    if "error" in data:
        return JSONResponse(status_code=400, content=data)
    return JSONResponse(content=data)

@api_router.post("/options/payoff")
async def api_options_payoff(req: PayoffRequest):
    try:
        legs_dict = [leg.model_dump() for leg in req.legs]
        matrix = calculate_payoff_matrix(legs_dict, req.current_price)
        return JSONResponse(content=matrix)
    except (ValueError, ZeroDivisionError) as e:
        return JSONResponse(status_code=422, content={"status": "error", "message": str(e)})
    except Exception as e:
        logger.exception("Payoff matrix calculation failed")
        return _error_500(e)

class GlossaryLearnAnswer(BaseModel):
    term_key: str
    grade: str


@api_router.get("/learn/overview")
@limiter.limit("30/minute")
async def api_learn_overview(request: Request):
    try:
        return JSONResponse(content={"status": "success", **glossary_learn_engine.overview()})
    except Exception as e:
        logger.exception("learn/overview failed")
        return _error_500(e)


@api_router.post("/learn/session")
@limiter.limit("15/minute")
async def api_learn_session(request: Request, size: int = Query(10, ge=1, le=30), section_id: Optional[str] = Query(None)):
    try:
        cards = glossary_learn_engine.build_session(size=size, section_id=section_id)
        return JSONResponse(content={"status": "success", "cards": cards})
    except Exception as e:
        logger.exception("learn/session failed")
        return _error_500(e)


@api_router.post("/learn/answer")
@limiter.limit("120/minute")
async def api_learn_answer(request: Request, body: GlossaryLearnAnswer):
    if body.grade not in ("good", "hard", "fail"):
        return JSONResponse(status_code=400, content={"status": "error", "message": "invalid grade"})
    try:
        result = glossary_learn_engine.get_answer(body.term_key, body.grade)
        return JSONResponse(content={"status": "success", **result})
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
    except Exception as e:
        logger.exception("learn/answer failed")
        return _error_500(e)


@api_router.get("/screener-data")
@limiter.limit("20/minute")
async def get_screener_data(request: Request):
    conn = None
    try:
        config_data = load_config()
        freetrade_only = config_data.get("UI_PREFERENCES", {}).get("FREETRADE_ONLY_MODE", False)

        conn = get_connection()
        cursor = conn.cursor()
        query = """
        WITH latest_quant AS (
            SELECT ticker, MAX(date) AS max_date
            FROM quant_signals
            GROUP BY ticker
        ),
        latest_sentiment AS (
            SELECT ticker, MAX(date) AS max_date
            FROM quant_signals
            WHERE sentiment_score IS NOT NULL
            GROUP BY ticker
        )
        SELECT
            q.ticker,
            COALESCE(p.company_name, s.company_name, m.company_name, q.ticker) as company_name,
            COALESCE(p.sector, s.sector, 'Unclassified') as sector,
            CASE
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NMS' THEN 'NASDAQ'
                WHEN UPPER(COALESCE(m.exchange, p.exchange)) = 'NYQ' THEN 'NYSE/AMEX'
                WHEN COALESCE(m.exchange, p.exchange) IS NOT NULL THEN UPPER(COALESCE(m.exchange, p.exchange))
                WHEN UPPER(q.ticker) LIKE '%.L' THEN 'LSE'
                ELSE 'US'
            END as exchange,
            COALESCE(p.currency, s.currency, 'USD') as currency,
            q.date, q.close_price,
            q.volume, q.rsi_14, q.macd_hist, q.sma_50, q.sma_200,
            q.volume_surge, q.bullish_cross,
            q.ml_confidence_score, q.var_95, q.cvar_95, q.atr_pct,
            q.week52_pct,
            qs_sent.sentiment_score,
            s.composite_score, s.overall_signal,
            s.roe, s.peg_ratio, s.trailing_pe, s.debt_to_equity, s.expense_ratio,
            COALESCE(s.next_earnings_date, ev.next_earnings_date) AS next_earnings_date,
            m.is_freetrade, m.freetrade_subtitle, m.freetrade_url,
            COALESCE(p.quote_type, s.quote_type, m.quote_type, 'EQUITY') as quote_type
        FROM quant_signals q
        INNER JOIN latest_quant lq ON q.ticker = lq.ticker AND q.date = lq.max_date
        INNER JOIN market_universe m ON q.ticker = m.ticker
        LEFT JOIN asset_profiles p ON q.ticker = p.ticker
        LEFT JOIN stock_signals s ON q.ticker = s.ticker
        LEFT JOIN latest_sentiment ls ON q.ticker = ls.ticker
        LEFT JOIN quant_signals qs_sent
            ON qs_sent.ticker = ls.ticker
            AND qs_sent.date = ls.max_date
            AND qs_sent.sentiment_score IS NOT NULL
        LEFT JOIN earnings_volatility ev ON q.ticker = ev.ticker
        """
        if freetrade_only:
            query += " WHERE m.is_freetrade = 1"

        cursor.execute(query)
        rows = cursor.fetchall()
        today_str = time_engine.now_local().strftime('%Y-%m-%d')
        data = []
        for row in rows:
            r = dict(row)
            r['quality_grade'] = compute_quality_grade(r)
            r['earnings_days'] = get_earnings_days(r, today_str)
            data.append(r)
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.error("Failed to fetch screener data: %s", e, exc_info=True)
        return JSONResponse(status_code=500, content={"error": "An internal error occurred. Check server logs for details.", "data": []})
    finally:
        if conn:
            conn.close()

@api_router.get("/reports/quality-compounders")
@limiter.limit("10/minute")
async def api_reports_quality_compounders(request: Request):
    try:
        data = get_quality_compounders()
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("quality-compounders report failed")
        return _error_500(e)

@api_router.get("/reports/quality-on-sale")
@limiter.limit("10/minute")
async def api_reports_quality_on_sale(request: Request):
    try:
        data = get_quality_on_sale()
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("quality-on-sale report failed")
        return _error_500(e)

@api_router.get("/reports/garp-tenbaggers")
@limiter.limit("10/minute")
async def api_reports_garp_tenbaggers(request: Request):
    try:
        data = get_garp_tenbaggers()
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("garp-tenbaggers report failed")
        return _error_500(e)

@api_router.get("/reports/sectors")
@limiter.limit("10/minute")
async def api_reports_sectors(request: Request):
    try:
        data = get_sector_trends()
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("sectors report failed")
        return _error_500(e)

@api_router.get("/reports/mean-reversion")
@limiter.limit("10/minute")
async def api_reports_mean_reversion(
    request: Request,
    max_rsi: float = Query(default=30.0, ge=0.0, le=100.0),
    min_sma_distance: float = Query(default=0.0, ge=0.0),
):
    try:
        data = get_mean_reversion_setups(max_rsi=max_rsi, min_sma_distance=min_sma_distance)
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("mean-reversion report failed")
        return _error_500(e)

@api_router.get("/reports/leaders")
@limiter.limit("10/minute")
async def api_reports_leaders(request: Request):
    try:
        data = get_leaders_laggards()
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("leaders report failed")
        return _error_500(e)

@api_router.get("/reports/dividends")
@limiter.limit("10/minute")
async def api_reports_dividends(
    request: Request,
    min_yield: float = Query(default=0.02, ge=0.0, le=1.0),
    min_score: int   = Query(default=50,   ge=0,   le=100),
):
    try:
        data = get_dividend_harvest_setups(min_yield=min_yield, min_score=min_score)
        return JSONResponse(content={"data": data})
    except Exception as e:
        logger.exception("dividends report failed")
        return _error_500(e)


@api_router.get("/freshness")
async def get_data_freshness():
    """Returns model-file age and price-data age, with pre-computed CSS state classes."""
    from constants import (
        FRESHNESS_MODEL_WARN_DAYS, FRESHNESS_MODEL_STALE_DAYS,
        FRESHNESS_PRICES_WARN_DAYS, FRESHNESS_PRICES_STALE_DAYS,
    )
    today = time_engine.now_local().date()

    def _state(days_ago: int, warn: int, stale: int) -> str:
        if days_ago >= stale: return "freshness-stale"
        if days_ago >= warn:  return "freshness-warn"
        return "freshness-fresh"

    # Model file staleness
    model_path = BASE_DIR / "models" / "ml_ensemble.joblib"
    if model_path.exists():
        mtime        = time_engine.to_local(datetime.fromtimestamp(model_path.stat().st_mtime, tz=timezone.utc)).date()
        model_days   = (today - mtime).days
        model_date   = mtime.strftime('%Y-%m-%d')
        model_state  = _state(model_days, FRESHNESS_MODEL_WARN_DAYS, FRESHNESS_MODEL_STALE_DAYS)
    else:
        model_days, model_date, model_state = None, None, "freshness-stale"

    # Price data freshness (latest row in quant_signals)
    conn = get_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT MAX(date) AS max_date FROM quant_signals")
        row = cursor.fetchone()
    finally:
        conn.close()

    if row and row["max_date"]:
        prices_d     = datetime.strptime(row["max_date"], "%Y-%m-%d").date()
        prices_days  = (today - prices_d).days
        prices_date  = row["max_date"]
        prices_state = _state(prices_days, FRESHNESS_PRICES_WARN_DAYS, FRESHNESS_PRICES_STALE_DAYS)
    else:
        prices_days, prices_date, prices_state = None, None, "freshness-stale"

    return JSONResponse(content={
        "model_date":    model_date,
        "model_days_ago": model_days,
        "model_state":   model_state,
        "prices_date":   prices_date,
        "prices_days_ago": prices_days,
        "prices_state":  prices_state,
    })


@api_router.get("/xray")
@limiter.limit("10/minute")
async def get_xray_report(request: Request, account_id: str = "all"):
    """
    Returns the full Portfolio X-ray report JSON for the given account scope.
    account_id: "all" for every configured source (Ghostfolio + built-in Trading accounts,
    combined); a Ghostfolio account UUID for that Ghostfolio account only; or "acct:{id}"
    for one built-in Trading account only.

    Combines live Ghostfolio holdings/allocations and/or built-in account holdings with
    SQLite-cached risk stats (beta, vol, correlation, VaR). The cache is populated by the
    nightly xray_risk_cache_job scheduler job. Historical VaR/CVaR, Sharpe/Calmar ratio,
    tracking error and skewness are derived at request time from per-ticker cached daily
    returns and work for any scope (Ghostfolio, built-in, or combined) — they are omitted
    with a data_warning only when fewer than 30 overlapping cached trading days exist yet.
    """
    try:
        report = assemble_xray_report(account_id)
        return JSONResponse(content=report)
    except RuntimeError as e:
        logger.warning(f"X-ray report failed for account_id={account_id!r}: {e}")
        return JSONResponse(status_code=503, content={"error": str(e)})
    except Exception as e:
        logger.error(f"X-ray report unexpected error: {e}")
        return JSONResponse(status_code=500, content={"error": "Internal error — check server logs."})


@api_router.post("/xray/trigger")
async def trigger_xray_risk_cache(background_tasks: BackgroundTasks):
    """
    Manually triggers the X-ray risk cache pre-compute job in the background.
    Use this after first setup or after adding new holdings to immediately
    populate the cache without waiting for the scheduled run.
    """
    background_tasks.add_task(run_xray_risk_cache_job)
    return JSONResponse(content={
        "status": "queued",
        "message": "X-ray risk cache job queued. Check system notifications for completion.",
    })


@api_router.get("/fx-drag")
async def get_fx_drag(
    request: Request,
    period: str = Query(default="ytd", pattern=r"^(ytd|1y|2y|lifetime)$"),
):
    if period == "lifetime":
        data = portfolio_lifetime_fx_breakdown()
    else:
        now = datetime.now(timezone.utc)
        if period == "ytd":
            days = (now.date() - now.date().replace(month=1, day=1)).days or 1
        elif period == "1y":
            days = 365
        else:
            days = 730
        data = portfolio_fx_breakdown(days)
    return JSONResponse(content={"status": "success", "period": period, "data": data})


@api_router.get("/news-feed")
async def get_news_feed(
    request: Request,
    source: str = Query(default="all", pattern=r"^(all|portfolio|watchlist|both)$"),
    limit: int = Query(default=50, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
):
    conn = get_connection()
    try:
        cursor = conn.cursor()
        if source == "all":
            cursor.execute(
                "SELECT COUNT(*) as total FROM news_articles"
            )
            total = cursor.fetchone()["total"]
            cursor.execute(
                """SELECT id, article_id, ticker, company_name, source_list,
                          headline, summary, full_text, body_fetched,
                          url, publisher, published_at, fetched_at,
                          sentiment_score, sentiment_label
                   FROM news_articles
                   ORDER BY published_at DESC
                   LIMIT ? OFFSET ?""",
                (limit, offset),
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) as total FROM news_articles WHERE source_list IN (?, 'both')",
                (source,),
            )
            total = cursor.fetchone()["total"]
            cursor.execute(
                """SELECT id, article_id, ticker, company_name, source_list,
                          headline, summary, full_text, body_fetched,
                          url, publisher, published_at, fetched_at,
                          sentiment_score, sentiment_label
                   FROM news_articles
                   WHERE source_list IN (?, 'both')
                   ORDER BY published_at DESC
                   LIMIT ? OFFSET ?""",
                (source, limit, offset),
            )
        articles = [dict(row) for row in cursor.fetchall()]
        return JSONResponse(content={"articles": articles, "total": total})
    except Exception as e:
        logger.error("GET /api/news-feed failed: %s", e, exc_info=True)
        return JSONResponse(status_code=500, content={"error": "An internal error occurred. Check server logs for details."})
    finally:
        conn.close()


@api_router.post("/news-feed/run-now")
async def run_news_feed_now(background_tasks: BackgroundTasks):
    background_tasks.add_task(run_news_feed_job)
    return JSONResponse(content={
        "status": "success",
        "message": "News feed fetch queued. New articles will appear shortly.",
    })


# ---------------------------------------------------------------------------
# Intraday Dip Radar endpoints
# ---------------------------------------------------------------------------

@api_router.post("/intraday-monitor/add")
async def intraday_monitor_add(req: DipRadarAddRequest):
    ticker = req.ticker.upper().strip()
    if not ticker:
        raise HTTPException(status_code=400, detail="ticker is required")
    days = max(1, min(30, req.days or 1))
    today = datetime.now(timezone.utc).date()
    today_str = today.isoformat()
    expire_date = (today + timedelta(days=days - 1)).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """INSERT INTO intraday_monitors (ticker, date_added, expire_date, is_active, activated_by)
               VALUES (?, ?, ?, 1, 'user')
               ON CONFLICT(ticker) DO UPDATE SET
                   date_added   = excluded.date_added,
                   expire_date  = excluded.expire_date,
                   is_active    = 1,
                   activated_by = 'user'""",
            (ticker, today_str, expire_date),
        )
        conn.commit()
        _cur_row = conn.execute(
            "SELECT currency FROM stock_signals WHERE ticker = ?", (ticker,)
        ).fetchone()
        _currency = _cur_row["currency"] if _cur_row else ""
    finally:
        conn.close()
    engine = IntradayBottomEngine()
    await asyncio.to_thread(engine.arm_alert, ticker)
    from database import log_notification
    _exch = time_engine.ticker_exchange(ticker, _currency)
    if days == 1:
        _reset_str = time_engine.fmt_reset_time(_exch)
        log_notification("DipRadar", f"🎯 Dip Radar enabled for {ticker} — scanning every 2 min until {_reset_str}. You will be notified if a bottoming zone is detected.")
    else:
        expire_display = (today + timedelta(days=days - 1)).strftime("%b %d")
        log_notification("DipRadar", f"🎯 Dip Radar enabled for {ticker} — scanning every 2 min during market hours for {days} days (until {expire_display}).")
    return JSONResponse(content={"status": "ok", "ticker": ticker, "expire_date": expire_date})


@api_router.post("/intraday-monitor/remove")
async def intraday_monitor_remove(req: TickerRequest):
    ticker = req.ticker.upper().strip()
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE intraday_monitors SET is_active = 0 WHERE ticker = ?", (ticker,)
        )
        conn.commit()
    finally:
        conn.close()
    return JSONResponse(content={"status": "ok", "ticker": ticker})


@api_router.get("/intraday-monitor/list")
async def intraday_monitor_list():
    today = datetime.now(timezone.utc).date().isoformat()
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT ticker, date_added, expire_date, is_active FROM intraday_monitors WHERE is_active = 1 AND expire_date >= ? ORDER BY ticker",
            (today,),
        ).fetchall()
        return JSONResponse(content={"monitors": [dict(r) for r in rows]})
    finally:
        conn.close()


@api_router.get("/intraday-monitor/analysis/{ticker}")
async def intraday_monitor_analysis(ticker: str = PathParam(..., pattern=r"^[A-Z0-9.\-\^=]{1,20}$")):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM intraday_monitor_results WHERE ticker = ?", (ticker.upper(),)
        ).fetchone()
        if not row:
            return JSONResponse(content=None)
        data = dict(row)
        data["reasons"] = json.loads(data.get("reasons_json") or "[]")
        data.pop("reasons_json", None)
        if "vol_climax" in data and data["vol_climax"] is not None:
            data["vol_climax"] = bool(data["vol_climax"])
        return JSONResponse(content=data)
    finally:
        conn.close()


@api_router.get("/intraday-monitor/summary")
async def intraday_monitor_summary():
    today = datetime.now(timezone.utc).date().isoformat()
    conn = get_connection()
    try:
        rows = conn.execute(
            """
            SELECT m.ticker, m.date_added, m.expire_date, m.activated_by,
                   r.scan_ts, r.current_price, r.reversal_score,
                   r.is_bottoming, r.rsi, r.bb_lower,
                   r.vwap, r.vwap_lower, r.vwap_deviation, r.vol_climax
            FROM intraday_monitors m
            LEFT JOIN intraday_monitor_results r ON m.ticker = r.ticker
            WHERE m.is_active = 1 AND m.expire_date >= ?
            ORDER BY COALESCE(r.reversal_score, -1) DESC
            """,
            (today,),
        ).fetchall()
        result = []
        for row in rows:
            d = dict(row)
            if d.get("vol_climax") is not None:
                d["vol_climax"] = bool(d["vol_climax"])
            result.append(d)
        return JSONResponse(content={"monitors": result, "session_date": today})
    finally:
        conn.close()


def _intraday_gap_notice_html(ticker: str) -> str:
    """Prepended to the intraday chart HTML when yahoo_engine has a persistent (>=30 min) empty-fetch
    streak for this ticker — otherwise a stale cached chart renders with no indication it isn't live."""
    if not yahoo_engine.is_intraday_gap_alerted(ticker):
        return ""
    return (
        "<div class='box-warning mb-2'>⚠️ Yahoo Finance currently has no fresh intraday data for "
        f"{ticker} — the chart below may be showing the last available data, not live.</div>"
    )


@api_router.get("/intraday-chart/{ticker}")
async def get_intraday_chart(ticker: str = PathParam(..., pattern=r"^[A-Z0-9.\-\^=]{1,20}$")):
    """Return freshly rendered intraday chart HTML for a given ticker."""
    ticker = ticker.upper()
    s1 = s2 = None
    df_macro = pd.DataFrame()

    # Derive exchange metadata for timezone + delay
    conn_meta = None
    try:
        conn_meta = get_connection()
        row = conn_meta.execute(
            "SELECT currency FROM stock_signals WHERE ticker = ? LIMIT 1", (ticker,)
        ).fetchone()
        currency = row["currency"] if row else "USD"
    except Exception:
        currency = "USD"
    finally:
        if conn_meta:
            conn_meta.close()

    mkt_tz = intraday_market_tz(ticker, currency)
    delay_min = EXCHANGE_DELAYS.get(currency, 0)

    try:
        df_macro = pd.read_parquet(HISTORICAL_DIR / f"{ticker}.parquet")
        if not df_macro.empty and len(df_macro) > 1:
            prev_day = df_macro.iloc[-2]
            P = (prev_day['High'] + prev_day['Low'] + prev_day['Close']) / 3
            s1 = P * 2 - prev_day['High']
            s2 = P - (prev_day['High'] - prev_day['Low'])
    except Exception:
        pass

    try:
        df_intraday = pd.read_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet")
    except FileNotFoundError:
        html = "<div class='intraday-placeholder'><span class='intraday-placeholder-icon'>📭</span><span class='intraday-placeholder-label'>No intraday data yet</span></div>"
        return JSONResponse(content={"html": _intraday_gap_notice_html(ticker) + html})
    except Exception:
        html = "<div class='intraday-placeholder intraday-placeholder--error'><span class='intraday-placeholder-icon'>⚠️</span><span class='intraday-placeholder-label'>Intraday data unavailable</span></div>"
        return JSONResponse(content={"html": _intraday_gap_notice_html(ticker) + html})

    live_pattern_name = live_pattern_tooltip = live_pattern_score = None
    try:
        if not df_intraday.empty and not df_macro.empty and len(df_macro) >= 2:
            curr_pseudo = pd.Series({
                'Open': df_intraday['Open'].iloc[0],
                'High': df_intraday['High'].max(),
                'Low': df_intraday['Low'].min(),
                'Close': df_intraday['Close'].iloc[-1],
            })
            live_patterns = get_candlestick_patterns(df_macro.iloc[-2], df_macro.iloc[-1], curr_pseudo)
            if live_patterns:
                live_pattern_name = live_patterns[0]["name"]
                live_pattern_tooltip = live_patterns[0]["tooltip"]
                live_pattern_score = live_patterns[0]["score"]
    except Exception:
        pass

    html = create_intraday_chart(
        df_intraday, ticker, s1=s1, s2=s2,
        live_pattern_name=live_pattern_name,
        live_pattern_tooltip=live_pattern_tooltip,
        live_pattern_score=live_pattern_score,
        include_plotlyjs=False,
        market_tz=mkt_tz,
        data_delay_minutes=delay_min,
    )
    return JSONResponse(content={"html": _intraday_gap_notice_html(ticker) + html})


@api_router.post("/intraday-chart/refresh")
async def refresh_intraday_chart(req: TickerRequest):
    """Fetch fresh intraday data from Yahoo Finance, persist to parquet, return re-rendered chart HTML."""
    ticker = req.ticker.upper()

    conn_meta = None
    try:
        conn_meta = get_connection()
        row = conn_meta.execute(
            "SELECT currency FROM stock_signals WHERE ticker = ? LIMIT 1", (ticker,)
        ).fetchone()
        currency = row["currency"] if row else "USD"
    except Exception:
        currency = "USD"
    finally:
        if conn_meta:
            conn_meta.close()

    try:
        with yahoo_engine._lock:
            yahoo_engine._cache.pop(f"intraday:{ticker}:1d:5m:", None)
        result = yahoo_engine.get_intraday([ticker], period="1d", interval="5m")
        df_fetched = result.get(ticker, pd.DataFrame())
        if not df_fetched.empty:
            if df_fetched.index.tz is not None:
                df_fetched.index = df_fetched.index.tz_convert(None)
            df_fetched.to_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet", engine="pyarrow")
    except Exception:
        pass

    s1 = s2 = None
    df_macro = pd.DataFrame()
    try:
        df_macro = pd.read_parquet(HISTORICAL_DIR / f"{ticker}.parquet")
        if not df_macro.empty and len(df_macro) > 1:
            prev_day = df_macro.iloc[-2]
            P = (prev_day["High"] + prev_day["Low"] + prev_day["Close"]) / 3
            s1 = P * 2 - prev_day["High"]
            s2 = P - (prev_day["High"] - prev_day["Low"])
    except Exception:
        pass

    try:
        df_intraday = pd.read_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet")
    except FileNotFoundError:
        html = "<div class='intraday-placeholder'><span class='intraday-placeholder-icon'>📭</span><span class='intraday-placeholder-label'>No intraday data yet</span></div>"
        return JSONResponse(content={"html": _intraday_gap_notice_html(ticker) + html})
    except Exception:
        html = "<div class='intraday-placeholder intraday-placeholder--error'><span class='intraday-placeholder-icon'>⚠️</span><span class='intraday-placeholder-label'>Intraday data unavailable</span></div>"
        return JSONResponse(content={"html": _intraday_gap_notice_html(ticker) + html})

    mkt_tz = intraday_market_tz(ticker, currency)
    delay_min = EXCHANGE_DELAYS.get(currency, 0)

    live_pattern_name = live_pattern_tooltip = live_pattern_score = None
    try:
        if not df_intraday.empty and not df_macro.empty and len(df_macro) >= 2:
            curr_pseudo = pd.Series({
                "Open": df_intraday["Open"].iloc[0],
                "High": df_intraday["High"].max(),
                "Low": df_intraday["Low"].min(),
                "Close": df_intraday["Close"].iloc[-1],
            })
            live_patterns = get_candlestick_patterns(df_macro.iloc[-2], df_macro.iloc[-1], curr_pseudo)
            if live_patterns:
                live_pattern_name = live_patterns[0]["name"]
                live_pattern_tooltip = live_patterns[0]["tooltip"]
                live_pattern_score = live_patterns[0]["score"]
    except Exception:
        pass

    html = create_intraday_chart(
        df_intraday, ticker, s1=s1, s2=s2,
        live_pattern_name=live_pattern_name,
        live_pattern_tooltip=live_pattern_tooltip,
        live_pattern_score=live_pattern_score,
        include_plotlyjs=False,
        market_tz=mkt_tz,
        data_delay_minutes=delay_min,
    )
    return JSONResponse(content={"html": _intraday_gap_notice_html(ticker) + html})

def _active_log_path() -> Path | None:
    cfg = load_config()
    fl = cfg.get("FILE_LOGGING", {})
    if not fl.get("ENABLED", False):
        return None
    log_dir = BASE_DIR / fl.get("LOG_DIR", "logs")
    p = log_dir / "app.log"
    return p if p.exists() else None


@api_router.get("/logs/tail")
async def logs_tail(lines: int = Query(default=500, ge=1, le=5000), full: bool = Query(default=False)):
    p = _active_log_path()
    if p is None:
        return JSONResponse({"status": "error", "message": "File logging is disabled or log file not found."})
    try:
        with open(p, "r", encoding="utf-8", errors="replace") as f:
            all_lines = f.readlines()
        selected = all_lines if full else all_lines[-lines:]
        tail = [ln.rstrip("\n") for ln in selected]
        return JSONResponse({"status": "success", "lines": tail})
    except Exception as e:
        logger.error("logs/tail failed: %s", e, exc_info=True)
        return JSONResponse(status_code=500, content={"status": "error", "message": "An internal error occurred. Check server logs for details."})


@api_router.get("/logs/stream")
async def logs_stream():
    p = _active_log_path()
    if p is None:
        async def _disabled():
            yield "data: {\"error\": \"File logging is disabled or log file not found.\"}\n\n"
        return StreamingResponse(_disabled(), media_type="text/event-stream")

    async def _tail_f():
        import json as _json
        try:
            with open(p, "r", encoding="utf-8", errors="replace") as f:
                f.seek(0, 2)
                while True:
                    chunk = f.read(65536)
                    if chunk:
                        for line in chunk.splitlines():
                            line = line.strip()
                            if line:
                                yield f"data: {_json.dumps(line)}\n\n"
                    else:
                        yield ": keep-alive\n\n"
                        await asyncio.sleep(1)
        except Exception as e:
            logger.error("logs/stream failed: %s", e)

    return StreamingResponse(
        _tail_f(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


class MonteCarloRequest(BaseModel):
    portfolio_value: float = Field(gt=0)
    monthly_contribution: float = Field(default=0.0, ge=0)
    horizon_years: int = Field(ge=1, le=50)
    target_wealth: float = Field(default=0.0, ge=0)
    drift_overrides: dict = {}
    inflation_pct: float = Field(default=2.5, ge=0)


@api_router.get("/monte-carlo/accounts")
@limiter.limit("10/minute")
async def api_monte_carlo_accounts(request: Request):
    try:
        accounts, total = list_scope_accounts_with_values()
        if not accounts:
            return JSONResponse(content={"status": "error", "message": "No accounts with holdings configured."})
        return JSONResponse(content={"status": "success", "accounts": accounts, "total": total})
    except Exception as e:
        return _error_500(e)


@api_router.get("/performance-analytics/accounts")
@limiter.limit("10/minute")
async def api_performance_analytics_accounts(request: Request):
    try:
        accounts, total = list_scope_accounts_with_values()
        if not accounts:
            return JSONResponse(content={"status": "error", "message": "No accounts with holdings configured."})
        return JSONResponse(content={"status": "success", "accounts": accounts, "total": total})
    except Exception as e:
        return _error_500(e)


@limiter.limit("10/minute")
@api_router.post("/monte-carlo/run")
async def api_monte_carlo_run(request: Request, req: MonteCarloRequest):
    try:
        result = await asyncio.to_thread(
            _run_mc_simulation,
            req.portfolio_value,
            req.monthly_contribution,
            req.horizon_years,
            req.target_wealth,
            req.drift_overrides,
            req.inflation_pct,
        )
        return JSONResponse(content=result)
    except Exception as e:
        return _error_500(e)


@api_router.get("/performance-analytics/report")
@limiter.limit("10/minute")
async def api_performance_analytics_report(request: Request, account_id: str = "all"):
    """
    Returns the Portfolio Tearsheet report JSON for the given account scope: Sortino/Calmar/
    Omega ratios, drawdown duration analytics, distribution/tail stats, win/loss stats, and
    chart data (underwater, cumulative growth vs. benchmark, monthly heatmap, histogram).
    Native computation from the same per-ticker cached returns xray_engine uses — no DB writes.
    """
    try:
        report = assemble_performance_report(account_id)
        return JSONResponse(content=report)
    except Exception as e:
        return _error_500(e)


class PortfolioOptimizerRunRequest(BaseModel):
    account_id: str = "all"
    include_tickers: List[str] = []


@api_router.get("/portfolio-optimizer/accounts")
@limiter.limit("10/minute")
async def api_portfolio_optimizer_accounts(request: Request):
    try:
        accounts, total = list_scope_accounts_with_values()
        if not accounts:
            return JSONResponse(content={"status": "error", "message": "No accounts with holdings configured."})
        return JSONResponse(content={"status": "success", "accounts": accounts, "total": total})
    except Exception as e:
        return _error_500(e)


@api_router.get("/portfolio-optimizer/candidates")
@limiter.limit("10/minute")
async def api_portfolio_optimizer_candidates(request: Request, account_id: str = "all"):
    """Held tickers (pre-checked) + full Watchlist ticker list (opt-in) for the candidate checklist."""
    try:
        result = _po_list_candidates(account_id)
        return JSONResponse(content=result)
    except Exception as e:
        return _error_500(e)


@api_router.post("/portfolio-optimizer/run")
@limiter.limit("10/minute")
async def api_portfolio_optimizer_run(request: Request, req: PortfolioOptimizerRunRequest):
    """
    Closed-form (numpy-only) Min-Variance / Max-Sharpe suggested weights for the given account
    scope plus any opted-in Watchlist tickers — informational only, no order execution.
    """
    try:
        report = await asyncio.to_thread(_po_optimize_portfolio, req.account_id, req.include_tickers)
        return JSONResponse(content=report)
    except Exception as e:
        return _error_500(e)


# --- Sub-router registrations ---
from api_routes_auth import auth_router
from api_routes_triggers import triggers_router
from api_routes_system import system_router
from api_routes_analysis import analysis_router
from api_routes_accounts import accounts_router

api_router.include_router(auth_router)
api_router.include_router(triggers_router)
api_router.include_router(system_router)
api_router.include_router(analysis_router)
api_router.include_router(accounts_router)
