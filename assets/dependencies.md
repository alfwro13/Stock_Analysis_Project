# Dependency Reference

Maps every entry in `requirements.txt` to what it's actually used for in this
codebase and any upgrade-risk notes. Update this file whenever a dependency
is added, removed, or a major-version upgrade is deliberately taken.

## `requirements.txt` and `requirements.lock`

`requirements.txt` is the hand-edited list of direct dependencies, with the
pins and floors that matter. `requirements.lock` is generated from it by
`uv pip compile requirements.txt --universal --python-version 3.13 --generate-hashes -o requirements.lock`
(the command is also the lock's header). It pins every package, transitive ones
included, with SHA-256 hashes, and is resolved for CPython 3.13 and newer on
every platform (environment markers on the lines that apply to one). CI, the
README install step, the Settings post-pull/restart reinstall
(`utils.pip_install_command()`) and the startup drift check
(`utils.check_requirements_drift()`) all use the lock with
`pip install --require-hashes`, so the packages tested are the packages run, and
a package whose files do not match its hash is refused.

- **Changing a dependency:** edit `requirements.txt`, then regenerate the lock
  with the command above and commit both. Regeneration keeps every version that
  still satisfies `requirements.txt`; add `--upgrade` (or `-P <package>`) to move
  versions. `tests/test_05_config_and_utils.py` fails when the lock does not
  satisfy `requirements.txt` or has an unhashed pin.
- **Automation:** `.github/workflows/refresh-lock.yml` regenerates the lock when
  `requirements.txt` changes on `main` and pushes it to a `refresh-lock` branch,
  with a link in the run summary to open the pull request (the repository setting
  that lets Actions open pull requests is off on purpose; a pull request you open
  yourself runs `tests.yml`). On a Dependabot pull request that changes
  `requirements.txt` it commits the new lock to the PR branch using the
  `LOCK_REFRESH_TOKEN` secret, a fine-grained PAT (this repository only,
  Contents: read and write). It must be stored as a **Dependabot** secret
  (Settings > Secrets and variables > Dependabot), because Dependabot-triggered
  runs cannot see Actions secrets. A push made with the PAT, unlike one made with
  the workflow token, starts `tests.yml` on the PR. Only `dependabot[bot]` pull
  requests run the job, and the PAT is read in the push step only. Once the lock
  commit is on the branch Dependabot stops rebasing it; comment
  `@dependabot recreate` to start over. Dispatched by hand from another branch it
  commits with the workflow token, so close and reopen that branch's pull
  request.
- **Advisories:** `.github/workflows/pip-audit.yml` runs `pip-audit` against the
  lock every Monday and on demand; it fails the run on any advisory. Fix by
  regenerating with `-P <package>` and merging.
- **Python upgrade:** change `--python-version` in the command and regenerate
  once every pinned package publishes wheels for the new interpreter.
- **CI interpreters:** `tests.yml` runs the full suite on Python 3.13 and 3.14
  (`fail-fast` off, so one leg failing does not hide the other). Production runs
  3.13, whose `tarfile` default extraction filter differs from 3.14's, so a
  restore-safety test could pass on 3.14 alone and fail open there.
- **GitHub Actions:** every `uses:` in `.github/workflows/` is pinned to a commit
  SHA with the release tag in a trailing comment. `.github/dependabot.yml` has a
  `github-actions` ecosystem that proposes new SHAs weekly; review them like any
  other Dependabot PR. Never replace a SHA with a moving tag such as `@v7`.

Dependabot itself cannot read this file — it has no concept of "usage docs."
Its own risk controls are configured in `.github/dependabot.yml` (grouping,
labels, schedule). This file exists so a human (or an agent) reviewing a
Dependabot PR can quickly answer "what does this package actually do here,
and what should I test?" without grepping the whole codebase first.

## How to use this file when a Dependabot PR appears

1. Find the package below. Read "Used for" and "What could break."
2. Check "Pin style" — an exact `==` pin means the maintainer wants upgrades
   reviewed one at a time (this is the default Dependabot already respects:
   it proposes a new exact pin rather than loosening it). A `>=` floor means
   any newer version is already allowed at install time; the PR is just
   raising the documented minimum.
3. Run `./run_tests.sh` against the PR branch. If it's a package with no
   direct test coverage (see notes below), do a manual smoke check of the
   feature it powers before merging.
4. CI (`.github/workflows/tests.yml`) already runs the full suite on every
   Dependabot PR automatically — check the PR's checks tab first. CI installs
   `requirements.lock`, not `requirements.txt`, so a Dependabot PR that only
   edits `requirements.txt` is tested against the old versions until the lock is
   regenerated; **Refresh dependency lock** does this on the Dependabot branch
   and the push re-runs CI, so wait for the second run. If the job fails on the
   missing `LOCK_REFRESH_TOKEN` secret, create it (see Automation above) or run
   the workflow by hand on the branch, then close and reopen the PR.

## Web framework & server

| Package | Used for | What could break |
|---|---|---|
| `fastapi` | Core web framework — every route in `*_routes*.py` | Route signature/dependency-injection changes; response model validation |
| `starlette` | ASGI toolkit under FastAPI: request/response objects and the middleware stack in `main.py`. Floor `>=1.7.0` clears published advisories | Middleware, `request.url` and form-parsing behaviour — run the page and API test files and log in once |
| `uvicorn` | ASGI server that runs `main.py` | Startup flags, worker/reload behaviour |
| `starlette-csrf` | CSRF middleware wrapping all POST endpoints (`main.py`) | Any CSRF bypass or token-validation change is a security-relevant upgrade — test login + a POST action manually |
| `slowapi` | Rate limiting (`api_deps.py` limiter used across all `api_routes*.py`) | Rate-limit key function / storage backend signature |
| `python-multipart` | Multipart form parsing (file uploads — CSV imports) | Form-parsing edge cases (large files, encoding) |
| `python-dotenv` | Loads `.env` secrets at startup | `.env` parsing rules |

## Data & market data

| Package | Used for | What could break |
|---|---|---|
| `yfinance` | Underlying Yahoo Finance client, wrapped by `yahoo_engine.py` (the only sanctioned caller per AGENTS.md rule 12) | Yahoo response schema changes (`.info` keys, history columns) — the most failure-prone dependency in this list since it tracks an undocumented external API |
| `pandas` | DataFrame layer for nearly every engine | dtype inference changes, deprecated indexing APIs |
| `pyarrow` | Parquet read/write for `data/historical/*.parquet` and `data/intraday/*.parquet` | Parquet format compatibility with existing on-disk files |
| `requests` | HTTP client used directly by several engines (news, macro, insider, account scraper, Ghostfolio sync) | Session/adapter API, SSL default changes |
| `urllib3` | Not imported directly; HTTP transport under `requests` and `yfinance`. Floor `>=2.8` clears published advisories | Connection-pool and retry behaviour for every outbound fetch |
| `idna` | Not imported directly; host-name encoding under `requests`. Floor `>=3.15` clears published advisories | Non-ASCII host names in scraper and news URLs |
| `ta` | Technical indicators (RSI, MACD, SMA, OBV) in `indicators.py`, `quant_signals.py`, `anomaly_engine.py`, `bull_bear_trap_engine.py`, `crash_engine.py`, `moonshot_engine.py` | Indicator formula changes would silently shift scores — no dedicated regression test compares indicator values against a fixed expected output beyond `tests/test_indicators_equivalence.py` |
| `exchange_calendars` | Holiday-aware trading-day calendar, used only inside `time_engine.py` per AGENTS.md rule 12 | New/changed exchange calendar codes (`time_engine._EXCHANGE_CALENDAR_CODES`) |
| `fake-useragent` | Random User-Agent header generation in `sentiment_engine.py` | API surface (`UserAgent()` constructor / `.random` property) — verified compatible through 2.2.0 |
| `python-slugify` | URL-safe label slugging in `ghostfolio_sync.py` | Minimal risk — pure string transform |
| `lxml` | HTML parsing backend, used directly (`account_scraper_engine.py`'s `lxml.html`) and as `trafilatura`'s/`BeautifulSoup`'s parser | Parsing edge cases on malformed HTML |
| `cssselect` | CSS-selector support for `lxml.html` (Account Price Scraper) | Selector syntax edge cases |
| `html5lib` | Fallback HTML5 parser (used transitively by `trafilatura`/`BeautifulSoup`) | Rarely invoked directly — low risk |
| `trafilatura` | Full-text article extraction for the News Feed (`news_feed_engine.py`); it only parses HTML that `outbound_fetch_helpers.fetch_body()` has already downloaded and never fetches a URL itself | Extraction heuristics changing what counts as "article body" |

## Charts & templating

| Package | Used for | What could break |
|---|---|---|
| `plotly` | All interactive charts (`visuals*.py`, client-side JS) | `fig.to_html()` output format — see AGENTS.md rule 18's server-rendered-chart caveats |
| `matplotlib` | Static Fear & Greed chart image (`sentiment_engine.py`), sent as a file attachment via Nextcloud | Rendering/backend changes for headless (non-GUI) image generation |
| `pillow` | Not imported directly; `matplotlib`'s image backend. Floor `>=12.3` clears published advisories | Rendering of the Fear & Greed PNG |
| `Jinja2` | Template engine for every page in `templates/` | Template syntax changes (rare — Jinja2 is very stable) |
| `markdown` | Renders markdown to HTML for a Learn/glossary surface (`page_routes.py`) | Markdown syntax edge cases |

## Scheduling & system

| Package | Used for | What could break |
|---|---|---|
| `apscheduler` | The only sanctioned scheduler per AGENTS.md rule 12 (`scheduler_engine.py`) | `CronTrigger`/job-store API changes — test that scheduled jobs still register (`tests/test_scheduler_engine.py`'s manifest-completeness check) |
| `pip` | The installer behind the reinstall that runs before a restart when packages are out of date (`api_routes_system.py`) and the startup drift check (`utils.py`). Floor `>=26.2` clears published advisories; the reinstall passes `--require-hashes` and `--resume-retries`, which needs pip 25.1 or newer | Nothing at runtime; the next `pip install --require-hashes -r requirements.lock` upgrades it. A large upgrade (torch pulls several GB of CUDA wheels) is bounded by `constants.DEPENDENCY_INSTALL_TIMEOUT_SECONDS` |
| `psutil` | System diagnostics (CPU/memory/disk) surfaced in Settings → System Diagnostics (`ml_training_engine.py`, `system_check_engine.py`) | Platform-specific metric availability |

## ML / AI

| Package | Used for | What could break |
|---|---|---|
| `scikit-learn` | RF models, cross-sectional scaling (`ml_training_engine.py`, `regime_engine.py`, `macro_ai_engine.py`) | Persisted estimators are not supported across versions. `model_compatibility_engine.py` records a version sidecar, rejects mismatches before unpickling, and queues the affected APScheduler training jobs sequentially after restart. Legacy artifacts without a sidecar fall back to `InconsistentVersionWarning` detection and gain a sidecar after a compatible load. |
| `xgboost` | Gradient-boosted models in the ML ensemble (`ml_training_engine.py`) | Same as scikit-learn — silent accuracy drift risk, not a crash risk |
| `hmmlearn` | GaussianHMM regime classification (`regime_engine.py`, `macro_ai_engine.py`) | Model API stability — this package has historically had breaking API changes across majors; pin conservatively |
| `scipy` | Statistical/optimization routines underpinning `scikit-learn`/`hmmlearn` and used directly in risk calculations; `scipy.optimize.minimize(method="SLSQP")` solves the Portfolio Optimizer's Long-Only mode and, through `portfolio_optimizer_engine.long_only_allocations()`, the Strategy Backtester's rolling Steadiest/Best Reward-for-Risk strategies | Rarely breaking on its own; mostly a transitive-compatibility concern with scikit-learn/hmmlearn pins. After an upgrade, run `tests/test_portfolio_optimizer_engine.py` and `tests/test_strategy_backtest_engine.py` — their hand-computed Long-Only answers and look-ahead checks catch SLSQP behaviour changes |
| `joblib` | Serializes/deserializes every `models/*.joblib` artifact | **Pickle-compatibility risk**: a joblib major upgrade can fail to deserialize models trained under an older version — always retrain (Settings → Machine Learning & AI Engine) after a joblib major-version bump, don't assume old `.joblib` files still load |
| `transformers` | FinBERT sentiment pipeline (`huggingface_engine.py`) — the only sanctioned NLP path per AGENTS.md rule 4 | `pipeline()` call signature, tokenizer defaults. **Note:** this project's dev venv was already running transformers 5.x before the `>=4.37.0` floor was ever raised in requirements.txt — the declared floor had drifted stale relative to what's actually been running and tested for some time |
| `torch` | Backend tensor library required by `transformers`'s pipeline; never imported directly by app code. Floor `>=2.14.1` is well above 2.6, the first release that closes the `torch.load` code-execution bug (CVE-2025-32434) | CUDA/CPU wheel selection, not usually an app-code compatibility risk |

## Testing (dev-only, not shipped)

| Package | Used for | What could break |
|---|---|---|
| `pytest` | Test runner for the whole `tests/` suite | Fixture/marker API changes — would surface immediately as `./run_tests.sh` failing to collect tests |

## Removed as unused

- **`ghostfolio`** (the `ghostfolio` PyPI client) — never imported. `ghostfolio_sync.py`
  talks to the Ghostfolio API with `requests`, so the integration does not need it.
- **`shap`** — never imported, and nothing loads `models/xgb_explainer*.pkl`;
  those files are orphaned artifacts of a retired training step. Both packages were
  dropped from `requirements.txt` (and, with them, `numba`, `llvmlite` and `slicer`
  from the lock) in the supply-chain hardening change (SEC 18/27).

## Vendored Mermaid

`static/vendor/mermaid/mermaid-12.2.0.min.js` is the unmodified Mermaid **12.2.0**
`dist/mermaid.min.js` from the npm package (MIT license), SHA-256
`a8a6a6f805b074a8261a2825c86f99c8054acb3b35f50bb7e24a4f9f2c3c3b9f`. It is loaded
only by the Settings page, where `static/js/workflow_monitor.js` draws the Workflow
Monitor flow-chart. It is committed to git like every other front-end library;
the application no longer downloads it at startup. To upgrade, download the new
`dist/mermaid.min.js`, store it under a new versioned filename, update the
`<script>` in `templates/settings.html`, delete the old file and bump `CSS_VERSION`.
Then check that the Workflow Monitor graph on Settings renders (nodes, status
colours, edges) and that single- and double-click on a node still work in the
graph tab: `static/js/workflow_tab.js` parses Mermaid's generated node element
ids (`<diagram id>-flowchart-<job id>-<n>`), which a major upgrade can change.

## Vendored column reordering

`static/vendor/datatables/dataTables.colReorder.min.js` is DataTables ColReorder **1.7.0**, distributed under the MIT license by SpryMedia: [official release](https://cdn.datatables.net/colreorder/1.7.0/), [license](https://datatables.net/license/mit). It matches the application's DataTables **1.13.7** and is loaded locally on Portfolio and Watchlist only. No runtime CDN request or package installation is needed. Required drag-feedback CSS is integrated into `styles.css` with the application's theme variables.

When upgrading DataTables or ColReorder, verify fixed-first Ticker, desktop mouse dragging, disabled touch/mobile dragging, hidden-column positions, sorting/filter identity after reorder, responsive child rows and saved View restoration. ColReorder 2.x targets DataTables 2.x and changes the fixed-column options; do not independently replace this 1.x extension with a 2.x release.
