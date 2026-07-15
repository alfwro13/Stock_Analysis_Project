# Dependency Reference

Maps every entry in `requirements.txt` to what it's actually used for in this
codebase and any upgrade-risk notes. Update this file whenever a dependency
is added, removed, or a major-version upgrade is deliberately taken.

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
   Dependabot PR automatically — check the PR's checks tab first.

## Web framework & server

| Package | Used for | What could break |
|---|---|---|
| `fastapi` | Core web framework — every route in `*_routes*.py` | Route signature/dependency-injection changes; response model validation |
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
| `ta` | Technical indicators (RSI, MACD, SMA, OBV) in `indicators.py`, `quant_signals.py`, `anomaly_engine.py`, `bull_bear_trap_engine.py`, `crash_engine.py`, `moonshot_engine.py` | Indicator formula changes would silently shift scores — no dedicated regression test compares indicator values against a fixed expected output beyond `tests/test_indicators_equivalence.py` |
| `exchange_calendars` | Holiday-aware trading-day calendar, used only inside `time_engine.py` per AGENTS.md rule 12 | New/changed exchange calendar codes (`time_engine._EXCHANGE_CALENDAR_CODES`) |
| `fake-useragent` | Random User-Agent header generation in `sentiment_engine.py` | API surface (`UserAgent()` constructor / `.random` property) — verified compatible through 2.2.0 |
| `python-slugify` | URL-safe label slugging in `ghostfolio_sync.py` | Minimal risk — pure string transform |
| `lxml` | HTML parsing backend, used directly (`account_scraper_engine.py`'s `lxml.html`) and as `trafilatura`'s/`BeautifulSoup`'s parser | Parsing edge cases on malformed HTML |
| `cssselect` | CSS-selector support for `lxml.html` (Account Price Scraper) | Selector syntax edge cases |
| `html5lib` | Fallback HTML5 parser (used transitively by `trafilatura`/`BeautifulSoup`) | Rarely invoked directly — low risk |
| `trafilatura` | Full-text article extraction for the News Feed (`news_feed_engine.py`) | Extraction heuristics changing what counts as "article body" |

## Charts & templating

| Package | Used for | What could break |
|---|---|---|
| `plotly` | All interactive charts (`visuals*.py`, client-side JS) | `fig.to_html()` output format — see AGENTS.md rule 18's server-rendered-chart caveats |
| `matplotlib` | Static Fear & Greed chart image (`sentiment_engine.py`), sent as a file attachment via Nextcloud | Rendering/backend changes for headless (non-GUI) image generation |
| `Jinja2` | Template engine for every page in `templates/` | Template syntax changes (rare — Jinja2 is very stable) |
| `markdown` | Renders markdown to HTML for a Learn/glossary surface (`page_routes.py`) | Markdown syntax edge cases |

## Scheduling & system

| Package | Used for | What could break |
|---|---|---|
| `apscheduler` | The only sanctioned scheduler per AGENTS.md rule 12 (`scheduler_engine.py`) | `CronTrigger`/job-store API changes — test that scheduled jobs still register (`tests/test_scheduler_engine.py`'s manifest-completeness check) |
| `psutil` | System diagnostics (CPU/memory/disk) surfaced in Settings → System Diagnostics (`ai_prediction_engine.py`, `system_check_engine.py`) | Platform-specific metric availability |

## ML / AI

| Package | Used for | What could break |
|---|---|---|
| `scikit-learn` | RF models, cross-sectional scaling (`ai_prediction_engine.py`, `regime_engine.py`, `macro_ai_engine.py`) | Estimator API/hyperparameter defaults changing — a silent accuracy regression, not a crash; re-run backfill + training after upgrading and compare accuracy metrics |
| `xgboost` | Gradient-boosted models in the ML ensemble (`ai_prediction_engine.py`) | Same as scikit-learn — silent accuracy drift risk, not a crash risk |
| `hmmlearn` | GaussianHMM regime classification (`regime_engine.py`, `macro_ai_engine.py`) | Model API stability — this package has historically had breaking API changes across majors; pin conservatively |
| `scipy` | Statistical/optimization routines underpinning `scikit-learn`/`hmmlearn` and used directly in risk calculations | Rarely breaking on its own; mostly a transitive-compatibility concern with scikit-learn/hmmlearn pins |
| `joblib` | Serializes/deserializes every `models/*.joblib` artifact | **Pickle-compatibility risk**: a joblib major upgrade can fail to deserialize models trained under an older version — always retrain (Settings → Machine Learning & AI Engine) after a joblib major-version bump, don't assume old `.joblib` files still load |
| `transformers` | FinBERT sentiment pipeline (`huggingface_engine.py`) — the only sanctioned NLP path per AGENTS.md rule 4 | `pipeline()` call signature, tokenizer defaults. **Note:** this project's dev venv was already running transformers 5.x before the `>=4.37.0` floor was ever raised in requirements.txt — the declared floor had drifted stale relative to what's actually been running and tested for some time |
| `torch` | Backend tensor library required by `transformers`'s pipeline; never imported directly by app code | CUDA/CPU wheel selection, not usually an app-code compatibility risk |

## Testing (dev-only, not shipped)

| Package | Used for | What could break |
|---|---|---|
| `pytest` | Test runner for the whole `tests/` suite | Fixture/marker API changes — would surface immediately as `./run_tests.sh` failing to collect tests |

## Declared but no direct import found (verify before assuming unused)

A static grep for `import <name>` across the codebase (excluding `venv/`)
found no hit for these two packages. They are **not** removed here since
removal wasn't in scope for this review and either could be a load-bearing
indirect dependency this grep missed (e.g. something only unpickled at
runtime, or exercised on a machine/config this checkout doesn't have) —
flagging for the operator to confirm before dropping them from
`requirements.txt`:

- **`ghostfolio`** (the `ghostfolio-py` API client package) — `ghostfolio_sync.py`
  talks to the Ghostfolio API directly via `requests` + a hand-rolled client,
  not via this package.
- **`shap`** — AGENTS.md's ML Models table documents `xgb_explainer*.pkl`
  artifacts as "SHAP explainers," but no current `.py` file imports `shap`.
  If those explainer artifacts are still produced by an out-of-repo training
  step, keep this dependency; if that step was retired, it may be dead.
