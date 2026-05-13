# scheduler_engine.py
import threading
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

# --- Background Task Scheduler Setup ---
scheduler = BackgroundScheduler()
task_lock = threading.Lock()

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
        print("[WARNING] System is currently busy. Skipping Update Analysis to prevent clash.")
        return
    try:
        print("\n--- BACKGROUND UPDATE INITIATED ---")
        DataEngine().update_all_data()
        QuantEngine().run_all()
        print("--- BACKGROUND UPDATE COMPLETE ---\n")
    finally:
        task_lock.release()

def run_ghostfolio_sync():
    """Executes the Ghostfolio API Sync to extract account holdings."""
    if not task_lock.acquire(blocking=False):
        print("[WARNING] System is currently busy. Skipping Ghostfolio Sync to prevent clash.")
        return
    try:
        sync_engine = GhostfolioSyncEngine()
        sync_engine.run_full_sync()
    finally:
        task_lock.release()

def run_overnight_quant_scan():
    """
    Fetches the combined list of portfolio and watchlist tickers, 
    and executes the resumable daily quant scan natively.
    """
    if not task_lock.acquire(blocking=False):
        print("[WARNING] System is currently busy. Skipping Overnight Quant Scan.")
        return
    try:
        print("\n--- OVERNIGHT QUANT SCAN INITIATED ---")
        engine = DataEngine()
        all_tickers = engine.get_all_tickers() 
        run_daily_quant_scan(all_tickers)
        print("--- OVERNIGHT QUANT SCAN COMPLETE ---\n")
    except Exception as e:
        print(f"[ERROR] Overnight Quant Scan Failed: {e}")
    finally:
        task_lock.release()

def run_weekend_earnings_scan():
    """Executes the quantitative earnings volatility options scan."""
    if not task_lock.acquire(blocking=False):
        print("[WARNING] System is busy. Skipping Earnings Volatility Scan.")
        return
    try:
        print("\n--- EARNINGS VOLATILITY SCAN INITIATED ---")
        engine = DataEngine()
        all_tickers = engine.get_all_tickers() 
        run_earnings_vol_scan(all_tickers)
        print("--- EARNINGS VOLATILITY SCAN COMPLETE ---\n")
    except Exception as e:
        print(f"[ERROR] Earnings Volatility Scan Failed: {e}")
    finally:
        task_lock.release()

def run_morning_briefing_dispatch():
    """Executes the morning quant briefing dispatch."""
    if not task_lock.acquire(blocking=False):
        print("[WARNING] System is busy. Skipping Morning Briefing Dispatch.")
        return
    try:
        print("\n--- MORNING BRIEFING DISPATCH INITIATED ---")
        push_morning_quant_briefing()
        print("--- MORNING BRIEFING DISPATCH COMPLETE ---\n")
    except Exception as e:
        print(f"[ERROR] Morning Briefing Dispatch Failed: {e}")
    finally:
        task_lock.release()

def reload_scheduler():
    """Reads the latest config.json and updates APScheduler dynamically."""
    print("[SCHEDULER] Reloading scheduled jobs from configuration...")
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
            print(f"[SCHEDULER] Market Sentiment Job scheduled for {freq} at {time_str}")
        except Exception as e:
            print(f"[ERROR] Failed to schedule Market Sentiment: {e}")

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
            print(f"[SCHEDULER] Earnings Alerts Job scheduled for mon-fri at {time_str}")
        except Exception as e:
            print(f"[ERROR] Failed to schedule Earnings Alerts: {e}")
    
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
            print(f"[SCHEDULER] Insider Trading Alert Job scheduled for {freq} at {time_str}")
        except Exception as e:
            print(f"[ERROR] Failed to schedule Insider Alerts: {e}")

    # 4. Core System Schedulers (Ghostfolio & Quant Analysis)
    ghost_cfg = scheduling.get("GHOSTFOLIO_SYNC", {})
    if ghost_cfg.get("ENABLED"):
        interval = int(ghost_cfg.get("INTERVAL_HOURS", 0))
        freq = ghost_cfg.get("FREQUENCY", "mon-fri")
        if interval > 0:
            scheduler.add_job(run_ghostfolio_sync, IntervalTrigger(hours=interval), id='ghostfolio_sync_job')
            print(f"[SCHEDULER] Ghostfolio Sync scheduled every {interval} hours.")
        else:
            time_str = ghost_cfg.get("TIME", "06:00")
            try:
                hour, minute = map(int, time_str.split(':'))
                scheduler.add_job(
                    run_ghostfolio_sync, 
                    CronTrigger(day_of_week=freq, hour=hour, minute=minute), 
                    id='ghostfolio_sync_job'
                )
                print(f"[SCHEDULER] Ghostfolio Sync scheduled for {freq} at {time_str}")
            except Exception as e:
                print(f"[ERROR] Failed to schedule Ghostfolio Sync: {e}")

    quant_cfg = scheduling.get("QUANT_ANALYSIS", {})
    if quant_cfg.get("ENABLED"):
        interval = int(quant_cfg.get("INTERVAL_HOURS", 0))
        freq = quant_cfg.get("FREQUENCY", "mon-fri")
        if interval > 0:
            scheduler.add_job(run_update_pipeline, IntervalTrigger(hours=interval), id='quant_analysis_job')
            print(f"[SCHEDULER] Quant Analysis scheduled every {interval} hours.")
        else:
            time_str = quant_cfg.get("TIME", "18:00")
            try:
                hour, minute = map(int, time_str.split(':'))
                scheduler.add_job(
                    run_update_pipeline, 
                    CronTrigger(day_of_week=freq, hour=hour, minute=minute), 
                    id='quant_analysis_job'
                )
                print(f"[SCHEDULER] Quant Analysis scheduled for {freq} at {time_str}")
            except Exception as e:
                print(f"[ERROR] Failed to schedule Quant Analysis: {e}")

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
            print(f"[SCHEDULER] Unified Intraday Orchestrator scheduled for {freq} between {start_time}-{end_time} every {interval_mins} mins.")
        except Exception as e:
            print(f"[ERROR] Failed to schedule Intraday Orchestrator: {e}")

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
            print(f"[SCHEDULER] DB/File Maintenance scheduled for {day_of_week} at {time_str}")
        except Exception as e:
            print(f"[ERROR] Failed to schedule Maintenance Job: {e}")

    # 7. Daily Quant Screener Engine
    quant_cfg = scheduling.get("QUANT_ENGINE", {})
    quant_days_list = quant_cfg.get("DAYS", ["mon", "tue", "wed", "thu", "fri"])
    # APScheduler accepts days separated by commas. Fallback to mon-fri if array is completely empty.
    quant_days = ",".join(quant_days_list) if quant_days_list else "mon-fri"
    quant_time = quant_cfg.get("TIME", "01:00")
    
    try:
        hour, minute = map(int, quant_time.split(':'))
        scheduler.add_job(
            run_overnight_quant_scan,
            CronTrigger(day_of_week=quant_days, hour=hour, minute=minute),
            id='overnight_quant_scan_job'
        )
        print(f"[SCHEDULER] Overnight Quant Scan scheduled for {quant_days} at {quant_time}")
    except Exception as e:
        print(f"[ERROR] Failed to schedule Overnight Quant Scan: {e}")

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
        print(f"[SCHEDULER] Earnings Volatility Scan scheduled for {earn_days} at {earn_time}")
    except Exception as e:
        print(f"[ERROR] Failed to schedule Earnings Volatility Scan: {e}")

    # 9. Morning Briefing Dispatch Engine
    disp_cfg = scheduling.get("DISPATCHER", {})
    if disp_cfg.get("ENABLED", False):
        disp_days_list = disp_cfg.get("DAYS", ["mon", "tue", "wed", "thu", "fri"])
        # APScheduler accepts days separated by commas. Fallback to mon-fri if array is empty.
        disp_days = ",".join(disp_days_list) if disp_days_list else "mon-fri"
        disp_time = disp_cfg.get("TIME", "08:00")
        
        try:
            hour, minute = map(int, disp_time.split(':'))
            scheduler.add_job(
                run_morning_briefing_dispatch,
                CronTrigger(day_of_week=disp_days, hour=hour, minute=minute),
                id='morning_briefing_dispatch_job'
            )
            print(f"[SCHEDULER] Morning Briefing Dispatch scheduled for {disp_days} at {disp_time}")
        except Exception as e:
            print(f"[ERROR] Failed to schedule Morning Briefing Dispatch: {e}")


def start_scheduler():
    scheduler.start()


def shutdown_scheduler():
    scheduler.shutdown()