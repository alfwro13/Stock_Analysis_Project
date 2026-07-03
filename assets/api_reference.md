# Quantamental Dashboard — API Reference

**Base path:** `/api`  
**Server:** `http://localhost:8090` (configurable via `PORT` in settings)  
**Format:** All endpoints accept and return `application/json`

---

## Table of Contents

1. [Response Conventions](#1-response-conventions)
2. [Notifications](#2-notifications)
3. [Portfolio & Watchlist](#3-portfolio--watchlist)
4. [Data Refresh](#4-data-refresh)
5. [Quant Screener](#5-quant-screener)
6. [Market Pulse](#6-market-pulse)
7. [Reports](#7-reports)
8. [Machine Learning (ML)](#8-machine-learning-ml)
9. [Macro Economy](#9-macro-economy)
10. [Market Universe](#10-market-universe)
11. [Options](#11-options)
12. [AI Prompt Engine](#12-ai-prompt-engine)
13. [Settings & Configuration](#13-settings--configuration)
14. [System & Infrastructure](#14-system--infrastructure)
15. [Alert Testing](#15-alert-testing)
16. [AI Sector Contagion Monitor](#16-ai-sector-contagion-monitor)
17. [Market Trap & Recovery Monitor](#17-market-trap--recovery-monitor)
18. [Market Regime (HMM + Market Stress IF)](#18-market-regime-hmm--market-stress-if)
19. [Accounts](#19-accounts)

---

## 1. Response Conventions

### Success response

```json
{
  "status": "success",
  "message": "Human-readable description of what happened."
}
```

### Error response

```json
{
  "status": "error",
  "message": "Description of what went wrong."
}
```

### Background task response

Trigger endpoints queue heavy work (ML training, data scans, etc.) as background tasks and return immediately. The actual work runs asynchronously — check **System Notifications** for progress updates.

```json
{
  "status": "success",
  "message": "Task initiated in the background. Check System Notifications for progress."
}
```

### HTTP status codes used

| Code | Meaning |
|------|---------|
| `200` | Success |
| `400` | Bad request — invalid input |
| `404` | Resource not found |
| `422` | Validation error — request body failed schema check |
| `500` | Internal server error |
| `502` | Bad gateway — upstream service (e.g. Yahoo Finance) failed |
| `504` | Gateway timeout — upstream service timed out |

---

## 2. Notifications

### `GET /api/notifications/latest`

Returns all system notifications newer than a given ID. Used by the UI to poll for new activity from background jobs.

**Query parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `last_id` | integer | `0` | Only return notifications with `id` greater than this value. Pass the highest ID you have already seen to receive only new ones. |

**Response**

```json
{
  "status": "success",
  "notifications": [
    {
      "id": 42,
      "type": "Success",
      "text": "Quant scan completed for 87 tickers.",
      "timestamp": "2026-05-29 14:30:00"
    }
  ]
}
```

`type` is a free-text category written by the engine that generated the notification. Common values: `Success`, `Error`, `Warning`, `Info`.

---

### `POST /api/notifications/mark-read`

Marks all unread notifications as read.

**Request body:** none

**Response**

```json
{
  "status": "success",
  "message": "All notifications marked as read."
}
```

---

### `POST /api/notifications/purge`

Permanently deletes **all** notifications from the database. Cannot be undone.

**Request body:** none

**Response**

```json
{
  "status": "success",
  "message": "All notifications purged successfully."
}
```

---

## 3. Portfolio & Watchlist

### `POST /api/update`

Triggers a full data pipeline update for all portfolio and watchlist tickers. Runs asynchronously.

Pipeline steps: Ghostfolio sync → price history download → quant signals → ML inference → tail risk (VaR/CVaR).

**Request body:** none  
**Response:** standard background task response

---

### `POST /api/sync-ghostfolio`

Pulls the latest holdings from Ghostfolio and refreshes the local portfolio state. Requires Ghostfolio credentials in settings.

**Request body:** none  
**Response:** standard background task response

---

### `POST /api/ghostfolio/discover`

Authenticates with Ghostfolio and discovers all available portfolio accounts. Saves discovered accounts to config and reloads the scheduler.

**Request body:** none

**Response (success)**

```json
{
  "status": "success",
  "message": "Successfully discovered 3 active accounts."
}
```

**Response (failure)**

```json
{
  "status": "error",
  "message": "Failed to authenticate with Ghostfolio."
}
```

---

### `POST /api/watchlist/add`

Adds a ticker to the native Watchlist account (the star toggle on `/stock/{ticker}` calls this). Resolves company name/currency/quote type via Yahoo and exchange via `time_engine.ticker_exchange()` before inserting into `watchlist_items`. Re-adding an already-watched ticker is a no-op.

**Request body**

```json
{
  "ticker": "AAPL"
}
```

**Response (success)**

```json
{ "status": "success" }
```

**Response (failure)**

```json
{
  "status": "error",
  "message": "Failed to add to watchlist."
}
```

---

### `POST /api/watchlist/remove`

Removes a ticker from the native Watchlist account (the star toggle on `/stock/{ticker}` calls this).

**Request body**

```json
{
  "ticker": "AAPL"
}
```

**Response (success)**

```json
{ "status": "success" }
```

---

### `GET /api/ticker-search?q=`

Company-name or ticker autocomplete (wraps `yfinance.Search`), used by the "+ Add Ticker" modal on the Watchlist account's detail page. Cached 1 hour per query.

**Response**

```json
{
  "status": "success",
  "results": [
    { "ticker": "AAPL", "company_name": "Apple Inc.", "quote_type": "EQUITY" }
  ]
}
```

---

### `GET /api/accounts/{account_id}/watchlist-items`

Lists every ticker on the given Watchlist account. 400 if `account_id` is not a Watchlist-type account.

**Response**

```json
{
  "status": "success",
  "items": [
    { "id": 1, "account_id": 3, "ticker": "AAPL", "company_name": "Apple Inc.", "currency": "USD", "quote_type": "EQUITY", "exchange": "NYSE", "added_at": "2026-06-27 12:00:00" }
  ]
}
```

---

### `POST /api/accounts/{account_id}/watchlist-items`

Adds a ticker to the given Watchlist account via the "+ Add Ticker" modal. Resolves metadata server-side the same way `/api/watchlist/add` does.

**Request body**

```json
{ "ticker": "AAPL" }
```

**Response**

```json
{ "status": "success", "id": 1 }
```

---

### `POST /api/accounts/{account_id}/watchlist-items/bulk-delete`

Deletes the given watchlist item rows (by id) from the Watchlist account — powers the checkbox multi-select delete on the compact management table.

**Request body**

```json
{ "ids": [1, 2, 3] }
```

**Response**

```json
{ "status": "success", "deleted": 3 }
```

---

### `POST /api/trigger-freetrade-sync`

Synchronises the local market universe with Freetrade's securities list via ISIN and MIC code lookups. Runs asynchronously.

**Request body:** none  
**Response:** standard background task response

---

## 4. Data Refresh

### `POST /api/data/refresh-single`

Fetches fresh price history, runs a full quant analysis, ML inference, tail risk, and sentiment for a single ticker. Runs synchronously (blocking — may take 10–30 seconds).

**Request body**

```json
{
  "ticker": "LLOY.L"
}
```

**Response (success)**

```json
{ "status": "success" }
```

**Response (failure)**

```json
{
  "status": "error",
  "message": "Data fetch failed."
}
```

---

### `POST /api/ticker/{ticker}/name-override`

Sets or clears a user-defined display name for the given ticker. The override is shown instead of the system name in the portfolio table, watchlist table, and stock detail page heading. Sending an empty `display_name` deletes the override and restores the system name.

**URL parameters**

| Name | Type | Description |
|------|------|-------------|
| `ticker` | string | Ticker symbol (normalised, e.g. `LLOY.L`) |

**Request body**

```json
{ "display_name": "Lloyds Bank" }
```

Pass `"display_name": ""` to clear an existing override.

**Response (success)**

```json
{ "status": "success" }
```

---

### `POST /api/intraday-chart/refresh`

Fetches fresh 5-minute intraday data from Yahoo Finance for a single ticker, persists it to parquet, then returns re-rendered chart HTML. Used by the Stock Detail page auto-refresh timer to keep the Intraday Pulse chart current without a full page reload.

**Request body**

```json
{ "ticker": "SMGB.L" }
```

**Response**

```json
{ "html": "<plotly chart HTML string>" }
```

---

### `GET /api/freshness`

Returns how up-to-date the ML model file and price data are, along with CSS state classes for the UI freshness badge.

**Response**

```json
{
  "model_date": "2026-05-25",
  "model_days_ago": 4,
  "model_state": "freshness-warn",
  "prices_date": "2026-05-28",
  "prices_days_ago": 1,
  "prices_state": "freshness-fresh"
}
```

**State values**

| Value | Meaning |
|-------|---------|
| `freshness-fresh` | Up to date — green badge |
| `freshness-warn` | Getting old — amber badge |
| `freshness-stale` | Out of date — red badge |

Thresholds are defined in `constants.py`:

| Item | Warn after | Stale after |
|------|-----------|-------------|
| ML model | 7 days | 14 days |
| Price data | 3 days | 5 days |

---

## 5. Quant Screener

### `POST /api/trigger-quant-scan`

Runs the full daily quant scan for all portfolio and watchlist tickers:
1. Candlestick patterns and composite score (0–100)
2. RSI, MACD, moving average signals
3. ML ensemble inference (XGBoost + Random Forest)
4. Historical simulation VaR/CVaR tail risk

Runs asynchronously. Results populate `quant_signals` and `stock_signals` tables.

**Request body:** none  
**Response:** standard background task response

---

### `POST /api/trigger-universe-quant-scan`

Same pipeline as above but run across the entire market universe (4,000+ tickers). This is a very long-running job (60+ minutes).

**Request body:** none  
**Response:** standard background task response

---

### `POST /api/trigger-earnings-scan`

Scans for upcoming earnings events and calculates implied vs historical volatility moves for options traders.

**Request body:** none  
**Response:** standard background task response

---

### `POST /api/trigger-sentiment-scan`

Runs the FinBERT NLP sentiment pipeline across all tickers — fetches recent news headlines and scores each one on a scale from -1.0 (panic) to +1.0 (euphoria).

**Request body:** none  
**Response:** standard background task response

---

### `POST /api/trigger-morning-briefing`

Generates the Morning Quant Briefing (overnight news, UK pre-open charts, quant signals) and — if `SCHEDULING.DISPATCHER.ENABLED` is true — uploads it to Nextcloud and shares it to the configured Talk conversation. Runs in the background.

**Request body:** none  
**Response:** standard background task response

---

### `POST /api/trigger-lunch-briefing`

Generates the Lunchtime Quant Briefing (morning session news, UK mid-session snapshot, US pre-market) and — if `SCHEDULING.LUNCH_DISPATCHER.ENABLED` is true — dispatches it to Talk. Runs in the background.

**Request body:** none  
**Response:** standard background task response

---

### `GET /api/screener-data`

Returns the latest quantitative signal snapshot for all tickers that have both `quant_signals` data and a `market_universe` entry.

Respects `FREETRADE_ONLY_MODE` setting — if enabled, only Freetrade-eligible tickers are returned.

**Response**

```json
{
  "data": [
    {
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "sector": "Technology",
      "exchange": "NASDAQ",
      "currency": "USD",
      "date": "2026-05-28",
      "close_price": 189.45,
      "volume": 54320000,
      "rsi_14": 58.3,
      "macd_hist": 0.72,
      "sma_50": 182.10,
      "sma_200": 175.60,
      "volume_surge": false,
      "bullish_cross": true,
      "ml_confidence_score": 67.5,
      "var_95": 0.024,
      "cvar_95": 0.031,
      "atr_pct": 0.018,
      "sentiment_score": 0.42,
      "composite_score": 55,
      "is_freetrade": 1,
      "quote_type": "EQUITY"
    }
  ]
}
```

---

## 6. Market Pulse

### `GET /api/market-pulse`

Returns the cached real-time pulse for market indices (S&P 500, FTSE 100, VIX, etc.). Fetches fresh data if the cache is stale.

**Response**

```json
{
  "status": "success",
  "data": [
    {
      "ticker": "^GSPC",
      "name": "S&P 500",
      "price": 5287.42,
      "change_pts": 12.55,
      "change_pct": 0.24,
      "is_positive": true,
      "last_updated": 1748520600.0
    }
  ]
}
```

---

### `POST /api/market-pulse`

Fetches live pulse data for a custom list of tickers and caches the results. Returns cached data immediately and refreshes stale items in the background.

**Request body**

```json
{
  "tickers": ["AAPL", "MSFT", "^GSPC"]
}
```

**Response**

```json
{
  "status": "success",
  "data": {
    "indexes": [ ... ],
    "assets": [ ... ]
  }
}
```

Each item contains: `ticker`, `name`, `price`, `change_pts`, `change_pct`, `is_positive`, `last_updated`, `is_stale`.

---

## 7. Reports

All report endpoints return a `data` array. The array may be empty if the database has insufficient data to generate ranked results.

### `GET /api/reports/quality-compounders`

Stocks with strong ROE, low debt, consistent revenue growth, and momentum — intended for long-term buy-and-hold selection.

### `GET /api/reports/quality-on-sale`

Quality stocks (high ROE, low debt) trading at a discount — value investing screener.

### `GET /api/reports/garp-tenbaggers`

Growth-at-reasonable-price candidates with high growth rates but still-affordable valuations (PEG < threshold).

### `GET /api/reports/sectors`

Sector trend analysis: average composite score and momentum broken down by GICS sector.

### `GET /api/reports/mean-reversion`

Oversold setups: stocks with RSI below the configured maximum (default: `MR_MAX_RSI` in settings) that may be due for a bounce.

### `GET /api/reports/leaders`

Market leaders and laggards: top and bottom performers ranked by recent momentum and composite score.

### `GET /api/reports/dividends`

Dividend harvest setups: stocks with yield above `DIV_MIN_YIELD` and composite score above `DIV_MIN_SCORE` (both configurable in settings).

**Common response shape (all 7 reports)**

```json
{
  "data": [
    {
      "ticker": "MSFT",
      "company_name": "Microsoft Corporation",
      "sector": "Technology",
      "composite_score": 72,
      "overall_signal": "Strong Buy",
      "rsi_14": 61.2,
      "current_price": 415.80,
      "dividend_yield": 0.0072,
      "roe": 0.38,
      "revenue_growth": 0.17
    }
  ]
}
```

Field availability varies by report type.

---

## 8. Machine Learning (ML)

The ML pipeline uses an XGBoost + Random Forest soft-voting ensemble trained on 2 years of historical daily features. It predicts whether a stock will return >3% over the next 10 trading days. The model must be trained before inference can run.

### `POST /api/ml/trigger-backfill`

Rebuilds 2 years of historical feature rows used to train the ML model. Only needs to be run once (or when the feature set changes). Runs asynchronously — may take 30–60 minutes.

**Request body:** none  
**Response:** standard background task response

---

### `POST /api/ml/trigger-training`

Trains the global XGBoost/RF soft-voting ensemble using the historical feature backfill. Saves the model to `models/ml_ensemble.joblib`. Runs asynchronously — may take 10–30 minutes.

**Request body:** none  
**Response:** standard background task response

---

### `POST /api/ml/trigger-inference`

Runs daily ML inference on all universe tickers (or portfolio/watchlist tickers if universe is empty) and writes `ml_confidence_score` values into the database. Runs asynchronously.

**Request body:** none  
**Response:** standard background task response

---

## 9. Macro Economy

The macro engine combines FRED API data (US M2, jobless claims, yield spreads) with UK economic indicators to train Hidden Markov Models and ensemble models for volatility prediction.

### `POST /api/macro/init-pipeline`

Full one-time initialisation sequence:
1. Seed the macro calendar with known economic events
2. Sync the calendar from external sources
3. Fetch all macro indicators from FRED and UK data sources
4. Train the HMM regime clustering model (3 states: expansion / choppy / recession)
5. Train the Random Forest consensus miss probability model
6. Train the XGBoost volatility magnitude model
7. Run inference for today's date

Only needs to be run once. Runs asynchronously — may take 5–15 minutes.

**Request body:** none  
**Response:** standard background task response

---

### `POST /api/macro/run-pipeline`

Standard daily macro update (after initialisation is complete):
1. Sync macro calendar events
2. Refresh FRED and UK macro indicators
3. Run inference for today

**Request body:** none  
**Response:** standard background task response

---

## 10. Market Universe

The market universe is a catalogue of 4,000+ tickers (S&P 500, FTSE 100, Freetrade securities) used as the target list for universe-wide scans.

### `GET /api/universe/profiler-status`

Returns the Fundamentals Profiler queue breakdown — how many tickers are eligible for profiling, already profiled, or stale.

**Response**

```json
{
  "status": "success",
  "pending_count": 47,
  "breakdown": {
    "pending_count": 47,
    "eligible": 650,
    "already_profiled": 603,
    "stale": 47
  }
}
```

---

### `POST /api/trigger-universe-update`

Refreshes the market universe ticker list — syncs new additions and removals from supported indices.

**Request body:** none  
**Response:** standard background task response

---

### `POST /api/universe/sync-indices`

Scrapes the current S&P 500 and FTSE 100 constituent lists and updates the `market_universe` table.

**Request body:** none  
**Response:** standard background task response

---

### `POST /api/universe/sync-profiler`

Runs the Fundamentals Profiler for stale/unprocessed tickers: fetches `.info` payloads from Yahoo Finance and stores them in `asset_profiles`. Processes tickers in configurable batch sizes.

**Request body:** none  
**Response:** standard background task response

---

### `POST /api/universe/deep-sync`

Full universe synchronisation pipeline sequencing:
1. Fundamentals profiling
2. Metadata sync
3. Technicals (quant scan)
4. ML inference

For the full index universe (FTSE 100 + S&P 500). Estimated runtime: **30–45 minutes**. Respects `FREETRADE_ONLY_MODE` setting.

**Request body:** none  
**Response:** standard background task response

---

### `GET /api/universe/imports/list`

Lists CSV files available to import from the server's `tools/data/imports/` directory.

**Response**

```json
{
  "status": "success",
  "files": ["custom_universe.csv", "lse_smallcap.csv"]
}
```

---

### `POST /api/universe/import/server`

Imports tickers from a CSV file already present on the server into the `market_universe` table. Triggers a quant scan for the imported tickers in the background.

**Required CSV columns:** `ticker`, `company_name`, `sector`, `industry`, `currency`, `country`, `exchange`

**Request body**

```json
{
  "filename": "custom_universe.csv"
}
```

**Response (success)**

```json
{
  "status": "success",
  "message": "Successfully sideloaded 142 assets from 'custom_universe.csv' into the local Market Universe."
}
```

**Error responses**

| Status | Cause |
|--------|-------|
| `400` | File is not a `.csv`, or a required column is missing |
| `404` | File not found on the server |
| `500` | Parser error |

---

## 11. Options

### `GET /api/options/chain/{ticker}`

Fetches the live options chain for a ticker from Yahoo Finance. Returns expiry dates, strike prices, calls and puts with Greeks.

**Path parameter**

| Parameter | Pattern | Example |
|-----------|---------|---------|
| `ticker` | `^[A-Z0-9.\-\^=]{1,20}$` | `AAPL` |

**Response (success):** Options chain data from Yahoo Finance (structure follows yfinance format).

**Response (failure)**

```json
{
  "error": "No options data available for FAKEXYZ."
}
```

---

### `POST /api/options/payoff`

Calculates a payoff matrix for a multi-leg options strategy at 500 evenly-spaced price points around the current price.

**Request body**

```json
{
  "current_price": 150.0,
  "legs": [
    {
      "type": "call",
      "strike": 155.0,
      "premium": 3.50,
      "position": "long",
      "quantity": 1
    },
    {
      "type": "put",
      "strike": 145.0,
      "premium": 2.80,
      "position": "long",
      "quantity": 1
    }
  ]
}
```

**Leg fields**

| Field | Type | Values |
|-------|------|--------|
| `type` | string | `call` \| `put` |
| `strike` | float | Strike price |
| `premium` | float | Premium paid/received per share |
| `position` | string | `long` \| `short` |
| `quantity` | integer | Number of contracts (default: `1`) |

**Response**

```json
{
  "prices": [140.0, 140.3, "..."],
  "payoff": [-6.30, -6.30, "..."],
  "breakeven_lower": 138.70,
  "breakeven_upper": 161.30,
  "max_profit": null,
  "max_loss": -6.30
}
```

**Error responses**

| Status | Cause |
|--------|-------|
| `422` | Invalid input (e.g. zero quantity, missing fields) |

---

## 12. AI Prompt Engine

### `GET /api/ai-prompt/{ticker}`

Compiles a structured, AI-consumable prompt containing the full quantamental context for a ticker: regime classification, technicals, fundamentals, ML confidence, VaR/CVaR, sentiment, and educational notes.

Designed to be pasted directly into an LLM (e.g. Claude) for AI-assisted investment analysis.

**Path parameter**

| Parameter | Pattern | Example |
|-----------|---------|---------|
| `ticker` | `^[A-Z0-9.\-\^=]{1,20}$` | `TSLA` |

**Query parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `mode` | string | `Quantamental Deep-Dive` | Analysis mode passed to the prompt engine. |

**Response (success)**

```json
{
  "status": "success",
  "prompt": "## Quantamental Deep-Dive: TSLA\n\n**Market Regime:** Normal..."
}
```

**Response (not found)**

```json
{
  "status": "error",
  "message": "Stock data not found in local database."
}
```

---

### `GET /api/ai-prompt/market-regime`

Compiles an AI-consumable prompt using live Market Regime HMM data: current HMM state, confidence, days in state, VIX, SPY volatility, empirical 3×3 transition matrix, and macro threat context.

**Query parameters**

| Parameter | Type | Default | Allowed values |
|-----------|------|---------|----------------|
| `mode` | string | `Plain English Briefing` | `Plain English Briefing`, `What Happens Next?`, `How Should I Position?`, `Red Flags Check` |

**Response (success)**

```json
{ "status": "success", "prompt": "You are a Patient financial educator..." }
```

**Response (bad mode)**

```json
{ "status": "error", "message": "Unrecognised mode: ..." }
```

---

### `GET /api/ai-prompt/market-sentiment/us`

Compiles an AI-consumable prompt using live US market sentiment data: regime label, turbulence index, VIX, HMM macro state, CPI, yield curve, high-yield spread, M2, macro threat, 10/30-year yields, DXY, upcoming USD macro events (ranked by AI surprise probability), and AI sector contagion status.

**Query parameters**

| Parameter | Type | Default | Allowed values |
|-----------|------|---------|----------------|
| `mode` | string | `US Market Health Check` | `US Market Health Check`, `This Week's US Risk Events`, `Recession Radar`, `Inflation & Rate Impact` |

**Response (success)**

```json
{ "status": "success", "prompt": "You are a Senior market strategist..." }
```

---

### `GET /api/ai-prompt/market-sentiment/uk`

Compiles an AI-consumable prompt using live UK market sentiment data: regime label, turbulence, FTSE volatility, UK CPI, corporate spread, M4, 10-year gilt yield, GBP/USD, macro threat, and upcoming GBP macro events. The `UK vs US Comparison` mode also includes all US data for side-by-side context.

**Query parameters**

| Parameter | Type | Default | Allowed values |
|-----------|------|---------|----------------|
| `mode` | string | `UK Market Health Check` | `UK Market Health Check`, `This Week's UK Risk Events`, `Pound & Gilt Impact`, `UK vs US Comparison`, `UK Investor in US Exposure` |

**Response (success)**

```json
{ "status": "success", "prompt": "You are a Senior UK market strategist..." }
```

---

## 13. Settings & Configuration

### `POST /api/settings`

Saves application settings. Only fields present in the request body are updated — absent fields are left unchanged (deep merge, not a full replacement).

After saving, the scheduler is reloaded to apply any changed schedule configurations.

`SettingsConfig` (`api_routes_system.py`) declares `extra: "forbid"` — any field not in its schema is rejected with a 422. Credentials are **not** part of this schema; they are `.env` secrets saved through their own dedicated endpoints and read back at runtime via `os.environ.get(...)`, never through `POST /api/settings`:

| Credential | Save endpoint | `.env` key(s) |
|---|---|---|
| FRED API key | `POST /api/save-fred-api-key` | `FRED_API_KEY` |
| Ghostfolio URL + token | `POST /api/save-ghostfolio-settings` | `GHOSTFOLIO_URL`, `GHOSTFOLIO_TOKEN` |
| Nextcloud Talk credentials | `POST /api/save-nextcloud-settings` | `NEXTCLOUD_URL`, `NEXTCLOUD_BOT_USERNAME`, `NEXTCLOUD_APP_PASSWORD`, `NEXTCLOUD_CONVERSATION_TOKEN` |
| HuggingFace token | `POST /api/save-hf-token` | `HF_TOKEN` |

**Request body** — all fields optional, send only what you want to change

```json
{
  "SERVER_URL": "http://192.168.1.100",
  "PORT": 8090,
  "BASE_CURRENCY": "GBP",
  "YAHOO_IPV6_ADDRESS": "2a00:1450:400f:804::200e",
  "IGNORED_TICKERS": ["GMESTOP"],
  "ACCOUNT_CURRENCIES": ["GBP", "GBp", "USD", "EUR"],
  "GHOSTFOLIO_ENABLED": true,
  "UI_PREFERENCES": {
    "LIVE_PORTFOLIO": true,
    "LIVE_WATCHLIST": true,
    "LIVE_DETAILS": false,
    "REFRESH_RATE": 30,
    "FREETRADE_ONLY_MODE": false,
    "FONT_SIZE_NAV": 16,
    "FONT_SIZE_TABLE": 14,
    "FONT_SIZE_FORM": 14,
    "FONT_SIZE_BTN": 14,
    "FONT_SIZE_SECTION": 20
  },
  "POSITION_SIZING": {
    "ACCOUNT_VALUE": 50000.0,
    "RISK_PCT": 1.0,
    "STOP_MULTIPLE": 2.0
  },
  "REPORTS_DEFAULTS": {
    "MR_MAX_RSI": 35,
    "DIV_MIN_YIELD": 0.03,
    "DIV_MIN_SCORE": 20
  },
  "NOTIFICATION_ROUTING": {
    "crash_alert":         { "log_file": true, "in_app": true, "nextcloud_talk": true },
    "quant_analysis_job":  { "log_file": false, "in_app": true, "nextcloud_talk": false }
  }
}
```

`NOTIFICATION_ROUTING` is keyed by notification source: a scheduled job id (its start/success/error status) or an alert source key (e.g. `crash_alert`, `moonshot_alert`, `earnings_alert`, `trap_monitor_alert`, `dip_radar_alert`, `cb_nlp_alert`, `network_fault`). Each value selects the delivery channels — `log_file`, `in_app`, `nextcloud_talk`. Any source omitted from this object falls back to its built-in default routing. This drives the **Notification Settings** panel in Settings and is consumed by `notification_engine.notify()`.

`GHOSTFOLIO_ENABLED` (default `true`) is the master switch for the **Ghostfolio Integration** card. Setting it to `false` has side effects beyond the usual deep-merge: `save_settings()` synchronously deletes `data/portfolio.json` and `data/watchlist.json`, clears `GHOSTFOLIO_ACCOUNTS.discovered`/`active`, and forces `SCHEDULING.GHOSTFOLIO_SYNC.ENABLED` to `false` regardless of what was submitted for it. `accounts_engine.get_combined_holdings()` (the Portfolio page's Global Values) and `GhostfolioSyncEngine.run_full_sync()` both also check this flag directly, so a stray manual sync or a restored `portfolio.json` cannot resurrect Ghostfolio data while disabled. The nightly **Database & File Maintenance** job (`maintenance_engine.enforce_ghostfolio_disabled`) re-deletes both files as a backstop, and **System Configuration Check** (`system_check_engine.py`) raises a `ghostfolio_files_not_purged` warning if they still exist between maintenance runs.

**Response**

```json
{
  "status": "success",
  "message": "Settings saved successfully."
}
```

---

### `POST /api/settings/test-yahoo-ipv6`

Tests whether a given IPv6 address can successfully reach Yahoo Finance edge nodes. Used to validate a custom IPv6 routing configuration before saving it.

**Request body**

```json
{
  "ipv6_address": "2a00:1450:400f:804::200e"
}
```

**Response (success)**

```json
{
  "status": "success",
  "message": "Successfully verified stable IPv6 socket connection to Yahoo Finance edge nodes via 2a00:1450:400f:804::200e."
}
```

**Error responses**

| Status | Message |
|--------|---------|
| `400` | IPv6 address is empty |
| `502` | Socket binding failed, network unreachable, or connection refused |
| `504` | Connection timed out |

---

### `GET /api/ui-theme.css`

Returns a CSS stylesheet fragment that sets the five user-configurable font-size custom properties at `:root` level, driven by the values stored in `UI_PREFERENCES` in `config.json`. Loaded automatically via `<link>` in `base.html` on every page; no auth required.

**Response** — `Content-Type: text/css`

```css
:root {--font-size-nav: 16px; --font-size-table: 14px; --font-size-form: 14px; --font-size-btn: 14px; --font-size-section: 20px;}
```

---

### `GET /api/settings/network-status`

Returns the currently active Yahoo Finance routing mode and its health status. Includes a `routing_mode` field reflecting the configured mode (`"IPv4 Only"`, `"IPv6 Only"`, or `"Dual (Round-robin)"`).

**Response (IPv4 Only)**

```json
{
  "status": "success",
  "route": "IPv4 Only",
  "routing_mode": "IPv4 Only",
  "indicator": "green",
  "message": "Using standard IPv4 routing. No IPv6 address is configured or IPv6 is disabled."
}
```

**Response (IPv6 Only)**

```json
{
  "status": "success",
  "route": "IPv6 Only",
  "routing_mode": "IPv6 Only",
  "indicator": "green",
  "message": "Routing all Yahoo Finance traffic exclusively through IPv6 (2a00:...)."
}
```

**Response (Dual round-robin)**

```json
{
  "status": "success",
  "route": "Dual (Round-robin)",
  "routing_mode": "Dual (Round-robin)",
  "indicator": "green",
  "message": "Round-robin load balancing between IPv4 and IPv6 (2a00:...)."
}
```

**Response (IPv6 failover active)**

```json
{
  "status": "warning",
  "route": "IPv4 (Failover Rescue Active)",
  "routing_mode": "IPv6 Only",
  "indicator": "yellow",
  "message": "IPv6 routing failed at 2026-05-29 12:00:00. Traffic is actively being rescued via IPv4 fallback. Last Error: ..."
}
```

---

### `GET /api/system/yahoo-api-stats`

Returns daily Yahoo Finance request counts for the past 8 days, broken down by interface and outcome.

**Response**

```json
{
  "status": "success",
  "rows": [
    {
      "date": "2026-06-22",
      "total_calls": 142,
      "ipv4_calls": 71,
      "ipv6_calls": 71,
      "rate_limit_429": 0,
      "other_errors": 2
    }
  ]
}
```

| Field | Description |
|---|---|
| `date` | UTC date (`YYYY-MM-DD`) |
| `total_calls` | Total `yahoo_connection_boundary` invocations |
| `ipv4_calls` | Calls routed via IPv4 |
| `ipv6_calls` | Calls routed via IPv6 |
| `rate_limit_429` | Calls that received HTTP 429 |
| `other_errors` | Calls that raised a non-429 exception |

---

### `POST /api/change-password`

Changes the dashboard login password. Requires `X-Confirm-Token` header. The new password is stored as a PBKDF2-SHA256 hash in `.env`; the plaintext `DASHBOARD_PASSWORD` key is cleared.

**Headers:** `X-Confirm-Token: <token>`

**Request body**

```json
{
  "current_password": "current",
  "new_password": "newpassword99",
  "confirm_password": "newpassword99"
}
```

**Validation:** new password must be ≥ 8 chars and not `"changeme"`.

**Error responses**

| Status | Condition |
|--------|-----------|
| `400` | Wrong current password, mismatched new passwords, too short, or forbidden value |
| `403` | Invalid or missing `X-Confirm-Token` |

---

### `POST /api/save-account-email`

Saves the account email address used for password-reset notifications. Requires `X-Confirm-Token` header. Stored as `ACCOUNT_EMAIL` in `.env`.

**Headers:** `X-Confirm-Token: <token>`

**Request body**

```json
{ "email": "admin@example.com" }
```

---

### `POST /api/save-smtp-settings`

Saves SMTP mail server configuration to `.env`. Takes effect immediately without a restart. Requires `X-Confirm-Token` header.

**Headers:** `X-Confirm-Token: <token>`, `Content-Type: application/json`

**Request body**

```json
{
  "smtp_host": "smtp.gmail.com",
  "smtp_port": "587",
  "smtp_user": "you@example.com",
  "smtp_pass": "app-password",
  "smtp_from": "noreply@example.com"
}
```

---

### `POST /api/send-test-email`

Sends a test email to the configured `ACCOUNT_EMAIL` using the current SMTP settings. Returns `400` if `SMTP_HOST` or `ACCOUNT_EMAIL` is not configured. Returns `500` on SMTP send failure. Requires `X-Confirm-Token` header.

**Headers:** `X-Confirm-Token: <token>`

---

### `POST /api/request-password-reset`

Initiates a self-service password reset. No authentication required — accessible from the login page.

If `email` matches `ACCOUNT_EMAIL` in `.env`, a one-time reset token (valid 1 hour) is generated and delivered via:
1. SMTP email — if `SMTP_HOST`, `SMTP_USER`, `SMTP_PASS` are set in `.env`
2. Nextcloud Talk — if `NEXTCLOUD_URL` is configured and SMTP is not
3. Server log (`INFO`) — if no delivery channel is configured

The response is always `200 OK` regardless of whether the email matched (prevents email enumeration).

**Request body**

```json
{ "email": "admin@example.com" }
```

**Response**

```json
{ "status": "ok", "message": "If the email matches, a reset link has been sent." }
```

---

### `POST /api/reset-password`

Completes a self-service password reset using a valid reset token. No authentication required.

**Request body**

```json
{
  "token": "<token from reset link>",
  "new_password": "newpassword99",
  "confirm_password": "newpassword99"
}
```

**Error responses**

| Status | Condition |
|--------|-----------|
| `400` | Invalid, expired, or already-used token; mismatched passwords; too short; forbidden value |

---

### `POST /api/admin-reset-password`

Admin emergency password reset — bypasses the old password requirement. Only works when `FORCE_PASSWORD_RESET: true` is set in `config.json`. Clears the flag automatically on success.

**Request body**

```json
{
  "new_password": "newpassword99",
  "confirm_password": "newpassword99"
}
```

**Error responses**

| Status | Condition |
|--------|-----------|
| `403` | `FORCE_PASSWORD_RESET` is not enabled in `config.json` |
| `400` | Mismatched passwords; too short; forbidden value |

---

## 14. System & Infrastructure

### `GET /api/workflow-monitor/status`

Returns the scheduled-job dependency graph and detected scheduling conflicts for the Settings → Workflow Monitor panel. Read-only.

**Request body:** none

**Response**

```json
{
  "status": "success",
  "nodes": [
    {
      "id": "ml_inference_job",
      "label": "Daily ML Inference",
      "category": "ml",
      "engine": "ai_prediction_engine.py",
      "produces": ["ml_predictions"],
      "consumes": ["quant_signals", "ml_model"],
      "enabled": true,
      "status": "green",
      "status_reason": "ok",
      "last_run": "2026-06-13 01:30",
      "last_run_display": "13 Jun 2026, 02:30",
      "next_run_display": "14 Jun 2026, 02:30",
      "avg_duration_sec": 92.4,
      "last_status": "success",
      "schedule": { "weekdays": [0, 1, 2, 3, 4], "minute_of_day": 90 }
    }
  ],
  "edges": [
    { "from": "overnight_quant_scan_job", "to": "ml_inference_job", "via": "quant_signals" }
  ],
  "conflicts": [
    {
      "type": "overlap_risk",
      "severity": "warning",
      "job_id": "ml_inference_job",
      "related": "overnight_quant_scan_job",
      "message": "Daily ML Inference starts 30 min after Daily Quant Screener (Portfolio & Watchlist), but it typically runs ~45 min — it may still be running."
    }
  ]
}
```

Most nodes are scheduled jobs (`status` of `green`/`amber`/`red`/`disabled`). Two `status` values represent non-job processes that are always `enabled: true` with no `schedule`: `external` (a data source outside the scheduler, e.g. Yahoo Finance) and `manual` (a hand-entered data source, e.g. Built-in Accounts' Manual Account Entry / Trading / Pension / House nodes).

`status` per node is one of `green` / `amber` / `red` / `disabled`. `conflicts[].type` is one of `overlap_risk`, `backwards_ordering`, `disabled_upstream`, `stale_never_run`, `last_run_error`; `severity` is `critical` / `warning` / `info`.

---

### `POST /api/maintenance/run`

Triggers the weekly `MaintenanceEngine` as a background task. Returns immediately; progress is visible in the Notifications panel.

**Request body:** none

**Response**

```json
{ "status": "success", "message": "Maintenance job started." }
```

---

### `POST /api/maintenance/dry-run`

Scans the data directories exactly as `MaintenanceEngine.garbage_collect_files()` would, but deletes nothing. Returns a preview of what would be removed.

**Request body:** none

**Response**

```json
{
  "status": "success",
  "days_to_keep_files": 60,
  "active_tickers_count": 4150,
  "would_delete": [
    { "file": "historical/AAPL.parquet", "ticker": "AAPL", "age_days": 90 }
  ],
  "would_keep_fresh": [
    { "file": "historical/TSLA.parquet", "ticker": "TSLA", "age_days": 5, "reason": "only 5d old (threshold: 60d)" }
  ],
  "summary": {
    "delete_count": 1,
    "keep_active_count": 4149,
    "keep_fresh_count": 1
  }
}
```

---

### `POST /api/backup/run`

Triggers an Automated Backup as a background task using the currently saved `SCHEDULING.BACKUP` config (location, components, retention). Returns immediately; the result (success/error, archive size) is dispatched via the Notification Router (`backup_status` source) and recorded in `backup_history`.

**Request body:** none

**Response**

```json
{ "status": "success", "message": "Backup started in the background. Check System Notifications for the summary." }
```

---

### `GET /api/backup/status`

Returns the most recent backup run plus the list of archives currently stored at the configured destination — feeds both the Backup Status diagnostics sub-panel and the Recovery file-selector dropdown.

**Response**

```json
{
  "status": "success",
  "last_backup": {
    "started_at": "2026-06-28 03:30:00", "finished_at": "2026-06-28 03:30:05",
    "trigger_type": "scheduled", "location_type": "local", "destination": "/app/backups",
    "components": "data,models,database", "filename": "backup_20260628_033000.tar.gz",
    "size_bytes": 1048576, "status": "success", "error_message": null
  },
  "stored_count": 7,
  "stored_size_bytes": 7340032,
  "backups": [
    { "filename": "backup_20260628_033000.tar.gz", "size_bytes": 1048576, "mtime": "2026-06-28 03:30:05" }
  ]
}
```

---

### `POST /api/backup/restore`

Extracts the named archive back into place, overwriting the live database, data files, and models with whatever components that archive contains. **Destructive — requires `X-Confirm-Token`.** Restart the service afterward so in-memory caches reload the restored data.

**Request body**

```json
{ "filename": "backup_20260628_033000.tar.gz" }
```

**Response**

```json
{ "status": "success", "message": "Restore completed from backup_20260628_033000.tar.gz. Restart the service so all in-memory caches reload the restored data." }
```

A filename containing `/` or `..` is rejected with `400` before any file is touched. A missing archive or extraction failure returns `500` with `status: "error"`.

---

### `GET /api/system/metrics`

Returns a comprehensive diagnostic snapshot of the system: universe coverage, ML model state, storage, and macro data counts.

**Response**

```json
{
  "status": "success",
  "universe": {
    "total": 4250,
    "index": 600,
    "freetrade": 1100,
    "sp500": 503,
    "ftse": 100,
    "coverage": {
      "stock_signals": 495,
      "quant_signals": 482,
      "ticker_metadata": 600,
      "asset_profiles": 598
    },
    "json_trackers": {
      "portfolio": 12,
      "watchlist": 28,
      "blacklist": 3
    },
    "fundamentals_files": 610
  },
  "ml": {
    "ensemble": {
      "exists": true,
      "mtime": "2026-05-25 09:15:00",
      "size_mb": 18.4
    },
    "feature_count": 32,
    "macro_hmm_outputs": 850,
    "macro_rf_outputs": 120
  },
  "infra": {
    "cpu": [0.45, 0.38, 0.30],
    "disk_used_gb": 42.1,
    "disk_total_gb": 500.0,
    "disk_pct": 8.4,
    "db_size_mb": 54.2,
    "hist_size_mb": 380.5,
    "hist_cnt": 4250,
    "intra_size_mb": 95.2,
    "intra_cnt": 4250
  },
  "state": {
    "macro_ind": 850,
    "macro_cal": 320,
    "notes_pending": 3,
    "notes_sent": 1240
  }
}
```

---

### `GET /api/system/checks`

Returns the current list of active system-health warnings and errors detected by the System Check Engine. Called on every Settings page load to populate the top-of-page banner.

**Authentication:** none required (read-only)

**Response**

```json
{
  "status": "success",
  "issues": [
    {
      "key": "ml_training_without_backfill",
      "level": "warning",
      "message": "ML Training is scheduled but ML Historical Backfill is disabled. ..."
    }
  ]
}
```

`issues` is an empty array when no problems are detected. Each issue has:

| Field | Type | Description |
|---|---|---|
| `key` | string | Machine-readable identifier (`ml_training_without_backfill`, `ml_training_before_backfill`, `low_inference_coverage`) |
| `level` | `"warning"` \| `"error"` | Severity |
| `message` | string | Human-readable description with remediation hint |

---

### `GET /api/system/market-status`

At-a-glance market/system status for the Home Assistant integration's sensors. `us_market_open`/`uk_market_open` use `market_pulse.is_exchange_open("NYSE"/"LSE")`, which prefers the live Yahoo `marketState` cached from that exchange's proxy index ticker (`^GSPC` for NYSE, `^FTSE` for LSE, refreshed into `market_pulse_cache.market_state` by `fetch_and_save_pulse()`) — `market_state == "REGULAR"` means open. This is exchange-holiday-aware (e.g. correctly reports NYSE closed on a day it observes a holiday, which the old weekday/hours-only check could not detect), and falls back to `time_engine.is_trading_session(exchange)` only when no live state has been cached yet (e.g. immediately after a fresh install, before the first market-pulse fetch). This endpoint self-triggers a background `fetch_and_save_pulse()` for whichever of the two proxy tickers has a missing/stale (>5 min) `market_state` (`market_pulse.proxy_tickers_needing_refresh()`) — the same self-refresh pattern `GET /api/market-pulse` and the accounts endpoints already use — so a caller that only ever polls this endpoint (e.g. Home Assistant, with no browser dashboard open to drive `/api/market-pulse`'s own JS polling) still keeps the cache warm rather than depending on something else fetching those two tickers. `yahoo_ok` reflects real Yahoo Finance call history from `database.get_yahoo_api_stats()` (at least one non-error call recorded on the most recent tracked day), not the in-process cache hit-rate, which reads 0% right after every server restart regardless of Yahoo's actual health. `system_ok` is `len(issues) == 0` from the same System Check Engine used by `GET /api/system/checks`.

**Authentication:** none required beyond the global `X-API-Key`/session middleware (read-only)

**Response**

```json
{
  "status": "success",
  "us_market_open": true,
  "uk_market_open": false,
  "yahoo_ok": true,
  "system_ok": true
}
```

---

### `POST /api/system/git-pull`

Pulls the latest code from the Git remote. Returns the git output. Also diffs the pre-pull and post-pull commits to detect whether `requirements.txt` changed; if so, `requirements_changed` is `true` and a pending flag is set so the next restart (see below) reinstalls dependencies automatically before shutting down.

**Request body:** none

**Response (success)**

```json
{
  "status": "success",
  "message": "Update successful. Please restart the service if required.\n\nAlready up to date.",
  "requirements_changed": false
}
```

---

### `GET /api/system/active-jobs`

Returns a snapshot of all scheduler jobs that are currently executing. The registry is updated in-memory by each job function; it is cleared on restart.

**Response**

```json
{
  "status": "success",
  "busy": true,
  "active_jobs": {
    "Global Model Training (Walk-Forward)": "2026-06-10T14:32:01",
    "Daily Quant Screener (Portfolio & Watchlist)": "2026-06-10T14:28:45"
  },
  "requirements_changed_pending": false
}
```

`active_jobs` is an empty object `{}` when the server is idle. Timestamps are UTC ISO-8601 strings representing when each job started. `requirements_changed_pending` mirrors the flag set by the last `POST /api/system/git-pull` — the Settings page polls this endpoint every 30 seconds to display a live status indicator and the "dependencies will be reinstalled on restart" warning banner.

---

### `POST /api/system/restart`

Sends a `SIGTERM` to the running process after a 2-second delay, triggering a graceful shutdown. The process manager (e.g. systemd or Docker) is expected to restart it automatically. If the last git pull changed `requirements.txt`, runs `pip install -r requirements.txt` in the current interpreter's environment first and dispatches a `system_update_status` notification with the outcome.

Returns HTTP **409** if any scheduler jobs are currently running (see `GET /api/system/active-jobs`), with a message listing the active processes. In that case the restart is not initiated.

**Request body:** none

**Response (idle)**

```json
{
  "status": "success",
  "message": "Restart signal sent. The dashboard will be back online in ~5-10 seconds."
}
```

**Response (busy — HTTP 409)**

```json
{
  "status": "busy",
  "message": "Cannot restart: Global Model Training (Walk-Forward) is currently running. Please wait for it to complete and try again."
}
```

---

## 15. Log Viewer

These endpoints expose the active rotating log file (`app.log`) for the in-browser live viewer. Both endpoints require a valid session (same auth as all other routes).

### `GET /api/logs/tail`

Returns the last N lines of the active log file as a JSON array. Intended for the initial page load of the log viewer.

**Query parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `lines` | integer | `500` | Number of tail lines to return. Min 1, max 5 000. |

**Response (logging enabled, file exists)**

```json
{
  "status": "success",
  "lines": [
    "2026-06-13 10:23:45,123 - quant_engine - INFO - Scan complete for AAPL",
    "2026-06-13 10:23:46,001 - api_routes - WARNING - Slow response: 3.2s"
  ]
}
```

**Response (logging disabled or log file missing)**

```json
{ "status": "error", "message": "File logging is disabled or log file not found." }
```

---

### `GET /api/logs/stream`

Server-Sent Events (SSE) endpoint that tails the active log file in real time, equivalent to `tail -f`. The client receives one `data:` event per new log line. A `: keep-alive` comment is sent every second when there are no new lines.

**Media type:** `text/event-stream`

**Event format**

Each event carries a JSON-encoded log line string:

```
data: "2026-06-13 10:24:00,000 - scheduler_engine - INFO - Job started"
```

When file logging is disabled, a single error event is emitted and the stream closes:

```
data: {"error": "File logging is disabled or log file not found."}
```

---

## 16. Alert Testing

These endpoints fire real alerts to verify that external integrations (Nextcloud Talk, email) are working. They make live network calls.

### `POST /api/test-sentiment-alert`

Sends a test sentiment alert via Nextcloud Talk. Verifies the webhook URL, bot credentials, and message formatting.

**Request body:** none

**Response (success)**

```json
{
  "status": "success",
  "message": "Sentiment alert delivered successfully."
}
```

---

### `POST /api/test-earnings-alert`

Sends a test earnings alert via Nextcloud Talk.

**Request body:** none  
**Response:** same shape as `/test-sentiment-alert`

---

### `POST /api/test-insider-alert`

Sends a test insider trading alert via Nextcloud Talk.

**Request body:** none  
**Response:** same shape as `/test-sentiment-alert`

---

## Appendix A — Quick Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/api/notifications/latest` | Poll for new system notifications |
| `POST` | `/api/notifications/mark-read` | Mark all notifications as read |
| `POST` | `/api/notifications/purge` | Delete all notifications |
| `POST` | `/api/update` | Full portfolio pipeline update |
| `POST` | `/api/sync-ghostfolio` | Sync Ghostfolio holdings |
| `POST` | `/api/ghostfolio/discover` | Discover Ghostfolio accounts |
| `POST` | `/api/watchlist/add` | Add ticker to watchlist (native, via Watchlist account) |
| `POST` | `/api/watchlist/remove` | Remove ticker from watchlist (native, via Watchlist account) |
| `GET` | `/api/ticker-search` | Company-name/ticker autocomplete for add-ticker UI |
| `GET` | `/api/accounts/{id}/watchlist-items` | List a Watchlist account's tickers |
| `POST` | `/api/accounts/{id}/watchlist-items` | Add a ticker to a Watchlist account |
| `POST` | `/api/accounts/{id}/watchlist-items/bulk-delete` | Bulk-delete tickers from a Watchlist account |
| `POST` | `/api/ticker/{ticker}/name-override` | Set or clear a user display name override |
| `POST` | `/api/trigger-freetrade-sync` | Sync Freetrade securities |
| `POST` | `/api/data/refresh-single` | Deep refresh one ticker |
| `GET` | `/api/freshness` | Model and price data freshness |
| `POST` | `/api/trigger-quant-scan` | Quant scan (portfolio/watchlist) |
| `POST` | `/api/trigger-universe-quant-scan` | Quant scan (full universe) |
| `POST` | `/api/trigger-earnings-scan` | Earnings volatility scan |
| `POST` | `/api/trigger-sentiment-scan` | FinBERT sentiment scan |
| `POST` | `/api/trigger-morning-briefing` | Generate and dispatch morning briefing |
| `POST` | `/api/trigger-lunch-briefing` | Generate and dispatch lunchtime briefing |
| `POST` | `/api/save-hf-token` | Persist HuggingFace API token to `.env` |
| `POST` | `/api/save-fred-api-key` | Persist FRED API key to `.env` |
| `POST` | `/api/save-ghostfolio-settings` | Persist Ghostfolio URL + token to `.env` |
| `POST` | `/api/save-nextcloud-settings` | Persist Nextcloud Talk credentials to `.env` |
| `POST` | `/api/test-hf-token` | Verify HuggingFace token via `whoami` |
| `GET` | `/api/screener-data` | Latest quant screener results |
| `GET` | `/api/market-pulse` | Live market index pulse (cached) |
| `POST` | `/api/market-pulse` | Live pulse for custom tickers |
| `GET` | `/api/reports/quality-compounders` | Quality compounder stock list |
| `GET` | `/api/reports/quality-on-sale` | Quality stocks at a discount |
| `GET` | `/api/reports/garp-tenbaggers` | GARP 10-bagger candidates |
| `GET` | `/api/reports/sectors` | Sector trend breakdown |
| `GET` | `/api/reports/mean-reversion` | Oversold mean-reversion setups |
| `GET` | `/api/reports/leaders` | Market leaders and laggards |
| `GET` | `/api/reports/dividends` | Dividend harvest setups |
| `POST` | `/api/ml/trigger-backfill` | Build ML feature history |
| `POST` | `/api/ml/trigger-training` | Train ML ensemble model |
| `POST` | `/api/ml/trigger-inference` | Run daily ML predictions |
| `POST` | `/api/macro/init-pipeline` | First-time macro AI setup |
| `POST` | `/api/macro/run-pipeline` | Daily macro AI update |
| `GET` | `/api/universe/profiler-status` | Fundamentals profiler queue |
| `POST` | `/api/trigger-universe-update` | Refresh universe ticker list |
| `POST` | `/api/universe/sync-indices` | Scrape S&P 500 + FTSE 100 |
| `POST` | `/api/universe/sync-profiler` | Run fundamentals profiler |
| `POST` | `/api/universe/deep-sync` | Full universe deep sync pipeline |
| `GET` | `/api/universe/imports/list` | List importable CSV files |
| `POST` | `/api/universe/import/server` | Import universe from CSV |
| `GET` | `/api/options/chain/{ticker}` | Live options chain |
| `POST` | `/api/options/payoff` | Options payoff matrix |
| `GET` | `/api/ai-prompt/market-regime` | AI prompt for Market Regime HMM page |
| `GET` | `/api/ai-prompt/market-sentiment/us` | AI prompt for US Market Sentiment |
| `GET` | `/api/ai-prompt/market-sentiment/uk` | AI prompt for UK Market Sentiment |
| `GET` | `/api/ai-prompt/{ticker}` | AI-consumable analysis prompt (stock) |
| `POST` | `/api/settings` | Save configuration |
| `POST` | `/api/settings/test-yahoo-ipv6` | Test IPv6 connection |
| `POST` | `/api/backup/run` | Run an Automated Backup now |
| `GET` | `/api/backup/status` | Last backup result + stored archive list |
| `POST` | `/api/backup/restore` | Restore from a backup archive (destructive) |
| `GET` | `/api/settings/network-status` | Current routing health and mode |
| `GET` | `/api/system/yahoo-api-stats` | Daily Yahoo Finance API call counts |
| `GET` | `/api/ui-theme.css` | Dynamic font-size CSS variables |
| `GET` | `/api/system/metrics` | System diagnostic data |
| `GET` | `/api/system/checks` | Active scheduling health warnings/errors |
| `GET` | `/api/system/active-jobs` | Currently executing scheduler jobs (busy indicator) |
| `POST` | `/api/system/git-pull` | Pull latest code from git |
| `POST` | `/api/system/restart` | Graceful application restart (409 if jobs running) |
| `POST` | `/api/test-sentiment-alert` | Test Nextcloud sentiment alert |
| `POST` | `/api/test-earnings-alert` | Test Nextcloud earnings alert |
| `POST` | `/api/test-insider-alert` | Test Nextcloud insider alert |
| `GET` | `/api/fx-drag` | FX-decomposed return breakdown for all USD portfolio positions |
| `GET` | `/api/news-feed` | Paginated news articles from local store |
| `POST` | `/api/news-feed/run-now` | Trigger immediate news feed refresh |
| `GET` | `/api/logs/tail` | Last N lines of the active log file as JSON |
| `GET` | `/api/logs/stream` | Server-Sent Events live tail of the active log file |

---

## Appendix B — Key Data Types

### Ticker format

Tickers follow Yahoo Finance conventions:
- US equities: `AAPL`, `MSFT`, `TSLA`
- LSE equities: `LLOY.L`, `BARC.L`
- Indices: `^GSPC` (S&P 500), `^FTSE` (FTSE 100), `^VIX`
- FX rates: `GBPUSD=X`
- ETFs: `SPY`, `ISF.L`

Path-parameter endpoints enforce the pattern `^[A-Z0-9.\-\^=]{1,20}$`.

### Composite score

The composite score is an integer in the range `-100` to `+100` computed by the quant engine. Signal labels:

| Score range | Label |
|-------------|-------|
| ≥ 40 | Strong Buy |
| 20 – 39 | Bullish |
| 0 – 19 | Neutral |
| -30 – -1 | Bearish |
| < -30 | Strong Sell |

### ML confidence score

The ML model outputs a probability (0–100) that the ticker will return more than 3% over the next 10 trading days. Scores below 40 are vetoed by the screener.

### VaR / CVaR

- **VaR 95** (`var_95`): the minimum loss expected on the worst 5% of trading days, expressed as a decimal fraction of the position value (e.g. `0.024` = 2.4% daily loss).
- **CVaR 95** (`cvar_95`): the average loss on those worst 5% of days (always ≥ VaR). Also called Expected Shortfall.

---

## 16. AI Sector Contagion Monitor

### `GET /ai-contagion`

HTML page. Renders the AI Sector Contagion Monitor with 30-day normalised performance, intraday performance (when market is open), and a 20-day pairwise correlation heatmap for: NVDA, AMD, AVGO, GOOGL, MSFT, META, AAPL, ORCL, AMZN, TSLA.

For methodology details see [`assets/ai_contagion_monitor.md`](ai_contagion_monitor.md).

---

## 17. Market Trap & Recovery Monitor

Detects four post-crash lifecycle phases from daily OHLCV data: Bull Trap (Dead Cat Bounce), Bear Trap (False Breakdown), Capitulation (Volume Climax), and Wyckoff Accumulation (BB Squeeze). Covers portfolio tickers plus a configurable proxy basket.

For full methodology, configuration reference, and alerting details see [`assets/bull_bear_trap_monitor.md`](bull_bear_trap_monitor.md).

### `GET /trap-monitor`

HTML page. Renders the unified Market Trap & Recovery Monitor with lifecycle arc diagram, active alert strip, and a full ticker status table showing all four signal columns.

### `GET /api/trap-monitor/results`

Returns all rows from `trap_monitor_results`, sorted by phase severity (most severe first).

**Response**

```json
{
  "status": "success",
  "results": [
    {
      "ticker": "NVDA",
      "phase": "BULL_TRAP_RISK",
      "bull_trap_level": "SEVERE_TRAP_RISK",
      "bull_trap_vol_ratio": 0.68,
      "bull_trap_notes": "Vol ratio 0.68 — recovery volume severely below sell-off volume. RSI 44 still below 50.",
      "bear_trap_level": "SAFE",
      "cap_level": "NONE",
      "wyckoff_level": "NONE",
      "ema_distance": -4.2,
      "rsi": 44.0,
      "scan_ts": "2026-06-10 14:30:00"
    }
  ]
}
```

### `POST /api/trap-monitor/run`

Triggers a background scan immediately. Returns `{"status": "success"}` immediately; results appear in `/api/trap-monitor/results` within a few seconds.

### `GET /api/trap-monitor/accuracy`

Returns per-phase prediction accuracy at 14-day and 30-day forward-return horizons, aggregated from `trap_phase_history`. Phase entries with zero resolved predictions show `null` for `accuracy_*` fields.

**Response:**
```json
{
  "status": "success",
  "phases": [
    {
      "phase": "BULL_TRAP_RISK",
      "total": 42,
      "resolved_14d": 35,
      "accuracy_14d": 71.4,
      "resolved_30d": 20,
      "accuracy_30d": 75.0
    }
  ],
  "overall": {
    "total": 102,
    "resolved_14d": 83,
    "accuracy_14d": 72.3,
    "resolved_30d": 45,
    "accuracy_30d": 74.1
  }
}
```

---

## 18. Bubble Radar

Valuation-euphoria detector that scans the portfolio and watchlist for tickers exhibiting extreme overextension across seven metrics. Scores are stored daily in `bubble_radar_metrics`; flag history and prediction accuracy live in `bubble_radar_history`.

### `GET /api/bubble-radar/data`

Returns all tickers with an active bubble flag (`flag IN ('watch', 'bubble')`) from the most recent scan, ordered by `bubble_score DESC`.

**Response:**
```json
{
  "status": "success",
  "results": [
    {
      "ticker": "NVDA",
      "company_name": "NVIDIA Corporation",
      "scan_date": "2026-06-16",
      "bubble_score": 87.5,
      "flag": "bubble",
      "sma_ext_pct": 38.2,
      "rsi_avg_20d": 76.1,
      "ps_ratio": 24.3,
      "peg_ratio": 3.1,
      "fcf_yield": 1.2,
      "riskfree_rate": 2.1,
      "iv_call_skew": 1.31,
      "spy_rsp_spread": 4.8
    }
  ]
}
```

### `GET /api/bubble-radar/ticker/{ticker}`

Returns the latest Bubble Risk Score row plus a per-metric breakdown for a single ticker.

**Response:**
```json
{
  "status": "success",
  "result": {
    "ticker": "NVDA",
    "scan_date": "2026-06-16",
    "bubble_score": 87.5,
    "flag": "bubble",
    "sma_ext_pct": 38.2,
    "rsi_avg_20d": 76.1,
    "metric_scores": {
      "sma_ext": { "label": "SMA-200 Extension", "value": 38.2, "score": 17 },
      "rsi":     { "label": "RSI (20d avg)",      "value": 76.1, "score": 15 },
      "ps":      { "label": "Price/Sales",         "value": 24.3, "score": 12 },
      "peg":     { "label": "PEG Ratio",           "value": 3.1,  "score": 15 },
      "fcf_yield":{ "label": "FCF Yield Gap",      "value": -0.9, "score": 10 },
      "iv_skew": { "label": "IV Call Skew",        "value": 1.31, "score": 8  },
      "spy_rsp": { "label": "SPY–RSP Spread",      "value": 4.8,  "score": 0  }
    }
  }
}
```

Returns `{"status": "success", "result": null}` if the ticker has not been scanned yet.

### `GET /api/bubble-radar/history`

Returns the last 200 flag events from `bubble_radar_history`, ordered by `flagged_date DESC`. Includes back-filled outcome fields once enough time has elapsed.

**Response:**
```json
{
  "status": "success",
  "results": [
    {
      "ticker": "NVDA",
      "company_name": "NVIDIA Corporation",
      "flagged_date": "2026-05-01",
      "flag_level": "bubble",
      "price_at_flag": 950.00,
      "price_4w": 880.00,
      "outcome_4w": "correct",
      "price_8w": null,
      "outcome_8w": null,
      "price_12w": null,
      "outcome_12w": null
    }
  ]
}
```

### `POST /api/bubble-radar/run`

Triggers an immediate background Bubble Radar scan across all portfolio and watchlist tickers. Returns immediately; results are visible in `/api/bubble-radar/data` within a few seconds.

**Response:** `{"status": "success", "message": "Bubble Radar scan started."}`

---

## 19. Market Regime (HMM + Market Stress IF)

Price-action Hidden Markov Model (3 states: Bull / Chop / Crash) fitted on 5-year SPY returns and EWMA volatility, plus a market-wide Isolation Forest stress score. Both are updated daily as part of the quant pipeline. The IF score (`market_stress_score`, REAL [0,1]) and contributing features (`market_stress_features`, JSON) are stored in the `market_regimes` table alongside the HMM columns.

### `GET /api/market-regime/current`

Returns the latest HMM regime state and the most recent regime transition. Lightweight; used by the Trap Monitor panel.

**Response:**
```json
{
  "status": "success",
  "current": { "state": 0, "label": "Bull", "probability": 0.87, "as_of": "2026-06-11" },
  "last_change": { "date": "2026-05-14", "from_label": "Chop", "to_label": "Bull" }
}
```

### `GET /api/market-regime`

Returns full Viterbi history, empirical transition matrix, and per-regime return/vol statistics.

**Response fields:** `current`, `last_change`, `history` (array of `{date, state, label, probability}`), `transition_matrix` (3×3 array), `regime_stats` (`{Bull, Chop, Crash}` each with `days`, `mean_daily_return`, `mean_vol`).

### `POST /api/market-regime/run`

Triggers `run_price_regime_hmm()` as a background task. Returns `{"status": "success"}` immediately.

### `GET /api/market-stress`

Returns the latest market-wide Isolation Forest stress score and the last 30 daily values. Used by the Market Stress panel on `/trap-monitor`.

**Response:**
```json
{
  "status": "success",
  "current": {
    "score": 0.42,
    "features": {
      "vix_level": 18.4, "vix_ma_ratio": 0.92, "hyg_return": -0.12,
      "tnx_change": 0.02, "spy_vol_zscore": 0.8, "spy_return": -0.31
    },
    "date": "2026-06-11"
  },
  "history": [{ "date": "2026-05-13", "score": 0.38 }, "..."]
}
```
`current` is `null` when no score has been computed yet (first run pending). `history` is ordered oldest-first, up to 30 entries.

---

## 20. FX Drag Analyzer

Decomposes each USD portfolio position's GBP return into equity (USD price move) and FX (GBP/USD rate move) components. Uses existing 2-year daily Parquet data — no additional data source required.

### `GET /api/fx-drag`

Returns an FX-decomposed return breakdown for all USD positions in `portfolio.json`.

**Query parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `period` | string | `"ytd"` | Lookback period: `"ytd"` (year-to-date), `"1y"` (365 days), `"2y"` (730 days), `"lifetime"` (purchase-date FX via Ghostfolio) |

**Reference-period response** (`ytd` / `1y` / `2y`):

```json
{
  "status": "success",
  "period": "ytd",
  "data": [
    {
      "ticker": "AAPL",
      "period_days": 170,
      "equity_pct": 12.34,
      "fx_pct": -2.10,
      "total_gbp_pct": 9.98,
      "ref_date": "2026-01-02",
      "gbpusd_ref": 1.2520,
      "gbpusd_now": 1.2786,
      "gbp_exposure": 4231.00
    }
  ]
}
```

**Lifetime response** (`lifetime`):

```json
{
  "status": "success",
  "period": "lifetime",
  "data": [
    {
      "ticker": "AAPL",
      "period_days": null,
      "equity_pct": 45.20,
      "fx_pct": -8.10,
      "total_gbp_pct": 33.46,
      "gbpusd_buy": 1.3680,
      "gbpusd_now": 1.2786,
      "earliest_buy": "2021-01-02",
      "buy_count": 3,
      "gbp_exposure": 4231.00,
      "ref_date": "2021-01-02"
    }
  ]
}
```

`equity_pct`: stock price change in USD since the reference date / purchase VWAP.  
`fx_pct`: GBP/USD rate change — positive means USD strengthened (tailwind), negative means GBP strengthened (headwind).  
`total_gbp_pct`: `(1 + equity_pct/100) × (1 + fx_pct/100) − 1`, expressed as a percentage.  
`gbp_exposure`: current GBP market value of the position (null if price data unavailable).  
`gbpusd_buy` (lifetime only): quantity-weighted average GBP/USD rate across all BUY trades, derived from `unitPrice` / `unitPriceInAssetProfileCurrency` in Ghostfolio activities.  
`earliest_buy` / `buy_count` (lifetime only): date of earliest BUY trade and total BUY trade count.  
Tickers with no Parquet data or no Ghostfolio BUY activities are omitted from the list.

---

## 21. News Feed

Stores and retrieves news articles for portfolio and watchlist tickers. Articles are fetched via yfinance, full-text extracted via trafilatura, and sentiment-scored via FinBERT. Results are stored in the `news_articles` table.

### `GET /api/news-feed`

Returns paginated news articles from the local `news_articles` table.

**Query parameters**

| Parameter | Type | Default | Description |
|-----------|------|---------|-------------|
| `source` | string | `"all"` | Filter by source list: `"portfolio"`, `"watchlist"`, or `"all"` |
| `page` | int | `1` | Page number (1-based) |
| `per_page` | int | `20` | Items per page |

**Response**

```json
{
  "status": "success",
  "total": 87,
  "page": 1,
  "per_page": 20,
  "articles": [
    {
      "id": 1,
      "article_id": "abc123...",
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "source_list": "portfolio",
      "headline": "Apple Reports Record Earnings",
      "summary": "...",
      "url": "https://...",
      "publisher": "Reuters",
      "published_at": 1717840000,
      "sentiment_score": 0.72,
      "sentiment_label": "positive",
      "body_fetched": 1
    }
  ]
}
```

---

### `POST /api/news-feed/run-now`

Triggers an immediate news feed refresh in the background. Fetches news for all portfolio and watchlist tickers, extracts full article text, and runs FinBERT sentiment scoring on unscored rows.

**Request body:** none  
**Response:** `{"status": "success", "message": "News feed refresh triggered."}`

---

---

## 20. Historical Stress Tester

Simulates portfolio impact during historical market crashes using a beta-adjusted scenario shock model. Each holding's estimated drop is computed as `market_drop × beta × sector_multiplier`. Sector multipliers are calibrated per scenario (e.g. Technology ×2.2 in the dot-com crash, Energy ×-0.6 in 2022). No historical price data is required — the model reads beta from `xray_risk_cache`. On-demand only; results are not persisted.

### `GET /stress-test`

HTML page. Renders the stress-test scenario selector, account scope picker, and results panel (populated by JS on demand).

### `GET /api/stress-test/scenarios`

Returns the full set of built-in scenarios.

**Response**

```json
{
  "status": "success",
  "scenarios": {
    "gfc_2008": {
      "name": "Global Financial Crisis (2008–09)",
      "market_drop": -0.57,
      "duration_days": 517,
      "recovery_months": 49,
      "sector_multipliers": { "Financial Services": 1.8, "Technology": 1.1 },
      "description": "..."
    },
    "dotcom_2000": { "market_drop": -0.49, ... },
    "covid_2020":  { "market_drop": -0.34, ... },
    "inflation_2022": { "market_drop": -0.25, ... },
    "custom": { "description": "..." }
  }
}
```

### `POST /api/stress-test/run`

Runs the stress simulation for the requested scenario and portfolio scope.

**Request body**

```json
{
  "account_id": "all",
  "scenario_id": "gfc_2008",
  "custom_drop": null
}
```

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `account_id` | string | `"all"` | `"all"` (every configured source — Ghostfolio if configured + every built-in Trading account, combined), a Ghostfolio account UUID, or `acct:{id}` for one built-in Trading account only |
| `scenario_id` | string | required | One of `gfc_2008`, `dotcom_2000`, `covid_2020`, `inflation_2022`, `custom` |
| `custom_drop` | float \| null | `null` | Required when `scenario_id == "custom"`. Decimal e.g. `-0.30` for a 30% crash |

Holdings for the requested scope are resolved via `xray_engine.resolve_scope_holdings()` — the same helper used by `assemble_xray_report()` and the Monte Carlo accounts endpoint — so this works whether Ghostfolio is enabled, disabled, or absent.

**Response**

```json
{
  "status": "success",
  "result": {
    "scenario": { "name": "...", "market_drop": -0.57, "recovery_months": 49 },
    "scenario_id": "gfc_2008",
    "account_id": "all",
    "portfolio_value": 52300.00,
    "portfolio_currency": "GBP",
    "estimated_loss": -22450.00,
    "estimated_loss_pct": -42.93,
    "holdings": [
      {
        "symbol": "NVDA", "name": "NVIDIA Corporation",
        "weight": 0.08, "value": 4184.0, "beta": 1.72,
        "sector": "Technology", "sector_multiplier": 1.1,
        "estimated_drop_pct": -107.9, "estimated_loss": -4515.3
      }
    ],
    "sector_impact": [
      { "sector": "Technology", "weight": 0.42, "estimated_loss": -12300.0 }
    ],
    "data_warnings": ["Beta not cached for 2 holding(s) — assumed β=1.0."],
    "generated_at": "2026-06-10T14:30:00+00:00"
  }
}
```

Returns HTTP 400 if `scenario_id` is unknown or `custom_drop` is missing for a custom scenario.

---

## 20a. Portfolio X-ray

Risk/diagnostics view for the Portfolio page, combining holdings from Ghostfolio and/or built-in Trading accounts with SQLite-cached risk stats (beta, volatility, correlation, VaR). Implemented in `xray_engine.py`; risk cache populated by the nightly `xray_risk_cache_job` scheduler job (19:00).

### `GET /api/xray`

Returns the full X-ray report JSON for the given account scope.

**Query params:**

| Param | Meaning |
|-------|---------|
| `account_id=all` (default) | Every configured source — Ghostfolio (if configured) + every built-in Trading account, combined (same ticker from both sources is summed) |
| `account_id=<ghostfolio-uuid>` | One active Ghostfolio account only |
| `account_id=acct:{id}` | One built-in Trading account only (no Ghostfolio) |

Historical VaR, CVaR, Sharpe/Calmar ratio, tracking error and skewness are derived at request time from per-ticker cached daily returns (`xray_returns_cache`), weighted by the current scope's holdings — this works for any scope (Ghostfolio, built-in, or combined). They are `null` with an entry in `data_warnings` only when fewer than 30 overlapping cached trading days exist across the in-scope tickers (e.g. newly-added holdings, before the next nightly risk cache run). Sector and country/continent breakdowns for built-in holdings come from `asset_profiles` as a single 100%-weight bucket per holding (no Ghostfolio-style ETF look-through decomposition).

Returns `503` with `{"error": "..."}` if the resolved scope has no holdings (e.g. Ghostfolio not configured and no built-in Trading accounts exist).

Rate limit: 10/minute.

### `POST /api/xray/trigger`

Manually triggers the X-ray risk cache pre-compute job in the background (recomputes beta/vol/correlation for every ticker across portfolio.json and built-in Trading accounts). Returns `{"status": "queued", "message": "..."}` immediately; progress visible in Notifications.

---

## 21. Macro Regime & Yield Curve Allocator

Synthesises live macro signals (yield curve, CPI, HY credit spread, real yield, HMM state) into a named economic regime label and returns the historically optimal asset class allocation for that regime. Requires the Macro Data Engine to have run at least once (`POST /api/macro/run-pipeline`). Portfolio alignment requires Ghostfolio to be configured.

### `GET /api/macro-regime-allocation`

Returns the full regime allocation payload. Consumed by the Portfolio X-ray panel (the standalone `/macro-allocator` page has been removed — regime content is now embedded in the X-ray view on `/portfolio`).

**Response:**
```json
{
  "status": "ok",
  "regime_label": "Late Cycle",
  "regime_date": "2026-06-10",
  "yield_curve_inverted": false,
  "days_inverted": 0,
  "us_threat_level": "YELLOW",
  "uk_threat_level": "GREEN",
  "key_signals": {
    "us_yield_curve": 0.14,
    "us_cpi_inflation": 3.8,
    "us_high_yield_spread": 380.0,
    "us_fed_funds_rate": 5.33,
    "us_real_yield_10y": 1.85,
    "uk_base_rate": 5.25
  },
  "ideal_allocation": { "equities": 57.5, "bonds": 27.5, "commodities": 10.0, "cash": 10.0 },
  "regime_ranges": {
    "equities": [50.0, 65.0], "bonds": [20.0, 35.0], "commodities": [5.0, 15.0], "cash": [5.0, 15.0]
  },
  "current_allocation": { "equities": 71.2, "bonds": 8.3, "commodities": 2.1, "cash": 18.4 },
  "alignment_score": 62,
  "rebalance_deltas": { "equities": -13.7, "bonds": 19.2, "commodities": 7.9, "cash": -8.4 },
  "portfolio_note": null,
  "regime_history": [
    { "date": "2026-06-10", "regime_label": "Late Cycle" }
  ]
}
```

`current_allocation`, `alignment_score`, and `rebalance_deltas` are `null` when Ghostfolio is not configured; `portfolio_note` then contains a human-readable explanation. Returns `{"status": "no_data"}` if the macro data engine has never run.

Rate limit: 30/minute.

---

## 22. ETF Price Predictor

Generic ETF next-session open price predictor. Multiple predictor configurations can be added from Settings → Tools → ETF Price Predictors. Each config specifies an ETF ticker and up to 20 constituent tickers with weights. Results accessible at `/etf-predictor`.

### `GET /api/etf-predictors`

Returns all non-deleted predictor configurations.

**Response:**
```json
{
  "status": "success",
  "configs": [
    {
      "id": 1,
      "name": "VUSA S&P500 Predictor",
      "etf_ticker": "VUSA.L",
      "constituents": [{"ticker": "AAPL", "weight": 0.07}, "..."],
      "enabled": 1,
      "auto_schedule": 1,
      "pre_run_time": "13:30",
      "post_run_time": "22:00",
      "deleted_at": null,
      "created_at": "2026-06-10 10:00:00"
    }
  ]
}
```

Rate limit: 20/minute.

---

### `POST /api/etf-predictors`

Create a new predictor configuration. Weights are normalised to sum=1.0 automatically.

**Request body:**
```json
{
  "name": "VUSA S&P500 Predictor",
  "etf_ticker": "VUSA.L",
  "constituents": [
    {"ticker": "AAPL", "weight": 7.0},
    {"ticker": "MSFT", "weight": 6.8}
  ],
  "enabled": true,
  "auto_schedule": true,
  "pre_run_time": "13:30",
  "post_run_time": "22:00"
}
```

Returns `{"status": "success", "message": "...", "id": <new_config_id>}`. Returns 422 if `constituents` is empty or all weights are zero.

Rate limit: 10/minute.

---

### `PUT /api/etf-predictors/{id}`

Update an existing predictor configuration. Same body as POST. Returns 404 if not found or soft-deleted.

Rate limit: 10/minute.

---

### `DELETE /api/etf-predictors/{id}`

Soft-deletes the predictor configuration (sets `deleted_at`). Prediction history is preserved. Unregisters any scheduled APScheduler jobs for this config.

Returns 404 if not found.

Rate limit: 10/minute.

---

### `POST /api/etf-predictors/validate`

Validates an ETF ticker and its constituent list against Yahoo Finance. Returns per-ticker validity, resolved name, total weight, and a `weight_ok` flag (true if total is within 1% of 100 or 0.01 of 1.0).

**Request body:**
```json
{
  "etf_ticker": "VUSA.L",
  "constituents": [{"ticker": "AAPL", "weight": 7.5}, ...]
}
```

**Response:**
```json
{
  "status": "success",
  "etf": {"ticker": "VUSA.L", "valid": true, "name": "Vanguard S&P 500 UCITS ETF"},
  "constituents": [{"ticker": "AAPL", "valid": true, "name": "Apple Inc.", "weight": 7.5}, ...],
  "total_weight": 100.0,
  "weight_ok": true
}
```

Rate limit: 10/minute.

---

### `POST /api/etf-predictors/{id}/run`

Triggers a prediction run as a background task. Returns immediately; progress visible in Notifications.

**Response:** `{"status": "success", "message": "ETF predictor {id} run initiated."}`

Returns 404 if not found.

Rate limit: 5/minute.

---

### `POST /api/etf-predictors/{id}/fill-actuals`

Triggers an actuals-fill pass for this config as a background task. Fetches the current ETF price and fills `actual_open` for unresolved prediction rows.

Returns 404 if not found.

Rate limit: 5/minute.

---

### `GET /api/etf-predictors/{id}/predictions`

Returns prediction history and accuracy summary for a specific config.

**Response:**
```json
{
  "status": "success",
  "next_open": {
    "rows": [
      {
        "id": 42,
        "target_date": "2026-06-11",
        "predicted_price": 8543.21,
        "actual_open": 8512.00,
        "pct_error": 0.37,
        "direction_correct": 1,
        "signal_source": "intraday_post_close"
      }
    ],
    "summary": {
      "total_predictions": 45,
      "resolved_count": 40,
      "direction_accuracy_pct": 62.5,
      "mae": 31.4,
      "mape_pct": 0.38,
      "last_10_direction_pct": 70.0,
      "last_30_direction_pct": 60.0
    }
  },
  "us_open_impact": { "rows": [], "summary": {} }
}
```

Returns 404 if config not found.

Rate limit: 20/minute.

---

### `GET /etf-predictor`

HTML page. Renders tile grid — one tile per configured predictor with last prediction, last actual, and direction accuracy summary.

### `GET /etf-predictor/{id}`

HTML page. Renders the detail view for a single predictor: accuracy metrics, prediction history table, Plotly accuracy-over-time chart, and ETF composition.

---

## 23. Forensic Screener

Monthly accounting forensics across portfolio and watchlist tickers. Computes Piotroski F-Score (0–9), Altman Z-Score (bankruptcy risk), and Beneish M-Score (earnings manipulation) from annual financial statements. Scores are stored in the `stock_signals` table and displayed at `/forensic-screener`.

### `GET /api/forensic-scores`

Returns the latest forensic scores for all portfolio and watchlist tickers.

**Response:**
```json
{
  "status": "success",
  "results": [
    {
      "ticker": "AAPL",
      "company_name": "Apple Inc.",
      "sector": "Technology",
      "piotroski_f_score": 7,
      "altman_z_score": 4.21,
      "beneish_m_score": -2.45,
      "forensic_last_updated": "2026-06-01 07:12:00",
      "flag_piotroski": false,
      "flag_altman": false,
      "flag_beneish": false
    }
  ]
}
```

Flag thresholds: `flag_piotroski` = true when score < 4; `flag_altman` = true when Z < 1.81; `flag_beneish` = true when M > −1.78.

---

### `POST /api/forensic-scores/run-fetch`

Triggers an immediate background run of the **Forensic Quarterly Data Fetch** job, which downloads annual financial statements from Yahoo Finance and caches them to `data/fundamentals/quarterly/`. Incremental — skips tickers with a cache file younger than 30 days.

**Auth:** Required (session cookie + CSRF token).

**Response:** `{"status": "success", "message": "Forensic Quarterly Data Fetch started."}`

---

### `POST /api/forensic-scores/run-score`

Triggers an immediate background run of the **Forensic Accounting Scores** job, which loads cached annual statements, computes all three forensic scores, writes them to `stock_signals`, and fires Nextcloud alerts for any holding breaching distress thresholds.

**Auth:** Required (session cookie + CSRF token).

**Response:** `{"status": "success", "message": "Forensic Accounting Scores started."}`

---

### `GET /api/monte-carlo/accounts`

Returns per-account portfolio values for every active Ghostfolio account (if Ghostfolio is configured) plus every built-in Trading account, using `xray_engine.resolve_scope_holdings()` for the built-in tiles — so this populates correctly with Ghostfolio disabled. Used to populate the account-selector bar on the Monte Carlo page.

**Auth:** Required (session cookie).

**Rate limit:** 10 requests/minute.

**Response (success):**

```json
{
  "status": "success",
  "accounts": [
    {"id": "<uuid>", "name": "ISA", "value": 45321.50},
    {"id": "acct:3", "name": "Trading (Built-in)", "value": 12890.00}
  ],
  "total": 58211.50
}
```

**Response (no Ghostfolio accounts and no built-in Trading accounts with holdings):**

```json
{"status": "error", "message": "No accounts with holdings configured."}
```

---

### `POST /api/monte-carlo/run`

Runs a forward-looking Monte Carlo Wealth Simulation and returns percentile fan data. Computes 1,000 correlated GBM paths using per-asset volatility from `xray_risk_cache`, pairwise correlations from `xray_correlation_matrix` (Cholesky decomposition), and per-asset drift derived from the asset class of each holding. Results are not persisted — computed fresh on every call.

**Auth:** Required (session cookie).

**Request body:**

```json
{
  "portfolio_value": 50000.0,
  "monthly_contribution": 500.0,
  "horizon_years": 20,
  "target_wealth": 250000.0,
  "drift_overrides": {
    "Global Equity ETF": 7.0,
    "UK Equity": 6.5,
    "Bond/Fixed Income": 3.5
  },
  "inflation_pct": 2.5
}
```

| Field | Type | Default | Description |
|---|---|---|---|
| `portfolio_value` | float | required | Current portfolio value (£) |
| `monthly_contribution` | float | 0.0 | Monthly investment added at each year-end step |
| `horizon_years` | int | required | Projection horizon in years (1–50) |
| `target_wealth` | float | 0.0 | Target wealth for probability-of-success calculation (0 = disabled) |
| `drift_overrides` | dict | {} | Per-asset-class annual return assumption (%) — keys must match the three class names above |
| `inflation_pct` | float | 2.5 | Annual inflation rate used to produce the real-wealth series |

**Response:**

```json
{
  "status": "success",
  "percentiles": {
    "p5":  [50000.0, ...],
    "p25": [...],
    "p50": [...],
    "p75": [...],
    "p95": [...]
  },
  "percentiles_real": { "p5": [...], "p25": [...], "p50": [...], "p75": [...], "p95": [...] },
  "probability_of_success": 0.73,
  "median_final": 187000.0,
  "p5_final": 62000.0,
  "horizon_years": 20,
  "n_simulations": 1000
}
```

Each percentile array has length `horizon_years + 1` (index 0 = current year, index N = end of horizon). `percentiles_real` contains the same structure with each value divided by `(1 + inflation_pct/100)^t`. `probability_of_success` is `null` when `target_wealth` is 0.

---

## 19. Accounts

Native, database-backed brokerage accounts + transaction ledger (`/accounts`). Coexists with Ghostfolio — built-in account holdings are merged into the Portfolio page alongside any Ghostfolio-synced accounts (see `accounts_engine.get_combined_holdings`). Backed by the `accounts`, `account_transactions`, `account_value_history`, and `account_price_history` SQLite tables. `House`/`Pension` accounts are tracked standalone via the **Account Price Scraper** (a generic URL + CSS-selector price feed, configured from the account's own tile/detail page rather than the Settings page) — see the dedicated endpoints below.

### `GET /accounts`

HTML page. Renders the account list, create-account form, and the shared Buy/Sell/Dividend/Interest/Fee/Cash transaction modal.

### `GET /accounts/{id}`

HTML page. Renders the account detail view: value-over-time chart with 1M/1Y/YTD/MAX range buttons (client-side Plotly, fed by `GET /api/accounts/{id}/value-history`, sourced from `account_value_history`), summary tiles (cash balance, equity value, realized P&amp;L, dividend/interest income, activity count), live return tiles (1D/1W/1M/3M/6M/1Y return, Unrealized P&amp;L, Money-Weighted Rate of Return — fed by `GET /api/accounts/{id}/live-performance`, auto-refreshing while the page stays open when `UI_PREFERENCES.LIVE_DETAILS` is enabled), Holdings (with current market value, allocation %, and unrealized performance %, live-priced from `market_pulse_cache` when fresh), Closed Positions, the full Activities ledger (inline edit/delete via the shared transaction modal), and Cash Balance History. Used by Trading accounts only now — redirects to `/accounts` for an unknown or soft-deleted account id, to `/accounts/{id}/pension` for a Pension account, and to `/accounts/{id}/house` for a House account (both have their own dedicated pages below). The selected chart range is read server-side from the `acct_chart_period` cookie (default `max`) to pick the initial dataset, then persisted client-side in `static/js/account_detail.js` whenever a range button is clicked, so it carries over to every other Trading account.

### `GET /accounts/{id}/pension`

HTML page. Dedicated Pension account detail view (replaces the generic ledger page above for this account type): unit price chart (`visuals.create_pension_unit_price_chart`, sourced from `account_price_history` — the scraped/imported fund unit value) and value-over-time chart (`visuals.create_pension_value_chart`, sourced from `account_value_history` — plots `total_value` only, with the y-axis auto-ranging to the data, and has no range buttons — unlike the Trading account detail page's client-side chart, which also plots Cash/Net Contributions and supports 1M/1Y/YTD/MAX). A Pension's snapshot always stores `cash_value = 0` (`accounts_engine.snapshot_all_accounts`/`resnapshot_account`/`backfill_value_history`) — Pension has no real cash sub-ledger, so `cash_balance()`'s `initial_cash` baseline must not leak in, or `total_value` stops matching the "Pension Value" tile's `equity_value`. Summary tiles show Pension Value plus Performance % over 1 month / YTD / 1 year, computed from the unit price history via `accounts_engine.pension_performance` (`null`/`—` for any window not yet covered by price history), Pay In / Admin Fee actions, the Account Price Scraper config, and an Activities table (Date, Type, Ticker — using the account's `pension_ticker_label` if set, else the internal `PENSION-{id}` ticker — Qty, Unit Price, Total, Running Total Units, Notes, Delete only — no Edit, since every row is system-generated and the generic Buy/Sell edit modal always submits `update_cash: true`, which would corrupt the cash-free ledger), sorted newest-first. Redirects to `/accounts` for an unknown account id or a non-Pension account.

### `GET /accounts/{id}/house`

HTML page. Dedicated House account detail view (replaces the generic ledger page above for this account type) — deliberately minimal, since a House account has no transactions/holdings concept at all: just the value-over-time chart (`visuals.create_house_value_chart`, sourced directly from `account_price_history`'s raw scraped/imported/purchase price points — the only "all available data points" House has, since there's no per-unit price to derive a separate total from — y-axis auto-ranges the same way the Pension value chart does) and the Account Price Scraper config. Redirects to `/accounts` for an unknown account id or a non-House account.

### `GET /api/accounts`

Returns all non-deleted accounts, each annotated with `scraper_last_status` (`"success"` | `"error"` | `null`) — the most recent run outcome of that account's `account_scraper_{id}_job` from `scheduler_run_log`, or `null` if the scraper is disabled or has never run. Powers the green/red status dot next to the Scraper button on House/Pension tiles. Pension and House accounts are additionally annotated with `current_balance` (`accounts_engine.account_summary()`'s `equity_value` — the live valuation from the latest scraped/imported price, falling back to cost basis if no price exists yet). The Pension tile shows `current_balance` instead of the static `initial_cash`/"Opening Balance". The House tile shows "Initial Purchase" (`initial_cash`), "Current Estimate" (`current_balance`), and "Value gain" (the percentage change of `current_balance` over `initial_cash`, computed client-side in `static/js/accounts.js`) together on one line. Trading accounts are annotated with `holdings_count`, `equity_value`, `cash_balance` (all from `account_summary()`), and `pending_topups` (unresolved rows from `account_autotopup_pending`, oldest first — powers the `[PENDING ACTION]` tile tag). Watchlist accounts are annotated with `watchlist_count` and `watchlist_breakdown` (`accounts_engine.watchlist_summary()` — `{"equity": n, "etf": n, "fund": n, "other": n}`, bucketed from each `watchlist_items.quote_type`), shown on the tile instead of `initial_cash`/currency (irrelevant for a Watchlist).

**Response:**
```json
{
  "status": "success",
  "accounts": [
    {"id": 1, "name": "My ISA", "currency": "GBP", "account_type": "Trading", "initial_cash": 1000.0, "note": null, "pension_ticker_label": null, "scraper_last_status": null, "deleted_at": null, "created_at": "2026-06-25 10:00:00"}
  ]
}
```

---

### `POST /api/accounts`

Creates a new account. Rate limit: 30/minute.

**Request body:**
```json
{ "name": "My ISA", "currency": "GBP", "account_type": "Trading", "initial_cash": 1000.0, "opened_date": "2020-03-15", "pension_start_date": null, "opening_balance_units": null, "pension_ticker_label": null, "note": "optional" }
```

`account_type` is optional and defaults to `"Trading"` — must be one of `Trading`, `House`, `Pension`, `Watchlist` (400 if not), but `"Watchlist"` is additionally rejected (400) since that account is created automatically by the system and can't be created, deleted, or converted to/from manually. Only `Trading` accounts are aggregated into the Portfolio page / X-ray; `House`/`Pension` are tracked standalone via the Account Price Scraper (see below). `opened_date` is optional — when set, it's the real-world account-opening date and is used as the Cash Balance History table's opening row date instead of `created_at` (useful when backfilling a historical account); for House/Pension the create/edit form relabels this field (and `initial_cash`) to fit — "Purchase Date"/"Purchase Value" for House, "Opening Balance Date"/"Opening Balance" for Pension — but they're the same two underlying fields. `pension_start_date` is optional and Pension-only in the UI (accepted for any type, but only shown/used for Pension) — a separate, earlier date recording when the pension itself started accumulating, distinct from `opened_date`/"Opening Balance Date"; currently just stored, with no display built from it yet. `opening_balance_units` is optional and Pension-only in the UI — how many fund units the Opening Balance (`initial_cash`) amount represents. Setting it on a Pension account (create or update) calls `accounts_engine.sync_pension_opening_balance()`, which materialises both fields as a real `Buy` transaction against the account's synthetic ticker, dated `opened_date` (falling back to `created_at`) at an implied price of `initial_cash / opening_balance_units` — so the units show up immediately in Holdings/units-held rather than only after a manual Pay In. Editing either field later updates that same transaction in place (tracked via `opening_balance_txn_id`, never duplicated); clearing `opening_balance_units` removes it. `pension_ticker_label` is optional and Pension-only in the UI — a purely cosmetic display name shown instead of the internal `PENSION-{id}` ticker on the Pension detail page; the underlying ticker stored on every transaction never changes, so `account_scraper_engine.parse_pension_account_id()` and the average-cost ledger are unaffected. Setting `initial_cash`/`opened_date` on a House account (create or update) calls `accounts_engine.sync_house_purchase_price()`, which seeds/updates the earliest row in `account_price_history` with the purchase value at the purchase date (`source: "purchase"`) — keyed by date, so it upserts in place rather than duplicating on every save — giving the House value chart a real starting point instead of starting at the first scrape. Returns `{"status": "success", "message": "...", "id": <new_account_id>}`.

---

### `PUT /api/accounts/{id}`

Updates an existing account. Same body as POST. Returns 404 if not found or soft-deleted. **`account_type` is immutable once an account is created — a PUT whose `account_type` differs from the existing value is rejected with 400** (e.g. House -> Pension would expose a `PENSION-{id}` synthetic ticker the ledger never created, or vice versa silently orphan a real one). Rate limit: 30/minute.

---

### `DELETE /api/accounts/{id}`

Soft-deletes the account (sets `deleted_at`). Transaction history is preserved. Returns 404 if not found. Rate limit: 30/minute. The Delete button lives in a "Danger Zone" section at the bottom of each account's detail page (Trading/House/Pension — `templates/partials/_delete_account_modal.html` + `static/js/account_danger_zone.js`), not on the account tile, and is gated behind a checkbox-confirmation modal rather than a one-click browser `confirm()`.

---

### `GET /api/accounts/{id}/transactions`

Returns all transactions for an account, ordered by `txn_date`. Returns 404 if the account does not exist.

**Response:**
```json
{
  "status": "success",
  "transactions": [
    {
      "id": 1, "account_id": 1, "txn_type": "Buy", "ticker": "AAPL", "company_name": "Apple Inc.",
      "currency": "USD", "txn_date": "2026-01-15", "quantity": 10.0, "unit_price": 150.0,
      "fee": 1.5, "exchange_rate": 0.8, "notes": null, "update_cash": 1, "price_in_pence": 0,
      "ghostfolio_ref": null, "linked_txn_id": null, "created_at": "2026-06-25 10:00:00"
    }
  ]
}
```

---

### `GET /api/accounts/{id}/value-history?period=`

Returns the account's `account_value_history` rows filtered to a chart range — `1m` (30 days), `ytd` (since 1 Jan), `1y` (365 days), or `max` (full history, the default). Powers the 1M/1Y/YTD/MAX range buttons on the Trading account detail page (`accounts_engine.filter_value_history_by_period`). Returns 404 if the account does not exist.

**Response:**
```json
{
  "status": "success",
  "period": "1y",
  "data": [
    {"account_id": 1, "snapshot_date": "2026-01-02", "total_value": 1050.0, "cash_value": 50.0, "equity_value": 1000.0, "net_contributions": 1000.0}
  ]
}
```

---

### `GET /api/accounts/{id}/live-performance`

Returns the account's live performance figures — equity value, cash balance, total value, unrealized P&amp;L, 1D/1W/1M/3M/6M/1Y period gain/loss (in `BASE_CURRENCY`, not a percentage — see below), and since-inception Money-Weighted Rate of Return (%, Modified Dietz method) — powering the live tile rows on the Trading account detail page. Serves the persisted `account_performance_cache` row rather than recomputing on every call; the 5-minute intraday scan (`intraday_orchestrator_job`) refreshes this cache for every Trading account as a side effect of its existing price refresh (`accounts_engine.refresh_performance_cache`), so every browser/tab that polls this endpoint shares one server-side computation instead of each re-deriving it. If no cache row exists yet (e.g. a brand-new account before the next scan cycle), computes and persists one on the fly. Only valid for `Trading`-type accounts — returns 400 for House/Pension/Watchlist. Returns 404 if the account does not exist.

The `return_*` fields are deliberately currency amounts (`end value − start value − net contributions during the period`), not percentages: dividing by the period's starting value produces a wildly misleading number whenever that baseline is small — most commonly when the lookback window is older than the account itself, so it falls back to the earliest available snapshot (near account opening, before real deposits landed, which can be tiny). `mwrr` is the one genuinely rate-based figure in this response and stays a percentage.

**Response:**
```json
{
  "status": "success",
  "account_id": 4,
  "total_value": 11797.83,
  "equity_value": 11788.68,
  "cash_balance": 9.15,
  "unrealized_pnl": 185.19,
  "return_1d": -0.71,
  "return_1w": -1.37,
  "return_1m": -2.57,
  "return_3m": 35.56,
  "return_6m": 149.3,
  "return_1y": 1273.97,
  "mwrr": 40.01,
  "last_updated": 1782928049.32
}
```

---

### `POST /api/accounts/{id}/reconcile-cash`

Books a `Cash` adjustment transaction for the difference between `accounts_engine.cash_balance()` and the `actual_balance` (in `BASE_CURRENCY`) the caller reports, e.g. to true up FX rounding drift against a real broker statement (`accounts_engine.reconcile_cash`). If the difference is less than half a penny, no transaction is created and `delta` is `0.0`. The booked transaction is dated today, denominated directly in `BASE_CURRENCY` with `exchange_rate=1.0` (no FX lookup needed), and flagged `is_adjustment=True` so it can be tagged/filtered in the UI. Returns 404 if the account does not exist; 500 if the transaction fails to write. Rate limit: 30/minute.

**Request:**
```json
{"actual_balance": 1005.32}
```

**Response (adjustment booked):**
```json
{"status": "success", "txn_id": 42, "delta": 5.32, "computed_balance": 1000.0}
```

**Response (already balanced):**
```json
{"status": "success", "delta": 0.0, "computed_balance": 1000.0, "message": "Already balanced — no adjustment needed."}
```

---

### `POST /api/accounts/{id}/transactions`

Adds a transaction to the ledger. `txn_type` must be one of `Buy`, `Sell`, `Fee`, `Dividend`, `Interest`, `Cash` (use `POST /api/accounts/{id}/transfer` for `Transfer` — it is rejected here with 422 since a transfer needs two linked rows across two accounts). If `currency` is omitted, the account's own currency is used. If `exchange_rate` is omitted, it is auto-filled via `accounts_engine.fx_rate_on_date(currency, txn_date)` (historical FX lookup, falling back to the live rate, then `1.0`). `fee` is billed in `fee_currency` — independent of the trade's own `currency` (e.g. a broker's FX spread fee already quoted in base currency on a foreign-currency trade). If `fee_currency` is omitted, it defaults to the trade `currency` (matches the ledger's pre-existing behaviour). If `fee_exchange_rate` is omitted, it is auto-filled the same way as `exchange_rate` when `fee_currency` differs from the trade currency, or reuses the trade's own `exchange_rate` when they match. If `ticker` is provided and not yet present in `asset_profiles`, a background task calls `profile_engine.update_single_profile(ticker)` so it enters the scan pipeline. `isin` is optional, free-text, and purely informational — the instrument's ISIN, which stays stable across a ticker symbol change/delisting, unlike `ticker`; not validated or looked up against any external source. Rate limit: 30/minute.

**Request body:**
```json
{
  "txn_type": "Buy", "txn_date": "2026-01-15", "ticker": "AAPL", "isin": "US0378331005",
  "company_name": "Apple Inc.", "currency": "USD", "quantity": 10, "unit_price": 150.0, "fee": 1.5,
  "exchange_rate": 0.8, "fee_currency": "GBP", "fee_exchange_rate": 1.0,
  "notes": "optional", "update_cash": true, "price_in_pence": false
}
```

Returns `{"status": "success", "message": "...", "id": <new_txn_id>}`. Returns 404 if the account does not exist, 422 if `txn_type` is invalid or is `Transfer`.

---

### `PUT /api/accounts/{id}/transactions/{txn_id}`

Updates a transaction. Same body and FX auto-fill behaviour as POST. Returns 404 if the account or transaction does not exist, or if the transaction belongs to a different account. Returns 422 if the existing transaction (or the requested new type) is `Transfer` — transfers can't be edited in place; delete and re-create instead. Rate limit: 30/minute.

---

### `POST /api/accounts/{id}/transfer`

Records a cash transfer from this account to another of your built-in accounts. Creates two linked rows via `accounts_engine.create_transfer()` — a negative (`Transfer`) leg on this account and a positive leg on `to_account_id` — so both accounts' cash balances reflect it correctly. `fee` (if any) is charged on the source leg only. Rate limit: 30/minute.

**Request body:**
```json
{ "to_account_id": 7, "amount": 250.0, "txn_date": "2026-01-15", "fee": 0, "notes": "optional" }
```

Returns `{"status": "success", "message": "Transfer recorded.", "out_txn_id": ..., "in_txn_id": ...}`. Returns 404 if either account does not exist; 422 if `amount` is not positive or the source and destination accounts are the same.

---

### `DELETE /api/accounts/{id}/transactions/{txn_id}`

Deletes a transaction. If it is one leg of a `Transfer`, the linked sibling leg on the other account is deleted too (`accounts_engine.delete_transaction_with_pair()`) so a transfer is never left half-deleted. Returns 404 if the transaction does not exist or belongs to a different account. Rate limit: 30/minute.

---

### `GET /api/fx-rate?currency=&date=`

Returns the historical exchange rate from `currency` to `BASE_CURRENCY` on `date` (`accounts_engine.fx_rate_on_date`) — used by the Add/Edit Transaction modal to auto-fill the Exchange Rate field whenever the transaction's currency or date changes (e.g. correcting `GBp` to `GBP` updates the suggested rate from `0.01` to `1.0` automatically). Rate limit: 30/minute.

**Response:**
```json
{ "status": "success", "rate": 0.79 }
```

---

### `GET /api/ticker-lookup?q=`

Looks up a ticker on Yahoo Finance for the transaction-entry "ticker/name lookup" UI. Rate limit: 20/minute.

**Response (found):**
```json
{
  "status": "success", "found": true, "ticker": "AAPL",
  "company_name": "Apple Inc.", "currency": "USD", "quote_type": "EQUITY"
}
```

**Response (not found):**
```json
{ "status": "success", "found": false, "ticker": "ZZZNOTREAL" }
```

---

### `POST /api/accounts/value-snapshot/trigger`

Manually queues the Account Value Snapshot job (`accounts_engine.snapshot_all_accounts`) as a background task — writes today's cash/equity/total value row for every built-in account without waiting for the nightly schedule. Used by the "Run Now" button on the Background Automation Schedulers settings panel.

Returns `{"status": "queued", "message": "..."}` immediately.

---

### `POST /api/accounts/{id}/import-csv`

Imports a GIA/broker-style activity export CSV (multipart file upload, field name `file`) into the given built-in account (`accounts_engine.import_csv_activities`). The required column layout, the four recognised `Type` values (`TOP_UP`, `INTEREST_FROM_CASH`, `ORDER`, `DIVIDEND` — `INTERNAL_TRANSFER` is ignored), and how the GBP exchange rate and fees are derived per row are documented in `assets/csv_import_format.md`. Columns are matched by exact header name, independent of order; a missing required column fails the whole import up front with a 422 naming it. Unlike Ghostfolio import, a row whose ticker can't be resolved (checked against the app's own `asset_profiles` cache, then a live Yahoo Finance lookup) is skipped outright rather than imported and flagged. Every skipped row — unresolved ticker, no ticker in the file, unparseable date, unrecognized `Type`, already-imported duplicate, or a DB write failure — is reported back individually under `skipped_rows` with its date, ticker, and a human-readable reason, so the operator can find the exact row in their file rather than just a per-ticker count. If any rows were skipped, the full list is also dispatched via `notification_engine.notify("accounts_csv_import", ...)` (source registered in `NOTIFICATION_SOURCES`, grouped under "Other" in the Settings Notification Settings panel since it has no parent scheduled job) so it's visible in the in-app Notifications panel after the modal is closed. Re-importing the same file is idempotent — each row is fingerprinted (date, type, ticker, amount, quantity, plus an occurrence counter for exact-duplicate rows) and stored in the transaction's `ghostfolio_ref` column prefixed `csv:`, the same dedup slot Ghostfolio import uses, so the two can never collide. On success, schedules the same background tasks as Ghostfolio import: a profile fetch for any newly-resolved ticker not yet in `asset_profiles`, and `accounts_engine.resnapshot_account`. Rate limit: 10/minute.

**Request:** `multipart/form-data` with a `file` field (the CSV).

Returns 404 if the account does not exist; 422 if the CSV is missing a required column or is not valid UTF-8.

**Response:**
```json
{
  "status": "success",
  "message": "Imported 240 rows (3 skipped, 7 ignored). See the Notifications panel for the per-row detail (date, ticker, reason).",
  "imported": 240,
  "skipped": 3,
  "ignored": 7,
  "skipped_rows": [
    { "date": "2021-04-19", "ticker": "ZZZNOPE", "reason": "ticker not found (possibly delisted or mistyped)" }
  ]
}
```

---

### `GET /api/accounts/{id}/export`

Exports the account's entire transaction ledger as a downloadable CSV (`accounts_engine.export_transactions_csv`) whose column layout deliberately mirrors the GIA-style file `POST /api/accounts/{id}/import-csv` accepts (see `assets/csv_import_format.md`), so the export doubles as a practical backup of the ledger — easy to eyeball against a brokerage statement and close to ready for re-import. Returns 404 if the account does not exist. Rate limit: 20/minute.

**Columns:** `Title` (company name, falling back to `Notes` for Cash/Interest rows), `Type` (`Buy`/`Sell` → `ORDER`, `Cash` → `TOP_UP`, `Interest` → `INTEREST_FROM_CASH`, `Dividend` → `DIVIDEND`; `Fee`/`Transfer` have no GIA equivalent and are written as `FEE`/`TRANSFER`, which the importer skips on re-import exactly like `INTERNAL_TRANSFER`), `Timestamp` (`DD/MM/YYYY`, the exact format the importer parses), `Account Currency`, `Total Amount in Account Currency` (`qty × price × fx_rate`), `Buy / Sell` (`BUY`/`SELL`, blank otherwise), `Ticker`, `ISIN`, `Price per Share in Account Currency` (`price × fx_rate`), `Fee` (native currency — also stands in for the import format's separate `Stamp Duty` and `Dividend Withheld Tax Amount` columns, since the ledger doesn't track those separately), `Quantity`, `Instrument Currency`, `Price per Share` (native currency), `Dividend Net Amount` (`Dividend` rows only: `quantity × price − fee`), `FX Rate`, `Position` (`closed` once a ticker has been fully exited — no shares left — blank otherwise, including while still partially held), `Total Amount in Instrument Currency` (`quantity × price`, no FX), `Realized P&L (Account Currency)` (`Sell` rows only, average-cost gain/loss for that sale), `Notes`, `Account Name`, `Transaction ID`.

Re-importing an export as-is will fail the importer's required-column check, since `Stamp Duty`, `FX Fee Amount`, and the four `Dividend *` columns aren't present — the header needs editing first (e.g. renaming `Fee` back to whichever column a row needs). `Fee`/`Transfer`-type rows aren't recognised by the importer at all and would need re-entering via the UI.

**Response:** `text/csv` with a `Content-Disposition: attachment` header (browser downloads it directly).

---

### Account Price Scraper (House / Pension)

A generic URL + CSS-selector price feed, replicating what Ghostfolio's "manual asset" scraper does — fetches a configured URL, extracts a number via a CSS selector (e.g. `#gf-price`), and records it in `account_price_history`. Typically pointed at a small static HTML file the operator's own external cron job writes (e.g. `<div id="gf-price">123.45</div>`); not limited to that — any URL/selector works. Configured per-account from the Accounts page (tile gear icon / detail page), not the Settings page. All six endpoints below 400 if the account is not `House`/`Pension` (the contribution/fee endpoints further require `Pension` specifically).

### `PUT /api/accounts/{id}/scraper-config`

Saves the scraper configuration and (re)registers the account's dynamic scheduled job (`scheduler_jobs.register_account_scraper_job`/`unregister_account_scraper_job`, job id `account_scraper_{id}_job`) accordingly — unregistered first unconditionally, then re-registered only if `scraper_enabled` is true. Rate limit: 30/minute.

**Request body:**
```json
{ "scraper_url": "http://192.168.1.71:8123/house_valuation.html", "scraper_selector": "#gf-price", "scraper_headers": {}, "scrape_time": "02:00", "scraper_enabled": true }
```

Returns `{"status": "success", "message": "Scraper configuration saved."}`. Returns 404 if the account does not exist; 400 if it is not `House`/`Pension`.

---

### `POST /api/accounts/{id}/scraper/test`

Fetches and extracts using the supplied (not-yet-saved) `url`/`selector`/`headers` — does **not** persist anything. Used by the config modal's "Test" button to validate a selector before saving. Rate limit: 20/minute.

**Request body:**
```json
{ "url": "http://192.168.1.71:8123/house_valuation.html", "selector": "#gf-price", "headers": {} }
```

Returns `{"status": "success", "price": 487000.0}`, or 422 with `{"status": "error", "message": "..."}` if the fetch fails or the selector matches nothing/non-numeric text.

---

### `POST /api/accounts/{id}/scraper/run-now`

Runs the account's **saved** scraper config immediately (`account_scraper_engine.run_scrape_for_account`) — fetches, extracts, and writes a real `account_price_history` row dated today (`source="scrape"`), then schedules a background `accounts_engine.resnapshot_account` so the value chart reflects it without waiting for the next nightly run. Used by the "Scrape Now" button — doubles as a config test and an ad-hoc backfill trigger. Rate limit: 20/minute.

Returns `{"status": "success", "price": 487000.0}`, or 422 if the saved config is missing/the fetch fails.

---

### `POST /api/accounts/{id}/price-history/import-csv`

Bulk-imports historical prices from pasted CSV text (semicolon-delimited, header `date;marketPrice` — the same format Ghostfolio's manual-asset historical import uses), via `account_scraper_engine.import_price_csv`. Each row is upserted into `account_price_history` with `source="csv_import"`. Malformed rows (bad date, non-numeric price, too few columns) are silently skipped and counted. Schedules a background `accounts_engine.resnapshot_account` on completion. Rate limit: 10/minute.

**Request body:**
```json
{ "csv_text": "date;marketPrice\n2026-06-27;123.45\n2026-06-28;124.10\n" }
```

**Response:**
```json
{ "status": "success", "message": "Imported 2 price row(s) (0 skipped).", "imported": 2, "skipped": 0 }
```

---

### `GET /api/accounts/{id}/price-history/at-date?date=`

Looks up the resolved unit price for `date` from `account_price_history` (`account_scraper_engine.price_as_of` — most recent row on or before `date`), with no side effects. Used by the Pay In and Admin Fee modals to auto-fill the Unit Price field whenever the date changes, so the operator sees a real number to accept or override rather than a blank "optional override" field. House/Pension-only; 400 otherwise. Rate limit: 60/minute.

**Response:** `{ "status": "success", "price": 1.6 }` (or `"price": null` if no price history exists on/before that date).

---

### `GET /api/accounts/{id}/pension/units-as-of?date=`

Returns the Pension account's synthetic-ticker units held as of `date` (`accounts_engine.pension_units_as_of` — the same ledger lookup `POST .../pension/fee` itself uses internally for `units_before`), with no side effects. Used by the Admin Fee modal to show "Units currently held" and compute the live units-removed/cost preview before saving. Pension-only; 400 otherwise. Rate limit: 60/minute.

**Response:** `{ "status": "success", "units": 1000.0 }`.

---

### `POST /api/accounts/{id}/pension/contribution`

**"Pay In"** — records a Pension contribution. Resolves the unit price for `txn_date` from `account_price_history` (or uses `unit_price` if supplied as an override), computes `units = amount / price`, and creates a `Buy` transaction against the account's synthetic ticker (`PENSION-{id}`, internal-only — never shown in the UI) with `update_cash=False` (the contribution never passes through the account's cash balance — it's invested the same day). Pension-only; 400 otherwise. Rate limit: 30/minute.

**Request body:**
```json
{ "txn_date": "2026-06-27", "amount": 500.0, "unit_price": null }
```

**Response:** `{ "status": "success", "txn_id": 42, "units": 312.5, "unit_price": 1.6 }`. Returns 422 if no price can be resolved for that date and no override was supplied.

---

### `POST /api/accounts/{id}/pension/fee`

**"Admin Fee"** — automates the arithmetic of a unit-based fee deduction. Accepts **exactly one** of two alternative inputs, since pension providers disclose this differently: `units_after` (the units balance read off the portal *after* the fee — units held *before* come from the existing ledger as of `txn_date`, and `units_removed = units_before - units_after`), or `units_removed` directly (if the provider states the deducted unit count itself). Either way, the monetary cost (`units_removed × that date's price`) is computed automatically and recorded as a `Sell` against the synthetic ticker (`update_cash=False`). The trigger stays manual — this only automates the calculation the operator previously did by hand. Pension-only; 400 otherwise. Rate limit: 30/minute.

**Request body (units remaining after fee):**
```json
{ "txn_date": "2026-07-01", "units_after": 995.0, "unit_price": null }
```

**Request body (units deducted, stated directly):**
```json
{ "txn_date": "2026-07-01", "units_removed": 5.0, "unit_price": null }
```

**Response:** `{ "status": "success", "txn_id": 43, "units_removed": 5.0, "unit_price": 1.1, "fee_cost": 5.5 }`. Returns 422 if neither or both of `units_after`/`units_removed` are supplied, if the resulting `units_removed` is not positive or exceeds the units currently held, or if no price can be resolved and no override was supplied.

---

### Auto Top-up (Trading)

Records a recurring direct-debit schedule on a Trading account and, on the scheduled date, creates a *pending* confirmation rather than posting cash automatically — see the Auto Top-up glossary entry for the full rationale. All three endpoints below 400 if the account is not `Trading`.

### `PUT /api/accounts/{id}/autotopup-config`

Saves the Auto Top-up configuration and (re)registers the account's dynamic scheduled job (`scheduler_jobs.register_account_topup_job`/`unregister_account_topup_job`, job id `account_autotopup_{id}_job`, fires at 08:00 in `USER_TIMEZONE`) accordingly — unregistered first unconditionally, then re-registered only if `enabled` is true. When `enabled` is true, `amount` must be greater than 0, `frequency` must be `"monthly"`/`"weekly"`, and the matching day field must be in range (`day_of_month` 1-31, `day_of_week` 1-5 = Mon-Fri). Rate limit: 30/minute.

**Request body:**
```json
{ "enabled": true, "amount": 250.0, "frequency": "monthly", "day_of_month": 26, "day_of_week": null, "notes": "Monthly ISA direct debit" }
```

Returns `{"status": "success", "message": "Auto Top-up configuration saved."}`. Returns 404 if the account does not exist; 400 if it is not `Trading` or the body fails validation.

---

### `POST /api/accounts/{id}/autotopup/confirm`

Posts the deferred top-up as a real `Cash` transaction (`update_cash=True`) for the given (possibly edited) `amount`/`txn_date`, and marks the pending row `confirmed` with the new transaction's id. Used by the "Confirm Payment" button on the account detail page's pending-action banner. Rate limit: 30/minute.

**Request body:**
```json
{ "pending_id": 7, "amount": 252.0, "txn_date": "2026-06-27" }
```

**Response:** `{ "status": "success", "message": "Top-up confirmed.", "txn_id": 101 }`. Returns 400 if the pending row doesn't exist or has already been resolved.

---

### `POST /api/accounts/{id}/autotopup/dismiss`

Marks a pending top-up `dismissed` with no transaction created — used when a direct debit failed or was skipped that period.

**Request body:**
```json
{ "pending_id": 7 }
```

**Response:** `{ "status": "success", "message": "Top-up dismissed." }`. Returns 400 if the pending row doesn't exist or has already been resolved.

---

### `GET /api/accounts/portfolio-totals`

Aggregates live figures across every non-deleted Trading account — the Home Assistant integration's portfolio-summary sensor data source. Thin wrapper around `accounts_engine.portfolio_totals()`. With zero Trading accounts, returns the same shape with all monetary fields at `0.0` and all percentage/return fields `null` rather than erroring.

Before reading, this endpoint also checks whether any held ticker's `market_pulse_cache` price is older than `UI_PREFERENCES.REFRESH_RATE` (while a relevant market is open) and, if so, kicks off a background `market_pulse.fetch_and_save_pulse()` for whichever tickers are due — the same `needs_refresh` pattern `GET /api/market-pulse` already uses for the live-ticking widget, extended to cover every held ticker rather than only ones rendered on screen. This means polling this endpoint (e.g. from Home Assistant) at a given interval makes that interval the real data-refresh cadence, not just a read cadence — see `accounts_engine.tickers_needing_refresh()`.

**Response**

```json
{
  "status": "success",
  "account_count": 2,
  "base_currency": "GBP",
  "as_of": 1751364000.0,
  "current_value": 18420.55,
  "total_investment": 15000.0,
  "portfolio_gain": 3100.10,
  "portfolio_gain_pct": 20.67,
  "portfolio_gain_fx": 3420.55,
  "portfolio_gain_fx_pct": 22.8,
  "unrealized_pnl": 3420.55,
  "unrealized_pnl_pct": 22.8,
  "twr_pct": 19.9,
  "twr_fx_pct": 21.4,
  "portfolio_dividends": 214.30
}
```

`portfolio_gain`/`portfolio_gain_pct` re-express the open-holdings unrealized gain at each holding's own purchase-time exchange rate, isolating the equity-only return from FX movement (FX-neutral). `portfolio_gain_fx`/`portfolio_gain_fx_pct` are the actual (FX-inclusive) unrealized gain at today's live exchange rate — matching `unrealized_pnl`. `twr_pct`/`twr_fx_pct` are the equivalent pairing for the chain-linked Time-Weighted Return derived from `account_value_history` (FX-neutral / actual-with-FX).

---

### `POST /api/accounts/refresh-now`

On-demand data refresh for the Home Assistant integration's "Refresh Data" button. Returns immediately with `{"status": "queued", ...}` and runs the refresh as a background task: refreshes live prices for every currently-held ticker (`market_pulse.fetch_and_save_pulse`) and recomputes the live-performance cache for every Trading account (`accounts_engine.refresh_performance_cache`). Deliberately does **not** call the Crash & Moonshot Alerts scan (`IntradayOrchestrator().run()`) directly — that scan silently no-ops outside configured market hours, which would make the button appear broken most of the day. Completion (or failure) is dispatched via `notification_engine.notify("ha_refresh_now_status", ...)` (source registered in `NOTIFICATION_SOURCES`, no parent scheduled job — grouped under "Other" in the Settings Notification Settings panel).

**Request body:** none

**Response:** `{ "status": "queued", "message": "Refresh queued." }` immediately; check the Notifications panel for completion status.

---

### `GET /api/accounts/list-with-metrics`

Per-Trading-account metrics for the Home Assistant integration's Phase 2 per-account sensors. Thin wrapper around `accounts_engine.account_metrics_list()`, which composes `account_performance_cache` (lazily refreshed the same way `GET /accounts/{account_id}/live-performance` is, if empty) with `account_summary()`'s dividend/interest/realized P&L, which the cache doesn't track. All monetary fields are in `BASE_CURRENCY` (the single top-level `base_currency` key), not the account's own native transaction currency. With zero Trading accounts, returns `"accounts": []`.

Like `portfolio-totals` above, this endpoint also self-triggers a background live-price refresh for any held ticker whose `market_pulse_cache` row is due (see that endpoint's note).

**Response**

```json
{
  "status": "success",
  "base_currency": "GBP",
  "accounts": [
    {
      "account_id": 3,
      "name": "ISA",
      "cash_balance": 512.68,
      "equity_value": 9840.20,
      "unrealized_pnl": 1120.40,
      "realized_pnl": 340.00,
      "dividend_income": 84.30,
      "interest_income": 12.50,
      "gain_1d": 45.10,
      "gain_1w": 120.60,
      "gain_1m": 310.20,
      "gain_3m": 890.15,
      "gain_1y": 2140.00,
      "mwrr_pct": 18.4
    }
  ]
}
```

`gain_1d`/`gain_1w`/`gain_1m`/`gain_3m`/`gain_1y` are `period_returns()`'s currency gain/loss over each window, excluding the effect of deposits/withdrawals during the period. `mwrr_pct` is `money_weighted_return()` — a since-inception Modified Dietz approximation of IRR, `null` if the account has no contribution history yet.

---

### `GET /api/accounts/holdings-list`

Per-holding metrics across every non-deleted Trading account, for the Home Assistant integration's Phase 3 per-holding sensor (one HA device per (account, ticker) pair — the same ticker held in two accounts produces two separate rows, never merged). Thin wrapper around `accounts_engine.holdings_with_metrics_all_accounts()`, which reuses `holdings_with_market_value()` per account and enriches each row with native price (`accounts_engine.current_price_map()`), technicals from `stock_signals` (RSI, 50d/200d trend, next earnings date), 24h change from `market_pulse_cache`, accumulated dividends (summed from `Dividend`-type transactions for that ticker in that account), and any stored price limit from `holding_price_limits`. With zero Trading accounts, returns `"holdings": []`.

Like `portfolio-totals` above, this endpoint also self-triggers a background live-price refresh for any held ticker whose `market_pulse_cache` row is due (see that endpoint's note). `market_price`'s own freshness comes from `current_price_map()` preferring `market_pulse_cache` whenever it's newer than `stock_signals.last_updated` — not gated by an absolute-age cutoff, since the background jobs that keep `market_pulse_cache` warm run far coarser than any UI poll rate, and a price a few minutes old is still far better than the once-nightly `stock_signals` fallback it would otherwise revert to.

**Response**

```json
{
  "status": "success",
  "base_currency": "GBP",
  "holdings": [
    {
      "account_id": 3,
      "account_name": "ISA",
      "ticker": "GOOGL",
      "company_name": "Alphabet Inc.",
      "shares": 2.544544,
      "currency_asset": "USD",
      "currency_base": "GBP",
      "market_price": 354.44,
      "market_price_currency": "USD",
      "market_price_in_base_currency": 265.34,
      "average_buy_price": 282.39,
      "average_buy_price_currency": "GBP",
      "market_value": 675.16,
      "total_investment": 718.56,
      "gain_value": -43.40,
      "gain_value_currency": "GBP",
      "gain_pct": -6.04,
      "profit_and_loss": -43.40,
      "accumulated_dividends": 0.14,
      "accumulated_dividends_currency": "GBP",
      "trend_vs_buy": "down",
      "asset_class": "EQUITY",
      "data_source": "YAHOO",
      "market_change_24h": -6.77,
      "market_change_pct_24h": -1.87,
      "rsi": 46.36,
      "trend_50d": "UP",
      "trend_200d": "UP",
      "next_earnings_date": "2026-09-07",
      "priced_at_cost": false,
      "allocation_pct": 8.51,
      "low_limit": null,
      "low_limit_set": false,
      "low_limit_reached": false,
      "high_limit": null,
      "high_limit_set": false,
      "high_limit_reached": false
    }
  ]
}
```

`gain_value`/`profit_and_loss` are the same figure (`market_value - total_investment`, in `BASE_CURRENCY`) exposed under two keys for parity with the prior Ghostfolio-based integration's attribute naming. `market_price`/`market_price_currency` are the instrument's native (uncoverted) price; `market_price_in_base_currency` and `average_buy_price` are both in `BASE_CURRENCY`. `rsi`/`trend_50d`/`trend_200d`/`next_earnings_date`/`asset_class` (from `stock_signals.quote_type`) may be `null` for a ticker the nightly quant scan hasn't priced yet. `low_limit`/`high_limit` come from `holding_price_limits` (see `POST /api/accounts/holding-price-limit` below); `*_reached` compares the native `market_price` against the stored limit.

---

### `GET /api/accounts/other-accounts-list`

Current value + basic performance for every non-deleted Pension/House account, for the Home Assistant integration's Phase 4 "Other Accounts" sensors (one sensor per account). Thin wrapper around `accounts_engine.other_accounts_list()`. With zero Pension/House accounts, returns `"accounts": []`.

**Response**

```json
{
  "status": "success",
  "base_currency": "GBP",
  "accounts": [
    {
      "account_id": 9,
      "name": "Aviva Pension",
      "account_type": "Pension",
      "currency": "GBP",
      "current_value": 84210.55,
      "performance": {"1m": 1.8, "ytd": 6.4, "1y": 11.2},
      "last_updated": "2026-07-02"
    },
    {
      "account_id": 11,
      "name": "House - Alicia Avenue",
      "account_type": "House",
      "currency": "GBP",
      "current_value": 350000.0,
      "performance": {"1m": null, "ytd": 0.0, "1y": 2.9},
      "last_updated": "2026-06-01"
    }
  ]
}
```

`current_value` is `accounts_engine.account_summary()`'s `equity_value` — deliberately **not** `total_value()`. `total_value()` also adds `cash_balance()`, which for a House account starts from `initial_cash` (a purchase-price memo, not real cash — House has no cash sub-ledger), which would double-count it against the scraped valuation; `GET /accounts` already sources its own House/Pension tile figure the same way. `performance` is `accounts_engine.scraped_price_performance()` (1 month / YTD / 1 year %, derived from `account_price_history`, `null` for a window with no price that far back yet). `last_updated` is the most recent `account_price_history.price_date` for that account, `null` if it has never been scraped/imported.

---

### `POST /api/accounts/holding-price-limit`

Sets one holding's low and/or high price alert limit, called by the Home Assistant integration's per-holding Low Limit / High Limit number entities. Thin wrapper around `accounts_engine.set_holding_price_limit()`.

**Request body**

```json
{
  "account_id": 3,
  "ticker": "GOOGL",
  "low_limit": 150.0,
  "high_limit": 220.0
}
```

`low_limit`/`high_limit` are each optional and independently settable — a request that includes only `low_limit` leaves any previously-stored `high_limit` untouched (partial-update semantics via `db_accounts.upsert_holding_price_limit()`'s dynamic-column upsert; a field omitted from the request body is never written, so it can't silently clear a sibling value already set by an earlier request).

**Response:** `{ "status": "success" }`.

---

*Generated: 2026-06-06 · Quantamental Dashboard*
