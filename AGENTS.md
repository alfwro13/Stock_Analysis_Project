# AGENTS.md — Quantamental Portfolio Dashboard

This file provides AI coding agents with the context needed to work effectively in this codebase.

---

## Project Overview

A self-hosted **FastAPI** web application that merges quantitative analysis, fundamental analysis, machine learning, and tail-risk management into a single portfolio dashboard. It is a hobby/personal project, not a production investment platform.

- **Server:** FastAPI + Uvicorn on port `8090` (configurable)
- **Language:** Python 3.10+
- **Templates:** Jinja2 HTML (server-rendered), with Plotly.js charts and DataTables via AJAX
- **Entry point:** `python main.py`
- **Database:** SQLite at `data/analysis.db` (WAL mode)
- **Scheduler:** APScheduler (runs inside the process — no external cron)

---

## Directory Layout

```
Stock_Analysis_Project/
├── main.py                   # App factory, middleware, lifespan hooks
├── api_routes.py             # /api/* router root: watchlist, screener, reports, options, xray, intraday, news, logs; includes sub-routers
├── api_routes_auth.py        # Auth + credential endpoints (login, password, account, Nextcloud/Ghostfolio/FRED/HF settings)
├── api_routes_triggers.py    # Scheduler trigger endpoints (ML, quant scan, universe, earnings, briefings, maintenance)
├── api_routes_system.py      # Settings Pydantic models + settings save, system ops, notifications, workflow monitor
├── api_routes_analysis.py    # Analysis signal endpoints (contagion, trap, bubble, forensic, regime, stress, ETF predictor, AI prompts)
├── api_routes_accounts.py    # Built-in Accounts CRUD + transaction ledger endpoints + ticker-lookup
├── api_deps.py               # Shared FastAPI dependencies for all api_routes* files: limiter (slowapi), require_confirm_token, _error_500
├── page_routes.py            # HTML page route handlers (thin shell — includes page_router_macro)
├── page_routes_macro.py      # /market-sentiment and /index/{ticker} routes + supporting data (INDEX_PARQUET_MAP, EVENT_GLOSSARY, enrich_macro_events, _parse_cb_nlp_message)
├── page_helpers.py           # Shared page-layer helpers: get_unread_count, _fmt_currency, _fmt_volume, _load_fundamentals_extra, _utc_str_to_local, _build_position_sizing_context, calculate_pnl
├── database.py               # Thin hub (~80 lines): get_connection(), log_notification(), re-exports from sub-modules below
├── db_schema.py              # init_db() (all CREATE TABLE statements) + migrate_db() (all ALTER TABLE migrations) + _seed_exchange_hours_json()
├── db_etf.py                 # ETF predictor CRUD: get/create/update/soft-delete configs, log_etf_prediction, fill_etf_actual, get_etf_accuracy
├── db_helpers.py             # Quant/trap/score helpers: upsert_quant_signal, log_score_event, log_trap_phase, get_unresolved_trap_phases, batch_update_trap_phase_actuals, get_trap_phase_accuracy, get_universe_tickers, get_mutual_fund_tickers
├── db_accounts.py            # Built-in Accounts CRUD: accounts + account_transactions + account_value_history
├── accounts_engine.py        # Built-in Accounts ledger math: average-cost holdings/closed-positions, cash balance, FX backfill, Ghostfolio merge
├── account_scraper_engine.py # Account Price Scraper: generic URL+CSS-selector price fetch/extract, CSV import, price lookups (House/Pension accounts)
├── backup_engine.py          # Backup & Recovery: tar.gz archive/restore of data/, models/, analysis.db to a local folder or NFS share
├── config.py / config.json   # Runtime configuration
├── constants.py              # Global constants
├── scheduler_engine.py       # APScheduler core: start/reload/shutdown, job wiring + infrastructure
├── scheduler_jobs.py         # All run_* job runner functions + resume_interrupted_scans
├── scheduler_manifest.py     # JOB_GRAPH, CONFIG_KEY_TO_JOB, label helpers (_resolve_manifest, job_label…)
├── scheduler_monitor.py      # Workflow Monitor graph builder (build_workflow_graph) + conflict detector
├── auth.py                   # Session-cookie auth
│
├── data_engine.py            # Yahoo Finance fetch + Parquet write
├── yahoo_engine.py           # Yahoo Finance HTTP cache / rate-limit wrapper (singleton)
├── huggingface_engine.py     # FinBERT NLP sentiment via HuggingFace transformers
├── universe_engine.py        # Market universe management
├── universe_fundamentals_engine.py
├── universe_deep_sync_engine.py
├── quant_engine.py           # Core scoring (0-100 composite score)
├── quant_screener.py         # Screener logic
├── quant_signals.py          # quant_signals table writes
├── score_analysis.py         # Score history analytics
├── indicators.py             # TA calculations (RSI, MACD, SMA, OBV…)
├── risk_engine.py            # VaR / CVaR / ATR stop-loss
├── position_sizing.py        # Kelly / fixed-fraction sizing
├── regime_engine.py          # Market regime classification
├── market_pulse.py           # CNN Fear & Greed + S&P chart
├── sentiment_engine.py       # Sentiment orchestration wrapper
├── insider_engine.py         # SEC Form 4 scraping
├── earnings_engine.py        # Earnings date tracking
├── earnings_vol_engine.py    # Implied vs historical move edge
├── options_engine.py         # Options chain data (calls/puts, IV smile)
├── macro_ai_engine.py        # HMM + RF + XGBoost macro predictions
├── macro_data_engine.py      # FRED / BoE / ONS ingestion
├── macro_calendar_engine.py  # Economic event calendar
├── treasury_auction_engine.py # Sovereign Debt Auction Monitor (fiscaldata.treasury.gov)
├── ai_engine.py              # LLM prompt aggregator
├── ai_prediction_engine.py   # XGBoost + RF soft-voting ensemble
├── ai_contagion_engine.py    # AI sector contagion monitor (10-ticker ecosystem)
├── bull_bear_trap_engine.py  # Post-crash lifecycle detector (Bull Trap, Bear Trap, Capitulation, Wyckoff)
├── anomaly_engine.py         # Unsupervised anomaly detection per ticker
├── xray_engine.py            # Portfolio X-ray / risk diagnostics
├── crash_engine.py           # Intraday crash detection
├── moonshot_engine.py        # Intraday parabolic / ATH detection
├── intraday_bottom_engine.py # Intraday capitulation-bottom detector (dip radar)
├── intraday_orchestrator.py  # 5-min scan loop
├── news_feed_engine.py       # RSS + full-text news fetching (yfinance + trafilatura)
├── gilt_engine.py            # UK gilt yield tracking
├── index_engine.py           # Index data (SPY, FTSE…)
├── freetrade_engine.py       # Freetrade CSV import
├── ghostfolio_sync.py        # Ghostfolio API sync
├── fundamentals_helpers.py   # Shared fundamentals calculations: calculate_piotroski_f_score, calculate_altman_z_score, calculate_beneish_m_score, calculate_peter_lynch_peg, get_instrument_type
├── visuals.py                # OHLCV, macro, and anomaly Plotly charts
├── visuals_etf.py            # ETF charts: correlation, prediction, contributions, overlay
├── visuals_ai.py             # AI contagion charts: performance chart, correlation heatmap
├── monte_carlo_engine.py     # Forward-looking Monte Carlo wealth simulation (on-demand, no scheduler job)
├── maintenance_engine.py     # DB vacuum, orphan file pruning
├── reports_engine.py         # Quant briefing report generation
├── morning_briefing.py       # Morning briefing assembly + dispatch
├── lunchtime_briefing.py     # Lunchtime briefing assembly + dispatch
├── notification_engine.py    # Unified notification router (log / in-app / Nextcloud) + NOTIFICATION_ROUTING registry
├── report_dispatcher.py      # Nextcloud Talk / alert dispatch
├── nextcloud_talk.py         # Nextcloud Talk webhook client
├── portfolio_service.py      # Portfolio aggregation helpers
├── profile_engine.py         # Asset profile cache (sector, country, exchange)
├── time_engine.py            # Central time/timezone module (all market-hours logic)
├── utils.py                  # Shared utilities
│
├── templates/                # Jinja2 HTML templates
│   ├── base.html             # Shared Bootstrap 5 shell — migrated pages {% extends %} it
│   ├── monte_carlo.html      # Monte Carlo Wealth Simulator page (/monte-carlo)
│   └── settings/             # Settings page partials (included by settings.html)
│       ├── _data.html        # Market Universe Pipeline, Macroeconomic Data, News & RSS cards
│       ├── _automation.html  # Background Automation Schedulers, ML & AI Engine, Live UI Updates cards
│       ├── _alerts.html      # Crash/Moonshot Alerts, Dip Radar, AI Contagion, Trap Monitor, Briefings cards
│       ├── _portfolio.html   # Position Sizing Defaults, X-Ray Allocation Targets cards
│       └── _system.html      # System Diagnostics, Manual Actions, Core System, Advanced Network, Nextcloud, Ghostfolio, User Account, Tools, Workflow Monitor, Notification Settings, System Updates cards
├── static/                   # CSS, JS, images
│   ├── css/styles.css        # Global stylesheet (single source of truth for all styles)
│   ├── vendor/               # Vendored front-end libs (Bootstrap 5, jQuery, DataTables + Responsive) — no CDN at runtime
│   └── js/
│       ├── csrf.js           # CSRF token helper
│       ├── navbar.js         # base.html navbar: notification poller + freshness badge
│       ├── position_sizing.js
│       ├── settings_shared.js    # setStatus(), setBoxStatus(), saveSettings(), search IIFE, global vars — load first
│       ├── settings_system.js    # auth, diagnostics, Workflow Monitor, git-pull, restart/terminate, Nextcloud/Ghostfolio, maintenance
│       ├── settings_data.js      # universe triggers, macro triggers, news fetch, profiler status, FRED key
│       ├── settings_automation.js # quant/earnings/briefing/ML/sentiment/X-ray triggers, HF token
│       ├── settings_alerts.js    # Dip Radar IIFE, crash/moonshot/contagion/trap/bubble/forensic triggers, test alerts
│       ├── settings_portfolio.js # ETF predictor CRUD + all helpers
│       └── monte_carlo.js        # Monte Carlo Wealth Simulator: initPage(), runSimulation(), renderChart() (Plotly fan chart)
├── data/                     # Runtime data (SQLite, Parquet, JSON)
│   ├── analysis.db
│   ├── historical/*.parquet  # 2-year daily OHLCV per ticker
│   ├── intraday/*.parquet    # 1-day 5-min OHLCV per ticker
│   ├── fundamentals/*.json   # Raw yfinance .info dumps
│   ├── portfolio.json
│   ├── watchlist.json
│   ├── freetrade_blacklist.json
│   └── isin_ticker_cache.json
├── models/                   # Trained ML artefacts (.joblib / .pkl)
├── assets/                   # Architecture docs and reference MDs
├── tests/                    # Pytest suite
├── debug_scripts/            # One-off diagnostic scripts (not part of tests)
└── reports/                  # Generated quant briefing markdown files
```

---

## Database Schema (SQLite — `data/analysis.db`)

All tables join on `ticker` as the primary key unless noted.

| Table | Purpose |
|---|---|
| `market_universe` | Master list of ~4,000 equities/ETFs |
| `asset_profiles` | Static corporate metadata (sector, country, exchange, summary) |
| `ticker_metadata` | Lightweight beta + market-cap cache (sector, beta, market_cap) |
| `stock_signals` | Final System Verdict + fundamental snapshot |
| `quant_signals` | Daily historical TA + ML scores (composite PK: ticker + date) |
| `quant_scan_states` | Resumability tracker for long-running scans |
| `score_history` | Per-ticker daily score + signal + close price history |
| `earnings_volatility` | Options arbitrage edge scores |
| `market_regimes` | VIX + SPY volatility regime labels + HMM state |
| `market_pulse_cache` | Cached CNN Fear & Greed + S&P snapshot |
| `macro_regimes` | Gilt/bond yield threat levels (US + UK) |
| `macro_calendar` | Economic events + AI volatility warnings |
| `macro_indicators` | FRED / BoE / ONS structural macro metrics |
| `treasury_auction_results` | US Treasury auction bid-to-cover, yield tail, and dealer participation — early warning for rate-shock events |
| `intraday_monitors` | Active dip-radar watch list (ticker, date_added, activated_by) |
| `intraday_monitor_results` | Per-ticker dip-radar scan results |
| `xray_risk_cache` | Per-ticker beta + annualised vol vs benchmark |
| `xray_portfolio_returns_cache` | Portfolio return series for X-ray calculations |
| `xray_correlation_matrix` | Rolling pairwise correlation matrix snapshot |
| `xray_dividend_cache` | Per-ticker dividend yield cache for X-ray |
| `ai_contagion_snapshots` | AI sector contagion scan results (payload JSON + alert flag) |
| `trap_monitor_results` | Latest trap scan result per ticker — phase label + four signal levels; powers `/trap-monitor` |
| `news_articles` | Full-text news articles with sentiment scores |
| `smgb_predictions` | SMGB.L morning price predictions + actuals + accuracy metrics |
| `alert_state` | Dedup ledger for intraday alert engines (fingerprint + cooldown) |
| `system_notifications` | In-app notification feed visible in the Settings notifications panel (written via the unified notification router) |
| `scheduler_run_log` | Last-run timestamp per APScheduler job ID, plus last-start, last/avg run duration, and last status (success/error) — powers the Workflow Monitor |
| `accounts` | Built-in brokerage accounts (name, currency, initial cash, soft-delete) — `/accounts` |
| `account_transactions` | Full transaction ledger per account (Buy/Sell/Fee/Dividend/Interest/Cash), with per-row trade currency + `exchange_rate` to `BASE_CURRENCY` |
| `account_value_history` | Nightly per-account value snapshot (total/cash/equity) — powers the account-value chart |
| `account_price_history` | Daily price series for the Account Price Scraper (House/Pension accounts) — source `scrape` or `csv_import`, `UNIQUE(account_id, price_date)` |
| `backup_history` | Audit log of every Backup & Recovery run (scheduled or manual) — trigger type, location, components, filename, size, status |

Schema changes must go through `db_schema.py:init_db()` (new tables) and `db_schema.py:migrate_db()` (ALTER TABLE on existing tables). All callers continue to `from database import init_db, migrate_db` — `database.py` re-exports both.

---

## Key Architecture Rules

1. **Dual-storage:** Relational metadata → SQLite. Heavy time-series → Parquet. Never swap these.
2. **Self-healing:** If Yahoo Finance fails, fall back to local Parquet/JSON cache — never crash the server.
3. **Priority arbitration:** Portfolio/Watchlist/Account Transactions > LSE universe > US universe. The daily fetch universe (`data_engine.py:get_all_tickers()`) unions `accounts_engine.get_combined_holdings()` (built-in Trading accounts + Ghostfolio when enabled), the Watchlist account (`database.get_watchlist_tickers()`), and every ticker in `account_transactions` across all account types (excluding the Pension synthetic `PENSION-{id}` ticker). A newly-bought unknown ticker also gets an immediate one-off fetch (`fetch_and_save_single_ticker`) as a background task, so it doesn't wait for the next 22:00 run. Delisted tickers are appended to `freetrade_blacklist.json` automatically.
4. **No LLM for sentiment:** Market sentiment uses FinBERT locally. `ai_engine.py` generates prompts for *external* LLMs but is not itself an LLM.
5. **APScheduler only:** Do not introduce external cron jobs. All scheduled work is wired through `scheduler_engine.py` (APScheduler setup, `reload_scheduler`, `start_scheduler`). Job runner functions live in `scheduler_jobs.py`; the job manifest and label helpers live in `scheduler_manifest.py`; the Workflow Monitor graph logic lives in `scheduler_monitor.py`. All three are re-exported from `scheduler_engine` so external callers need not change their imports.
6. **CSRF + session auth:** All POST endpoints are protected. Session cookies only. See `auth.py`.
7. **Workflow manifest:** Every `scheduler.add_job(... id=X)` must have a matching `JOB_GRAPH` entry in `scheduler_manifest.py` declaring its `produces`/`consumes` data artifacts (dynamic ids matched in `_resolve_manifest`). The Workflow Monitor derives its dependency graph and conflict detection from these; the manifest-completeness test fails if a registered job is missing.
8. **One canonical name per feature — no mixed terminology.** Every scheduled job, engine, page, tool, metric, signal, config option, and feature has exactly **one** user-facing name, and that *same* wording must be used everywhere it appears: the Settings UI (configuration panels **and** the Master APScheduler Matrix), the Workflow Monitor, the System Diagnostics panel, the glossary (`templates/glossary.html`), the asset docs (`assets/`), and the `README.md`. **Never invent a new display name in one place when the thing is already named elsewhere, and never show a code-derived name (e.g. a config key like `ML_TRAINING` rendered as "Ml Training") in one surface while a descriptive name is used in another.** When you add or rename anything user-facing, grep the whole app for the old/related wording and update every surface in the same change. If a single Settings panel controls several jobs (or one job spans several panels), that mismatch must be resolved — not papered over with ad-hoc per-place variants.

   **For scheduled jobs the single source of truth is `scheduler_manifest.JOB_GRAPH[job_id]["label"]`** (re-exported as `scheduler_engine.JOB_GRAPH`; it equals the Settings panel wording). Surfaces that are keyed by config key (the Master Matrix, the diagnostics last-run map) must resolve their display text through `scheduler_engine.CONFIG_KEY_TO_JOB` + `job_label()`/`scheduler_display_names()` — never by title-casing the config key. The Active-Jobs panel name comes from `_mark_job_started(job_label("<job_id>"))`, never a hardcoded literal. **Code identifiers (job ids, `run_*` functions, config keys, engine module/class names) are deliberately *not* renamed to match** — instead, every engine module whose code name differs from its GUI name carries a top-of-module comment `# GUI name: "<name>". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.` so a reader knows what the user calls it. Add that comment whenever you create a job whose code name differs from its GUI label.

9. **All notifications go through the unified router.** Every user-facing notification — scheduled-job status and all alerts — must be dispatched via `notification_engine.notify(source, message_type, message_text, ...)`. Do **not** call `nextcloud_talk.send_text_message()` or `INSERT` into `system_notifications` directly from a feature engine. Per-source channel routing (log file / in-app / Nextcloud Talk) lives in `NOTIFICATION_ROUTING` (`config.json`), is editable in the Settings **Notification Settings** panel, and falls back to each source's default in `notification_engine.NOTIFICATION_SOURCES`. A new alert source must be added to that registry (with a canonical `label` and parent `job_id`); a new scheduled job automatically gets a routable status row. Dedup/cooldown stays in the engines (`alert_state`) — the router only decides *where* a fired event goes. Exceptions: deep pipeline-progress chatter may still call `database.log_notification()` directly (in-app only), and file-attachment dispatches (briefings, the Fear & Greed chart) keep their own upload path gated by their enable toggle.

10. **Background jobs must never starve the web-server thread pool.** APScheduler jobs run in a thread pool shared with (or adjacent to) the threads that serve synchronous FastAPI route handlers. Any `time.sleep()`, long network retry, or other blocking call made **while holding a lock that is also acquired by web-request code paths** will prevent those threads from making progress, exhaust the pool, and make the site unresponsive — exactly what a `threading.Lock` held during 429 backoff does to `yahoo_engine._yf_singleton_lock`.

   **Rule:** Never sleep or block inside a lock that web-request threads also need. The correct pattern:

   - On the blocking condition (e.g. HTTP 429), trip a global `threading.Event` circuit breaker (clear it) and spawn a daemon thread for the actual sleep; the calling thread raises and exits the lock immediately.
   - Any thread that wants the shared resource calls `event.wait()` **before** acquiring the lock — it sleeps outside the lock and wakes with every other waiter when the event is set.

   See `tools/network_engine._enter_yahoo_rate_limit` + `yahoo_engine._RateLimitAwareLock` as the canonical reference implementation for this pattern.

11. **Bootstrap 5 front-end on `base.html` — migration complete.** Every page `{% extends "base.html" %}` — never duplicate the `<!DOCTYPE>`/`<head>`/navbar boilerplate. `templates/navbar.html` has been deleted; the Bootstrap navbar lives entirely in `base.html` with active-link detection via `request.url.path`. The UI is Bootstrap 5.3 (dark via `data-bs-theme="dark"` plus CSS-variable overrides in the theme layer at the top of `static/css/styles.css` — no Sass build). Front-end libraries (Bootstrap, jQuery, DataTables + Responsive, Plotly) are **vendored** under `static/vendor/` and served locally; do not add CDN `<script>`/`<link>` tags.

    **New page checklist:**
    - `{% extends "base.html" %}` — fill blocks: `title`, `extra_head` (Plotly if needed), `body_class`, `container_class`, `content`, `scripts`.
    - All CSS in `static/css/styles.css`; no inline `style=` attributes (exception: JS-generated `innerHTML`). Check for an existing class before adding one.
    - JS blocks > ~50 lines with no Jinja → `static/js/<page>.js` loaded via `{% block scripts %}`; use a tiny inline bootstrap for `window.*` Jinja globals. Never put `{{ }}` in a `.js` file.
    - Tables use `<table class="table table-hover w-100">` with DataTables `responsive: true` and per-column `responsivePriority`. For JS-rendered tables (rows built by `fetch()`) keep a plain `<table>` with Bootstrap classes rather than DataTables.
    - Use Bootstrap grid (`.row` + `.col-12 .col-lg-*`) and spacing utilities; avoid custom layout grids where Bootstrap breakpoints suffice.
    - Bump `CSS_VERSION` in `constants.py` whenever `styles.css` changes.
    - Canonical reference implementation: `templates/watchlist.html` + `static/js/watchlist.js`.

12. **AI manageability & central-engine compliance.** This codebase is edited collaboratively with AI coding agents whose context windows are finite. Keep files at a size where a single Read can capture the relevant section without loading the whole module into context.

    **File growth:** There are no hard line-count limits, but when a Python module or HTML template has grown to the point where an agent must read it in multiple passes to understand a single feature, that is the signal to split. Prefer extracting large, self-contained functions or related groups of functions into a new module rather than growing the existing one. For HTML templates, follow the same pattern already used for `templates/settings/` — extract card groups into `_partial.html` files and `{% include %}` them. Do not create a new file unless the existing file is genuinely unwieldy; three or four files of moderate size are better than one that requires multiple reads and one that is near-empty.

    **Central engines — never bypass:**

    | Concern | Canonical engine | What a bypass looks like |
    |---|---|---|
    | Time / timezone | `time_engine.py` | `datetime.now()` without `timezone.utc`; `date.today()`; hardcoded `"Europe/London"` / `"America/New_York"` / `"UTC"` outside `time_engine.py`; `ZoneInfo(…)` or `pytz` imports in any other module |
    | Yahoo Finance | `yahoo_engine.py` | `yf.Ticker(…)` / `yf.download(…)` in any module other than `yahoo_engine.py` |
    | Scheduled jobs | `scheduler_engine.py` (wiring) + `scheduler_jobs.py` (runners) + `scheduler_manifest.py` (manifest) + `scheduler_monitor.py` (graph) | APScheduler imported anywhere outside these four files; any external `cron` or `systemd timer` |
    | Notifications | `notification_engine.notify()` | Direct `nextcloud_talk.send_text_message()` or bare `INSERT INTO system_notifications` in feature engines |
    | Font sizes (UI text) | CSS custom properties in `:root` | Literal `font-size: 14px` / `font-size: 1rem` on UI-text elements in `styles.css` — use a `var(--font-size-*)` property instead |
    | Arbitrary external HTML price scraping | `account_scraper_engine.py` | Ad-hoc `requests.get()` + selector/regex parsing of a third-party or self-hosted price page anywhere outside this module |
    | Backup / restore of app data | `backup_engine.py` | Ad-hoc `tarfile`/`shutil` archiving or extraction of `data/`, `models/`, or `analysis.db` anywhere outside this module |

    **Bootstrap 5 / front-end vendoring:** Every full HTML page must `{% extends "base.html" %}`. No page may load Bootstrap, jQuery, DataTables, or Plotly via a CDN `<script>` or `<link>` tag — all front-end libraries are vendored under `static/vendor/` and served locally. Verify with: `grep -r "cdn\." templates/ --include="*.html"`.

    **Before touching a large file:** read only the section you need (use `offset` and `limit` on the Read tool, or a targeted `grep`). If a file is so large that understanding one feature requires reading more than ~400 lines, flag it as [NEEDS REVIEW] for splitting rather than loading it wholesale.

13. **Every subsystem must be visible in the Workflow Monitor.** When a new engine, page, or feature produces or consumes any data artifact already tracked in `scheduler_manifest.JOB_GRAPH` (or starts a new artifact chain other jobs will read), it must get a `JOB_GRAPH` entry in the same change — even if it isn't a scheduled job. Non-scheduled processes (manual data entry, external integrations) get a `non_job: True` or `category: "external"` entry with accurate `produces`/`consumes` so the graph shows where data actually originates. Don't leave this for a later audit pass — the Built-in Accounts subsystem (Trading/Pension/House) went unrepresented on the graph for several sessions after it was added before this was caught.

14. **Built-in Accounts is the primary portfolio/watchlist source. Ghostfolio is opt-in only.** `accounts_engine.get_combined_holdings()` is the canonical source for all portfolio ticker lists and position data (shares, cost basis, account membership). `database.get_watchlist_tickers()` is the canonical source for watchlist tickers. Every engine, scheduled job, API route, and page that needs "what's in the user's portfolio/watchlist" **must** call one of these two functions — never read `data/portfolio.json` or `data/watchlist.json` directly.

   `portfolio.json` and `watchlist.json` are **Ghostfolio output files** written only by `ghostfolio_sync.py` when `GHOSTFOLIO_ENABLED = True` (disabled by default). `accounts_engine.get_combined_holdings()` already merges Ghostfolio holdings (when the file exists) with built-in Trading account holdings, so calling it is always correct whether Ghostfolio is on or off.

   **Bypasses to flag as bugs:** any `open(PORTFOLIO_PATH)`, `_load_json(PORTFOLIO_PATH)`, `get_tickers_from_json(PORTFOLIO_PATH, ...)`, or `engine.portfolio` attribute access outside `accounts_engine.py` and `ghostfolio_sync.py` will silently return an empty portfolio when Ghostfolio is disabled.

15. **Never let a fetched value die with the engine that fetched it — share it via a timestamped cache.** Any engine that fetches external data (Yahoo Finance price/OHLCV, macro data, news, etc.) for its own narrow purpose must also persist any generally-useful derived value (e.g. a ticker's current price) to a shared, timestamped cache table, so other engines and pages can reuse it instead of re-fetching. Before writing new fetch logic for something another part of the app already tracks, check the relevant shared cache first (with a freshness/staleness check) and only fetch if it's missing or stale — never fetch unconditionally just because it's convenient for the caller.

    The canonical example is live/current price: `market_pulse_cache` (`market_pulse.py`) is the single shared cache — `market_pulse.upsert_live_price()` writes to it, `market_pulse.get_cached_pulse_from_db()`'s `needs_refresh` flag is the check-before-fetch read path. The Crash & Moonshot scan (`intraday_orchestrator.py`) and Dip Radar (`intraday_bottom_engine.py`) both write into it from data they already fetch for their own pattern detection; the Portfolio page and the `/api/market-pulse` JS-polling loop both read from it. Neither of the two heavy scanners re-fetches from this cache themselves — they need actual OHLCV bars at a specific resolution their own pattern math requires, which a single cached price can't substitute for — but they never discard the price they derive along the way.

    **When adding or materially changing an engine that fetches external data, or when you notice one engine re-fetching something another engine already holds fresh:** wire the new engine into the existing shared cache (extending it if the existing schema doesn't fit) rather than adding another isolated fetch-and-discard path. Flag it as a bug if a code review finds a fetch that duplicates data another engine already has timestamped in a shared table.

16. **One canonical function per shared calculation — never recompute the same concept independently in multiple places.** This applies to derived business math, not just raw fetched values (rule 15 covers the latter). If two or more features need "the current price for a ticker," "P&L for a position," "an FX conversion," etc., there must be exactly **one** function that computes it, and every caller uses that function — not a parallel reimplementation with its own logic for freshness, fallback, or edge cases.

    **Bypass to flag as a bug:** a new page/engine/endpoint writing its own inline logic to derive a value that an existing engine already computes (e.g. picking a "current price" from `market_pulse_cache` vs. `stock_signals` with bespoke freshness rules, when `accounts_engine.current_price_map()` already does this canonically). Independent reimplementations drift apart over time even when they start out equivalent, and the divergence is invisible until two pages are compared side by side — see the Portfolio/Stock Detail/Accounts price-consistency bug (fixed 2026-07-03) where three separate "get the current price" implementations had quietly diverged.

    **Before writing new calculation logic:** grep for the concept first (e.g. `current_price`, `exchange_rate`, `pnl`) to check whether a canonical engine function already exists. If it does, use it. If a genuinely new calculation is needed that overlaps with an existing one, extend the existing canonical function rather than writing a sibling.

17. **Helper files are the canonical home for shared utility/calculation logic — check them before writing new logic, and extend rather than duplicate.** This codebase has four dedicated shared-helper modules, each scoped to a layer:

    | Helper file | Scope | Examples |
    |---|---|---|
    | `db_helpers.py` | DB-layer query/write helpers used by more than one engine or route (quant/trap/score persistence, universe/ticker classification queries) | `upsert_quant_signal`, `log_trap_phase`, `get_universe_tickers`, `get_mutual_fund_tickers` |
    | `fundamentals_helpers.py` | Pure calculation helpers derived from fundamentals data (accounting-forensics scores, valuation ratios, instrument classification) | `calculate_piotroski_f_score`, `calculate_altman_z_score`, `calculate_beneish_m_score`, `calculate_peter_lynch_peg`, `get_instrument_type` |
    | `page_helpers.py` | Page-route-layer display/formatting helpers shared across `page_routes*.py` | `_fmt_currency`, `_fmt_volume`, `calculate_pnl`, `_build_position_sizing_context` |
    | `utils.py` | Generic, layer-agnostic utilities with no DB/page dependency | `normalize_ticker` |

    **Before writing a new utility function, calculation, or query helper:** grep the relevant helper file(s) above for the concept first. If an existing function already does it, or is close enough to extend (e.g. an extra parameter), use/extend it — do not write a second, parallel implementation in the calling module. Same failure mode as rule 16, generalised beyond business calculations to any reusable helper: duplicated logic drifts apart silently (a fix applied to one copy doesn't propagate to the other) until two callers disagree on something that should be identical — see the `get_mutual_fund_tickers()` gap (fixed 2026-07-03) where `intraday_orchestrator.py` had its own undocumented ad-hoc `ticker.startswith('0P')` mutual-fund check instead of using the canonical `db_helpers` function, which itself hadn't been wired into every `get_intraday()` call site that needed it.

    **Search by purpose, not exact name.** A duplicate is rarely named the same as the original — `_get_current_price`, `resolve_price`, and `latest_quote` can all be the same concept reimplemented three times. Grep for the underlying noun/verb (`price`, `pnl`, `mutual_fund`, `exchange_rate`) across the four helper files above rather than just the function name you're about to write.

    **When no existing helper file fits:** if a genuinely new domain of shared logic emerges, create a new `<domain>_helpers.py` rather than forcing it into an unrelated file or leaving it duplicated inline. Keep each helper file scoped to one concern; if it grows too large for a single Read, that's the rule 12 split signal.

---

## Running the App

```bash
source venv/bin/activate
python main.py          # starts on http://localhost:8090
```

First boot auto-creates `config.json` and initialises the DB schema.

---

## Testing

Always run the full regression suite after any code change:

```bash
./run_tests.sh              # full suite (~2052 tests)
./run_tests.sh --fast       # skip slow page-render tests
./run_tests.sh --db-only    # DB schema tests only
./run_tests.sh --api-only   # API endpoint tests only
```

Tests live in `tests/`. Fixtures and the test client are in `tests/conftest.py`. Do not mock the database in tests — the suite uses a real in-memory SQLite instance spun up per session.

A new `run_*` job-runner function in `scheduler_jobs.py` must have at least one test that calls the runner itself (e.g. `scheduler_jobs.run_foo_job()`), not only the engine function it delegates to. A test that only exercises `some_engine.do_thing()` will not catch a missing import or wiring bug in the runner that wraps it — exactly the kind of gap that let a `NameError` slip into `run_account_value_snapshot()` undetected until a direct runner-level test was added (June 2026).

---

## API Conventions

- Base path: `/api`
- All responses: `{ "status": "success"|"error", "message": "..." }`
- Heavy operations (ML training, data scans) return immediately and run as background tasks. Progress is visible in the Notifications tab (`GET /api/notifications/latest`).
- Full endpoint reference: [assets/api_reference.md](assets/api_reference.md)

---

## Documentation Maintenance

Every code change that adds, removes, or significantly alters a feature **must** be accompanied by documentation updates in the same task. Do not mark work done until these steps are complete.

### Glossary (`templates/glossary.html`)
- When a new user-facing concept, metric, score, signal, or algorithm is introduced, add a `<div class="term-box">` entry under the appropriate `<details>` section. Follow the existing style exactly (see surrounding entries).
- When a concept is renamed or removed, update or delete its entry.

### Asset documentation (`assets/`)
- Identify which markdown files in `assets/` describe the area you changed. Update them to reflect the new behaviour, new DB tables, new config keys, new scheduler jobs, or new API endpoints.
- If no existing file covers the new feature, create one only if the feature is substantial enough to warrant standalone documentation (e.g. a new engine, a new sub-system). Otherwise integrate it into the closest related file.
- Always update `assets/api_reference.md` when adding, removing, or changing any `/api/*` endpoint.
- Always update `assets/db_schema_and architecture.md` when adding or changing DB tables.

### README (`README.md`)
- When a new feature, tool, page, engine, or integration is added, update `README.md` to reflect it. This includes new entries in any feature list, new configuration keys, new dependencies, or changes to how the app is run or installed.
- Do not add implementation detail to the README — keep it user-facing and high-level.

### AGENTS.md (this file)
- If a change warrants an update to AGENTS.md (new engine in the directory layout, new DB table in the schema list, new architectural rule, new external integration), **do not edit this file automatically**.
- Instead, present the proposed addition or change to the operator and wait for explicit approval before applying it.

---

## Coding Guidelines for Agents

- **Do not add comments** unless the why is genuinely non-obvious.
- **Do not add error handling** for scenarios that cannot happen — trust framework guarantees.
- **Do not create new files** unless strictly necessary; prefer editing existing modules unless the existing files have grown to big in which case consider all options for splitting them into smaler ones
- **Do not introduce abstractions** beyond what the task requires.
- **Run `./run_tests.sh`** after every change and fix failures before marking work done.
- **Tooltips:** Use `<abbr title="Explanation text.">Label</abbr>` — wrap the label itself, no custom JS tooltip systems, no icon, no `style` attribute on the `<abbr>`. The global CSS in `static/css/styles.css` already applies `text-decoration: underline dotted #666`, `cursor: pointer`, and `color: inherit` to all `abbr` elements. Never override these inline. Keep tooltip text to 1–2 sentences matching existing examples (e.g. Support 1, RSI, ATR).
- **Styles belong in `static/css/styles.css`:** Do not write inline `style="..."` attributes. Check whether a CSS class already exists before adding anything. Only use inline styles in JS-generated HTML (e.g. dynamic `innerHTML`) where class-based styling is impractical, and even then keep it minimal.
- **No hardcoded `font-size` values for UI layout elements in `styles.css`.** All user-visible text sizes must reference a CSS custom property declared in the `:root` block (e.g. `font-size: var(--font-size-body)`). The ten variables are `--font-size-nav`, `--font-size-table` (screener/report tables), `--font-size-dt-table` (Portfolio/Watchlist DataTables — uses an explicit `td`/`th` rule, not inheritance), `--font-size-form`, `--font-size-btn`, `--font-size-section`, `--font-size-body`, `--font-size-h1`, `--font-size-h2`, `--font-size-h3`; their runtime values come from `GET /api/ui-theme.css` which reads `UI_PREFERENCES` from `config.json`. Exception: intentional data-visualisation sizes (large numeric KPI tiles, score displays, chart annotation text) may use explicit `px` values when they are purposely non-configurable.
- **Large JS blocks belong in `static/js/`:** If a template `<script>` block exceeds ~50 lines, extract it to a `.js` file (see `templates/watchlist.html` / `static/js/watchlist.js` as the reference). Use a small inline bootstrap to expose any Jinja-derived values as `window.*` globals, then load the external file with `<script src="/static/js/file.js?v={{ css_version }}">`. Never put `{{ ... }}` Jinja interpolations inside `.js` files.
- **Tables use DataTables Responsive:** New or migrated data tables initialise DataTables with `responsive: true` and explicit per-column `responsivePriority` so the full column set shows on desktop and only the essentials survive on a phone (collapsed columns move to the expandable child row). Keep the client-side full-array data load — do not switch to server-side processing. See `static/js/watchlist.js`.
- **UK market quirks:** LSE-listed stocks may have prices quoted in pence (GBX), not pounds (GBP). The codebase handles this explicitly — do not remove or simplify that logic.
- **Secrets:** All credentials live in `.env` (loaded via `python-dotenv`). Never hard-code tokens or API keys. Never commit `.env`.
- **Port:** Default is `8090`. Do not change it without updating `config.json` and `config.py`.
- **Never mask bad data in the display layer.** If a chart, table, or API response shows incorrect values due to corrupt or misformatted data in the database, the fix must go to the source — either the data pipeline (engine) or a data migration in `db_schema.py:migrate_db()`. Do not add filters, clamps, or guards in `visuals.py`, template code, or API serialisation to hide the bad values. Filtering in the display layer hides the problem from monitoring and leaves incorrect data in the DB silently corrupting other consumers (e.g. `regime_engine.py` reads `us_cpi_inflation` directly for macro regime classification).

### Settings page structure

`templates/settings.html` is a thin shell (~70 lines). All card HTML lives in five Jinja2 partials under `templates/settings/`:

| Partial | Cards |
|---|---|
| `_data.html` | Market Universe Pipeline, Macroeconomic Data, News & RSS |
| `_automation.html` | Background Automation Schedulers, Machine Learning & AI Engine, Live UI Updates & Notifications |
| `_alerts.html` | Crash & Moonshot Alerts, Dip Radar, AI Sector Contagion Monitor, Trap Monitor, Alerts/Reports/Quant Briefings |
| `_portfolio.html` | Position Sizing Defaults, X-Ray Allocation Targets |
| `_system.html` | System Diagnostics, Manual Actions, Core System & Currencies, Advanced Network, Nextcloud, Ghostfolio, User Account, Tools, Workflow Monitor, Notification Settings, System Updates |

JS is split across six domain files in `static/js/`:

| File | Responsibility |
|---|---|
| `settings_shared.js` | `setStatus()`, `setBoxStatus()`, `saveSettings()`, search IIFE, global vars (`CONFIRM_TOKEN`, `currentDiscoveredAccounts`, `macroInitState`) — **must load first** |
| `settings_system.js` | Auth, diagnostics, Workflow Monitor, git-pull, restart/terminate, Nextcloud test, Ghostfolio discover, maintenance, active-jobs refresh |
| `settings_data.js` | Universe triggers (Freetrade, index scrape, profiler, deep sync, import), macro triggers, news fetch, profiler status, FRED key save |
| `settings_automation.js` | Quant scan, earnings scan, morning/lunch briefings, ML backfill/training/inference/anomaly, sentiment scan, HF token |
| `settings_alerts.js` | Dip Radar IIFE + functions, crash/moonshot/contagion/trap/bubble/forensic triggers, test-alert functions, `copyRssFeedUrl` |
| `settings_portfolio.js` | ETF predictor CRUD + all helpers |

**Adding a new settings panel:**
- Put the card HTML in the appropriate partial (or `_system.html` for system-level concerns).
- Put the card's JS functions in the matching domain file.
- Declare any new global state with `var` (not `let`) in `settings_shared.js` if it must be read by more than one domain file.
- Cards inside the left column (`_data`, `_automation`, `_alerts`, `_portfolio`) are inside `<form id="settingsForm">` — their inputs are harvested by `saveSettings()` on Save. Cards in `_system.html` are outside the form and are saved via their own dedicated API calls.

### Time and Timezone Rules

All time-related code **must** go through `time_engine.py`. Never hardcode timezone strings, market hours, or reset times anywhere else in the codebase.

**Storage:** Always store datetimes in UTC (SQLite text as `"%Y-%m-%d %H:%M:%S"`, Parquet as naive UTC timestamps). Never store local times.

**Display:** Convert to the user's local timezone at the point of display only, using `time_engine.to_local(dt)`, `time_engine.fmt_time(dt)`, or `time_engine.fmt_datetime(dt)`. These read `USER_TIMEZONE` from config automatically.

**Market-window checks:** Use `time_engine.is_market_open(exchange)` or `time_engine.market_window_utc(exchange)`. Never compare `datetime.now(timezone.utc).time()` against a hardcoded `"HH:MM"` string — that pattern is always a bug because the string has no timezone context.

**Per-ticker exchange detection:** Use `time_engine.ticker_exchange(ticker, currency)` — it maps `.L`/GBP→LSE, `.DE`/EUR→XETRA, `.T`→TSE, USD→NYSE, and falls back to `HOME_EXCHANGE` for ambiguous tickers.

**APScheduler jobs:** Use `time_engine.reset_cron_trigger_params(exchange)` to generate `CronTrigger` kwargs for end-of-session resets. Always specify `timezone` in every `CronTrigger` — a trigger without a timezone is implicitly system-local and will break across DST boundaries or server moves.

**Formatting a reset time for display:** Use `time_engine.fmt_reset_time(exchange) -> str` — it wraps `reset_cron_trigger_params` + `datetime.combine` + `fmt_time` so callers never need to import `ZoneInfo` directly.

**Config keys:** `USER_TIMEZONE` (IANA string, e.g. `"Europe/London"`) and `HOME_EXCHANGE` (`"LSE"` | `"NYSE"` | `"XETRA"` | `"TSE"`) are the two user-facing settings that drive all of the above. Both live in `config.json` and are editable via the Settings UI.

---

## External Integrations

| Service | Purpose | Config key |
|---|---|---|
| Ghostfolio | Live portfolio holdings | `GHOSTFOLIO_URL`, `API_TOKEN` |
| Yahoo Finance (`yfinance`) | Price, OHLCV, fundamentals | (public, no key) |
| HuggingFace (`transformers`) | FinBERT NLP sentiment model | `HF_TOKEN` in `.env` (optional, speeds up hub download) |
| Nextcloud Talk | Push alerts / morning briefing | `NEXTCLOUD_*` keys in `.env` |
| FRED / BoE / ONS | Macro indicators | (public) |
| SEC EDGAR | Insider Form 4 filings | (public) |

---

## ML Models (`models/`)

| File | Description |
|---|---|
| `ml_ensemble.joblib` | Primary soft-voting classifier (XGBoost + RF) |
| `production_ensemble*.pkl` | Long/short directional ensembles |
| `raw_xgb_model*.pkl` | Raw XGBoost base learners |
| `xgb_explainer*.pkl` | SHAP explainers |
| `feature_names.json` | Ordered feature list for inference |
| `feature_stats.joblib` | Cross-sectional z-score stats |

Retrain via the Settings UI ("Initialize AI Engine") or the `ml_historical_backfill.py` script.

---

## Tools Menu (`/tools`)

A dedicated page housing standalone analytical tools. Each tool is self-contained and fetches data live on demand. Accessible via the navbar (`🔧 Tools`). New tools are added as `guide-card` entries in `templates/tools.html` with a corresponding route in `page_routes.py`.

| Tool | Route | Description |
|---|---|---|
| Dip Radar Summary | `/dip-radar` | Live intraday capitulation detector across all monitored tickers; reversal score 0–100, refreshed every 2 min |
| Options Sandbox | `/options-sandbox` | Interactive options chain explorer; live calls/puts, IV smile, open interest and volume across expiries |
| AI Sector Contagion Monitor | `/ai-contagion` | Tracks 10-ticker AI ecosystem (semis + hyperscalers + cloud); 30-day normalised performance, intraday session, rolling 20-day correlation heatmap |
| Market Trap & Recovery Monitor | `/trap-monitor` | Post-crash lifecycle monitor: Bull Trap / Dead Cat Bounce, Bear Trap, Capitulation volume climax, and Wyckoff Accumulation detection across portfolio + proxy basket |
| Market Regime (HMM) | `/market-regime` | Classifies the market into Bull / Chop / Crash via a 5-year GaussianHMM on SPY returns and EWMA volatility; shows Viterbi history, transition probabilities, and per-regime return/vol statistics |
| Historical Stress Tester | `/stress-test` | Simulates the portfolio through GFC 2008, Dot-com 2000, COVID-19 crash, and 2022 inflation shock using beta-adjusted scenario shocks; shows estimated monetary loss per holding and by sector |
| Bubble Radar | `/bubble-radar` | Scans portfolio and watchlist for valuation euphoria: SMA-200 extension, sustained overbought RSI, stretched P/S and PEG, IV call skew, and market breadth; tracks prediction accuracy at 4, 8, and 12 weeks |
| Forensic Screener | `/forensic-screener` | Monthly institutional-grade accounting forensics: Piotroski F-Score, Altman Z-Score, and Beneish M-Score from annual financials; fires Nextcloud alerts when holdings breach distress thresholds |
| FX Drag Analyzer | `/fx-drag` | Decomposes each US stock position's GBP return into equity return (USD) and FX effect (GBP/USD movement) across YTD, 1-year, and 2-year windows |
| ETF Price Predictor | `/etf-predictor` | Generic morning price predictor for any ETF; configure constituent tickers and weights, predicts next-session open via holdings-weighted basket return and OLS regression with FX adjustment; tracks accuracy over time |
| Monte Carlo Wealth Simulator | `/monte-carlo` | Projects portfolio wealth over 10/20/30 years using 1,000 correlated GBM paths (Cholesky of `xray_correlation_matrix`); percentile fan (P5–P95) in nominal and real terms; probability of reaching a target wealth. On-demand only — no scheduler job. Engine: `monte_carlo_engine.py` |

---

## Notable Sub-systems

- **X-ray engine** (`xray_engine.py`): Portfolio risk diagnostics view, added June 2026. Runs as a scheduler job at 19:00. Served at `GET /api/xray`. Renders inline on `portfolio.html` as a same-page swap.
- **Intraday orchestrator** (`intraday_orchestrator.py`): Runs every 5 minutes during market hours. Detects crash/moonshot conditions and fires Nextcloud alerts.
- **Quant briefing** (`reports_engine.py`): Generates a markdown report dispatched each morning via Nextcloud Talk.
- **Workflow Monitor** (`scheduler_engine.py`): Settings-page dependency flow-chart of every scheduled job, added June 2026. Built from the `JOB_GRAPH` manifest (each job declares `produces`/`consumes`; edges derive from artifact intersection) merged with live scheduler state and `scheduler_run_log` durations. Detects scheduling conflicts (overlap with a still-running upstream, backwards ordering, disabled upstream, stale/never-run, last-run error). Served at `GET /api/workflow-monitor/status`; rendered with vendored Mermaid.js (fetched on first boot, gitignored).
- **Embed mode:** Append `?embed=true` to `/portfolio` or `/watchlist` to strip the navbar for iframe integration (e.g. Home Assistant).
- **Built-in Accounts** (`accounts_engine.py` + `db_accounts.py` + `api_routes_accounts.py`): native, database-backed brokerage accounts + transaction ledger, added June 2026, served at `/accounts`. Coexists with Ghostfolio — `accounts_engine.get_combined_holdings()` sums the same ticker across both sources and lists both account entries; built-in account filter ids are namespaced `"acct:{id}"` so they never collide with Ghostfolio UUIDs. Holdings/closed-positions/realized P&L are derived via a single chronological average-cost pass per ticker (`accounts_engine._ledger_for_account`). Cost basis is stored in `BASE_CURRENCY`; FX is resolved per-transaction via an explicit `exchange_rate`, auto-filled from `accounts_engine.fx_rate_on_date()` (historical lookup → live rate → `1.0`) when not supplied. GBp (LSE pence) is preserved verbatim as the transaction currency — never uppercased — so the existing pence-conversion logic keeps working. Each account has a detail page (`/accounts/{id}`) with a value-over-time chart, Holdings (market value/allocation %/performance), Closed Positions, the full Activities ledger, and Cash Balance History. The chart is fed by a nightly `account_value_snapshot_job` (`accounts_engine.snapshot_all_accounts`) plus a one-time `accounts_engine.backfill_value_history` from cached Parquet price data, triggered as a background task when an account is created. The chart is rendered client-side (Plotly, `static/js/account_detail.js`) with 1M/1Y/YTD/MAX range buttons fed by `GET /api/accounts/{id}/value-history?period=` (`accounts_engine.filter_value_history_by_period`); the selected range is saved in an `acct_chart_period` cookie, read server-side on page load (`page_routes.account_detail_page`) to pick the initial dataset, so it carries over to every other account.
- **Account Price Scraper** (`account_scraper_engine.py`): generic URL + CSS-selector price feed for `House`/`Pension` accounts, added June 2026, replicating Ghostfolio's "manual asset" scraper. Configured per-account from the Accounts page (tile gear icon / detail page) — not the Settings page — via `PUT /accounts/{id}/scraper-config` (URL, selector, optional JSON headers, daily run time, enabled flag); a `Test` action validates without persisting, a `Scrape Now` action runs the saved config immediately. Each scraper-enabled account gets its own dynamic APScheduler job (`account_scraper_{account_id}_job`, registered/unregistered by `scheduler_jobs.register_account_scraper_job`/`unregister_account_scraper_job`, matched in the manifest via `scheduler_manifest._DYNAMIC_ACCOUNT_SCRAPER_RE`) at the account's own time in `USER_TIMEZONE`, with no `day_of_week` restriction. Extracted prices land in `account_price_history`. For `House`, the latest price *is* the account's equity value (`accounts_engine._equity_value_for_account`) — there is no holdings/transaction concept at all. For `Pension`, the price values a single synthetic holding represented internally as ticker `PENSION-{account_id}` (never shown in the UI) so the existing average-cost ledger needs no changes; `accounts_engine._current_price_map()` resolves that ticker from `account_price_history` instead of `stock_signals`. Two dedicated actions build that holding: `record_pension_contribution()` ("Pay In" — amount ÷ that date's price = units bought) and `record_pension_fee()` ("Admin Fee" — units-before from the ledger minus the units-after the operator reads off the provider's portal = units removed and their cost). Both create `Buy`/`Sell` rows with `update_cash=False`, since a Pension account has no cash sub-ledger.
- **Backup & Recovery** (`backup_engine.py`): self-hosted backup/restore, added June 2026, configured at Settings → Backup & Recovery (right-hand column). Each run writes one timestamped `backup_YYYYMMDD_HHMMSS.tar.gz` containing any combination of `data/` (excluding `analysis.db`, via a `tarfile` member filter so the Data and Database components never overlap), `models/`, and `data/analysis.db` itself, independently toggled. Destination is a local folder (default `backups/`, gitignored) or an NFS share, mounted into a scratch mountpoint under `data/.nfs_backup_mount` for the duration of the run and unmounted afterward via two fixed root-owned wrapper scripts (`/usr/local/sbin/quant-backup-nfs-mount` / `-umount`), invoked as `sudo -n` — mounting requires root, and the app itself never runs as root, so this is delegated through a narrowly-scoped passwordless sudoers rule (operator-installed, not part of the repo) that grants only those two exact script paths, not `mount`/`umount` in general. Without that sudoers rule installed, NFS backups fail with a clean error rather than crashing; Local Folder backups are unaffected. Retention keeps the N most recent archives (sorted by filename, which embeds the timestamp). Every run — scheduled (`backup_job`, day-of-week + time) or manual ("Run Backup Now") — is logged to `backup_history` with `trigger_type` (`scheduled`/`manual`) and dispatched via `notification_engine.notify("backup_status", ...)`. Restore (`POST /api/backup/restore`, gated behind `X-Confirm-Token`) extracts the chosen archive back under `BASE_DIR`, relying on the archive's own internal paths to land correctly — destructive; the operator is expected to restart the service afterward so in-memory caches reload the restored data.
- **Home Assistant Integration** (`Stock_Analysis_Project_ha_integration/`): a companion HACS custom component, added July 2026, that polls this app's own API to surface portfolio totals and system/market health as native Home Assistant sensors. As of July 2026 it is also published as its own independent git repository (https://github.com/alfwro13/Stock_Analysis_Project_ha_integration, with its own CI) in addition to remaining a subfolder of this repo's own checkout — it has its own `AGENTS.md` and `task_prompt.md` (the latter local-only, gitignored from its own published repo) — read both before touching anything it depends on. It currently consumes exactly 7 endpoints: `GET /api/accounts/portfolio-totals` (`accounts_engine.portfolio_totals()`), `POST /api/accounts/refresh-now`, `GET /api/system/market-status`, `GET /api/accounts/list-with-metrics` (`accounts_engine.account_metrics_list()` — Phase 2), `GET /api/accounts/holdings-list` (`accounts_engine.holdings_with_metrics_all_accounts()` — Phase 3), `POST /api/accounts/holding-price-limit` (`accounts_engine.set_holding_price_limit()` — Phase 3), and `GET /api/accounts/other-accounts-list` (`accounts_engine.other_accounts_list()` — Phase 4, Pension/House account sensors). All 4 planned phases have now shipped. **Rule:** any change to the response schema, authentication, or behavior of an HA-integration-consumed endpoint must be checked against `Stock_Analysis_Project_ha_integration/` in the same change — additive-only field changes are safe (the integration reads fields defensively via `.get()`), but renaming/removing a field, changing an endpoint's path, or changing its auth requirements is a breaking change requiring an integration-side update (`custom_components/stock_analysis_project/api.py` at minimum, likely `sensor.py`/`binary_sensor.py`/`number.py` too) in lockstep, not a follow-up task. This also applies to the supporting infrastructure these endpoints depend on: the `account_value_history_currency` table (backs `portfolio_twr_ex_fx()`), the `account_performance_cache` table + `refresh_performance_cache()` (backs `account_metrics_list()`'s cash/equity/gain/MWRR fields), the `holding_price_limits` table (backs `holdings-list`'s `low_limit_*`/`high_limit_*` fields), the `scheduler_manifest.JOB_GRAPH["ha_refresh_now_source"]` entry, and the `notification_engine.NOTIFICATION_SOURCES["ha_refresh_now_status"]` entry. Cross-reference: `Stock_Analysis_Project_ha_integration/AGENTS.md` (integration-side rules, the X-API-Key auth model, the coordinator/switch/number/button interaction contract, the dynamic per-item entity + config-toggle patterns, the `[NEEDS REVIEW]`-flagged coupling to `DataUpdateCoordinator` internals that must be re-verified after any Home Assistant core upgrade) and `Stock_Analysis_Project_ha_integration/task_prompt.md` (the phased Phase 1-4 build-out plan, so a future session knows what's shipped vs. planned before assuming an endpoint doesn't exist yet).
