import logging
import functools as _functools
import threading as _threading
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.events import EVENT_JOB_SUBMITTED, EVENT_JOB_EXECUTED, EVENT_JOB_ERROR
from config import load_config
import time_engine
from notification_engine import notify, set_job_source, clear_job_source, current_job_source, SCHEDULER_STATUS_SOURCE
from database import get_accounts, get_connection, get_etf_predictor_configs
from news_feed_engine import run_news_feed_job

logger = logging.getLogger(__name__)

scheduler = BackgroundScheduler(job_defaults={"coalesce": True, "max_instances": 1, "misfire_grace_time": 60})


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

_active_jobs: dict[str, str] = {}
_active_jobs_lock = _threading.Lock()

def _mark_job_started(name: str) -> None:
    with _active_jobs_lock:
        _active_jobs[name] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")

def _mark_job_done(name: str) -> None:
    with _active_jobs_lock:
        _active_jobs.pop(name, None)

def get_active_jobs() -> dict[str, str]:
    with _active_jobs_lock:
        return dict(_active_jobs)

def force_clear_active_jobs() -> None:
    with _active_jobs_lock:
        _active_jobs.clear()

def log_sched_notification(msg_type: str, msg_text: str):
    level = "error" if msg_type == "Error" else ("warning" if msg_type == "Warning" else "info")
    notify(current_job_source() or SCHEDULER_STATUS_SOURCE, msg_type, msg_text, level=level)

def record_job_run(job_id: str):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO scheduler_run_log (job_id, last_run) VALUES (?, ?) "
            "ON CONFLICT(job_id) DO UPDATE SET last_run = excluded.last_run",
            (job_id, datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S'))
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


def reload_scheduler():
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
                run_earnings_alert_job,
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
                run_insider_alert_job,
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
            end_h, end_m = map(int, end_time.split(':'))
            # Bump end_h up when END_TIME has a non-zero minute so the */interval_mins ticks still reach
            # it — hour/minute cron fields combine independently, so a truncated end_h can stop the last
            # tick short of the configured end time. run_intraday_orchestrator's own bounds check (in
            # IntradayOrchestrator._run) gates the exact window, so over-covering here is harmless.
            if end_m > 0:
                end_h += 1
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

    snapshot_cfg = scheduling.get("ACCOUNT_VALUE_SNAPSHOT", {})
    if snapshot_cfg.get("ENABLED", True):
        # Must run after overnight_quant_scan_job (default 01:00, ~5min) writes the verified
        # daily close to stock_signals — snapshotting earlier bakes in a stale pre-close price.
        snapshot_time = snapshot_cfg.get("TIME", "01:30")
        try:
            hour, minute = map(int, snapshot_time.split(':'))
            scheduler.add_job(
                run_account_value_snapshot,
                CronTrigger(hour=hour, minute=minute, timezone=user_tz),
                id='account_value_snapshot_job'
            )
            logger.info("Account Value Snapshot scheduled daily at %s.", snapshot_time)
        except Exception as e:
            logger.error("Failed to schedule Account Value Snapshot job: %s", e)

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

    uni_cfg = scheduling.get("UNIVERSE_ENGINE", {})
    if uni_cfg.get("ENABLED"):
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

        try:
            scheduler.add_job(
                run_trap_accuracy_fill_job,
                CronTrigger(day_of_week="mon-sun", hour="20", minute="30", timezone=user_tz),
                id="trap_accuracy_fill_job",
                replace_existing=True,
                misfire_grace_time=600,
            )
            logger.info("Trap accuracy fill job scheduled (daily 20:30).")
        except Exception as e:
            logger.error("Failed to schedule Trap accuracy fill job: %s", e)

    referee_sched = scheduling.get("ALERT_REFEREE_TRAINING", {})
    if referee_sched.get("ENABLED", False):
        try:
            referee_days_list = referee_sched.get("DAYS", ["sun"])
            referee_days = ",".join(referee_days_list) if referee_days_list else "sun"
            referee_time = referee_sched.get("TIME", "05:00")
            hour, minute = map(int, referee_time.split(':'))
            scheduler.add_job(
                run_alert_referee_training_job,
                CronTrigger(day_of_week=referee_days, hour=hour, minute=minute, timezone=user_tz),
                id='alert_referee_training_job',
                replace_existing=True,
                misfire_grace_time=600,
            )
            logger.info("Alert Confidence Referee training scheduled for %s at %s.", referee_days, referee_time)
        except Exception as e:
            logger.error("Failed to schedule Alert Confidence Referee training: %s", e)

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

    bubble_cfg = scheduling.get("BUBBLE_RADAR", {})
    if bubble_cfg.get("ENABLED", False):
        try:
            days = ",".join(bubble_cfg.get("DAYS", ["mon", "tue", "wed", "thu", "fri"]))
            time_str = bubble_cfg.get("TIME", "19:30")
            run_h, run_m = map(int, time_str.split(":"))
            scheduler.add_job(
                run_bubble_radar_job,
                CronTrigger(day_of_week=days, hour=run_h, minute=run_m, timezone=user_tz),
                id="bubble_radar_job",
                replace_existing=True,
                misfire_grace_time=600,
            )
            logger.info("Bubble Radar Scan scheduled for %s at %s.", days, time_str)
        except Exception as e:
            logger.error("Failed to schedule Bubble Radar Scan: %s", e)

    pairs_spread_cfg = scheduling.get("PAIRS_SPREAD_MONITOR", {})
    if pairs_spread_cfg.get("ENABLED", False):
        try:
            days = ",".join(pairs_spread_cfg.get("DAYS", ["mon", "tue", "wed", "thu", "fri"]))
            time_str = pairs_spread_cfg.get("TIME", "19:10")
            run_h, run_m = map(int, time_str.split(":"))
            scheduler.add_job(
                run_pairs_spread_monitor_job,
                CronTrigger(day_of_week=days, hour=run_h, minute=run_m, timezone=user_tz),
                id="pairs_spread_monitor_job",
                replace_existing=True,
                misfire_grace_time=600,
            )
            logger.info("Pairs Spread Monitor scheduled for %s at %s.", days, time_str)
        except Exception as e:
            logger.error("Failed to schedule Pairs Spread Monitor: %s", e)

    head_shoulders_cfg = scheduling.get("HEAD_SHOULDERS", {})
    if head_shoulders_cfg.get("ENABLED", False):
        try:
            days = ",".join(head_shoulders_cfg.get("DAYS", ["mon", "tue", "wed", "thu", "fri"]))
            time_str = head_shoulders_cfg.get("TIME", "22:20")
            run_h, run_m = map(int, time_str.split(":"))
            scheduler.add_job(
                run_head_shoulders_job,
                CronTrigger(day_of_week=days, hour=run_h, minute=run_m, timezone=user_tz),
                id="head_shoulders_job",
                replace_existing=True,
                misfire_grace_time=600,
            )
            logger.info("Head & Shoulders Pattern Detector scheduled for %s at %s.", days, time_str)
        except Exception as e:
            logger.error("Failed to schedule Head & Shoulders Pattern Detector: %s", e)

        try:
            scheduler.add_job(
                run_head_shoulders_accuracy_fill_job,
                CronTrigger(day_of_week="mon-sun", hour="23", minute="0", timezone=user_tz),
                id="head_shoulders_accuracy_fill_job",
                replace_existing=True,
                misfire_grace_time=600,
            )
            logger.info("Head & Shoulders accuracy fill job scheduled (daily 23:00).")
        except Exception as e:
            logger.error("Failed to schedule Head & Shoulders accuracy fill job: %s", e)

    forensic_fetch_cfg = scheduling.get("FORENSIC_QUARTERLY_FETCH", {})
    if forensic_fetch_cfg.get("ENABLED", False):
        try:
            time_str = forensic_fetch_cfg.get("TIME", "06:00")
            run_h, run_m = map(int, time_str.split(":"))
            day_of_month = forensic_fetch_cfg.get("DAY_OF_MONTH", 1)
            scheduler.add_job(
                run_forensic_quarterly_fetch_job,
                CronTrigger(day=day_of_month, hour=run_h, minute=run_m, timezone=time_engine.get_user_tz()),
                id="forensic_quarterly_fetch_job",
                replace_existing=True,
                misfire_grace_time=3600,
            )
            logger.info("Forensic Quarterly Data Fetch scheduled for day %s at %s local.", day_of_month, time_str)
        except Exception as e:
            logger.error("Failed to schedule Forensic Quarterly Data Fetch: %s", e)

    forensic_scores_cfg = scheduling.get("FORENSIC_SCORES", {})
    if forensic_scores_cfg.get("ENABLED", False):
        try:
            time_str = forensic_scores_cfg.get("TIME", "07:00")
            run_h, run_m = map(int, time_str.split(":"))
            day_of_month = forensic_scores_cfg.get("DAY_OF_MONTH", 1)
            scheduler.add_job(
                run_forensic_scores_job,
                CronTrigger(day=day_of_month, hour=run_h, minute=run_m, timezone=time_engine.get_user_tz()),
                id="forensic_scores_job",
                replace_existing=True,
                misfire_grace_time=3600,
            )
            logger.info("Forensic Accounting Scores scheduled for day %s at %s local.", day_of_month, time_str)
        except Exception as e:
            logger.error("Failed to schedule Forensic Accounting Scores: %s", e)

    # Always-on: fast-exits silently if no tickers are armed
    try:
        scheduler.add_job(
            run_intraday_dip_scan,
            CronTrigger(day_of_week='mon-fri', hour='7-21', minute='*/2', timezone=timezone.utc),
            id='intraday_dip_scan_job',
            replace_existing=True,
            misfire_grace_time=60,
        )
        logger.info("Intraday Dip Radar scan scheduled mon-fri 07:00–21:59 UTC every 2 min (covers LSE 08:00–16:30 BST and NYSE 14:30–21:00 BST).")
    except Exception as e:
        logger.error("Failed to schedule Intraday Dip Radar scan: %s", e)

    # Always-on, DB-only (no Yahoo calls) — safe at a 1-minute cadence, independent of the
    # heavier Yahoo-fetching Crash/Moonshot scan's own INTERVAL_MINUTES. Window is capped at
    # NYSE's close (20:00 UTC, i.e. hour up to and including 20) rather than padded further, so
    # this job stops recomputing account_performance_cache before the 22:30 BST Update Pipeline
    # writes stock_signals — the previous 07:00-21:59 UTC window left a ~2h dead zone after
    # market_pulse_cache goes stale (Crash & Moonshot's own local-time END_TIME cutoff) during
    # which this job kept re-baking whatever current_price_map() resolved to, letting a stale
    # nightly write win the race purely by being newer. See accounts_engine.current_price_map()'s
    # docstring for the companion guard.
    try:
        scheduler.add_job(
            run_account_performance_refresh_job,
            CronTrigger(day_of_week='mon-fri', hour='7-20', minute='*/1', timezone=timezone.utc),
            id='account_performance_refresh_job',
            replace_existing=True,
            misfire_grace_time=60,
        )
        logger.info("Account Performance Refresh scheduled mon-fri 07:00–20:59 UTC every 1 min.")
    except Exception as e:
        logger.error("Failed to schedule Account Performance Refresh: %s", e)

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

    # Always-on: UK Treasury Bill Maturity Sweep — runs daily (bills mature on weekends too), so a
    # matured position never sits open corrupting cash/holdings. No config flag required.
    try:
        scheduler.add_job(
            run_treasury_bill_maturity_sweep,
            CronTrigger(day_of_week='mon-sun', hour=7, minute=0, timezone=user_tz),
            id='treasury_bill_maturity_sweep_job',
        )
        logger.info("UK Treasury Bill Maturity Sweep scheduled daily at 07:00.")
    except Exception as e:
        logger.error("Failed to schedule UK Treasury Bill Maturity Sweep job: %s", e)

    # Always-on: ETF Predictor actual-fill — runs Mon–Fri at 09:20 UTC.
    try:
        scheduler.add_job(
            run_etf_actual_fill_job,
            CronTrigger(day_of_week="mon-fri", hour=9, minute=20, timezone=timezone.utc),
            id="etf_predictor_actual_fill_job",
        )
        logger.info("ETF Predictor actual-fill job scheduled for mon-fri at 09:20 UTC.")
    except Exception as e:
        logger.error("Failed to schedule ETF Predictor actual-fill job: %s", e)

    try:
        for _etf_cfg in get_etf_predictor_configs():
            if _etf_cfg.get("auto_schedule") and _etf_cfg.get("enabled") and not _etf_cfg.get("deleted_at"):
                register_etf_predictor_jobs(_etf_cfg)
    except Exception as e:
        logger.error("Failed to register ETF predictor jobs from DB: %s", e)

    try:
        for _acc in get_accounts():
            if _acc["account_type"] in ("House", "Pension"):
                register_account_scraper_job(_acc)
    except Exception as e:
        logger.error("Failed to register account scraper jobs from DB: %s", e)

    try:
        for _acc in get_accounts():
            if _acc["account_type"] == "Trading" and _acc.get("autotopup_enabled"):
                register_account_topup_job(_acc)
    except Exception as e:
        logger.error("Failed to register account Auto Top-up jobs from DB: %s", e)

    auction_cfg = scheduling.get("MACRO_AUCTIONS", {})
    if auction_cfg.get("ENABLED", True):
        _user_tz = time_engine.get_user_tz()
        am_time = auction_cfg.get("AM_TIME", time_engine.fmt_et_time_value("13:15"))
        pm_time = auction_cfg.get("PM_TIME", time_engine.fmt_et_time_value("15:30"))
        try:
            am_h, am_m = map(int, am_time.split(":"))
            scheduler.add_job(
                lambda: run_treasury_auction_check("am"),
                CronTrigger(day_of_week="mon-fri", hour=am_h, minute=am_m, timezone=_user_tz),
                id="macro_auction_job_am",
                replace_existing=True,
            )
            logger.info("Sovereign Debt Auction Monitor (AM) scheduled mon-fri at %s local.", am_time)
        except Exception as e:
            logger.error("Failed to schedule Sovereign Debt Auction Monitor (AM): %s", e)
        try:
            pm_h, pm_m = map(int, pm_time.split(":"))
            scheduler.add_job(
                lambda: run_treasury_auction_check("pm"),
                CronTrigger(day_of_week="mon-fri", hour=pm_h, minute=pm_m, timezone=_user_tz),
                id="macro_auction_job_pm",
                replace_existing=True,
            )
            logger.info("Sovereign Debt Auction Monitor (PM) scheduled mon-fri at %s local.", pm_time)
        except Exception as e:
            logger.error("Failed to schedule Sovereign Debt Auction Monitor (PM): %s", e)

    backup_cfg = scheduling.get("BACKUP", {})
    if backup_cfg.get("ENABLED", False):
        backup_days_list = backup_cfg.get("DAYS", ["sun"])
        backup_days = ",".join(backup_days_list) if backup_days_list else "sun"
        backup_time = backup_cfg.get("TIME", "03:30")
        try:
            hour, minute = map(int, backup_time.split(':'))
            scheduler.add_job(
                run_backup_job,
                CronTrigger(day_of_week=backup_days, hour=hour, minute=minute, timezone=user_tz),
                id='backup_job'
            )
            logger.info("Automated Backup scheduled for %s at %s", backup_days, backup_time)
        except Exception as e:
            logger.error("Failed to schedule Automated Backup: %s", e)

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
    scheduler.shutdown(wait=False)


# Sub-module re-exports — placed after all primary definitions so the sub-modules can
# import from this module at their top level without a circular-import error.
from scheduler_manifest import (
    JOB_GRAPH, CONFIG_KEY_TO_JOB, job_label, display_name_for_config_key,
    scheduler_display_names, _DYNAMIC_ETF_RE, _resolve_manifest,
)
from scheduler_monitor import build_workflow_graph, detect_workflow_conflicts, _job_status
from scheduler_jobs import (
    resume_interrupted_scans,
    trigger_sentiment_report, run_intraday_orchestrator, run_maintenance_engine,
    run_earnings_alert_job, run_insider_alert_job, run_update_pipeline, run_ghostfolio_sync, run_freetrade_sync,
    run_sentiment_scan, run_overnight_quant_scan, run_weekend_earnings_scan,
    run_weekend_universe_routine, run_index_scraper, run_fundamentals_profiler,
    run_universe_deep_sync_job, run_ml_backfill, run_ml_training, run_ml_inference,
    run_macro_calendar_update, run_central_bank_nlp_check, run_macro_data_update,
    run_xray_risk_cache_job, run_anomaly_training_job, run_intraday_dip_scan,
    run_intraday_dip_reset, _build_contagion_feed_text, _build_contagion_message,
    run_ai_contagion_job, run_trap_monitor_job, run_trap_accuracy_fill_job, run_alert_referee_training_job,
    run_bubble_radar_job, run_pairs_spread_monitor_job, run_pairs_spread_universe_scan,
    run_head_shoulders_job, run_head_shoulders_accuracy_fill_job,
    register_etf_predictor_jobs, unregister_etf_predictor_jobs,
    run_forensic_quarterly_fetch_job, run_forensic_scores_job, run_etf_actual_fill_job,
    run_system_check_job, run_treasury_auction_check, run_account_value_snapshot,
    register_account_scraper_job, unregister_account_scraper_job, _run_account_scraper_job,
    register_account_topup_job, unregister_account_topup_job, _run_account_topup_job,
    run_backup_job, run_treasury_bill_maturity_sweep, run_account_performance_refresh_job,
)
