import logging
import re
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_SUBMITTED, EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
from config import load_config
import time_engine
from sentiment_engine import run_nextcloud_alert
from huggingface_engine import update_all_sentiment, run_central_bank_nlp_alert
from earnings_engine import run_earnings_alert
from insider_engine import run_insider_alert
from intraday_orchestrator import IntradayOrchestrator
from notification_engine import notify, set_job_source, clear_job_source, current_job_source, SCHEDULER_STATUS_SOURCE
from maintenance_engine import MaintenanceEngine
from data_engine import DataEngine
from quant_signals import QuantEngine
from ghostfolio_sync import GhostfolioSyncEngine
from quant_engine import run_daily_quant_scan
from earnings_vol_engine import run_earnings_vol_scan
from report_dispatcher import push_morning_quant_briefing, push_lunchtime_quant_briefing
from database import get_universe_tickers, get_connection, fill_smgb_actual
from universe_engine import update_market_universe
from profile_engine import run_profile_audit
from regime_engine import calculate_market_regime
from ai_prediction_engine import (
    train_global_ml_model, update_daily_ml_predictions, run_historical_backfill,
    train_quantile_models, score_quantile_predictions,
)
from risk_engine import update_all_tail_risks
from freetrade_engine import sync_freetrade_universe
from universe_deep_sync_engine import run_universe_deep_sync
from system_check_engine import run_system_checks
from macro_calendar_engine import update_macro_calendar
from macro_data_engine import update_macro_indicators
from xray_engine import run_xray_precompute
from news_feed_engine import run_news_feed_job
from intraday_bottom_engine import IntradayBottomEngine

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60})

import functools as _functools


def _with_job_source(job_id, fn):
    """Tags the worker thread with its job id so log_sched_notification routes to that job's status source."""
    @_functools.wraps(fn)
    def _runner(*args, **kwargs):
        set_job_source(job_id)
        try:
            return fn(*args, **kwargs)
        finally:
            clear_job_source()
    return _runner


_orig_add_job = scheduler.add_job


def _tracked_add_job(func, *args, **kwargs):
    job_id = kwargs.get("id")
    return _orig_add_job(_with_job_source(job_id, func) if job_id else func, *args, **kwargs)


scheduler.add_job = _tracked_add_job

import threading as _threading
from datetime import datetime as _dt, timezone as _tz
_active_jobs: dict[str, str] = {}
_active_jobs_lock = _threading.Lock()

def _mark_job_started(name: str) -> None:
    with _active_jobs_lock:
        _active_jobs[name] = _dt.now(_tz.utc).strftime("%Y-%m-%dT%H:%M:%S")

def _mark_job_done(name: str) -> None:
    with _active_jobs_lock:
        _active_jobs.pop(name, None)

def get_active_jobs() -> dict[str, str]:
    with _active_jobs_lock:
        return dict(_active_jobs)

def log_sched_notification(msg_type: str, msg_text: str):
    level = "error" if msg_type == "Error" else ("warning" if msg_type == "Warning" else "info")
    notify(current_job_source() or SCHEDULER_STATUS_SOURCE, msg_type, msg_text, level=level)

def record_job_run(job_id: str):
    from datetime import datetime, timezone
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO scheduler_run_log (job_id, last_run) VALUES (?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET last_run = excluded.last_run",
            (job_id, datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M'))
        )
        conn.commit()
    except Exception as e:
        logger.error("Failed to record job run for %s: %s", job_id, e)
    finally:
        if conn:
            conn.close()

def get_all_job_last_runs() -> dict:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT job_id, last_run, last_started, last_duration_sec, avg_duration_sec, last_status FROM scheduler_run_log")
        rows = cursor.fetchall()
        return {
            row[0]: {
                "last_run": row[1],
                "last_started": row[2],
                "last_duration_sec": row[3],
                "avg_duration_sec": row[4],
                "last_status": row[5],
            }
            for row in rows
        }
    except Exception:
        return {}
    finally:
        if conn:
            conn.close()


_job_start_times: dict[str, float] = {}
_DURATION_EMA_ALPHA = 0.3


def _record_job_duration(job_id: str, started_iso: str, duration_sec: float, status: str) -> None:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT avg_duration_sec FROM scheduler_run_log WHERE job_id = ?", (job_id,))
        row = cursor.fetchone()
        prev_avg = row[0] if row and row[0] is not None else None
        new_avg = duration_sec if prev_avg is None else (_DURATION_EMA_ALPHA * duration_sec + (1 - _DURATION_EMA_ALPHA) * prev_avg)
        cursor.execute(
            "INSERT INTO scheduler_run_log (job_id, last_run, last_started, last_duration_sec, avg_duration_sec, last_status) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET last_started = excluded.last_started, "
            "last_duration_sec = excluded.last_duration_sec, avg_duration_sec = excluded.avg_duration_sec, "
            "last_status = excluded.last_status",
            (job_id, started_iso, started_iso, duration_sec, new_avg, status),
        )
        conn.commit()
    except Exception as e:
        logger.error("Failed to record duration for %s: %s", job_id, e)
    finally:
        if conn:
            conn.close()


def _on_job_event(event) -> None:
    from datetime import datetime, timezone
    now = datetime.now(timezone.utc)
    if event.code == EVENT_JOB_SUBMITTED:
        _job_start_times[event.job_id] = now.timestamp()
        return
    started_ts = _job_start_times.pop(event.job_id, None)
    if started_ts is None:
        return
    duration = max(0.0, now.timestamp() - started_ts)
    started_iso = datetime.fromtimestamp(started_ts, timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    status = "error" if event.code == EVENT_JOB_ERROR else "success"
    _record_job_duration(event.job_id, started_iso, duration, status)


def resume_interrupted_scans() -> None:
    """Called once on startup; re-fires any scan that was IN_PROGRESS when the server last shut down."""
    from datetime import datetime, timezone
    today_str = datetime.now(timezone.utc).strftime('%Y-%m-%d')

    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT scan_type, status FROM quant_scan_states WHERE scan_date = ?",
            (today_str,)
        )
        today_states = {row['scan_type']: row['status'] for row in cursor.fetchall()}

        cursor.execute(
            "SELECT 1 FROM quant_scan_states WHERE scan_type = 'ml_backfill' AND status = 'IN_PROGRESS' "
            "ORDER BY scan_date DESC LIMIT 1"
        )
        standalone_ml_in_progress = cursor.fetchone() is not None
    finally:
        if conn:
            conn.close()

    _deep_sync_keys = {'deep_sync_s1', 'deep_sync_s2', 'deep_sync_s4', 'deep_sync_s5', 'universe_deep_sync'}
    deep_sync_active = any(k in today_states for k in _deep_sync_keys)
    deep_sync_complete = today_states.get('deep_sync_s5') == 'COMPLETED'

    dispatched = False

    if deep_sync_active and not deep_sync_complete:
        logger.info("Startup: detected interrupted Universe Deep Sync — resuming immediately.")
        log_sched_notification("Info", "Resuming interrupted Universe Deep Sync pipeline after restart.")
        _threading.Thread(target=run_universe_deep_sync_job, daemon=True).start()
        dispatched = True

    if today_states.get('daily') == 'IN_PROGRESS':
        logger.info("Startup: detected interrupted Overnight Quant Scan — resuming immediately.")
        log_sched_notification("Info", "Resuming interrupted Overnight Quant Scan after restart.")
        _threading.Thread(target=run_overnight_quant_scan, daemon=True).start()
        dispatched = True

    if today_states.get('universe') == 'IN_PROGRESS':
        logger.info("Startup: detected interrupted Weekend Universe Routine — resuming immediately.")
        log_sched_notification("Info", "Resuming interrupted Weekend Universe Routine after restart.")
        _threading.Thread(target=run_weekend_universe_routine, daemon=True).start()
        dispatched = True

    if standalone_ml_in_progress and not deep_sync_active:
        logger.info("Startup: detected interrupted ML Backfill — resuming immediately.")
        log_sched_notification("Info", "Resuming interrupted ML Historical Backfill after restart.")
        _threading.Thread(target=run_ml_backfill, daemon=True).start()
        dispatched = True

    if not dispatched:
        logger.info("Startup resume check: no interrupted scans found.")


def trigger_sentiment_report():
    try:
        run_nextcloud_alert()
    finally:
        record_job_run('market_sentiment_job')

def run_intraday_orchestrator():
    try:
        IntradayOrchestrator().run()
    finally:
        record_job_run('intraday_orchestrator_job')

def run_maintenance_engine():
    try:
        MaintenanceEngine().run()
    finally:
        record_job_run('maintenance_job')

def run_update_pipeline():
    _mark_job_started(job_label("quant_analysis_job"))
    try:
        log_sched_notification("Scheduler", "Started Update Pipeline...")
        logger.info("Background update initiated.")
        DataEngine().update_all_data()
        from regime_engine import calculate_systemic_macro_threat, calculate_market_regime
        calculate_systemic_macro_threat()
        regime_result = calculate_market_regime()
        QuantEngine().run_all()
        logger.info("Background update complete.")
        log_sched_notification("Success", "Update Pipeline completed successfully.")

        # Fire Nextcloud alert when HMM regime transitions to a new state
        hmm = (regime_result or {}).get("hmm", {})
        if (
            hmm
            and hmm.get("state") is not None
            and hmm.get("previous_state") is not None
            and hmm["state"] != hmm["previous_state"]
        ):
            msg = (
                f"HMM REGIME CHANGE: {hmm['previous_label']} → {hmm['label']} "
                f"(confidence: {hmm['probability']:.0%}) | {hmm['date']}"
            )
            orch = IntradayOrchestrator()
            conn_alert = None
            try:
                conn_alert = get_connection()
                if not orch._evaluate_alert_gate("HMM", "SPY", None, f"REGIME_{hmm['label']}", conn_alert):
                    if notify("hmm_regime_alert", "HMM Regime", msg, conn=conn_alert):
                        orch.record_alert_fired("HMM", "SPY", None, f"REGIME_{hmm['label']}", conn_alert)
            except Exception as _e:
                logger.error("HMM alert gate error: %s", _e)
            finally:
                if conn_alert:
                    conn_alert.close()

        # Fire notification when market stress IF detects sustained systemic anomaly
        stress = (regime_result or {}).get("market_stress", {})
        if stress and stress.get("alert"):
            feats = stress.get("features", {})
            msg = (
                f"MARKET STRESS ALERT: Multivariate anomaly score {stress['score']:.2f}/1.00 "
                f"— systemic conditions are statistically abnormal. "
                f"VIX: {feats.get('vix_level', 0):.1f} | "
                f"SPY: {feats.get('spy_return', 0):+.2f}% | "
                f"HYG: {feats.get('hyg_return', 0):+.2f}% | "
                f"10Y Δ: {feats.get('tnx_change', 0):+.2f}bps | {stress['date']}"
            )
            orch = IntradayOrchestrator()
            conn_alert = None
            try:
                conn_alert = get_connection()
                if not orch._evaluate_alert_gate("MarketStress", "MARKET", None, "STRESS_ELEVATED", conn_alert):
                    if notify("market_stress_alert", "Market Stress", msg, conn=conn_alert):
                        orch.record_alert_fired("MarketStress", "MARKET", None, "STRESS_ELEVATED", conn_alert)
            except Exception as _e:
                logger.error("Market stress alert gate error: %s", _e)
            finally:
                if conn_alert:
                    conn_alert.close()

    except Exception as e:
        log_sched_notification("Error", f"Update Pipeline failed: {e}")
    finally:
        _mark_job_done(job_label("quant_analysis_job"))
        record_job_run('quant_analysis_job')

def run_ghostfolio_sync():
    _mark_job_started(job_label("ghostfolio_sync_job"))
    try:
        log_sched_notification("Scheduler", "Started Ghostfolio Sync...")
        sync_engine = GhostfolioSyncEngine()
        sync_engine.run_full_sync()
        log_sched_notification("Success", "Ghostfolio Sync completed successfully.")
    except Exception as e:
        log_sched_notification("Error", f"Ghostfolio Sync failed: {e}")
    finally:
        _mark_job_done(job_label("ghostfolio_sync_job"))
        record_job_run('ghostfolio_sync_job')

def run_freetrade_sync():
    _mark_job_started(job_label("freetrade_sync_job"))
    try:
        log_sched_notification("Scheduler", "Started Freetrade Sync...")
        logger.info("Freetrade sync initiated.")
        sync_freetrade_universe()
        logger.info("Freetrade sync complete.")
        log_sched_notification("Success", "Freetrade Sync completed successfully.")
    except Exception as e:
        logger.error("Freetrade Sync Failed: %s", e)
        log_sched_notification("Error", f"Freetrade Sync failed: {e}")
    finally:
        _mark_job_done(job_label("freetrade_sync_job"))
        record_job_run('freetrade_sync_job')

def run_sentiment_scan():
    _mark_job_started(job_label("sentiment_scan_job"))
    try:
        log_sched_notification("Scheduler", "Started Sentiment Scan...")
        logger.info("Sentiment scan initiated.")
        engine = DataEngine()
        all_tickers = engine.get_all_tickers()
        update_all_sentiment(all_tickers)
        logger.info("Sentiment scan complete.")
        log_sched_notification("Success", "Sentiment Scan completed successfully.")
    except Exception as e:
        logger.error("Sentiment Scan Failed: %s", e)
        log_sched_notification("Error", f"Sentiment Scan failed: {e}")
    finally:
        _mark_job_done(job_label("sentiment_scan_job"))
        record_job_run('sentiment_scan_job')

def run_overnight_quant_scan():
    """Portfolio + watchlist resumable quant scan followed by tail-risk computation."""
    _mark_job_started(job_label("overnight_quant_scan_job"))
    try:
        log_sched_notification("Scheduler", "Started Overnight Quant Scan...")
        logger.info("Overnight quant scan initiated.")
        engine = DataEngine()
        all_tickers = engine.get_all_tickers()
        run_daily_quant_scan(all_tickers)
        logger.info("Overnight tail risk computation initiated.")
        update_all_tail_risks(all_tickers)
        logger.info("Overnight quant scan complete.")
        log_sched_notification("Success", "Overnight Quant Scan completed successfully.")
    except Exception as e:
        logger.error("Overnight Quant Scan Failed: %s", e)
        log_sched_notification("Error", f"Overnight Quant Scan failed: {e}")
    finally:
        _mark_job_done(job_label("overnight_quant_scan_job"))
        record_job_run('overnight_quant_scan_job')

def run_weekend_earnings_scan():
    _mark_job_started(job_label("weekend_earnings_vol_scan_job"))
    try:
        log_sched_notification("Scheduler", "Started Earnings Volatility Scan...")
        logger.info("Earnings volatility scan initiated.")
        engine = DataEngine()
        all_tickers = engine.get_all_tickers()
        run_earnings_vol_scan(all_tickers)
        logger.info("Earnings volatility scan complete.")
        log_sched_notification("Success", "Earnings Volatility Scan completed successfully.")
    except Exception as e:
        logger.error("Earnings Volatility Scan Failed: %s", e)
        log_sched_notification("Error", f"Earnings Volatility Scan failed: {e}")
    finally:
        _mark_job_done(job_label("weekend_earnings_vol_scan_job"))
        record_job_run('weekend_earnings_vol_scan_job')

def run_morning_briefing_dispatch():
    _mark_job_started(job_label("morning_briefing_dispatch_job"))
    try:
        log_sched_notification("Scheduler", "Started Morning Briefing Dispatch...")
        logger.info("Morning briefing dispatch initiated.")
        push_morning_quant_briefing()
        logger.info("Morning briefing dispatch complete.")
        log_sched_notification("Success", "Morning Briefing Dispatch completed successfully.")
    except Exception as e:
        logger.error("Morning Briefing Dispatch Failed: %s", e)
        log_sched_notification("Error", f"Morning Briefing Dispatch failed: {e}")
    finally:
        _mark_job_done(job_label("morning_briefing_dispatch_job"))
        record_job_run('morning_briefing_dispatch_job')


def run_lunchtime_briefing_dispatch():
    _mark_job_started(job_label("lunchtime_briefing_dispatch_job"))
    try:
        log_sched_notification("Scheduler", "Started Lunchtime Briefing Dispatch...")
        logger.info("Lunchtime briefing dispatch initiated.")
        push_lunchtime_quant_briefing()
        logger.info("Lunchtime briefing dispatch complete.")
        log_sched_notification("Success", "Lunchtime Briefing Dispatch completed successfully.")
    except Exception as e:
        logger.error("Lunchtime Briefing Dispatch Failed: %s", e)
        log_sched_notification("Error", f"Lunchtime Briefing Dispatch failed: {e}")
    finally:
        _mark_job_done(job_label("lunchtime_briefing_dispatch_job"))
        record_job_run('lunchtime_briefing_dispatch_job')

def run_weekend_universe_routine():
    _mark_job_started(job_label("universe_routine_job"))
    try:
        log_sched_notification("Scheduler", "Started Weekend Universe Routine...")
        logger.info("Weekend universe routine initiated.")
        update_market_universe()
        all_tickers = get_universe_tickers()
        if all_tickers:
            run_daily_quant_scan(all_tickers, scan_type='universe')
            logger.info("Universe Technicals complete. Proceeding to heavy metric crunch (VaR, Sentiment)...")
            update_all_tail_risks(all_tickers)
            update_all_sentiment(all_tickers)
            run_historical_backfill(tickers=all_tickers)
        else:
            logger.warning("Universe is empty, skipping quant scan.")
        logger.info("Weekend universe routine complete.")
        log_sched_notification("Success", "Weekend Universe Routine completed successfully.")
    except Exception as e:
        logger.error("Weekend Universe Routine Failed: %s", e)
        log_sched_notification("Error", f"Weekend Universe Routine failed: {e}")
    finally:
        _mark_job_done(job_label("universe_routine_job"))
        record_job_run('universe_routine_job')

def run_index_scraper():
    _mark_job_started(job_label("index_scraper_job"))
    try:
        log_sched_notification("Scheduler", "Started Index Constituents Scraper...")
        logger.info("Index scraper initiated.")
        # Delayed import avoids circular dependency with index_engine
        from index_engine import sync_all_indices
        sync_all_indices()
        logger.info("Index scraper complete.")
        log_sched_notification("Success", "Index Constituents Scraper completed successfully.")
    except Exception as e:
        logger.error("Index Scraper Failed: %s", e)
        log_sched_notification("Error", f"Index Scraper failed: {e}")
    finally:
        _mark_job_done(job_label("index_scraper_job"))
        record_job_run('index_scraper_job')

def run_fundamentals_profiler():
    """Batch size read from SCHEDULING.PROFILER_ENGINE.BATCH_SIZE in config."""
    _mark_job_started(job_label("fundamentals_profiler_job"))
    try:
        log_sched_notification("Scheduler", "Started Fundamentals Profiler...")
        logger.info("Fundamentals profiler initiated.")
        from profile_engine import run_profile_audit
        config = load_config()
        batch_size = config.get("SCHEDULING", {}).get("PROFILER_ENGINE", {}).get("BATCH_SIZE", 250)
        run_profile_audit(limit=int(batch_size))
        logger.info("Fundamentals profiler complete.")
        log_sched_notification("Success", "Fundamentals Profiler completed successfully.")
    except Exception as e:
        logger.error("Fundamentals Profiler Failed: %s", e)
        log_sched_notification("Error", f"Fundamentals Profiler failed: {e}")
    finally:
        _mark_job_done(job_label("fundamentals_profiler_job"))
        record_job_run('fundamentals_profiler_job')

def run_universe_deep_sync_job():
    """Scheduler envelope for universe_deep_sync_engine; that engine emits its own per-stage notifications."""
    _mark_job_started(job_label("universe_deep_sync_job"))
    try:
        log_sched_notification("Scheduler", "Started Universe Deep Sync Pipeline...")
        run_universe_deep_sync()
        log_sched_notification("Success", "Universe Deep Sync Pipeline job completed.")
    except Exception as e:
        logger.error("Universe Deep Sync Pipeline Failed: %s", e)
        log_sched_notification("Error", f"Universe Deep Sync Pipeline failed: {e}")
    finally:
        _mark_job_done(job_label("universe_deep_sync_job"))
        record_job_run('universe_deep_sync_job')


def run_ml_backfill():
    _mark_job_started(job_label("ml_backfill_job"))
    try:
        log_sched_notification("Scheduler", "Started ML Historical Backfill...")
        logger.info("ML Historical Backfill initiated.")
        run_historical_backfill()
        logger.info("ML Historical Backfill complete.")
        log_sched_notification("Success", "ML Historical Backfill completed successfully.")
    except Exception as e:
        logger.error("ML Historical Backfill Failed: %s", e)
        log_sched_notification("Error", f"ML Historical Backfill failed: {e}")
    finally:
        _mark_job_done(job_label("ml_backfill_job"))
        record_job_run('ml_backfill_job')

def run_ml_training():
    _mark_job_started(job_label("ml_training_job"))
    try:
        log_sched_notification("Scheduler", "Started ML Global Training...")
        logger.info("ML Global Training initiated.")
        train_global_ml_model()
        train_quantile_models()
        logger.info("ML Global Training complete.")
        log_sched_notification("Success", "ML Global Training completed successfully.")
    except Exception as e:
        logger.error("ML Global Training Failed: %s", e)
        log_sched_notification("Error", f"ML Global Training failed: {e}")
    finally:
        _mark_job_done(job_label("ml_training_job"))
        record_job_run('ml_training_job')

def run_ml_inference():
    _mark_job_started(job_label("ml_inference_job"))
    try:
        log_sched_notification("Scheduler", "Started Daily ML Inference...")
        logger.info("Daily ML Inference initiated.")
        tickers = get_universe_tickers()
        if not tickers:
            engine = DataEngine()
            tickers = engine.get_all_tickers()
        if tickers:
            update_daily_ml_predictions(tickers)
            score_quantile_predictions(tickers)
        logger.info("Daily ML Inference complete.")
        log_sched_notification("Success", "Daily ML Inference completed successfully.")
    except Exception as e:
        logger.error("Daily ML Inference Failed: %s", e)
        log_sched_notification("Error", f"Daily ML Inference failed: {e}")
    finally:
        _mark_job_done(job_label("ml_inference_job"))
        record_job_run('ml_inference_job')

def run_macro_calendar_update():
    _mark_job_started(job_label("macro_calendar_job"))
    try:
        log_sched_notification("Scheduler", "Started Macro Calendar Update...")
        logger.info("Macro calendar update initiated.")
        update_macro_calendar()
        logger.info("Macro calendar update complete.")
        log_sched_notification("Success", "Macro Calendar Update completed successfully.")
    except Exception as e:
        logger.error("Macro Calendar Update Failed: %s", e)
        log_sched_notification("Error", f"Macro Calendar Update failed: {e}")
    finally:
        _mark_job_done(job_label("macro_calendar_job"))
        record_job_run('macro_calendar_job')

def run_central_bank_nlp_check():
    """Polls for today's passed central bank events and dispatches FinBERT NLP alerts."""
    CB_EVENTS = {
        'Fed Interest Rate Decision', 'FOMC Meeting Minutes',
        'BoE Official Bank Rate', 'BOE Gov Bailey Speaks'
    }
    placeholders = ','.join('?' * len(CB_EVENTS))
    try:
        rows = []
        conn = None
        try:
            conn = get_connection()
            rows = conn.cursor().execute(
                f"""SELECT event_id, event_name, currency FROM macro_calendar
                    WHERE DATE(event_date) = DATE('now')
                    AND event_date <= datetime('now')
                    AND alert_dispatched = 0
                    AND event_name IN ({placeholders})""",
                tuple(CB_EVENTS)
            ).fetchall()
        finally:
            if conn:
                conn.close()

        for event_id, event_name, currency in rows:
            success = run_central_bank_nlp_alert(event_name, currency)
            if success:
                conn2 = None
                try:
                    conn2 = get_connection()
                    conn2.execute(
                        "UPDATE macro_calendar SET alert_dispatched = 1 WHERE event_id = ?",
                        (event_id,)
                    )
                    conn2.commit()
                finally:
                    if conn2:
                        conn2.close()
                log_sched_notification("Macro NLP", f"Central Bank NLP dispatched for: {event_name}")
    except Exception as e:
        logger.error("Central Bank NLP check failed: %s", e)
    finally:
        record_job_run('cb_nlp_alert_job')


def run_macro_data_update():
    _mark_job_started(job_label("macro_data_job"))
    try:
        log_sched_notification("Scheduler", "Started Macro Data Update...")
        logger.info("Macro data update initiated.")
        update_macro_indicators()
        logger.info("Macro data update complete.")
        log_sched_notification("Success", "Macro Data Update completed successfully.")
    except Exception as e:
        logger.error("Macro Data Update Failed: %s", e)
        log_sched_notification("Error", f"Macro Data Update failed: {e}")
    finally:
        _mark_job_done(job_label("macro_data_job"))
        record_job_run('macro_data_job')

def run_xray_risk_cache_job():
    """Pre-computes portfolio beta, vol, correlation, and dividend yields for the X-ray report."""
    _mark_job_started(job_label("xray_risk_cache_job"))
    try:
        log_sched_notification("Scheduler", "Started X-ray Risk Cache job...")
        success = run_xray_precompute()
        if success:
            log_sched_notification("Success", "X-ray Risk Cache updated successfully.")
        else:
            log_sched_notification("Warning", "X-ray Risk Cache job completed with warnings — check logs.")
    except Exception as e:
        logger.error("X-ray Risk Cache job failed: %s", e)
        log_sched_notification("Error", f"X-ray Risk Cache job failed: {e}")
    finally:
        _mark_job_done(job_label("xray_risk_cache_job"))
        record_job_run('xray_risk_cache_job')


def run_anomaly_training_job():
    """Nightly retraining of per-ticker Isolation Forest anomaly models (Mon–Fri 18:30)."""
    _mark_job_started(job_label("anomaly_training_job"))
    try:
        log_sched_notification("Scheduler", "Started Anomaly Training job...")
        from anomaly_engine import AnomalyEngine
        from config import HISTORICAL_DIR
        all_tickers = DataEngine().get_all_tickers()
        engine = AnomalyEngine()
        engine.train_all(all_tickers, HISTORICAL_DIR)
        engine.backfill_all(all_tickers, HISTORICAL_DIR)
        log_sched_notification("Success", "Anomaly Training & backfill completed.")
    except Exception as e:
        logger.error("Anomaly Training job failed: %s", e)
        log_sched_notification("Error", f"Anomaly Training job failed: {e}")
    finally:
        _mark_job_done(job_label("anomaly_training_job"))
        record_job_run('anomaly_training_job')


def run_intraday_dip_scan():
    """Scans actively-monitored tickers for intraday capitulation bottoms (every 2 min, market hours)."""
    engine = IntradayBottomEngine()
    if not engine.get_active_monitors():
        return  # Fast-exit — nothing armed, log nothing
    try:
        engine.run_scan()
        # Bottoming-zone alerts are logged inside engine._fire_alert() only when score ≥ 65.
        # Silent on clean scans to avoid filling the notification feed with noise.
    except Exception as e:
        logger.error("DipRadar scan job failed: %s", e)
        log_sched_notification("Error", f"DipRadar scan failed: {e}")
    finally:
        record_job_run('intraday_dip_scan_job')


def run_intraday_dip_reset(exchange: str = "NYSE"):
    """Deactivates intraday monitors for *exchange* at end of its trading day."""
    try:
        IntradayBottomEngine().deactivate_exchange_today(exchange)
    except Exception as e:
        logger.error("DipRadar reset job failed (%s): %s", exchange, e)
        log_sched_notification("Error", f"DipRadar reset failed ({exchange}): {e}")
    finally:
        record_job_run('intraday_dip_reset_job')


def _build_contagion_feed_text(event: dict) -> str:
    leaders = event.get("leader_shocks", [])
    etfs = event.get("etf_hits", [])
    vol_tickers = set(event.get("volume_spikes", []))
    severity = event.get("severity_score", 0.0)

    n = len(leaders)
    leader_parts = [
        f"{s['ticker']} ({s['intraday_pct']:+.2f}%){' ⚡' if s['ticker'] in vol_tickers else ''}"
        for s in leaders
    ]
    etf_parts = [f"{e['ticker']} ({e['intraday_pct']:+.2f}%)" for e in etfs]

    lines = [
        f"AI/Semi flash crash — {n} leader{'s' if n != 1 else ''} down, ETF contagion confirmed",
        f"Leaders: {', '.join(leader_parts)}",
    ]
    if etf_parts:
        lines.append(f"ETFs: {', '.join(etf_parts)}")
    lines.append(f"Severity {severity:.0%} | Review AI/semiconductor exposure")
    return "\n".join(lines)


def _build_contagion_message(event: dict, config: dict) -> str:
    leaders = event.get("leader_shocks", [])
    etfs = event.get("etf_hits", [])
    vol_tickers = event.get("volume_spikes", [])

    lines = [
        "🚨 **AI SECTOR CONTAGION DETECTED** 🚨",
        "",
        "A flash sell-off in leading AI stocks is spreading to sector ETFs.",
        "",
        "**📉 Leader Shocks:**",
    ]
    for s in leaders:
        vol_note = " _(volume spike)_" if s["ticker"] in vol_tickers else ""
        lines.append(f"- **{s['ticker']}**: {s['intraday_pct']:+.2f}%{vol_note}")

    lines += ["", "**📉 ETF Contagion Confirmed:**"]
    for e in etfs:
        lines.append(f"- **{e['ticker']}**: {e['intraday_pct']:+.2f}%")

    lines += ["", "_Consider reviewing open positions and hedging long AI/semiconductor exposure._"]
    return "\n".join(lines)


def run_ai_contagion_job():
    """Intraday AI Sector Contagion scan — runs every 15 min during extended market hours."""
    from ai_contagion_engine import AIContagionEngine, record_scan_snapshot
    config = load_config()
    conn = None
    try:
        conn = get_connection()
        engine = AIContagionEngine(config)
        candidates = engine.scan()

        record_scan_snapshot(conn, candidates)

        if not candidates:
            return

        orch = IntradayOrchestrator()

        for event in candidates:
            suppress = orch._evaluate_alert_gate(
                "AIContagion", event["ticker"], event["price"], event["reason"], conn
            )
            if suppress:
                logger.info("AIContagion: alert suppressed by gate (cooldown/rearm).")
                continue

            if notify(
                "ai_contagion_alert",
                "AIContagion",
                _build_contagion_feed_text(event),
                nextcloud_text=_build_contagion_message(event, config),
                conn=conn,
            ):
                orch.record_alert_fired(
                    "AIContagion", event["ticker"], event["price"], event["reason"], conn
                )
            leaders_summary = ", ".join(
                f"{s['ticker']} ({s['intraday_pct']:+.2f}%)"
                for s in event.get("leader_shocks", [])
            )
            logger.warning("AIContagion: alert fired. Leaders: %s", leaders_summary)
    except Exception as e:
        logger.error("AI Contagion job failed: %s", e)
        log_sched_notification("Error", f"AI Contagion job failed: {e}")
    finally:
        if conn:
            conn.close()
        record_job_run('ai_contagion_job')


def run_trap_monitor_job():
    from bull_bear_trap_engine import TrapEngine
    from intraday_orchestrator import IntradayOrchestrator
    config = load_config()
    conn = None
    try:
        conn = get_connection()
        engine = TrapEngine(config)
        results = engine.run_scan()

        if not results:
            return

        # Only alert on high-severity phases
        alert_phases = {"ACTIVE_SELLOFF", "BULL_TRAP_RISK", "CAPITULATION_FORMING", "BEAR_TRAP_RISK"}
        orch = IntradayOrchestrator()

        for row in results:
            phase = row.get("phase", "NEUTRAL")
            if phase not in alert_phases:
                continue

            ticker = row["ticker"]
            reason = f"TRAP MONITOR {phase.replace('_', ' ')}"
            suppress = orch._evaluate_alert_gate("TrapMonitor", ticker, None, reason, conn)
            if suppress:
                continue

            notes = (
                row.get("bull_trap_notes") or row.get("bear_trap_notes") or
                row.get("cap_notes") or row.get("wyckoff_notes") or ""
            )
            feed_text = (
                f"**{ticker}** — Phase: {phase.replace('_', ' ')} | "
                f"RSI {row.get('rsi', '—')} | EMA dist {row.get('ema_distance', '—')}% | {notes}"
            )
            msg_lines = [
                f"🎭 **TRAP MONITOR: {ticker}** — {phase.replace('_', ' ')}",
                "",
                notes,
                "",
                f"RSI: {row.get('rsi', '—')} | EMA Distance: {row.get('ema_distance', '—')}%",
                f"Bull Trap: {row.get('bull_trap_level', '—')} | Bear Trap: {row.get('bear_trap_level', '—')}",
                f"Capitulation: {row.get('cap_level', '—')} | Wyckoff: {row.get('wyckoff_level', '—')}",
            ]
            if notify("trap_monitor_alert", "TrapMonitor", feed_text, nextcloud_text="\n".join(msg_lines), conn=conn):
                orch.record_alert_fired("TrapMonitor", ticker, None, reason, conn)
            logger.info("TrapMonitor: alert fired for %s (%s).", ticker, phase)

    except Exception as e:
        logger.error("Trap Monitor job failed: %s", e)
        log_sched_notification("Error", f"Trap Monitor job failed: {e}")
    finally:
        if conn:
            conn.close()
        record_job_run('trap_monitor_job')


def run_smgb_actual_fill():
    """Fills actuals for both prediction types from today's open and yesterday's close."""
    from datetime import datetime, timezone, timedelta
    import pandas as pd
    log_sched_notification("Scheduler", "Started SMGB actual-fill job...")
    try:
        target = datetime.now(timezone.utc).date()
        while target.weekday() >= 5:
            target -= timedelta(days=1)

        from yahoo_engine import yahoo_engine as _ye
        history = _ye.get_price_history(["SMGB.L"], period="5d", interval="1d")
        df = history.get("SMGB.L")
        if df is None or df.empty:
            log_sched_notification("Warning", "SMGB actual-fill: no price data returned.")
            return

        df.index = df.index.normalize()

        # next_open: today's actual opening price
        target_ts = pd.Timestamp(target.isoformat())
        if target_ts in df.index:
            actual_open = float(df.loc[target_ts, "Open"])
            fill_smgb_actual(target.isoformat(), actual_open, prediction_type='next_open')
            log_sched_notification("Success", f"SMGB actual-fill (next_open): {target} at open £{actual_open:.4f}.")
        else:
            log_sched_notification("Warning", f"SMGB actual-fill: no data for {target}.")

        # us_open_impact: yesterday's closing price (reflects full US session influence)
        prev = target - timedelta(days=1)
        while prev.weekday() >= 5:
            prev -= timedelta(days=1)
        prev_ts = pd.Timestamp(prev.isoformat())
        if prev_ts in df.index:
            actual_close = float(df.loc[prev_ts, "Close"])
            fill_smgb_actual(prev.isoformat(), actual_close, prediction_type='us_open_impact')
            log_sched_notification("Success", f"SMGB actual-fill (us_open_impact): {prev} at close £{actual_close:.4f}.")
    except Exception as e:
        log_sched_notification("Error", f"SMGB actual-fill failed: {e}")
    finally:
        record_job_run('smgb_actual_fill_job')


def run_smgb_predictor_job():
    from smgb_predictor import run_smgb_prediction
    log_sched_notification("Scheduler", "Started SMGB.L Price Predictor job...")
    try:
        result = run_smgb_prediction()
        if result.get("status") != "success":
            log_sched_notification("Warning", f"SMGB predictor: {result.get('error', 'unknown error')}")
            return

        predicted = result.get("predicted_price")
        change = result.get("predicted_change_pct", 0)
        signal = result.get("signal_source", "")
        target = result.get("next_open_date", "")
        sign = "+" if change >= 0 else ""
        msg = (
            f"SMGB.L Price Predictor — {target}: "
            f"£{predicted:.4f} ({sign}{change:.2f}%) | signal: {signal}"
        )
        notify("smgb_prediction", "SMGB Prediction", msg)
    except Exception as e:
        logger.error("SMGB predictor job failed: %s", e)
        log_sched_notification("Error", f"SMGB predictor job failed: {e}")
    finally:
        record_job_run('smgb_predictor_job')


def _run_etf_predictor_job(config_id: int, fill_actuals: bool = False) -> None:
    job_type = "post" if fill_actuals else "pre"
    job_id = f"etf_predictor_{config_id}_{job_type}_job"
    job_name = f"ETF Price Predictor #{config_id} ({'post-close' if fill_actuals else 'pre-open'})"
    _mark_job_started(job_name)
    log_sched_notification("Scheduler", f"Started {job_name} ({job_type})...")
    try:
        from etf_predictor_engine import fill_actuals_for_config, run_prediction
        if fill_actuals:
            fill_actuals_for_config(config_id)
            result = run_prediction(config_id)
        else:
            result = run_prediction(config_id)
        if result.get("status") != "success":
            log_sched_notification("Warning", f"{job_name}: {result.get('error', 'unknown error')}")
            return
        predicted = result.get("predicted_price")
        change = result.get("predicted_change_pct", 0)
        signal = result.get("signal_source", "")
        target = result.get("next_open_date", "")
        etf = config_id
        cfg_db = result.get("etf_info", {})
        ticker_label = cfg_db.get("name", str(config_id))
        sign = "+" if change >= 0 else ""
        log_sched_notification(
            "Success",
            f"{ticker_label} Predictor — {target}: {predicted:.4f} ({sign}{change:.2f}%) | signal: {signal}",
        )
    except Exception as e:
        logger.error("ETF predictor job %s failed: %s", job_id, e)
        log_sched_notification("Error", f"{job_name} failed: {e}")
    finally:
        _mark_job_done(job_name)
        record_job_run(job_id)


def register_etf_predictor_jobs(config: dict) -> None:
    config_id = config["id"]
    if not config.get("enabled") or config.get("deleted_at"):
        return
    pre_time = config.get("pre_run_time", "13:30")
    post_time = config.get("post_run_time", "22:00")
    try:
        pre_h, pre_m = map(int, pre_time.split(":"))
        scheduler.add_job(
            _run_etf_predictor_job,
            CronTrigger(day_of_week="mon-fri", hour=pre_h, minute=pre_m, timezone="UTC"),
            id=f"etf_predictor_{config_id}_pre_job",
            kwargs={"config_id": config_id, "fill_actuals": False},
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info("ETF predictor %s pre-job scheduled at %s UTC.", config_id, pre_time)
    except Exception as e:
        logger.error("Failed to register ETF predictor %s pre-job: %s", config_id, e)
    try:
        post_h, post_m = map(int, post_time.split(":"))
        scheduler.add_job(
            _run_etf_predictor_job,
            CronTrigger(day_of_week="mon-fri", hour=post_h, minute=post_m, timezone="UTC"),
            id=f"etf_predictor_{config_id}_post_job",
            kwargs={"config_id": config_id, "fill_actuals": True},
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info("ETF predictor %s post-job scheduled at %s UTC.", config_id, post_time)
    except Exception as e:
        logger.error("Failed to register ETF predictor %s post-job: %s", config_id, e)


def unregister_etf_predictor_jobs(config_id: int) -> None:
    """Remove pre/post jobs for a given ETF predictor config. Silently ignores missing jobs."""
    from apscheduler.jobstores.base import JobLookupError
    for suffix in ("pre", "post"):
        try:
            scheduler.remove_job(f"etf_predictor_{config_id}_{suffix}_job")
        except (JobLookupError, Exception):
            pass


def run_etf_actual_fill_job() -> None:
    """Always-on: fills actuals for all active ETF predictor configs."""
    log_sched_notification("Scheduler", "Started ETF Predictor actual-fill job...")
    try:
        from etf_predictor_engine import fill_all_actuals
        fill_all_actuals()
        log_sched_notification("Success", "ETF Predictor actual-fill complete.")
    except Exception as e:
        log_sched_notification("Error", f"ETF Predictor actual-fill failed: {e}")
    finally:
        record_job_run("etf_predictor_actual_fill_job")


def run_system_check_job():
    log_sched_notification("Scheduler", "Started System Configuration Check...")
    try:
        issues = run_system_checks()
        for issue in issues:
            level = "Error" if issue["level"] == "error" else "Warning"
            key_marker = f"[system-check:{issue['key']}]"
            conn = None
            try:
                conn = get_connection()
                already = conn.execute(
                    "SELECT 1 FROM system_notifications WHERE message_text LIKE ?"
                    " AND timestamp > datetime('now', '-23 hours')",
                    (f"%{key_marker}%",)
                ).fetchone()
            finally:
                if conn:
                    conn.close()
            if not already:
                log_sched_notification(level, f"{key_marker} {issue['message']}")
        label = f"{len(issues)} issue(s) found." if issues else "No issues found."
        sched_level = "Warning" if issues else "Success"
        log_sched_notification(sched_level, f"System Configuration Check: {label}")
    except Exception as e:
        logger.error("System Check Job Failed: %s", e)
        log_sched_notification("Error", f"System Check failed: {e}")
    finally:
        record_job_run('system_check_job')


def reload_scheduler():
    """Reads the latest config.json and updates APScheduler dynamically."""
    logger.info("Reloading scheduled jobs from configuration...")
    scheduler.remove_all_jobs()
    user_tz = time_engine.get_user_tz()

    config = load_config()
    notifications = config.get("NOTIFICATIONS", {})
    scheduling = config.get("SCHEDULING", {})

    sentiment_cfg = notifications.get("MARKET_SENTIMENT", {})
    if sentiment_cfg.get("ENABLED"):
        time_str = sentiment_cfg.get("TIME", "09:30")
        freq = sentiment_cfg.get("FREQUENCY", "mon-fri")
        try:
            hour, minute = map(int, time_str.split(':'))
            scheduler.add_job(
                trigger_sentiment_report,
                CronTrigger(day_of_week=freq, hour=hour, minute=minute, timezone=user_tz),
                id='market_sentiment_job'
            )
            logger.info("Market Sentiment Job scheduled for %s at %s", freq, time_str)
        except Exception as e:
            logger.error("Failed to schedule Market Sentiment: %s", e)

    earnings_cfg = notifications.get("EARNINGS_ALERTS", {})
    if earnings_cfg.get("ENABLED"):
        time_str = earnings_cfg.get("TIME", "08:00")
        try:
            hour, minute = map(int, time_str.split(':'))
            scheduler.add_job(
                run_earnings_alert,
                CronTrigger(day_of_week='mon-fri', hour=hour, minute=minute, timezone=user_tz),
                id='earnings_alert_job'
            )
            logger.info("Earnings Alerts Job scheduled for mon-fri at %s", time_str)
        except Exception as e:
            logger.error("Failed to schedule Earnings Alerts: %s", e)

    insider_cfg = notifications.get("INSIDER_TRADING", {})
    if insider_cfg.get("ENABLED_PORTFOLIO") or insider_cfg.get("ENABLED_WATCHLIST"):
        time_str = insider_cfg.get("TIME", "18:00")
        freq = insider_cfg.get("FREQUENCY", "mon-fri")
        try:
            hour, minute = map(int, time_str.split(':'))
            scheduler.add_job(
                run_insider_alert,
                CronTrigger(day_of_week=freq, hour=hour, minute=minute, timezone=user_tz),
                id='insider_alert_job'
            )
            logger.info("Insider Trading Alert Job scheduled for %s at %s", freq, time_str)
        except Exception as e:
            logger.error("Failed to schedule Insider Alerts: %s", e)

    ghost_cfg = scheduling.get("GHOSTFOLIO_SYNC", {})
    if ghost_cfg.get("ENABLED"):
        interval = int(ghost_cfg.get("INTERVAL_HOURS", 0))
        freq = ghost_cfg.get("FREQUENCY", "mon-fri")
        if interval > 0:
            scheduler.add_job(run_ghostfolio_sync, IntervalTrigger(hours=interval), id='ghostfolio_sync_job')
            logger.info("Ghostfolio Sync scheduled every %d hours.", interval)
        else:
            time_str = ghost_cfg.get("TIME", "06:00")
            try:
                hour, minute = map(int, time_str.split(':'))
                scheduler.add_job(
                    run_ghostfolio_sync,
                    CronTrigger(day_of_week=freq, hour=hour, minute=minute, timezone=user_tz),
                    id='ghostfolio_sync_job'
                )
                logger.info("Ghostfolio Sync scheduled for %s at %s", freq, time_str)
            except Exception as e:
                logger.error("Failed to schedule Ghostfolio Sync: %s", e)

    quant_cfg = scheduling.get("QUANT_ANALYSIS", {})
    if quant_cfg.get("ENABLED"):
        interval = int(quant_cfg.get("INTERVAL_HOURS", 0))
        freq = quant_cfg.get("FREQUENCY", "mon-fri")
        if interval > 0:
            scheduler.add_job(run_update_pipeline, IntervalTrigger(hours=interval), id='quant_analysis_job')
            logger.info("Quant Analysis scheduled every %d hours.", interval)
        else:
            time_str = quant_cfg.get("TIME", "18:00")
            try:
                hour, minute = map(int, time_str.split(':'))
                scheduler.add_job(
                    run_update_pipeline,
                    CronTrigger(day_of_week=freq, hour=hour, minute=minute, timezone=user_tz),
                    id='quant_analysis_job'
                )
                logger.info("Quant Analysis scheduled for %s at %s", freq, time_str)
            except Exception as e:
                logger.error("Failed to schedule Quant Analysis: %s", e)

    sent_scan_cfg = scheduling.get("SENTIMENT_ENGINE", {})
    if sent_scan_cfg.get("ENABLED"):
        freq = sent_scan_cfg.get("FREQUENCY", "mon-fri")
        start_time = sent_scan_cfg.get("START_TIME", "09:30")
        end_time = sent_scan_cfg.get("END_TIME", "16:00")
        interval_hours = int(sent_scan_cfg.get("INTERVAL_HOURS", 4))
        try:
            start_h, _ = map(int, start_time.split(':'))
            end_h, _ = map(int, end_time.split(':'))
            scheduler.add_job(
                run_sentiment_scan,
                CronTrigger(day_of_week=freq, hour=f"{start_h}-{end_h}/{interval_hours}", timezone=user_tz),
                id='sentiment_scan_job'
            )
            logger.info("Sentiment Scan scheduled for %s between %s-%s every %d hours.", freq, start_time, end_time, interval_hours)
        except Exception as e:
            logger.error("Failed to schedule Sentiment Scan: %s", e)

    crash_cfg = scheduling.get("CRASH_ALERTS", {})
    moon_cfg = scheduling.get("MOONSHOT_ALERTS", {})
    crash_enabled = crash_cfg.get("ENABLED", False)
    moon_enabled = moon_cfg.get("ENABLED", False)
    if crash_enabled or moon_enabled:
        active_cfg = crash_cfg if crash_enabled else moon_cfg
        freq = active_cfg.get("FREQUENCY", "mon-fri")
        start_time = active_cfg.get("START_TIME", "09:30")
        end_time = active_cfg.get("END_TIME", "16:00")
        interval_mins = int(active_cfg.get("INTERVAL_MINUTES", 10))
        try:
            start_h, _ = map(int, start_time.split(':'))
            end_h, _ = map(int, end_time.split(':'))
            scheduler.add_job(
                run_intraday_orchestrator,
                CronTrigger(day_of_week=freq, hour=f"{start_h}-{end_h}", minute=f"*/{interval_mins}", timezone=user_tz),
                id='intraday_orchestrator_job'
            )
            logger.info("Unified Intraday Orchestrator scheduled for %s between %s-%s every %d mins.", freq, start_time, end_time, interval_mins)
        except Exception as e:
            logger.error("Failed to schedule Intraday Orchestrator: %s", e)

    maint_cfg = scheduling.get("MAINTENANCE", {})
    if maint_cfg.get("ENABLED", True):
        time_str = maint_cfg.get("TIME", "02:00")
        day_of_week = maint_cfg.get("DAY_OF_WEEK", "sun")
        try:
            hour, minute = map(int, time_str.split(':'))
            scheduler.add_job(
                run_maintenance_engine,
                CronTrigger(day_of_week=day_of_week, hour=hour, minute=minute, timezone=user_tz),
                id='maintenance_job'
            )
            logger.info("DB/File Maintenance scheduled for %s at %s", day_of_week, time_str)
        except Exception as e:
            logger.error("Failed to schedule Maintenance Job: %s", e)

    quant_cfg = scheduling.get("QUANT_ENGINE", {})
    quant_days_list = quant_cfg.get("DAYS", ["mon", "tue", "wed", "thu", "fri"])
    quant_days = ",".join(quant_days_list) if quant_days_list else "mon-fri"
    quant_time = quant_cfg.get("TIME", "01:00")
    try:
        hour, minute = map(int, quant_time.split(':'))
        scheduler.add_job(
            run_overnight_quant_scan,
            CronTrigger(day_of_week=quant_days, hour=hour, minute=minute, timezone=user_tz),
            id='overnight_quant_scan_job'
        )
        logger.info("Overnight Quant Scan scheduled for %s at %s", quant_days, quant_time)
    except Exception as e:
        logger.error("Failed to schedule Overnight Quant Scan: %s", e)

    earn_cfg = scheduling.get("EARNINGS_ENGINE", {})
    earn_days_list = earn_cfg.get("DAYS", ["sat"])
    earn_days = ",".join(earn_days_list) if earn_days_list else "sat"
    earn_time = earn_cfg.get("TIME", "10:00")
    try:
        hour, minute = map(int, earn_time.split(':'))
        scheduler.add_job(
            run_weekend_earnings_scan,
            CronTrigger(day_of_week=earn_days, hour=hour, minute=minute, timezone=user_tz),
            id='weekend_earnings_vol_scan_job'
        )
        logger.info("Earnings Volatility Scan scheduled for %s at %s", earn_days, earn_time)
    except Exception as e:
        logger.error("Failed to schedule Earnings Volatility Scan: %s", e)

    # Morning Briefing — always schedule; ENABLED flag only gates Nextcloud Talk sending
    disp_cfg = scheduling.get("DISPATCHER", {})
    disp_days_list = disp_cfg.get("DAYS", ["mon", "tue", "wed", "thu", "fri"])
    disp_days = ",".join(disp_days_list) if disp_days_list else "mon-fri"
    disp_time = disp_cfg.get("TIME", "07:15")
    try:
        hour, minute = map(int, disp_time.split(':'))
        scheduler.add_job(
            run_morning_briefing_dispatch,
            CronTrigger(day_of_week=disp_days, hour=hour, minute=minute, timezone=user_tz),
            id='morning_briefing_dispatch_job'
        )
        logger.info("Morning Briefing scheduled for %s at %s", disp_days, disp_time)
    except Exception as e:
        logger.error("Failed to schedule Morning Briefing: %s", e)

    # Lunchtime Briefing — always schedule; ENABLED flag only gates Nextcloud Talk sending
    lunch_cfg = scheduling.get("LUNCH_DISPATCHER", {})
    lunch_days_list = lunch_cfg.get("DAYS", ["mon", "tue", "wed", "thu", "fri"])
    lunch_days = ",".join(lunch_days_list) if lunch_days_list else "mon-fri"
    lunch_time = lunch_cfg.get("TIME", "12:00")
    try:
        hour, minute = map(int, lunch_time.split(':'))
        scheduler.add_job(
            run_lunchtime_briefing_dispatch,
            CronTrigger(day_of_week=lunch_days, hour=hour, minute=minute, timezone=user_tz),
            id='lunchtime_briefing_dispatch_job'
        )
        logger.info("Lunchtime Briefing scheduled for %s at %s", lunch_days, lunch_time)
    except Exception as e:
        logger.error("Failed to schedule Lunchtime Briefing: %s", e)

    uni_cfg = scheduling.get("UNIVERSE_ENGINE", {})
    uni_days_list = uni_cfg.get("DAYS", ["sat"])
    uni_days = ",".join(uni_days_list) if uni_days_list else "sat"
    uni_time = uni_cfg.get("TIME", "02:00")
    try:
        hour, minute = map(int, uni_time.split(':'))
        scheduler.add_job(
            run_weekend_universe_routine,
            CronTrigger(day_of_week=uni_days, hour=hour, minute=minute, timezone=user_tz),
            id='universe_routine_job'
        )
        logger.info("Weekend Universe Routine scheduled for %s at %s", uni_days, uni_time)
    except Exception as e:
        logger.error("Failed to schedule Weekend Universe Routine: %s", e)

    ml_backfill_cfg = scheduling.get("ML_BACKFILL", {})
    if ml_backfill_cfg.get("ENABLED", False):
        backfill_days_list = ml_backfill_cfg.get("DAYS", ["sat"])
        backfill_days = ",".join(backfill_days_list) if backfill_days_list else "sat"
        backfill_time = ml_backfill_cfg.get("TIME", "02:00")
        try:
            hour, minute = map(int, backfill_time.split(':'))
            scheduler.add_job(
                run_ml_backfill,
                CronTrigger(day_of_week=backfill_days, hour=hour, minute=minute, timezone=user_tz),
                id='ml_backfill_job'
            )
            logger.info("ML Historical Backfill scheduled for %s at %s", backfill_days, backfill_time)
        except Exception as e:
            logger.error("Failed to schedule ML Backfill: %s", e)

    ml_training_cfg = scheduling.get("ML_TRAINING", {})
    if ml_training_cfg.get("ENABLED", True):
        train_days_list = ml_training_cfg.get("DAYS", ["sun"])
        train_days = ",".join(train_days_list) if train_days_list else "sun"
        train_time = ml_training_cfg.get("TIME", "04:00")
        try:
            hour, minute = map(int, train_time.split(':'))
            scheduler.add_job(
                run_ml_training,
                CronTrigger(day_of_week=train_days, hour=hour, minute=minute, timezone=user_tz),
                id='ml_training_job'
            )
            logger.info("ML Global Training scheduled for %s at %s", train_days, train_time)
        except Exception as e:
            logger.error("Failed to schedule ML Training: %s", e)

    ml_infer_cfg = scheduling.get("ML_INFERENCE", {})
    if ml_infer_cfg.get("ENABLED", True):
        infer_days_list = ml_infer_cfg.get("DAYS", ["mon", "tue", "wed", "thu", "fri"])
        infer_days = ",".join(infer_days_list) if infer_days_list else "mon-fri"
        infer_time = ml_infer_cfg.get("TIME", "01:30")
        try:
            hour, minute = map(int, infer_time.split(':'))
            scheduler.add_job(
                run_ml_inference,
                CronTrigger(day_of_week=infer_days, hour=hour, minute=minute, timezone=user_tz),
                id='ml_inference_job'
            )
            logger.info("Daily ML Inference scheduled for %s at %s", infer_days, infer_time)
        except Exception as e:
            logger.error("Failed to schedule ML Inference: %s", e)

    ft_cfg = scheduling.get("FREETRADE_SYNC", {})
    if ft_cfg.get("ENABLED", False):
        ft_days_list = ft_cfg.get("DAYS", ["mon", "tue", "wed", "thu", "fri"])
        ft_days = ",".join(ft_days_list) if ft_days_list else "mon-fri"
        ft_time = ft_cfg.get("TIME", "03:00")
        try:
            hour, minute = map(int, ft_time.split(':'))
            scheduler.add_job(
                run_freetrade_sync,
                CronTrigger(day_of_week=ft_days, hour=hour, minute=minute, timezone=user_tz),
                id='freetrade_sync_job'
            )
            logger.info("Freetrade Sync scheduled for %s at %s", ft_days, ft_time)
        except Exception as e:
            logger.error("Failed to schedule Freetrade Sync: %s", e)

    macro_cfg = scheduling.get("MACRO_ENGINE", {})
    if macro_cfg.get("ENABLED", True):
        calendar_time = macro_cfg.get("CALENDAR_TIME", "04:00")
        data_day = macro_cfg.get("DATA_DAY", "sat")
        data_time = macro_cfg.get("DATA_TIME", "05:00")
        try:
            cal_hour, cal_minute = map(int, calendar_time.split(':'))
            scheduler.add_job(
                run_macro_calendar_update,
                CronTrigger(day_of_week='mon-sun', hour=cal_hour, minute=cal_minute, timezone=user_tz),
                id='macro_calendar_job'
            )
            logger.info("Macro Calendar Update scheduled daily at %s", calendar_time)

            data_hour, data_minute = map(int, data_time.split(':'))
            scheduler.add_job(
                run_macro_data_update,
                CronTrigger(day_of_week=data_day, hour=data_hour, minute=data_minute, timezone=user_tz),
                id='macro_data_job'
            )
            logger.info("Macro Data Update scheduled for %s at %s", data_day, data_time)
        except Exception as e:
            logger.error("Failed to schedule Macro Engine Jobs: %s", e)

    cb_nlp_cfg = scheduling.get("CB_NLP_ALERT", {})
    if cb_nlp_cfg.get("ENABLED", True):
        cb_freq = cb_nlp_cfg.get("FREQUENCY", "mon-fri")
        cb_start = cb_nlp_cfg.get("START_TIME", "12:00")
        cb_end = cb_nlp_cfg.get("END_TIME", "21:00")
        cb_interval = int(cb_nlp_cfg.get("INTERVAL_MINUTES", 30))
        try:
            cb_start_h, _ = map(int, cb_start.split(':'))
            cb_end_h, _ = map(int, cb_end.split(':'))
            scheduler.add_job(
                run_central_bank_nlp_check,
                CronTrigger(day_of_week=cb_freq, hour=f"{cb_start_h}-{cb_end_h}", minute=f"*/{cb_interval}", timezone=user_tz),
                id='cb_nlp_alert_job'
            )
            logger.info("Central Bank NLP Alert polling scheduled %s %s-%s UTC every %dm", cb_freq, cb_start, cb_end, cb_interval)
        except Exception as e:
            logger.error("Failed to schedule Central Bank NLP Alert: %s", e)

    sync_indices_cfg = scheduling.get("SYNC_INDICES", {})
    if sync_indices_cfg.get("ENABLED", False):
        index_days_list = sync_indices_cfg.get("DAYS", ["sat"])
        index_days = ",".join(index_days_list) if index_days_list else "sat"
        index_time = sync_indices_cfg.get("TIME", "03:00")
        try:
            hour, minute = map(int, index_time.split(':'))
            scheduler.add_job(
                run_index_scraper,
                CronTrigger(day_of_week=index_days, hour=hour, minute=minute, timezone=user_tz),
                id='index_scraper_job'
            )
            logger.info("Index Scraper scheduled for %s at %s", index_days, index_time)
        except Exception as e:
            logger.error("Failed to schedule Index Scraper: %s", e)

    profiler_cfg = scheduling.get("PROFILER_ENGINE", {})
    if profiler_cfg.get("ENABLED", False):
        profiler_days_list = profiler_cfg.get("DAYS", ["sun"])
        profiler_days = ",".join(profiler_days_list) if profiler_days_list else "sun"
        profiler_time = profiler_cfg.get("TIME", "05:00")
        try:
            hour, minute = map(int, profiler_time.split(':'))
            scheduler.add_job(
                run_fundamentals_profiler,
                CronTrigger(day_of_week=profiler_days, hour=hour, minute=minute, timezone=user_tz),
                id='fundamentals_profiler_job'
            )
            logger.info("Fundamentals Profiler scheduled for %s at %s", profiler_days, profiler_time)
        except Exception as e:
            logger.error("Failed to schedule Fundamentals Profiler: %s", e)

    # Sequences fundamentals -> metadata -> technicals -> ML inference for the
    # full index universe (FTSE100 + S&P500). Required for GARP, Quality Compounders,
    # and other market-wide reports.
    uds_cfg = scheduling.get("UNIVERSE_DEEP_SYNC", {})
    if uds_cfg.get("ENABLED", False):
        uds_days_list = uds_cfg.get("DAYS", ["sun"])
        uds_days = ",".join(uds_days_list) if uds_days_list else "sun"
        uds_time = uds_cfg.get("TIME", "02:00")
        try:
            hour, minute = map(int, uds_time.split(':'))
            scheduler.add_job(
                run_universe_deep_sync_job,
                CronTrigger(day_of_week=uds_days, hour=hour, minute=minute, timezone=user_tz),
                id='universe_deep_sync_job'
            )
            logger.info("Universe Deep Sync Pipeline scheduled for %s at %s", uds_days, uds_time)
        except Exception as e:
            logger.error("Failed to schedule Universe Deep Sync Pipeline: %s", e)

    # Anomaly Training Job — runs Mon–Fri at 18:30 (after quant_analysis_job at 18:00,
    # before xray_risk_cache_job at 19:00). Controlled by NOTIFICATIONS.ANOMALY_ALERTS.ENABLED.
    anomaly_cfg = notifications.get("ANOMALY_ALERTS", {})
    if anomaly_cfg.get("ENABLED", False):
        try:
            scheduler.add_job(
                run_anomaly_training_job,
                CronTrigger(day_of_week='mon-fri', hour=18, minute=30, timezone=user_tz),
                id='anomaly_training_job',
            )
            logger.info("Anomaly Training job scheduled for mon-fri at 18:30.")
        except Exception as e:
            logger.error("Failed to schedule Anomaly Training job: %s", e)

    ai_c_sched = scheduling.get("AI_CONTAGION", {})
    if ai_c_sched.get("ENABLED", False):
        try:
            start_h = int(ai_c_sched.get("START_TIME", "09:00").split(":")[0])
            end_h   = int(ai_c_sched.get("END_TIME",   "21:00").split(":")[0])
            mins    = int(ai_c_sched.get("INTERVAL_MINUTES", 15))
            freq    = ai_c_sched.get("FREQUENCY", "mon-fri")
            scheduler.add_job(
                run_ai_contagion_job,
                CronTrigger(day_of_week=freq, hour=f"{start_h}-{end_h}", minute=f"*/{mins}", timezone=user_tz),
                id='ai_contagion_job',
                replace_existing=True,
                misfire_grace_time=300,
            )
            logger.info(
                "AI Contagion Monitor scheduled (%s %02d:00–%02d:00 every %dm).",
                freq, start_h, end_h, mins,
            )
        except Exception as e:
            logger.error("Failed to schedule AI Contagion Monitor: %s", e)

    trap_sched = scheduling.get("TRAP_MONITORS", {})
    if trap_sched.get("ENABLED", False):
        try:
            start_h = int(trap_sched.get("START_TIME", "08:00").split(":")[0])
            end_h   = int(trap_sched.get("END_TIME",   "21:00").split(":")[0])
            mins    = int(trap_sched.get("INTERVAL_MINUTES", 30))
            freq    = trap_sched.get("FREQUENCY", "mon-fri")
            scheduler.add_job(
                run_trap_monitor_job,
                CronTrigger(day_of_week=freq, hour=f"{start_h}-{end_h}", minute=f"*/{mins}", timezone=user_tz),
                id='trap_monitor_job',
                replace_existing=True,
                misfire_grace_time=300,
            )
            logger.info(
                "Trap Monitor scheduled (%s %02d:00–%02d:00 every %dm).",
                freq, start_h, end_h, mins,
            )
        except Exception as e:
            logger.error("Failed to schedule Trap Monitor: %s", e)

    news_cfg = scheduling.get("NEWS_FEED", {})
    if news_cfg.get("ENABLED", False):
        news_freq = news_cfg.get("FREQUENCY", "mon-fri")
        news_interval_h = int(news_cfg.get("INTERVAL_HOURS", 4))
        news_start = news_cfg.get("START_TIME", "08:00")
        news_end = news_cfg.get("END_TIME", "20:00")
        try:
            start_h, _ = map(int, news_start.split(":"))
            end_h, _ = map(int, news_end.split(":"))
            scheduler.add_job(
                run_news_feed_job,
                CronTrigger(day_of_week=news_freq, hour=f"{start_h}-{end_h}/{news_interval_h}", timezone=user_tz),
                id="news_feed_job",
                replace_existing=True,
            )
            logger.info("News Feed scheduled for %s between %s-%s every %dh.", news_freq, news_start, news_end, news_interval_h)
        except Exception as e:
            logger.error("Failed to schedule News Feed job: %s", e)

    # Always-on: fast-exits silently if no tickers are armed
    try:
        scheduler.add_job(
            run_intraday_dip_scan,
            CronTrigger(day_of_week='mon-fri', hour='7-21', minute='*/2', timezone='UTC'),
            id='intraday_dip_scan_job',
            replace_existing=True,
            misfire_grace_time=60,
        )
        logger.info("Intraday Dip Radar scan scheduled mon-fri 07:00–21:59 UTC every 2 min (covers LSE 08:00–16:30 BST and NYSE 14:30–21:00 BST).")
    except Exception as e:
        logger.error("Failed to schedule Intraday Dip Radar scan: %s", e)

    for _exch in ("LSE", "NYSE"):
        try:
            _params = time_engine.reset_cron_trigger_params(_exch)
            _info = time_engine.EXCHANGE_HOURS[_exch]
            scheduler.add_job(
                lambda e=_exch: run_intraday_dip_reset(e),
                CronTrigger(**_params),
                id=f'intraday_dip_reset_{_exch.lower()}_job',
                replace_existing=True,
            )
            logger.info(
                "Intraday Dip Radar reset for %s scheduled mon-fri at %02d:%02d %s.",
                _exch, _params["hour"], _params["minute"], _info["tz"],
            )
        except Exception as e:
            logger.error("Failed to schedule Intraday Dip Radar reset for %s: %s", _exch, e)

    # Always-on: X-ray Risk Cache — runs daily Mon–Fri at 19:00 (after market close).
    # No config flag required; the X-ray report is always available.
    try:
        scheduler.add_job(
            run_xray_risk_cache_job,
            CronTrigger(day_of_week='mon-fri', hour=19, minute=0, timezone=user_tz),
            id='xray_risk_cache_job',
        )
        logger.info("X-ray Risk Cache job scheduled for mon-fri at 19:00.")
    except Exception as e:
        logger.error("Failed to schedule X-ray Risk Cache job: %s", e)

    smgb_pred_cfg = scheduling.get("SMGB_PREDICTOR", {})
    if smgb_pred_cfg.get("ENABLED", False):
        pre_time = smgb_pred_cfg.get("PRE_US_OPEN_TIME", "13:30")
        post_time = smgb_pred_cfg.get("POST_US_CLOSE_TIME", "22:00")
        try:
            pre_h, pre_m = map(int, pre_time.split(':'))
            scheduler.add_job(
                run_smgb_predictor_job,
                CronTrigger(day_of_week='mon-fri', hour=pre_h, minute=pre_m, timezone=user_tz),
                id='smgb_predictor_job',
            )
            logger.info("SMGB predictor (pre-US-open) scheduled for mon-fri at %s.", pre_time)
        except Exception as e:
            logger.error("Failed to schedule SMGB predictor (pre-US-open): %s", e)
        try:
            post_h, post_m = map(int, post_time.split(':'))
            scheduler.add_job(
                run_smgb_predictor_job,
                CronTrigger(day_of_week='mon-fri', hour=post_h, minute=post_m, timezone=user_tz),
                id='smgb_predictor_post_job',
            )
            logger.info("SMGB predictor (post-US-close) scheduled for mon-fri at %s.", post_time)
        except Exception as e:
            logger.error("Failed to schedule SMGB predictor (post-US-close): %s", e)

    # Always-on: SMGB.L Actual Fill — runs Mon–Fri at 09:15 GMT (45 min after LSE opens).
    # Fetches the actual open price for that morning and resolves the previous prediction row.
    try:
        scheduler.add_job(
            run_smgb_actual_fill,
            CronTrigger(day_of_week='mon-fri', hour=9, minute=15, timezone=user_tz),
            id='smgb_actual_fill_job',
        )
        logger.info("SMGB actual-fill job scheduled for mon-fri at 09:15.")
    except Exception as e:
        logger.error("Failed to schedule SMGB actual-fill job: %s", e)

    # Always-on: ETF Predictor actual-fill — runs Mon–Fri at 09:20 UTC.
    try:
        scheduler.add_job(
            run_etf_actual_fill_job,
            CronTrigger(day_of_week="mon-fri", hour=9, minute=20, timezone="UTC"),
            id="etf_predictor_actual_fill_job",
        )
        logger.info("ETF Predictor actual-fill job scheduled for mon-fri at 09:20 UTC.")
    except Exception as e:
        logger.error("Failed to schedule ETF Predictor actual-fill job: %s", e)

    # Dynamic: register per-config ETF predictor jobs from DB.
    try:
        from database import get_etf_predictor_configs as _get_etf_cfgs
        for _etf_cfg in _get_etf_cfgs():
            if _etf_cfg.get("auto_schedule") and _etf_cfg.get("enabled") and not _etf_cfg.get("deleted_at"):
                register_etf_predictor_jobs(_etf_cfg)
    except Exception as e:
        logger.error("Failed to register ETF predictor jobs from DB: %s", e)

    sys_check_cfg = scheduling.get("SYSTEM_CHECK", {})
    if sys_check_cfg.get("ENABLED", True):
        sys_check_days_list = sys_check_cfg.get("DAYS", ["mon", "tue", "wed", "thu", "fri", "sat", "sun"])
        sys_check_days = ",".join(sys_check_days_list) if sys_check_days_list else "mon-sun"
        sys_check_time = sys_check_cfg.get("TIME", "06:00")
        try:
            hour, minute = map(int, sys_check_time.split(':'))
            scheduler.add_job(
                run_system_check_job,
                CronTrigger(day_of_week=sys_check_days, hour=hour, minute=minute, timezone=user_tz),
                id='system_check_job'
            )
            logger.info("System Configuration Check scheduled for %s at %s", sys_check_days, sys_check_time)
        except Exception as e:
            logger.error("Failed to schedule System Configuration Check: %s", e)


def start_scheduler():
    scheduler.add_listener(_on_job_event, EVENT_JOB_SUBMITTED | EVENT_JOB_EXECUTED | EVENT_JOB_ERROR)
    scheduler.start()


def shutdown_scheduler():
    scheduler.shutdown()


# ---------------------------------------------------------------------------
# Workflow Monitor — declarative job manifest + dependency/conflict engine.
#
# Every scheduler.add_job(... id=X) MUST have a matching JOB_GRAPH entry. Edges
# are derived from each job's declared `produces`/`consumes` data artifacts, so
# a new job with correct declarations auto-wires into the dependency graph.
# The manifest-completeness test enforces this — see tests/test_scheduler_engine.py.
# ---------------------------------------------------------------------------

# `label` MUST match how the job is named in the Settings GUI (panel/card header) so
# the same wording is searchable in both places — never use code-style names here.
# Jobs that share one Settings panel use that panel's name plus a parenthetical.
JOB_GRAPH: dict[str, dict] = {
    "ghostfolio_sync_job":          {"label": "Ghostfolio Sync",                                "category": "data",        "engine": "ghostfolio_sync.py",            "produces": ["portfolio"],                                                  "consumes": []},
    "freetrade_sync_job":           {"label": "Freetrade Broker Integration",                   "category": "data",        "engine": "freetrade_engine.py",           "produces": ["market_universe", "portfolio"],                               "consumes": []},
    "index_scraper_job":            {"label": "Index Constituents Scraper",                     "category": "data",        "engine": "index_engine.py",               "produces": ["index_constituents"],                                         "consumes": []},
    "universe_routine_job":         {"label": "Legacy File Sideloading & Nasdaq Sync",          "category": "universe",    "engine": "universe_engine.py",            "produces": ["market_universe", "quant_signals", "tail_risk", "sentiment", "ml_features"], "consumes": ["historical_parquet", "index_constituents"]},
    "universe_deep_sync_job":       {"label": "Universe Deep Sync Pipeline",                    "category": "universe",    "engine": "universe_deep_sync_engine.py",  "produces": ["fundamentals", "ticker_metadata", "quant_signals", "ml_predictions"], "consumes": ["market_universe", "historical_parquet", "ml_model"]},
    "fundamentals_profiler_job":    {"label": "Fundamentals Data Profiler",                     "category": "data",        "engine": "profile_engine.py",             "produces": ["fundamentals", "asset_profiles"],                             "consumes": ["market_universe"]},
    "overnight_quant_scan_job":     {"label": "Daily Quant Screener (Portfolio & Watchlist)",   "category": "quant",       "engine": "quant_engine.py",               "produces": ["quant_signals", "tail_risk"],                                 "consumes": ["historical_parquet"]},
    "quant_analysis_job":           {"label": "Quantamental Analysis Engine",                   "category": "quant",       "engine": "quant_signals.py",              "produces": ["historical_parquet", "stock_signals", "quant_signals", "market_regimes", "macro_regimes"], "consumes": ["portfolio", "macro_indicators"]},
    "weekend_earnings_vol_scan_job":{"label": "Earnings Volatility Engine",                     "category": "quant",       "engine": "earnings_vol_engine.py",        "produces": ["earnings_volatility"],                                        "consumes": ["historical_parquet"]},
    "ml_backfill_job":              {"label": "Historical Data Backfill & Sync",                "category": "ml",          "engine": "ai_prediction_engine.py",       "produces": ["ml_features"],                                                "consumes": ["historical_parquet", "quant_signals"]},
    "ml_training_job":              {"label": "Global Model Training (Walk-Forward)",           "category": "ml",          "engine": "ai_prediction_engine.py",       "produces": ["ml_model"],                                                   "consumes": ["ml_features"]},
    "ml_inference_job":             {"label": "Daily ML Inference",                             "category": "ml",          "engine": "ai_prediction_engine.py",       "produces": ["ml_predictions"],                                             "consumes": ["quant_signals", "ml_model"]},
    "anomaly_training_job":         {"label": "Isolation Forest Anomaly Detection",             "category": "ml",          "engine": "anomaly_engine.py",             "produces": ["anomaly_models"],                                             "consumes": ["historical_parquet"]},
    "xray_risk_cache_job":          {"label": "Portfolio X-ray Risk Cache",                     "category": "risk",        "engine": "xray_engine.py",                "produces": ["xray_caches"],                                                "consumes": ["historical_parquet", "portfolio"]},
    "sentiment_scan_job":           {"label": "NLP Market Sentiment Engine",                    "category": "sentiment",   "engine": "huggingface_engine.py",         "produces": ["sentiment"],                                                  "consumes": []},
    "news_feed_job":                {"label": "News Feed",                                      "category": "sentiment",   "engine": "news_feed_engine.py",           "produces": ["news_articles", "sentiment"],                                 "consumes": []},
    "market_sentiment_job":         {"label": "Market Sentiment (Fear & Greed)",                "category": "alert",       "engine": "sentiment_engine.py",           "produces": [],                                                             "consumes": ["sentiment", "market_regimes"]},
    "cb_nlp_alert_job":             {"label": "Central Bank NLP Alert",                         "category": "alert",       "engine": "huggingface_engine.py",         "produces": [],                                                             "consumes": ["macro_calendar"]},
    "macro_calendar_job":           {"label": "Macroeconomic Automation Schedulers (Calendar)", "category": "macro",       "engine": "macro_calendar_engine.py",      "produces": ["macro_calendar"],                                             "consumes": []},
    "macro_data_job":               {"label": "Macroeconomic Automation Schedulers (Data)",     "category": "macro",       "engine": "macro_data_engine.py",          "produces": ["macro_indicators"],                                           "consumes": []},
    "earnings_alert_job":           {"label": "Portfolio Earnings Alerts",                      "category": "alert",       "engine": "earnings_engine.py",            "produces": [],                                                             "consumes": ["portfolio"]},
    "insider_alert_job":            {"label": "Insider Trading Alerts",                         "category": "alert",       "engine": "insider_engine.py",             "produces": [],                                                             "consumes": ["portfolio"]},
    "morning_briefing_dispatch_job":{"label": "Quant Briefing Generator & Notifications (Morning)",   "category": "briefing", "engine": "report_dispatcher.py",     "produces": [],                                                             "consumes": ["quant_signals", "stock_signals", "market_regimes", "sentiment", "ml_predictions"]},
    "lunchtime_briefing_dispatch_job":{"label": "Quant Briefing Generator & Notifications (Lunchtime)", "category": "briefing", "engine": "report_dispatcher.py",   "produces": [],                                                             "consumes": ["quant_signals", "stock_signals", "market_regimes", "sentiment"]},
    "intraday_orchestrator_job":    {"label": "Crash & Moonshot Alerts",                        "category": "intraday",    "engine": "intraday_orchestrator.py",      "produces": [],                                                             "consumes": ["intraday_parquet"]},
    "intraday_dip_scan_job":        {"label": "Dip Radar — Intraday Bottom Finder",             "category": "intraday",    "engine": "intraday_bottom_engine.py",     "produces": ["intraday_monitor_results"],                                   "consumes": ["intraday_parquet"]},
    "intraday_dip_reset_lse_job":   {"label": "Dip Radar — Intraday Bottom Finder (LSE reset)", "category": "intraday",    "engine": "intraday_bottom_engine.py",     "produces": ["intraday_monitor_results"],                                   "consumes": []},
    "intraday_dip_reset_nyse_job":  {"label": "Dip Radar — Intraday Bottom Finder (NYSE reset)","category": "intraday",    "engine": "intraday_bottom_engine.py",     "produces": ["intraday_monitor_results"],                                   "consumes": []},
    "ai_contagion_job":             {"label": "AI Sector Contagion Monitor",                    "category": "intraday",    "engine": "ai_contagion_engine.py",        "produces": ["ai_contagion_snapshots"],                                     "consumes": ["intraday_parquet"]},
    "trap_monitor_job":             {"label": "Market Trap & Recovery Monitor",                 "category": "intraday",    "engine": "bull_bear_trap_engine.py",      "produces": ["trap_monitor_results"],                                       "consumes": ["historical_parquet"]},
    "smgb_predictor_job":           {"label": "SMGB.L Price Predictor Schedule (pre-open)",     "category": "predictor",   "engine": "smgb_predictor.py",             "produces": ["smgb_predictions"],                                           "consumes": []},
    "smgb_predictor_post_job":      {"label": "SMGB.L Price Predictor Schedule (post-close)",   "category": "predictor",   "engine": "smgb_predictor.py",             "produces": ["smgb_predictions"],                                           "consumes": []},
    "smgb_actual_fill_job":         {"label": "SMGB.L Price Predictor Schedule (actual fill)",  "category": "predictor",   "engine": "smgb_predictor.py",             "produces": ["smgb_predictions"],                                           "consumes": ["smgb_predictions"]},
    "etf_predictor_actual_fill_job":{"label": "ETF Price Predictors (actual fill)",             "category": "predictor",   "engine": "etf_predictor_engine.py",       "produces": ["etf_predictions"],                                            "consumes": ["etf_predictions"]},
    "maintenance_job":              {"label": "Database & File Maintenance",                    "category": "maintenance", "engine": "maintenance_engine.py",         "produces": [],                                                             "consumes": []},
    "system_check_job":             {"label": "System Configuration Check",                     "category": "maintenance", "engine": "system_check_engine.py",        "produces": [],                                                             "consumes": []},
    "etf_predictor_dynamic":        {"label": "ETF Price Predictors",                           "category": "predictor",   "engine": "etf_predictor_engine.py",       "produces": ["etf_predictions"],                                            "consumes": [], "dynamic": True},
}

# Canonical config-key → job-id map. `config.json` SCHEDULING/NOTIFICATIONS keys and code
# identifiers are NOT user-facing; the one display name lives in JOB_GRAPH[...]["label"].
# This map is the single bridge from a config key to that canonical name (used by the
# Settings Master Matrix, the diagnostics panel, etc.). Several keys may share a job
# (CRASH_ALERTS + MOONSHOT_ALERTS → the one orchestrator).
CONFIG_KEY_TO_JOB: dict[str, str] = {
    "GHOSTFOLIO_SYNC":    "ghostfolio_sync_job",
    "QUANT_ANALYSIS":     "quant_analysis_job",
    "SENTIMENT_ENGINE":   "sentiment_scan_job",
    "CRASH_ALERTS":       "intraday_orchestrator_job",
    "MOONSHOT_ALERTS":    "intraday_orchestrator_job",
    "MAINTENANCE":        "maintenance_job",
    "QUANT_ENGINE":       "overnight_quant_scan_job",
    "EARNINGS_ENGINE":    "weekend_earnings_vol_scan_job",
    "DISPATCHER":         "morning_briefing_dispatch_job",
    "LUNCH_DISPATCHER":   "lunchtime_briefing_dispatch_job",
    "UNIVERSE_ENGINE":    "universe_routine_job",
    "ML_BACKFILL":        "ml_backfill_job",
    "ML_TRAINING":        "ml_training_job",
    "ML_INFERENCE":       "ml_inference_job",
    "FREETRADE_SYNC":     "freetrade_sync_job",
    "MACRO_ENGINE":       "macro_calendar_job",
    "SYNC_INDICES":       "index_scraper_job",
    "PROFILER_ENGINE":    "fundamentals_profiler_job",
    "UNIVERSE_DEEP_SYNC": "universe_deep_sync_job",
    "ANOMALY_ALERTS":     "anomaly_training_job",
    "XRAY_RISK_CACHE":    "xray_risk_cache_job",
    "AI_CONTAGION":       "ai_contagion_job",
    "CB_NLP_ALERT":       "cb_nlp_alert_job",
    "NEWS_FEED":          "news_feed_job",
    "SYSTEM_CHECK":       "system_check_job",
    "SMGB_PREDICTOR":     "smgb_predictor_job",
    "TRAP_MONITORS":      "trap_monitor_job",
    "MARKET_SENTIMENT":   "market_sentiment_job",
    "EARNINGS_ALERTS":    "earnings_alert_job",
    "INSIDER_TRADING":    "insider_alert_job",
}


def job_label(job_id: str) -> str:
    """Canonical GUI display name for a job id — the single source of truth for naming."""
    meta = JOB_GRAPH.get(job_id)
    return meta["label"] if meta else job_id


def display_name_for_config_key(config_key: str) -> str | None:
    job_id = CONFIG_KEY_TO_JOB.get(config_key)
    return job_label(job_id) if job_id else None


def scheduler_display_names() -> dict[str, str]:
    """config_key → canonical display name, for surfaces keyed by config key (e.g. the Matrix)."""
    return {key: job_label(job_id) for key, job_id in CONFIG_KEY_TO_JOB.items()}


_DYNAMIC_ETF_RE = re.compile(r"^etf_predictor_\d+_(pre|post)_job$")
_OVERLAP_BUFFER_MIN = 2
_UNKNOWN_GAP_MIN = 30
_WEEK_MIN = 7 * 24 * 60
_BACKWARDS_FOLLOW_MIN = 240
_BACKWARDS_STALE_MIN = 24 * 60
_WEEKDAY_TO_INT = {"mon": 0, "tue": 1, "wed": 2, "thu": 3, "fri": 4, "sat": 5, "sun": 6}


def _fire_times(schedule: dict | None) -> list[int]:
    """Minute-of-week slots a cron job fires at; empty for interval/non-cron triggers."""
    if not schedule:
        return []
    return [wd * 1440 + schedule["minute_of_day"] for wd in schedule["weekdays"]]


def _resolve_manifest(job_id: str) -> dict | None:
    meta = JOB_GRAPH.get(job_id)
    if meta is not None:
        return None if meta.get("dynamic") else meta
    if _DYNAMIC_ETF_RE.match(job_id):
        return JOB_GRAPH["etf_predictor_dynamic"]
    return None


def _wd_to_int(token: str) -> int | None:
    token = token.strip().lower()
    if token in _WEEKDAY_TO_INT:
        return _WEEKDAY_TO_INT[token]
    if token.isdigit():
        return int(token) % 7
    return None


def _weekdays_from_expr(expr: str) -> set[int]:
    expr = expr.strip().lower()
    if expr in ("*", "?", ""):
        return set(range(7))
    days: set[int] = set()
    for part in expr.split(","):
        part = part.strip()
        if "-" in part:
            a, _, b = part.partition("-")
            ai, bi = _wd_to_int(a), _wd_to_int(b)
            if ai is not None and bi is not None:
                days.update(range(ai, bi + 1) if ai <= bi else list(range(ai, 7)) + list(range(0, bi + 1)))
        else:
            wi = _wd_to_int(part)
            if wi is not None:
                days.add(wi)
    return days or set(range(7))


def _first_int_from_expr(expr: str, default: int = 0) -> int:
    token = expr.strip().split(",")[0].split("/")[0].split("-")[0]
    if token in ("*", "?", ""):
        return default
    try:
        return int(token)
    except ValueError:
        return default


def _schedule_slot(trigger) -> tuple[set[int], int] | None:
    try:
        fields = {f.name: str(f) for f in trigger.fields}
    except AttributeError:
        return None
    hour = _first_int_from_expr(fields.get("hour", "0"))
    minute = _first_int_from_expr(fields.get("minute", "0"))
    return _weekdays_from_expr(fields.get("day_of_week", "*")), hour * 60 + minute


def _period_days(weekdays: set[int] | None) -> int:
    return 1 if (weekdays is None or len(weekdays) >= 5) else 7


def _parse_last_run(value):
    if not value:
        return None
    from datetime import datetime, timezone
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M"):
        try:
            return datetime.strptime(value, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    return None


def _job_status(node: dict) -> tuple[str, str]:
    from datetime import datetime, timezone
    if not node["enabled"]:
        return "disabled", "disabled"
    if node.get("last_status") == "error":
        return "red", "error"
    last_run = _parse_last_run(node.get("last_run"))
    if last_run is None:
        return "amber", "never_run"
    weekdays = set(node["schedule"]["weekdays"]) if node.get("schedule") else None
    period = _period_days(weekdays)
    age_days = (datetime.now(timezone.utc) - last_run).total_seconds() / 86400.0
    if age_days > period * 2 + 2:
        return "red", "overdue"
    if age_days > period + 1:
        return "amber", "stale"
    return "green", "ok"


def _build_node(job_id: str, meta: dict, job, run_row: dict) -> dict:
    from datetime import timezone
    enabled = job is not None
    schedule = None
    if enabled:
        slot = _schedule_slot(job.trigger)
        if slot is not None:
            weekdays, minute_of_day = slot
            schedule = {"weekdays": sorted(weekdays), "minute_of_day": minute_of_day}
    label = meta["label"]
    if _DYNAMIC_ETF_RE.match(job_id):
        cfg_id = job_id.split("_")[2]
        phase = "pre-open" if job_id.endswith("pre_job") else "post-close"
        label = f"ETF Price Predictor #{cfg_id} ({phase})"
    runs = run_row or {}
    next_run = None
    next_run_time = getattr(job, "next_run_time", None) if enabled else None
    if next_run_time is not None:
        next_run = next_run_time.astimezone(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    node = {
        "id": job_id,
        "label": label,
        "category": meta["category"],
        "engine": meta["engine"],
        "produces": list(meta.get("produces", [])),
        "consumes": list(meta.get("consumes", [])),
        "enabled": enabled,
        "last_run": runs.get("last_run"),
        "last_status": runs.get("last_status"),
        "avg_duration_sec": runs.get("avg_duration_sec"),
        "next_run": next_run,
        "schedule": schedule,
    }
    node["status"], node["status_reason"] = _job_status(node)
    return node


def build_workflow_graph() -> dict:
    runs = get_all_job_last_runs()
    live = {j.id: j for j in scheduler.get_jobs()}
    nodes, seen = [], set()
    for job_id, meta in JOB_GRAPH.items():
        if meta.get("dynamic"):
            continue
        nodes.append(_build_node(job_id, meta, live.get(job_id), runs.get(job_id, {})))
        seen.add(job_id)
    for job_id, job in live.items():
        if job_id in seen:
            continue
        meta = _resolve_manifest(job_id)
        if meta is not None:
            nodes.append(_build_node(job_id, meta, job, runs.get(job_id, {})))
    edges = _derive_edges(nodes)
    return {"nodes": nodes, "edges": edges}


def _derive_edges(nodes: list[dict]) -> list[dict]:
    from collections import defaultdict
    producers = defaultdict(list)
    for n in nodes:
        for artifact in n["produces"]:
            producers[artifact].append(n["id"])
    edges, seen = [], set()
    for n in nodes:
        for artifact in n["consumes"]:
            for producer_id in producers.get(artifact, []):
                if producer_id == n["id"]:
                    continue
                key = (producer_id, n["id"], artifact)
                if key in seen:
                    continue
                seen.add(key)
                edges.append({"from": producer_id, "to": n["id"], "via": artifact})
    return edges


def detect_workflow_conflicts(graph: dict) -> list[dict]:
    nodes = {n["id"]: n for n in graph["nodes"]}
    conflicts = []
    for edge in graph["edges"]:
        producer, consumer = nodes.get(edge["from"]), nodes.get(edge["to"])
        if not producer or not consumer:
            continue
        if not consumer["enabled"]:
            continue
        if not producer["enabled"]:
            conflicts.append({
                "type": "disabled_upstream", "severity": "warning",
                "job_id": consumer["id"], "related": producer["id"],
                "message": f"{consumer['label']} depends on {producer['label']} (via {edge['via']}), which is disabled — its inputs may be stale or missing.",
            })
            continue
        p_fires, c_fires = _fire_times(producer.get("schedule")), _fire_times(consumer.get("schedule"))
        if not p_fires or not c_fires:
            continue
        back_gap = min((cf - pf) % _WEEK_MIN for cf in c_fires for pf in p_fires)
        fwd_gap = min((pf - cf) % _WEEK_MIN for cf in c_fires for pf in p_fires)
        avg = producer.get("avg_duration_sec")
        if avg is None:
            if back_gap < _UNKNOWN_GAP_MIN:
                conflicts.append({
                    "type": "overlap_risk", "severity": "info",
                    "job_id": consumer["id"], "related": producer["id"],
                    "message": f"{consumer['label']} starts {back_gap} min after {producer['label']} (its source of {edge['via']}); the producer's typical runtime is not yet known, so overlap cannot be ruled out.",
                })
        elif back_gap < avg / 60.0 + _OVERLAP_BUFFER_MIN:
            conflicts.append({
                "type": "overlap_risk", "severity": "warning",
                "job_id": consumer["id"], "related": producer["id"],
                "message": f"{consumer['label']} starts {back_gap} min after {producer['label']} (its source of {edge['via']}), but {producer['label']} typically runs ~{avg / 60.0:.0f} min — it may still be running, so {consumer['label']} could read incomplete data.",
            })
        if fwd_gap <= _BACKWARDS_FOLLOW_MIN and back_gap >= _BACKWARDS_STALE_MIN:
            conflicts.append({
                "type": "backwards_ordering", "severity": "critical",
                "job_id": consumer["id"], "related": producer["id"],
                "message": f"{consumer['label']} runs {fwd_gap} min before {producer['label']}, the upstream producer of {edge['via']} — it cannot use the same cycle's output and falls back on data at least {back_gap // 60}h old.",
            })
    for node in graph["nodes"]:
        reason = node.get("status_reason")
        if reason == "error":
            conflicts.append({
                "type": "last_run_error", "severity": "critical",
                "job_id": node["id"], "related": None,
                "message": f"{node['label']} failed on its last run.",
            })
        elif reason == "overdue":
            conflicts.append({
                "type": "stale_never_run", "severity": "warning",
                "job_id": node["id"], "related": None,
                "message": f"{node['label']} is enabled but has not run recently (last run: {node.get('last_run') or 'never'}).",
            })
        elif reason == "never_run":
            conflicts.append({
                "type": "stale_never_run", "severity": "info",
                "job_id": node["id"], "related": None,
                "message": f"{node['label']} is enabled and scheduled but has never recorded a run.",
            })
    return conflicts
