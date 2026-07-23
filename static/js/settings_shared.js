var CONFIRM_TOKEN = window.CONFIRM_TOKEN;
var currentDiscoveredAccounts = JSON.parse(document.getElementById('discoveredAccountsData').textContent);
var macroInitState = JSON.parse(document.getElementById('macroInitState').textContent);

function setStatus(elId, type, msg) {
    const el = document.getElementById(elId);
    el.innerText = (type === 'success' ? '✅ ' : type === 'error' ? '❌ ' : type === 'warning' ? '⚠️ ' : '⏳ ') + msg;
    el.className = 'status-msg-sm ' + (type === 'success' ? 'msg-success' : type === 'error' ? 'msg-error' : type === 'warning' ? 'msg-warning' : 'msg-info');
}

function setBoxStatus(elId, type, htmlMsg) {
    const el = document.getElementById(elId);
    el.style.display = 'block';
    el.innerHTML = htmlMsg;
    el.className = 'status-msg-sm ' + (type === 'success' ? 'box-success' : type === 'error' ? 'box-error' : type === 'warning' ? 'box-warning' : 'box-info');
}

async function saveSettings(silent = false) {
    const btn = document.querySelector('.btn-save');

    if (!silent) {
        btn.disabled = true;
        btn.innerText = "Saving Configuration...";
    }

    const activeAccounts = [];
    document.querySelectorAll('.ghostfolio-account-checkbox').forEach((cb) => {
        if (cb.checked) activeAccounts.push(cb.value);
    });

    const quantDays        = Array.from(document.querySelectorAll('.quant-day:checked')).map(cb => cb.value);
    const earnDays         = Array.from(document.querySelectorAll('.earn-day:checked')).map(cb => cb.value);
    const universeDays     = Array.from(document.querySelectorAll('.universe-day:checked')).map(cb => cb.value);
    const mlBackfillDays   = Array.from(document.querySelectorAll('.ml-backfill-day:checked')).map(cb => cb.value);
    const mlTrainingDays   = Array.from(document.querySelectorAll('.ml-training-day:checked')).map(cb => cb.value);
    const mlInferenceDays  = Array.from(document.querySelectorAll('.ml-inference-day:checked')).map(cb => cb.value);
    const indexDays        = Array.from(document.querySelectorAll('.index-day:checked')).map(cb => cb.value);
    const profilerDays     = Array.from(document.querySelectorAll('.profiler-day:checked')).map(cb => cb.value);
    const udsDays          = Array.from(document.querySelectorAll('.uds-day:checked')).map(cb => cb.value);
    const backupDays       = Array.from(document.querySelectorAll('.backup-day:checked')).map(cb => cb.value);
    const activeIndices    = Array.from(document.querySelectorAll('.index-target:checked')).map(cb => cb.value);
    const refereeDays      = Array.from(document.querySelectorAll('.referee-training-day:checked')).map(cb => cb.value);
    const confluenceRefereeDays = Array.from(document.querySelectorAll('.confluence-referee-training-day:checked')).map(cb => cb.value);

    const payload = {
        "SERVER_URL": document.getElementById('SERVER_URL').value,
        "PORT": parseInt(document.getElementById('PORT').value),
        "BASE_CURRENCY": document.getElementById('BASE_CURRENCY').value,
        "USER_TIMEZONE": document.getElementById('USER_TIMEZONE').value.trim(),
        "HOME_EXCHANGE": document.getElementById('HOME_EXCHANGE').value,
        "IGNORED_TICKERS": document.getElementById('IGNORED_TICKERS').value.split(',').map(s => s.trim()).filter(Boolean),
        "ACCOUNT_CURRENCIES": document.getElementById('ACCOUNT_CURRENCIES').value.split(',').map(s => s.trim()).filter(Boolean),
        "FILE_LOGGING": {
            "ENABLED": document.getElementById('FILE_LOGGING_ENABLED').checked,
            "LEVEL": document.getElementById('FILE_LOGGING_LEVEL').value,
            "DAYS_TO_KEEP": parseInt(document.getElementById('FILE_LOGGING_DAYS_TO_KEEP').value) || 30,
            "ARCHIVE": document.getElementById('FILE_LOGGING_ARCHIVE').checked,
            "LOG_DIR": document.getElementById('FILE_LOGGING_LOG_DIR').value.trim() || 'logs'
        },
        "YAHOO_IPV6_ADDRESS": document.getElementById('YAHOO_IPV6_ADDRESS').value.trim(),
        "YAHOO_USE_IPV4": document.getElementById('YAHOO_USE_IPV4').checked,
        "YAHOO_USE_IPV6": document.getElementById('YAHOO_USE_IPV6').checked,
        "GHOSTFOLIO_ENABLED": document.getElementById('GHOSTFOLIO_ENABLED').checked,
        "GHOSTFOLIO_ACCOUNTS": {
            "discovered": currentDiscoveredAccounts,
            "active": activeAccounts
        },
        "UI_PREFERENCES": {
            "LIVE_PORTFOLIO": document.getElementById('LIVE_PORTFOLIO').checked,
            "LIVE_WATCHLIST": document.getElementById('LIVE_WATCHLIST').checked,
            "LIVE_DETAILS": document.getElementById('LIVE_DETAILS').checked,
            "FREETRADE_ONLY_MODE": document.getElementById('FREETRADE_ONLY_MODE').checked,
            "REFRESH_RATE": parseInt(document.getElementById('REFRESH_RATE').value) || 60,
            "MARKET_PULSE_DYNAMIC": document.getElementById('MARKET_PULSE_DYNAMIC').checked,
            "MARKET_PULSE_DESKTOP_COUNT": parseInt(document.getElementById('MARKET_PULSE_DESKTOP_COUNT').value) || 10,
            "MARKET_PULSE_MOBILE_COUNT": parseInt(document.getElementById('MARKET_PULSE_MOBILE_COUNT').value) || 8,
            "FONT_SIZE_NAV": parseInt(document.getElementById('FONT_SIZE_NAV').value) || 16,
            "FONT_SIZE_TABLE": parseInt(document.getElementById('FONT_SIZE_TABLE').value) || 12,
            "FONT_SIZE_DT_TABLE": parseInt(document.getElementById('FONT_SIZE_DT_TABLE').value) || 12,
            "FONT_SIZE_FORM": parseInt(document.getElementById('FONT_SIZE_FORM').value) || 12,
            "FONT_SIZE_BTN": parseInt(document.getElementById('FONT_SIZE_BTN').value) || 14,
            "FONT_SIZE_SECTION": parseInt(document.getElementById('FONT_SIZE_SECTION').value) || 20,
            "FONT_SIZE_BODY": parseInt(document.getElementById('FONT_SIZE_BODY').value) || 12,
            "FONT_SIZE_H1": parseInt(document.getElementById('FONT_SIZE_H1').value) || 17,
            "FONT_SIZE_H2": parseInt(document.getElementById('FONT_SIZE_H2').value) || 14,
            "FONT_SIZE_H3": parseInt(document.getElementById('FONT_SIZE_H3').value) || 12
        },
        "POSITION_SIZING": {
            "ACCOUNT_VALUE":   parseFloat(document.getElementById('POSITION_SIZING_ACCOUNT_VALUE').value) || 10000,
            "RISK_PCT":        parseFloat(document.getElementById('POSITION_SIZING_RISK_PCT').value) || 1.0,
            "STOP_MULTIPLE":   parseFloat(document.getElementById('POSITION_SIZING_STOP_MULTIPLE').value) || 2.0,
            "MIN_RISK_REWARD": parseFloat(document.getElementById('POSITION_SIZING_MIN_RISK_REWARD').value) || 1.5
        },
        "SCHEDULING": {
            "GHOSTFOLIO_SYNC": {
                "ENABLED": document.getElementById('GHOSTFOLIO_SYNC_ENABLED').checked,
                "FREQUENCY": document.getElementById('GHOSTFOLIO_SYNC_FREQ').value,
                "INTERVAL_HOURS": parseInt(document.getElementById('GHOSTFOLIO_SYNC_INTERVAL').value) || 0,
                "TIME": document.getElementById('GHOSTFOLIO_SYNC_TIME').value
            },
            "FREETRADE_SYNC": {
                "ENABLED": document.getElementById('FREETRADE_SYNC_ENABLED').checked,
                "FREQUENCY": document.getElementById('FREETRADE_SYNC_FREQ').value,
                "TIME": document.getElementById('FREETRADE_SYNC_TIME').value
            },
            "QUANT_ANALYSIS": {
                "ENABLED": document.getElementById('QUANT_ANALYSIS_ENABLED').checked,
                "FREQUENCY": document.getElementById('QUANT_ANALYSIS_FREQ').value,
                "INTERVAL_HOURS": parseInt(document.getElementById('QUANT_ANALYSIS_INTERVAL').value) || 0,
                "TIME": document.getElementById('QUANT_ANALYSIS_TIME').value
            },
            "SENTIMENT_ENGINE": {
                "ENABLED": document.getElementById('SENTIMENT_ENGINE_ENABLED').checked,
                "FREQUENCY": document.getElementById('SENTIMENT_ENGINE_FREQ').value,
                "START_TIME": document.getElementById('SENTIMENT_ENGINE_START').value,
                "END_TIME": document.getElementById('SENTIMENT_ENGINE_END').value,
                "INTERVAL_HOURS": parseInt(document.getElementById('SENTIMENT_ENGINE_INTERVAL').value) || 4
            },
            "NEWS_FEED": {
                "ENABLED": document.getElementById('NEWS_FEED_ENABLED').checked,
                "FREQUENCY": document.getElementById('NEWS_FEED_FREQ').value,
                "START_TIME": document.getElementById('NEWS_FEED_START').value,
                "END_TIME": document.getElementById('NEWS_FEED_END').value,
                "INTERVAL_HOURS": parseInt(document.getElementById('NEWS_FEED_INTERVAL').value) || 4,
                "MAX_PER_TICKER": parseInt(document.getElementById('NEWS_FEED_MAX_PER').value) || 5,
                "MAX_AGE_DAYS": parseInt(document.getElementById('NEWS_FEED_MAX_AGE').value) || 7
            },
            "CRASH_ALERTS": {
                "ENABLED": document.getElementById('CRASH_ALERTS_SCHED_ENABLED').checked,
                "FREQUENCY": document.getElementById('CRASH_ALERTS_FREQ').value,
                "START_TIME": document.getElementById('CRASH_ALERTS_START').value,
                "END_TIME": document.getElementById('CRASH_ALERTS_END').value,
                "INTERVAL_MINUTES": parseInt(document.getElementById('CRASH_ALERTS_MINUTES').value) || 10,
                "FLASH_CRASH_THRESHOLD": parseFloat(document.getElementById('CRASH_FLASH_THRESHOLD').value)
            },
            "MOONSHOT_ALERTS": {
                "ENABLED": document.getElementById('MOONSHOT_ALERTS_SCHED_ENABLED').checked,
                "FREQUENCY": document.getElementById('MOONSHOT_ALERTS_FREQ').value,
                "START_TIME": document.getElementById('MOONSHOT_ALERTS_START').value,
                "END_TIME": document.getElementById('MOONSHOT_ALERTS_END').value,
                "INTERVAL_MINUTES": parseInt(document.getElementById('MOONSHOT_ALERTS_MINUTES').value) || 10,
                "SPIKE_PERCENT": parseFloat(document.getElementById('MOONSHOT_SPIKE_PERCENT').value),
                "SPIKE_DAYS": parseInt(document.getElementById('MOONSHOT_SPIKE_DAYS').value),
                "SMA_LENGTH": parseInt(document.getElementById('MOONSHOT_SMA_LENGTH').value),
                "SMA_GAP_PERCENT": parseFloat(document.getElementById('MOONSHOT_SMA_GAP_PERCENT').value)
            },
            "MAINTENANCE": {
                "ENABLED": document.getElementById('MAINTENANCE_ENABLED').checked,
                "DAY_OF_WEEK": document.getElementById('MAINTENANCE_DAY').value,
                "TIME": document.getElementById('MAINTENANCE_TIME').value,
                "DAYS_TO_KEEP_FILES": parseInt(document.getElementById('MAINTENANCE_DAYS_TO_KEEP_FILES').value) || 60
            },
            "ACCOUNT_VALUE_SNAPSHOT": {
                "ENABLED": document.getElementById('ACCOUNT_VALUE_SNAPSHOT_ENABLED').checked,
                "TIME": document.getElementById('ACCOUNT_VALUE_SNAPSHOT_TIME').value
            },
            "QUANT_ENGINE": {
                "DAYS": quantDays,
                "TIME": document.getElementById('QUANT_ENGINE_TIME').value
            },
            "EARNINGS_ENGINE": {
                "DAYS": earnDays,
                "TIME": document.getElementById('EARNINGS_ENGINE_TIME').value
            },
            "SYNC_INDICES": {
                "ENABLED": document.getElementById('SYNC_INDICES_ENABLED').checked,
                "INDICES": activeIndices,
                "DAYS": indexDays,
                "TIME": document.getElementById('SYNC_INDICES_TIME').value
            },
            "PROFILER_ENGINE": {
                "ENABLED": document.getElementById('PROFILER_ENGINE_ENABLED').checked,
                "DAYS": profilerDays,
                "TIME": document.getElementById('PROFILER_ENGINE_TIME').value,
                "BATCH_SIZE": parseInt(document.getElementById('PROFILER_BATCH_SIZE').value) || 250
            },
            "UNIVERSE_DEEP_SYNC": {
                "ENABLED": document.getElementById('UNIVERSE_DEEP_SYNC_ENABLED').checked,
                "DAYS": udsDays,
                "TIME": document.getElementById('UNIVERSE_DEEP_SYNC_TIME').value
            },
            "UNIVERSE_ENGINE": {
                "ENABLED": document.getElementById('UNIVERSE_ENGINE_ENABLED').checked,
                "DAYS": universeDays,
                "TIME": document.getElementById('UNIVERSE_ENGINE_TIME').value
            },
            "ML_BACKFILL": {
                "ENABLED": document.getElementById('ML_BACKFILL_ENABLED').checked,
                "DAYS": mlBackfillDays,
                "TIME": document.getElementById('ML_BACKFILL_TIME').value
            },
            "ML_TRAINING": {
                "ENABLED": document.getElementById('ML_TRAINING_ENABLED').checked,
                "DAYS": mlTrainingDays,
                "TIME": document.getElementById('ML_TRAINING_TIME').value
            },
            "ML_INFERENCE": {
                "ENABLED": document.getElementById('ML_INFERENCE_ENABLED').checked,
                "DAYS": mlInferenceDays,
                "TIME": document.getElementById('ML_INFERENCE_TIME').value
            },
            "MACRO_ENGINE": {
                "ENABLED": document.getElementById('MACRO_ENGINE_ENABLED').checked,
                "INITIALIZED": macroInitState,
                "CALENDAR_TIME": document.getElementById('MACRO_CALENDAR_TIME').value,
                "DATA_DAY": document.getElementById('MACRO_DATA_DAY').value,
                "DATA_TIME": document.getElementById('MACRO_DATA_TIME').value
            },
            "AI_CONTAGION": {
                "ENABLED": document.getElementById('AI_CONTAGION_SCHED_ENABLED').checked,
                "FREQUENCY": document.getElementById('AI_CONTAGION_FREQ').value,
                "START_TIME": document.getElementById('AI_CONTAGION_START').value,
                "END_TIME": document.getElementById('AI_CONTAGION_END').value,
                "INTERVAL_MINUTES": parseInt(document.getElementById('AI_CONTAGION_INTERVAL').value) || 15
            },
            "TRAP_MONITORS": {
                "ENABLED": document.getElementById('TRAP_MONITOR_ENABLED').checked,
                "BULL_TRAP": document.getElementById('TRAP_BULL_ENABLED').checked,
                "BEAR_TRAP": document.getElementById('TRAP_BEAR_ENABLED').checked,
                "CAPITULATION": document.getElementById('TRAP_CAP_ENABLED').checked,
                "WYCKOFF": document.getElementById('TRAP_WYK_ENABLED').checked,
                "MONITOR_PORTFOLIO": document.getElementById('TRAP_MONITOR_PORTFOLIO').checked,
                "MONITOR_WATCHLIST": document.getElementById('TRAP_MONITOR_WATCHLIST').checked,
                "FREQUENCY": document.getElementById('TRAP_MONITOR_FREQ').value,
                "START_TIME": document.getElementById('TRAP_MONITOR_START').value,
                "END_TIME": document.getElementById('TRAP_MONITOR_END').value,
                "INTERVAL_MINUTES": parseInt(document.getElementById('TRAP_MONITOR_INTERVAL').value) || 30
            },
            "BUBBLE_RADAR": {
                "ENABLED": document.getElementById('BUBBLE_RADAR_ENABLED').checked,
                "DAYS": document.getElementById('BUBBLE_RADAR_FREQ').value === 'mon-sun'
                    ? ['mon','tue','wed','thu','fri','sat','sun']
                    : document.getElementById('BUBBLE_RADAR_FREQ').value === 'weekly'
                    ? ['mon']
                    : ['mon','tue','wed','thu','fri'],
                "TIME": document.getElementById('BUBBLE_RADAR_TIME').value,
                "WATCH_THRESHOLD": parseInt(document.getElementById('BUBBLE_RADAR_WATCH_THRESHOLD').value) || 70,
                "FLAG_THRESHOLD": parseInt(document.getElementById('BUBBLE_RADAR_FLAG_THRESHOLD').value) || 85
            },
            "RISK_ORCHESTRATOR": {
                "ENABLED": document.getElementById('RISK_ORCHESTRATOR_ENABLED').checked,
                "DAYS": ['mon','tue','wed','thu','fri'],
                "TIME": document.getElementById('RISK_ORCHESTRATOR_TIME').value,
                "WEIGHTS": {
                    "VAR": (parseFloat(document.getElementById('RO_W_VAR').value) || 40) / 100,
                    "CORRELATION": (parseFloat(document.getElementById('RO_W_CORR').value) || 30) / 100,
                    "DRAWDOWN": (parseFloat(document.getElementById('RO_W_DD').value) || 30) / 100
                },
                "THRESHOLDS": {
                    "PHI_YELLOW": parseFloat(document.getElementById('RO_T_PHI_YELLOW').value) || 40,
                    "PHI_RED": parseFloat(document.getElementById('RO_T_PHI_RED').value) || 75,
                    "VAR_PCT_YELLOW": parseFloat(document.getElementById('RO_T_VAR_YELLOW').value) || 2.0,
                    "VAR_PCT_RED": parseFloat(document.getElementById('RO_T_VAR_RED').value) || 4.0,
                    "MAX_CORR_YELLOW": parseFloat(document.getElementById('RO_T_CORR_YELLOW').value) || 0.5,
                    "MAX_CORR_RED": parseFloat(document.getElementById('RO_T_CORR_RED').value) || 0.75,
                    "DRAWDOWN_PCT_YELLOW": parseFloat(document.getElementById('RO_T_DD_YELLOW').value) || 5.0,
                    "DRAWDOWN_PCT_RED": parseFloat(document.getElementById('RO_T_DD_RED').value) || 10.0
                }
            },
            "RISK_ORCHESTRATOR_DIGEST": {
                "ENABLED": document.getElementById('RISK_ORCHESTRATOR_DIGEST_ENABLED').checked,
                "DAYS": ['mon','tue','wed','thu','fri'],
                "TIME": document.getElementById('RISK_ORCHESTRATOR_DIGEST_TIME').value
            },
            "PAIRS_SPREAD_MONITOR": {
                "ENABLED": document.getElementById('PAIRS_SPREAD_ENABLED').checked,
                "DAYS": document.getElementById('PAIRS_SPREAD_FREQ').value === 'mon-sun'
                    ? ['mon','tue','wed','thu','fri','sat','sun']
                    : ['mon','tue','wed','thu','fri'],
                "TIME": document.getElementById('PAIRS_SPREAD_TIME').value,
                "CORRELATION_THRESHOLD": parseFloat(document.getElementById('PAIRS_SPREAD_CORRELATION_THRESHOLD').value) || 0.7,
                "ZSCORE_THRESHOLD": parseFloat(document.getElementById('PAIRS_SPREAD_ZSCORE_THRESHOLD').value) || 2.0
            },
            "PATTERN_DETECTION": {
                "ENABLED": document.getElementById('PATTERN_DETECTION_ENABLED').checked,
                "MONITOR_PORTFOLIO": document.getElementById('PATTERN_DETECTION_PORTFOLIO').checked,
                "MONITOR_WATCHLIST": document.getElementById('PATTERN_DETECTION_WATCHLIST').checked,
                "DAYS": document.getElementById('PATTERN_DETECTION_FREQ').value === 'mon-sun'
                    ? ['mon','tue','wed','thu','fri','sat','sun']
                    : ['mon','tue','wed','thu','fri'],
                "TIME": document.getElementById('PATTERN_DETECTION_TIME').value,
                "HEAD_SHOULDERS": {
                    "REGULAR_ENABLED": document.getElementById('PATTERN_DETECTION_HS_REGULAR').checked,
                    "INVERSE_ENABLED": document.getElementById('PATTERN_DETECTION_HS_INVERSE').checked
                },
                "DOUBLE_TOP_BOTTOM": {
                    "TOP_ENABLED": document.getElementById('PATTERN_DETECTION_DTB_TOP').checked,
                    "BOTTOM_ENABLED": document.getElementById('PATTERN_DETECTION_DTB_BOTTOM').checked
                },
                "FLAG": {
                    "BULL_ENABLED": document.getElementById('PATTERN_DETECTION_FLAG_BULL').checked,
                    "BEAR_ENABLED": document.getElementById('PATTERN_DETECTION_FLAG_BEAR').checked
                },
                "PENNANT": {
                    "BULL_ENABLED": document.getElementById('PATTERN_DETECTION_PENNANT_BULL').checked,
                    "BEAR_ENABLED": document.getElementById('PATTERN_DETECTION_PENNANT_BEAR').checked
                },
                "TRIANGLE": {
                    "ASCENDING_ENABLED": document.getElementById('PATTERN_DETECTION_TRI_ASC').checked,
                    "DESCENDING_ENABLED": document.getElementById('PATTERN_DETECTION_TRI_DESC').checked,
                    "BULLISH_ENABLED": document.getElementById('PATTERN_DETECTION_TRI_SYM_BULLISH').checked,
                    "BEARISH_ENABLED": document.getElementById('PATTERN_DETECTION_TRI_SYM_BEARISH').checked
                },
                "WEDGE": {
                    "RISING_ENABLED": document.getElementById('PATTERN_DETECTION_WEDGE_RISING').checked,
                    "FALLING_ENABLED": document.getElementById('PATTERN_DETECTION_WEDGE_FALLING').checked
                },
                "VOLATILITY_SQUEEZE": {
                    "BULLISH_ENABLED": document.getElementById('PATTERN_DETECTION_VS_BULLISH').checked,
                    "BEARISH_ENABLED": document.getElementById('PATTERN_DETECTION_VS_BEARISH').checked
                },
                "NARROW_RANGE": {
                    "NR4_ENABLED": document.getElementById('PATTERN_DETECTION_NR_NR4').checked,
                    "NR7_ENABLED": document.getElementById('PATTERN_DETECTION_NR_NR7').checked,
                    "BULLISH_ENABLED": document.getElementById('PATTERN_DETECTION_NR_BULLISH').checked,
                    "BEARISH_ENABLED": document.getElementById('PATTERN_DETECTION_NR_BEARISH').checked
                },
                "PARABOLIC_STRETCH": {
                    "OVERBOUGHT_ENABLED": document.getElementById('PATTERN_DETECTION_PS_OVERBOUGHT').checked,
                    "OVERSOLD_ENABLED": document.getElementById('PATTERN_DETECTION_PS_OVERSOLD').checked
                },
                "MOMENTUM_DIVERGENCE": {
                    "BULLISH_ENABLED": document.getElementById('PATTERN_DETECTION_MD_BULLISH').checked,
                    "BEARISH_ENABLED": document.getElementById('PATTERN_DETECTION_MD_BEARISH').checked
                },
                "CANDLESTICK_TRIGGER": {
                    "ENGULFING_ENABLED": document.getElementById('PATTERN_DETECTION_CT_ENGULFING').checked,
                    "PIN_BAR_ENABLED": document.getElementById('PATTERN_DETECTION_CT_PIN_BAR').checked,
                    "BULLISH_ENABLED": document.getElementById('PATTERN_DETECTION_CT_BULLISH').checked,
                    "BEARISH_ENABLED": document.getElementById('PATTERN_DETECTION_CT_BEARISH').checked
                }
            },
            "FORENSIC_QUARTERLY_FETCH": {
                "ENABLED": document.getElementById('FORENSIC_QUARTERLY_FETCH_ENABLED').checked,
                "DAY_OF_MONTH": 1,
                "TIME": "06:00"
            },
            "FORENSIC_SCORES": {
                "ENABLED": document.getElementById('FORENSIC_SCORES_ENABLED').checked,
                "DAY_OF_MONTH": 1,
                "TIME": "07:00"
            },
            "MACRO_AUCTIONS": {
                "ENABLED": document.getElementById('MACRO_AUCTIONS_ENABLED').checked,
                "AM_TIME": document.getElementById('MACRO_AUCTIONS_AM_TIME').value,
                "PM_TIME": document.getElementById('MACRO_AUCTIONS_PM_TIME').value
            },
            "BACKUP": {
                "ENABLED": document.getElementById('BACKUP_ENABLED').checked,
                "LOCATION": document.getElementById('BACKUP_LOCATION').value,
                "LOCAL_PATH": document.getElementById('BACKUP_LOCAL_PATH').value.trim() || 'backups',
                "NFS_SERVER": document.getElementById('BACKUP_NFS_SERVER').value.trim(),
                "NFS_PATH": document.getElementById('BACKUP_NFS_PATH').value.trim(),
                "INCLUDE_DATA": document.getElementById('BACKUP_INCLUDE_DATA').checked,
                "INCLUDE_MODELS": document.getElementById('BACKUP_INCLUDE_MODELS').checked,
                "INCLUDE_DATABASE": document.getElementById('BACKUP_INCLUDE_DATABASE').checked,
                "DAYS": backupDays,
                "TIME": document.getElementById('BACKUP_TIME').value,
                "RETENTION_COUNT": parseInt(document.getElementById('BACKUP_RETENTION_COUNT').value) || 7
            },
            "ALERT_REFEREE_TRAINING": {
                "ENABLED": document.getElementById('ALERT_REFEREE_ENABLED').checked,
                "DAYS": refereeDays,
                "TIME": document.getElementById('ALERT_REFEREE_TIME').value,
                "MODE": document.getElementById('ALERT_REFEREE_MODE').value,
                "VETO_THRESHOLD": parseFloat(document.getElementById('ALERT_REFEREE_VETO_THRESHOLD').value) || 0.3,
                "MIN_TRAINING_SAMPLES": parseInt(document.getElementById('ALERT_REFEREE_MIN_SAMPLES').value) || 200
            },
            "ALERT_REFEREE_TRAINING_CONFLUENCE": {
                "ENABLED": document.getElementById('CONFLUENCE_REFEREE_ENABLED').checked,
                "DAYS": confluenceRefereeDays,
                "TIME": document.getElementById('CONFLUENCE_REFEREE_TIME').value,
                "MODE": document.getElementById('CONFLUENCE_REFEREE_MODE').value,
                "VETO_THRESHOLD": parseFloat(document.getElementById('CONFLUENCE_REFEREE_VETO_THRESHOLD').value) || 0.3,
                "MIN_TRAINING_SAMPLES": parseInt(document.getElementById('CONFLUENCE_REFEREE_MIN_SAMPLES').value) || 200
            }
        },
        "NOTIFICATIONS": {
            "MARKET_SENTIMENT": {
                "ENABLED": document.getElementById('FNG_ENABLED').checked,
                "TIME": document.getElementById('FNG_TIME').value,
                "FREQUENCY": document.getElementById('FNG_FREQUENCY').value
            },
            "EARNINGS_ALERTS": {
                "ENABLED": document.getElementById('EARNINGS_ENABLED').checked,
                "TIME": document.getElementById('EARNINGS_TIME').value,
                "DAYS_AHEAD": parseInt(document.getElementById('EARNINGS_DAYS_AHEAD').value),
                "ALERT_TYPE": document.getElementById('EARNINGS_ALERT_TYPE').value
            },
            "INSIDER_TRADING": {
                "ENABLED_PORTFOLIO": document.getElementById('INSIDER_ENABLED_PORTFOLIO').checked,
                "ENABLED_WATCHLIST": document.getElementById('INSIDER_ENABLED_WATCHLIST').checked,
                "TIME": document.getElementById('INSIDER_TIME').value,
                "FREQUENCY": document.getElementById('INSIDER_FREQUENCY').value,
                "MIN_VALUE": parseInt(document.getElementById('INSIDER_MIN_VALUE').value),
                "DAYS_BACK": parseInt(document.getElementById('INSIDER_DAYS_BACK').value)
            },
            "CRASH_ALERTS": {
                "DROP_PERCENT": parseFloat(document.getElementById('CRASH_DROP_PERCENT').value),
                "DROP_DAYS": parseInt(document.getElementById('CRASH_DROP_DAYS').value),
                "SMA_LENGTH": parseInt(document.getElementById('CRASH_SMA_LENGTH').value),
                "SMA_GAP_PERCENT": parseFloat(document.getElementById('CRASH_SMA_GAP_PERCENT').value),
                "FLASH_CRASH_THRESHOLD": parseFloat(document.getElementById('CRASH_FLASH_THRESHOLD').value)
            },
            "MOONSHOT_ALERTS": {
                "SPIKE_PERCENT": parseFloat(document.getElementById('MOONSHOT_SPIKE_PERCENT').value),
                "SPIKE_DAYS": parseInt(document.getElementById('MOONSHOT_SPIKE_DAYS').value),
                "SMA_LENGTH": parseInt(document.getElementById('MOONSHOT_SMA_LENGTH').value),
                "SMA_GAP_PERCENT": parseFloat(document.getElementById('MOONSHOT_SMA_GAP_PERCENT').value)
            },
            "RSS_FEED": {
                "ENABLED": document.getElementById('RSS_FEED_ENABLED').checked
            },
            "AI_CONTAGION": {
                "ENABLED": document.getElementById('AI_CONTAGION_ENABLED').checked,
                "LEADER_THRESHOLD_PCT": parseFloat(document.getElementById('AI_CONTAGION_LEADER_THRESHOLD').value),
                "ETF_CONFIRMATION_THRESHOLD_PCT": parseFloat(document.getElementById('AI_CONTAGION_ETF_THRESHOLD').value),
                "VOLUME_SPIKE_MULTIPLIER": parseFloat(document.getElementById('AI_CONTAGION_VOLUME_MULT').value),
                "BELLWETHER_TICKERS": document.getElementById('AI_CONTAGION_BELLWETHERS').value
                    .split(/[,\s]+/).map(s => s.trim()).filter(Boolean),
                "ETF_BASKET": document.getElementById('AI_CONTAGION_ETFS').value
                    .split(/[,\s]+/).map(s => s.trim()).filter(Boolean),
                "MAX_ALERTS_PER_DAY": parseInt(document.getElementById('AI_CONTAGION_MAX_PER_DAY').value)
            },
            "TRAP_MONITOR_ALERTS": {
                "COOLDOWN_MINUTES": parseFloat(document.getElementById('TRAP_COOLDOWN').value),
                "RETRIGGER_PERCENT": parseFloat(document.getElementById('TRAP_RETRIGGER').value),
                "REARM_PERCENT": parseFloat(document.getElementById('TRAP_REARM').value),
                "PROXY_TICKERS": document.getElementById('TRAP_PROXY_TICKERS').value
                    .split(/[,\s]+/).map(s => s.trim().toUpperCase()).filter(Boolean)
            },
            "PAIRS_SPREAD_MONITOR_ALERTS": {
                "COOLDOWN_MINUTES": parseFloat(document.getElementById('PAIRS_SPREAD_COOLDOWN').value),
                "RETRIGGER_PERCENT": parseFloat(document.getElementById('PAIRS_SPREAD_RETRIGGER').value),
                "REARM_PERCENT": parseFloat(document.getElementById('PAIRS_SPREAD_REARM').value)
            },
            "RISK_ORCHESTRATOR_ALERTS": {
                "ENABLED": document.getElementById('RO_ALERTS_ENABLED').checked,
                "COOLDOWN_MINUTES": parseFloat(document.getElementById('RO_ALERTS_COOLDOWN').value) || 120,
                "RETRIGGER_PERCENT": parseFloat(document.getElementById('RO_ALERTS_RETRIGGER').value) || 5.0,
                "REARM_PERCENT": parseFloat(document.getElementById('RO_ALERTS_REARM').value) || 10.0
            },
            "PATTERN_DETECTION_ALERTS": {
                "COOLDOWN_MINUTES": parseFloat(document.getElementById('PATTERN_DETECTION_COOLDOWN').value),
                "RETRIGGER_PERCENT": parseFloat(document.getElementById('PATTERN_DETECTION_RETRIGGER').value),
                "REARM_PERCENT": parseFloat(document.getElementById('PATTERN_DETECTION_REARM').value),
                "HEAD_SHOULDERS": {
                    "PRIOR_TREND_MIN_PCT": parseFloat(document.getElementById('PATTERN_DETECTION_HS_PRIOR_TREND').value) || 8.0,
                    "VOLUME_CONFIRM_MULTIPLIER": parseFloat(document.getElementById('PATTERN_DETECTION_HS_VOLUME_MULT').value) || 1.5
                },
                "DOUBLE_TOP_BOTTOM": {
                    "PRIOR_TREND_MIN_PCT": parseFloat(document.getElementById('PATTERN_DETECTION_DTB_PRIOR_TREND').value) || 8.0,
                    "VOLUME_CONFIRM_MULTIPLIER": parseFloat(document.getElementById('PATTERN_DETECTION_DTB_VOLUME_MULT').value) || 1.5,
                    "BALANCE_TOLERANCE_PCT": parseFloat(document.getElementById('PATTERN_DETECTION_DTB_BALANCE_TOL').value) || 3.0,
                    "MIN_SEPARATION_PCT": parseFloat(document.getElementById('PATTERN_DETECTION_DTB_MIN_SEP').value) || 3.0
                },
                "FLAG": {
                    "SIGMA_MULTIPLIER": parseFloat(document.getElementById('PATTERN_DETECTION_FLAG_SIGMA_MULT').value) || 1.5,
                    "FLAGPOLE_LOOKBACK_DAYS": parseInt(document.getElementById('PATTERN_DETECTION_FLAG_POLE_DAYS').value) || 10,
                    "SIGMA_WINDOW_DAYS": parseInt(document.getElementById('PATTERN_DETECTION_FLAG_SIGMA_WINDOW').value) || 20,
                    "MIN_CONSOLIDATION_DAYS": parseInt(document.getElementById('PATTERN_DETECTION_FLAG_MIN_CONSOL').value) || 7,
                    "MAX_CONSOLIDATION_DAYS": parseInt(document.getElementById('PATTERN_DETECTION_FLAG_MAX_CONSOL').value) || 15,
                    "MAX_CHANNEL_SLOPE_PCT": parseFloat(document.getElementById('PATTERN_DETECTION_FLAG_MAX_SLOPE').value) || 0.75,
                    "PARALLEL_TOLERANCE_PCT": parseFloat(document.getElementById('PATTERN_DETECTION_FLAG_PARALLEL_TOL').value) || 0.15
                },
                "PENNANT": {
                    "SIGMA_MULTIPLIER": parseFloat(document.getElementById('PATTERN_DETECTION_PENNANT_SIGMA_MULT').value) || 1.5,
                    "FLAGPOLE_LOOKBACK_DAYS": parseInt(document.getElementById('PATTERN_DETECTION_PENNANT_POLE_DAYS').value) || 10,
                    "SIGMA_WINDOW_DAYS": parseInt(document.getElementById('PATTERN_DETECTION_PENNANT_SIGMA_WINDOW').value) || 20,
                    "MIN_SLOPE_PCT": parseFloat(document.getElementById('PATTERN_DETECTION_PENNANT_MIN_SLOPE').value) || 0.15,
                    "MIN_CONSOLIDATION_DAYS": parseInt(document.getElementById('PATTERN_DETECTION_PENNANT_MIN_CONSOL').value) || 5,
                    "MAX_CONSOLIDATION_DAYS": parseInt(document.getElementById('PATTERN_DETECTION_PENNANT_MAX_CONSOL').value) || 12
                },
                "TRIANGLE": {
                    "WINDOW_DAYS": parseInt(document.getElementById('PATTERN_DETECTION_TRI_WINDOW').value) || 40,
                    "FLAT_SLOPE_EPSILON_PCT": parseFloat(document.getElementById('PATTERN_DETECTION_TRI_FLAT_EPS').value) || 0.15,
                    "MIN_SLOPE_PCT": parseFloat(document.getElementById('PATTERN_DETECTION_TRI_MIN_SLOPE').value) || 0.15
                },
                "WEDGE": {
                    "WINDOW_DAYS": parseInt(document.getElementById('PATTERN_DETECTION_WEDGE_WINDOW').value) || 40,
                    "MIN_SLOPE_PCT": parseFloat(document.getElementById('PATTERN_DETECTION_WEDGE_MIN_SLOPE').value) || 0.15,
                    "MIN_CONVERGENCE_DIFF_PCT": parseFloat(document.getElementById('PATTERN_DETECTION_WEDGE_CONVERGENCE').value) || 0.1
                },
                "VOLATILITY_SQUEEZE": {
                    "WINDOW_DAYS": parseInt(document.getElementById('PATTERN_DETECTION_VS_WINDOW').value) || 20,
                    "NUM_STD": parseFloat(document.getElementById('PATTERN_DETECTION_VS_NUM_STD').value) || 2.0,
                    "KC_MULTIPLIER": parseFloat(document.getElementById('PATTERN_DETECTION_VS_KC_MULT').value) || 1.5,
                    "MIN_SQUEEZE_DAYS": parseInt(document.getElementById('PATTERN_DETECTION_VS_MIN_SQUEEZE_DAYS').value) || 6,
                    "BREAKOUT_LOOKAHEAD_DAYS": parseInt(document.getElementById('PATTERN_DETECTION_VS_BREAKOUT_LOOKAHEAD').value) || 5
                },
                "NARROW_RANGE": {
                    "BREAKOUT_LOOKAHEAD_DAYS": parseInt(document.getElementById('PATTERN_DETECTION_NR_BREAKOUT_LOOKAHEAD').value) || 5,
                    "VOLUME_CONFIRM_MULTIPLIER": parseFloat(document.getElementById('PATTERN_DETECTION_NR_VOLUME_MULT').value) || 1.5
                },
                "PARABOLIC_STRETCH": {
                    "SMA_WINDOW": parseInt(document.getElementById('PATTERN_DETECTION_PS_SMA_WINDOW').value) || 200,
                    "Z_WINDOW_DAYS": parseInt(document.getElementById('PATTERN_DETECTION_PS_Z_WINDOW').value) || 252,
                    "Z_THRESHOLD": parseFloat(document.getElementById('PATTERN_DETECTION_PS_Z_THRESHOLD').value) || 3.0,
                    "CONFIRM_Z_THRESHOLD": parseFloat(document.getElementById('PATTERN_DETECTION_PS_CONFIRM_Z').value) || 2.0,
                    "BREAKOUT_LOOKAHEAD_DAYS": parseInt(document.getElementById('PATTERN_DETECTION_PS_BREAKOUT_LOOKAHEAD').value) || 10,
                    "VOLUME_CONFIRM_MULTIPLIER": parseFloat(document.getElementById('PATTERN_DETECTION_PS_VOLUME_MULT').value) || 1.5
                },
                "MOMENTUM_DIVERGENCE": {
                    "MIN_PRICE_CHANGE_PCT": parseFloat(document.getElementById('PATTERN_DETECTION_MD_MIN_PRICE').value) || 1.0,
                    "MIN_RSI_GAP": parseFloat(document.getElementById('PATTERN_DETECTION_MD_MIN_RSI_GAP').value) || 3.0,
                    "VOLUME_CONFIRM_MULTIPLIER": parseFloat(document.getElementById('PATTERN_DETECTION_MD_VOLUME_MULT').value) || 1.5
                },
                "CANDLESTICK_TRIGGER": {
                    "RSI_OVERSOLD": parseFloat(document.getElementById('PATTERN_DETECTION_CT_RSI_OVERSOLD').value) || 30.0,
                    "RSI_OVERBOUGHT": parseFloat(document.getElementById('PATTERN_DETECTION_CT_RSI_OVERBOUGHT').value) || 70.0,
                    "BB_WINDOW_DAYS": parseInt(document.getElementById('PATTERN_DETECTION_CT_BB_WINDOW').value) || 20,
                    "BB_NUM_STD": parseFloat(document.getElementById('PATTERN_DETECTION_CT_BB_NUM_STD').value) || 2.0,
                    "WICK_MULTIPLIER": parseFloat(document.getElementById('PATTERN_DETECTION_CT_WICK_MULT').value) || 2.0,
                    "OPPOSITE_WICK_MAX_PCT": parseFloat(document.getElementById('PATTERN_DETECTION_CT_OPP_WICK_MAX').value) || 0.2,
                    "VOLUME_CONFIRM_MULTIPLIER": parseFloat(document.getElementById('PATTERN_DETECTION_CT_VOLUME_MULT').value) || 1.5
                }
            }
        },
        "NOTIFICATION_ROUTING": (function() {
            const routing = {};
            document.querySelectorAll('[data-notif-source]').forEach(cb => {
                const src = cb.dataset.notifSource;
                (routing[src] = routing[src] || {})[cb.dataset.notifChannel] = cb.checked;
            });
            return routing;
        })(),
        "XRAY_TARGETS": (function() {
            function xt(id) {
                const v = (document.getElementById(id)?.value ?? '').trim();
                return v === '' ? null : parseFloat(v);
            }
            return {
                "market_development": {
                    "Developed Markets": { "min": xt('XT_DEV_MIN'), "max": xt('XT_DEV_MAX') },
                    "Emerging Markets":  { "min": xt('XT_EM_MIN'),  "max": xt('XT_EM_MAX') }
                },
                "regional_clusters": {
                    "North America":    { "min": xt('XT_NA_MIN'),    "max": xt('XT_NA_MAX') },
                    "Europe":           { "min": xt('XT_EU_MIN'),    "max": xt('XT_EU_MAX') },
                    "Japan":            { "min": xt('XT_JP_MIN'),    "max": xt('XT_JP_MAX') },
                    "Asia-Pacific":     { "min": xt('XT_AP_MIN'),    "max": xt('XT_AP_MAX') },
                    "Emerging Markets": { "min": xt('XT_RC_EM_MIN'), "max": xt('XT_RC_EM_MAX') }
                },
                "country_concentration": {
                    "United States":  { "min": null, "max": xt('XT_CC_US') },
                    "China":          { "min": null, "max": xt('XT_CC_CN') },
                    "Japan":          { "min": null, "max": xt('XT_CC_JP') },
                    "United Kingdom": { "min": null, "max": xt('XT_CC_GB') }
                },
                "sector_targets": {
                    "Technology":             { "min": null, "max": xt('XT_SEC_TECH') },
                    "Financials":             { "min": null, "max": xt('XT_SEC_FIN')  },
                    "Healthcare":             { "min": null, "max": xt('XT_SEC_HLTH') },
                    "Consumer Cyclical":      { "min": null, "max": xt('XT_SEC_CC')   },
                    "Industrials":            { "min": null, "max": xt('XT_SEC_IND')  },
                    "Communication Services": { "min": null, "max": xt('XT_SEC_COMM') },
                    "Consumer Staples":       { "min": null, "max": xt('XT_SEC_CS')   },
                    "Energy":                 { "min": null, "max": xt('XT_SEC_ENE')  },
                    "Materials":              { "min": null, "max": xt('XT_SEC_MAT')  },
                    "Utilities":              { "min": null, "max": xt('XT_SEC_UTIL') },
                    "Real Estate":            { "min": null, "max": xt('XT_SEC_RE')   }
                },
                "asset_class_targets": {
                    "ETF":          { "min": xt('XT_AC_ETF_MIN'), "max": xt('XT_AC_ETF_MAX') },
                    "Equity":       { "min": xt('XT_AC_EQ_MIN'),  "max": xt('XT_AC_EQ_MAX')  },
                    "Fixed Income": { "min": xt('XT_AC_FI_MIN'),  "max": xt('XT_AC_FI_MAX')  },
                    "Commodity":    { "min": xt('XT_AC_COM_MIN'), "max": xt('XT_AC_COM_MAX') }
                },
                "concentration_targets": {
                    "max_single_position_pct": xt('XT_CONC_MAX_POS'),
                    "top5_weight_max_pct":     xt('XT_CONC_TOP5'),
                    "top10_weight_max_pct":    xt('XT_CONC_TOP10'),
                    "hhi_max":                 xt('XT_CONC_HHI')
                },
                "risk_metric_targets": {
                    "portfolio_beta_min":      xt('XT_RISK_BETA_MIN'),
                    "portfolio_beta_max":      xt('XT_RISK_BETA_MAX'),
                    "annualized_vol_max_pct":  xt('XT_RISK_VOL_MAX'),
                    "sharpe_ratio_min":        xt('XT_RISK_SHARPE_MIN'),
                    "max_drawdown_max_pct":    xt('XT_RISK_DD_MAX'),
                    "avg_correlation_max":     xt('XT_RISK_CORR_MAX')
                },
                "income_targets": {
                    "dividend_yield_min_pct": xt('XT_INC_DIV_MIN')
                }
            };
        })(),
        "META_SCORING": {
            "REGIME_WEIGHTS": {
                "Bull": {
                    "composite_score": (parseFloat(document.getElementById('MS_BULL_W_COMPOSITE').value) || 40) / 100,
                    "ml_confidence": (parseFloat(document.getElementById('MS_BULL_W_ML').value) || 30) / 100,
                    "pattern": (parseFloat(document.getElementById('MS_BULL_W_PATTERN').value) || 20) / 100,
                    "trap": (parseFloat(document.getElementById('MS_BULL_W_TRAP').value) || 10) / 100
                },
                "Chop": {
                    "composite_score": (parseFloat(document.getElementById('MS_CHOP_W_COMPOSITE').value) || 25) / 100,
                    "ml_confidence": (parseFloat(document.getElementById('MS_CHOP_W_ML').value) || 25) / 100,
                    "pattern": (parseFloat(document.getElementById('MS_CHOP_W_PATTERN').value) || 35) / 100,
                    "trap": (parseFloat(document.getElementById('MS_CHOP_W_TRAP').value) || 15) / 100
                }
            },
            "CRASH_VETO": {
                "MARKET_STRESS_THRESHOLD": parseFloat(document.getElementById('MS_CRASH_STRESS_THRESHOLD').value) || 0.75
            }
        }
    };

    try {
        const [response, smtpResponse] = await Promise.all([
            fetch('/api/settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Confirm-Token': CONFIRM_TOKEN },
                body: JSON.stringify(payload)
            }),
            fetch('/api/save-smtp-settings', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Confirm-Token': CONFIRM_TOKEN },
                body: JSON.stringify({
                    smtp_host: document.getElementById('SMTP_HOST').value,
                    smtp_port: document.getElementById('SMTP_PORT').value,
                    smtp_user: document.getElementById('SMTP_USER').value,
                    smtp_pass: document.getElementById('SMTP_PASS').value,
                    smtp_from: document.getElementById('SMTP_FROM').value,
                })
            })
        ]);

        const result = await response.json();

        if (!silent) {
            if (response.ok && smtpResponse.ok) {
                setStatus('status-msg', 'success', "Settings saved. Background schedulers restarted dynamically.");
            } else if (!response.ok) {
                const errMsg = result.message || (result.detail ? (Array.isArray(result.detail) ? result.detail.map(d => d.loc.join('.') + ': ' + d.msg).join('; ') : result.detail) : 'Unknown error');
                setStatus('status-msg', 'error', errMsg);
            } else {
                const smtpResult = await smtpResponse.json();
                setStatus('status-msg', 'error', 'Mail settings: ' + (smtpResult.detail || 'Failed to save.'));
            }
        }
    } catch (error) {
        if (!silent) {
            setStatus('status-msg', 'error', "Network Error while saving.");
        }
    }

    if (!silent) {
        setTimeout(() => {
            btn.disabled = false;
            btn.innerText = "💾 Save & Apply System Settings";
            document.getElementById('status-msg').innerText = "";
        }, 5000);
    }
}

(function() {
    const searchInput = document.getElementById('settingsSearch');
    const clearBtn = document.getElementById('settingsSearchClear');
    const cards = document.querySelectorAll('details.settings-card');
    const noResults = document.getElementById('noSettingsResults');

    searchInput.addEventListener('input', function() {
        const query = this.value.toLowerCase().trim();
        let found = 0;

        if (clearBtn) clearBtn.style.display = this.value ? 'block' : 'none';

        cards.forEach(card => {
            const matches = query === '' || card.innerText.toLowerCase().includes(query);
            card.style.display = matches ? '' : 'none';
            if (matches) {
                found++;
                if (query !== '' && !card.hasAttribute('ontoggle')) {
                    card.setAttribute('open', '');
                }
            } else if (query !== '') {
                card.removeAttribute('open');
            }
        });

        noResults.style.display = (found === 0 && query !== '') ? 'block' : 'none';
    });

    if (clearBtn) {
        clearBtn.addEventListener('click', function() {
            searchInput.value = '';
            searchInput.dispatchEvent(new Event('input'));
            searchInput.focus();
        });
    }
})();
