import logging
import pandas as pd
from datetime import datetime, timezone
from pathlib import Path
from typing import List

import time_engine
from fastapi import APIRouter, BackgroundTasks, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from api_deps import limiter, _error_500, require_confirm_token

from config import BASE_DIR, DATA_DIR, update_config_atomic
from database import get_connection, get_universe_tickers
from scheduler_engine import (
    run_update_pipeline, run_ghostfolio_sync, run_freetrade_sync,
    reload_scheduler, run_sentiment_scan, run_index_scraper,
    run_fundamentals_profiler, run_universe_deep_sync_job,
    run_xray_risk_cache_job, run_anomaly_training_job, record_job_run,
    run_maintenance_engine,
)
from maintenance_engine import MaintenanceEngine
from backup_engine import get_backup_status, restore_backup, run_backup
from ghostfolio_sync import GhostfolioSyncEngine
from report_dispatcher import push_morning_quant_briefing, push_lunchtime_quant_briefing
from data_engine import DataEngine
from quant_engine import run_daily_quant_scan
from earnings_vol_engine import run_earnings_vol_scan
from universe_engine import update_market_universe
from ai_prediction_engine import train_global_ml_model, update_daily_ml_predictions, run_historical_backfill
from risk_engine import update_all_tail_risks
from profile_engine import get_profiler_queue_breakdown
from seed_macro_calendar import seed_calendar
from macro_calendar_engine import update_macro_calendar
from macro_data_engine import update_macro_indicators
from macro_ai_engine import MacroAIEngine

logger = logging.getLogger(__name__)

triggers_router = APIRouter()

IMPORT_DIR = BASE_DIR / "tools" / "data" / "imports"


class ImportRequest(BaseModel):
    filename: str


class RestoreBackupRequest(BaseModel):
    filename: str


def log_notification(message_type: str, message_text: str) -> None:
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)",
            (message_type, message_text)
        )
        conn.commit()
    except Exception as e:
        logger.error(f"Failed to log notification: {e}")
    finally:
        if 'conn' in locals():
            conn.close()


def bg_execute_quant_scan():
    engine = DataEngine()
    tickers = engine.get_all_tickers()

    if tickers:
        run_daily_quant_scan(tickers)
        update_daily_ml_predictions(tickers)
        update_all_tail_risks(tickers)

def bg_execute_earnings_scan():
    engine = DataEngine()
    tickers = engine.get_all_tickers()
    try:
        run_earnings_vol_scan(tickers)
    finally:
        record_job_run('weekend_earnings_vol_scan_job')

def bg_execute_universe_quant_scan():
    tickers = get_universe_tickers()
    if not tickers:
        logger.warning("Universe is empty. Please trigger a Universe Update first.")
        return
    run_daily_quant_scan(tickers, scan_type='universe')

def bg_execute_universe_quant_scan_subset(tickers: List[str]):
    run_daily_quant_scan(tickers, scan_type='sideload')

def bg_execute_ml_inference():
    tickers = get_universe_tickers()
    if not tickers:
        engine = DataEngine()
        tickers = engine.get_all_tickers()

    if tickers:
        update_daily_ml_predictions(tickers)

def bg_init_macro_pipeline():
    try:
        logger.info("Starting Macro AI Initialization Sequence...")

        seed_calendar()
        update_macro_calendar()
        update_macro_indicators()

        from regime_engine import calculate_systemic_macro_threat, calculate_market_regime
        calculate_systemic_macro_threat()
        calculate_market_regime()

        ai_engine = MacroAIEngine()
        try:
            ai_engine.train_regime_clustering()
            ai_engine.train_consensus_miss_probability()
            ai_engine.train_volatility_magnitude()
            scan_date = time_engine.now_local().strftime('%Y-%m-%d')
            ai_engine.run_macro_inference(scan_date)
        finally:
            ai_engine.close()

        update_config_atomic({"SCHEDULING": {"MACRO_ENGINE": {"INITIALIZED": True}}})

        log_notification("Success", "Macro AI Pipeline successfully initialized and trained.")
    except Exception as e:
        logger.error(f"Macro AI Pipeline initialization failed: {e}")
        log_notification("Error", f"Macro AI Pipeline Initialization failed: {e}")

def bg_run_macro_pipeline():
    try:
        logger.info("Starting Macro AI Run Sequence...")

        update_macro_calendar()
        update_macro_indicators()

        from regime_engine import calculate_systemic_macro_threat, calculate_market_regime
        calculate_systemic_macro_threat()
        calculate_market_regime()

        ai_engine = MacroAIEngine()
        try:
            scan_date = time_engine.now_local().strftime('%Y-%m-%d')
            ai_engine.run_macro_inference(scan_date)
        finally:
            ai_engine.close()

        log_notification("Success", "Macro AI Pipeline executed successfully.")
    except Exception as e:
        logger.error(f"Macro AI Pipeline execution failed: {e}")
        log_notification("Error", f"Macro AI Pipeline execution failed: {e}")


@triggers_router.post("/macro/init-pipeline")
@limiter.limit("2/minute")
async def trigger_macro_init_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_init_macro_pipeline)
    return JSONResponse(content={
        "status": "success",
        "message": "Macro AI Initialization started in the background. Check notifications."
    })

@triggers_router.post("/macro/run-pipeline")
@limiter.limit("2/minute")
async def trigger_macro_run_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_run_macro_pipeline)
    return JSONResponse(content={
        "status": "success",
        "message": "Macro AI Run initiated in the background. Check notifications."
    })


@triggers_router.post("/trigger-treasury-auction-check")
@limiter.limit("10/minute")
async def trigger_treasury_auction_check_endpoint(request: Request, background_tasks: BackgroundTasks):
    from scheduler_engine import run_treasury_auction_check, _with_job_source
    background_tasks.add_task(_with_job_source("macro_auction_job_am", lambda: run_treasury_auction_check("am")))
    return JSONResponse(content={
        "status": "success",
        "message": "Sovereign Debt Auction Monitor check initiated in the background. Check System Notifications for progress updates."
    })

@triggers_router.get("/macro-regime-allocation")
@limiter.limit("30/minute")
async def get_macro_regime_allocation(request: Request):
    """Returns regime label, ideal allocation, portfolio alignment score (0–100), and 90-day history."""
    from macro_allocator_engine import get_macro_allocation_data
    try:
        data = get_macro_allocation_data()
        return JSONResponse(content=data)
    except Exception as e:
        logger.error("macro-regime-allocation error: %s", e)
        return JSONResponse(status_code=500, content={"status": "error", "message": "Internal error — check server logs."})


@triggers_router.post("/ml/trigger-backfill")
@limiter.limit("2/minute")
async def trigger_ml_backfill_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_historical_backfill)
    return JSONResponse(content={
        "status": "success",
        "message": "ML Historical Backfill initiated in the background. Check System Notifications."
    })

@triggers_router.post("/ml/trigger-training")
@limiter.limit("2/minute")
async def trigger_ml_training_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(train_global_ml_model)
    return JSONResponse(content={
        "status": "success",
        "message": "Global ML Walk-Forward Training initiated in the background. Check System Notifications."
    })

@triggers_router.post("/ml/trigger-inference")
@limiter.limit("2/minute")
async def trigger_ml_inference_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_execute_ml_inference)
    return JSONResponse(content={
        "status": "success",
        "message": "Daily ML Inference initiated in the background. Check System Notifications."
    })

@triggers_router.post("/ml/trigger-anomaly-training")
@limiter.limit("2/minute")
async def trigger_anomaly_training_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_anomaly_training_job)
    return JSONResponse(content={
        "status": "success",
        "message": "Isolation Forest anomaly training initiated in the background. Check System Notifications."
    })

@triggers_router.post("/trigger-quant-scan")
@limiter.limit("10/minute")
async def trigger_quant_scan_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_execute_quant_scan)
    return JSONResponse(content={
        "status": "success",
        "message": "Portfolio Quant Scan initiated in the background. Check System Notifications for progress updates."
    })

@triggers_router.post("/trigger-earnings-scan")
@limiter.limit("10/minute")
async def trigger_earnings_scan_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_execute_earnings_scan)
    return JSONResponse(content={
        "status": "success",
        "message": "Earnings Volatility Scan initiated in the background. Check System Notifications for progress updates."
    })

@triggers_router.post("/trigger-morning-briefing")
@limiter.limit("10/minute")
async def trigger_morning_briefing_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(push_morning_quant_briefing)
    return JSONResponse(content={
        "status": "success",
        "message": "Morning Briefing generation started in the background. Check reports/ for the output file."
    })

@triggers_router.post("/trigger-lunch-briefing")
@limiter.limit("10/minute")
async def trigger_lunch_briefing_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(push_lunchtime_quant_briefing)
    return JSONResponse(content={
        "status": "success",
        "message": "Lunchtime Briefing generation started in the background. Check reports/ for the output file."
    })

@triggers_router.post("/trigger-universe-update")
@limiter.limit("10/minute")
async def trigger_universe_update_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(update_market_universe)
    return JSONResponse(content={
        "status": "success",
        "message": "Market Universe update initiated in the background. Check System Notifications for progress."
    })

@triggers_router.get("/universe/profiler-status")
async def get_profiler_status():
    """
    Returns a full breakdown of the Fundamentals Profiler queue so the UI can
    show *why* the pending count is what it is (eligible vs already profiled
    vs stale). The legacy 'pending_count' top-level key is preserved for any
    external callers depending on the original API shape.
    """
    try:
        breakdown = get_profiler_queue_breakdown()
        return JSONResponse(content={
            "status": "success",
            "pending_count": breakdown.get("pending_count", 0),
            "breakdown": breakdown
        })
    except Exception as e:
        logger.error(f"Failed to compute profiler status: {e}")
        return _error_500(e)

@triggers_router.post("/universe/sync-indices")
@limiter.limit("10/minute")
async def trigger_sync_indices_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_index_scraper)
    return JSONResponse(content={
        "status": "success",
        "message": "Index Constituent scraping initiated in the background. Check System Notifications for progress."
    })

@triggers_router.post("/universe/sync-profiler")
@limiter.limit("2/minute")
async def trigger_sync_profiler_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_fundamentals_profiler)
    return JSONResponse(content={
        "status": "success",
        "message": "Fundamentals Profiler initiated in the background. Check System Notifications for progress."
    })

@triggers_router.post("/universe/deep-sync")
@limiter.limit("2/minute")
async def trigger_universe_deep_sync_endpoint(request: Request, background_tasks: BackgroundTasks):
    """
    Manually trigger the unified Universe Deep Sync pipeline.

    Sequences: fundamentals → metadata → technicals → ML inference for the
    full index universe (FTSE100 + S&P500), respecting UI_PREFERENCES.
    FREETRADE_ONLY_MODE for the Freetrade firewall. Returns immediately
    while the pipeline runs in the background (≈30–45 minutes).
    """
    background_tasks.add_task(run_universe_deep_sync_job)
    return JSONResponse(content={
        "status": "success",
        "message": (
            "Universe Deep Sync Pipeline initiated in the background. "
            "Sequencing fundamentals → metadata → technicals → ML inference "
            "for the full index universe. Estimated runtime: 30–45 minutes. "
            "Check System Notifications for progress."
        )
    })

@triggers_router.post("/trigger-universe-quant-scan")
@limiter.limit("2/minute")
async def trigger_universe_quant_scan_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_execute_universe_quant_scan)
    return JSONResponse(content={
        "status": "success",
        "message": "Full Universe Quant Scan initiated in the background. This will take over an hour. Check System Notifications for progress."
    })

@triggers_router.post("/trigger-sentiment-scan")
@limiter.limit("10/minute")
async def trigger_sentiment_scan_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_sentiment_scan)
    return JSONResponse(content={
        "status": "success",
        "message": "Sentiment Scan initiated in the background. Check System Notifications for progress."
    })

@triggers_router.get("/universe/imports/list")
async def list_importable_csvs():
    try:
        IMPORT_DIR.mkdir(parents=True, exist_ok=True)
        files = [f.name for f in IMPORT_DIR.glob("*.csv")]
        logger.info(f"Scan found {len(files)} CSV files in {IMPORT_DIR}")
        return JSONResponse(content={"status": "success", "files": files})
    except Exception as e:
        logger.error(f"Failed to list import directory {IMPORT_DIR}: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": f"Failed to list import directory: {str(e)}"})

@triggers_router.post("/universe/import/server")
async def import_server_csv(request: ImportRequest, background_tasks: BackgroundTasks):
    if not request.filename.endswith('.csv'):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid file type. Only .csv files are supported."})
    file_path = (IMPORT_DIR / request.filename).resolve()
    if not str(file_path).startswith(str(IMPORT_DIR.resolve())):
        return JSONResponse(status_code=400, content={"status": "error", "message": "Invalid filename."})
    conn = None
    try:
        if not file_path.exists():
            return JSONResponse(status_code=404, content={"status": "error", "message": f"File '{request.filename}' not found on server at {file_path}."})

        df = pd.read_csv(file_path)
        required_cols = ['ticker', 'company_name', 'sector', 'industry', 'currency', 'country', 'exchange']
        for col in required_cols:
            if col not in df.columns:
                return JSONResponse(status_code=400, content={"status": "error", "message": f"Malformed CSV. Missing required column: {col}"})

        df = df.dropna(subset=['ticker'])
        records = []
        current_time = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        for _, row in df.iterrows():
            records.append((
                str(row['ticker']),
                str(row['company_name']) if pd.notna(row['company_name']) else 'Unknown',
                str(row['sector']) if pd.notna(row['sector']) else 'Unclassified',
                str(row['industry']) if pd.notna(row['industry']) else 'Unclassified',
                str(row['country']) if pd.notna(row['country']) else 'Unknown',
                str(row['exchange']) if pd.notna(row['exchange']) else 'Unknown',
                current_time
            ))

        conn = get_connection()
        cursor = conn.cursor()
        cursor.executemany('''
            INSERT INTO market_universe
            (ticker, company_name, sector, industry, country, exchange, last_updated)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                company_name = excluded.company_name,
                sector       = excluded.sector,
                industry     = excluded.industry,
                country      = excluded.country,
                exchange     = excluded.exchange,
                last_updated = excluded.last_updated
        ''', records)
        conn.commit()
        background_tasks.add_task(bg_execute_universe_quant_scan_subset, [r[0] for r in records])
        return JSONResponse(content={
            "status": "success",
            "message": f"Successfully sideloaded {len(records)} assets from '{request.filename}' into the local Market Universe."
        })
    except Exception as e:
        logger.error(f"Fatal error executing CSV parser for {request.filename}: {e}")
        return JSONResponse(status_code=500, content={"status": "error", "message": f"Fatal error executing CSV parser: {str(e)}"})
    finally:
        if conn:
            conn.close()


@triggers_router.post("/update")
@limiter.limit("10/minute")
async def trigger_update(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_update_pipeline)
    return JSONResponse(content={"status": "success"})

@triggers_router.post("/sync-ghostfolio")
@limiter.limit("10/minute")
async def trigger_ghostfolio_sync(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_ghostfolio_sync)
    return JSONResponse(content={"status": "success"})

@triggers_router.post("/trigger-freetrade-sync")
@limiter.limit("10/minute")
async def trigger_freetrade_sync_endpoint(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_freetrade_sync)
    return JSONResponse(content={
        "status": "success",
        "message": "Freetrade synchronization initiated in the background. Check System Notifications for progress updates."
    })

@triggers_router.post("/maintenance/run")
@limiter.limit("5/minute")
async def trigger_maintenance_run(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(run_maintenance_engine)
    return JSONResponse(content={"status": "success", "message": "Maintenance job started in the background. Check System Notifications for the summary."})

@triggers_router.post("/maintenance/dry-run")
@limiter.limit("5/minute")
async def trigger_maintenance_dry_run(request: Request):
    try:
        engine = MaintenanceEngine()
        results = engine.dry_run()
        return JSONResponse(content={"status": "success", "results": results})
    except Exception as e:
        logger.exception("Maintenance dry-run failed")
        return _error_500(e)

def bg_execute_backup_run():
    try:
        run_backup(trigger_type="manual")
    finally:
        record_job_run('backup_job')


@triggers_router.post("/backup/run")
@limiter.limit("5/minute")
async def trigger_backup_run(request: Request, background_tasks: BackgroundTasks):
    background_tasks.add_task(bg_execute_backup_run)
    return JSONResponse(content={"status": "success", "message": "Backup started in the background. Check System Notifications for the summary."})


def _localise_backup_ts(ts):
    if not ts:
        return ts
    try:
        return time_engine.fmt_datetime(datetime.strptime(ts, "%Y-%m-%d %H:%M:%S"))
    except ValueError:
        return ts


@triggers_router.get("/backup/status")
async def get_backup_status_endpoint():
    try:
        result = get_backup_status()
        if result.get("last_backup"):
            result["last_backup"]["started_at"] = _localise_backup_ts(result["last_backup"].get("started_at"))
            result["last_backup"]["finished_at"] = _localise_backup_ts(result["last_backup"].get("finished_at"))
        for b in result.get("backups", []):
            b["mtime"] = _localise_backup_ts(b.get("mtime"))
        return JSONResponse(content={"status": "success", **result})
    except Exception as e:
        logger.exception("Failed to fetch backup status")
        return _error_500(e)


@triggers_router.post("/backup/restore", dependencies=[Depends(require_confirm_token)])
@limiter.limit("5/minute")
async def trigger_backup_restore(request: Request, body: RestoreBackupRequest):
    try:
        result = restore_backup(body.filename)
        if result["status"] == "success":
            return JSONResponse(content=result)
        return JSONResponse(status_code=500, content=result)
    except ValueError as e:
        return JSONResponse(status_code=400, content={"status": "error", "message": str(e)})
    except Exception as e:
        logger.exception("Backup restore failed")
        return _error_500(e)


@triggers_router.post("/ghostfolio/discover")
async def trigger_discovery():
    try:
        engine = GhostfolioSyncEngine()
        if not engine.authenticate():
            return JSONResponse(status_code=500, content={"status": "error", "message": "Failed to authenticate with Ghostfolio."})
        accounts = engine.discover_accounts()
        if accounts:
            reload_scheduler()
            return JSONResponse(content={"status": "success", "message": f"Successfully discovered {len(accounts)} active accounts."})
        return JSONResponse(status_code=500, content={"status": "error", "message": "No accounts discovered or network error occurred."})
    except Exception as e:
        logger.exception("Ghostfolio account discovery failed")
        return _error_500(e)
