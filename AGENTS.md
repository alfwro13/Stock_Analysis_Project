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
├── universe_engine.py        # Market universe management
├── universe_fundamentals_engine.py
├── universe_deep_sync_engine.py
├── quant_engine.py           # Core scoring (0-100 composite score)
├── quant_screener.py         # Screener logic
├── quant_signals.py          # quant_signals table writes
├── indicators.py             # TA calculations (RSI, MACD, SMA, OBV…)
├── risk_engine.py            # VaR / CVaR / ATR stop-loss
├── position_sizing.py        # Kelly / fixed-fraction sizing
├── regime_engine.py          # Market regime classification
├── market_pulse.py           # CNN Fear & Greed + S&P chart
├── sentiment_engine.py       # FinBERT NLP sentiment
├── insider_engine.py         # SEC Form 4 scraping
├── earnings_engine.py        # Earnings date tracking
├── earnings_vol_engine.py    # Implied vs historical move edge
├── macro_ai_engine.py        # HMM + RF + XGBoost macro predictions
├── macro_data_engine.py      # FRED / BoE / ONS ingestion
├── macro_calendar_engine.py  # Economic event calendar
├── ai_engine.py              # LLM prompt aggregator
├── ai_prediction_engine.py   # XGBoost + RF soft-voting ensemble
├── xray_engine.py            # Portfolio X-ray / risk diagnostics
├── crash_engine.py           # Intraday crash detection
├── moonshot_engine.py        # Intraday parabolic / ATH detection
├── intraday_orchestrator.py  # 5-min scan loop
├── gilt_engine.py            # UK gilt yield tracking
├── index_engine.py           # Index data (SPY, FTSE…)
├── freetrade_engine.py       # Freetrade CSV import
├── ghostfolio_sync.py        # Ghostfolio API sync
├── fundamentals_helpers.py   # Shared fundamentals utilities
├── visuals.py                # Matplotlib / Plotly chart generation
├── maintenance_engine.py     # DB vacuum, orphan file pruning
├── reports_engine.py         # Quant briefing report generation
├── report_dispatcher.py      # Nextcloud Talk / alert dispatch
├── nextcloud_talk.py         # Nextcloud Talk webhook client
├── portfolio_service.py      # Portfolio aggregation helpers
├── profile_engine.py         # Asset profile cache
├── utils.py                  # Shared utilities
│
├── templates/                # Jinja2 HTML templates
├── static/                   # CSS, JS, images
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
| `asset_profiles` | Static corporate metadata (sector, summary) |
| `stock_signals` | Final System Verdict + fundamental snapshot |
| `quant_signals` | Daily historical TA + ML scores (composite PK: ticker + date) |
| `quant_scan_states` | Resumability tracker for long-running scans |
| `earnings_volatility` | Options arbitrage edge scores |
| `market_regimes` | VIX + SPY volatility regime labels + HMM state |
| `macro_regimes` | Gilt/bond yield threat levels (US + UK) |
| `macro_calendar` | Economic events + AI volatility warnings |
| `macro_indicators` | FRED / BoE / ONS structural macro metrics |
| `portfolio_xray` | X-ray risk diagnostics (added June 2026) |
| `xray_history` | Historical X-ray snapshots |
| `xray_alerts` | X-ray alert ledger |

Schema changes must go through `database.py:init_db()`.

---

## Key Architecture Rules

1. **Dual-storage:** Relational metadata → SQLite. Heavy time-series → Parquet. Never swap these.
2. **Self-healing:** If Yahoo Finance fails, fall back to local Parquet/JSON cache — never crash the server.
3. **Priority arbitration:** Portfolio/Watchlist > LSE universe > US universe. Delisted tickers are appended to `freetrade_blacklist.json` automatically.
4. **No LLM for sentiment:** Market sentiment uses FinBERT locally. `ai_engine.py` generates prompts for *external* LLMs but is not itself an LLM.
5. **APScheduler only:** Do not introduce external cron jobs. All scheduled work lives in `scheduler_engine.py`.
6. **CSRF + session auth:** All POST endpoints are protected. Session cookies only. See `auth.py`.

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

## Coding Guidelines for Agents

- **Do not add comments** unless the why is genuinely non-obvious.
- **Do not add error handling** for scenarios that cannot happen — trust framework guarantees.
- **Do not create new files** unless strictly necessary; prefer editing existing modules.
- **Do not introduce abstractions** beyond what the task requires.
- **Run `./run_tests.sh`** after every change and fix failures before marking work done.
- **UK market quirks:** LSE-listed stocks may have prices quoted in pence (GBX), not pounds (GBP). The codebase handles this explicitly — do not remove or simplify that logic.
- **Secrets:** All credentials live in `.env` (loaded via `python-dotenv`). Never hard-code tokens or API keys. Never commit `.env`.
- **Port:** Default is `8090`. Do not change it without updating `config.json` and `config.py`.

---

## External Integrations

| Service | Purpose | Config key |
|---|---|---|
| Ghostfolio | Live portfolio holdings | `GHOSTFOLIO_URL`, `API_TOKEN` |
| Yahoo Finance (`yfinance`) | Price, OHLCV, fundamentals | (public, no key) |
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

## Notable Sub-systems

- **X-ray engine** (`xray_engine.py`): Portfolio risk diagnostics view, added June 2026. Runs as a scheduler job at 19:00. Served at `GET /api/xray`. Renders inline on `portfolio.html` as a same-page swap.
- **Intraday orchestrator** (`intraday_orchestrator.py`): Runs every 5 minutes during market hours. Detects crash/moonshot conditions and fires Nextcloud alerts.
- **Quant briefing** (`reports_engine.py`): Generates a markdown report dispatched each morning via Nextcloud Talk.
- **Embed mode:** Append `?embed=true` to `/portfolio` or `/watchlist` to strip the navbar for iframe integration (e.g. Home Assistant).
