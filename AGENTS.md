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

## Reference Index — read on demand

The detailed reference material lives in `agents/`. The `agents/` files are part of AGENTS.md: the same rule applies to them — do not edit them yourself; propose changes to the operator (see "AGENTS.md (this file)" under Documentation Maintenance).

`agents/` and `audit/` are local, Git-ignored directories shared with feature worktrees through symlinks. Keep those symlinks in place for the entire task. If the operator approves an edit to either directory, edit the shared file through the link; do not replace the link with a worktree copy or stage those local files in a PR. `AGENTS.md` itself is tracked and belongs on the feature branch.

**How to use this index:** before you write or change code, read — in full — every file below whose "Read when" condition matches your task. If you are unsure whether a condition applies, read the file. A one-line stub in the rule list below is a pointer, not the rule: do not act on a stubbed rule without reading its file.

| File | Read when |
|---|---|
| `agents/DIRECTORY_LAYOUT.md` | You need to know where a module, template or JS file lives, or before creating any new file. |
| `agents/DATABASE_SCHEMA.md` | You touch any SQLite table, query, migration or `db/` module. |
| `agents/RULES_DATA_CACHING_HELPERS.md` | Rules 3, 15, 16, 17 — you fetch or cache external data, coordinate an on-demand refresh, change FX or historical-data freshness policy, decide which tickers get fetched, compute a value another engine may already compute, or write ANY new utility/calculation function (always read before adding a helper). |
| `agents/RULE_12_CENTRAL_ENGINES.md` | Rule 12 — you touch time/timezone, Yahoo Finance, scheduling, notifications, persisted scikit-learn models, font sizes, price scraping, backup, Pattern Detection or the market ticker registry; you add or split files; ALWAYS for the POST-TASK AUDIT central-engine compliance check. |
| `agents/RULE_12_DATA_PITFALLS.md` | Rule 12, continued — you touch daily-bar completeness, live-quote or price freshness, ticker-list filtering before Yahoo calls, `roe`/`debt_to_equity` units, "fill in the actual" backfill loops, or ticker-native vs transaction currency. |
| `agents/RULES_CONCURRENCY_ALERTS_DEPENDENCIES.md` | Rules 10, 19, 20 — you add sleeps, retries, locks or blocking calls inside an `async def` route; you add or rename a `Server-Timing` request stage; you touch alert firing, dedup or cooldown logic; you change `requirements.txt`, `requirements.lock` or any dependency. |
| `agents/RULES_FRONTEND_UI.md` | Rules 11, 18 and Settings page structure — you touch any template, page, CSS, JS, Plotly chart, DataTables column or Settings card. |
| `agents/TIME_AND_TIMEZONE.md` | You touch datetimes, market hours, holidays, `CronTrigger`s, or config schedule windows (`START_TIME`/`END_TIME`). |
| `agents/TOOLS_AND_REPORTS_MENUS.md` | You add or change a page under `/tools` or `/reports`, or work on any tool listed there (Pattern Detection, Pairs Spread, Monte Carlo, ...). |
| `agents/SUBSYSTEM_ANALYTICS_AND_MONITORING.md` | You touch X-ray, Portfolio Heat Index, the intraday orchestrator, the Workflow Monitor, the Portfolio Tearsheet or embed mode. |
| `agents/SUBSYSTEM_MARKETS_PAGE.md` | You touch the Markets page, Market Pulse or the market ticker registry. |
| `agents/SUBSYSTEM_ACCOUNTS_AND_POSITIONS.md` | You touch Built-in Accounts, Position Targets (`holding_price_limits`), the Account Price Scraper or UK Treasury Bills. |
| `agents/SUBSYSTEM_BACKUP_RECOVERY.md` | You touch Backup & Recovery (`backup_engine.py`). |
| `agents/SUBSYSTEM_ALERT_REFEREE.md` | You touch the Alert Confidence Referee or the Trap Monitor alert path. |
| `agents/SUBSYSTEM_HOME_ASSISTANT.md` | You touch any `/api/accounts/*` endpoint, `accounts/portfolio_metrics_engine.py`, `account_performance_cache`, `account_value_history_currency`, `holding_price_limits` or `api.deps.API_KEY_ROUTES` — the Home Assistant integration depends on them and a breaking change needs a lockstep integration-side update. Adding or removing an endpoint the integration calls means changing `API_KEY_ROUTES` (see **API key scope** in `assets/api_reference.md`). |
| `agents/SUBSYSTEM_MCP.md` | You touch the `mcp_server/` package, the `/mcp` endpoint, any MCP tool, prompt or resource, the Settings → MCP Server card, the investment policy file (`data/investment_policy.md`), or the `claude_mcp_server_source` / `investment_policy_source` `JOB_GRAPH` entries. It is the core and lists the four topic files below: read it first, then the topic files for the area you touch. |
| `agents/SUBSYSTEM_MCP_TOOLS_PORTFOLIO.md` | You touch `get_accounts`, `get_holdings`, `get_xray`, `get_heat_index`, `get_market_regime`, `get_ticker_snapshot`, `get_watchlist` or `pretrade_check`. |
| `agents/SUBSYSTEM_MCP_TOOLS_ALERTS_CALENDAR_DISCOVERY.md` | You touch `get_alerts`, `get_news`, `get_calendar`, `get_market_report`, `screen_universe` or `find_candidates`. |
| `agents/SUBSYSTEM_MCP_AUDIT_AND_PUBLIC_HOST.md` | You touch `mcp_audit_log`, the MCP rate limits or lockout, `mcp_server/public_host.py`, `PublicHostGuard`, or the `MCP_PUBLIC_HOST` / `MCP_REMOTE_*` settings. |
| `agents/SUBSYSTEM_MCP_OAUTH_AND_REMOTE_ACCESS.md` | You touch the OAuth server (`mcp_server/oauth*.py`, `db/mcp_oauth.py`), the pairing window or the Remote Access panel, grants or revocation, the access token life or restart warning, or the Go-live runbook. |
| `agents/RULE_22_ADMIN_AUTH_AND_MCP_ACCESS.md` | Rule 22 — you add or change an admin or state-changing route, step-up (`require_step_up`), `X-API-Key` / `API_KEY_ROUTES`, a login, password or reset route, the MCP Server's keys or OAuth, `MCP_PUBLIC_HOST` or `PublicHostGuard`. |
| `agents/RULES_NOTIFICATIONS_AND_ERROR_TEXT.md` | Rules 9 and 23 — you send a notification, add an alert source, touch `notification_engine` or Nextcloud Talk, return an error from a route, add a log handler, or log a URL, token or exception. |
| `agents/TESTING.md` | You write or change a test, fixture, marker or `conftest.py`, add a `run_*` job runner, or need the full-suite cadence. |
| `agents/DOCUMENTATION_MAINTENANCE.md` | You add or change a feature, endpoint, table, config key, glossary term, Learning card, Portfolio/Watchlist column or README entry. |
| `agents/CODING_GUIDELINES_DETAIL.md` | You write UI text, CSS or JS (tooltips, font sizes, DataTables), a request model, a column that can be missing, or code that shows data that looks wrong. |

---

## Key Architecture Rules

1. **Dual-storage:** Relational metadata → SQLite. Heavy time-series → Parquet. Never swap these.
2. **Self-healing:** If Yahoo Finance fails, fall back to local Parquet/JSON cache — never crash the server.
3. **Priority arbitration:** Portfolio/Watchlist/Account Transactions > LSE universe > US universe. → **moved:** `agents/RULES_DATA_CACHING_HELPERS.md` — read it when deciding which tickers are fetched or touching `data_engine.py`/universe logic.
4. **No LLM for sentiment:** Market sentiment uses FinBERT locally. `ai_prompts/ai_engine.py` generates prompts for *external* LLMs but is not itself an LLM.
5. **APScheduler only:** Do not introduce external cron jobs. All scheduled work is wired through `scheduler_engine.py` (APScheduler setup, `reload_scheduler`, `start_scheduler`). Job runner functions live in `scheduler_jobs.py`; the job manifest and label helpers live in `scheduler_manifest.py`; the Workflow Monitor graph logic lives in `scheduler_monitor.py`. All three are re-exported from `scheduler_engine` so external callers need not change their imports.
6. **CSRF + session auth:** All POST endpoints are protected. Session cookies only. See `auth.py`.
7. **Workflow manifest:** Every `scheduler.add_job(... id=X)` must have a matching `JOB_GRAPH` entry in `scheduler_manifest.py` declaring its `produces`/`consumes` data artifacts (dynamic ids matched in `_resolve_manifest`). The Workflow Monitor derives its dependency graph and conflict detection from these; the manifest-completeness test fails if a registered job is missing.
8. **One canonical name per feature — no mixed terminology.** Every scheduled job, engine, page, tool, metric, signal, config option, and feature has exactly **one** user-facing name, and that *same* wording must be used everywhere it appears: the Settings UI (configuration panels **and** the Master APScheduler Matrix), the Workflow Monitor, the System Diagnostics panel, the glossary (`templates/glossary.html`), the asset docs (`assets/`), and the `README.md`. **Never invent a new display name in one place when the thing is already named elsewhere, and never show a code-derived name (e.g. a config key like `ML_TRAINING` rendered as "Ml Training") in one surface while a descriptive name is used in another.** When you add or rename anything user-facing, grep the whole app for the old/related wording and update every surface in the same change. If a single Settings panel controls several jobs (or one job spans several panels), that mismatch must be resolved — not papered over with ad-hoc per-place variants.

   **For scheduled jobs the single source of truth is `scheduler_manifest.JOB_GRAPH[job_id]["label"]`** (re-exported as `scheduler_engine.JOB_GRAPH`; it equals the Settings panel wording). Surfaces that are keyed by config key (the Master Matrix, the diagnostics last-run map) must resolve their display text through `scheduler_engine.CONFIG_KEY_TO_JOB` + `job_label()`/`scheduler_display_names()` — never by title-casing the config key. The Active-Jobs panel name comes from `_mark_job_started(job_label("<job_id>"))`, never a hardcoded literal. **Code identifiers (job ids, `run_*` functions, config keys, engine module/class names) are deliberately *not* renamed to match** — instead, every engine module whose code name differs from its GUI name carries a top-of-module comment `# GUI name: "<name>". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.` so a reader knows what the user calls it. Add that comment whenever you create a job whose code name differs from its GUI label.

9. **All notifications go through the unified router.** Every user-facing notification — scheduled-job status and all alerts — is dispatched via `notification_engine.notify(source, message_type, message_text, ...)`. Never call `nextcloud_talk.send_text_message()` or `INSERT` into `system_notifications` from a feature engine (the password-reset link in `api/routes_auth.py` is the one exception). A new alert source is added to `notification_engine.NOTIFICATION_SOURCES`; dedup and cooldown stay in the engine, and a failing Talk delivery never defeats dedup. → **moved:** `agents/RULES_NOTIFICATIONS_AND_ERROR_TEXT.md` — read it before sending a notification, adding an alert source or touching Nextcloud Talk.

10. **Background jobs must never starve the web-server thread pool.** → **moved:** `agents/RULES_CONCURRENCY_ALERTS_DEPENDENCIES.md` — read it before adding any sleep/retry/lock/blocking call, or calling blocking code from an `async def` route.

11. **Bootstrap 5 front-end on `base.html` — migration complete.** → **moved:** `agents/RULES_FRONTEND_UI.md` — read it before creating or changing any page, template, CSS or JS.

12. **AI manageability & central-engine compliance.** → **moved:** `agents/RULE_12_CENTRAL_ENGINES.md` — read it (and `RULE_12_DATA_PITFALLS.md` for price/data-freshness work) before touching any central-engine concern, and always during the POST-TASK AUDIT.

13. **Every subsystem must be visible in the Workflow Monitor.** When a new engine, page, or feature produces or consumes any data artifact already tracked in `scheduler_manifest.JOB_GRAPH` (or starts a new artifact chain other jobs will read), it must get a `JOB_GRAPH` entry in the same change — even if it isn't a scheduled job. Non-scheduled processes (manual data entry, external integrations) get a `non_job: True` or `category: "external"` entry with accurate `produces`/`consumes` so the graph shows where data actually originates. An `external` node may declare `consumes` as well as `produces` (the MCP Server reads artifacts and produces only its own audit log, `mcp_audit_log`, which Database & File Maintenance consumes to prune it); `scheduler_monitor._build_node` keeps both. Don't leave this for a later audit pass: a subsystem missing from the graph hides where its data originates.

14. **Built-in Accounts is the primary portfolio/watchlist source. Ghostfolio is opt-in only.** `accounts.accounts_engine.get_combined_holdings()` is the canonical source for all portfolio ticker lists and position data (shares, cost basis, account membership). `db.database.get_watchlist_tickers()` is the canonical source for watchlist tickers. Every engine, scheduled job, API route, and page that needs "what's in the user's portfolio/watchlist" **must** call one of these two functions — never read `data/portfolio.json` or `data/watchlist.json` directly.

   `portfolio.json` and `watchlist.json` are **Ghostfolio output files** written only by `accounts/ghostfolio_sync.py` when `GHOSTFOLIO_ENABLED = True` (disabled by default). `accounts.accounts_engine.get_combined_holdings()` already merges Ghostfolio holdings (when the file exists) with built-in Trading account holdings, so calling it is always correct whether Ghostfolio is on or off.

   **Portfolio and Watchlist lists are exclusive:** when a feature needs them as separate lists, use `db.helpers.get_portfolio_tickers()` and `db.helpers.get_watchlist_only_tickers()` (Watchlist tickers you do not hold) — a ticker both held and watched belongs to Portfolio only. `get_portfolio_watchlist_tickers()` is the union.

   **Bypasses to flag as bugs:** any `open(PORTFOLIO_PATH)`, `_load_json(PORTFOLIO_PATH)`, `get_tickers_from_json(PORTFOLIO_PATH, ...)`, or `engine.portfolio` attribute access outside `accounts/accounts_engine.py` and `accounts/ghostfolio_sync.py` will silently return an empty portfolio when Ghostfolio is disabled.

15. **Never let a fetched value die with the engine that fetched it — share it via a timestamped cache.** → **moved:** `agents/RULES_DATA_CACHING_HELPERS.md` — read it before adding any fetch of external data.

16. **One canonical function per shared calculation — never recompute the same concept independently in multiple places.** → **moved:** `agents/RULES_DATA_CACHING_HELPERS.md` — read it before writing any calculation another engine may already do.

17. **Helper files are the canonical home for shared utility/calculation logic — check them before writing new logic, and extend rather than duplicate.** → **moved:** `agents/RULES_DATA_CACHING_HELPERS.md` — read it before writing ANY new helper/utility function.

18. **Uniform Plotly chart conventions.** → **moved:** `agents/RULES_FRONTEND_UI.md` — read it before creating or changing any Plotly chart, fullscreen button or DataTables column picker.

19. **Alert dedup gates must never auto-fire solely because a calendar day rolled over.** → **moved:** `agents/RULES_CONCURRENCY_ALERTS_DEPENDENCIES.md` — read it before touching any alert firing, dedup or cooldown logic.

20. **Dependency updates are CI-gated, documented, and self-verified at runtime.** → **moved:** `agents/RULES_CONCURRENCY_ALERTS_DEPENDENCIES.md` — read it before changing `requirements.txt`, `requirements.lock` or any dependency.

21. **Persisted scikit-learn estimators use the shared compatibility helper.** Write with `model_compatibility_engine.dump_sklearn_artifact()` and load with `load_sklearn_artifact(path, retraining_job_id)`; never call `joblib.dump()` or `joblib.load()` for a persisted estimator. A job that scores many items loads the model once, never per item. → **moved:** `agents/RULE_12_CENTRAL_ENGINES.md` ("Rule 21", at the end) — read it before persisting or loading any model.

22. **State-changing admin routes require a password step-up, and nothing that authorises an action is ever rendered into a page.** Routes that change the host or its credentials (settings save, git-pull, restart, backup restore, secret and credential save or rotation) depend on `api.deps.require_step_up`, called from the browser with `stepUpFetch()`; never add a shared token, header secret or page-embedded value as a gate. `X-API-Key` is accepted only for the `(method, path)` pairs in `api.deps.API_KEY_ROUTES` (the Home Assistant routes): never widen it, never list `/mcp` there. The MCP Server (`/mcp`) is read-only with its own named Bearer keys; on `MCP_PUBLIC_HOST` only OAuth tokens authenticate and `PublicHostGuard` serves exactly eight routes. No write, setting change or step-up-gated action is ever an MCP tool, and no unauthenticated state-changing admin route may exist (no config flag opens one). Every route that verifies the dashboard password calls `enforce_login_lockout()` before and `record_password_attempt()` after; every credential change goes through `api.routes_auth._store_new_password()`. → **moved:** `agents/RULE_22_ADMIN_AUTH_AND_MCP_ACCESS.md` — read it before adding or changing an admin route, an API-key route, a login/password/reset route, or anything in the MCP auth, OAuth or public-host path.

23. **Error text shown to a client is fixed, and logs never carry a raw line break or a secret query value.** A route never returns `str(e)`, a subprocess's output or a library's exception text from a bare `except Exception`: log the detail and answer with `api.deps._error_500()`, a fixed message or a typed exception with curated text. Every log handler uses `log_config.SafeFormatter`; configure console logging only through `log_config.configure_console_logging()`. Tokens, OAuth codes and secrets never reach a log line, notification or page. Job-status text may carry raw exception text. → **moved:** `agents/RULES_NOTIFICATIONS_AND_ERROR_TEXT.md` — read it before changing a route's error handling, adding a log handler, or logging a URL, token or exception.

---

## Running the App

```bash
source venv/bin/activate
python main.py          # starts on http://localhost:8090
```

First boot auto-creates `config.json` and initialises the DB schema.

---

## Testing

Run the targeted test files for what you changed (`pytest tests/test_foo.py`) before marking work done; CI runs the full suite on every push and PR, so run `./run_tests.sh` locally only if the operator asks or CI fails in a way you need to reproduce (once per task at most). Do not mock the database: tests use a real in-memory SQLite shared by the whole session, so a test file that writes to it declares `pytestmark = pytest.mark.restore_tables("table", ...)`. A test never reaches Yahoo (an autouse guard fails it); use the `offline_yahoo` fixture or patch the `yahoo_engine` method. A new `run_*` job runner in `scheduler_jobs.py` needs a test that calls the runner itself. Tests live in `tests/` (a package's tests in `tests/<package>/`); fixtures are in `tests/conftest.py`. → **moved:** `agents/TESTING.md` — read it before writing or changing a test, fixture or marker, adding a job runner, or deciding how much to run.

## API Conventions

- Base path: `/api`
- All responses: `{ "status": "success"|"error", "message": "..." }`
- Heavy operations (ML training, data scans) return immediately and run as background tasks. Progress is visible in the Notifications tab (`GET /api/notifications/latest`).
- Full endpoint reference: [assets/api_reference.md](assets/api_reference.md)

---

## Documentation Maintenance

Every code change that adds, removes, or significantly alters a feature **must** be accompanied by documentation updates in the same task. Do not mark work done until these steps are complete. The surfaces: the glossary (`templates/glossary.html`; every new term-box also needs a matching card in `learn_cards_seed.CARDS`, enforced by `tests/test_glossary_learn_seed.py`), `OPTIONAL_COLUMNS` in `table_columns_helpers.py` for a new per-ticker value, the matching `assets/*.md` (always `assets/api_reference.md` for any `/api/*` change and `assets/db_schema_and_architecture.md` for any table change), and `README.md` (user-facing, high-level). → **moved:** `agents/DOCUMENTATION_MAINTENANCE.md` — the full checklist for each surface; read it whenever you touch docs, the glossary or a column registry.

### Current-state documentation

`assets/*.md`, `README.md`, the glossary partials and AGENTS.md (with `agents/`) describe what the system does now and why. They never record when or how it changed.

- **Never write:** dates, PR or issue numbers, audit or ticket IDs (`LR-nn`, `SEC nn/27`, `PERF-n`, `MCP-nn`, `BUG-nn`), "added/fixed/found/decided on <date>", "operator decision, <date>", "Last Updated" lines, or change-narrative wording such as "no longer", "now", "previously", "used to", "originally", "remain for later steps", "replaces the old …", "unchanged" (about an earlier version). The commit message and PR description carry that; git history is the changelog.
- **Keep the reason a rule exists, as a standing fact** ("unpickling per item stalls the event loop"), not as an incident ("found on <date>, a run took 25-45 s").
- **A removed feature leaves no "removed" note.** Delete its text; if its absence is itself a rule, state the rule ("there is no unauthenticated reset route").
- Example dates inside sample JSON payloads are fine; a dated record in prose is not.
- `tests/test_docs_current_state.py` fails on ticket IDs and dated attributions in `assets/*.md`, `README.md` and AGENTS.md.

### AGENTS.md (this file)
- If a change warrants an update to AGENTS.md (new engine in the directory layout, new DB table in the schema list, new architectural rule, new external integration), **do not edit this file automatically**.
- Instead, present the proposed addition or change to the operator and wait for explicit approval before applying it.
- Moving or renaming a file or function that AGENTS.md or the `agents/` files name (Directory Layout, rule examples, sub-system notes) leaves stale references. Grep both for the old name and propose the exact edits in the same task; apply them once approved.

---

## Coding Guidelines for Agents

- **Do not add comments** unless the why is genuinely non-obvious.
- **Do not add error handling** for scenarios that cannot happen — trust framework guarantees.
- **Do not create new files** unless strictly necessary; prefer editing existing modules (split one that has grown too big).
- **New modules go into the package they belong to**, never the repository root (the root holds only entry points and cross-cutting core modules); a package's tests live in `tests/<package>/`. Layout: `agents/DIRECTORY_LAYOUT.md`.
- **Do not introduce abstractions** beyond what the task requires.
- **Run the targeted tests** for what you changed and fix failures before marking work done — see Testing.
- **Front end:** tooltips are `<abbr title="One or two sentences.">Label</abbr>` only; styles go in `static/css/styles.css` with no inline `style=`; UI font sizes use the `--font-size-*` variables, never a literal; a template `<script>` over ~50 lines moves to `static/js/` with no Jinja inside; data tables use DataTables Responsive with a `responsivePriority` per column.
- **UK market quirks:** LSE prices may be in pence (GBX) — never remove or simplify that logic.
- **Secrets** live in `.env` only; never hard-code or commit them. **Port** is `8090`; change it only together with `config.json` and `config.py`.
- **Never mask bad data in the display layer:** fix the pipeline or add a `db/schema.py:migrate_db()` migration, never a filter, clamp or guard in `visuals/*.py`, a template or a serialiser.
- **A missing value is `NULL`**, never `0.0`, `""` or another placeholder; a failed fetch leaves the stored value untouched.
- **Request bodies reject non-finite numbers and have size caps:** float fields inherit `api.deps.FiniteModel`, a list or text field a user can grow takes a `max_length` from `constants.py`, and a route that writes `config.json`, the ledger or a Yahoo-bound loop takes a `@limiter.limit`.

→ **moved:** `agents/CODING_GUIDELINES_DETAIL.md` — the full text of every guideline above (reasons, examples, exceptions); read it before writing UI text, CSS or JS, a request model, a nullable column, or a display of data that looks wrong.

---

## External Integrations

| Service | Purpose | Config key |
|---|---|---|
| Ghostfolio | Live portfolio holdings | `GHOSTFOLIO_URL`, `API_TOKEN` |
| Yahoo Finance (`yfinance`) | Price, OHLCV, fundamentals | (public, no key) |
| HuggingFace (`transformers`) | FinBERT NLP sentiment model, loaded at the pinned `constants.NLP_FINBERT_REVISION` from `model.safetensors` only (`use_safetensors=True`, never the pickle `.bin`) with `HF_HUB_OFFLINE=1` in the systemd unit; a revision change needs a manual pre-download (see `assets/nlp_sentiment_architecture.md`); the weights are CC BY-NC-SA 4.0 (non-commercial) | `HF_TOKEN` in `.env` (only used for that download and the Settings token test) |
| Nextcloud Talk | Push alerts | `NEXTCLOUD_*` keys in `.env` |
| MCP Server (inbound) | Read-only MCP endpoint at `/mcp` so Claude Code, Desktop and claude.ai can query the dashboard; Bearer-key auth, no writes (rule 22) | named keys in Settings → MCP Server → Local/LAN Access (`mcp_api_keys` table) |
| FRED / BoE / ONS | Macro indicators | (public) |
| SEC EDGAR | Insider Form 4 filings | (public) |

---

## ML Models (`models/`)

| File | Description |
|---|---|
| `ml_ensemble.joblib` | Primary soft-voting classifier (XGBoost + RF) |
| `quantile_q10.joblib`, `quantile_q90.joblib` | Q10/Q90 quantile models behind the ML upside range |
| `feature_stats.json` | Training feature statistics (mean/std) for the inference drift check; plain data, not a pickle |
| `alert_referee_trapmonitor.joblib`, `alert_referee_confluence.joblib` | Alert Confidence Referee models |
| `*.sklearn-version` | Version sidecars written by `model_compatibility_engine` |

Retrain via Settings → Machine Learning & AI Engine: Run Backfill Now (Historical Data Backfill & Sync), then Run Training Now (Global Model Training).
