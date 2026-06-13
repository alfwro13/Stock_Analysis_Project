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
├── api_routes.py             # All /api/* REST endpoints
├── page_routes.py            # All HTML page routes
├── database.py               # init_db(), schema migrations, SQLite helpers
├── config.py / config.json   # Runtime configuration
├── constants.py              # Global constants
├── scheduler_engine.py       # APScheduler job definitions
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
├── fundamentals_helpers.py   # Shared fundamentals utilities
├── smgb_predictor.py         # SMGB.L morning price predictor (ETF holdings + OLS)
├── visuals.py                # Matplotlib / Plotly chart generation
├── maintenance_engine.py     # DB vacuum, orphan file pruning
├── reports_engine.py         # Quant briefing report generation
├── morning_briefing.py       # Morning briefing assembly + dispatch
├── lunchtime_briefing.py     # Lunchtime briefing assembly + dispatch
├── report_dispatcher.py      # Nextcloud Talk / alert dispatch
├── nextcloud_talk.py         # Nextcloud Talk webhook client
├── portfolio_service.py      # Portfolio aggregation helpers
├── profile_engine.py         # Asset profile cache (sector, country, exchange)
├── time_engine.py            # Central time/timezone module (all market-hours logic)
├── utils.py                  # Shared utilities
│
├── templates/                # Jinja2 HTML templates
├── static/                   # CSS, JS, images
│   ├── css/styles.css        # Global stylesheet (single source of truth for all styles)
│   └── js/
│       ├── csrf.js           # CSRF token helper
│       ├── position_sizing.js
│       └── settings.js       # Settings page JS (2100 lines, extracted from settings.html)
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
| `system_notifications` | Scheduler job log visible in the Settings notifications panel |
| `scheduler_run_log` | Last-run timestamp per APScheduler job ID, plus last-start, last/avg run duration, and last status (success/error) — powers the Workflow Monitor |

Schema changes must go through `database.py:init_db()`.

---

## Key Architecture Rules

1. **Dual-storage:** Relational metadata → SQLite. Heavy time-series → Parquet. Never swap these.
2. **Self-healing:** If Yahoo Finance fails, fall back to local Parquet/JSON cache — never crash the server.
3. **Priority arbitration:** Portfolio/Watchlist > LSE universe > US universe. Delisted tickers are appended to `freetrade_blacklist.json` automatically.
4. **No LLM for sentiment:** Market sentiment uses FinBERT locally. `ai_engine.py` generates prompts for *external* LLMs but is not itself an LLM.
5. **APScheduler only:** Do not introduce external cron jobs. All scheduled work lives in `scheduler_engine.py`.
6. **CSRF + session auth:** All POST endpoints are protected. Session cookies only. See `auth.py`.
7. **Workflow manifest:** Every `scheduler.add_job(... id=X)` must have a matching `JOB_GRAPH` entry in `scheduler_engine.py` declaring its `produces`/`consumes` data artifacts (dynamic ids matched in `_resolve_manifest`). The Workflow Monitor derives its dependency graph and conflict detection from these; the manifest-completeness test fails if a registered job is missing.
8. **One canonical name per feature — no mixed terminology.** Every scheduled job, engine, page, tool, metric, signal, config option, and feature has exactly **one** user-facing name, and that *same* wording must be used everywhere it appears: the Settings UI (configuration panels **and** the Master APScheduler Matrix), the Workflow Monitor, the System Diagnostics panel, the glossary (`templates/glossary.html`), the asset docs (`assets/`), and the `README.md`. **Never invent a new display name in one place when the thing is already named elsewhere, and never show a code-derived name (e.g. a config key like `ML_TRAINING` rendered as "Ml Training") in one surface while a descriptive name is used in another.** When you add or rename anything user-facing, grep the whole app for the old/related wording and update every surface in the same change. If a single Settings panel controls several jobs (or one job spans several panels), that mismatch must be resolved — not papered over with ad-hoc per-place variants.

   **For scheduled jobs the single source of truth is `scheduler_engine.JOB_GRAPH[job_id]["label"]`** (it equals the Settings panel wording). Surfaces that are keyed by config key (the Master Matrix, the diagnostics last-run map) must resolve their display text through `scheduler_engine.CONFIG_KEY_TO_JOB` + `job_label()`/`scheduler_display_names()` — never by title-casing the config key. The Active-Jobs panel name comes from `_mark_job_started(job_label("<job_id>"))`, never a hardcoded literal. **Code identifiers (job ids, `run_*` functions, config keys, engine module/class names) are deliberately *not* renamed to match** — instead, every engine module whose code name differs from its GUI name carries a top-of-module comment `# GUI name: "<name>". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.` so a reader knows what the user calls it. Add that comment whenever you create a job whose code name differs from its GUI label.

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
./run_tests.sh              # full suite (~152 tests)
./run_tests.sh --fast       # skip slow page-render tests
./run_tests.sh --db-only    # DB schema tests only
./run_tests.sh --api-only   # API endpoint tests only
```

Tests live in `tests/`. Fixtures and the test client are in `tests/conftest.py`. Do not mock the database in tests — the suite uses a real in-memory SQLite instance spun up per session.

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
- **Large JS blocks belong in `static/js/`:** If a template `<script>` block exceeds ~50 lines, extract it to a `.js` file (see `settings.html` / `settings.js` as the reference). Use a small inline bootstrap to expose any Jinja-derived values as `window.*` globals, then load the external file with `<script src="/static/js/file.js?v={{ css_version }}">`. Never put `{{ ... }}` Jinja interpolations inside `.js` files.
- **UK market quirks:** LSE-listed stocks may have prices quoted in pence (GBX), not pounds (GBP). The codebase handles this explicitly — do not remove or simplify that logic.
- **Secrets:** All credentials live in `.env` (loaded via `python-dotenv`). Never hard-code tokens or API keys. Never commit `.env`.
- **Port:** Default is `8090`. Do not change it without updating `config.json` and `config.py`.

### Time and Timezone Rules

All time-related code **must** go through `time_engine.py`. Never hardcode timezone strings, market hours, or reset times anywhere else in the codebase.

**Storage:** Always store datetimes in UTC (SQLite text as `"%Y-%m-%d %H:%M:%S"`, Parquet as naive UTC timestamps). Never store local times.

**Display:** Convert to the user's local timezone at the point of display only, using `time_engine.to_local(dt)`, `time_engine.fmt_time(dt)`, or `time_engine.fmt_datetime(dt)`. These read `USER_TIMEZONE` from config automatically.

**Market-window checks:** Use `time_engine.is_market_open(exchange)` or `time_engine.market_window_utc(exchange)`. Never compare `datetime.now(timezone.utc).time()` against a hardcoded `"HH:MM"` string — that pattern is always a bug because the string has no timezone context.

**Per-ticker exchange detection:** Use `time_engine.ticker_exchange(ticker, currency)` — it maps `.L`/GBP→LSE, `.DE`/EUR→XETRA, `.T`→TSE, USD→NYSE, and falls back to `HOME_EXCHANGE` for ambiguous tickers.

**APScheduler jobs:** Use `time_engine.reset_cron_trigger_params(exchange)` to generate `CronTrigger` kwargs for end-of-session resets. Always specify `timezone` in every `CronTrigger` — a trigger without a timezone is implicitly system-local and will break across DST boundaries or server moves.

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
| SMGB.L Morning Price Predictor | `/uk-etf-forecast` | Estimates SMGB.L next-open price using top-10 ETF holdings' US post-close prices + GBPUSD FX + OLS regression with 60-day confidence interval |
| AI Sector Contagion Monitor | `/ai-contagion` | Tracks 10-ticker AI ecosystem (semis + hyperscalers + cloud); 30-day normalised performance, intraday session, rolling 20-day correlation heatmap |
| Market Trap & Recovery Monitor | `/trap-monitor` | Post-crash lifecycle monitor: Bull Trap / Dead Cat Bounce, Bear Trap, Capitulation volume climax, and Wyckoff Accumulation detection across portfolio + proxy basket |

---

## Notable Sub-systems

- **X-ray engine** (`xray_engine.py`): Portfolio risk diagnostics view, added June 2026. Runs as a scheduler job at 19:00. Served at `GET /api/xray`. Renders inline on `portfolio.html` as a same-page swap.
- **Intraday orchestrator** (`intraday_orchestrator.py`): Runs every 5 minutes during market hours. Detects crash/moonshot conditions and fires Nextcloud alerts.
- **Quant briefing** (`reports_engine.py`): Generates a markdown report dispatched each morning via Nextcloud Talk.
- **Workflow Monitor** (`scheduler_engine.py`): Settings-page dependency flow-chart of every scheduled job, added June 2026. Built from the `JOB_GRAPH` manifest (each job declares `produces`/`consumes`; edges derive from artifact intersection) merged with live scheduler state and `scheduler_run_log` durations. Detects scheduling conflicts (overlap with a still-running upstream, backwards ordering, disabled upstream, stale/never-run, last-run error). Served at `GET /api/workflow-monitor/status`; rendered with vendored Mermaid.js (fetched on first boot, gitignored).
- **Embed mode:** Append `?embed=true` to `/portfolio` or `/watchlist` to strip the navbar for iframe integration (e.g. Home Assistant).
