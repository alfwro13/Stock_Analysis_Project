import logging
import threading as _threading
from datetime import datetime, timedelta, timezone

import time_engine
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from accounts_engine import refresh_all_trading_performance_caches, resnapshot_account, snapshot_all_accounts
from backup_engine import run_backup
from config import load_config
from data_engine import DataEngine
from database import get_account, get_connection, get_universe_tickers
from db_helpers import get_ticker_currency_map
from earnings_engine import run_earnings_alert
from insider_engine import run_insider_alert
from earnings_vol_engine import backfill_earnings_drift_outcomes, log_near_earnings_predictions, run_earnings_vol_scan
from market_pulse import is_quote_settled
from freetrade_engine import sync_freetrade_universe
from ghostfolio_sync import GhostfolioSyncEngine
from huggingface_engine import update_all_sentiment, run_central_bank_nlp_alert
from intraday_bottom_engine import IntradayBottomEngine
from intraday_orchestrator import IntradayOrchestrator
from macro_calendar_engine import update_macro_calendar
from macro_data_engine import update_macro_indicators
from maintenance_engine import MaintenanceEngine
from notification_engine import notify
from ai_prediction_engine import (
    train_global_ml_model, update_daily_ml_predictions, run_historical_backfill,
    train_quantile_models, score_quantile_predictions,
)
from quant_engine import run_daily_quant_scan
from quant_signals import QuantEngine
from risk_engine import update_all_tail_risks
from sentiment_engine import run_nextcloud_alert
from system_check_engine import run_system_checks
from treasury_auction_engine import check_auction_results
from treasury_bill_engine import sweep_matured_bills
from universe_deep_sync_engine import run_universe_deep_sync
from universe_engine import update_market_universe
from xray_engine import run_xray_precompute
from scheduler_manifest import job_label

logger = logging.getLogger(__name__)


def resume_interrupted_scans() -> None:
    """Called once on startup; re-fires any scan that was IN_PROGRESS when the server last shut down."""
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

    # Resume tail risk scans only when the parent quant scan already completed; if the quant scan is
    # itself IN_PROGRESS its resume will call update_all_tail_risks at its end.
    if today_states.get('daily') != 'IN_PROGRESS' and today_states.get('tail_risk_daily') == 'IN_PROGRESS':
        logger.info("Startup: detected interrupted Tail Risk (daily) — resuming immediately.")
        log_sched_notification("Info", "Resuming interrupted Tail Risk (daily) scan after restart.")
        def _run_daily_tr():
            update_all_tail_risks(DataEngine().get_all_tickers(), scan_type='tail_risk_daily')
        _threading.Thread(target=_run_daily_tr, daemon=True).start()
        dispatched = True

    if today_states.get('universe') != 'IN_PROGRESS' and today_states.get('tail_risk_universe') == 'IN_PROGRESS':
        logger.info("Startup: detected interrupted Tail Risk (universe) — resuming immediately.")
        log_sched_notification("Info", "Resuming interrupted Tail Risk (universe) scan after restart.")
        def _run_universe_tr():
            update_all_tail_risks(get_universe_tickers(), scan_type='tail_risk_universe')
        _threading.Thread(target=_run_universe_tr, daemon=True).start()
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

def run_backup_job():
    try:
        run_backup(trigger_type="scheduled")
    finally:
        record_job_run('backup_job')

def run_account_value_snapshot(scheduled: bool = True):
    try:
        written = snapshot_all_accounts(scheduled=scheduled)
        # Otherwise account_performance_cache stays frozen at ~21:59 UTC's numbers until the
        # 07:00 UTC cron window reopens — HA polls that happen overnight (once no longer gated
        # on market hours) would see a stale figure until the next trading day is well underway.
        # DB/cache-only: reads whatever prices are already cached, no live Yahoo call.
        refresh_all_trading_performance_caches()
        notify("account_value_snapshot_status", "Success",
               f"Account Value Snapshot completed: {written} account(s) updated.", level="info")
    except Exception as e:
        notify("account_value_snapshot_status", "Error",
               f"Account Value Snapshot failed: {e}", level="error")
        raise
    finally:
        record_job_run('account_value_snapshot_job')

def run_account_performance_refresh_job():
    """Runs every minute during market hours (DB-only, no Yahoo calls) so account-detail period
    returns stay close to live rather than only updating on the slower Crash/Moonshot scan tick.
    Silent on success like the Dip Radar scan — errors only, to avoid flooding the notification feed."""
    try:
        refresh_all_trading_performance_caches()
    except Exception as e:
        log_sched_notification("Error", f"Account Performance Refresh failed: {e}")
    finally:
        record_job_run('account_performance_refresh_job')

def run_treasury_bill_maturity_sweep():
    try:
        result = sweep_matured_bills()
        message = f"UK Treasury Bill Maturity Sweep completed: {result['matured']} bill(s) closed"
        message += f", {result['reminders']} reinvest reminder(s) sent." if result['reminders'] else "."
        notify("treasury_bill_maturity_status", "Success", message, level="info")
    except Exception as e:
        notify("treasury_bill_maturity_status", "Error",
               f"UK Treasury Bill Maturity Sweep failed: {e}", level="error")
        raise
    finally:
        record_job_run('treasury_bill_maturity_sweep_job')

def run_earnings_alert_job():
    try:
        run_earnings_alert()
    finally:
        record_job_run('earnings_alert_job')

def run_insider_alert_job():
    try:
        run_insider_alert()
    finally:
        record_job_run('insider_alert_job')

def run_update_pipeline():
    _mark_job_started(job_label("quant_analysis_job"))
    try:
        log_sched_notification("Scheduler", "Started Update Pipeline...")
        logger.info("Background update initiated.")
        data_engine = DataEngine()
        data_engine.update_all_data()
        from regime_engine import calculate_systemic_macro_threat, calculate_market_regime
        calculate_systemic_macro_threat()
        regime_result = calculate_market_regime()
        QuantEngine().run_all()
        from universe_fundamentals_engine import sync_etf_holdings_cache
        sync_etf_holdings_cache(data_engine.get_all_tickers())
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
        update_all_tail_risks(all_tickers, scan_type='tail_risk_daily')
        logged = log_near_earnings_predictions(all_tickers)
        resolved = backfill_earnings_drift_outcomes()
        logger.info("Earnings Drift: logged/refreshed %d, resolved %d outcomes.", logged, resolved)
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
        failed_tickers = run_earnings_vol_scan(all_tickers)
        _schedule_earnings_vol_retry(failed_tickers)
        logger.info("Earnings volatility scan complete.")
        log_sched_notification("Success", "Earnings Volatility Scan completed successfully.")
    except Exception as e:
        logger.error("Earnings Volatility Scan Failed: %s", e)
        log_sched_notification("Error", f"Earnings Volatility Scan failed: {e}")
    finally:
        _mark_job_done(job_label("weekend_earnings_vol_scan_job"))
        record_job_run('weekend_earnings_vol_scan_job')


_EARNINGS_VOL_RETRY_DELAY_MINUTES = 12


def _schedule_earnings_vol_retry(tickers: list) -> None:
    """One-off retry ~12 minutes later for tickers the main scan couldn't reach (Yahoo fetch
    failure, not a legitimate 'no earnings due' skip) — Yahoo's guce.yahoo.com consent gate has
    been observed intermittently refusing connections mid-scan across 100+ sequential tickers.
    Single retry pass only (no re-scheduling on repeat failure), so a ticker with no earnings
    data on Yahoo doesn't loop forever. In-memory job store: a restart before this fires drops
    it silently, same as any other unpersisted APScheduler job in this app — acceptable for a
    resilience nicety, not load-bearing data."""
    if not tickers:
        return
    run_date = datetime.now(timezone.utc) + timedelta(minutes=_EARNINGS_VOL_RETRY_DELAY_MINUTES)
    try:
        scheduler.add_job(
            _run_earnings_vol_retry_job,
            DateTrigger(run_date=run_date, timezone=timezone.utc),
            id='earnings_vol_retry_job',
            kwargs={'tickers': tickers},
            replace_existing=True,
            misfire_grace_time=600,
        )
        logger.info("Earnings volatility retry scheduled for %d ticker(s) at %s.", len(tickers), run_date)
    except Exception as e:
        logger.error("Failed to schedule earnings volatility retry: %s", e)


def _run_earnings_vol_retry_job(tickers: list) -> None:
    job_name = job_label("earnings_vol_retry_job")
    _mark_job_started(job_name)
    try:
        logger.info("Retrying earnings volatility scan for %d previously-failed ticker(s).", len(tickers))
        still_failed = run_earnings_vol_scan(tickers)
        if still_failed:
            logger.warning("Earnings volatility retry: %d ticker(s) still failed after retry: %s",
                            len(still_failed), still_failed)
            log_sched_notification(
                "Warning",
                f"Earnings Volatility retry: {len(still_failed)}/{len(tickers)} ticker(s) still "
                f"unreachable — will pick up on the next scheduled scan.",
            )
        else:
            log_sched_notification("Success", f"Earnings Volatility retry resolved all {len(tickers)} ticker(s).")
    except Exception as e:
        logger.error("Earnings volatility retry job failed: %s", e)
        log_sched_notification("Error", f"Earnings Volatility retry failed: {e}")
    finally:
        _mark_job_done(job_name)
        record_job_run('earnings_vol_retry_job')


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
            update_all_tail_risks(all_tickers, scan_type='tail_risk_universe')
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
            from predicted_movers_engine import backfill_actual_outcomes, log_predictions
            resolved = backfill_actual_outcomes()
            logged = log_predictions()
            logger.info("Predicted Movers: logged %d new predictions, resolved %d outcomes.", logged, resolved)
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
        max_per_day = int(
            config.get("NOTIFICATIONS", {}).get("AI_CONTAGION", {}).get("MAX_ALERTS_PER_DAY", 1)
        )

        for event in candidates:
            suppress = orch._evaluate_daily_alert_gate(
                "AIContagion", event["ticker"], conn, max_per_day=max_per_day
            )
            if suppress:
                logger.info("AIContagion: alert suppressed by daily gate (max_per_day=%s).", max_per_day)
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
    from alert_referee_engine import evaluate_alert
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

        candidate_tickers = [row["ticker"] for row in results if row.get("phase", "NEUTRAL") in alert_phases]
        currency_map = get_ticker_currency_map(candidate_tickers, conn)

        for row in results:
            phase = row.get("phase", "NEUTRAL")
            if phase not in alert_phases:
                continue

            ticker = row["ticker"]
            currency = currency_map.get(ticker, "")
            if not currency and "." not in ticker:
                # Proxy tickers (e.g. QQQ, SMH) have no stock_signals row to source currency
                # from; a bare suffix-less symbol is always a US listing on Yahoo Finance.
                currency = "USD"
            exchange = time_engine.ticker_exchange(ticker, currency)
            if not is_quote_settled(exchange, include_premarket=(exchange == "NYSE")):
                logger.debug("TrapMonitor: %s — %s market closed or quote not yet settled, suppressing alert.", ticker, exchange)
                continue

            verdict = evaluate_alert("TrapMonitor", ticker, phase, row, conn)
            if verdict.vetoed:
                logger.info(
                    "TrapMonitor: Alert Confidence Referee vetoed %s (%s), fire probability %.2f.",
                    ticker, phase, verdict.fire_probability,
                )
                continue

            reason = f"TRAP MONITOR {phase.replace('_', ' ')}"
            ema_distance = row.get("ema_distance")
            suppress = orch._evaluate_alert_gate("TrapMonitor", ticker, ema_distance, reason, conn)
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
                orch.record_alert_fired("TrapMonitor", ticker, ema_distance, reason, conn)
            logger.info("TrapMonitor: alert fired for %s (%s).", ticker, phase)

    except Exception as e:
        logger.error("Trap Monitor job failed: %s", e)
        log_sched_notification("Error", f"Trap Monitor job failed: {e}")
    finally:
        if conn:
            conn.close()
        record_job_run('trap_monitor_job')


def run_trap_accuracy_fill_job():
    from bull_bear_trap_engine import fill_trap_phase_actuals
    _mark_job_started(job_label("trap_accuracy_fill_job"))
    try:
        count = fill_trap_phase_actuals()
        log_sched_notification("Scheduler", f"Trap accuracy fill: resolved {count} predictions.")
    except Exception as e:
        logger.error("Trap accuracy fill job failed: %s", e)
        log_sched_notification("Error", f"Trap accuracy fill job failed: {e}")
    finally:
        _mark_job_done(job_label("trap_accuracy_fill_job"))
        record_job_run("trap_accuracy_fill_job")


def run_alert_referee_training_job():
    from alert_referee_engine import train_referee_model, TRAP_MONITOR_ENGINE
    _mark_job_started(job_label("alert_referee_training_job"))
    try:
        result = train_referee_model(TRAP_MONITOR_ENGINE)
        status = result.get("status")
        if status == "trained":
            log_sched_notification(
                "Success",
                f"Alert Confidence Referee: trained on {result['sample_count']} samples "
                f"(effective mode: {result['effective_mode']}).",
            )
        elif status == "insufficient_data":
            log_sched_notification(
                "Info",
                f"Alert Confidence Referee: not enough resolved samples yet "
                f"({result.get('sample_count', 0)}) — {result.get('message', 'skipping training.')}",
            )
        else:
            log_sched_notification("Error", f"Alert Confidence Referee training failed: {result.get('message')}")
    except Exception as e:
        logger.error("Alert Confidence Referee training job failed: %s", e)
        log_sched_notification("Error", f"Alert Confidence Referee training job failed: {e}")
    finally:
        _mark_job_done(job_label("alert_referee_training_job"))
        record_job_run("alert_referee_training_job")


def _fire_risk_orchestrator_critical_alerts(scopes: list) -> None:
    """Pillar C2: instant escalation when a scope's PHI or max correlation reaches RED — a
    computed severity condition, so per AGENTS.md rule 19 this uses the shared worsened/
    recovered/cooldown alert gate (not the static-threshold daily gate), keyed per scope so an
    "all" breach and an individual account breach never collide."""
    if not load_config().get("NOTIFICATIONS", {}).get("RISK_ORCHESTRATOR_ALERTS", {}).get("ENABLED", False):
        return
    if not scopes:
        return
    orch = IntradayOrchestrator()
    conn_alert = None
    try:
        conn_alert = get_connection()
        for row in scopes:
            if row["tier"] == "RED" and row["phi_score"] is not None:
                reason = f"PHI_RED_{row['scope']}"
                if not orch._evaluate_alert_gate("PhiCritical", row["scope"], row["phi_score"], reason, conn_alert):
                    msg = (
                        f"🌡️ **Portfolio Heat Index Critical: {row['scope_label']}** 🌡️\n\n"
                        f"PHI score {row['phi_score']} has reached RED.\n\n"
                        f"🔗 [View Portfolio Heat Index](/portfolio-heat-index)"
                    )
                    if notify("risk_orchestrator_phi_critical", "PhiCritical", msg, conn=conn_alert):
                        orch.record_alert_fired("PhiCritical", row["scope"], row["phi_score"], reason, conn_alert)
            if row["correlation_tier"] == "RED" and row["max_correlation"] is not None:
                reason = f"CORRELATION_SPIKE_{row['scope']}"
                if not orch._evaluate_alert_gate("CorrelationSpike", row["scope"], row["max_correlation"], reason, conn_alert):
                    msg = (
                        f"🔗 **Correlation Spike: {row['scope_label']}** 🔗\n\n"
                        f"Max pairwise correlation {row['max_correlation']} has reached RED.\n\n"
                        f"🔗 [View Portfolio Heat Index](/portfolio-heat-index)"
                    )
                    if notify("risk_orchestrator_correlation_spike", "CorrelationSpike", msg, conn=conn_alert):
                        orch.record_alert_fired("CorrelationSpike", row["scope"], row["max_correlation"], reason, conn_alert)
    except Exception as e:
        logger.error("Risk Orchestrator critical alert evaluation failed: %s", e)
    finally:
        if conn_alert:
            conn_alert.close()


def run_risk_orchestrator_job():
    from risk_orchestrator_engine import run_scan, get_critical_scopes
    _mark_job_started(job_label("risk_orchestrator_job"))
    try:
        log_sched_notification("Scheduler", "Started Risk Orchestrator Scan...")
        result = run_scan()
        log_sched_notification(
            "Success",
            f"Risk Orchestrator Scan complete — {result['scopes_computed']} scope(s) scored, "
            f"{result['tickers_scored']} ticker(s) rated.",
        )
        _fire_risk_orchestrator_critical_alerts(get_critical_scopes())
    except Exception as e:
        logger.error("Risk Orchestrator Scan failed: %s", e)
        log_sched_notification("Error", f"Risk Orchestrator Scan failed: {e}")
    finally:
        _mark_job_done(job_label("risk_orchestrator_job"))
        record_job_run("risk_orchestrator_job")


def run_bubble_radar_job():
    from bubble_radar_engine import run_bubble_scan
    _mark_job_started(job_label("bubble_radar_job"))
    try:
        log_sched_notification("Scheduler", "Started Bubble Radar Scan...")
        engine = DataEngine()
        tickers = engine.get_all_tickers()
        if not tickers:
            log_sched_notification("Info", "Bubble Radar: no tickers to scan.")
            return
        results = run_bubble_scan(tickers)
        flagged = sum(1 for v in results.values() if v.get("flag"))
        log_sched_notification("Success", f"Bubble Radar Scan complete — {len(results)} tickers, {flagged} flagged.")
    except Exception as e:
        logger.error("Bubble Radar Scan failed: %s", e)
        log_sched_notification("Error", f"Bubble Radar Scan failed: {e}")
    finally:
        _mark_job_done(job_label("bubble_radar_job"))
        record_job_run("bubble_radar_job")


def run_pairs_spread_monitor_job():
    from pairs_spread_engine import PairsSpreadEngine, SCOPE_PORTFOLIO_WATCHLIST
    config = load_config()
    _mark_job_started(job_label("pairs_spread_monitor_job"))
    conn = None
    try:
        conn = get_connection()
        engine = PairsSpreadEngine(config)
        results = engine.run_scan(scope=SCOPE_PORTFOLIO_WATCHLIST)

        if not results:
            log_sched_notification("Info", "Pairs Spread Monitor: no correlated pairs found.")
            return

        orch = IntradayOrchestrator()
        fired = 0
        for row in results:
            if abs(row["zscore"]) < engine.zscore_threshold:
                continue

            pair_key = row["pair_key"]
            reason = f"PAIRS SPREAD {row['direction']}"
            suppress = orch._evaluate_alert_gate("PairsSpreadMonitor", pair_key, abs(row["zscore"]), reason, conn)
            if suppress:
                continue

            feed_text = (
                f"**{row['ticker_a']}/{row['ticker_b']}** — z-score {row['zscore']:+.2f} | "
                f"correlation {row['correlation']:.2f} | {row['direction']}"
            )
            msg_lines = [
                f"📐 **PAIRS SPREAD MONITOR: {row['ticker_a']} / {row['ticker_b']}**",
                "",
                f"Spread z-score: {row['zscore']:+.2f} (threshold {engine.zscore_threshold:.2f})",
                f"Correlation: {row['correlation']:.2f}",
                row["direction"],
            ]
            if notify("pairs_spread_alert", "PairsSpreadMonitor", feed_text, nextcloud_text="\n".join(msg_lines), conn=conn):
                orch.record_alert_fired("PairsSpreadMonitor", pair_key, abs(row["zscore"]), reason, conn)
                fired += 1

        log_sched_notification("Success", f"Pairs Spread Monitor complete — {len(results)} pair(s) monitored, {fired} alert(s) fired.")
    except Exception as e:
        logger.error("Pairs Spread Monitor job failed: %s", e)
        log_sched_notification("Error", f"Pairs Spread Monitor job failed: {e}")
    finally:
        if conn:
            conn.close()
        _mark_job_done(job_label("pairs_spread_monitor_job"))
        record_job_run("pairs_spread_monitor_job")


def run_pattern_detection_job():
    from pattern_detection_engine import PatternDetectionEngine, DETECTORS
    config = load_config()
    _mark_job_started(job_label("pattern_detection_job"))
    # Uses notify(..., source="pattern_detection_job", ...) directly rather than
    # log_sched_notification()'s current_job_source() lookup — a manually-triggered run
    # (POST /api/pattern-detection/run) executes via background_tasks.add_task(), which never
    # goes through scheduler.add_job()'s _with_job_source wrapper, so current_job_source()
    # would return None and misroute this job's status under the generic scheduler_status
    # bucket instead of the "Pattern Detection" routing the operator actually configures.
    notify("pattern_detection_job", "Info", "Pattern Detection scan started.")
    conn = None
    try:
        conn = get_connection()
        engine = PatternDetectionEngine(config)
        results = engine.run_scan()

        if not results:
            notify("pattern_detection_job", "Info", "Pattern Detection complete — no candidates found.")
            return

        orch = IntradayOrchestrator()
        candidate_tickers = [row["ticker"] for row in results]
        currency_map = get_ticker_currency_map(candidate_tickers, conn)

        fired = 0
        for row in results:
            ticker = row["ticker"]
            family = row["pattern_family"]
            currency = currency_map.get(ticker, "")
            exchange = time_engine.ticker_exchange(ticker, currency)
            if not is_quote_settled(exchange, include_premarket=(exchange == "NYSE")):
                logger.debug("PatternDetector: %s — %s market closed or quote not yet settled, suppressing alert.", ticker, exchange)
                continue

            # Composite dedup key so different pattern families on the same ticker don't share
            # cooldown state (per AGENTS.md rule 19's composite-key pattern).
            gate_key = f"{family}:{ticker}"
            module = DETECTORS[family]
            label = module.phase_label(row["pattern_type"], row["phase"])
            key_level = row.get("key_level")
            reason = f"{family.upper()} {row['pattern_type'].upper()} {row['phase']}"
            severity = abs(row["close_price"] - key_level) / key_level * 100 if key_level else 0.0
            suppress = orch._evaluate_alert_gate("PatternDetector", gate_key, severity, reason, conn)
            if suppress:
                continue

            points_txt = " | ".join(f"{p['label']} {p['price']} ({p['date']})" for p in row.get("points", []))
            feed_text = (
                f"**{ticker}** — {label} | Key level {key_level if key_level is not None else '—'} | "
                f"Target {row.get('measured_target', '—')} | Vol confirms: {row.get('volume_confirms')} | "
                f"RSI divergence: {row.get('rsi_divergence')}"
            )
            msg_lines = [
                f"📐 **PATTERN DETECTED: {ticker}** — {label}",
                "",
                points_txt,
                f"Key level: {key_level if key_level is not None else '—'} | Measured target: {row.get('measured_target', '—')}",
                f"Volume confirms: {row.get('volume_confirms')} | RSI divergence: {row.get('rsi_divergence')} | "
                f"Pattern fit (R²): {row.get('pattern_r2') if row.get('pattern_r2') is not None else '—'}",
            ]
            if notify("pattern_detection_alert", "PatternDetector", feed_text, nextcloud_text="\n".join(msg_lines), conn=conn):
                orch.record_alert_fired("PatternDetector", gate_key, severity, reason, conn)
                fired += 1

        notify("pattern_detection_job", "Success", f"Pattern Detection complete — {len(results)} candidate(s), {fired} alert(s) fired.")
    except Exception as e:
        logger.error("Pattern Detection job failed: %s", e)
        notify("pattern_detection_job", "Error", f"Pattern Detection job failed: {e}", level="error")
    finally:
        if conn:
            conn.close()
        _mark_job_done(job_label("pattern_detection_job"))
        record_job_run("pattern_detection_job")


def run_pattern_detection_accuracy_fill_job():
    from pattern_detection_engine import fill_pattern_outcomes
    _mark_job_started(job_label("pattern_detection_accuracy_fill_job"))
    try:
        count = fill_pattern_outcomes()
        log_sched_notification("Scheduler", f"Pattern Detection accuracy fill: resolved {count} predictions.")
    except Exception as e:
        logger.error("Pattern Detection accuracy fill job failed: %s", e)
        log_sched_notification("Error", f"Pattern Detection accuracy fill job failed: {e}")
    finally:
        _mark_job_done(job_label("pattern_detection_accuracy_fill_job"))
        record_job_run("pattern_detection_accuracy_fill_job")


def run_pairs_spread_universe_scan():
    """On-demand only (no scheduler job, no alerting) — a full market-universe correlation
    scan is too expensive to run automatically every night, and firing alerts off a scan the
    operator didn't ask for and may not repeat would defeat the dedup/cooldown model, which
    assumes a recurring scan cadence."""
    from pairs_spread_engine import PairsSpreadEngine, SCOPE_UNIVERSE
    config = load_config()
    _mark_job_started(job_label("pairs_spread_universe_source"))
    try:
        engine = PairsSpreadEngine(config)
        results = engine.run_scan(scope=SCOPE_UNIVERSE)
        log_sched_notification("Success", f"Pairs Spread Monitor (Universe): {len(results)} pair(s) found.")
    except Exception as e:
        logger.error("Pairs Spread Monitor universe scan failed: %s", e)
        log_sched_notification("Error", f"Pairs Spread Monitor (Universe) scan failed: {e}")
    finally:
        _mark_job_done(job_label("pairs_spread_universe_source"))


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
            CronTrigger(day_of_week="mon-fri", hour=pre_h, minute=pre_m, timezone=timezone.utc),
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
            CronTrigger(day_of_week="mon-fri", hour=post_h, minute=post_m, timezone=timezone.utc),
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


def _run_account_scraper_job(account_id: int) -> None:
    job_id = f"account_scraper_{account_id}_job"
    acc = get_account(account_id)
    job_name = f"Account Price Scraper — {acc['name'] if acc else account_id}"
    _mark_job_started(job_name)
    try:
        from account_scraper_engine import run_scrape_for_account
        result = run_scrape_for_account(account_id)
        if result.get("status") != "success":
            log_sched_notification("Warning", f"{job_name}: {result.get('message', 'unknown error')}")
            return
        resnapshot_account(account_id)
        log_sched_notification("Success", f"{job_name}: price={result['price']}")
    except Exception as e:
        logger.error("Account scraper job %s failed: %s", job_id, e)
        log_sched_notification("Error", f"{job_name} failed: {e}")
    finally:
        _mark_job_done(job_name)
        record_job_run(job_id)


def register_account_scraper_job(account: dict) -> None:
    if not account.get("scraper_enabled") or not account.get("scraper_url") or not account.get("scraper_selector"):
        return
    account_id = account["id"]
    try:
        import time_engine
        hour, minute = map(int, (account.get("scrape_time") or "02:00").split(":"))
        scheduler.add_job(
            _run_account_scraper_job,
            CronTrigger(hour=hour, minute=minute, timezone=time_engine.get_user_tz()),
            id=f"account_scraper_{account_id}_job",
            kwargs={"account_id": account_id},
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info("Account scraper job registered for account %s at %s.", account_id, account.get("scrape_time"))
    except Exception as e:
        logger.error("Failed to register account scraper job for account %s: %s", account_id, e)


def unregister_account_scraper_job(account_id: int) -> None:
    """Remove the scraper job for a given account. Silently ignores a missing job."""
    from apscheduler.jobstores.base import JobLookupError
    try:
        scheduler.remove_job(f"account_scraper_{account_id}_job")
    except (JobLookupError, Exception):
        pass


def _run_account_topup_job(account_id: int) -> None:
    job_id = f"account_autotopup_{account_id}_job"
    acc = get_account(account_id)
    job_name = f"Account Auto Top-up — {acc['name'] if acc else account_id}"
    _mark_job_started(job_name)
    try:
        if not acc or not acc.get("autotopup_enabled"):
            return
        from database import create_pending_topup
        today = datetime.now(timezone.utc).date().isoformat()
        pending_id = create_pending_topup(account_id, today, acc["autotopup_amount"])
        if pending_id is None:
            notify("account_autotopup_status", "Error", f"{job_name}: failed to record pending top-up.", level="error")
            return
        notify("account_autotopup_status", "Success",
               f"{job_name}: {acc['autotopup_amount']} {acc['currency']} due — confirm on the Accounts page.",
               level="info")
    except Exception as e:
        logger.error("Account Auto Top-up job %s failed: %s", job_id, e)
        notify("account_autotopup_status", "Error", f"{job_name} failed: {e}", level="error")
    finally:
        _mark_job_done(job_name)
        record_job_run(job_id)


_AUTOTOPUP_WEEKDAY_NAMES = {1: "mon", 2: "tue", 3: "wed", 4: "thu", 5: "fri"}


def register_account_topup_job(account: dict) -> None:
    if not account.get("autotopup_enabled") or not account.get("autotopup_amount"):
        return
    account_id = account["id"]
    try:
        import time_engine
        frequency = account.get("autotopup_frequency")
        if frequency == "monthly":
            day = int(account["autotopup_day_of_month"])
            trigger = CronTrigger(day=day, hour=8, minute=0, timezone=time_engine.get_user_tz())
        elif frequency == "weekly":
            day_name = _AUTOTOPUP_WEEKDAY_NAMES[int(account["autotopup_day_of_week"])]
            trigger = CronTrigger(day_of_week=day_name, hour=8, minute=0, timezone=time_engine.get_user_tz())
        else:
            logger.error("Account Auto Top-up: unknown frequency %r for account %s", frequency, account_id)
            return
        scheduler.add_job(
            _run_account_topup_job,
            trigger,
            id=f"account_autotopup_{account_id}_job",
            kwargs={"account_id": account_id},
            replace_existing=True,
            misfire_grace_time=300,
        )
        logger.info("Account Auto Top-up job registered for account %s (%s).", account_id, frequency)
    except Exception as e:
        logger.error("Failed to register Account Auto Top-up job for account %s: %s", account_id, e)


def unregister_account_topup_job(account_id: int) -> None:
    """Remove the Auto Top-up job for a given account. Silently ignores a missing job."""
    from apscheduler.jobstores.base import JobLookupError
    try:
        scheduler.remove_job(f"account_autotopup_{account_id}_job")
    except (JobLookupError, Exception):
        pass


def run_forensic_quarterly_fetch_job():
    # GUI name: "Forensic Quarterly Data Fetch". Canonical scheduled-job name lives in scheduler_engine.JOB_GRAPH.
    import json
    import time as _time
    from yahoo_engine import yahoo_engine as _yengine
    from config import FORENSIC_DIR
    _mark_job_started(job_label("forensic_quarterly_fetch_job"))
    try:
        log_sched_notification("Scheduler", "Forensic Quarterly Data Fetch started...")
        engine = DataEngine()
        tickers = engine.get_all_tickers()
        if not tickers:
            log_sched_notification("Info", "Forensic Quarterly Data Fetch: no tickers found.")
            return
        FORENSIC_DIR.mkdir(parents=True, exist_ok=True)
        now_utc = datetime.now(timezone.utc)

        # Build a set of non-equity quote types from asset_profiles so we can skip funds/ETFs.
        non_equity_tickers: set = set()
        try:
            _pconn = get_connection()
            _rows = _pconn.execute(
                "SELECT ticker FROM asset_profiles WHERE quote_type NOT IN ('EQUITY', 'NONE') AND quote_type IS NOT NULL"
            ).fetchall()
            non_equity_tickers = {r[0] for r in _rows}
            _pconn.close()
        except Exception:
            pass

        fetched = skipped = errors = 0
        for ticker in tickers:
            if ticker in non_equity_tickers:
                skipped += 1
                continue
            cache_path = FORENSIC_DIR / f"{ticker}.json"
            if cache_path.exists():
                try:
                    age_days = (now_utc.timestamp() - cache_path.stat().st_mtime) / 86400
                    if age_days < 30:
                        skipped += 1
                        continue
                except OSError:
                    pass
            try:
                bs, fin, cf = _yengine.get_annual_financials(ticker)
                if bs is None:
                    log_sched_notification("Warning", f"Forensic fetch: {ticker} — Yahoo Finance returned no annual balance sheet (sparse coverage or newly listed). Skipping.")
                    errors += 1
                    continue
                payload = {
                    "ticker": ticker,
                    "fetched_utc": now_utc.strftime("%Y-%m-%d %H:%M:%S"),
                    "balance_sheet": {str(k): v for k, v in bs.to_dict().items()},
                    "financials":    {str(k): v for k, v in fin.to_dict().items()} if fin is not None else {},
                    "cashflow":      {str(k): v for k, v in cf.to_dict().items()}  if cf is not None else {},
                }
                with open(cache_path, 'w') as f:
                    json.dump(payload, f, default=str)
                fetched += 1
                _time.sleep(0.5)
            except Exception as e:
                log_sched_notification("Warning", f"Forensic fetch failed for {ticker}: {e}")
                errors += 1
        log_sched_notification("Success", f"Forensic Quarterly Data Fetch complete — {fetched} fetched, {skipped} skipped (fresh), {errors} errors.")
    except Exception as e:
        logger.error("Forensic Quarterly Data Fetch failed: %s", e)
        log_sched_notification("Error", f"Forensic Quarterly Data Fetch failed: {e}")
    finally:
        _mark_job_done(job_label("forensic_quarterly_fetch_job"))
        record_job_run("forensic_quarterly_fetch_job")


def run_forensic_scores_job():
    # GUI name: "Forensic Accounting Scores". Canonical scheduled-job name lives in scheduler_engine.JOB_GRAPH.
    import json
    import pandas as pd
    from config import FORENSIC_DIR
    from fundamentals_helpers import calculate_piotroski_f_score, calculate_altman_z_score, calculate_beneish_m_score
    from universe_fundamentals_engine import _fetch_info
    _mark_job_started(job_label("forensic_scores_job"))
    try:
        log_sched_notification("Scheduler", "Forensic Accounting Scores started...")
        engine = DataEngine()
        tickers = engine.get_all_tickers()
        if not tickers:
            log_sched_notification("Info", "Forensic Accounting Scores: no tickers found.")
            return

        scored = alerts = errors = 0
        now_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")

        for ticker in tickers:
            cache_path = FORENSIC_DIR / f"{ticker}.json"
            if not cache_path.exists():
                continue
            try:
                with open(cache_path, 'r') as f:
                    data = json.load(f)

                bs  = pd.DataFrame.from_dict(data.get('balance_sheet', {}))
                fin = pd.DataFrame.from_dict(data.get('financials', {}))
                cf  = pd.DataFrame.from_dict(data.get('cashflow', {}))

                if not bs.empty:
                    bs.columns  = pd.to_datetime(bs.columns)
                    bs  = bs.sort_index(axis=1, ascending=False)
                if not fin.empty:
                    fin.columns = pd.to_datetime(fin.columns)
                    fin = fin.sort_index(axis=1, ascending=False)
                if not cf.empty:
                    cf.columns  = pd.to_datetime(cf.columns)
                    cf  = cf.sort_index(axis=1, ascending=False)

                info = _fetch_info(ticker)
                piotroski = calculate_piotroski_f_score(bs, fin, cf)
                altman    = calculate_altman_z_score(info, bs, fin)
                beneish   = calculate_beneish_m_score(bs, fin, cf)

                company_name = ticker
                conn = None
                try:
                    conn = get_connection()
                    cursor = conn.cursor()
                    cursor.execute(
                        "UPDATE stock_signals SET piotroski_f_score=?, altman_z_score=?, beneish_m_score=?, forensic_last_updated=? WHERE ticker=?",
                        (piotroski, altman, beneish, now_ts, ticker),
                    )
                    if cursor.rowcount == 0:
                        cursor.execute(
                            "INSERT OR IGNORE INTO stock_signals (ticker, piotroski_f_score, altman_z_score, beneish_m_score, forensic_last_updated, score_method) VALUES (?,?,?,?,?,'FORENSIC')",
                            (ticker, piotroski, altman, beneish, now_ts),
                        )
                    conn.commit()
                    row = conn.execute("SELECT company_name FROM stock_signals WHERE ticker=?", (ticker,)).fetchone()
                    if row and row[0]:
                        company_name = row[0]
                finally:
                    if conn:
                        conn.close()

                scored += 1

                flag_lines = []
                if piotroski is not None and piotroski < 4:
                    flag_lines.append(
                        f"  • Piotroski F-Score: {int(piotroski)}/9 — scored below 4, indicating deterioration "
                        f"across profitability, leverage, or efficiency metrics. "
                        f"A score under 4 historically precedes fundamental decline."
                    )
                if altman is not None and altman < 1.81:
                    zone = "distress zone (< 1.1)" if altman < 1.1 else "grey zone (1.1–1.81)"
                    flag_lines.append(
                        f"  • Altman Z-Score: {altman:.2f} — in the {zone}. "
                        f"This bankruptcy-prediction model flags elevated insolvency risk. "
                        f"Scores below 1.81 require monitoring; below 1.1 indicate acute distress."
                    )
                if beneish is not None and beneish > -1.78:
                    flag_lines.append(
                        f"  • Beneish M-Score: {beneish:.3f} — above the −1.78 manipulation threshold. "
                        f"The Beneish model detects statistical patterns in annual filings consistent with "
                        f"earnings manipulation (e.g. inflated receivables, margin compression, aggressive accruals). "
                        f"This is a statistical signal, not a confirmed finding — review the annual report."
                    )

                if flag_lines:
                    alert_lines = [
                        f"⚠️ Forensic Accounting Alert — {company_name} ({ticker})",
                        f"The monthly Forensic Accounting Scores engine has flagged this holding:",
                        "",
                    ] + flag_lines + [
                        "",
                        f"Scores are derived from annual financial statements via Yahoo Finance. "
                        f"Review the latest annual report before acting. "
                        f"Source: Forensic Accounting Scores engine.",
                    ]
                    alert_msg = "\n".join(alert_lines)
                    alert_conn = None
                    try:
                        alert_conn = get_connection()
                        notify("forensic_alert", "Forensic Alert", alert_msg, conn=alert_conn)
                    finally:
                        if alert_conn:
                            alert_conn.close()
                    alerts += 1

            except Exception as e:
                log_sched_notification("Warning", f"Forensic scoring failed for {ticker}: {e}")
                errors += 1

        log_sched_notification("Success", f"Forensic Accounting Scores complete — {scored} scored, {alerts} alerts fired, {errors} errors.")
    except Exception as e:
        logger.error("Forensic Accounting Scores failed: %s", e)
        log_sched_notification("Error", f"Forensic Accounting Scores failed: {e}")
    finally:
        _mark_job_done(job_label("forensic_scores_job"))
        record_job_run("forensic_scores_job")


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


def run_treasury_auction_check(slot: str) -> None:
    job_id = f"macro_auction_job_{slot}"
    _mark_job_started(job_label(job_id))
    try:
        log_sched_notification("Scheduler", f"Started Sovereign Debt Auction Monitor ({slot.upper()})...")
        new_count = check_auction_results()
        log_sched_notification("Success", f"Sovereign Debt Auction Monitor ({slot.upper()}) complete: {new_count} new result(s).")
    except Exception as e:
        logger.error("Sovereign Debt Auction Monitor (%s) failed: %s", slot, e)
        log_sched_notification("Error", f"Sovereign Debt Auction Monitor ({slot.upper()}) failed: {e}")
    finally:
        _mark_job_done(job_label(job_id))
        record_job_run(job_id)


# Imported last (not at module top) — scheduler_engine.py itself imports every run_* function
# from this module, so importing it before they're all defined deadlocks the circular import.
from scheduler_engine import (
    scheduler, record_job_run, log_sched_notification,
    _mark_job_started, _mark_job_done,
)
