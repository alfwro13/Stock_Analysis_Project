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

Adds a ticker to the Ghostfolio watchlist and triggers a watchlist sync.

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
  "message": "Failed to add to Ghostfolio."
}
```

---

### `POST /api/watchlist/remove`

Removes a ticker from the Ghostfolio watchlist.

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
| `mode` | string | `UK Market Health Check` | `UK Market Health Check`, `This Week's UK Risk Events`, `Pound & Gilt Impact`, `UK vs US Comparison` |

**Response (success)**

```json
{ "status": "success", "prompt": "You are a Senior UK market strategist..." }
```

---

## 13. Settings & Configuration

### `POST /api/settings`

Saves application settings. Only fields present in the request body are updated — absent fields are left unchanged (deep merge, not a full replacement).

After saving, the scheduler is reloaded to apply any changed schedule configurations.

**Request body** — all fields optional, send only what you want to change

```json
{
  "SERVER_URL": "http://192.168.1.100",
  "PORT": 8090,
  "BASE_CURRENCY": "GBP",
  "GHOSTFOLIO_URL": "http://ghostfolio:3333",
  "API_TOKEN": "your-ghostfolio-token",
  "FRED_API_KEY": "your-fred-api-key",
  "YAHOO_IPV6_ADDRESS": "2a00:1450:400f:804::200e",
  "NEXTCLOUD_URL": "https://nextcloud.example.com/...",
  "BOT_USERNAME": "alertbot",
  "APP_PASSWORD": "app-password",
  "CONVERSATION_TOKEN": "chat-token",
  "IGNORED_TICKERS": ["GMESTOP"],
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

Returns the currently active Yahoo Finance routing mode and its health status.

**Response (IPv4 default)**

```json
{
  "status": "success",
  "route": "IPv4 (OS Default)",
  "indicator": "green",
  "message": "Using standard IPv4 routing. No custom IPv6 address is configured."
}
```

**Response (IPv6 active)**

```json
{
  "status": "success",
  "route": "IPv6 (Active)",
  "indicator": "green",
  "message": "Successfully routing Yahoo Finance edge traffic exclusively through 2a00:..."
}
```

**Response (IPv6 failover)**

```json
{
  "status": "warning",
  "route": "IPv4 (Failover Rescue Active)",
  "indicator": "yellow",
  "message": "IPv6 routing failed at 2026-05-29 12:00:00. Traffic is actively being rescued via IPv4 fallback. Last Error: ..."
}
```

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

### `POST /api/system/git-pull`

Pulls the latest code from the Git remote. Returns the git output.

**Request body:** none

**Response (success)**

```json
{
  "status": "success",
  "message": "Update successful. Please restart the service if required.\n\nAlready up to date."
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
  }
}
```

`active_jobs` is an empty object `{}` when the server is idle. Timestamps are UTC ISO-8601 strings representing when each job started. The Settings page polls this endpoint every 30 seconds to display a live status indicator.

---

### `POST /api/system/restart`

Sends a `SIGTERM` to the running process after a 2-second delay, triggering a graceful shutdown. The process manager (e.g. systemd or Docker) is expected to restart it automatically.

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
| `POST` | `/api/watchlist/add` | Add ticker to watchlist |
| `POST` | `/api/watchlist/remove` | Remove ticker from watchlist |
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
| `GET` | `/api/settings/network-status` | Current routing health |
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
| `account_id` | string | `"all"` | Ghostfolio account ID or `"all"` for the combined portfolio |
| `scenario_id` | string | required | One of `gfc_2008`, `dotcom_2000`, `covid_2020`, `inflation_2022`, `custom` |
| `custom_drop` | float \| null | `null` | Required when `scenario_id == "custom"`. Decimal e.g. `-0.30` for a 30% crash |

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

*Generated: 2026-06-06 · Quantamental Dashboard*
