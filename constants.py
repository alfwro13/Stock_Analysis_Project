# constants.py — single source of truth for every threshold shared across modules.

# ── ML prediction ─────────────────────────────────────────────────────────────
PREDICTION_HORIZON_DAYS     = 10    # trading days from T+1 entry to T+10 exit
PREDICTION_RETURN_THRESHOLD = 0.03  # 3% return required for a positive label

# ── Earnings Volatility drift prediction ───────────────────────────────────────
EARNINGS_DRIFT_HORIZONS = (1, 5, 20)  # trading days after earnings for signed drift stats + predictions

DAILY_BAR_COLUMNS = ("Open", "High", "Low", "Close", "Volume")  # what prepare_daily_history() leaves in every ticker's data/historical file
QUANT_SCAN_ALWAYS_TICKERS = ("SPY",)  # the ML benchmark and regime proxy: its file is read by features although it is no universe listing

# ── Market regime classification ───────────────────────────────────────────────
REGIME_CRASH_VOL    = 35.0  # annualised vol % at or above which regime = Crash
REGIME_VOLATILE_VOL = 20.0  # annualised vol % at or above which regime = Volatile
HMM_CACHE_MIN_ROWS = 252  # SPY_hmm.parquet with fewer bars is rebuilt from a full fetch
HMM_CACHE_MAX_GAP_DAYS = 10  # calendar days between consecutive bars; the longest real closure is about 5
HMM_CACHE_MAX_ABS_LOG_RETURN = 0.25  # a larger one-day SPY log return is a bad bar (the worst S&P 500 day on record, 19 Oct 1987, was about -0.23)
HMM_MIN_STATE_FRACTION = 0.05  # a Price-Action HMM state covering less of the path is a degenerate fit and is not stored

# ── RSI bands ─────────────────────────────────────────────────────────────────
RSI_HEALTHY_MIN         = 40.0  # scoring model: RSI "room to run" lower bound
RSI_HEALTHY_MAX         = 65.0  # scoring model: RSI "room to run" upper bound

# ── Composite score → signal label boundaries ─────────────────────────────────
SCORE_STRONG_BUY  =  40
SCORE_BULLISH     =  20
SCORE_NEUTRAL     =   0
SCORE_BEARISH     = -30
SCORE_STRONG_SELL = -60
COMPOSITE_SCORE_MAX = 100  # stock_signals.composite_score is clamped to -COMPOSITE_SCORE_MAX..+COMPOSITE_SCORE_MAX

# ── Data freshness thresholds (UI staleness badge) ────────────────────────────
FRESHNESS_MODEL_WARN_DAYS   = 7   # model file older than this → amber
FRESHNESS_MODEL_STALE_DAYS  = 14  # model file older than this → red
FRESHNESS_PRICES_WARN_DAYS  = 3   # trading days (NYSE or home exchange open) since the latest bar → amber; weekends and holidays don't count
FRESHNESS_PRICES_STALE_DAYS = 5   # trading days since the latest bar → red

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

CACHE_REFRESH_AWAIT_TIMEOUT_SECONDS = 30  # longest a request or job thread waits on an awaited cache refresh; an unbounded wait on a stuck Yahoo call exhausts the web worker pool (rule 10)

UNIVERSE_HISTORY_MIN_BATCH_SECONDS = 25  # a 250-ticker universe-history batch takes at least this long (about 10 tickers/s); unthrottled, yfinance's thread pool ran at about 23/s and Yahoo answered 429 about 150 s into the step
UNIVERSE_HISTORY_SETTLE_SECONDS = 60     # pause after the 429 cooldown before the next universe-history batch; the limit outlasted a bare 60 s cooldown
UNIVERSE_HISTORY_MIN_WRITTEN = 0.99      # a universe-history refresh that writes a smaller share of its pool raises the degraded-refresh alert; a healthy night writes all but a handful of dead symbols
UNIVERSE_HISTORY_EMPTY_NIGHTS_TO_BLACKLIST = 5  # consecutive counted nights a universe-history ticker may come back without a usable bar before it is blacklisted and purged; a night with a 429 trip or a degraded write neither counts nor resets
QUOTE_CHANGE_TOLERANCE_PCT = 0.5  # a Yahoo regularMarketChange within this many percent of regularMarketPreviousClose (of its size) from the negative of it is the glitch that sent -19.92 for a 19.92 previous close; any other disagreement is left to Yahoo's fields, because regularMarketPreviousClose is sometimes a session stale (^AXJO, ^HSI, DX-Y.NYB)

QUOTE_CLOSE_GRACE_SECONDS = 900  # a cached quote taken this close before its exchange's close still counts as that session's last one: the intraday scan runs every 10 minutes and stops at the close, so its final quote is up to 10 minutes short of it

PORTFOLIO_TOTALS_CACHE_MAX_AGE_SECONDS = 600 # GET /api/accounts/portfolio-totals treats its answer as current this long, even when no input changed
PORTFOLIO_TOTALS_STALE_MAX_AGE_SECONDS = 1800  # a price, FX or snapshot change, or an expired answer, is served while a background recompute runs, but never older than this (LR-10)

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
MCP_WEEK_CHANGE_SESSIONS     = 5                # trading sessions behind get_holdings' week_change_pct
MCP_NEWS_MAX_ROWS           = 50               # `limit` of get_news
MCP_NEWS_HEADLINE_MAX_CHARS = 200              # headline returned by get_news (external content)
MCP_NEWS_SUMMARY_MAX_CHARS  = 400              # summary returned by get_news (external content)
MCP_ARTICLE_TEXT_MAX_CHARS  = 8000             # article text returned by get_article (external content), about 1,300 words: enough for the lead and main argument
GFETCHER_MIN_DELAY_SECONDS  = 3.0              # Google News Fetcher: shortest gap between Google requests; the gap is drawn from [delay, 2 x delay]
GFETCHER_MAX_DELAY_SECONDS  = 60.0             # largest delay Settings accepts for the Google News Fetcher
GFETCHER_MAX_TICKERS_PER_RUN = 50              # tickers one Google News Fetcher run may fetch
GFETCHER_MAX_REQUESTS_PER_DAY = 1000           # rolling 24-hour Google request budget Settings accepts
GFETCHER_MAX_PER_TICKER     = 30               # articles kept per ticker and run (Google lists 10-22)
GFETCHER_DOMAIN_MAX_CHARS   = 253              # publisher domain of a Google News Fetcher rule (the DNS limit)
MCP_PRETRADE_AMOUNT_MAX     = 1_000_000_000_000  # `amount` of pretrade_check, in BASE_CURRENCY; a bound so a typo cannot reach the simulation
MCP_CALENDAR_MAX_DAYS       = 30               # `days` of get_calendar: earnings and ex-dividend dates a month ahead; the macro feed itself only lists the current week
MCP_CALENDAR_MAX_ROWS       = 50               # rows returned per list by get_calendar
MACRO_ACTUALS_LOOKBACK_DAYS = 2                # get_calendar lists macro events this many days back so a release and its actual stay visible
MACRO_ACTUALS_BACKFILL_DAYS = 365              # the daily Macro Calendar run fills every released event of this many days back that still has no actual; the BLS, ONS, FRED and Bank of England series cover the span
MCP_WATCHLIST_DEFAULT_ROWS  = 25               # tickers get_watchlist returns when `limit` is left out (a row is about 1.3k characters, and a client cuts a reply off near 100k)
MCP_WATCHLIST_MAX_ROWS      = 50               # `limit` of get_watchlist; a larger value is refused, not clamped
MCP_WATCHLIST_MAX_OFFSET    = 10_000           # `offset` of get_watchlist; a larger value is refused
MCP_DISCOVERY_DEFAULT_ROWS  = 10               # rows get_market_report and screen_universe return when `limit` is left out
MCP_DISCOVERY_MAX_ROWS      = 25               # `limit` of get_market_report and screen_universe; a larger value is refused, not clamped
MCP_DISCOVERY_POOL_ROWS     = 1000             # rows of the Predicted Movers universe leaderboard read before the portfolio/watchlist/new filter
MCP_CANDIDATE_POOL_ROWS     = 150              # best tickers by composite score that find_candidates correlates with the portfolio (each is one price-file read)
MCP_CANDIDATE_DEFAULT_MIN_SCORE = 60           # composite-score floor of find_candidates when `min_composite_score` is left out
MCP_DISCOVERY_NAME_MAX_CHARS = 100             # company name returned by get_market_report and screen_universe (third-party text)
MCP_DISCOVERY_TEXT_MAX_CHARS = 60              # `sector` and `exchange` arguments and the sector/exchange text returned by the discovery tools
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
NEXTCLOUD_TALK_MAX_RETRIES  = 3                # re-sends of a Talk message that failed, after the first attempt
NEXTCLOUD_TALK_RETRYABLE_STATUSES = (408, 429)  # the only 4xx answers worth a re-send; any other 4xx is a rejection no retry can change
NEXTCLOUD_TALK_RETRY_MIN_INTERVAL_SECONDS = 300  # a failed message is retried no sooner than this after its last attempt, so a burst of alerts cannot use up its retries at once
NEXTCLOUD_TALK_RETRY_MAX_AGE_SECONDS = 7200    # a queued Talk message older than this is dropped as stale
NEXTCLOUD_TALK_HEALTH_WARNING_COOLDOWN_SECONDS = 21600  # the "Nextcloud Talk delivery is failing" notification repeats at most this often
NEXTCLOUD_TALK_STATS_RETENTION_DAYS = 90       # nextcloud_talk_stats rows older than this are pruned by the maintenance job
RISK_DIGEST_TALK_TOP_MOVERS   = 10               # stops listed in the Talk copy of the Risk Orchestrator Daily Rollup (largest % rise first); the in-app row lists all of them
MCP_AUDIT_QUEUE_MAX         = 1000             # audit rows waiting for the writer thread; past it a row is dropped, never the request
MCP_AUDIT_UNAUTH_ROWS_PER_MINUTE = 600         # audit rows without a valid key or token (rejections, OAuth browser steps) stored per minute; the rest are counted into one summary row
MCP_AUDIT_TEXT_MAX_CHARS    = 100            # request-derived text (JSON-RPC method, tool/prompt/resource name) stored in mcp_audit_log
MCP_AUDIT_ADDRESS_MAX_CHARS = 64               # client address stored in mcp_audit_log
MCP_PUBLIC_HOST_MAX_CHARS   = 253              # MCP_PUBLIC_HOST in the Settings save schema (longest DNS name)
MCP_REMOTE_CIDRS_MAX        = 16               # networks in MCP_REMOTE_ALLOWED_CIDRS
MCP_OAUTH_ACCESS_TOKEN_SECONDS = 15 * 60       # default life of an OAuth access token
MCP_OAUTH_ACCESS_TOKEN_CHOICES = (15 * 60, 60 * 60, 8 * 60 * 60, 24 * 60 * 60)  # lives the Settings box offers; a stored value outside this list is ignored
MCP_OAUTH_TOKEN_BOOST_SECONDS = 8 * 60 * 60    # the Settings extension button: for this long, tokens issued last at least this long
MCP_OAUTH_REFRESH_AHEAD_SECONDS = 120          # a restart warns when a connected app's access token expires within this many seconds
MCP_OAUTH_REFRESH_BEHIND_SECONDS = 300         # a client sees an expired token up to one long-held request (about 4 minutes) late, so a refresh is still expected this long after expiry
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
MCP_WRITE_ID_LENGTH         = 10               # characters of a proposal id (unambiguous 32-character alphabet, 50 bits)
MCP_WRITE_LIMIT_DEFAULTS    = {                # config.json MCP_WRITE_LIMITS: per key or grant, counted from mcp_write_log
    "proposals_per_hour": 20,                  # proposals created per rolling hour
    "pending_max": 5,                          # unexpired, unconfirmed proposals held at once
    "changes_per_day": 20,                     # executed changes per UTC day (undo included)
    "adds_per_day": 10,                        # Watchlist adds among them: each queues Yahoo-backed fetches
    "proposal_seconds": 600,                   # life of a proposal before confirm_change refuses it
}
MCP_WRITE_LIMIT_BOUNDS      = {                # (min, max) a saved limit may take; 0 blocks that action, proposal_seconds has no 0
    "proposals_per_hour": (0, 100),
    "pending_max": (0, 20),
    "changes_per_day": (0, 100),
    "adds_per_day": (0, 50),
    "proposal_seconds": (60, 3600),
}
MCP_WRITE_ID_ARG_MAX_CHARS  = 32               # `proposal_id` and `change_id` arguments of the write tools
MCP_OAUTH_CONNECTED_APPS_MAX = 50              # grants listed in the Settings Connected Apps table
MCP_ACTIVITY_ROWS           = 50               # audit rows listed in the Settings Remote Access Recent Activity table
ETF_CONSTITUENTS_MAX        = 50
OPTION_LEGS_MAX             = 20
TABLE_VIEWS_MAX             = 20               # saved column-preset Views per page
TABLE_COLUMN_KEYS_MAX       = 200              # keys in any one column list (registry holds ~100 per page)
TABLE_COLUMN_KEY_MAX_CHARS  = 64
TABLE_VIEW_NAME_MAX_CHARS   = 100
TABLE_FILTER_CONDITIONS_MAX = 50
TABLE_FILTER_VALUE_MAX_CHARS = 200

DB_WRITE_HOLD_WARN_SECONDS  = 5                # a SQLite write transaction held longer than this is logged at WARNING with its caller
DB_DEFERRED_WRITE_RETRY_SECONDS = (30, 60, 120, 240, 480)  # waits before each retry of a write that hit "database is locked"; after the last failed retry the write is abandoned
DB_DEFERRED_WRITE_QUEUE_MAX = 200              # writes waiting for a retry; past it a new one is dropped and logged, never blocked on
DB_SWAP_DRAIN_TIMEOUT_SECONDS = 30            # a backup restore waits this long for open database connections to close before it refuses and changes nothing
DB_SWAP_GATE_WAIT_SECONDS = 60                # a new connection waits this long for a closed swap gate before it raises DatabaseSwapInProgress

MEMORY_SAMPLE_INTERVAL_MINUTES = 15            # Process Memory Telemetry cadence
MEMORY_JOB_LOG_MIN_SECONDS  = 5                # a scheduled job gets a memory line when it ran at least this long
MEMORY_JOB_LOG_MIN_DELTA_MIB = 50              # ... or when it moved the process RSS by at least this much

ML_TRAINING_ABORT_AVAILABLE_GB = 0.75          # Global Model Training aborts before loading the history with less available RAM than this
ML_TRAINING_POST_LOAD_MIN_AVAILABLE_GB = 1.5   # ... and after the load, where the child still grows by about 1.2 GB before its ~2.1 GB peak
ML_TRAINING_SEARCH_ITERATIONS = 10             # RandomizedSearchCV draws for each classifier
ML_TRAINING_WATCHDOG_MIN_AVAILABLE_MIB = 300   # the web process kills the training child below this much available RAM, ahead of the kernel OOM killer; initial value, tune from the logged peak
ML_TRAINING_WATCHDOG_POLL_SECONDS = 2          # how often the web process samples the training child's memory
ML_TRAINING_CHILD_OOM_SCORE_ADJ = 1000         # the training child asks the kernel to pick it, not the web process, if memory runs out

# ── Static asset versioning ───────────────────────────────────────────────────
CSS_VERSION = "5.221" # bump this whenever styles.css (or any versioned static/js/*.js file) changes to bust browser caches

# ── FinBERT / NLP sentiment ────────────────────────────────────────────────────
NLP_FINBERT_MAX_TOKENS     = 512   # HuggingFace token limit for ProsusAI/finbert
NLP_FINBERT_REVISION       = "0bc2ac10dda6929bcb1f77ccc43c7ec5c424f031"  # ProsusAI/finbert main; pinned so a Hub-side change never swaps the weights (SEC 22/27)
NLP_TEXT_TRUNCATE_CHARS    = 5000  # perf guard only — tokeniser truncation=True handles semantic cut
NLP_NEWS_FETCH_LIMIT       = 15    # headlines scored per ticker in the routine scan
NLP_CB_NEWS_FETCH_LIMIT    = 20    # headlines scored for central bank NLP alerts
NLP_CB_TONE_THRESHOLD      = 0.15  # |score| above which tone is hawkish/dovish vs neutral
NEWS_REPEATED_SUMMARY_MIN_HEADLINES = 3  # a publisher summary shared by this many different headlines is a site-wide teaser, not a description of the article
NLP_NEWS_MAX_AGE_DAYS      = 3     # default age limit for a headline to count; SENTIMENT_ENGINE.MAX_NEWS_AGE_DAYS overrides
NLP_SENTIMENT_MIN_COVERAGE = 0.5   # a scan scoring a smaller share of its tickers raises the degraded-scan alert

ML_FEATURE_MAX_AGE_TRADING_DAYS = 3  # newest quant_signals row inference accepts, and the age past which a stored ML score is not shown

# ── Sentiment chart rendering ─────────────────────────────────────────────────
SENTIMENT_CHART_FIGSIZE    = (12, 6)  # matplotlib figure size for Nextcloud alert chart
SENTIMENT_CHART_DPI        = 300      # export resolution for Nextcloud alert chart
SENTIMENT_VISUAL_CACHE_TTL_SECONDS = 3600  # Macro Sentiment charts and Fear & Greed value; every rebuild makes a live CNN request

FREETRADE_IGNORED_MICS = frozenset({"XAMS", "XBRU", "XWBO", "XSTO", "XHEL", "XPAR", "MTAA", "XETR", "XLIS"})  # exchanges the app does not cover; the Freetrade sync skips them without a warning
