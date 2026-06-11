import logging
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from config import load_config
import time_engine
from sentiment_engine import run_nextcloud_alert
from huggingface_engine import update_all_sentiment, run_central_bank_nlp_alert
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
from database import get_universe_tickers, get_connection, fill_smgb_actual
from universe_engine import update_market_universe
from profile_engine import run_profile_audit
from regime_engine import calculate_market_regime
from ai_prediction_engine import train_global_ml_model, update_daily_ml_predictions, run_historical_backfill
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

scheduler = BackgroundScheduler()

import threading as _threading
from datetime import datetime as _dt
_active_jobs: dict[str, str] = {}
_active_jobs_lock = _threading.Lock()

def _mark_job_started(name: str) -> None:
    with _active_jobs_lock:
        _active_jobs[name] = _dt.utcnow().strftime("%Y-%m-%dT%H:%M:%S")

def _mark_job_done(name: str) -> None:
    with _active_jobs_lock:
        _active_jobs.pop(name, None)

def get_active_jobs() -> dict[str, str]:
    with _active_jobs_lock:
        return dict(_active_jobs)

def log_sched_notification(msg_type: str, msg_text: str):
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)", (msg_type, msg_text))
        conn.commit()
    except Exception as e:
        logger.error("Failed to log notification: %s", e)
    finally:
        if conn:
            conn.close()

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
        cursor.execute("SELECT job_id, last_run FROM scheduler_run_log")
        rows = cursor.fetchall()
        return {row[0]: row[1] for row in rows}
    except Exception:
        return {}
    finally:
        if conn:
            conn.close()

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
    _mark_job_started("Update Pipeline")
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
            config = load_config()
            notif_cfg = config.get("NOTIFICATIONS", {}).get("HMM_ALERTS", {})
            msg = (
                f"HMM REGIME CHANGE: {hmm['previous_label']} → {hmm['label']} "
                f"(confidence: {hmm['probability']:.0%}) | {hmm['date']}"
            )
            log_sched_notification("HMM Regime", msg)
            if notif_cfg.get("NEXTCLOUD_ENABLED", False):
                orch = IntradayOrchestrator()
                conn_alert = None
                try:
                    from database import get_connection as _gc
                    conn_alert = _gc()
                    suppress = orch._evaluate_alert_gate(
                        "HMM", "SPY", None, f"REGIME_{hmm['label']}", conn_alert
                    )
                    if not suppress:
                        try:
                            ok = send_text_message(msg, config)
                        except Exception as _e:
                            logger.error("HMM: Nextcloud dispatch failed: %s", _e)
                            ok = False
                        if ok:
                            orch.record_alert_fired(
                                "HMM", "SPY", None, f"REGIME_{hmm['label']}", conn_alert
                            )
                except Exception as _e:
                    logger.error("HMM alert gate error: %s", _e)
                finally:
                    if conn_alert:
                        conn_alert.close()

        # Fire notification when market stress IF detects sustained systemic anomaly
        stress = (regime_result or {}).get("market_stress", {})
        if stress and stress.get("alert"):
            config = load_config()
            stress_cfg = config.get("NOTIFICATIONS", {}).get("MARKET_STRESS_ALERTS", {})
            feats = stress.get("features", {})
            msg = (
                f"MARKET STRESS ALERT: Multivariate anomaly score {stress['score']:.2f}/1.00 "
                f"— systemic conditions are statistically abnormal. "
                f"VIX: {feats.get('vix_level', 0):.1f} | "
                f"SPY: {feats.get('spy_return', 0):+.2f}% | "
                f"HYG: {feats.get('hyg_return', 0):+.2f}% | "
                f"10Y Δ: {feats.get('tnx_change', 0):+.2f}bps | {stress['date']}"
            )
            log_sched_notification("Market Stress", msg)
            if stress_cfg.get("NEXTCLOUD_ENABLED", False):
                orch = IntradayOrchestrator()
                conn_alert = None
                try:
                    from database import get_connection as _gc
                    conn_alert = _gc()
                    suppress = orch._evaluate_alert_gate(
                        "MarketStress", "MARKET", None, "STRESS_ELEVATED", conn_alert
                    )
                    if not suppress:
                        try:
                            ok = send_text_message(msg, config)
                        except Exception as _e:
                            logger.error("Market stress: Nextcloud dispatch failed: %s", _e)
                            ok = False
                        if ok:
                            orch.record_alert_fired(
                                "MarketStress", "MARKET", None, "STRESS_ELEVATED", conn_alert
                            )
                except Exception as _e:
                    logger.error("Market stress alert gate error: %s", _e)
                finally:
                    if conn_alert:
                        conn_alert.close()

    except Exception as e:
        log_sched_notification("Error", f"Update Pipeline failed: {e}")
    finally:
        _mark_job_done("Update Pipeline")
        record_job_run('quant_analysis_job')

def run_ghostfolio_sync():
    _mark_job_started("Ghostfolio Sync")
    try:
        log_sched_notification("Scheduler", "Started Ghostfolio Sync...")
        sync_engine = GhostfolioSyncEngine()
        sync_engine.run_full_sync()
        log_sched_notification("Success", "Ghostfolio Sync completed successfully.")
    except Exception as e:
        log_sched_notification("Error", f"Ghostfolio Sync failed: {e}")
    finally:
        _mark_job_done("Ghostfolio Sync")
        record_job_run('ghostfolio_sync_job')

def run_freetrade_sync():
    _mark_job_started("Freetrade Sync")
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
        _mark_job_done("Freetrade Sync")
        record_job_run('freetrade_sync_job')

def run_sentiment_scan():
    _mark_job_started("Sentiment Scan")
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
        _mark_job_done("Sentiment Scan")
        record_job_run('sentiment_scan_job')

def run_overnight_quant_scan():
    """Portfolio + watchlist resumable quant scan followed by tail-risk computation."""
    _mark_job_started("Overnight Quant Scan")
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
        _mark_job_done("Overnight Quant Scan")
        record_job_run('overnight_quant_scan_job')

def run_weekend_earnings_scan():
    _mark_job_started("Earnings Volatility Scan")
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
        _mark_job_done("Earnings Volatility Scan")
        record_job_run('weekend_earnings_vol_scan_job')

def run_morning_briefing_dispatch():
    _mark_job_started("Morning Briefing Dispatch")
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
        _mark_job_done("Morning Briefing Dispatch")
        record_job_run('morning_briefing_dispatch_job')


def run_lunchtime_briefing_dispatch():
    _mark_job_started("Lunchtime Briefing Dispatch")
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
        _mark_job_done("Lunchtime Briefing Dispatch")
        record_job_run('lunchtime_briefing_dispatch_job')

def run_weekend_universe_routine():
    _mark_job_started("Weekend Universe Routine")
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
        _mark_job_done("Weekend Universe Routine")
        record_job_run('universe_routine_job')

def run_index_scraper():
    _mark_job_started("Index Constituents Scraper")
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
        _mark_job_done("Index Constituents Scraper")
        record_job_run('index_scraper_job')

def run_fundamentals_profiler():
    """Batch size read from SCHEDULING.PROFILER_ENGINE.BATCH_SIZE in config."""
    _mark_job_started("Fundamentals Profiler")
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
        _mark_job_done("Fundamentals Profiler")
        record_job_run('fundamentals_profiler_job')

def run_universe_deep_sync_job():
    """Scheduler envelope for universe_deep_sync_engine; that engine emits its own per-stage notifications."""
    _mark_job_started("Universe Deep Sync")
    try:
        log_sched_notification("Scheduler", "Started Universe Deep Sync Pipeline...")
        run_universe_deep_sync()
        log_sched_notification("Success", "Universe Deep Sync Pipeline job completed.")
    except Exception as e:
        logger.error("Universe Deep Sync Pipeline Failed: %s", e)
        log_sched_notification("Error", f"Universe Deep Sync Pipeline failed: {e}")
    finally:
        _mark_job_done("Universe Deep Sync")
        record_job_run('universe_deep_sync_job')


def run_ml_backfill():
    _mark_job_started("ML Historical Backfill")
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
        _mark_job_done("ML Historical Backfill")
        record_job_run('ml_backfill_job')

def run_ml_training():
    _mark_job_started("ML Global Training")
    try:
        log_sched_notification("Scheduler", "Started ML Global Training...")
        logger.info("ML Global Training initiated.")
        train_global_ml_model()
        logger.info("ML Global Training complete.")
        log_sched_notification("Success", "ML Global Training completed successfully.")
    except Exception as e:
        logger.error("ML Global Training Failed: %s", e)
        log_sched_notification("Error", f"ML Global Training failed: {e}")
    finally:
        _mark_job_done("ML Global Training")
        record_job_run('ml_training_job')

def run_ml_inference():
    _mark_job_started("Daily ML Inference")
    try:
        log_sched_notification("Scheduler", "Started Daily ML Inference...")
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
        logger.error("Daily ML Inference Failed: %s", e)
        log_sched_notification("Error", f"Daily ML Inference failed: {e}")
    finally:
        _mark_job_done("Daily ML Inference")
        record_job_run('ml_inference_job')

def run_macro_calendar_update():
    _mark_job_started("Macro Calendar Update")
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
        _mark_job_done("Macro Calendar Update")
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
    _mark_job_started("Macro Data Update")
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
        _mark_job_done("Macro Data Update")
        record_job_run('macro_data_job')

def run_xray_risk_cache_job():
    """Pre-computes portfolio beta, vol, correlation, and dividend yields for the X-ray report."""
    _mark_job_started("X-ray Risk Cache")
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
        _mark_job_done("X-ray Risk Cache")
        record_job_run('xray_risk_cache_job')


def run_anomaly_training_job():
    """Nightly retraining of per-ticker Isolation Forest anomaly models (Mon–Fri 18:30)."""
    _mark_job_started("Anomaly Training")
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
        _mark_job_done("Anomaly Training")
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
                    logger.error("AIContagion: Nextcloud dispatch failed: %s", e)
                    ok = False
            else:
                ok = True  # record dedup state even when Nextcloud is disabled

            if ok:
                orch.record_alert_fired(
                    "AIContagion", event["ticker"], event["price"], event["reason"], conn
                )
                orch.log_notification_feed(
                    "AIContagion",
                    _build_contagion_feed_text(event),
                    conn,
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
    """Scans portfolio + proxy tickers for bull/bear trap, capitulation, and Wyckoff accumulation signals."""
    from bull_bear_trap_engine import TrapEngine
    from intraday_orchestrator import IntradayOrchestrator
    from nextcloud_talk import send_text_message
    config = load_config()
    conn = None
    try:
        conn = get_connection()
        engine = TrapEngine(config)
        results = engine.run_scan()

        if not results:
            return

        trap_cfg = config.get("NOTIFICATIONS", {}).get("TRAP_MONITOR_ALERTS", {})
        nextcloud_enabled = trap_cfg.get("NEXTCLOUD_ENABLED", False)

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

            if nextcloud_enabled:
                msg_lines = [
                    f"🎭 **TRAP MONITOR: {ticker}** — {phase.replace('_', ' ')}",
                    "",
                    notes,
                    "",
                    f"RSI: {row.get('rsi', '—')} | EMA Distance: {row.get('ema_distance', '—')}%",
                    f"Bull Trap: {row.get('bull_trap_level', '—')} | Bear Trap: {row.get('bear_trap_level', '—')}",
                    f"Capitulation: {row.get('cap_level', '—')} | Wyckoff: {row.get('wyckoff_level', '—')}",
                ]
                try:
                    ok = send_text_message("\n".join(msg_lines), config)
                except Exception as e:
                    logger.error("TrapMonitor: Nextcloud dispatch failed for %s: %s", ticker, e)
                    ok = False
            else:
                ok = True

            if ok:
                orch.record_alert_fired("TrapMonitor", ticker, None, reason, conn)
                orch.log_notification_feed("TrapMonitor", feed_text, conn)
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
    """Runs the SMGB.L prediction and logs it; optionally dispatches to Nextcloud Talk."""
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
        log_sched_notification("Success", msg)

        cfg = load_config()
        smgb_cfg = cfg.get("SCHEDULING", {}).get("SMGB_PREDICTOR", {})
        if smgb_cfg.get("SEND_NEXTCLOUD", False):
            from nextcloud_talk import send_text_message
            send_text_message(msg, cfg)
    except Exception as e:
        logger.error("SMGB predictor job failed: %s", e)
        log_sched_notification("Error", f"SMGB predictor job failed: {e}")
    finally:
        record_job_run('smgb_predictor_job')


def _run_etf_predictor_job(config_id: int, fill_actuals: bool = False) -> None:
    job_type = "post" if fill_actuals else "pre"
    job_id = f"etf_predictor_{config_id}_{job_type}_job"
    job_name = f"ETF Predictor [{config_id}]"
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
    """Register or replace pre/post APScheduler jobs for a single ETF predictor config."""
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
    scheduler.start()


def shutdown_scheduler():
    scheduler.shutdown()
