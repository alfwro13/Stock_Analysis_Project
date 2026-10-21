# constants.py — single source of truth for every threshold shared across modules.

# ── ML prediction ─────────────────────────────────────────────────────────────
PREDICTION_HORIZON_DAYS     = 10    # trading days from T+1 entry to T+10 exit
PREDICTION_RETURN_THRESHOLD = 0.03  # 3% return required for a positive label

# ── Earnings Volatility drift prediction ───────────────────────────────────────
EARNINGS_DRIFT_HORIZONS = (1, 5, 20)  # trading days after earnings for signed drift stats + predictions

# ── Market regime classification ───────────────────────────────────────────────
REGIME_CRASH_VOL    = 35.0  # annualised vol % at or above which regime = Crash
REGIME_VOLATILE_VOL = 20.0  # annualised vol % at or above which regime = Volatile

# ── RSI bands ─────────────────────────────────────────────────────────────────
RSI_HEALTHY_MIN         = 40.0  # scoring model: RSI "room to run" lower bound
RSI_HEALTHY_MAX         = 65.0  # scoring model: RSI "room to run" upper bound

# ── Composite score → signal label boundaries ─────────────────────────────────
SCORE_STRONG_BUY  =  40
SCORE_BULLISH     =  20
SCORE_NEUTRAL     =   0
SCORE_BEARISH     = -30
SCORE_STRONG_SELL = -60

# ── Data freshness thresholds (UI staleness badge) ────────────────────────────
FRESHNESS_MODEL_WARN_DAYS   = 7   # model file older than this → amber
FRESHNESS_MODEL_STALE_DAYS  = 14  # model file older than this → red
FRESHNESS_PRICES_WARN_DAYS  = 3   # price data older than this → amber (covers normal weekends)
FRESHNESS_PRICES_STALE_DAYS = 5   # price data older than this → red

# ── Macro AI engine ────────────────────────────────────────────────────────────
# Training data minimums
MACRO_HMM_MIN_TRAIN_ROWS   = 50    # macro_indicators rows required to fit HMM
MACRO_CAL_MIN_TRAIN_ROWS   = 10    # macro_calendar rows required for RF and XGBoost

# HMM architecture
MACRO_HMM_N_STATES         = 3     # hidden states: 0=expansion, 1=choppy, 2=recession
MACRO_HMM_N_ITER           = 100   # EM iterations for GaussianHMM.fit()
MACRO_HMM_TRAIN_FRAC       = 0.8   # fraction of rows used to fit HMM; remainder scored for log-likelihood

# Random Forest hyperparameters (consensus miss probability)
MACRO_RF_N_ESTIMATORS      = 100
MACRO_RF_MAX_DEPTH         = 4

# XGBoost hyperparameters (volatility magnitude)
MACRO_XGB_N_ESTIMATORS     = 100
MACRO_XGB_MAX_DEPTH        = 4
MACRO_XGB_LEARNING_RATE    = 0.05

# Cross-validation
MACRO_CV_N_SPLITS          = 3     # TimeSeriesSplit folds for supervised CV

# Inference thresholds
MACRO_VIX_DEFAULT          = 20.0  # VIX fallback when market_regimes has no data
MACRO_SEVERE_VOL_THRESHOLD = 2.0   # predicted SPY gap % that triggers a warning log

# ── Market Stress Isolation Forest ───────────────────────────────────────────
IF_STRESS_N_ESTIMATORS    = 200    # trees in the market-wide IsolationForest
IF_STRESS_CONTAMINATION   = 0.05   # expected anomalous fraction of training data
IF_STRESS_MIN_ROWS        = 100    # minimum aligned rows required to fit/score
IF_STRESS_VOL_WINDOW      = 20     # rolling window for VIX MA and SPY volume z-score
IF_STRESS_ALERT_THRESHOLD = 0.75   # score in [0,1] above which the alert check runs
IF_STRESS_ALERT_DAYS      = 2      # consecutive days above threshold before firing

PENSION_BENCHMARK_WATCHLIST_TICKERS = ("URTH", "VWRL.L")
PENSION_BENCHMARK_WATCHLIST_NOTE = (
    "Used for the Pension benchmark chart. Keep this ticker on the Watchlist so its price history updates nightly."
)

SCHEDULER_RUN_HISTORY_LIMIT = 20  # runs kept per job in scheduler_run_history; avg_duration_sec is the mean of the successful ones

DEPENDENCY_INSTALL_TIMEOUT_SECONDS = 1800  # a torch upgrade pulls several GB of CUDA wheels; the old 300 s limit killed it mid-download
DEPENDENCY_INSTALL_RESUME_RETRIES = 10     # pip --resume-retries: re-attempts for an interrupted wheel download

PORTFOLIO_TOTALS_CACHE_MAX_AGE_SECONDS = 600  # GET /api/accounts/portfolio-totals reuses its answer this long at most, even when no input changed

# ── Request input limits (SEC 23/27) ──────────────────────────────────────────
CSV_IMPORT_MAX_BYTES        = 2 * 1024 * 1024  # transactions CSV upload
CSV_IMPORT_MAX_ROWS         = 5000             # data rows in one transactions CSV
CSV_IMPORT_MAX_TICKERS      = 200              # unique tickers in one CSV: each unknown one costs up to 2 live Yahoo lookups
PRICE_CSV_MAX_CHARS         = 500_000          # price-history CSV body
PRICE_CSV_MAX_ROWS          = 10_000
ACCOUNT_NAME_MAX_CHARS      = 100              # AccountBody.name
MCP_KEY_NAME_MAX_CHARS    = 60               # name of an MCP Server key (McpKeyBody.name)
MCP_KEYS_MAX                = 20               # MCP Server keys that can exist at once
MCP_KEY_LAST_USED_WRITE_SECONDS = 60           # an MCP key's last-used time is written at most this often
INVESTMENT_POLICY_MAX_CHARS = 50_000           # data/investment_policy.md served by the MCP Server; a longer file is refused, not cut
MCP_ACCOUNT_SCOPE_MAX_CHARS = 64               # `account` argument of the MCP Server tools ("all", "acct:N" or a Ghostfolio UUID)
MCP_TICKER_MAX_CHARS        = 64               # `ticker` argument of the MCP Server tools (same bound as utils.safe_ticker_filename)
MCP_ALERTS_MAX_DAYS         = 30               # `days` of get_alerts: system_notifications is pruned after 30 days, so a longer window adds nothing
MCP_ALERTS_MAX_ROWS         = 50               # rows returned per source by get_alerts
MCP_ALERT_TEXT_MAX_CHARS    = 500              # alert message text returned by get_alerts
MCP_NEWS_MAX_ROWS           = 50               # `limit` of get_news
MCP_NEWS_HEADLINE_MAX_CHARS = 200              # headline returned by get_news (external content)
MCP_NEWS_SUMMARY_MAX_CHARS  = 400              # summary returned by get_news (external content)
MCP_PRETRADE_AMOUNT_MAX     = 1_000_000_000_000  # `amount` of pretrade_check, in BASE_CURRENCY; a bound so a typo cannot reach the simulation
MCP_CALENDAR_MAX_DAYS       = 30               # `days` of get_calendar: earnings and ex-dividend dates a month ahead; the macro feed itself only lists the current week
MCP_CALENDAR_MAX_ROWS       = 50               # rows returned per list by get_calendar
MCP_REQUEST_MAX_BYTES       = 64 * 1024        # body of one POST /mcp; larger is 413 (the audit log reads the JSON-RPC method and name from it)
MCP_RATE_CALLS_PER_MINUTE   = 60               # calls per MCP key per rolling minute; over it is 429 + Retry-After
MCP_RATE_CONCURRENT_CALLS   = 8                # calls of one MCP key in flight at once; over it is 429 + Retry-After
MCP_AUTH_FAILURES_MAX       = 5                # wrong credentials from one address inside MCP_AUTH_FAILURE_WINDOW_SECONDS lock that address out
MCP_AUTH_FAILURE_WINDOW_SECONDS = 60
MCP_AUTH_LOCKOUT_SECONDS    = 300              # every /mcp request from a locked-out address is 429, even with a valid key; the dashboard login is not affected
MCP_THROTTLE_MAX_ENTRIES    = 10_000           # addresses/keys the in-memory /mcp throttles track at once; the oldest is dropped past it
MCP_REFUSAL_LOG_SECONDS     = 60               # a 429 is written to the audit log at most this often per address or key
MCP_NOTIFY_COOLDOWN_SECONDS = 3600             # a lockout or off-range notification repeats for one address at most this often
MCP_AUDIT_RETENTION_DAYS    = 90               # mcp_audit_log rows older than this are pruned by the maintenance job
MCP_AUDIT_QUEUE_MAX         = 1000             # audit rows waiting for the writer thread; past it a row is dropped, never the request
MCP_AUDIT_UNAUTH_ROWS_PER_MINUTE = 600         # audit rows without a valid key or token (rejections, OAuth browser steps) stored per minute; the rest are counted into one summary row
MCP_AUDIT_TEXT_MAX_CHARS    = 100            # request-derived text (JSON-RPC method, tool/prompt/resource name) stored in mcp_audit_log
MCP_AUDIT_ADDRESS_MAX_CHARS = 64               # client address stored in mcp_audit_log
MCP_PUBLIC_HOST_MAX_CHARS   = 253              # MCP_PUBLIC_HOST in the Settings save schema (longest DNS name)
MCP_REMOTE_CIDRS_MAX        = 16               # networks in MCP_REMOTE_ALLOWED_CIDRS
MCP_OAUTH_ACCESS_TOKEN_SECONDS = 15 * 60       # life of an OAuth access token
MCP_OAUTH_REFRESH_TOKEN_DAYS = 30              # idle life of a refresh token; each rotation starts a new one
MCP_OAUTH_GRANT_DAYS        = 90               # absolute life of a grant; after it the operator approves again
MCP_OAUTH_CODE_SECONDS      = 60               # life of an authorization code, counted from the moment the bound browser claims it
MCP_OAUTH_REQUEST_SECONDS   = 300              # life of a pending connection request
MCP_OAUTH_PAIRING_MINUTES   = 10               # default length of a pairing window
MCP_OAUTH_PENDING_MAX       = 5                # unexpired pending connection requests at once
MCP_OAUTH_REQUESTS_PER_HOUR = 10               # connection requests created per rolling hour
MCP_OAUTH_FORM_MAX_BYTES    = 8 * 1024         # body of POST /oauth/token and /oauth/revoke
MCP_OAUTH_PARAM_MAX_CHARS   = 2048             # any single OAuth request parameter
MCP_OAUTH_STATE_MAX_CHARS   = 512              # the client's `state`
MCP_OAUTH_USER_AGENT_MAX_CHARS = 200           # requester user agent stored with a pending request
MCP_OAUTH_CACHE_MAX_ENTRIES = 1000             # access-token hashes the in-memory verification cache holds
MCP_OAUTH_PRUNE_AFTER_DAYS  = 1                # expired tokens and finished requests older than this are deleted when a new one is written
MCP_OAUTH_REFRESH_RETRY_GRACE_SECONDS = 10    # a rotated refresh token presented again this soon after its rotation is a retry, refused without revoking the grant
MCP_OAUTH_LAST_USED_WRITE_SECONDS = 60         # a grant's last-used time and address are written at most this often
MCP_OAUTH_CONNECTED_APPS_MAX = 50              # grants listed in the Settings Connected Apps table
ETF_CONSTITUENTS_MAX        = 50
OPTION_LEGS_MAX             = 20
TABLE_VIEWS_MAX             = 20               # saved column-preset Views per page
TABLE_COLUMN_KEYS_MAX       = 200              # keys in any one column list (registry holds ~100 per page)
TABLE_COLUMN_KEY_MAX_CHARS  = 64
TABLE_VIEW_NAME_MAX_CHARS   = 100
TABLE_FILTER_CONDITIONS_MAX = 50
TABLE_FILTER_VALUE_MAX_CHARS = 200

# ── Static asset versioning ───────────────────────────────────────────────────
CSS_VERSION = "5.198"  # bump this whenever styles.css (or any versioned static/js/*.js file) changes to bust browser caches

# ── FinBERT / NLP sentiment ────────────────────────────────────────────────────
NLP_FINBERT_MAX_TOKENS     = 512   # HuggingFace token limit for ProsusAI/finbert
NLP_FINBERT_REVISION       = "0bc2ac10dda6929bcb1f77ccc43c7ec5c424f031"  # ProsusAI/finbert main; pinned so a Hub-side change never swaps the weights (SEC 22/27)
NLP_TEXT_TRUNCATE_CHARS    = 5000  # perf guard only — tokeniser truncation=True handles semantic cut
NLP_NEWS_FETCH_LIMIT       = 15    # headlines scored per ticker in the routine scan
NLP_CB_NEWS_FETCH_LIMIT    = 20    # headlines scored for central bank NLP alerts
NLP_CB_TONE_THRESHOLD      = 0.15  # |score| above which tone is hawkish/dovish vs neutral
NLP_NEWS_MAX_AGE_DAYS      = 3     # default age limit for a headline to count; SENTIMENT_ENGINE.MAX_NEWS_AGE_DAYS overrides
NLP_SENTIMENT_MIN_COVERAGE = 0.5   # a scan scoring a smaller share of its tickers raises the degraded-scan alert

# ── Sentiment chart rendering ─────────────────────────────────────────────────
SENTIMENT_CHART_FIGSIZE    = (12, 6)  # matplotlib figure size for Nextcloud alert chart
SENTIMENT_CHART_DPI        = 300      # export resolution for Nextcloud alert chart
