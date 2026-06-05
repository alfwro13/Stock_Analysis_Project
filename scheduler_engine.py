# scheduler_engine.py
import threading
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from config import load_config
from sentiment_engine import run_nextcloud_alert, update_all_sentiment, run_central_bank_nlp_alert
from earnings_engine import run_earnings_alert
from insider_engine import run_insider_alert
from intraday_orchestrator import IntradayOrchestrator
from nextcloud_talk import send_text_message
from maintenance_engine import MaintenanceEngine
from data_engine import DataEngine
from quant_signals import QuantEngine
from ghostfolio_sync import GhostfolioSyncEngine
from quant_engine import run_daily_quant_scan
from earnings_vol_engine import run_earnings_vol_scan
from report_dispatcher import push_morning_quant_briefing, push_lunchtime_quant_briefing
from database import get_universe_tickers, get_connection
from universe_engine import update_market_universe
from profile_engine import run_profile_audit
from regime_engine import calculate_market_regime
from ai_prediction_engine import train_global_ml_model, update_daily_ml_predictions, run_historical_backfill
from risk_engine import update_all_tail_risks
from freetrade_engine import sync_freetrade_universe
from universe_deep_sync_engine import run_universe_deep_sync
# Import new Macro Engines
from macro_calendar_engine import update_macro_calendar
from macro_data_engine import update_macro_indicators
from xray_engine import run_xray_precompute
from news_feed_engine import run_news_feed_job

logger = logging.getLogger(__name__)

# --- Background Task Scheduler Setup ---
scheduler = BackgroundScheduler()

def log_sched_notification(msg_type: str, msg_text: str):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)", (msg_type, msg_text))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to log notification: {e}")

def record_job_run(job_id: str):
    from datetime import datetime
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO scheduler_run_log (job_id, last_run) VALUES (?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET last_run = excluded.last_run",
            (job_id, datetime.now().strftime('%Y-%m-%d %H:%M'))
        )
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to record job run for {job_id}: {e}")

def get_all_job_last_runs() -> dict:
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT job_id, last_run FROM scheduler_run_log")
        rows = cursor.fetchall()
        conn.close()
        return {row[0]: row[1] for row in rows}
    except Exception:
        return {}

def trigger_sentiment_report():
    """Triggered by the scheduler to run the Nextcloud Market Sentiment alert."""
    try:
        run_nextcloud_alert()
    finally:
        record_job_run('market_sentiment_job')

def run_intraday_orchestrator():
    """Executes the unified high-frequency intraday scan (Crash + Moonshot)."""
    try:
        IntradayOrchestrator().run()
    finally:
        record_job_run('intraday_orchestrator_job')

def run_maintenance_engine():
    """Executes the background database and file system maintenance."""
    try:
        MaintenanceEngine().run()
    finally:
        record_job_run('maintenance_job')

def run_update_pipeline():
    """Executes the heavy data ingestion and mathematical quant modeling."""
    log_sched_notification("Scheduler", "Started Update Pipeline...")
    try:
        logger.info("Background update initiated.")
        DataEngine().update_all_data()
        from regime_engine import calculate_systemic_macro_threat, calculate_market_regime
        calculate_systemic_macro_threat()
        calculate_market_regime()
        QuantEngine().run_all()
        logger.info("Background update complete.")
        log_sched_notification("Success", "Update Pipeline completed successfully.")
    except Exception as e:
        log_sched_notification("Error", f"Update Pipeline failed: {e}")
    finally:
        record_job_run('quant_analysis_job')

def run_ghostfolio_sync():
    """Executes the Ghostfolio API Sync to extract account holdings."""
    log_sched_notification("Scheduler", "Started Ghostfolio Sync...")
    try:
        sync_engine = GhostfolioSyncEngine()
        sync_engine.run_full_sync()
        log_sched_notification("Success", "Ghostfolio Sync completed successfully.")
    except Exception as e:
        log_sched_notification("Error", f"Ghostfolio Sync failed: {e}")
    finally:
        record_job_run('ghostfolio_sync_job')

def run_freetrade_sync():
    """Executes the Freetrade Universe CSV Sync."""
    log_sched_notification("Scheduler", "Started Freetrade Sync...")
    try:
        logger.info("Freetrade sync initiated.")
        sync_freetrade_universe()
        logger.info("Freetrade sync complete.")
        log_sched_notification("Success", "Freetrade Sync completed successfully.")
    except Exception as e:
        logger.error(f"Freetrade Sync Failed: {e}")
        log_sched_notification("Error", f"Freetrade Sync failed: {e}")
    finally:
        record_job_run('freetrade_sync_job')

def run_sentiment_scan():
    """Executes the standalone NLP Sentiment pipeline."""
    log_sched_notification("Scheduler", "Started Sentiment Scan...")
    try:
        logger.info("Sentiment scan initiated.")
        engine = DataEngine()
        all_tickers = engine.get_all_tickers()
        update_all_sentiment(all_tickers)
        logger.info("Sentiment scan complete.")
        log_sched_notification("Success", "Sentiment Scan completed successfully.")
    except Exception as e:
        logger.error(f"Sentiment Scan Failed: {e}")
        log_sched_notification("Error", f"Sentiment Scan failed: {e}")
    finally:
        record_job_run('sentiment_scan_job')

def run_overnight_quant_scan():
    """
    Fetches the combined list of portfolio and watchlist tickers,
    executes the resumable daily quant scan, and Tail Risk.
    """
    log_sched_notification("Scheduler", "Started Overnight Quant Scan...")
    try:
        logger.info("Overnight quant scan initiated.")
        engine = DataEngine()
        all_tickers = engine.get_all_tickers()

        # 1. Execute Technical Analysis Pipeline
        run_daily_quant_scan(all_tickers)

        # 2. Execute Tail Risk calculations
        logger.info("Overnight tail risk computation initiated.")
        update_all_tail_risks(all_tickers)

        logger.info("Overnight quant scan complete.")
        log_sched_notification("Success", "Overnight Quant Scan completed successfully.")
    except Exception as e:
        logger.error(f"Overnight Quant Scan Failed: {e}")
        log_sched_notification("Error", f"Overnight Quant Scan failed: {e}")
    finally:
        record_job_run('overnight_quant_scan_job')

def run_weekend_earnings_scan():
    """Executes the quantitative earnings volatility options scan."""
    log_sched_notification("Scheduler", "Started Earnings Volatility Scan...")
    try:
        logger.info("Earnings volatility scan initiated.")
        engine = DataEngine()
        all_tickers = engine.get_all_tickers()
        run_earnings_vol_scan(all_tickers)
        logger.info("Earnings volatility scan complete.")
        log_sched_notification("Success", "Earnings Volatility Scan completed successfully.")
    except Exception as e:
        logger.error(f"Earnings Volatility Scan Failed: {e}")
        log_sched_notification("Error", f"Earnings Volatility Scan failed: {e}")
    finally:
        record_job_run('weekend_earnings_vol_scan_job')

def run_morning_briefing_dispatch():
    """Executes the morning quant briefing dispatch."""
    log_sched_notification("Scheduler", "Started Morning Briefing Dispatch...")
    try:
        logger.info("Morning briefing dispatch initiated.")
        push_morning_quant_briefing()
        logger.info("Morning briefing dispatch complete.")
        log_sched_notification("Success", "Morning Briefing Dispatch completed successfully.")
    except Exception as e:
        logger.error(f"Morning Briefing Dispatch Failed: {e}")
        log_sched_notification("Error", f"Morning Briefing Dispatch failed: {e}")
    finally:
        record_job_run('morning_briefing_dispatch_job')


def run_lunchtime_briefing_dispatch():
    """Executes the lunchtime quant briefing dispatch."""
    log_sched_notification("Scheduler", "Started Lunchtime Briefing Dispatch...")
    try:
        logger.info("Lunchtime briefing dispatch initiated.")
        push_lunchtime_quant_briefing()
        logger.info("Lunchtime briefing dispatch complete.")
        log_sched_notification("Success", "Lunchtime Briefing Dispatch completed successfully.")
    except Exception as e:
        logger.error(f"Lunchtime Briefing Dispatch Failed: {e}")
        log_sched_notification("Error", f"Lunchtime Briefing Dispatch failed: {e}")
    finally:
        record_job_run('lunchtime_briefing_dispatch_job')

def run_weekend_universe_routine():
    """Executes the massive 4000+ Universe Download and Quant Scan."""
    log_sched_notification("Scheduler", "Started Weekend Universe Routine...")
    try:
        logger.info("Weekend universe routine initiated.")
        # 1. Update the Ticker List from the FTP server
        update_market_universe()

        # 2. Extract the fresh list and run the massive quant scan
        all_tickers = get_universe_tickers()
        if all_tickers:
            # 2a. Execute Core Technicals
            run_daily_quant_scan(all_tickers, scan_type='universe')
            # 2b. Execute Heavy Metrics (Risk, Sentiment)
            logger.info("Universe Technicals complete. Proceeding to heavy metric crunch (VaR, Sentiment)...")
            update_all_tail_risks(all_tickers)
            update_all_sentiment(all_tickers)
        else:
            logger.warning("Universe is empty, skipping quant scan.")

        logger.info("Weekend universe routine complete.")
        log_sched_notification("Success", "Weekend Universe Routine completed successfully.")
    except Exception as e:
        logger.error(f"Weekend Universe Routine Failed: {e}")
        log_sched_notification("Error", f"Weekend Universe Routine failed: {e}")
    finally:
        record_job_run('universe_routine_job')

def run_index_scraper():
    """Executes the Wikipedia index constituent scraper."""
    log_sched_notification("Scheduler", "Started Index Constituents Scraper...")
    try:
        logger.info("Index scraper initiated.")
        # Delayed import to avoid circular dependencies before Phase 4 is built
        from index_engine import sync_all_indices
        sync_all_indices()
        logger.info("Index scraper complete.")
        log_sched_notification("Success", "Index Constituents Scraper completed successfully.")
    except Exception as e:
        logger.error(f"Index Scraper Failed: {e}")
        log_sched_notification("Error", f"Index Scraper failed: {e}")
    finally:
        record_job_run('index_scraper_job')

def run_fundamentals_profiler():
    """Executes the heavy rolling metadata audit to download fundamentals (PE, EPS, Sector)."""
    log_sched_notification("Scheduler", "Started Fundamentals Profiler...")
    try:
        logger.info("Fundamentals profiler initiated.")
        from profile_engine import run_profile_audit

        # Read the dynamic batch size from config
        config = load_config()
        batch_size = config.get("SCHEDULING", {}).get("PROFILER_ENGINE", {}).get("BATCH_SIZE", 250)

        run_profile_audit(limit=int(batch_size))

        logger.info("Fundamentals profiler complete.")
        log_sched_notification("Success", "Fundamentals Profiler completed successfully.")
    except Exception as e:
        logger.error(f"Fundamentals Profiler Failed: {e}")
        log_sched_notification("Error", f"Fundamentals Profiler failed: {e}")
    finally:
        record_job_run('fundamentals_profiler_job')

def run_universe_deep_sync_job():
    """
    Scheduler wrapper executing the unified Universe Deep Sync pipeline.

    The orchestrator (universe_deep_sync_engine.run_universe_deep_sync) emits
    its own rich per-stage notifications. This wrapper adds the standard
    scheduler envelope (Started / Success / Error) for consistency with the
    other _job functions in this module.
    """
    log_sched_notification("Scheduler", "Started Universe Deep Sync Pipeline...")
    try:
        run_universe_deep_sync()
        log_sched_notification("Success", "Universe Deep Sync Pipeline job completed.")
    except Exception as e:
        logger.error(f"Universe Deep Sync Pipeline Failed: {e}")
        log_sched_notification("Error", f"Universe Deep Sync Pipeline failed: {e}")
    finally:
        record_job_run('universe_deep_sync_job')


# --- MODULAR ML PIPELINE RUNNERS ---

def run_ml_backfill():
    """Executes the Historical Data Backfill for the Machine Learning pipeline."""
    log_sched_notification("Scheduler", "Started ML Historical Backfill...")
    try:
        logger.info("ML Historical Backfill initiated.")
        run_historical_backfill()
        logger.info("ML Historical Backfill complete.")
        log_sched_notification("Success", "ML Historical Backfill completed successfully.")
    except Exception as e:
        logger.error(f"ML Historical Backfill Failed: {e}")
        log_sched_notification("Error", f"ML Historical Backfill failed: {e}")
    finally:
        record_job_run('ml_backfill_job')

def run_ml_training():
    """Executes the Global Machine Learning Walk-Forward Training cycle."""
    log_sched_notification("Scheduler", "Started ML Global Training...")
    try:
        logger.info("ML Global Training initiated.")
        train_global_ml_model()
        logger.info("ML Global Training complete.")
        log_sched_notification("Success", "ML Global Training completed successfully.")
    except Exception as e:
        logger.error(f"ML Global Training Failed: {e}")
        log_sched_notification("Error", f"ML Global Training failed: {e}")
    finally:
        record_job_run('ml_training_job')

def run_ml_inference():
    """Executes the Daily Machine Learning Prediction Inference."""
    log_sched_notification("Scheduler", "Started Daily ML Inference...")
    try:
        logger.info("Daily ML Inference initiated.")
        tickers = get_universe_tickers()
        if not tickers:
            engine = DataEngine()
            tickers = engine.get_all_tickers()
        if tickers:
            update_daily_ml_predictions(tickers)
        logger.info("Daily ML Inference complete.")
        log_sched_notification("Success", "Daily ML Inference completed successfully.")
    except Exception as e:
        logger.error(f"Daily ML Inference Failed: {e}")
        log_sched_notification("Error", f"Daily ML Inference failed: {e}")
    finally:
        record_job_run('ml_inference_job')

def run_macro_calendar_update():
    """Executes the daily Tier-1 Macro Calendar refresh."""
    log_sched_notification("Scheduler", "Started Macro Calendar Update...")
    try:
        logger.info("Macro calendar update initiated.")
        update_macro_calendar()
        logger.info("Macro calendar update complete.")
        log_sched_notification("Success", "Macro Calendar Update completed successfully.")
    except Exception as e:
        logger.error(f"Macro Calendar Update Failed: {e}")
        log_sched_notification("Error", f"Macro Calendar Update failed: {e}")
    finally:
        record_job_run('macro_calendar_job')

def run_central_bank_nlp_check():
    """Polls for today's passed central bank events and dispatches FinBERT NLP alerts."""
    CB_EVENTS = {
        'Fed Interest Rate Decision', 'FOMC Meeting Minutes',
        'BoE Official Bank Rate', 'BOE Gov Bailey Speaks'
    }
    placeholders = ','.join('?' * len(CB_EVENTS))
    try:
        conn = get_connection()
        cursor = conn.cursor()
        rows = cursor.execute(
            f"""SELECT event_id, event_name, currency FROM macro_calendar
                WHERE DATE(event_date) = DATE('now')
                AND event_date <= datetime('now')
                AND alert_dispatched = 0
                AND event_name IN ({placeholders})""",
            tuple(CB_EVENTS)
        ).fetchall()
        conn.close()

        for event_id, event_name, currency in rows:
            success = run_central_bank_nlp_alert(event_name, currency)
            if success:
                conn = get_connection()
                conn.execute(
                    "UPDATE macro_calendar SET alert_dispatched = 1 WHERE event_id = ?",
                    (event_id,)
                )
                conn.commit()
                conn.close()
                log_sched_notification("Macro NLP", f"Central Bank NLP dispatched for: {event_name}")
    except Exception as e:
        logger.error(f"Central Bank NLP check failed: {e}")
    finally:
        record_job_run('cb_nlp_alert_job')


def run_macro_data_update():
    """Executes the weekly structural Macroeconomic Data sync (FRED/BoE)."""
    log_sched_notification("Scheduler", "Started Macro Data Update...")
    try:
        logger.info("Macro data update initiated.")
        update_macro_indicators()
        logger.info("Macro data update complete.")
        log_sched_notification("Success", "Macro Data Update completed successfully.")
    except Exception as e:
        logger.error(f"Macro Data Update Failed: {e}")
        log_sched_notification("Error", f"Macro Data Update failed: {e}")
    finally:
        record_job_run('macro_data_job')

def run_xray_risk_cache_job():
    """Pre-computes portfolio beta, vol, correlation, and dividend yields for the X-ray report."""
    log_sched_notification("Scheduler", "Started X-ray Risk Cache job...")
    try:
        success = run_xray_precompute()
        if success:
            log_sched_notification("Success", "X-ray Risk Cache updated successfully.")
        else:
            log_sched_notification("Warning", "X-ray Risk Cache job completed with warnings — check logs.")
    except Exception as e:
        logger.error(f"X-ray Risk Cache job failed: {e}")
        log_sched_notification("Error", f"X-ray Risk Cache job failed: {e}")
    finally:
        record_job_run('xray_risk_cache_job')


def run_anomaly_training_job():
    """Nightly retraining of per-ticker Isolation Forest anomaly models (Mon–Fri 18:30)."""
    log_sched_notification("Scheduler", "Started Anomaly Training job...")
    try:
        from anomaly_engine import AnomalyEngine
        from config import load_config, HISTORICAL_DIR
        config = load_config()
        all_tickers = DataEngine().get_all_tickers()
        engine = AnomalyEngine(config)
        engine.train_all(all_tickers, HISTORICAL_DIR)
        engine.backfill_all(all_tickers, HISTORICAL_DIR)
        log_sched_notification("Success", "Anomaly Training & backfill completed.")
    except Exception as e:
        logger.error("Anomaly Training job failed: %s", e)
        log_sched_notification("Error", f"Anomaly Training job failed: {e}")
    finally:
        record_job_run('anomaly_training_job')


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
    conn = get_connection()
    try:
        engine = AIContagionEngine(config)
        candidates = engine.scan(conn)

        record_scan_snapshot(conn, candidates)

        if not candidates:
            return

        orch = IntradayOrchestrator(config)
        contagion_cfg = config.get("NOTIFICATIONS", {}).get("AI_CONTAGION", {})
        nextcloud_enabled = contagion_cfg.get("ENABLED", False)

        for event in candidates:
            suppress = orch._evaluate_alert_gate(
                "AIContagion", event["ticker"], event["price"], event["reason"], conn
            )
            if suppress:
                logger.info("AIContagion: alert suppressed by gate (cooldown/rearm).")
                continue

            if nextcloud_enabled:
                msg = _build_contagion_message(event, config)
                try:
                    ok = send_text_message(msg, config)
                except Exception as e:
                    logger.error(f"AIContagion: Nextcloud dispatch failed: {e}")
                    ok = False
            else:
                ok = True  # record dedup state even when Nextcloud is disabled

            if ok:
                orch.record_alert_fired(
                    "AIContagion", event["ticker"], event["price"], event["reason"], conn
                )
                leaders_summary = ", ".join(
                    f"{s['ticker']} ({s['intraday_pct']:+.2f}%)"
                    for s in event.get("leader_shocks", [])
                )
                orch.log_notification_feed(
                    "AIContagion",
                    f"Contagion: {leaders_summary}",
                    conn,
                )
                logger.warning("AIContagion: alert fired. Leaders: %s", leaders_summary)
    except Exception as e:
        logger.error(f"AI Contagion job failed: {e}")
        log_sched_notification("Error", f"AI Contagion job failed: {e}")
    finally:
        conn.close()
        record_job_run('ai_contagion_job')


def reload_scheduler():
    """Reads the latest config.json and updates APScheduler dynamically."""
    logger.info("Reloading scheduled jobs from configuration...")
    scheduler.remove_all_jobs()
    
    config = load_config()
    notifications = config.get("NOTIFICATIONS", {})
    scheduling = config.get("SCHEDULING", {})
    
    # 1. Market Sentiment Job
    sentiment_cfg = notifications.get("MARKET_SENTIMENT", {})
    if sentiment_cfg.get("ENABLED"):
        time_str = sentiment_cfg.get("TIME", "09:30")
        freq = sentiment_cfg.get("FREQUENCY", "mon-fri")
        try:
            hour, minute = map(int, time_str.split(':'))
            scheduler.add_job(
                trigger_sentiment_report,
                CronTrigger(day_of_week=freq, hour=hour, minute=minute),
                id='market_sentiment_job'
            )
            logger.info(f"Market Sentiment Job scheduled for {freq} at {time_str}")
        except Exception as e:
            logger.error(f"Failed to schedule Market Sentiment: {e}")

    # 2. Earnings Alerts Job
    earnings_cfg = notifications.get("EARNINGS_ALERTS", {})
    if earnings_cfg.get("ENABLED"):
        time_str = earnings_cfg.get("TIME", "08:00")
        try:
            hour, minute = map(int, time_str.split(':'))
            scheduler.add_job(
                run_earnings_alert,
                CronTrigger(day_of_week='mon-fri', hour=hour, minute=minute),
                id='earnings_alert_job'
            )
            logger.info(f"Earnings Alerts Job scheduled for mon-fri at {time_str}")
        except Exception as e:
            logger.error(f"Failed to schedule Earnings Alerts: {e}")
    
    # 3. Insider Trading Alerts Job
    insider_cfg = notifications.get("INSIDER_TRADING", {})
    if insider_cfg.get("ENABLED_PORTFOLIO") or insider_cfg.get("ENABLED_WATCHLIST"):
        time_str = insider_cfg.get("TIME", "18:00")
        freq = insider_cfg.get("FREQUENCY", "mon-fri")
        try:
            hour, minute = map(int, time_str.split(':'))
            scheduler.add_job(
                run_insider_alert,
                CronTrigger(day_of_week=freq, hour=hour, minute=minute),
                id='insider_alert_job'
            )
            logger.info(f"Insider Trading Alert Job scheduled for {freq} at {time_str}")
        except Exception as e:
            logger.error(f"Failed to schedule Insider Alerts: {e}")

    # 4. Core System Schedulers (Ghostfolio & Quant Analysis)
    ghost_cfg = scheduling.get("GHOSTFOLIO_SYNC", {})
    if ghost_cfg.get("ENABLED"):
        interval = int(ghost_cfg.get("INTERVAL_HOURS", 0))
        freq = ghost_cfg.get("FREQUENCY", "mon-fri")
        if interval > 0:
            scheduler.add_job(run_ghostfolio_sync, IntervalTrigger(hours=interval), id='ghostfolio_sync_job')
            logger.info(f"Ghostfolio Sync scheduled every {interval} hours.")
        else:
            time_str = ghost_cfg.get("TIME", "06:00")
            try:
                hour, minute = map(int, time_str.split(':'))
                scheduler.add_job(
                    run_ghostfolio_sync, 
                    CronTrigger(day_of_week=freq, hour=hour, minute=minute), 
                    id='ghostfolio_sync_job'
                )
                logger.info(f"Ghostfolio Sync scheduled for {freq} at {time_str}")
            except Exception as e:
                logger.error(f"Failed to schedule Ghostfolio Sync: {e}")

    quant_cfg = scheduling.get("QUANT_ANALYSIS", {})
    if quant_cfg.get("ENABLED"):
        interval = int(quant_cfg.get("INTERVAL_HOURS", 0))
        freq = quant_cfg.get("FREQUENCY", "mon-fri")
        if interval > 0:
            scheduler.add_job(run_update_pipeline, IntervalTrigger(hours=interval), id='quant_analysis_job')
            logger.info(f"Quant Analysis scheduled every {interval} hours.")
        else:
            time_str = quant_cfg.get("TIME", "18:00")
            try:
                hour, minute = map(int, time_str.split(':'))
                scheduler.add_job(
                    run_update_pipeline, 
                    CronTrigger(day_of_week=freq, hour=hour, minute=minute), 
                    id='quant_analysis_job'
                )
                logger.info(f"Quant Analysis scheduled for {freq} at {time_str}")
            except Exception as e:
                logger.error(f"Failed to schedule Quant Analysis: {e}")

    # 4b. Standalone NLP Market Sentiment Engine
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
                CronTrigger(day_of_week=freq, hour=f"{start_h}-{end_h}/{interval_hours}"),
                id='sentiment_scan_job'
            )
            logger.info(f"Sentiment Scan scheduled for {freq} between {start_time}-{end_time} every {interval_hours} hours.")
        except Exception as e:
            logger.error(f"Failed to schedule Sentiment Scan: {e}")

    # 5. Unified Intraday Orchestrator
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
                CronTrigger(day_of_week=freq, hour=f"{start_h}-{end_h}", minute=f"*/{interval_mins}"),
                id='intraday_orchestrator_job'
            )
            logger.info(f"Unified Intraday Orchestrator scheduled for {freq} between {start_time}-{end_time} every {interval_mins} mins.")
        except Exception as e:
            logger.error(f"Failed to schedule Intraday Orchestrator: {e}")

    # 6. System Maintenance Engine
    maint_cfg = scheduling.get("MAINTENANCE", {})
    if maint_cfg.get("ENABLED", True):
        time_str = maint_cfg.get("TIME", "02:00")
        day_of_week = maint_cfg.get("DAY_OF_WEEK", "sun")
        try:
            hour, minute = map(int, time_str.split(':'))
            scheduler.add_job(
                run_maintenance_engine,
                CronTrigger(day_of_week=day_of_week, hour=hour, minute=minute),
                id='maintenance_job'
            )
            logger.info(f"DB/File Maintenance scheduled for {day_of_week} at {time_str}")
        except Exception as e:
            logger.error(f"Failed to schedule Maintenance Job: {e}")

    # 7. Daily Quant Screener Engine (Portfolio/Watchlist)
    quant_cfg = scheduling.get("QUANT_ENGINE", {})
    quant_days_list = quant_cfg.get("DAYS", ["mon", "tue", "wed", "thu", "fri"])
    quant_days = ",".join(quant_days_list) if quant_days_list else "mon-fri"
    quant_time = quant_cfg.get("TIME", "01:00")
    
    try:
        hour, minute = map(int, quant_time.split(':'))
        scheduler.add_job(
            run_overnight_quant_scan,
            CronTrigger(day_of_week=quant_days, hour=hour, minute=minute),
            id='overnight_quant_scan_job'
        )
        logger.info(f"Overnight Quant Scan scheduled for {quant_days} at {quant_time}")
    except Exception as e:
        logger.error(f"Failed to schedule Overnight Quant Scan: {e}")

    # 8. Earnings Volatility Engine
    earn_cfg = scheduling.get("EARNINGS_ENGINE", {})
    earn_days_list = earn_cfg.get("DAYS", ["sat"])
    earn_days = ",".join(earn_days_list) if earn_days_list else "sat"
    earn_time = earn_cfg.get("TIME", "10:00")
    
    try:
        hour, minute = map(int, earn_time.split(':'))
        scheduler.add_job(
            run_weekend_earnings_scan,
            CronTrigger(day_of_week=earn_days, hour=hour, minute=minute),
            id='weekend_earnings_vol_scan_job'
        )
        logger.info(f"Earnings Volatility Scan scheduled for {earn_days} at {earn_time}")
    except Exception as e:
        logger.error(f"Failed to schedule Earnings Volatility Scan: {e}")

    # 9. Morning Briefing — always schedule; ENABLED flag only gates Nextcloud Talk sending
    disp_cfg = scheduling.get("DISPATCHER", {})
    disp_days_list = disp_cfg.get("DAYS", ["mon", "tue", "wed", "thu", "fri"])
    disp_days = ",".join(disp_days_list) if disp_days_list else "mon-fri"
    disp_time = disp_cfg.get("TIME", "07:15")

    try:
        hour, minute = map(int, disp_time.split(':'))
        scheduler.add_job(
            run_morning_briefing_dispatch,
            CronTrigger(day_of_week=disp_days, hour=hour, minute=minute),
            id='morning_briefing_dispatch_job'
        )
        logger.info(f"Morning Briefing scheduled for {disp_days} at {disp_time}")
    except Exception as e:
        logger.error(f"Failed to schedule Morning Briefing: {e}")

    # 9b. Lunchtime Briefing — always schedule; ENABLED flag only gates Nextcloud Talk sending
    lunch_cfg = scheduling.get("LUNCH_DISPATCHER", {})
    lunch_days_list = lunch_cfg.get("DAYS", ["mon", "tue", "wed", "thu", "fri"])
    lunch_days = ",".join(lunch_days_list) if lunch_days_list else "mon-fri"
    lunch_time = lunch_cfg.get("TIME", "12:00")

    try:
        hour, minute = map(int, lunch_time.split(':'))
        scheduler.add_job(
            run_lunchtime_briefing_dispatch,
            CronTrigger(day_of_week=lunch_days, hour=hour, minute=minute),
            id='lunchtime_briefing_dispatch_job'
        )
        logger.info(f"Lunchtime Briefing scheduled for {lunch_days} at {lunch_time}")
    except Exception as e:
        logger.error(f"Failed to schedule Lunchtime Briefing: {e}")

    # 10. Weekend Universe Routine (4000+ Tickers)
    uni_cfg = scheduling.get("UNIVERSE_ENGINE", {})
    uni_days_list = uni_cfg.get("DAYS", ["sat"])
    uni_days = ",".join(uni_days_list) if uni_days_list else "sat"
    uni_time = uni_cfg.get("TIME", "02:00")
    
    try:
        hour, minute = map(int, uni_time.split(':'))
        scheduler.add_job(
            run_weekend_universe_routine,
            CronTrigger(day_of_week=uni_days, hour=hour, minute=minute),
            id='universe_routine_job'
        )
        logger.info(f"Weekend Universe Routine scheduled for {uni_days} at {uni_time}")
        
    except Exception as e:
        logger.error(f"Failed to schedule Weekend Universe Routine: {e}")

    # 12. Modular ML Engine Scheduling
    ml_backfill_cfg = scheduling.get("ML_BACKFILL", {})
    if ml_backfill_cfg.get("ENABLED", False):
        backfill_days_list = ml_backfill_cfg.get("DAYS", ["sat"])
        backfill_days = ",".join(backfill_days_list) if backfill_days_list else "sat"
        backfill_time = ml_backfill_cfg.get("TIME", "02:00")
        try:
            hour, minute = map(int, backfill_time.split(':'))
            scheduler.add_job(
                run_ml_backfill,
                CronTrigger(day_of_week=backfill_days, hour=hour, minute=minute),
                id='ml_backfill_job'
            )
            logger.info(f"ML Historical Backfill scheduled for {backfill_days} at {backfill_time}")
        except Exception as e:
            logger.error(f"Failed to schedule ML Backfill: {e}")

    ml_training_cfg = scheduling.get("ML_TRAINING", {})
    if ml_training_cfg.get("ENABLED", True):
        train_days_list = ml_training_cfg.get("DAYS", ["sun"])
        train_days = ",".join(train_days_list) if train_days_list else "sun"
        train_time = ml_training_cfg.get("TIME", "04:00")
        try:
            hour, minute = map(int, train_time.split(':'))
            scheduler.add_job(
                run_ml_training,
                CronTrigger(day_of_week=train_days, hour=hour, minute=minute),
                id='ml_training_job'
            )
            logger.info(f"ML Global Training scheduled for {train_days} at {train_time}")
        except Exception as e:
            logger.error(f"Failed to schedule ML Training: {e}")

    ml_infer_cfg = scheduling.get("ML_INFERENCE", {})
    if ml_infer_cfg.get("ENABLED", True):
        infer_days_list = ml_infer_cfg.get("DAYS", ["mon", "tue", "wed", "thu", "fri"])
        infer_days = ",".join(infer_days_list) if infer_days_list else "mon-fri"
        infer_time = ml_infer_cfg.get("TIME", "01:30")
        try:
            hour, minute = map(int, infer_time.split(':'))
            scheduler.add_job(
                run_ml_inference,
                CronTrigger(day_of_week=infer_days, hour=hour, minute=minute),
                id='ml_inference_job'
            )
            logger.info(f"Daily ML Inference scheduled for {infer_days} at {infer_time}")
        except Exception as e:
            logger.error(f"Failed to schedule ML Inference: {e}")

    # 13. Freetrade Universe Sync Engine
    ft_cfg = scheduling.get("FREETRADE_SYNC", {})
    if ft_cfg.get("ENABLED", False):
        ft_days_list = ft_cfg.get("DAYS", ["mon", "tue", "wed", "thu", "fri"])
        ft_days = ",".join(ft_days_list) if ft_days_list else "mon-fri"
        ft_time = ft_cfg.get("TIME", "03:00")
        try:
            hour, minute = map(int, ft_time.split(':'))
            scheduler.add_job(
                run_freetrade_sync,
                CronTrigger(day_of_week=ft_days, hour=hour, minute=minute),
                id='freetrade_sync_job'
            )
            logger.info(f"Freetrade Sync scheduled for {ft_days} at {ft_time}")
        except Exception as e:
            logger.error(f"Failed to schedule Freetrade Sync: {e}")

    # 14. Macro Calendar and Data Engines
    macro_cfg = scheduling.get("MACRO_ENGINE", {})
    if macro_cfg.get("ENABLED", True):
        calendar_time = macro_cfg.get("CALENDAR_TIME", "04:00")
        data_day = macro_cfg.get("DATA_DAY", "sat")
        data_time = macro_cfg.get("DATA_TIME", "05:00")

        try:
            cal_hour, cal_minute = map(int, calendar_time.split(':'))
            scheduler.add_job(
                run_macro_calendar_update,
                CronTrigger(day_of_week='mon-sun', hour=cal_hour, minute=cal_minute),
                id='macro_calendar_job'
            )
            logger.info(f"Macro Calendar Update scheduled daily at {calendar_time}")

            data_hour, data_minute = map(int, data_time.split(':'))
            scheduler.add_job(
                run_macro_data_update,
                CronTrigger(day_of_week=data_day, hour=data_hour, minute=data_minute),
                id='macro_data_job'
            )
            logger.info(f"Macro Data Update scheduled for {data_day} at {data_time}")
        except Exception as e:
            logger.error(f"Failed to schedule Macro Engine Jobs: {e}")

    # 14b. Central Bank NLP Alert (polls for same-day FOMC/BoE events post-announcement)
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
                CronTrigger(day_of_week=cb_freq, hour=f"{cb_start_h}-{cb_end_h}", minute=f"*/{cb_interval}"),
                id='cb_nlp_alert_job'
            )
            logger.info(f"Central Bank NLP Alert polling scheduled {cb_freq} {cb_start}-{cb_end} UTC every {cb_interval}m")
        except Exception as e:
            logger.error(f"Failed to schedule Central Bank NLP Alert: {e}")

    # 15. Index Constituents Scraper
    sync_indices_cfg = scheduling.get("SYNC_INDICES", {})
    if sync_indices_cfg.get("ENABLED", False):
        index_days_list = sync_indices_cfg.get("DAYS", ["sat"])
        index_days = ",".join(index_days_list) if index_days_list else "sat"
        index_time = sync_indices_cfg.get("TIME", "03:00")
        try:
            hour, minute = map(int, index_time.split(':'))
            scheduler.add_job(
                run_index_scraper,
                CronTrigger(day_of_week=index_days, hour=hour, minute=minute),
                id='index_scraper_job'
            )
            logger.info(f"Index Scraper scheduled for {index_days} at {index_time}")
        except Exception as e:
            logger.error(f"Failed to schedule Index Scraper: {e}")

    # 16. Fundamentals Profiler Engine
    profiler_cfg = scheduling.get("PROFILER_ENGINE", {})
    if profiler_cfg.get("ENABLED", False):
        profiler_days_list = profiler_cfg.get("DAYS", ["sun"])
        profiler_days = ",".join(profiler_days_list) if profiler_days_list else "sun"
        profiler_time = profiler_cfg.get("TIME", "05:00")
        try:
            hour, minute = map(int, profiler_time.split(':'))
            scheduler.add_job(
                run_fundamentals_profiler,
                CronTrigger(day_of_week=profiler_days, hour=hour, minute=minute),
                id='fundamentals_profiler_job'
            )
            logger.info(f"Fundamentals Profiler scheduled for {profiler_days} at {profiler_time}")
        except Exception as e:
            logger.error(f"Failed to schedule Fundamentals Profiler: {e}")

    # 17. Universe Deep Sync Pipeline (replaces legacy UNIVERSE_FUNDAMENTALS).
    # Sequences fundamentals -> metadata -> technicals -> ML inference for the
    # full index universe (FTSE100 + S&P500). Required for the GARP, Quality
    # Compounders, and other market-wide reports.
    uds_cfg = scheduling.get("UNIVERSE_DEEP_SYNC", {})
    if uds_cfg.get("ENABLED", False):
        uds_days_list = uds_cfg.get("DAYS", ["sun"])
        uds_days = ",".join(uds_days_list) if uds_days_list else "sun"
        uds_time = uds_cfg.get("TIME", "02:00")
        try:
            hour, minute = map(int, uds_time.split(':'))
            scheduler.add_job(
                run_universe_deep_sync_job,
                CronTrigger(day_of_week=uds_days, hour=hour, minute=minute),
                id='universe_deep_sync_job'
            )
            logger.info(f"Universe Deep Sync Pipeline scheduled for {uds_days} at {uds_time}")
        except Exception as e:
            logger.error(f"Failed to schedule Universe Deep Sync Pipeline: {e}")


    # Anomaly Training Job — runs Mon–Fri at 18:30 (after quant_analysis_job at 18:00,
    # before xray_risk_cache_job at 19:00). Controlled by NOTIFICATIONS.ANOMALY_ALERTS.ENABLED.
    anomaly_cfg = notifications.get("ANOMALY_ALERTS", {})
    if anomaly_cfg.get("ENABLED", False):
        try:
            scheduler.add_job(
                run_anomaly_training_job,
                CronTrigger(day_of_week='mon-fri', hour=18, minute=30),
                id='anomaly_training_job',
            )
            logger.info("Anomaly Training job scheduled for mon-fri at 18:30.")
        except Exception as e:
            logger.error(f"Failed to schedule Anomaly Training job: {e}")

    # AI Sector Contagion Monitor — intraday scan, every N minutes during extended market hours.
    ai_c_sched = scheduling.get("AI_CONTAGION", {})
    if ai_c_sched.get("ENABLED", False):
        try:
            start_h = int(ai_c_sched.get("START_TIME", "09:00").split(":")[0])
            end_h   = int(ai_c_sched.get("END_TIME",   "21:00").split(":")[0])
            mins    = int(ai_c_sched.get("INTERVAL_MINUTES", 15))
            freq    = ai_c_sched.get("FREQUENCY", "mon-fri")
            scheduler.add_job(
                run_ai_contagion_job,
                CronTrigger(day_of_week=freq, hour=f"{start_h}-{end_h}", minute=f"*/{mins}"),
                id='ai_contagion_job',
                replace_existing=True,
                misfire_grace_time=300,
            )
            logger.info(
                f"AI Contagion Monitor scheduled ({freq} {start_h:02d}:00–{end_h:02d}:00 every {mins}m)."
            )
        except Exception as e:
            logger.error(f"Failed to schedule AI Contagion Monitor: {e}")

    # 18. News Feed Engine — periodic yfinance + trafilatura article fetch
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
                CronTrigger(day_of_week=news_freq, hour=f"{start_h}-{end_h}/{news_interval_h}"),
                id="news_feed_job",
                replace_existing=True,
            )
            logger.info(f"News Feed scheduled for {news_freq} between {news_start}-{news_end} every {news_interval_h}h.")
        except Exception as e:
            logger.error(f"Failed to schedule News Feed job: {e}")

    # Always-on: X-ray Risk Cache — runs daily Mon–Fri at 19:00 (after market close).
    # No config flag required; the X-ray report is always available.
    try:
        scheduler.add_job(
            run_xray_risk_cache_job,
            CronTrigger(day_of_week='mon-fri', hour=19, minute=0),
            id='xray_risk_cache_job',
        )
        logger.info("X-ray Risk Cache job scheduled for mon-fri at 19:00.")
    except Exception as e:
        logger.error(f"Failed to schedule X-ray Risk Cache job: {e}")


def start_scheduler():
    scheduler.start()


def shutdown_scheduler():
    scheduler.shutdown()