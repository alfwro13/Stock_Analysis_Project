# GUI name: "Notification Settings". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.
import logging
import threading
import sqlite3
from typing import Optional

from config import load_config
from database import log_notification as _db_log_notification
import nextcloud_talk

logger = logging.getLogger(__name__)

CHANNELS = ("log_file", "in_app", "nextcloud_talk")

_ON = {"log_file": True, "in_app": True, "nextcloud_talk": True}
_NO_TALK = {"log_file": True, "in_app": True, "nextcloud_talk": False}
LIFECYCLE_DEFAULT = dict(_NO_TALK)

SCHEDULER_STATUS_SOURCE = "scheduler_status"

_LEVELS = {"debug": logging.DEBUG, "info": logging.INFO, "warning": logging.WARNING, "error": logging.ERROR}

# Alert/briefing sources: each carries its canonical label and parent job_id (for grouping
# in the Settings panel) plus per-channel default routing.
NOTIFICATION_SOURCES: dict[str, dict] = {
    "crash_alert":           {"label": "Crash Alert",                  "job_id": "intraday_orchestrator_job", "default": dict(_ON)},
    "moonshot_alert":        {"label": "Moonshot Alert",               "job_id": "intraday_orchestrator_job", "default": dict(_ON)},
    "anomaly_alert":         {"label": "Anomaly Alert",                "job_id": "intraday_orchestrator_job", "default": dict(_ON)},
    "macro_yield_alert":     {"label": "Macro Yield Surge Alert",      "job_id": "intraday_orchestrator_job", "default": dict(_ON)},
    "hmm_regime_alert":      {"label": "Market Regime Change (HMM)",   "job_id": "quant_analysis_job",        "default": dict(_NO_TALK)},
    "market_stress_alert":   {"label": "Market Stress Alert",          "job_id": "quant_analysis_job",        "default": dict(_NO_TALK)},
    "ai_contagion_alert":    {"label": "AI Sector Contagion Alert",    "job_id": "ai_contagion_job",          "default": dict(_NO_TALK)},
    "trap_monitor_alert":    {"label": "Market Trap & Recovery Alert", "job_id": "trap_monitor_job",          "default": dict(_NO_TALK)},
    "bubble_radar_alert":    {"label": "Bubble Radar Alert",           "job_id": "bubble_radar_job",          "default": dict(_NO_TALK)},
    "dip_radar_alert":       {"label": "Dip Radar — Bottom Detected",  "job_id": "intraday_dip_scan_job",     "default": dict(_NO_TALK)},
    "earnings_alert":        {"label": "Portfolio Earnings Alert",     "job_id": "earnings_alert_job",        "default": dict(_ON)},
    "insider_alert":         {"label": "Insider Trading Alert",        "job_id": "insider_alert_job",         "default": dict(_ON)},
    "cb_nlp_alert":          {"label": "Central Bank NLP Alert",       "job_id": "cb_nlp_alert_job",          "default": dict(_ON)},
    "network_fault":         {"label": "Network Fault Alert",          "job_id": None,                        "default": dict(_NO_TALK)},
    "forensic_fetch_status": {"label": "Forensic Quarterly Data Fetch", "job_id": "forensic_quarterly_fetch_job", "default": dict(_NO_TALK)},
    "forensic_alert":          {"label": "Forensic Accounting Alert",       "job_id": "forensic_scores_job",    "default": dict(_ON)},
    "etf_predictor":           {"label": "ETF Predictor",                   "job_id": None,                     "default": dict(_NO_TALK)},
    "treasury_auction_alert":  {"label": "Sovereign Debt Auction Alert",    "job_id": "macro_auction_job_am",   "default": dict(_ON)},
    "accounts_csv_import":     {"label": "Accounts CSV Import",             "job_id": None,                     "default": dict(_NO_TALK)},
    "backup_status":           {"label": "Automated Backup",                "job_id": "backup_job",             "default": dict(_NO_TALK)},
    "account_value_snapshot_status": {"label": "Account Value Snapshot",    "job_id": "account_value_snapshot_job", "default": dict(_NO_TALK)},
    "treasury_bill_maturity_status": {"label": "Treasury Bill Maturity Sweep", "job_id": "treasury_bill_maturity_sweep_job", "default": dict(_NO_TALK)},
    "treasury_bill_reminder":        {"label": "Treasury Bill Reinvest Reminder", "job_id": "treasury_bill_maturity_sweep_job", "default": dict(_ON)},
    "account_autotopup_status": {"label": "Account Auto Top-up",            "job_id": None,                     "default": dict(_ON)},
    "system_update_status":    {"label": "System Updates & Power",          "job_id": None,                     "default": dict(_NO_TALK)},
    "ha_refresh_now_status":   {"label": "Home Assistant Refresh Now",      "job_id": None,                     "default": dict(_NO_TALK)},
    "stale_price_alert":       {"label": "Stale Price Alert",               "job_id": None,                     "default": dict(_ON)},
}

CATEGORY_LABELS = {
    "data": "Data Ingestion",
    "universe": "Universe",
    "quant": "Quant",
    "ml": "Machine Learning",
    "risk": "Risk",
    "sentiment": "Sentiment & News",
    "alert": "Alerts",
    "macro": "Macro",
    "briefing": "Briefings",
    "intraday": "Intraday",
    "predictor": "Predictors",
    "maintenance": "Maintenance",
    "other": "Other",
}


_job_ctx = threading.local()


def set_job_source(job_id: Optional[str]) -> None:
    _job_ctx.source = job_id


def clear_job_source() -> None:
    _job_ctx.source = None


def current_job_source() -> Optional[str]:
    return getattr(_job_ctx, "source", None)


def _default_routing(source: str) -> dict:
    meta = NOTIFICATION_SOURCES.get(source)
    if meta:
        return dict(meta["default"])
    return dict(LIFECYCLE_DEFAULT)


def effective_routing(source: str, config: Optional[dict] = None) -> dict:
    cfg = config if config is not None else load_config()
    default = _default_routing(source)
    override = (cfg.get("NOTIFICATION_ROUTING") or {}).get(source)
    if not override:
        return default
    return {ch: bool(override.get(ch, default[ch])) for ch in CHANNELS}


def notify(
    source: str,
    message_type: str,
    message_text: str,
    *,
    nextcloud_text: Optional[str] = None,
    level: str = "info",
    conn: Optional[sqlite3.Connection] = None,
) -> bool:
    """Route one event to log/in-app/Nextcloud per NOTIFICATION_ROUTING; returns False only when Talk is enabled and the send failed (lets dedup callers retry on the next scan)."""
    config = load_config()
    routing = effective_routing(source, config)

    if routing["log_file"]:
        logger.log(_LEVELS.get(level, logging.INFO), "[%s] %s", message_type, message_text)

    if routing["in_app"]:
        if conn is not None:
            try:
                conn.execute(
                    "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)",
                    (message_type, message_text),
                )
                conn.commit()
            except Exception as e:
                logger.error("notify: in-app write failed for %s: %s", source, e)
        else:
            _db_log_notification(message_type, message_text)

    if routing["nextcloud_talk"]:
        try:
            return bool(nextcloud_talk.send_text_message(nextcloud_text or message_text, config))
        except Exception as e:
            logger.error("notify: Nextcloud dispatch failed for %s: %s", source, e)
            return False

    return True


def send_test_message() -> bool:
    """Send a Nextcloud Talk connectivity test message. Returns True on success."""
    return nextcloud_talk.send_text_message(
        "✅ Quantamental test message — Nextcloud Talk integration is working correctly.", {}
    )


def build_routing_panel(config: Optional[dict] = None) -> list:
    """Grouped source list for the Settings 'Notification Settings' panel, keyed by JOB_GRAPH category."""
    from scheduler_engine import JOB_GRAPH, job_label

    cfg = config if config is not None else load_config()

    children: dict[str, list] = {}
    for key, meta in NOTIFICATION_SOURCES.items():
        children.setdefault(meta["job_id"], []).append(
            {"source": key, "label": meta["label"], "channels": effective_routing(key, cfg)}
        )

    groups: dict[str, list] = {}
    seen_categories: list[str] = []
    for job_id, meta in JOB_GRAPH.items():
        if meta.get("dynamic"):
            continue
        category = meta["category"]
        if category not in groups:
            groups[category] = []
            seen_categories.append(category)
        rows = [{
            "type": "status",
            "source": job_id,
            "label": job_label(job_id),
            "channels": effective_routing(job_id, cfg),
        }]
        for alert in children.get(job_id, []):
            rows.append({"type": "alert", **alert})
        groups[category].append(rows)

    panel = []
    for category in seen_categories:
        panel.append({
            "category": CATEGORY_LABELS.get(category, category.title()),
            "jobs": groups[category],
        })

    orphan_alerts = children.get(None, [])
    if orphan_alerts:
        panel.append({
            "category": CATEGORY_LABELS["other"],
            "jobs": [[{"type": "alert", **a} for a in orphan_alerts]],
        })
    return panel
