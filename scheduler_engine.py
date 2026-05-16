# scheduler_engine.py
import threading
import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from config import load_config
from sentiment_engine import run_nextcloud_alert
from earnings_engine import run_earnings_alert
from insider_engine import run_insider_alert
from intraday_orchestrator import IntradayOrchestrator
from maintenance_engine import MaintenanceEngine
from data_engine import DataEngine
from quant_signals import QuantEngine
from ghostfolio_sync import GhostfolioSyncEngine
from quant_engine import run_daily_quant_scan
from earnings_vol_engine import run_earnings_vol_scan
from report_dispatcher import push_morning_quant_briefing
from database import get_universe_tickers, get_connection
from universe_engine import update_market_universe
from profile_engine import run_profile_audit
from sentiment_engine import run_nextcloud_alert, update_all_sentiment
from regime_engine import calculate_market_regime
from ai_prediction_engine import train_global_ml_model, update_daily_ml_predictions

# Configure robust module-level logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - SCHEDULER_ENGINE - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# --- Background Task Scheduler Setup ---
scheduler = BackgroundScheduler()
task_lock = threading.Lock()

def log_sched_notification(msg_type: str, msg_text: str):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)", (msg_type, msg_text))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"Failed to log notification: {e}")

def trigger_sentiment_report():
    """Triggered by the scheduler to run the Nextcloud Market Sentiment alert."""
    run_nextcloud_alert()

def run_intraday_orchestrator():
    """Executes the unified high-frequency intraday scan (Crash + Moonshot)."""
    IntradayOrchestrator().run()

def run_maintenance_engine():
    """Executes the background database and file system maintenance."""
    MaintenanceEngine().run()

def run_update_pipeline():
    """Executes the heavy data ingestion and mathematical quant modeling."""
    if not task_lock.acquire(blocking=False):
        logger.warning("System is currently busy. Skipping Update Analysis to prevent clash.")
        return
    log_sched_notification("Scheduler", "Started Update Pipeline...")
    try:
        logger.info("Background update initiated.")
        DataEngine().update_all_data()
        QuantEngine().run_all()
        logger.info("Background update complete.")
        log_sched_notification("Success", "Update Pipeline completed successfully.")
    except Exception as e:
        log_sched_notification("Error", f"Update Pipeline failed: {e}")
    finally:
        task_lock.release()

def run_ghostfolio_sync():
    """Executes the Ghostfolio API Sync to extract account holdings."""
    if not task_lock.acquire(blocking=False):
        logger.warning("System is currently busy. Skipping Ghostfolio Sync to prevent clash.")
        return
    log_sched_notification("Scheduler", "Started Ghostfolio Sync...")
    try:
        sync_engine = GhostfolioSyncEngine()
        sync_engine.run_full_sync()
        log_sched_notification("Success", "Ghostfolio Sync completed successfully.")
    except Exception as e:
        log_sched_notification("Error", f"Ghostfolio Sync failed: {e}")
    finally:
        task_lock.release()

def run_overnight_quant_scan():
    """
    Fetches the combined list of portfolio and watchlist tickers, 
    executes the resumable daily quant scan, runs the ML Engine, and executes VADER NLP.
    """
    if not task_lock.acquire(blocking=False):
        logger.warning("System is currently busy. Skipping Overnight Quant Scan.")
        return
    log_sched_notification("Scheduler", "Started Overnight Quant Scan...")
    try:
        logger.info("Overnight quant scan initiated.")
        engine = DataEngine()
        all_tickers = engine.get_all_tickers() 
        
        # 1. Execute Technical Analysis Pipeline
        run_daily_quant_scan(all_tickers)
        
        # 2. Execute ML Inference Pipeline (Phase 5)
        logger.info("Overnight ML inference initiated.")
        update_daily_ml_predictions(all_tickers)
        
        # 3. Execute NLP Sentiment Pipeline (Phase 4)
        logger.info("Overnight sentiment analysis initiated.")
        update_all_sentiment(all_tickers)
        
        logger.info("Overnight quant scan complete.")
        log_sched_notification("Success", "Overnight Quant Scan completed successfully.")
    except Exception as e:
        logger.error(f"Overnight Quant Scan Failed: {e}")
        log_sched_notification("Error", f"Overnight Quant Scan failed: {e}")
    finally:
        task_lock.release()

def run_weekend_earnings_scan():
    """Executes the quantitative earnings volatility options scan."""
    if not task_lock.acquire(blocking=False):
        logger.warning("System is busy. Skipping Earnings Volatility Scan.")
        return
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
        task_lock.release()

def run_morning_briefing_dispatch():
    """Executes the morning quant briefing dispatch."""
    if not task_lock.acquire(blocking=False):
        logger.warning("System is busy. Skipping Morning Briefing Dispatch.")
        return
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
        task_lock.release()

def run_weekend_universe_routine():
    """Executes the massive 4000+ Universe Download and Quant Scan."""
    if not task_lock.acquire(blocking=False):
        logger.warning("System is busy. Skipping Weekend Universe Routine.")
        return
    log_sched_notification("Scheduler", "Started Weekend Universe Routine...")
    try:
        logger.info("Weekend universe routine initiated.")
        # 1. Update the Ticker List from the FTP server
        update_market_universe()
        
        # 2. Extract the fresh list and run the massive quant scan
        all_tickers = get_universe_tickers()
        if all_tickers:
            # Pass scan_type='universe' to prevent collision with daily scans
            run_daily_quant_scan(all_tickers, scan_type='universe')
        else:
            logger.warning("Universe is empty, skipping quant scan.")
            
        logger.info("Weekend universe routine complete.")
        log_sched_notification("Success", "Weekend Universe Routine completed successfully.")
    except Exception as e:
        logger.error(f"Weekend Universe Routine Failed: {e}")
        log_sched_notification("Error", f"Weekend Universe Routine failed: {e}")
    finally:
        task_lock.release()

def run_weekend_profile_audit():
    """Executes the rolling metadata audit for 250 assets."""
    if not task_lock.acquire(blocking=False):
        logger.warning("System is busy. Skipping Profile Audit.")
        return
    log_sched_notification("Scheduler", "Started Weekend Profile Audit...")
    try:
        logger.info("Weekend profile audit initiated.")
        run_profile_audit(limit=250)
        logger.info("Weekend profile audit complete.")
        log_sched_notification("Success", "Weekend Profile Audit completed successfully.")
    except Exception as e:
        logger.error(f"Weekend Profile Audit Failed: {e}")
        log_sched_notification("Error", f"Weekend Profile Audit failed: {e}")
    finally:
        task_lock.release()

def run_weekend_ml_training():
    """Executes the global Machine Learning training cycle."""
    if not task_lock.acquire(blocking=False):
        logger.warning("System is busy. Skipping ML Training.")
        return
    log_sched_notification("Scheduler", "Started Weekend ML Training...")
    try:
        logger.info("Weekend ML training initiated.")
        train_global_ml_model()
        logger.info("Weekend ML training complete.")
        log_sched_notification("Success", "Weekend ML Training completed successfully.")
    except Exception as e:
        logger.error(f"Weekend ML Training Failed: {e}")
        log_sched_notification("Error", f"Weekend ML Training failed: {e}")
    finally:
        task_lock.release()

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

    # 9. Morning Briefing Dispatch Engine
    disp_cfg = scheduling.get("DISPATCHER", {})
    if disp_cfg.get("ENABLED", False):
        disp_days_list = disp_cfg.get("DAYS", ["mon", "tue", "wed", "thu", "fri"])
        disp_days = ",".join(disp_days_list) if disp_days_list else "mon-fri"
        disp_time = disp_cfg.get("TIME", "08:00")
        
        try:
            hour, minute = map(int, disp_time.split(':'))
            scheduler.add_job(
                run_morning_briefing_dispatch,
                CronTrigger(day_of_week=disp_days, hour=hour, minute=minute),
                id='morning_briefing_dispatch_job'
            )
            logger.info(f"Morning Briefing Dispatch scheduled for {disp_days} at {disp_time}")
        except Exception as e:
            logger.error(f"Failed to schedule Morning Briefing Dispatch: {e}")

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
        
        # 11. Profile Rolling Audit (Staggered 1 hour after the universe routine)
        audit_hour = (hour + 1) % 24
        scheduler.add_job(
            run_weekend_profile_audit,
            CronTrigger(day_of_week=uni_days, hour=audit_hour, minute=minute),
            id='profile_audit_job'
        )
        logger.info(f"Weekend Profile Audit scheduled for {uni_days} at {audit_hour:02d}:{minute:02d}")
        
    except Exception as e:
        logger.error(f"Failed to schedule Weekend Universe Routine: {e}")

    # 12. ML Engine (Weekend Routine)
    ml_cfg = scheduling.get("ML_ENGINE", {})
    ml_days_list = ml_cfg.get("DAYS", ["sun"])
    ml_days = ",".join(ml_days_list) if ml_days_list else "sun"
    ml_time = ml_cfg.get("TIME", "04:00")
    
    try:
        hour, minute = map(int, ml_time.split(':'))
        scheduler.add_job(
            run_weekend_ml_training,
            CronTrigger(day_of_week=ml_days, hour=hour, minute=minute),
            id='ml_training_job'
        )
        logger.info(f"Weekend ML Training scheduled for {ml_days} at {hour:02d}:{minute:02d}")
    except Exception as e:
        logger.error(f"Failed to schedule ML Training: {e}")


def start_scheduler():
    scheduler.start()


def shutdown_scheduler():
    scheduler.shutdown()