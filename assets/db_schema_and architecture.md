# 🗄️ Database Schema & Data Architecture

The Quantamental Dashboard employs a **Dual-Storage Architecture** to guarantee high performance, bypass memory bottlenecks, and prevent API rate-limit bans. It also features a dedicated **AI & Machine Learning Layer** that acts as a cyclic enricher and LLM prompt generator.

Structured metadata, fundamental metrics, and system scoring are routed to a relational **SQLite database**, while massive time-series matrices are routed to highly compressed **Parquet files** and flat JSONs.

---

## 1. The Relational Database (`data/analysis.db`)

The SQLite database acts as the central brain of the dashboard. It uses a star-like schema where most tables join on the primary key `ticker`, optimized for heavy concurrent access via WAL (Write-Ahead Logging).

### Core Tables & Entities

#### `market_universe`
* **Purpose:** The master tracker of all ~4,000+ available equities, ETFs, and Mutual Funds.
* **Key Columns:** `ticker` (PK), `company_name`, `sector`, `industry`, `country`, `exchange`, `is_freetrade`, `freetrade_url`, `last_updated`.

#### `asset_profiles`
* **Purpose:** Static, slow-moving corporate metadata. Stored separately (3NF normalization) to prevent querying Yahoo Finance for static data.
* **Key Columns:** `ticker` (PK), `company_name`, `sector`, `quote_type`, `business_summary`, `last_verified_date`.

#### `stock_signals`
* **Purpose:** The primary aggregation table for the Portfolio & Watchlist UI. It houses the final System Verdicts and Fundamental Health snapshots.
* **Key Columns:** `ticker` (PK), `current_price`, `trend_50d`, `trend_200d`, `atr_stop_loss`, `trailing_pe`, `debt_to_equity`, `peter_lynch_peg`, `yield_correlation`, `composite_score`, `overall_signal`, `setup_tags`.

#### `quant_signals`
* **Purpose:** Stores a daily historical log of technical and quantitative metrics for machine learning and mean-reversion analysis.
* **Key Columns:** `ticker`, `date` (Composite PK), `close_price`, `rsi_14`, `macd_hist`, `sma_50`, `sma_200`, `volume_surge`, `bullish_cross`, `ml_confidence_score`, `sentiment_score`, `var_95`, `cvar_95`.
* **AI Interaction:** The `ai_prediction_engine` reads from this table, calculates ML probabilities, and writes the `ml_confidence_score` back into it daily.

#### `quant_scan_states`
* **Purpose:** Composite-key state tracker for resumability in long-running jobs to prevent data gaps upon unexpected interruptions.
* **Key Columns:** `scan_date`, `scan_type` (Composite PK), `last_processed_ticker`, `status`.

#### `earnings_volatility`
* **Purpose:** The options arbitrage ledger.
* **Key Columns:** `ticker` (PK), `next_earnings_date`, `implied_move_pct`, `historical_avg_move_pct`, `edge_score`.

### System, Macro & AI Models Tables

#### `market_regimes`
* **Purpose:** Tracks broad market volatility regimes and surface states for the AI Hidden Markov Model (HMM).
* **Key Columns:** `date` (PK), `vix_close`, `spy_volatility`, `us_turbulence`, `us_regime_label`, `uk_turbulence`, `uk_regime_label`, `ai_hmm_state`.

#### `macro_regimes`
* **Purpose:** Dual-region tracker for systemic threat levels derived from sovereign bond yields and exchange rates.
* **Key Columns:** `date` (PK), `tyx_close`, `tnx_close`, `dxy_close`, `uk_gilt_close`, `us_yield_velocity`, `us_threat_level`, `uk_threat_level`.

#### `macro_calendar`
* **Purpose:** Tracks Tier-1 economic events, their ground-truth effects on the SPY, and AI-predicted volatility warnings.
* **Key Columns:** `event_id` (PK), `event_date`, `event_name`, `forecast_val`, `previous_val`, `actual_val`, `post_event_spy_gap`, `ai_volatility_warning`, `ai_consensus_miss_prob`.

#### `macro_indicators`
* **Purpose:** A structural economic datastore integrating FRED, BoE, and ONS metrics (M2 Supply, Jobless Claims, Yield Curve).
* **Key Columns:** `date` (PK), `us_m2`, `us_jobless_claims`, `us_high_yield_spread`, `us_yield_curve`, `uk_m4`, `uk_corporate_spread`.

---

## 2. Local File Storage (`data/` Directory)

We offload heavy time-series math and unstructured payload caching to local file storage.

* 📁 **`data/historical/` (`.parquet`)**: 2 years of daily OHLCV data. Used by `ta` to calculate moving averages and by `plotly` to render interactive charts.
* 📁 **`data/intraday/` (`.parquet`)**: 1 day of 5-minute interval OHLCV data. Used by the `intraday_orchestrator` to detect flash crashes.
* 📁 **`data/fundamentals/` (`.json`)**: Raw, unadulterated `.info` dictionary dump directly from Yahoo Finance.
* 📄 **`data/portfolio.json`**: Contains positions, shares, and VWAP synced from Ghostfolio (both Macro and Micro ledgers).
* 📄 **`data/watchlist.json`**: Active watchlist tickers.
* 📄 **`data/freetrade_blacklist.json`**: Self-healing ledger of permanently banned tickers.
* 📄 **`data/isin_ticker_cache.json`**: Mapping of European ISINs to Yahoo Finance symbols.

---

## 3. The AI & Machine Learning Architecture

The dashboard features a tri-level AI architecture that interacts heavily with both the SQLite and Local File Storage layers.

### A. The LLM Prompt Aggregator (`AIPromptEngine`)
Located in `ai_engine.py`, this engine acts as a massive data bridge. It extracts data across the Dual-Storage architecture to generate high-context, institutional-grade prompts for external LLMs.
1. **SQLite Extraction:** Joins `stock_signals` and `quant_signals` to pull the latest system verdicts, ML Confidence Scores, VaR/CVaR, and NLP Sentiment.
2. **JSON Extraction:** Pulls live global VWAP, active shares, and unrealized P&L from `portfolio.json`.
3. **Parquet Extraction:** Reads raw `.parquet` historical files on-the-fly to calculate real-time technicals (MACD lines, OBV trends, volume averages).
4. **Prompt Wrapping:** Compiles this cross-storage data into specific personas (e.g., "The Devil's Advocate", "Risk/Reward Audit", "Earnings Strategy").

### B. Micro-Equities Prediction (`AIPredictionEngine`)
An XGBoost + Random Forest soft-voting ensemble that evaluates individual stock momentum.
* **Flow:** Reads 24 features from historical Parquet files and fundamental tables -> Applies cross-sectional z-scoring -> Predicts probability of >3% return in 10 days -> Upserts the `ml_confidence_score` into `quant_signals`.

### C. Macroeconomic Event Prediction (`MacroAIEngine`)
A stacked model architecture that evaluates systemic market risk.
* **Flow:** * **HMM (Hidden Markov Model):** Reads structural `macro_indicators` to predict the hidden regime state (`ai_hmm_state` in `market_regimes`).
  * **Random Forest:** Reads `macro_calendar` forecasts to predict the probability of Wall Street being wrong (`ai_consensus_miss_prob`).
  * **XGBoost (Stacking):** Consumes the HMM state and RF probability to predict the exact percentage shock an upcoming event will have on the S&P 500 (`ai_volatility_warning`).