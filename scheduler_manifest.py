import re

# `label` MUST match how the job is named in the Settings GUI (panel/card header) so
# the same wording is searchable in both places — never use code-style names here.
# Jobs that share one Settings panel use that panel's name plus a parenthetical.
JOB_GRAPH: dict[str, dict] = {
    # External data sources — not scheduled jobs; rendered with distinct styling in the graph.
    "yahoo_finance_source":           {"label": "Yahoo Finance",                                  "category": "external",    "engine": "yahoo_engine.py",               "produces": ["yahoo_price_data"],                                           "consumes": [],                                                                   "settings_anchor": None},

    # Built-in Accounts — not scheduled jobs either; the data originates from the operator
    # hand-entering transactions/prices rather than a job run, but it still flows into the
    # same downstream artifacts (e.g. "portfolio") so it must appear on the graph too.
    "manual_account_entry_source":    {"label": "Manual Account Entry",                           "category": "manual",      "engine": "accounts_engine.py",            "produces": ["manual_account_entries"],                                     "consumes": [],                                                                   "non_job": True, "settings_anchor": None},
    "trading_accounts_node":          {"label": "Trading Account(s)",                             "category": "manual",      "engine": "accounts_engine.py",            "produces": ["portfolio"],                                                  "consumes": ["manual_account_entries", "account_autotopup_pending"],              "non_job": True, "settings_anchor": None},
    "pension_accounts_node":          {"label": "Pension Account(s)",                              "category": "manual",      "engine": "accounts_engine.py",            "produces": ["pension_account_data"],                                       "consumes": ["manual_account_entries"],                                           "non_job": True, "settings_anchor": None},
    "house_accounts_node":            {"label": "House Account(s)",                                "category": "manual",      "engine": "accounts_engine.py",            "produces": ["house_account_data"],                                         "consumes": ["manual_account_entries"],                                           "non_job": True, "settings_anchor": None},

    "ghostfolio_sync_job":            {"label": "Ghostfolio Sync",                                "category": "data",        "engine": "ghostfolio_sync.py",            "produces": ["portfolio"],                                                  "consumes": [],                                                                   "settings_anchor": "ghostfolio-integration-card"},
    "freetrade_sync_job":             {"label": "Freetrade Broker Integration",                   "category": "data",        "engine": "freetrade_engine.py",           "produces": ["market_universe", "portfolio"],                               "consumes": [],                                                                   "settings_anchor": "market-universe-card"},
    "index_scraper_job":              {"label": "Index Constituents Scraper",                     "category": "data",        "engine": "index_engine.py",               "produces": ["index_constituents"],                                         "consumes": [],                                                                   "settings_anchor": "market-universe-card"},
    "universe_routine_job":           {"label": "Legacy File Sideloading & Nasdaq Sync",          "category": "universe",    "engine": "universe_engine.py",            "produces": ["market_universe", "quant_signals", "tail_risk", "sentiment", "ml_features"], "consumes": ["historical_parquet", "index_constituents"],            "settings_anchor": "market-universe-card"},
    "universe_deep_sync_job":         {"label": "Universe Deep Sync Pipeline",                    "category": "universe",    "engine": "universe_deep_sync_engine.py",  "produces": ["fundamentals", "ticker_metadata", "quant_signals", "ml_predictions"], "consumes": ["market_universe", "historical_parquet", "ml_model", "yahoo_price_data"], "settings_anchor": "market-universe-card"},
    "fundamentals_profiler_job":      {"label": "Fundamentals Data Profiler",                     "category": "data",        "engine": "profile_engine.py",             "produces": ["fundamentals", "asset_profiles"],                             "consumes": ["market_universe", "yahoo_price_data"],                              "settings_anchor": "market-universe-card"},
    "overnight_quant_scan_job":       {"label": "Daily Quant Screener (Portfolio & Watchlist)",   "category": "quant",       "engine": "quant_engine.py",               "produces": ["quant_signals", "tail_risk"],                                 "consumes": ["historical_parquet"],                                               "settings_anchor": "automation-schedulers-card"},
    "quant_analysis_job":             {"label": "Quantamental Analysis Engine",                   "category": "quant",       "engine": "quant_signals.py",              "produces": ["historical_parquet", "stock_signals", "quant_signals", "market_regimes", "macro_regimes", "etf_holdings_cache"], "consumes": ["portfolio", "macro_indicators", "yahoo_price_data"], "settings_anchor": "automation-schedulers-card"},
    "weekend_earnings_vol_scan_job":  {"label": "Earnings Volatility Engine",                     "category": "quant",       "engine": "earnings_vol_engine.py",        "produces": ["earnings_volatility"],                                        "consumes": ["historical_parquet", "yahoo_price_data"],                           "settings_anchor": "automation-schedulers-card"},
    "ml_backfill_job":                {"label": "Historical Data Backfill & Sync",                "category": "ml",          "engine": "ai_prediction_engine.py",       "produces": ["ml_features"],                                                "consumes": ["historical_parquet", "quant_signals"],                              "settings_anchor": "ml-ai-card"},
    "ml_training_job":                {"label": "Global Model Training (Walk-Forward)",           "category": "ml",          "engine": "ai_prediction_engine.py",       "produces": ["ml_model"],                                                   "consumes": ["ml_features"],                                                      "settings_anchor": "ml-ai-card"},
    "ml_inference_job":               {"label": "Daily ML Inference",                             "category": "ml",          "engine": "ai_prediction_engine.py",       "produces": ["ml_predictions"],                                             "consumes": ["quant_signals", "ml_model"],                                        "settings_anchor": "ml-ai-card"},
    "anomaly_training_job":           {"label": "Isolation Forest Anomaly Detection",             "category": "ml",          "engine": "anomaly_engine.py",             "produces": ["anomaly_models"],                                             "consumes": ["historical_parquet"],                                               "settings_anchor": "ml-ai-card"},
    "xray_risk_cache_job":            {"label": "Portfolio X-ray Risk Cache",                     "category": "risk",        "engine": "xray_engine.py",                "produces": ["xray_caches"],                                                "consumes": ["historical_parquet", "portfolio", "yahoo_price_data"],              "settings_anchor": "automation-schedulers-card"},
    "sentiment_scan_job":             {"label": "NLP Market Sentiment Engine",                    "category": "sentiment",   "engine": "huggingface_engine.py",         "produces": ["sentiment"],                                                  "consumes": [],                                                                   "settings_anchor": "ml-ai-card"},
    "news_feed_job":                  {"label": "News Feed",                                      "category": "sentiment",   "engine": "news_feed_engine.py",           "produces": ["news_articles", "sentiment"],                                 "consumes": ["yahoo_price_data"],                                                 "settings_anchor": "news-rss-card"},
    "market_sentiment_job":           {"label": "Market Sentiment (Fear & Greed)",                "category": "alert",       "engine": "sentiment_engine.py",           "produces": [],                                                             "consumes": ["sentiment", "market_regimes"],                                      "settings_anchor": "alerts-briefings-card"},
    "cb_nlp_alert_job":               {"label": "Central Bank NLP Alert",                         "category": "alert",       "engine": "huggingface_engine.py",         "produces": [],                                                             "consumes": ["macro_calendar"],                                                   "settings_anchor": "ml-ai-card"},
    "macro_calendar_job":             {"label": "Macroeconomic Automation Schedulers (Calendar)", "category": "macro",       "engine": "macro_calendar_engine.py",      "produces": ["macro_calendar"],                                             "consumes": [],                                                                   "settings_anchor": "macro-data-card"},
    "macro_data_job":                 {"label": "Macroeconomic Automation Schedulers (Data)",     "category": "macro",       "engine": "macro_data_engine.py",          "produces": ["macro_indicators"],                                           "consumes": [],                                                                   "settings_anchor": "macro-data-card"},
    "earnings_alert_job":             {"label": "Portfolio Earnings Alerts",                      "category": "alert",       "engine": "earnings_engine.py",            "produces": [],                                                             "consumes": ["portfolio", "yahoo_price_data"],                                    "settings_anchor": "alerts-briefings-card"},
    "insider_alert_job":              {"label": "Insider Trading Alerts",                         "category": "alert",       "engine": "insider_engine.py",             "produces": [],                                                             "consumes": ["portfolio", "yahoo_price_data"],                                    "settings_anchor": "alerts-briefings-card"},
    "intraday_orchestrator_job":      {"label": "Crash & Moonshot Alerts",                        "category": "intraday",    "engine": "intraday_orchestrator.py",      "produces": ["intraday_parquet", "market_pulse_cache", "account_performance_cache"],  "consumes": ["yahoo_price_data", "etf_holdings_cache"],                           "settings_anchor": "crash-moonshot-card"},
    "intraday_dip_scan_job":          {"label": "Dip Radar — Intraday Bottom Finder",             "category": "intraday",    "engine": "intraday_bottom_engine.py",     "produces": ["intraday_monitor_results", "market_pulse_cache"],             "consumes": ["intraday_parquet"],                                                 "settings_anchor": "dip-radar-settings-card"},
    "intraday_dip_reset_lse_job":     {"label": "Dip Radar — Intraday Bottom Finder (LSE reset)", "category": "intraday",    "engine": "intraday_bottom_engine.py",     "produces": ["intraday_monitor_results"],                                   "consumes": [],                                                                   "settings_anchor": "dip-radar-settings-card"},
    "intraday_dip_reset_nyse_job":    {"label": "Dip Radar — Intraday Bottom Finder (NYSE reset)","category": "intraday",    "engine": "intraday_bottom_engine.py",     "produces": ["intraday_monitor_results"],                                   "consumes": [],                                                                   "settings_anchor": "dip-radar-settings-card"},
    "ai_contagion_job":               {"label": "AI Sector Contagion Monitor",                    "category": "intraday",    "engine": "ai_contagion_engine.py",        "produces": ["ai_contagion_snapshots"],                                     "consumes": ["intraday_parquet"],                                                 "settings_anchor": "ai-contagion-card"},
    "trap_monitor_job":               {"label": "Market Trap & Recovery Monitor",                 "category": "intraday",    "engine": "bull_bear_trap_engine.py",      "produces": ["trap_monitor_results"],                                       "consumes": ["historical_parquet"],                                               "settings_anchor": "trap-monitor-card"},
    "trap_accuracy_fill_job":         {"label": "Market Trap & Recovery Monitor (accuracy fill)", "category": "intraday",    "engine": "bull_bear_trap_engine.py",      "produces": ["trap_phase_history"],                                         "consumes": ["trap_phase_history", "historical_parquet"],                         "settings_anchor": "trap-monitor-card"},
    "etf_predictor_actual_fill_job":  {"label": "ETF Price Predictors (actual fill)",             "category": "predictor",   "engine": "etf_predictor_engine.py",       "produces": ["etf_predictions"],                                            "consumes": ["etf_predictions"],                                                  "settings_anchor": "tools-card"},
    "maintenance_job":                {"label": "Database & File Maintenance",                    "category": "maintenance", "engine": "maintenance_engine.py",         "produces": [],                                                             "consumes": [],                                                                   "settings_anchor": "diagnostics-panel"},
    "system_check_job":               {"label": "System Configuration Check",                     "category": "maintenance", "engine": "system_check_engine.py",        "produces": [],                                                             "consumes": [],                                                                   "settings_anchor": "diagnostics-panel"},
    "etf_predictor_dynamic":          {"label": "ETF Price Predictors",                           "category": "predictor",   "engine": "etf_predictor_engine.py",       "produces": ["etf_predictions"],                                            "consumes": [],                        "dynamic": True,                          "settings_anchor": "tools-card"},
    "bubble_radar_job":               {"label": "Bubble Radar Scan",                              "category": "quant",       "engine": "bubble_radar_engine.py",        "produces": ["bubble_radar_metrics", "bubble_radar_history"],               "consumes": ["quant_signals", "stock_signals", "macro_indicators"],               "settings_anchor": "tools-card"},
    "forensic_quarterly_fetch_job":   {"label": "Forensic Quarterly Data Fetch",                  "category": "quant",       "engine": "fundamentals_helpers.py",       "produces": ["forensic_quarterly_cache"],                                   "consumes": ["portfolio", "yahoo_price_data"],                                    "settings_anchor": "forensic-screener-card"},
    "forensic_scores_job":            {"label": "Forensic Accounting Scores",                     "category": "quant",       "engine": "fundamentals_helpers.py",       "produces": ["stock_signals", "forensic_scores"],                           "consumes": ["forensic_quarterly_cache", "fundamentals"],                         "settings_anchor": "forensic-screener-card"},
    "macro_auction_job_am":           {"label": "Sovereign Debt Auction Monitor (AM)",             "category": "macro",       "engine": "treasury_auction_engine.py",    "produces": ["treasury_auction_results"],                                   "consumes": [],                                                                   "settings_anchor": "macro-data-card"},
    "macro_auction_job_pm":           {"label": "Sovereign Debt Auction Monitor (PM)",             "category": "macro",       "engine": "treasury_auction_engine.py",    "produces": ["treasury_auction_results"],                                   "consumes": [],                                                                   "settings_anchor": "macro-data-card"},
    "account_value_snapshot_job":     {"label": "Account Value Snapshot",                         "category": "portfolio",   "engine": "accounts_engine.py",            "produces": ["account_value_history", "account_performance_cache"],        "consumes": ["stock_signals"],                                                    "settings_anchor": "automation-schedulers-card"},
    "account_performance_refresh_job":{"label": "Account Performance Refresh",                    "category": "intraday",    "engine": "accounts_engine.py",            "produces": ["account_performance_cache"],                                  "consumes": ["market_pulse_cache", "stock_signals"],                              "settings_anchor": None},
    "account_scraper_dynamic":        {"label": "Account Price Scraper",                          "category": "portfolio",   "engine": "account_scraper_engine.py",     "produces": ["account_price_history"],                                      "consumes": [],                        "dynamic": True,                          "settings_anchor": None},
    "account_autotopup_dynamic":      {"label": "Account Auto Top-up",                            "category": "portfolio",   "engine": "accounts_engine.py",            "produces": ["account_autotopup_pending"],                                  "consumes": [],                        "dynamic": True,                          "settings_anchor": None},
    "treasury_bill_maturity_sweep_job": {"label": "UK Treasury Bill Maturity Sweep",              "category": "portfolio",   "engine": "treasury_bill_engine.py",       "produces": ["account_transactions", "treasury_bills"],                    "consumes": ["treasury_bills"],                                                   "settings_anchor": "automation-schedulers-card"},
    "backup_job":                     {"label": "Automated Backup",                               "category": "maintenance", "engine": "backup_engine.py",              "produces": ["backup_archive"],                                             "consumes": ["analysis.db", "data", "models"],                                    "settings_anchor": "backup-recovery-card"},

    # Home Assistant "Refresh Data" button — not a scheduled job; an on-demand HTTP trigger that
    # rides the same market_pulse_cache/account_performance_cache artifacts as the intraday scan.
    "ha_refresh_now_source":         {"label": "Home Assistant Refresh Now",                     "category": "manual",      "engine": "api_routes_accounts.py",        "produces": ["market_pulse_cache", "market_pulse_sparkline", "account_performance_cache"],  "consumes": ["yahoo_price_data"],                                                 "non_job": True, "settings_anchor": None},

    # Markets page — not a scheduled job; refresh is page-traffic-driven (same pattern as Market
    # Pulse itself), triggered inline from GET /api/markets whenever a tile's cache is stale.
    "markets_page_source":           {"label": "Markets Page",                                   "category": "manual",      "engine": "markets_engine.py",             "produces": ["market_pulse_cache", "market_pulse_sparkline"],              "consumes": ["yahoo_price_data"],                                                 "non_job": True, "settings_anchor": "markets-registry-card"},

    # Home Assistant's passive coordinator poll of GET /api/system/market-status — not a scheduled
    # job, but the only reliable background warm-up for the Markets registry on installs that never
    # press "Refresh Data" and never have /markets open in a browser tab (added 2026-07-10, since
    # the coordinator's normal poll otherwise never touched these tickers at all).
    "ha_market_status_source":       {"label": "Home Assistant Market Status Poll",              "category": "manual",      "engine": "api_routes_system.py",          "produces": ["market_pulse_cache"],                                         "consumes": ["yahoo_price_data"],                                                 "non_job": True, "settings_anchor": None},

    # Monte Carlo Wealth Simulator — not a scheduled job; pure on-demand computation over
    # already-cached X-ray artifacts (correlation matrix, risk cache), no new artifact produced.
    "monte_carlo_source":             {"label": "Monte Carlo Wealth Simulator",                   "category": "manual",      "engine": "monte_carlo_engine.py",         "produces": [],                                                             "consumes": ["xray_caches", "portfolio"],                                         "non_job": True, "settings_anchor": None},

    # Portfolio Tearsheet — not a scheduled job; pure on-demand computation over the same
    # xray_returns_cache-derived return series X-ray uses, no new artifact produced.
    "performance_analytics_source":  {"label": "Portfolio Tearsheet",                            "category": "manual",      "engine": "performance_analytics_engine.py", "produces": [],                                                           "consumes": ["xray_caches", "portfolio"],                                         "non_job": True, "settings_anchor": None},
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
    "TRAP_MONITORS":      "trap_monitor_job",
    "BUBBLE_RADAR":       "bubble_radar_job",
    "MARKET_SENTIMENT":   "market_sentiment_job",
    "EARNINGS_ALERTS":          "earnings_alert_job",
    "INSIDER_TRADING":          "insider_alert_job",
    "FORENSIC_QUARTERLY_FETCH": "forensic_quarterly_fetch_job",
    "FORENSIC_SCORES":          "forensic_scores_job",
    "MACRO_AUCTIONS":           "macro_auction_job_am",
    "ACCOUNT_VALUE_SNAPSHOT":   "account_value_snapshot_job",
    "BACKUP":                   "backup_job",
}

_DYNAMIC_ETF_RE = re.compile(r"^etf_predictor_\d+_(pre|post)_job$")
_DYNAMIC_ACCOUNT_SCRAPER_RE = re.compile(r"^account_scraper_\d+_job$")
_DYNAMIC_ACCOUNT_TOPUP_RE = re.compile(r"^account_autotopup_\d+_job$")


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


def _resolve_manifest(job_id: str) -> dict | None:
    meta = JOB_GRAPH.get(job_id)
    if meta is not None:
        return None if meta.get("dynamic") else meta
    if _DYNAMIC_ETF_RE.match(job_id):
        return JOB_GRAPH["etf_predictor_dynamic"]
    if _DYNAMIC_ACCOUNT_SCRAPER_RE.match(job_id):
        return JOB_GRAPH["account_scraper_dynamic"]
    if _DYNAMIC_ACCOUNT_TOPUP_RE.match(job_id):
        return JOB_GRAPH["account_autotopup_dynamic"]
    return None
