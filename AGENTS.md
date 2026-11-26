# AGENTS.md — Quantamental Portfolio Dashboard

Context for AI coding agents working in this self-hosted **FastAPI** hobby/personal portfolio dashboard (quantitative, fundamental, ML, tail-risk; not a production investment platform).

- **Stack:** Python 3.10+, FastAPI + Uvicorn (`:8090`, configurable), Jinja2 HTML + Plotly.js + DataTables (AJAX), SQLite `data/analysis.db` (WAL), in-process APScheduler (no external cron).
- **Run:** `source venv/bin/activate && python main.py` → `http://localhost:8090` (first boot auto-creates `config.json` and initialises DB schema).

## Reference Index (Read on Demand)

Detailed references live in `agents/`. **Do not edit `AGENTS.md` or `agents/` yourself** — propose edits to the operator and wait for approval.

`agents/` and `audit/` are local, Git-ignored directories symlinked into feature worktrees. Keep symlinks intact for the entire task; edit operator-approved changes through the link (never replace with a worktree copy or stage in a PR). `AGENTS.md` is tracked on the feature branch.

**Protocol:** Before writing/changing code, read in full every matching file below (when unsure, read it). Stubbed rules below are pointers, not the rule — never act on a stub without reading its file.

| File (`agents/`) | Read when touching / doing |
|---|---|
| `DIRECTORY_LAYOUT.md` | Finding where a module/template/JS file lives, or before creating any new file. |
| `DATABASE_SCHEMA.md` | Any SQLite table, query, migration, or `db/` module. |
| `RULES_DATA_CACHING_HELPERS.md` | Rules 3, 15–17: external data fetch/cache, on-demand refresh coordination, FX/historical freshness, ticker fetch priority (`data_engine.py`/universe), shared calculations, or **any** new utility/calculation helper. |
| `RULE_12_CENTRAL_ENGINES.md` | Rules 12 & 21: time/timezone, Yahoo Finance, scheduling, notifications, persisted scikit-learn models, font sizes, price scraping, backup, Pattern Detection, market ticker registry; adding/splitting files; **always** for POST-TASK AUDIT central-engine check. |
| `RULE_12_DATA_PITFALLS.md` | Rule 12 (cont.): daily-bar completeness, live-quote/price freshness, ticker filtering before Yahoo calls, `roe`/`debt_to_equity` units, "fill in the actual" backfill loops, ticker-native vs transaction currency. |
| `RULES_CONCURRENCY_ALERTS_DEPENDENCIES.md` | Rules 10, 19, 20: sleeps/retries/locks/blocking calls in `async def` routes, `Server-Timing` stages, alert firing/dedup/cooldown, `requirements.txt`/`requirements.lock`/dependencies. |
| `RULES_FRONTEND_UI.md` | Rules 11, 18 & Settings structure: templates, pages, CSS, JS, Plotly charts/fullscreen buttons, DataTables columns/pickers, Settings cards. |
| `TIME_AND_TIMEZONE.md` | Datetimes, market hours, holidays, `CronTrigger`s, schedule windows (`START_TIME`/`END_TIME`). |
| `TOOLS_AND_REPORTS_MENUS.md` | `/tools` or `/reports` pages, or listed tools (Pattern Detection, Pairs Spread, Monte Carlo, ...). |
| `SUBSYSTEM_ANALYTICS_AND_MONITORING.md` | X-ray, Portfolio Heat Index, intraday orchestrator, Workflow Monitor, Portfolio Tearsheet, embed mode. |
| `SUBSYSTEM_MARKETS_PAGE.md` | Markets page, Market Pulse, market ticker registry. |
| `SUBSYSTEM_ACCOUNTS_AND_POSITIONS.md` | Built-in Accounts, Position Targets (`holding_price_limits`), Account Price Scraper, UK Treasury Bills. |
| `SUBSYSTEM_BACKUP_RECOVERY.md` | Backup & Recovery (`backup_engine.py`). |
| `SUBSYSTEM_ALERT_REFEREE.md` | Alert Confidence Referee, Trap Monitor alert path. |
| `SUBSYSTEM_HOME_ASSISTANT.md` | `/api/accounts/*`, `accounts/portfolio_metrics_engine.py`, `account_performance_cache`, `account_value_history_currency`, `holding_price_limits`, `api.deps.API_KEY_ROUTES`. Breaking changes require a lockstep Home Assistant integration update; adding/removing integration endpoints requires updating `API_KEY_ROUTES` (see **API key scope** in `assets/api_reference.md`). |
| `SUBSYSTEM_MCP.md` | `mcp_server/`, `/mcp`, MCP tools/prompts/resources, Settings → MCP Server card, `data/investment_policy.md`, `claude_mcp_server_source` / `investment_policy_source` `JOB_GRAPH` entries. Core doc listing the 5 topic files below — read first, then relevant topic files. |
| `SUBSYSTEM_MCP_TOOLS_PORTFOLIO.md` | `get_accounts`, `get_holdings`, `get_xray`, `get_heat_index`, `get_market_regime`, `get_ticker_snapshot`, `get_watchlist`, `pretrade_check`. |
| `SUBSYSTEM_MCP_TOOLS_ALERTS_CALENDAR_DISCOVERY.md` | `get_alerts`, `get_news`, `get_calendar`, `get_market_report`, `screen_universe`, `find_candidates`. |
| `SUBSYSTEM_MCP_AUDIT_AND_PUBLIC_HOST.md` | `mcp_audit_log`, MCP rate limits/lockout, `mcp_server/public_host.py`, `PublicHostGuard`, `MCP_PUBLIC_HOST` / `MCP_REMOTE_*`. |
| `SUBSYSTEM_MCP_WRITE_ACCESS.md` | `propose_watchlist_change`, `propose_undo`, `confirm_change`, `mcp_server/writes.py`, `mcp_server/caller.py`, `mcp_write_log`, `MCP_WRITES_ENABLED`, `write_allowed`. |
| `SUBSYSTEM_MCP_OAUTH_AND_REMOTE_ACCESS.md` | OAuth server (`mcp_server/oauth*.py`, `db/mcp_oauth.py`), pairing window, Remote Access panel, grants/revocation, access token life/restart warning, Go-live runbook. |
| `SUBSYSTEM_GFETCHER.md` | `news/gfetcher/`, `gfetcher_*` tables, `news_articles` Google rows (`feed_source = 'google'`), `SCHEDULING.GFETCHER`, `gfetcher_job`, `gfetcher_probe_source`, `api/routes_gfetcher.py`. |
| `RULE_22_ADMIN_AUTH_AND_MCP_ACCESS.md` | Rule 22: admin/state-changing routes, `require_step_up`, `X-API-Key`/`API_KEY_ROUTES`, login/password/reset routes, MCP keys/OAuth, `MCP_PUBLIC_HOST`, `PublicHostGuard`. |
| `RULES_NOTIFICATIONS_AND_ERROR_TEXT.md` | Rules 9 & 23: notifications, alert sources, `notification_engine`, Nextcloud Talk, route error responses, log handlers, logging URLs/tokens/exceptions. |
| `TESTING.md` | Tests, fixtures, markers, `conftest.py`, new `run_*` job runners, full-suite cadence. |
| `DOCUMENTATION_MAINTENANCE.md` | Adding/changing features, endpoints, tables, config keys, glossary terms, Learning cards, Portfolio/Watchlist columns, README entries. |
| `CODING_GUIDELINES_DETAIL.md` | UI text, CSS/JS (tooltips, font sizes, DataTables), request models, nullable columns, displaying suspect data. |

## Key Architecture Rules

1. **Dual-storage:** Relational metadata → SQLite; heavy time-series → Parquet. Never swap.
2. **Self-healing:** On Yahoo Finance failure, fall back to local Parquet/JSON cache — never crash the server.
3. **Priority arbitration:** Portfolio/Watchlist/Account Transactions > LSE universe > US universe. → `agents/RULES_DATA_CACHING_HELPERS.md`.
4. **No LLM for sentiment:** Sentiment uses local FinBERT. `ai_prompts/ai_engine.py` only generates prompts for *external* LLMs.
5. **APScheduler only (no external cron):** Wired in `scheduler_engine.py` (setup, `reload_scheduler`, `start_scheduler`). Runners live in `scheduler_jobs.py`, manifest/label helpers in `scheduler_manifest.py`, Workflow Monitor graph logic in `scheduler_monitor.py`; all three are re-exported from `scheduler_engine`.
6. **CSRF + session auth:** All POST endpoints are protected; session cookies only (`auth.py`).
7. **Workflow manifest:** Every `scheduler.add_job(... id=X)` needs a matching `JOB_GRAPH` entry in `scheduler_manifest.py` declaring `produces`/`consumes` (dynamic IDs matched in `_resolve_manifest`). Drives Workflow Monitor dependencies/conflicts; enforced by manifest-completeness test.
8. **One canonical user-facing name per feature:** Every scheduled job, engine, page, tool, metric, signal, config option, and feature has one display name used identically across Settings (panels + Master APScheduler Matrix), Workflow Monitor, System Diagnostics, `templates/glossary.html`, `assets/`, and `README.md`. Never invent per-surface names or expose title-cased config keys (e.g. `ML_TRAINING` → "Ml Training"). On add/rename, grep and update all surfaces in the same change; resolve Settings-panel/job cardinality mismatches rather than papering over them.
   - Scheduled-job source of truth: `scheduler_manifest.JOB_GRAPH[job_id]["label"]` (re-exported via `scheduler_engine.JOB_GRAPH`; matches Settings panel). Config-keyed surfaces (Master Matrix, diagnostics last-run) must resolve via `scheduler_engine.CONFIG_KEY_TO_JOB` + `job_label()`/`scheduler_display_names()`. Active-Jobs panel uses `_mark_job_started(job_label("<job_id>"))`, never a literal.
   - Code identifiers (job IDs, `run_*`, config keys, module/class names) are intentionally *not* renamed to match; engines whose code name differs from their GUI name must carry top-of-module comment `# GUI name: "<name>". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.`
9. **Unified notification router:** Dispatch all user-facing notifications (job status + alerts) via `notification_engine.notify(source, message_type, message_text, ...)`. Never call `nextcloud_talk.send_text_message()` or `INSERT` into `system_notifications` from feature engines (sole exception: password-reset link in `api/routes_auth.py`). Register new sources in `notification_engine.NOTIFICATION_SOURCES`; dedup/cooldown stay in the engine and failing Talk delivery never defeats dedup. → `agents/RULES_NOTIFICATIONS_AND_ERROR_TEXT.md`.
10. **Never starve the web-server thread pool in background jobs.** → `agents/RULES_CONCURRENCY_ALERTS_DEPENDENCIES.md`.
11. **Bootstrap 5 front-end on `base.html` (migration complete).** → `agents/RULES_FRONTEND_UI.md`.
12. **AI manageability & central-engine compliance.** → `agents/RULE_12_CENTRAL_ENGINES.md` (+ `agents/RULE_12_DATA_PITFALLS.md` for price/data freshness).
13. **Workflow Monitor visibility for all subsystems:** Any new engine/page/feature producing or consuming tracked `JOB_GRAPH` artifacts (or starting a new chain) must add a `JOB_GRAPH` entry in the same change. Non-scheduled processes use `non_job: True` or `category: "external"` with accurate `produces`/`consumes`. `external` nodes may declare both (e.g. MCP Server reads artifacts and produces `mcp_audit_log`, which Database & File Maintenance prunes; `scheduler_monitor._build_node` preserves both).
14. **Built-in Accounts is primary; Ghostfolio is opt-in only:** Canonical sources are `accounts.accounts_engine.get_combined_holdings()` (portfolio tickers, shares, cost basis, accounts; auto-merges Ghostfolio when present) and `db.database.get_watchlist_tickers()` (watchlist). Never read `data/portfolio.json` or `data/watchlist.json` directly — those are Ghostfolio outputs written only by `accounts/ghostfolio_sync.py` when `GHOSTFOLIO_ENABLED = True` (default off).
    - Separate lists are exclusive: use `db.helpers.get_portfolio_tickers()` and `db.helpers.get_watchlist_only_tickers()` (held+watched belongs to Portfolio only); union is `get_portfolio_watchlist_tickers()`.
    - Flag as bugs outside `accounts/accounts_engine.py` & `accounts/ghostfolio_sync.py`: `open(PORTFOLIO_PATH)`, `_load_json(PORTFOLIO_PATH)`, `get_tickers_from_json(PORTFOLIO_PATH, ...)`, `engine.portfolio`.
15. **Share fetched external values via timestamped cache — never let them die in one engine.** → `agents/RULES_DATA_CACHING_HELPERS.md`.
16. **One canonical function per shared calculation — never recompute independently.** → `agents/RULES_DATA_CACHING_HELPERS.md`.
17. **Check and extend helper files before writing new utility/calculation logic.** → `agents/RULES_DATA_CACHING_HELPERS.md`.
18. **Uniform Plotly chart conventions.** → `agents/RULES_FRONTEND_UI.md`.
19. **Alert dedup gates must never auto-fire solely on calendar-day rollover.** → `agents/RULES_CONCURRENCY_ALERTS_DEPENDENCIES.md`.
20. **Dependency updates are CI-gated, documented, and runtime self-verified.** → `agents/RULES_CONCURRENCY_ALERTS_DEPENDENCIES.md`.
21. **Persisted scikit-learn estimators:** Use `model_compatibility_engine.dump_sklearn_artifact()` and `load_sklearn_artifact(path, retraining_job_id)` — never `joblib.dump()`/`joblib.load()`. Load once per job, never per scored item. → `agents/RULE_12_CENTRAL_ENGINES.md` ("Rule 21").
22. **Admin auth, step-up, API keys & MCP access:**
    - Host/credential-changing routes (settings save, git-pull, restart, backup restore, secret/credential save/rotation) require `api.deps.require_step_up`, called via `stepUpFetch()`. Never render authorising tokens/secrets into pages or use shared header gates. No unauthenticated state-changing admin routes (no config flag opens one).
    - `X-API-Key` is restricted strictly to `(method, path)` pairs in `api.deps.API_KEY_ROUTES` (Home Assistant routes) — never widen or add `/mcp`.
    - `/mcp` uses named Bearer keys (on `MCP_PUBLIC_HOST`, OAuth tokens only; `PublicHostGuard` serves exactly 8 routes). Read-only except confirm-gated Watchlist add/remove/undo via `propose_watchlist_change`, `propose_undo`, `confirm_change` when both credential `write_allowed` and global `MCP_WRITES_ENABLED` are on (both default off; toggling either is step-up gated). No other writes, settings/credential changes, or step-up actions may be MCP tools.
    - Dashboard password verification must call `enforce_login_lockout()` before and `record_password_attempt()` after; all credential changes go through `api.routes_auth._store_new_password()`. → `agents/RULE_22_ADMIN_AUTH_AND_MCP_ACCESS.md`.
23. **Fixed client error text & safe logging:** Never return `str(e)`, subprocess output, or library exceptions from bare `except Exception` — log detail and return `api.deps._error_500()`, a fixed message, or curated typed exception. Every log handler uses `log_config.SafeFormatter`; configure console logging only via `log_config.configure_console_logging()`. Never log, notify, or render tokens, OAuth codes, secrets, or raw line breaks in logs (job-status text may include raw exception text). → `agents/RULES_NOTIFICATIONS_AND_ERROR_TEXT.md`.

## Testing

- Run targeted tests (`pytest tests/test_foo.py`) before marking work done. CI runs the full suite on push/PR; run `./run_tests.sh` locally only if asked or reproducing a CI failure (max once/task).
- Never mock the DB: tests share a session in-memory SQLite, so write tests declare `pytestmark = pytest.mark.restore_tables("table", ...)`.
- Never hit Yahoo (autouse guard fails it): use `offline_yahoo` fixture or patch `yahoo_engine`.
- New `run_*` runners in `scheduler_jobs.py` require a test calling the runner directly.
- Layout: `tests/` (package tests in `tests/<package>/`), fixtures in `tests/conftest.py`. → `agents/TESTING.md`.

## API Conventions

- Base `/api`; responses `{ "status": "success"|"error", "message": "..." }`.
- Heavy ops (ML training, data scans) return immediately and run in background; progress via `GET /api/notifications/latest`.
- Full reference: `assets/api_reference.md`.

## Documentation Maintenance

Update docs in the same task before marking work done: `templates/glossary.html` (every new term-box needs a matching `learn_cards_seed.CARDS` entry, enforced by `tests/test_glossary_learn_seed.py`), `OPTIONAL_COLUMNS` in `table_columns_helpers.py` (new per-ticker values), matching `assets/*.md` (`assets/api_reference.md` for `/api/*`, `assets/db_schema_and_architecture.md` for tables), and `README.md` (high-level, user-facing). → `agents/DOCUMENTATION_MAINTENANCE.md`.

### Current-State Documentation
`assets/*.md`, `README.md`, glossary partials, and `AGENTS.md` (+ `agents/`) describe what the system does and why — never history (git carries history).
- **Forbidden:** dates, PR/issue numbers, ticket/audit IDs (`LR-nn`, `SEC nn/27`, `PERF-n`, `MCP-nn`, `BUG-nn`), "added/fixed/found/decided on <date>", "operator decision, <date>", "Last Updated", change-narrative phrasing ("no longer", "now", "previously", "used to", "originally", "remain for later steps", "replaces the old …", "unchanged" about earlier versions). Example dates inside sample JSON payloads are fine. Enforced by `tests/test_docs_current_state.py` on `assets/*.md`, `README.md`, `AGENTS.md`.
- State rationale as standing fact ("unpickling per item stalls the event loop"), not incident ("found on <date>, a run took 25-45 s").
- Removed features leave no "removed" note: delete text, or state a standing rule ("there is no unauthenticated reset route").

### Updating `AGENTS.md` & `agents/`
- Never edit automatically. Propose additions/changes (new engine, DB table, rule, integration) to the operator and wait for explicit approval.
- When moving/renaming files or functions referenced in `AGENTS.md` or `agents/`, grep both and propose exact edits in the same task; apply once approved.

## Coding Guidelines

- No comments unless the *why* is non-obvious; no error handling for impossible scenarios (trust framework guarantees); no unnecessary abstractions.
- Prefer editing existing modules (split if overgrown) over creating new files. New modules go in their package, never repo root (root = entry points + cross-cutting core); package tests in `tests/<package>/` (`agents/DIRECTORY_LAYOUT.md`).
- Run targeted tests and fix failures before done.
- **Front end:** tooltips = `<abbr title="One or two sentences.">Label</abbr>` only; styles in `static/css/styles.css` (no inline `style=`); font sizes via `--font-size-*` vars (no literals); `<script>` > ~50 lines → `static/js/` (no Jinja inside); tables use DataTables Responsive with `responsivePriority` per column.
- **UK market:** LSE prices may be in pence (GBX) — never remove/simplify that logic.
- **Config/secrets:** Secrets in `.env` only (never hard-code/commit). Port `8090` changes only with `config.json` and `config.py`.
- **Data integrity:** Never mask bad data in `visuals/*.py`, templates, or serialisers — fix the pipeline or add a `db/schema.py:migrate_db()` migration. Missing value = `NULL` (never `0.0`, `""`, or placeholder); failed fetches leave stored values untouched.
- **Request validation:** Float fields inherit `api.deps.FiniteModel`; user-growable lists/text take `max_length` from `constants.py`; routes writing `config.json`, the ledger, or Yahoo-bound loops take `@limiter.limit`.
- Full detail: `agents/CODING_GUIDELINES_DETAIL.md`.

## External Integrations

| Service | Purpose | Config key |
|---|---|---|
| Ghostfolio | Live portfolio holdings | `GHOSTFOLIO_URL`, `API_TOKEN` |
| Yahoo Finance (`yfinance`) | Price, OHLCV, fundamentals | (public) |
| HuggingFace (`transformers`) | FinBERT sentiment at pinned `constants.NLP_FINBERT_REVISION`, `model.safetensors` only (`use_safetensors=True`, never `.bin`), `HF_HUB_OFFLINE=1` in systemd unit; revision changes need manual pre-download (`assets/nlp_sentiment_architecture.md`); weights CC BY-NC-SA 4.0 (non-commercial) | `HF_TOKEN` in `.env` (download + Settings token test only) |
| Nextcloud Talk | Push alerts | `NEXTCLOUD_*` in `.env` |
| MCP Server (inbound) | `/mcp` for Claude Code, Desktop, claude.ai; Bearer auth; read-only except confirm-gated Watchlist changes (Rule 22) | Named keys in Settings → MCP Server → Local/LAN Access (`mcp_api_keys`) |
| FRED / BoE / ONS | Macro indicators | (public) |
| SEC EDGAR | Insider Form 4 filings | (public) |

## ML Models (`models/`)

| File | Description |
|---|---|
| `ml_ensemble.joblib` | Primary soft-voting classifier (XGBoost + RF) |
| `quantile_q10.joblib`, `quantile_q90.joblib` | Q10/Q90 quantile models for ML upside range |
| `feature_stats.json` | Training mean/std for inference drift check (plain JSON, not pickle) |
| `alert_referee_trapmonitor.joblib`, `alert_referee_confluence.joblib` | Alert Confidence Referee models |
| `*.sklearn-version` | Version sidecars written by `model_compatibility_engine` |

Retrain: Settings → Machine Learning & AI Engine → **Run Backfill Now** (Historical Data Backfill & Sync) → **Run Training Now** (Global Model Training).