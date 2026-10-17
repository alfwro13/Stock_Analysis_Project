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

The detailed reference material that used to live in this file now lives in `agents/`. It was moved out of this file, not dropped. The `agents/` files are part of AGENTS.md: the same rule applies to them — do not edit them yourself; propose changes to the operator (see "AGENTS.md (this file)" under Documentation Maintenance).

`agents/` and `audit/` are local, Git-ignored directories shared with feature worktrees through symlinks. Keep those symlinks in place for the entire task. If the operator approves an edit to either directory, edit the shared file through the link; do not replace the link with a worktree copy or stage those local files in a PR. `AGENTS.md` itself is tracked and belongs on the feature branch.

**How to use this index:** before you write or change code, read — in full — every file below whose "Read when" condition matches your task. If you are unsure whether a condition applies, read the file. A one-line stub in the rule list below is a pointer, not the rule: do not act on a stubbed rule without reading its file.

| File | Read when |
|---|---|
| `agents/DIRECTORY_LAYOUT.md` | You need to know where a module, template or JS file lives, or before creating any new file. |
| `agents/DATABASE_SCHEMA.md` | You touch any SQLite table, query, migration or `db_*.py` file. |
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
| `agents/SUBSYSTEM_HOME_ASSISTANT.md` | You touch any `/api/accounts/*` endpoint, `portfolio_metrics_engine.py`, `account_performance_cache`, `account_value_history_currency`, `holding_price_limits` or `api_deps.API_KEY_ROUTES` — the Home Assistant integration depends on them and a breaking change needs a lockstep integration-side update. Adding or removing an endpoint the integration calls means changing `API_KEY_ROUTES` (see **API key scope** in `assets/api_reference.md`). |
| `agents/SUBSYSTEM_MCP.md` | You touch the `mcp_server/` package, the `/mcp` endpoint or its auth, any MCP tool, prompt or resource, the investment policy file (`data/investment_policy.md`), or the `claude_mcp_server_source` / `investment_policy_source` `JOB_GRAPH` entries. |

---

## Key Architecture Rules

1. **Dual-storage:** Relational metadata → SQLite. Heavy time-series → Parquet. Never swap these.
2. **Self-healing:** If Yahoo Finance fails, fall back to local Parquet/JSON cache — never crash the server.
3. **Priority arbitration:** Portfolio/Watchlist/Account Transactions > LSE universe > US universe. → **moved:** `agents/RULES_DATA_CACHING_HELPERS.md` — read it when deciding which tickers are fetched or touching `data_engine.py`/universe logic.
4. **No LLM for sentiment:** Market sentiment uses FinBERT locally. `ai_engine.py` generates prompts for *external* LLMs but is not itself an LLM.
5. **APScheduler only:** Do not introduce external cron jobs. All scheduled work is wired through `scheduler_engine.py` (APScheduler setup, `reload_scheduler`, `start_scheduler`). Job runner functions live in `scheduler_jobs.py`; the job manifest and label helpers live in `scheduler_manifest.py`; the Workflow Monitor graph logic lives in `scheduler_monitor.py`. All three are re-exported from `scheduler_engine` so external callers need not change their imports.
6. **CSRF + session auth:** All POST endpoints are protected. Session cookies only. See `auth.py`.
7. **Workflow manifest:** Every `scheduler.add_job(... id=X)` must have a matching `JOB_GRAPH` entry in `scheduler_manifest.py` declaring its `produces`/`consumes` data artifacts (dynamic ids matched in `_resolve_manifest`). The Workflow Monitor derives its dependency graph and conflict detection from these; the manifest-completeness test fails if a registered job is missing.
8. **One canonical name per feature — no mixed terminology.** Every scheduled job, engine, page, tool, metric, signal, config option, and feature has exactly **one** user-facing name, and that *same* wording must be used everywhere it appears: the Settings UI (configuration panels **and** the Master APScheduler Matrix), the Workflow Monitor, the System Diagnostics panel, the glossary (`templates/glossary.html`), the asset docs (`assets/`), and the `README.md`. **Never invent a new display name in one place when the thing is already named elsewhere, and never show a code-derived name (e.g. a config key like `ML_TRAINING` rendered as "Ml Training") in one surface while a descriptive name is used in another.** When you add or rename anything user-facing, grep the whole app for the old/related wording and update every surface in the same change. If a single Settings panel controls several jobs (or one job spans several panels), that mismatch must be resolved — not papered over with ad-hoc per-place variants.

   **For scheduled jobs the single source of truth is `scheduler_manifest.JOB_GRAPH[job_id]["label"]`** (re-exported as `scheduler_engine.JOB_GRAPH`; it equals the Settings panel wording). Surfaces that are keyed by config key (the Master Matrix, the diagnostics last-run map) must resolve their display text through `scheduler_engine.CONFIG_KEY_TO_JOB` + `job_label()`/`scheduler_display_names()` — never by title-casing the config key. The Active-Jobs panel name comes from `_mark_job_started(job_label("<job_id>"))`, never a hardcoded literal. **Code identifiers (job ids, `run_*` functions, config keys, engine module/class names) are deliberately *not* renamed to match** — instead, every engine module whose code name differs from its GUI name carries a top-of-module comment `# GUI name: "<name>". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.` so a reader knows what the user calls it. Add that comment whenever you create a job whose code name differs from its GUI label.

9. **All notifications go through the unified router.** Every user-facing notification — scheduled-job status and all alerts — must be dispatched via `notification_engine.notify(source, message_type, message_text, ...)`. Do **not** call `nextcloud_talk.send_text_message()` or `INSERT` into `system_notifications` directly from a feature engine. Per-source channel routing (log file / in-app / Nextcloud Talk) lives in `NOTIFICATION_ROUTING` (`config.json`), is editable in the Settings **Notification Settings** panel, and falls back to each source's default in `notification_engine.NOTIFICATION_SOURCES`. A new alert source must be added to that registry (with a canonical `label` and parent `job_id`); a new scheduled job automatically gets a routable status row. Dedup/cooldown stays in the engines (`alert_state`) — the router only decides *where* a fired event goes. Exceptions: deep pipeline-progress chatter may still call `database.log_notification()` directly (in-app only), and file-attachment dispatches (the Fear & Greed chart) keep their own upload path gated by their enable toggle. The password-reset link (`api_routes_auth._send_reset_notification`) is also sent directly via `nextcloud_talk.send_text_message()`: routing it through `notify()` would write the one-time link to the log file and in-app notifications, so it must never go through the router.

10. **Background jobs must never starve the web-server thread pool.** → **moved:** `agents/RULES_CONCURRENCY_ALERTS_DEPENDENCIES.md` — read it before adding any sleep/retry/lock/blocking call, or calling blocking code from an `async def` route.

11. **Bootstrap 5 front-end on `base.html` — migration complete.** → **moved:** `agents/RULES_FRONTEND_UI.md` — read it before creating or changing any page, template, CSS or JS.

12. **AI manageability & central-engine compliance.** → **moved:** `agents/RULE_12_CENTRAL_ENGINES.md` — read it (and `RULE_12_DATA_PITFALLS.md` for price/data-freshness work) before touching any central-engine concern, and always during the POST-TASK AUDIT.

13. **Every subsystem must be visible in the Workflow Monitor.** When a new engine, page, or feature produces or consumes any data artifact already tracked in `scheduler_manifest.JOB_GRAPH` (or starts a new artifact chain other jobs will read), it must get a `JOB_GRAPH` entry in the same change — even if it isn't a scheduled job. Non-scheduled processes (manual data entry, external integrations) get a `non_job: True` or `category: "external"` entry with accurate `produces`/`consumes` so the graph shows where data actually originates. An `external` node may declare `consumes` as well as `produces` (the MCP Server reads artifacts and produces none); `scheduler_monitor._build_node` keeps both. Don't leave this for a later audit pass — the Built-in Accounts subsystem (Trading/Pension/House) went unrepresented on the graph for several sessions after it was added before this was caught.

14. **Built-in Accounts is the primary portfolio/watchlist source. Ghostfolio is opt-in only.** `accounts_engine.get_combined_holdings()` is the canonical source for all portfolio ticker lists and position data (shares, cost basis, account membership). `database.get_watchlist_tickers()` is the canonical source for watchlist tickers. Every engine, scheduled job, API route, and page that needs "what's in the user's portfolio/watchlist" **must** call one of these two functions — never read `data/portfolio.json` or `data/watchlist.json` directly.

   `portfolio.json` and `watchlist.json` are **Ghostfolio output files** written only by `ghostfolio_sync.py` when `GHOSTFOLIO_ENABLED = True` (disabled by default). `accounts_engine.get_combined_holdings()` already merges Ghostfolio holdings (when the file exists) with built-in Trading account holdings, so calling it is always correct whether Ghostfolio is on or off.

   **Portfolio and Watchlist lists are exclusive:** when a feature needs them as separate lists, use `db_helpers.get_portfolio_tickers()` and `db_helpers.get_watchlist_only_tickers()` (Watchlist tickers you do not hold) — a ticker both held and watched belongs to Portfolio only. `get_portfolio_watchlist_tickers()` is the union.

   **Bypasses to flag as bugs:** any `open(PORTFOLIO_PATH)`, `_load_json(PORTFOLIO_PATH)`, `get_tickers_from_json(PORTFOLIO_PATH, ...)`, or `engine.portfolio` attribute access outside `accounts_engine.py` and `ghostfolio_sync.py` will silently return an empty portfolio when Ghostfolio is disabled.

15. **Never let a fetched value die with the engine that fetched it — share it via a timestamped cache.** → **moved:** `agents/RULES_DATA_CACHING_HELPERS.md` — read it before adding any fetch of external data.

16. **One canonical function per shared calculation — never recompute the same concept independently in multiple places.** → **moved:** `agents/RULES_DATA_CACHING_HELPERS.md` — read it before writing any calculation another engine may already do.

17. **Helper files are the canonical home for shared utility/calculation logic — check them before writing new logic, and extend rather than duplicate.** → **moved:** `agents/RULES_DATA_CACHING_HELPERS.md` — read it before writing ANY new helper/utility function.

18. **Uniform Plotly chart conventions.** → **moved:** `agents/RULES_FRONTEND_UI.md` — read it before creating or changing any Plotly chart, fullscreen button or DataTables column picker.

19. **Alert dedup gates must never auto-fire solely because a calendar day rolled over.** → **moved:** `agents/RULES_CONCURRENCY_ALERTS_DEPENDENCIES.md` — read it before touching any alert firing, dedup or cooldown logic.

20. **Dependency updates are CI-gated, documented, and self-verified at runtime.** → **moved:** `agents/RULES_CONCURRENCY_ALERTS_DEPENDENCIES.md` — read it before changing `requirements.txt`, `requirements.lock` or any dependency.

21. **Persisted scikit-learn estimators use the shared compatibility helper.** Write with `model_compatibility_engine.dump_sklearn_artifact()` and load with `model_compatibility_engine.load_sklearn_artifact(path, retraining_job_id)` so version sidecars, incompatible-load detection, and automatic retraining remain connected. Do not call `joblib.dump()` or `joblib.load()` directly for a persisted estimator. The helper also records the XGBoost version for any artifact that embeds XGBoost (`.xgboost-version` sidecar) and rejects older-XGBoost pickles, so an `xgboost` bump retrains those models like a `scikit-learn` bump does. The retraining job ID must match the model family registered by `model_compatibility_engine.scan_model_compatibility()` and `scheduler_engine.start_model_compatibility_guard()`. **A job that scores many items with the same model loads it once and reuses it; it never unpickles per item.** Each unpickle allocates enough objects to set off garbage-collection passes that hold the GIL and stall the web server's event loop (rule 10), and the pauses grow with the heap. Found 2026-10-16 (PERF-7): `alert_referee_engine.load_model()` unpickled the referee model for each of the ~119 alerts of a Trap Monitor run, so a run took 25-45 s and caused most of the `event_loop_lag` warnings with no project frame. It now caches the bundle keyed by path, mtime and size (a retrain is picked up, a failed load is never cached); a new per-item model load in a scheduled job or scan loop follows the same pattern or loads before the loop.

22. **State-changing admin routes require a password step-up, and nothing that authorises an action is ever rendered into a page.** Routes that change the host or its credentials (settings save, git-pull, restart, backup restore, secret/credential save and rotation) depend on `api_deps.require_step_up`: a signed, `HttpOnly`, session-bound `stepup` cookie issued for 5 minutes by `POST /api/step-up` after the dashboard password is re-entered. An `X-API-Key` request has no session and cannot obtain one, so API-key scripts cannot call these routes. Never add a shared token, a header secret or a page-embedded value as the gate. The browser side is `stepUpFetch()`; read `agents/RULES_FRONTEND_UI.md` (Settings page structure) before adding a gated route, and update `_GATED_ROUTES` in `tests/test_06_auth.py` and the list under `POST /api/step-up` in `assets/api_reference.md` in the same change. A route that already verifies the current password in its own body (`/api/change-password`) needs no step-up but must be rate-limited.

   **`X-API-Key` is scoped to the Home Assistant routes.** `auth_middleware` accepts it only for the `(method, path)` pairs in `api_deps.API_KEY_ROUTES` and returns 403 for every other route and page. A new endpoint the integration calls must be added there, with a row in the **API key scope** table in `assets/api_reference.md`, in the same change as the integration-side code (`agents/SUBSYSTEM_HOME_ASSISTANT.md` has the steps). Never widen the key to a prefix or a page, and never put a secret-returning or step-up-gated route on the list.

   **The MCP Server (`/mcp`) has its own named keys.** The read-only MCP endpoint (the `mcp_server/` package, mounted at `/mcp`) authenticates with `Authorization: Bearer <key>`, where the key is one of the named keys created in Settings → MCP Server and stored only as SHA-256 hashes in `mcp_api_keys`; `auth_middleware` accepts them only on `/mcp` and `/mcp/*`. It fails closed: every `/mcp` request is refused while no key exists. `X-API-Key` and `API_KEY_ROUTES` never apply to `/mcp`, and the MCP key never authenticates any other route, so a leaked Home Assistant key cannot read the portfolio through MCP and a leaked MCP key cannot call the Home Assistant routes. Never list `/mcp` in `API_KEY_ROUTES`; never expose a write, a setting change or a step-up-gated action as an MCP tool or prompt; create and delete keys only through step-up-gated routes (`POST /api/mcp-keys`, `DELETE /api/mcp-keys/{id}`); a key is shown once, never stored in clear and never rendered back. The endpoint also has an enable toggle, `MCP_SERVER_ENABLED` in `config.json` (default off): while it is off every `/mcp` request answers `503` before any key check, and turning it on is step-up gated (`POST /api/mcp-server/enabled`; turning it off is not).

   **No unauthenticated state-changing admin route may exist, and no config flag or toggle may open one.** Password recovery is the console script (`python reset_admin_password.py`), a `.env` edit, or the one-time link delivered to `ACCOUNT_EMAIL` by SMTP or Nextcloud Talk. The `FORCE_PASSWORD_RESET` flag and `/admin-reset-password` page were removed for letting anyone who could reach the app set the password (SEC 15/27). The self-service reset routes are public by necessity: they reply identically whether or not the email matches, never log the link, invalidate older links when a new one is issued, and are rate-limited.

   **Every route that verifies the dashboard password** (login, step-up, change-password) calls `api_deps.enforce_login_lockout()` before the check and `api_deps.record_password_attempt()` after it, so all of them share the persisted account-wide failure counter (`login_throttle`). **Every route that changes the credentials** goes through `api_routes_auth._store_new_password()`, which raises `SESSION_EPOCH` (signing out every browser) and clears the lockout; a new such route must not write `DASHBOARD_PASSWORD_HASH` itself. New passwords are checked with `auth.password_policy_error()` (12+ characters, not in `tools/common_passwords.txt`).

23. **Error text shown to a client is fixed, and logs never carry a raw line break or a secret query value.** A route never returns `str(e)`, a subprocess's stdout/stderr or a library's exception text from a bare `except Exception`: it logs the detail and answers with `api_deps._error_500()`, a fixed message (per category where the caller needs to tell failures apart), or a typed exception whose text is written for the client (`xray_engine.XRayScopeUnavailable`, an `except ValueError`/`RuntimeError` around code that raises curated messages). A route that is a reachability probe (test email, SMTP, IPv6, token tests) answers every failure with one message so it cannot become a port-scan oracle. Every log handler the app creates uses `log_config.SafeFormatter` (the `app.log` file handler and the `main.py` console handler already do), which writes a CR or LF inside a logged value as `\r`/`\n` and masks `api_key=`, `token=` and `embed_token=` values. Code that logs exception text from a request that carried a secret in its URL (FRED's `api_key`) passes it through `log_config.redact_secrets()`. A secret is never sent in a query string when the upstream API accepts a header, and the uvicorn access log is masked through `log_config.uvicorn_log_config()`; the reverse proxy's own access log is the operator's to configure. `tests/test_log_config.py` covers the formatter and the access filter.

   **Job-status text is the exception.** Job-status notifications (`notify()` / `log_sched_notification`) and `scheduler_run_history.error_detail` carry raw exception text by design: they are the operator's only in-app view of why a job failed and are readable only with a full session cookie (the embed token and the API key cannot reach them). This rule governs route responses, not those channels, so an engine returning `{"status": "error", "message": str(e)}` to a job runner is not a finding. Do not enable Nextcloud Talk routing for job-status sources if that text should stay off an external service.

---

## Running the App

```bash
source venv/bin/activate
python main.py          # starts on http://localhost:8090
```

First boot auto-creates `config.json` and initialises the DB schema.

---

## Testing

Run targeted test files for what you changed (`pytest tests/test_foo.py`) before marking work done. CI runs the full suite on every push and PR; run the full suite locally only if the operator asks or CI fails in a way you need to reproduce:

```bash
./run_tests.sh              # full suite (~3400 tests)
./run_tests.sh --fast       # skip slow page-render tests
./run_tests.sh --db-only    # DB schema tests only
./run_tests.sh --api-only   # API endpoint tests only
```

If you do run the full suite, once per task is sufficient — do not re-run it after every individual fix. `.github/workflows/tests.yml` already runs the full suite on every push to `main` and every pull request (see rule 20), so a pushed/PR'd change gets a second independent run regardless; running it repeatedly locally mid-task mostly burns time (~10 minutes a run) without adding coverage the final local run and CI don't already provide. If you're actively debugging a specific failure and want a fast local signal before the final full run, `./run_tests.sh --fast` or a targeted `pytest tests/test_foo.py` is cheaper than a full run — use judgement rather than defaulting to the full suite on every step.

Tests live in `tests/`. Fixtures and the test client are in `tests/conftest.py`. Do not mock the database in tests — the suite uses a real in-memory SQLite instance spun up per session.

That database is shared by every test in the session, so rows a test inserts stay visible to tests that run later. Delete what a test adds (a fixture or `finally`), and seed rows that respect the writer's invariants (e.g. a `market_pulse_cache` row with `extended_session` must also carry `extended_price`). The default alphabetical order hides leaks, so check a suspected one by running the polluting file ahead of the victim file.

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
- **Every new term-box must also get a Glossary Learning card in the same task.** Add a matching entry to `learn_cards_seed.CARDS` (`term_key`, `section_id`, `term_title` — must exactly match the term-box's title text, `question`, `answer`, exactly 3 `distractors`, `explanation` — the term-box's own `<p>` prose copied verbatim, and `candle_html` if the term-box has a `.candle-display` block); a new glossary section needs a new `learn_cards_seed.LEVELS` entry too. This is not optional busywork — `tests/test_glossary_learn_seed.py` enforces a strict 1:1 mapping between glossary term-boxes and seed cards in both directions, so `./run_tests.sh` fails if a term-box is added without a card (or a card is left behind after a term-box is removed/renamed). See `assets/glossary_learning.md` for the full checklist and card-authoring guidance.

### Portfolio/Watchlist column registry (`table_columns_helpers.py`)
- Whenever a code change adds a new engine, table, or computed field that produces a per-ticker value that could sensibly be shown as a column on the Portfolio or Watchlist page, add it to `OPTIONAL_COLUMNS` in the same task — do not leave newly-available data undiscoverable through the column picker. This mirrors the Glossary rule above: new displayable data gets wired into the relevant user-facing surface as part of the same change, not as a follow-up.
- Pick the correct `fmt` type by checking the actual unit/scale the writer produces (see the central-engine "Fundamentals unit conventions" rule above) — never assume a percentage-looking field is a fraction or vice versa.
- If the new field needs a JOIN the existing Portfolio/Watchlist queries don't already have, add it to `page_data_signal_rows._SIGNAL_JOINS` (and its column to `_SIGNAL_COLUMNS`) — the one join block and column list shared by `fetch_portfolio_signal_rows()` and `fetch_watchlist_signal_rows()`; a field only one page needs goes in that function's own SELECT/JOIN (both pages must offer the same optional-column catalog unless there's a genuine parity-gap reason not to, per the existing `pages=("portfolio",)`/`("watchlist",)` single-page exceptions in `table_columns_helpers.py`).
- **Both signal SELECTs take the page's own ticker list and select only those rows** (shared batched `IN (...)` helper) — never SELECT the universe and filter in Python. Position-sizing FX, the FX status/timestamp/modal and Force Refresh derive their currencies from the rows returned, so an unscoped query makes every page load price and queue refreshes for universe-only currencies (measured on the Watchlist: 4,188 rows / ~890 ms unscoped vs 164 rows / ~13 ms scoped).
- See `assets/configurable_columns.md` for the full registry design and the exact steps to add a column.

### Asset documentation (`assets/`)
- Identify which markdown files in `assets/` describe the area you changed. Update them to reflect the new behaviour, new DB tables, new config keys, new scheduler jobs, or new API endpoints.
- If no existing file covers the new feature, create one only if the feature is substantial enough to warrant standalone documentation (e.g. a new engine, a new sub-system). Otherwise integrate it into the closest related file.
- Always update `assets/api_reference.md` when adding, removing, or changing any `/api/*` endpoint.
- Always update `assets/db_schema_and_architecture.md` when adding or changing DB tables.

### README (`README.md`)
- When a new feature, tool, page, engine, or integration is added, update `README.md` to reflect it. This includes new entries in any feature list, new configuration keys, new dependencies, or changes to how the app is run or installed.
- Do not add implementation detail to the README — keep it user-facing and high-level.

### AGENTS.md (this file)
- If a change warrants an update to AGENTS.md (new engine in the directory layout, new DB table in the schema list, new architectural rule, new external integration), **do not edit this file automatically**.
- Instead, present the proposed addition or change to the operator and wait for explicit approval before applying it.
- Moving or renaming a file or function that AGENTS.md or the `agents/` files name (Directory Layout, rule examples, sub-system notes) leaves stale references. Grep both for the old name and propose the exact edits in the same task; apply them once approved.

---

## Coding Guidelines for Agents

- **Do not add comments** unless the why is genuinely non-obvious.
- **Do not add error handling** for scenarios that cannot happen — trust framework guarantees.
- **Do not create new files** unless strictly necessary; prefer editing existing modules unless the existing files have grown to big in which case consider all options for splitting them into smaler ones
- **Do not introduce abstractions** beyond what the task requires.
- **Run the targeted tests** for what you changed and fix failures before marking work done; CI runs the full `./run_tests.sh` — see the Testing section.
- **Tooltips:** Use `<abbr title="Explanation text.">Label</abbr>` — wrap the label itself, no custom JS tooltip systems, no icon, no `style` attribute on the `<abbr>`. The global CSS in `static/css/styles.css` already applies `text-decoration: underline dotted #666`, `cursor: pointer`, and `color: inherit` to all `abbr` elements. Never override these inline. Keep tooltip text to 1–2 sentences matching existing examples (e.g. Support 1, RSI, ATR).
- **Styles belong in `static/css/styles.css`:** Do not write inline `style="..."` attributes. Check whether a CSS class already exists before adding anything. Only use inline styles in JS-generated HTML (e.g. dynamic `innerHTML`) where class-based styling is impractical, and even then keep it minimal.
- **No hardcoded `font-size` values for UI layout elements in `styles.css`.** All user-visible text sizes must reference a CSS custom property declared in the `:root` block (e.g. `font-size: var(--font-size-body)`). The ten variables are `--font-size-nav`, `--font-size-table` (screener/report tables), `--font-size-dt-table` (Portfolio/Watchlist DataTables — uses an explicit `td`/`th` rule, not inheritance), `--font-size-form`, `--font-size-btn`, `--font-size-section`, `--font-size-body`, `--font-size-h1`, `--font-size-h2`, `--font-size-h3`; their runtime values come from `GET /api/ui-theme.css` which reads `UI_PREFERENCES` from `config.json`. Exception: intentional data-visualisation sizes (large numeric KPI tiles, score displays, chart annotation text) may use explicit `px` values when they are purposely non-configurable.
- **Large JS blocks belong in `static/js/`:** If a template `<script>` block exceeds ~50 lines, extract it to a `.js` file (see `templates/watchlist.html` / `static/js/watchlist.js` as the reference). Use a small inline bootstrap to expose any Jinja-derived values as `window.*` globals, then load the external file with `<script src="/static/js/file.js?v={{ css_version }}">`. Never put `{{ ... }}` Jinja interpolations inside `.js` files.
- **Tables use DataTables Responsive:** New or migrated data tables initialise DataTables with `responsive: true` and explicit per-column `responsivePriority` so the full column set shows on desktop and only the essentials survive on a phone (collapsed columns move to the expandable child row). Keep the client-side full-array data load — do not switch to server-side processing. See `static/js/watchlist.js`.
- **UK market quirks:** LSE-listed stocks may have prices quoted in pence (GBX), not pounds (GBP). The codebase handles this explicitly — do not remove or simplify that logic.
- **Secrets:** All credentials live in `.env` (loaded via `python-dotenv`). Never hard-code tokens or API keys. Never commit `.env`.
- **Port:** Default is `8090`. Do not change it without updating `config.json` and `config.py`.
- **Never mask bad data in the display layer.** If a chart, table, or API response shows incorrect values due to corrupt or misformatted data in the database, the fix must go to the source — either the data pipeline (engine) or a data migration in `db_schema.py:migrate_db()`. Do not add filters, clamps, or guards in `visuals.py`, template code, or API serialisation to hide the bad values. Filtering in the display layer hides the problem from monitoring and leaves incorrect data in the DB silently corrupting other consumers (e.g. `regime_engine.py` reads `us_cpi_inflation` directly for macro regime classification).
- **A missing value is `NULL`, never a neutral placeholder.** A failed or empty fetch must not be persisted as `0.0`, `""` or any other value a reader would take for a real measurement, and a failed call leaves the stored value untouched. Changing what `NULL` means for a column requires auditing every reader of it, including those that carry the latest non-NULL value forward (`grep "IS NOT NULL.*ORDER BY date DESC LIMIT 1"`), since they will silently surface an old value. Found 2026-10-12: Yahoo retired the endpoint behind `Ticker.news`, yfinance returned `[]`, and every sentiment scan stored `0.0` for every ticker and reported success. See `agents/RULE_12_DATA_PITFALLS.md`.
- **Request bodies reject non-finite numbers and have size caps (SEC 23/27).** A request model with a float field inherits `api_deps.FiniteModel` (`allow_inf_nan=False`), so `Infinity`/`NaN`/`1e999` is a 422 and never reaches the database. The setting is not inherited by nested models, so every nested model that carries a float inherits `FiniteModel` itself, and an untyped `dict`/`List[Any]` field takes a `field_validator` calling `api_deps.reject_non_finite` (the `POST /api/settings` schema does both). An untyped Settings block also gets a shape check, not only a finite check (SEC 26/27 · S2-6): `notification_engine.validate_routing_map()` for `NOTIFICATION_ROUTING`, `config.validate_xray_targets()` / `config.validate_meta_scoring()` for `XRAY_TARGETS` / `META_SCORING` (allowed keys come from `DEFAULT_CONFIG`, so a new target needs no second key list), and `log_config.resolve_log_dir()` for `FILE_LOGGING.LOG_DIR`. A CSV importer skips such a row instead of storing `0.0`. A list or text field a user can grow takes a `max_length` from the request-limits block in `constants.py` (e.g. `ACCOUNT_NAME_MAX_CHARS` for `AccountBody.name`), a route that reads an upload caps the bytes it reads, and a route that writes to `config.json`, the ledger or a Yahoo-bound loop takes a `@limiter.limit`. `main._validation_error_handler` returns `loc`/`msg`/`type` only, because echoing the rejected `input` made a rejected `Infinity` a 500. New limits are listed under "Request input limits" in `assets/api_reference.md`.

---

## External Integrations

| Service | Purpose | Config key |
|---|---|---|
| Ghostfolio | Live portfolio holdings | `GHOSTFOLIO_URL`, `API_TOKEN` |
| Yahoo Finance (`yfinance`) | Price, OHLCV, fundamentals | (public, no key) |
| HuggingFace (`transformers`) | FinBERT NLP sentiment model, loaded at the pinned `constants.NLP_FINBERT_REVISION` from `model.safetensors` only (`use_safetensors=True`, never the pickle `.bin`) with `HF_HUB_OFFLINE=1` in the systemd unit; a revision change needs a manual pre-download (see `assets/nlp_sentiment_architecture.md`); the weights are CC BY-NC-SA 4.0 (non-commercial) | `HF_TOKEN` in `.env` (only used for that download and the Settings token test) |
| Nextcloud Talk | Push alerts | `NEXTCLOUD_*` keys in `.env` |
| MCP Server (inbound) | Read-only MCP endpoint at `/mcp` so Claude Code, Desktop and claude.ai can query the dashboard; Bearer-key auth, no writes (rule 22) | named keys in Settings → MCP Server (`mcp_api_keys` table) |
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
