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
16. [UK ETF Forecast (SMGB.L)](#16-uk-etf-forecast-smgbl)
17. [AI Sector Contagion Monitor](#17-ai-sector-contagion-monitor)

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
    "FREETRADE_ONLY_MODE": false
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
  }
}
```

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

## 14. System & Infrastructure

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

### `POST /api/system/restart`

Sends a `SIGTERM` to the running process after a 2-second delay, triggering a graceful shutdown. The process manager (e.g. systemd or Docker) is expected to restart it automatically.

**Request body:** none

**Response**

```json
{
  "status": "success",
  "message": "Restart signal sent. The dashboard will be back online in ~5-10 seconds."
}
```

---

## 15. Alert Testing

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
| `GET` | `/api/ai-prompt/{ticker}` | AI-consumable analysis prompt |
| `POST` | `/api/settings` | Save configuration |
| `POST` | `/api/settings/test-yahoo-ipv6` | Test IPv6 connection |
| `GET` | `/api/settings/network-status` | Current routing health |
| `GET` | `/api/system/metrics` | System diagnostic data |
| `POST` | `/api/system/git-pull` | Pull latest code from git |
| `POST` | `/api/system/restart` | Graceful application restart |
| `POST` | `/api/test-sentiment-alert` | Test Nextcloud sentiment alert |
| `POST` | `/api/test-earnings-alert` | Test Nextcloud earnings alert |
| `POST` | `/api/test-insider-alert` | Test Nextcloud insider alert |
| `GET` | `/api/news-feed` | Paginated news articles from local store |
| `POST` | `/api/news-feed/run-now` | Trigger immediate news feed refresh |

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

## 16. UK ETF Forecast (SMGB.L)

### `GET /api/smgb-prediction`

Returns the current SMGB.L next-morning open prediction. Rate-limited to 10 requests/minute.

**Response fields**

| Field | Type | Description |
|-------|------|-------------|
| `status` | string | `"success"` or `"error"` |
| `predicted_price` | float | Predicted SMGB.L open in GBP (£) |
| `last_smgb_close` | float | Last known SMGB.L close in GBP (£) |
| `predicted_change_pct` | float | Predicted change from last close (%) |
| `data_source` | string | `"holdings"` · `"known_weights_fallback"` · `"regression_only"` |
| `signal_source` | string | `"intraday_post_close"` · `"intraday_premarket"` · `"daily_close"` |
| `fx_rate_gbpusd` | float | GBP/USD spot rate used for FX adjustment |
| `next_open_date` | string | ISO date of predicted open (`"YYYY-MM-DD"`) |
| `as_of_utc` | string | Data timestamp |
| `n_holdings_used` | int | Number of holdings used by the holdings model |
| `holdings_engine` | object\|null | Holdings model detail: `predicted_price`, `contributions[]`, `fx_adjustment_pct` |
| `regression_engine` | object\|null | Regression model detail: `predicted_price`, `lower_bound`, `upper_bound`, `alpha`, `beta`, `r_squared`, `n_observations` |
| `error` | string\|null | Error message if `status == "error"` |

`signal_source` indicates which price data drove the prediction:
- `intraday_post_close` — US prices after LSE close (16:30 BST), return measured vs price at UK-close time
- `intraday_premarket` — US pre-market prices (04:00–09:30 ET)
- `daily_close` — prior day daily closes (fallback)

For methodology details see [`assets/smgb_predictor.md`](smgb_predictor.md).

---

### `GET /uk-etf-forecast`

HTML page. Renders the full SMGB.L Morning Price Predictor UI including four charts and the portfolio impact tile.

---

## 17. AI Sector Contagion Monitor

### `GET /ai-contagion`

HTML page. Renders the AI Sector Contagion Monitor with 30-day normalised performance, intraday performance (when market is open), and a 20-day pairwise correlation heatmap for: NVDA, AMD, AVGO, GOOGL, MSFT, META, AAPL, ORCL, AMZN, TSLA.

For methodology details see [`assets/ai_contagion_monitor.md`](ai_contagion_monitor.md).

---

## 18. News Feed

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

*Generated: 2026-06-06 · Quantamental Dashboard*
