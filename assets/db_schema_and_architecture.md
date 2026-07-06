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

#### `company_name_overrides`
* **Purpose:** Stores user-defined display name overrides per ticker. When present, the override takes precedence over `asset_profiles.company_name` and `market_universe.company_name` in the portfolio, watchlist, and stock detail pages.
* **Key Columns:** `ticker` (PK), `display_name` (user-supplied name, NOT NULL), `updated_at` (UTC timestamp).
* **Write path:** `POST /api/ticker/{ticker}/name-override`. Sending an empty `display_name` deletes the row (clears override). Read-path is a `LEFT JOIN` in the portfolio, watchlist, and stock detail SQL queries.

#### `stock_signals`
* **Purpose:** The primary aggregation table for the Portfolio & Watchlist UI. It houses the final System Verdicts and Fundamental Health snapshots.
* **Key Columns:** `ticker` (PK), `current_price`, `trend_50d`, `trend_200d`, `atr_stop_loss`, `trailing_pe`, `debt_to_equity`, `peter_lynch_peg`, `yield_correlation`, `composite_score`, `overall_signal`, `setup_tags`.
* **Forensic Columns (added June 2026):** `piotroski_f_score` (REAL, 0–9 integer stored as real), `altman_z_score` (REAL), `beneish_m_score` (REAL), `forensic_last_updated` (TEXT UTC). Written monthly by the Forensic Accounting Scores job. NULL until first run.

#### `quant_signals`
* **Purpose:** Stores a daily historical log of technical and quantitative metrics for machine learning and mean-reversion analysis.
* **Key Columns:** `ticker`, `date` (Composite PK), `close_price`, `rsi_14`, `macd_hist`, `sma_50`, `sma_200`, `volume_surge`, `bullish_cross`, `ml_confidence_score`, `sentiment_score`, `var_95`, `cvar_95`.
* **Entry & Exit Zone Columns (added June 2026):** `vp_poc` (Volume Profile Point of Control price), `vp_val` (Value Area Low), `vp_vah` (Value Area High), `vp_entry_zone` (nearest HVN or VAL below current price — buy-entry support level), `vp_exit_zone` (nearest overhead HVN or VAH — take-profit resistance level), `kc_z_score` (Keltner Channel Z-score: (Close − EMA21) / ATR14 — negative = below EMA), `kc_entry_signal` (1 if Z-score ∈ (−3, −2) AND 200-day uptrend), `kc_exit_signal` (1 if Z-score > +3 AND RSI > 75).
* **Quantile Price Band Columns (added June 2026):** `price_q10` (10th-percentile 10-day price floor from XGBoost quantile regression), `price_q90` (90th-percentile 10-day price ceiling — optimistic take-profit target). Written by `score_quantile_predictions()` in `ai_prediction_engine.py` during the daily ML inference job.
* **AI Interaction:** The `ai_prediction_engine` reads from this table, calculates ML probabilities, and writes the `ml_confidence_score`, `price_q10`, and `price_q90` back into it daily.

#### `quant_scan_states`
* **Purpose:** Composite-key state tracker for resumability in long-running jobs to prevent data gaps upon unexpected interruptions. On startup, `resume_interrupted_scans()` queries this table and re-fires any IN_PROGRESS scan automatically.
* **Key Columns:** `scan_date`, `scan_type` (Composite PK), `last_processed_ticker`, `status`.
* **`scan_type` values:**
  - `'daily'` — overnight portfolio+watchlist quant scan (`quant_engine.py`)
  - `'universe'` — weekend full-universe quant scan (`quant_engine.py`)
  - `'universe_deep_sync'` — Stage 3 technicals within the deep sync pipeline (`quant_engine.py`)
  - `'ml_backfill'` — ML historical feature backfill per ticker (`ai_prediction_engine.py`); date-agnostic lookup so cross-day restarts still resume
  - `'deep_sync_s1'` … `'deep_sync_s5'` — per-stage checkpoints for the Universe Deep Sync pipeline (`universe_deep_sync_engine.py`); Stage 3 uses `'universe_deep_sync'` above

#### `earnings_volatility`
* **Purpose:** The options arbitrage ledger.
* **Key Columns:** `ticker` (PK), `next_earnings_date`, `implied_move_pct`, `historical_avg_move_pct`, `edge_score`.

### System, Macro & AI Models Tables

#### `market_regimes`
* **Purpose:** Tracks broad market volatility regimes, the HMM surface state, and the market-wide Isolation Forest stress score.
* **Key Columns:** `date` (PK), `vix_close`, `spy_volatility`, `us_turbulence`, `us_regime_label`, `uk_turbulence`, `uk_regime_label`, `ai_hmm_state`, `price_hmm_state` (0=Bull/1=Chop/2=Crash), `price_hmm_label` (text label), `price_hmm_prob` (posterior probability of current state), `market_stress_score` (REAL [0,1] — 1.0 = maximally anomalous), `market_stress_features` (TEXT — JSON blob of the 6 raw feature values: `vix_level`, `vix_ma_ratio`, `hyg_return`, `tnx_change`, `spy_vol_zscore`, `spy_return`).

#### `price_hmm_states`
* **Purpose:** Full Viterbi-decoded state history from the price-action HMM — one row per trading day. Used by the `/market-regime` page and the `/api/market-regime` endpoint.
* **Key Columns:** `date` (PK), `state` (INTEGER 0/1/2), `label` (TEXT: Bull/Chop/Crash), `probability` (REAL posterior confidence).

#### `macro_regimes`
* **Purpose:** Dual-region tracker for systemic threat levels derived from sovereign bond yields and exchange rates. Also stores the synthesised macro regime label computed by `regime_engine.classify_macro_regime()`.
* **Key Columns:** `date` (PK), `tyx_close`, `tnx_close`, `dxy_close`, `uk_gilt_close`, `us_yield_velocity`, `us_threat_level`, `uk_threat_level`, `yield_curve_inverted` (0/1), `days_inverted` (consecutive day streak), `regime_label` (one of: Risk-On, Late Cycle, Stagflation, Contraction, Recovery).

#### `macro_calendar`
* **Purpose:** Tracks Tier-1 economic events, their ground-truth effects on the SPY, and AI-predicted volatility warnings.
* **Key Columns:** `event_id` (PK), `event_date`, `event_name`, `forecast_val`, `previous_val`, `actual_val`, `post_event_spy_gap`, `ai_volatility_warning`, `ai_consensus_miss_prob`.

#### `macro_indicators`
* **Purpose:** A structural economic datastore integrating FRED, BoE, and ONS metrics (M2 Supply, Jobless Claims, Yield Curve, CPI, Fed Funds Rate, real yield).
* **Key Columns:** `date` (PK), `us_m2`, `us_jobless_claims`, `us_high_yield_spread`, `us_yield_curve`, `uk_m4`, `uk_corporate_spread`, `us_cpi_inflation`, `us_fed_funds_rate` (FEDFUNDS), `us_real_yield_10y` (DFII10 TIPS), `uk_base_rate` (IUDBEDR).

#### `treasury_auction_results`
* **Purpose:** US Treasury auction demand metrics fetched from the free fiscaldata.treasury.gov API. One row per CUSIP × auction date. Powers the Sovereign Debt Auction Monitor, which fires an alert when bid-to-cover or yield tail is significantly below/above the 6-auction rolling baseline for that maturity.
* **Key Columns:** `cusip` + `auction_date` (composite PK), `maturity_label` (e.g. "10Y", "30Y"), `high_yield`, `bid_to_cover`, `tail_bp` (high_yield − median_yield in basis points), `direct_pct`, `indirect_pct`, `dealer_pct`, `offering_amt` (raw API value), `alert_fired` (0/1 dedup flag). Engine: `treasury_auction_engine.py`.

---

## 1b. Indexes

| Index | Table | Columns | Notes |
|---|---|---|---|
| `idx_macro_event_date` | `macro_calendar` | `event_date` | Range-filtered by 6+ call sites; added June 2026. |
| `idx_treasury_auction_date` | `treasury_auction_results` | `auction_date` | Baseline query filters by auction_date; added June 2026. |
| *(PK)* | `quant_signals` | `ticker, date` | Composite PK; covers all ticker+date lookups. The redundant `idx_qs_ticker_date` explicit index was dropped June 2026. |

---

## 2. Local File Storage (`data/` Directory)

We offload heavy time-series math and unstructured payload caching to local file storage.

* 📁 **`data/historical/` (`.parquet`)**: 2 years of daily OHLCV data. Used by `ta` to calculate moving averages and by `plotly` to render interactive charts.
* 📁 **`data/intraday/` (`.parquet`)**: 1 day of 5-minute interval OHLCV data. Used by the `intraday_orchestrator` to detect flash crashes.
* 📁 **`data/fundamentals/` (`.json`)**: Raw, unadulterated `.info` dictionary dump directly from Yahoo Finance.
* 📁 **`data/fundamentals/quarterly/` (`.json`)**: Annual financial statement cache used by the Forensic Screener. One JSON file per ticker (`{TICKER}.json`) containing `balance_sheet`, `financials`, and `cashflow` DataFrames serialised to dict. Incremental — files younger than 30 days are skipped on re-fetch. Exempt from maintenance pruning (the maintenance engine only processes files directly in `data/fundamentals/`, not subdirectories).
* 📄 **`data/portfolio.json`**: Contains positions, shares, and VWAP synced from Ghostfolio (both Macro and Micro ledgers). Only exists while `GHOSTFOLIO_ENABLED` is `true` — unchecking **Enable Ghostfolio Integration** in Settings deletes it immediately, and the nightly maintenance job / System Configuration Check re-delete it if it reappears (e.g. restored from a backup) while disabled. `accounts_engine._read_portfolio_json()` also short-circuits to `{}` when the flag is off, regardless of whether the file exists.
* 📄 **`data/watchlist.json`**: Legacy active-watchlist file, superseded by the `watchlist_items` table (see §1) — only read once at startup for the one-time migration. Same disabled-Ghostfolio deletion lifecycle as `portfolio.json` above.
* 📄 **`data/freetrade_blacklist.json`**: Self-healing ledger of permanently banned tickers.
* 📄 **`data/isin_ticker_cache.json`**: Mapping of European ISINs to Yahoo Finance symbols.
* 📄 **`data/exchange_hours.json`**: Per-exchange open/close times, timezones, currencies, and ticker suffixes. Auto-seeded with defaults for 28 exchanges on first boot. Edit this file to customise or add exchange definitions; read at runtime by `time_engine.py` and `etf_predictor_engine.py`.

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

---

## 4. Additional SQLite Tables

Tables added after initial schema creation. All managed via `db_schema.py:init_db()` and `db_schema.py:migrate_db()` (re-exported from `database.py`).

#### `ticker_metadata`
* **Purpose:** Lightweight beta + market-cap cache used by ML feature assembly. Created by `ai_prediction_engine.sync_ticker_metadata()` rather than `init_db()` (see code note).
* **Key Columns:** `ticker` (PK), `sector`, `beta`, `market_cap`.

#### `market_pulse_cache`
* **Purpose:** Shared, timestamped live-price cache — the single place any engine that derives a fresh ticker price from a Yahoo Finance fetch shares it, so other engines/pages reuse it instead of re-fetching. Originally just the index/asset snapshot behind `/market-sentiment` JS polling; now also written by the Crash & Moonshot scan (`intraday_orchestrator.py`) and Dip Radar (`intraday_bottom_engine.py`) from OHLCV data they already pull for their own pattern detection. Read by `market_pulse.get_cached_pulse_from_db()` (JS polling, with a `needs_refresh` staleness check that skips a fetch once another engine has updated a ticker) and, for the ticking price widget on the Portfolio and Stock Detail pages, `market_pulse.get_all_cached_pulse()`. The Portfolio page's per-position value/P&L math (`page_routes.portfolio_page`), the Stock Detail page's "Your Position" math (`page_routes.stock_detail`), and Built-in Accounts (`accounts_engine.current_price_map()` — Accounts holdings table, `portfolio-totals`/`list-with-metrics`/`holdings-list`) all resolve their current price through the single canonical `accounts_engine.current_price_map()` instead of each reading `market_pulse_cache`/`stock_signals` independently, so a position's value and P&L agree across all three pages for the same request (the latter three list/metrics endpoints also self-trigger a `fetch_and_save_pulse()` background task for any held ticker whose row is older than `UI_PREFERENCES.REFRESH_RATE`, via `accounts_engine.tickers_needing_refresh()`, so polling those endpoints — e.g. from Home Assistant — is itself what keeps the cache warm, not just a passive read). `current_price_map()` prefers this cache's price over `stock_signals.current_price` whenever the cache row is newer than `stock_signals.last_updated`, rather than an absolute-age cutoff — `market_pulse.is_price_fresh()`'s age-based check (a 5-minute floor, or 2x `REFRESH_RATE` if larger) is display-only, feeding the "grey out as stale" flag on the live-ticking widget, not data selection.
* **Key Columns:** `ticker` (PK), `name`, `price`, `change_pts`, `change_pct`, `is_positive`, `last_updated`, `market_state`. `market_state` is only populated for the two exchange-status proxy tickers (`^GSPC` for NYSE, `^FTSE` for LSE) with the live Yahoo `marketState` value (`"REGULAR"`, `"CLOSED"`, `"PRE"`, `"POST"`, ...); `market_pulse.is_exchange_open(exchange)` reads it to give `GET /api/system/market-status` and `accounts_engine.tickers_needing_refresh()` an exchange-holiday-aware open/closed check, falling back to `time_engine.is_trading_session()`'s weekday/hours heuristic when no live state is cached yet. `GET /api/system/market-status` self-triggers a `fetch_and_save_pulse()` background task for either proxy ticker whose `market_state` is missing or older than 5 minutes (`market_pulse.proxy_tickers_needing_refresh()`), so a caller that only ever polls that one endpoint (e.g. Home Assistant, never opening the browser dashboard that drives `/market-sentiment`'s own JS polling) still keeps `market_state` warm instead of it going stale forever.
* **Written by:** `market_pulse.upsert_live_price()` (shared writer, name preserved via `COALESCE` if already on record) or `market_pulse.fetch_and_save_pulse()`'s own insert (index/mutual-fund/gilt fallback paths). The latter also fires a once-per-day `stale_price_alert` notification (via `alert_state` dedup, engine `stale_price`) when a held ticker's fetch has been failing for 30+ minutes during its own market hours.
* **Retention:** the weekly **Database & File Maintenance** job (`maintenance_engine.prune_pulse_cache()`) deletes a row only if it is **both** older than 24h **and** orphaned — not currently tracked by any writer (`maintenance_engine._get_pulse_active_tickers()`: portfolio holdings, watchlist, `market_pulse.INDEX_TICKERS`, `"UK10YG"`, `ai_contagion_engine.AI_ECOSYSTEM_TICKERS`, active Dip Radar monitors). A currently-held ticker's row therefore survives indefinitely across a weekend/holiday market closure instead of being wiped by the weekly prune before the next session can refresh it (fixed 2026-07-05 — the prior blind 24h-age prune reliably wiped every held ticker's row every weekend). Separately, `accounts_engine.tickers_needing_refresh()`'s market-hours gate (only auto-refresh while a market is open, to avoid pointless Yahoo calls when nothing can have changed) has a bootstrap exception: a ticker with **no** cached row at all is still refreshed even while both markets are closed, since a genuinely missing row — a fresh install, a newly-bought ticker, or a gap predating this fix — could otherwise never self-heal until a market reopened.

#### `alert_state`
* **Purpose:** Dedup ledger for intraday alert engines. Decoupled from `system_notifications` so display and dedup logic never interfere.
* **Key Columns:** `engine`, `ticker` (composite PK), `fingerprint`, `last_price`, `last_fired_utc`, `armed`, `fire_count`, `state_date`.

#### `system_notifications`
* **Purpose:** In-app notification feed surfaced in the Settings notifications panel.
* **Key Columns:** `id` (PK autoincrement), `timestamp`, `message_type`, `message_text`, `is_read`, `status`.
* **Written by:** all notifications now funnel through `notification_engine.notify()`, whose in-app channel writes this table (directly, or via `database.log_notification()`). Whether a given source reaches this table is controlled by `NOTIFICATION_ROUTING`. Deep pipeline-progress messages still call `database.log_notification()` directly (in-app only).

#### `scheduler_run_log`
* **Purpose:** Records the last-run timestamp per APScheduler job ID so jobs can guard against re-running within their minimum interval. Also stores per-job timing/outcome used by the Workflow Monitor.
* **Key Columns:** `job_id` (PK), `last_run`, `last_started`, `last_duration_sec`, `avg_duration_sec` (EMA of run duration), `last_status` (`success`/`error`).
* **Written by:** each job's `record_job_run()` (`last_run`) plus the APScheduler event listener `_on_job_event` (start, duration, status) registered in `start_scheduler()`.

#### `score_history`
* **Purpose:** Per-ticker daily score + signal + close price history. Accumulates over time to enable forward-returns analysis.
* **Key Columns:** `ticker`, `date` (composite PK), `score`, `signal`, `close_price`.

#### `xray_risk_cache`
* **Purpose:** Per-ticker beta and annualised volatility vs the configured benchmark, pre-computed by the scheduler job. Universe = every ticker in `portfolio.json` (Ghostfolio) **and** every ticker held by a built-in Trading account, so X-ray works whichever scope (`all`, a Ghostfolio account, or `acct:{id}`) is requested.
* **Key Columns:** `ticker`, `benchmark` (composite PK), `last_updated`, `beta`, `annualized_vol`.

#### `xray_correlation_matrix`
* **Purpose:** Full pairwise correlation matrix stored as JSON blobs (one row per benchmark — cheapest way to reconstruct the N×N matrix).
* **Key Columns:** `benchmark` (PK), `last_updated`, `tickers_json`, `matrix_json`.

#### `xray_dividend_cache`
* **Purpose:** Per-holding dividend yield cache to avoid blocking the page load with live Ghostfolio calls.
* **Key Columns:** `ticker`, `data_source` (composite PK), `last_updated`, `dividend_yield_pct`, `dividend_in_base_currency`.

#### `xray_portfolio_returns_cache`
* **Purpose:** Orphaned (2026-06-29) — superseded by `xray_returns_cache` below. Previously held a single Ghostfolio-only weighted return series; no code reads or writes it anymore. Kept rather than dropped, per this codebase's convention for superseded cache tables (see `smgb_predictions`).
* **Key Columns:** `benchmark` (PK), `last_updated`, `dates_json`, `returns_json`, `benchmark_returns_json`.

#### `xray_returns_cache`
* **Purpose:** Per-ticker daily return series (same data already fetched for beta/vol/correlation), one row per ticker covering every ticker in the risk-cache universe (Ghostfolio + built-in Trading accounts). `assemble_xray_report` derives a weighted portfolio return series for whatever account scope was requested by combining the relevant tickers' cached series with that scope's current weights — historical VaR/CVaR, Sharpe/Calmar ratio, tracking error, and skewness/kurtosis work for any scope (Ghostfolio, built-in, or combined), not just a precomputed global one.
* **Key Columns:** `ticker`, `benchmark` (composite PK), `last_updated`, `dates_json`, `returns_json`.

#### `ai_contagion_snapshots`
* **Purpose:** AI sector contagion scan results — payload JSON + alert flag. Pruned to last 7 days automatically.
* **Key Columns:** `id` (PK autoincrement), `scan_ts`, `leader_count`, `etf_count`, `alert_fired`, `payload_json`.

#### `news_articles`
* **Purpose:** Full-text news articles with FinBERT sentiment scores, deduped by `article_id`.
* **Key Columns:** `id` (PK autoincrement), `article_id` (UNIQUE), `ticker`, `source_list`, `headline`, `published_at`, `sentiment_score`, `sentiment_label`.

#### `smgb_predictions`
* **Purpose:** Orphaned table (retained for data preservation). Was used by the removed SMGB.L Morning Price Predictor (`smgb_predictor.py`). No code reads or writes to this table anymore.
* **Key Columns:** `id` (PK autoincrement), `target_date` (UNIQUE), `predicted_price`, `actual_open`, `absolute_error`, `pct_error`, `direction_correct`.

#### `etf_predictor_configs`
* **Purpose:** Configuration for each generic ETF predictor setup (multi-config, user-managed from Settings).
* **Key Columns:** `id` (PK autoincrement), `name`, `etf_ticker`, `constituents` (JSON array of `{ticker, weight}`), `enabled`, `auto_schedule`, `pre_run_time` (HH:MM UTC), `post_run_time` (HH:MM UTC), `deleted_at` (soft-delete), `created_at`.
* **Notes:** Weights stored normalised to sum=1.0. Soft-delete preserves prediction history. Scheduler jobs are registered dynamically from this table at startup.

#### `etf_predictor_predictions`
* **Purpose:** Prediction log per config — tracks predicted vs actual open prices and accuracy metrics over time.
* **Key Columns:** `id` (PK autoincrement), `config_id` (FK to etf_predictor_configs), `run_at`, `prediction_date`, `target_date`, `prediction_type` (`next_open` | `us_open_impact`), `predicted_price`, `actual_open`, `absolute_error`, `pct_error`, `direction_correct`, `constituent_snapshot` (JSON weights at prediction time), `fx_rate`, `r_squared`.
* **Bias/blend tracking columns** (added July 2026): `bias_corrected_price`, `bias_corrected_change_pct`, `blended_price`, `blended_change_pct` — two alternate predictions logged alongside the standard one purely for later comparison (`etf_predictor_engine._compute_bias_corrected_prediction()` / `_compute_blended_prediction()`). Neither drives any other calculation; both stay `NULL` until a config has at least 10 resolved predictions of that `prediction_type`. `db_etf.get_recent_prediction_errors()` feeds both calculations; `db_etf.get_etf_accuracy()`'s summary computes `bias_corrected`/`blended` direction-accuracy/MAE/MAPE from the same already-fetched rows.
* **Constraint:** `UNIQUE(config_id, target_date, prediction_type)` — idempotent logging via `ON CONFLICT DO NOTHING`.

#### `trap_monitor_results`
* **Purpose:** Latest Trap Monitor scan result per ticker — one row per ticker, upserted on each scan. Powers `/trap-monitor` live table.
* **Key Columns:** `ticker` (PK), `phase` (lifecycle phase label), `bull_trap_level`, `bull_trap_vol_ratio`, `bear_trap_level`, `cap_level`, `cap_vol_zscore`, `wyckoff_level`, `wyckoff_bb_width`, `ema_distance`, `rsi`, `scan_ts`. Notes for each signal stored in `*_notes` text columns.
* **Phase values:** `ACTIVE_SELLOFF` | `BULL_TRAP_RISK` | `CAPITULATION_FORMING` | `BEAR_TRAP_RISK` | `ACCUMULATION` | `CAUTION` | `NEUTRAL`.

#### `trap_phase_history`
* **Purpose:** Append-only log of trap phase assignments per ticker per day, used to evaluate prediction accuracy retroactively. One row per (ticker, scan_date) — the first scan of each day is kept.
* **Key Columns:** `id` (PK autoincrement), `ticker`, `phase`, `scan_date` (YYYY-MM-DD), `scan_ts` (UTC ISO), `close_price` (reference price at scan time), `actual_price_14d` / `actual_date_14d` / `direction_correct_14d` (filled ~14 calendar days later), `actual_price_30d` / `actual_date_30d` / `direction_correct_30d` (filled ~30 calendar days later).
* **Constraint:** `UNIQUE(ticker, scan_date)` — `INSERT OR IGNORE` keeps the first result of each day.
* **Written by:** `bull_bear_trap_engine.TrapEngine._save_results()` → `database.log_trap_phase()`. Actuals filled daily by `trap_accuracy_fill_job` via `bull_bear_trap_engine.fill_trap_phase_actuals()`.

#### `bubble_radar_metrics`
* **Purpose:** Daily snapshot of the Bubble Risk Score and its seven component metrics for every ticker that has been scanned. One row per (ticker, scan_date); re-scans upsert the same row.
* **Key Columns:** `ticker`, `scan_date` (YYYY-MM-DD UTC), `bubble_score` (REAL 0–100), `flag` (NULL / `'watch'` / `'bubble'`), `sma_ext_pct` (% above 200-day SMA), `rsi_avg_20d`, `ps_ratio`, `peg_ratio`, `fcf_yield` (FCF/market-cap × 100), `riskfree_rate` (DFII10 used at scan time), `iv_call_skew` (NULL for non-US tickers), `spy_rsp_spread` (20-day return spread).
* **Written by:** `bubble_radar_engine.run_bubble_scan()`, called by the `bubble_radar_job` scheduler job.

#### `bubble_radar_history`
* **Purpose:** Append-only log of flag events (first time a ticker crosses a Watch or Bubble threshold) used to evaluate prediction reliability over 4-, 8-, and 12-week horizons.
* **Key Columns:** `id` (PK autoincrement), `ticker`, `flagged_date` (YYYY-MM-DD UTC), `flag_level` (`'watch'` / `'bubble'`), `price_at_flag`, `price_4w` / `price_8w` / `price_12w` (back-filled), `outcome_4w` / `outcome_8w` / `outcome_12w` (NULL / `'correct'` / `'incorrect'`).
* **Constraint:** `UNIQUE(ticker, flagged_date)` — only the first flag event per ticker per day is recorded.
* **Back-fill:** `bubble_radar_engine._backfill_outcomes()` runs on every scan and fills forward prices once sufficient time has elapsed.

#### `intraday_monitors`
* **Purpose:** Active dip-radar watch list — one row per ticker armed for today's session.
* **Key Columns:** `ticker` (PK), `date_added`, `expire_date`, `is_active`, `activated_by`.

#### `intraday_monitor_results`
* **Purpose:** Latest scan result per ticker so the dip-radar UI can poll without waiting for the next orchestrator cycle.
* **Key Columns:** `ticker` (PK), `scan_ts`, `current_price`, `reversal_score`, `is_bottoming`, `reasons_json`, `rsi`, `vwap`, `vwap_deviation`.

#### `model_training_log`
* **Purpose:** Audit log of ML model training runs — stores CV score and sample count per training event.
* **Key Columns:** `id` (PK autoincrement), `model_name`, `trained_at`, `n_samples`, `cv_score_mean`, `cv_score_std`, `score_metric`.
* **Note:** Created by `MacroAIEngine._ensure_training_log_table()` on first use, not by `db_schema.py:init_db()`.

#### `password_reset_tokens`
* **Purpose:** Stores one-time password-reset tokens generated by the self-service reset flow (`POST /api/request-password-reset`). Each token is stored as a SHA-256 hash — the raw token is only ever sent to the user, never stored.
* **Key Columns:** `token_hash` (PK TEXT), `expires_at` (TEXT UTC ISO), `used` (INTEGER 0/1).
* **Lifecycle:** Tokens expire after 1 hour. Once redeemed, `used` is set to 1 and the token cannot be reused.

#### `yahoo_api_stats`
* **Purpose:** Daily aggregated counters for all `yahoo_connection_boundary` invocations. Used by the Yahoo Finance API Usage card in Settings to show request volume, interface breakdown, rate-limit hits, and errors over the past 8 days.
* **Key Columns:** `date` (PK TEXT `YYYY-MM-DD` UTC), `total_calls`, `ipv4_calls`, `ipv6_calls`, `rate_limit_429`, `other_errors` (all INTEGER, default 0).
* **Writes:** Appended via a background daemon queue in `tools/network_engine._increment_api_stat()`. The queue drains to `_write_api_stat()` which uses SQLite `ON CONFLICT DO UPDATE` (upsert) so concurrent writes never corrupt the counters.
* **Reads:** `database.get_yahoo_api_stats(days=8)` → `GET /api/system/yahoo-api-stats`.

#### `yahoo_api_call_log`
* **Purpose:** Per-call detail backing the click-through chart on the Yahoo Finance API Usage card — clicking a day's row opens `/yahoo-api-usage?date=...` showing that day's requests by 15-minute local-time interval, stacked by which scheduled job was running when each call was made. Retained 8 days (matching `yahoo_api_stats`'s window) and pruned automatically.
* **Key Columns:** `id` (PK autoincrement), `call_time` (TEXT UTC `YYYY-MM-DD HH:MM:SS`), `date` (TEXT UTC `YYYY-MM-DD`, indexed), `interface` (`ipv4`/`ipv6`), `status` (`success`/`429`/`error`), `job_id` (raw APScheduler job id, `NULL` if the call happened outside any tracked job — e.g. a live page-view fetch), `action_context` (free-text description of the call, e.g. `"Ticker Info: AAPL"`).
* **Writes:** Same background daemon queue as `yahoo_api_stats` (`tools/network_engine._increment_api_stat()` → `_write_call_log_entry()`), extended to also capture the job id via `notification_engine.current_job_source()` (the same thread-local the app already uses to route job-status notifications) and the call's `action_context`. `_maybe_prune_call_log()` deletes rows older than 8 days, throttled to run at most once per hour.
* **Reads:** `database.get_yahoo_api_call_log(date_str)` (aggregates to per-minute/job/status counts in SQL to keep the payload small) → `GET /api/system/yahoo-api-stats/{date_str}`, which further buckets into 15-minute local-time intervals and resolves `job_id` to its canonical display name via `scheduler_manifest.job_label()`.

#### `accounts`
* **Purpose:** User-defined accounts for the built-in (non-Ghostfolio) portfolio system. One row per account; powers the `/accounts` page and feeds the Portfolio page alongside Ghostfolio holdings.
* **Key Columns:** `id` (PK autoincrement), `name`, `currency` (account reporting currency; equals the system `BASE_CURRENCY` for now), `account_type` (`Trading` | `House` | `Pension` | `Watchlist`, default `Trading` — only `Trading` accounts are aggregated by `accounts_engine.derive_account_holdings(None)`/`get_combined_holdings()` and therefore appear on the Portfolio page and its account-filter dropdown; `House`/`Pension` are tracked standalone — see Account Price Scraper below — and are excluded from portfolio-wide ticker aggregation; **immutable once set** — `PUT /api/accounts/{id}` rejects any request whose `account_type` differs from the existing value, since changing it could silently corrupt the type-specific ledger conventions described throughout this section), `initial_cash` (opening cash balance — the create/edit form and the `/accounts` tile relabel this to "Purchase Value" for House and "Opening Balance" for Pension, same underlying column), `opened_date` (user-set real-world account-opening date, e.g. for backfilled historical accounts; falls back to `created_at`'s date when not set — used as the Cash Balance History table's opening row date; relabeled "Purchase Date"/"Opening Balance Date" for House/Pension), `pension_start_date` (Pension-only in the UI — a separate, earlier date for when the pension itself started accumulating, distinct from `opened_date`; currently just stored, no display built on it yet), `opening_balance_units` (Pension-only in the UI — how many fund units the Opening Balance amount represents; `accounts_engine.sync_pension_opening_balance()` materialises this + `initial_cash` as a real `Buy` transaction against the synthetic ticker on every create/update, so a pre-existing balance shows real units/holdings immediately rather than starting at zero — see `opening_balance_txn_id`), `opening_balance_txn_id` (FK-ish to `account_transactions.id` — tracks which transaction represents the synced opening balance, so a later edit updates it in place instead of duplicating it, and clearing either field deletes it; `NULL` for non-Pension accounts or when no opening balance has been set), `pension_ticker_label` (Pension-only in the UI — a purely cosmetic display name shown instead of the internal `PENSION-{id}` ticker on the dedicated Pension detail page (`/accounts/{id}/pension`); `NULL` falls back to the internal ticker. The underlying ticker stored on every `account_transactions` row is never renamed, so `account_scraper_engine.parse_pension_account_id()` and the average-cost ledger are unaffected), `note`, `scraper_url`/`scraper_selector`/`scraper_headers` (JSON text, default `'{}'`)/`scrape_time` (`HH:MM`, default `'02:00'`)/`scraper_enabled` (the Account Price Scraper config for `House`/`Pension` accounts — see below), `autotopup_enabled`/`autotopup_amount`/`autotopup_frequency` (`monthly` | `weekly`)/`autotopup_day_of_month` (1-31)/`autotopup_day_of_week` (1-5 = Mon-Fri)/`autotopup_notes` (the Auto Top-up config for `Trading` accounts — see `account_autotopup_pending` below), `deleted_at` (soft-delete), `created_at` (DB-row creation timestamp, not user-editable).
* **Watchlist account:** exactly one `account_type='Watchlist'` row always exists — `db_schema._ensure_watchlist_account()` creates it on every boot if missing. It cannot be created, deleted, or have its type changed via `/api/accounts` (`api_routes_accounts.py` rejects all three). It holds no transactions; its detail page (`/accounts/{id}`) renders `watchlist_account_detail.html` instead of the standard ledger view. See `watchlist_items` below.
* **CRUD:** `db_accounts.py` (re-exported from `database.py`). Soft-delete preserves transaction history.

#### `account_transactions`
* **Purpose:** Full transaction ledger for built-in accounts. Every activity is retained — including Buys/Sells for tickers no longer held — so closed positions and realized P&L can be derived.
* **Key Columns:** `id` (PK autoincrement), `account_id` (FK to accounts), `txn_type` (`Buy` | `Sell` | `Fee` | `Dividend` | `Interest` | `Cash` | `Transfer`), `ticker`, `isin` (optional — International Securities Identification Number, the instrument's permanent identifier independent of ticker symbol changes; manually editable in the Add/Edit Transaction modal next to Ticker, and auto-populated by CSV import when the source file provides it), `company_name`, `currency` (trade currency — USD/EUR/GBP/GBp/…), `txn_date` (YYYY-MM-DD), `quantity`, `unit_price`, `fee`, `exchange_rate` (trade currency → base), `fee_currency` (the currency the `fee` itself is billed in — independent of `currency`, e.g. a broker's FX spread fee already quoted in base currency on a foreign-currency trade; `NULL` defaults to `currency` at read time), `fee_exchange_rate` (fee currency → base; `NULL` defaults to the row's own `exchange_rate` at read time — see `accounts_engine._fee_fx()`), `notes`, `update_cash` (0/1 — whether the row adjusts the cash balance; always 1 for manually-entered transactions, the UI no longer exposes this toggle — it stays 0 only for Ghostfolio-imported rows, which have no real deposit history to derive cash from), `price_in_pence` (0/1 — GBp pence), `ghostfolio_ref` (dedup key for imported activities, also reused with a `csv:` prefix by CSV import), `linked_txn_id` (the sibling row's `id` for `Transfer` rows — each transfer is two linked rows, a negative leg on the source account and a positive leg on the destination; `NULL` for every other type), `is_adjustment` (0/1, default 0 — set only by `accounts_engine.reconcile_cash()` on the synthetic `Cash` row it books to true up drift against a real broker statement; shown as an **Adjustment** badge and filterable in the Activities table; not part of `_ALLOWED_TXN_COLUMNS`, so editing the row's other fields later never clears it), `created_at`.
* **Notes:** Cost basis is computed in base currency via `exchange_rate`. Holdings, cash, and realized P&L are derived on read by `accounts_engine.py` — no aggregated snapshot is stored here. `Transfer` rows reuse the `Cash` type's sign convention (negative=outgoing, positive=incoming) so `_cash_delta` needs no special-cased branch. Transfers are created via `accounts_engine.create_transfer()` (both rows inserted atomically) and deleted via `delete_transaction_with_pair()` (deleting either leg deletes both); they cannot be edited in place — delete and recreate instead. `_cash_delta` converts `fee` using `_fee_fx()` (the fee's own rate) rather than the trade's `_fx()` — previously the fee always inherited the trade's `exchange_rate`, which silently mis-converted a fee billed in a different currency than the trade (e.g. an FX spread fee already quoted in GBP on a USD trade being multiplied by the USD rate a second time).

#### `account_value_history`
* **Purpose:** Per-day snapshot of each built-in account's value, used to draw the account-value-over-time chart on the account detail page. Written nightly by the scheduled job, but also recomputed immediately (via `accounts_engine.resnapshot_account()`) after every transaction add/edit/delete, transfer, or Ghostfolio import, so the chart never waits a full day to reflect a change.
* **Key Columns:** `id` (PK autoincrement), `account_id` (FK to accounts), `snapshot_date` (YYYY-MM-DD), `total_value` (cash + equity), `cash_value`, `equity_value`, `net_contributions` (cumulative `initial_cash` + every `Cash`/`Transfer` movement only — excludes Buy/Sell/Dividend/Interest/Fee, so comparing it against `total_value` shows at a glance whether the account is ahead of or behind what was actually put in) — all in base currency. For Pension accounts, `cash_value` is always written as `0` regardless of `cash_balance()`'s `initial_cash` baseline — a Pension has no real cash sub-ledger, so that baseline would otherwise leak in as a phantom constant offset on every row, decoupling `total_value` from the Pension Value tile's `equity_value`.
* **Constraint:** `UNIQUE(account_id, snapshot_date)` — idempotent upsert via `ON CONFLICT DO UPDATE`.

#### `account_value_history_currency`
* **Purpose:** Per-day, per-currency breakdown of each built-in account's equity value — one row per account/day/currency actually held that day. Extends `account_value_history` (which only stores the day's combined total) so a Home Assistant/portfolio-wide Time-Weighted Return can be computed both "as actually experienced" (today's live FX rate) and "FX-neutral" (isolating the equity-only return from currency movement) — see `accounts_engine.portfolio_twr_fx()` / `portfolio_twr_ex_fx()`.
* **Key Columns:** `account_id` (FK to accounts), `snapshot_date` (YYYY-MM-DD), `currency`, `equity_value_native` (that currency's equity value in its own currency, e.g. USD), `equity_value_base` (the same value converted to `BASE_CURRENCY` using that day's `fx_rate`), `fx_rate` (currency → BASE_CURRENCY rate in effect on `snapshot_date`).
* **Constraint:** `PRIMARY KEY (account_id, snapshot_date, currency)` — idempotent upsert via `ON CONFLICT DO UPDATE`.
* **Writes:** Populated alongside every `account_value_history` write — `accounts_engine.snapshot_all_accounts()`, `resnapshot_account()`, `backfill_value_history()`, and `_backfill_house_value_history()` all derive the per-currency breakdown from the same equity-valuation pass via the shared `accounts_engine._bucket_equity_by_currency()` helper, so the two tables are always written together and stay in sync (summing `equity_value_base` across a date's currency rows reproduces that date's `account_value_history.equity_value`).
* **CRUD:** `db_accounts.upsert_value_snapshot_currency()` / `get_value_history_currency()` (both re-exported from `database.py`).

#### `account_performance_cache`
* **Purpose:** One row per `Trading` account holding its last computed live-performance figures — total value, equity value, cash balance, unrealized P&amp;L, realized P&amp;L, dividend/interest income, 1D/1W/1M/3M/6M/1Y period gain/loss in `BASE_CURRENCY`, and since-inception Money-Weighted Rate of Return (Modified Dietz method, %) — powering the live tile rows on the Trading account detail page and the Home Assistant `account_metrics_list()` per-account sensor set. Written by `accounts_engine.refresh_performance_cache()` as a side effect of the 5-minute intraday scan (`intraday_orchestrator_job`, which already fetches live prices for every held ticker into `market_pulse_cache`) so the figures are computed once server-side and shared by every browser/tab that polls `GET /api/accounts/{id}/live-performance`, rather than each poll re-deriving MWRR/period-returns from the full transaction history. Falls back to computing on the fly (still writing the row) if a request arrives before the next scan has populated it — e.g. a brand-new account. `realized_pnl`/`dividend_income`/`interest_income` are derived from the same `account_summary()` call as the other fields in that refresh, so `account_metrics_list()` can read every field from this one cache row instead of mixing it with a separately-timed live `account_summary()` call.
* **Key Columns:** `account_id` (PK, FK to accounts), `total_value`, `equity_value`, `cash_balance`, `unrealized_pnl`, `return_1d`, `return_1w`, `return_1m`, `return_3m`, `return_6m`, `return_1y` (all currency amounts, `end value − start value − net contributions during the period` — deliberately not a percentage, since dividing by the period's starting value blows up whenever that baseline is small, e.g. a lookback window older than the account itself falling back to the earliest snapshot near opening), `mwrr` (%, the one genuinely rate-based metric here — `None` if the account has no contributions yet), `realized_pnl`, `dividend_income`, `interest_income` (currency amounts, from `account_summary()`), `last_updated` (Unix timestamp).
* **CRUD:** `db_accounts.upsert_performance_cache()` / `get_performance_cache()` (both re-exported from `database.py`).

#### `account_price_history`
* **Purpose:** Raw daily price series for the **Account Price Scraper** feature — a generic URL + CSS-selector price feed for `House`/`Pension` accounts, replicating what Ghostfolio's "manual asset" scraper does (the user's own external cron scripts write a small static HTML page such as `<div id="gf-price">123.45</div>`; this table stores what was extracted from it). One row per account per day. For `House`, the latest row *is* the account's equity value directly (`accounts_engine._equity_value_for_account`). For `Pension`, the row is the fund's unit price, used to value a synthetic single holding (see `account_transactions` note below) and to auto-resolve the price for "Pay In"/"Admin Fee" actions.
* **Key Columns:** `id` (PK autoincrement), `account_id` (FK to accounts), `price_date` (YYYY-MM-DD), `price`, `source` (`scrape` | `csv_import` | `purchase` — distinguishes a scheduled/manual scrape, a pasted historical-CSV backfill, or the House purchase-price row `accounts_engine.sync_house_purchase_price()` seeds at `opened_date`), `created_at`.
* **Constraint:** `UNIQUE(account_id, price_date)` — idempotent upsert via `ON CONFLICT DO UPDATE`, so re-scraping the same day overwrites rather than duplicates.
* **CRUD:** `db_accounts.py` (re-exported from `database.py`): `add_price_history`, `get_price_history`, `get_latest_price`, `get_price_as_of`. Fetch/extract/CSV-parse logic lives in `account_scraper_engine.py`.
* **Pension's synthetic holding:** a Pension account's one holding is represented as ticker `PENSION-{account_id}` in `account_transactions` — never shown in the UI, purely an internal key so the existing average-cost ledger (`_ledger_for_account`) can represent "units of the fund" with no changes to that machinery. "Pay In" creates a `Buy` row (units = amount ÷ that date's price); "Admin Fee" creates a `Sell` row sized from the units-before (from the ledger) minus the units-after the user reads off the provider's portal. Both set `update_cash=False` — the money never passes through the account's cash balance. `accounts_engine.current_price_map()` resolves this ticker's current price from `account_price_history` instead of `stock_signals`.

#### `holding_price_limits`
* **Purpose:** Stores an optional low/high price alert limit per (account, ticker) holding, set from the Home Assistant integration's Phase 3 Low Limit / High Limit number entities (both disabled by default). Powers the `low_limit_set`/`low_limit_reached`/`high_limit_set`/`high_limit_reached` fields on `GET /api/accounts/holdings-list`. Nothing in the main app's own UI reads or writes this table — it exists purely to give the Home Assistant integration a place to persist a value that survives HA restarts and is visible to every poller, not just the one that set it.
* **Key Columns:** `account_id` (FK to accounts), `ticker`, `low_limit`, `high_limit` (both nullable — either can be set independently of the other), `updated_at`.
* **Constraint:** `PRIMARY KEY (account_id, ticker)` — idempotent upsert via `ON CONFLICT DO UPDATE`, only overwriting the column(s) actually supplied so setting one limit never clears the other.
* **CRUD:** `db_accounts.upsert_holding_price_limit()` / `get_all_holding_price_limits()`. `portfolio_metrics_engine.set_holding_price_limit()` is the caller-facing wrapper used by `POST /api/accounts/holding-price-limit`.

#### `account_autotopup_pending`
* **Purpose:** Staging table for the **Auto Top-up** feature (`Trading` accounts only) — the scheduled job (`scheduler_jobs.register_account_topup_job`/`_run_account_topup_job`, dynamic job id `account_autotopup_{id}_job`) never posts a cash deposit directly, since the real bank credit date can drift around weekends/holidays. Instead it inserts a `pending` row here, the account is tagged `[PENDING ACTION]` on `/accounts`, and the account detail page surfaces a confirmation banner. Multiple unresolved rows can stack per account; each is resolved independently rather than the newest replacing or blocking earlier ones.
* **Key Columns:** `id` (PK autoincrement), `account_id` (FK to accounts), `scheduled_date` (the date the job fired), `expected_amount` (the configured `autotopup_amount` at fire time), `status` (`pending` | `confirmed` | `dismissed`), `confirmed_amount`/`confirmed_date` (the operator-edited values actually used, set only on confirm), `txn_id` (FK to the `account_transactions` row created on confirm — `NULL` until then or if dismissed), `created_at`.
* **CRUD:** `db_accounts.py` (re-exported from `database.py`): `create_pending_topup`, `get_unresolved_pending_topups`, `get_pending_topup`, `resolve_pending_topup`. Confirm/dismiss logic lives in `accounts_engine.confirm_autotopup()`/`dismiss_autotopup()` — confirming posts a `Cash` transaction (`update_cash=True`) for the edited amount/date; dismissing never touches `account_transactions`.
* **Scheduling:** one dynamic APScheduler job per scraper-enabled account (`account_scraper_{account_id}_job`, registered/unregistered by `scheduler_jobs.register_account_scraper_job`/`unregister_account_scraper_job`), at the account's own `scrape_time` in `USER_TIMEZONE` — configured from the Accounts page tile/detail page, not the Settings page. See `scheduler_manifest.JOB_GRAPH["account_scraper_dynamic"]`.

#### `treasury_bills`
* **Purpose:** Tracks UK Treasury bill holdings (`Trading` accounts only) — zero-coupon instruments bought at a discount to face value with no coupon, maturing at par roughly 28 days later. One row per purchase. Each bill's Buy leg is posted to `account_transactions` against a unique synthetic ticker (`TBILL-{buy_txn_id}`), so concurrently-held bills never blend cost basis in `_ledger_for_account`'s average-cost math the way a shared ticker would.
* **Key Columns:** `id` (PK autoincrement), `account_id` (FK to accounts), `buy_txn_id` (FK to the `account_transactions` Buy row), `ticker` (`TBILL-{buy_txn_id}`, `UNIQUE`), `face_value` (par/maturity value — see note below on how this is derived), `purchase_price` (discount price actually paid — always `< face_value`), `indicative_ytm` (nullable — the annualised yield shown in the app at purchase time; overwritten with the real confirmed rate once the operator confirms it, see below), `ytm_confirmed` (bool, default `0` — whether the operator has confirmed/accepted the YTM since the Friday tender closed; `1` immediately if no `indicative_ytm` was given at purchase, since there's then nothing to confirm), `purchase_date`, `maturity_date`, `auto_reinvest` (bool — a reminder-only flag, see below), `status` (`Open` | `Matured`), `maturity_txn_id` (FK to the `account_transactions` Sell row posted at maturity, `NULL` until then), `notes`, `created_at`.
* **No `currency` column** — always derived from the linked account at read time (one canonical source, per the codebase's "never duplicate a shared calculation" rule).
* **`face_value` starts as an estimate, editable any time it turns out wrong:** Freetrade never states a bill's face value directly — only the amount debited and an indicative, pre-tender yield (the real yield for a specific bill isn't fixed until the Friday DMO tender closes, which can be after the bill is already logged in this app). The Buy T-Bill modal computes `face_value = purchase_price + purchase_price × indicative_ytm × days/365` (`treasury_bill_engine.estimate_face_value()`) and pre-fills it as an editable field. Once `purchase_date` (the settlement date, always after that Friday's tender) has arrived, an unconfirmed bill surfaces a "Confirm the final YTM" banner on the account page (`treasury_bill_engine.bills_pending_ytm_confirmation()`, mirroring the Auto Top-up confirm/dismiss pattern); entering the real rate recomputes `face_value` with the same formula, and "Keep Estimate" just clears the banner unchanged — both via `treasury_bill_engine.confirm_ytm()`. That same function backs a general **Edit** action available on every bill's row at any time, Open or already Matured — accepting either a `confirmed_ytm` to recompute from, or a `face_value` directly (if the operator knows the exact redemption figure without needing a rate). **Editing a Matured bill also corrects its posted maturity Sell transaction's amount to match**, since that transaction — not the `treasury_bills` row — is what the account's cash balance actually derives from; without this, an edit after maturity would silently desync the two.
* **Pricing:** `treasury_bill_engine.accreted_price()` computes a deterministic straight-line value between `purchase_price` (at `purchase_date`) and `face_value` (at `maturity_date`) — no external price feed needed, unlike the Pension/House synthetic-holding pattern's `account_price_history`. `accounts_engine.current_price_map()` dispatches any `TBILL-%` ticker to this function (mirroring the existing `PENSION-%` branch), so `holdings_with_market_value`, `portfolio_totals`, and the Home Assistant `holdings-list` endpoint all show the accreting value automatically.
* **CRUD:** `db_accounts.py` (re-exported from `database.py` where used by the API layer): `create_treasury_bill`, `get_treasury_bill`, `get_treasury_bill_by_ticker`, `get_treasury_bills_for_account`, `get_open_treasury_bills_due`, `mark_treasury_bill_matured`, `update_treasury_bill_auto_reinvest`, `get_treasury_bills_pending_ytm_confirmation`, `confirm_treasury_bill_ytm`, `delete_treasury_bill`. Purchase/maturity/confirmation/deletion logic lives in `treasury_bill_engine.py`.
* **Scheduling:** the always-on daily `treasury_bill_maturity_sweep_job` (`treasury_bill_engine.sweep_matured_bills`, runs 07:00 in `USER_TIMEZONE`, no Settings toggle — a matured position must never sit open corrupting cash/holdings) posts the maturity `Sell` transaction for any bill whose `maturity_date` has arrived, crediting the account's cash balance with `face_value`. If `auto_reinvest` is set, it fires a `notification_engine.notify("treasury_bill_reminder", ...)` reminder — **never an automatic re-purchase**, since the actual yield on the next weekly DMO tender isn't known until the Friday it closes. The YTM-confirmation banner, by contrast, is a plain page-render-time check (`purchase_date <= today AND ytm_confirmed=0`) — no separate scheduled job or notification, since the bill row already exists in full from the moment of purchase.
* **Excluded from Yahoo fetch machinery:** both `db_accounts.get_all_account_tickers()` and `accounts_engine.held_tickers_lightweight()` exclude `TBILL-%` tickers, the same way they exclude `PENSION-%` — the latter exclusion is load-bearing here in a way it wasn't for Pension, since T-bill tickers live inside ordinary `Trading` accounts rather than a dedicated account type.

#### `backup_history`
* **Purpose:** Audit log of every Backup &amp; Recovery run (scheduled or manual). Powers the Backup Status sub-panel in the Settings → System Diagnostics card and the "last backup" summary used by the Recovery file selector.
* **Key Columns:** `id` (PK autoincrement), `started_at`/`finished_at` (UTC `YYYY-MM-DD HH:MM:SS`), `trigger_type` (`scheduled` | `manual`), `location_type` (`local` | `nfs`), `destination` (resolved backup directory path at the time of the run), `components` (comma-joined subset of `data,models,database` that was actually archived), `filename` (the `backup_YYYYMMDD_HHMMSS.tar.gz` written, `NULL` on failure before an archive was created), `size_bytes`, `status` (`success` | `error`), `error_message`.
* **Writes:** `backup_engine._record_backup_history()`, called once per run from `backup_engine.run_backup()`.
* **Note:** This table only records run *history* — the archive files themselves live on disk at the configured destination (local folder or NFS mount), never inside SQLite. `backup_engine.list_backups()` lists those files directly from the filesystem; `get_backup_status()` joins the latest history row with that file listing.

#### `watchlist_items`
* **Purpose:** The native watchlist — replaces Ghostfolio as the source of truth for "tickers I'm following without holding." Every row belongs to the single system-managed `accounts` row of type `Watchlist`. Powers the star toggle on `/stock/{ticker}`, the `/watchlist` page, and the compact management UI at `/accounts/{watchlist_id}`.
* **Key Columns:** `id` (PK autoincrement), `account_id` (FK to the Watchlist account), `ticker`, `company_name`, `currency`, `quote_type` (EQUITY/ETF/MUTUALFUND/etc., from Yahoo), `exchange` (always resolved via `time_engine.ticker_exchange()`, never Yahoo's free-text `exchDisp`, so it matches the `LSE`/`NYSE`/`XETRA`/`TSE` vocabulary used everywhere else), `added_at`.
* **Constraint:** `UNIQUE(account_id, ticker)` — re-adding an already-watched ticker is a no-op (`INSERT OR IGNORE`).
* **CRUD:** `db_accounts.py` (re-exported from `database.py`): `get_watchlist_account`, `get_watchlist_items`, `add_watchlist_item`, `delete_watchlist_items`, `remove_watchlist_ticker`, `get_watchlist_tickers`. Metadata (`company_name`/`currency`/`quote_type`/`exchange`) is resolved once at insert time via `accounts_engine.resolve_watchlist_metadata()`.
* **Migration:** `db_schema._import_legacy_watchlist_json()` runs once at startup — if the table is empty and `data/watchlist.json` exists, every ticker is imported (enriched from cached `stock_signals` data where available). The file itself is left on disk afterward and is never read or written again.

---

### Stateless Tools (no DB table)

#### Historical Stress Tester (`stress_engine.py`)
Results are computed on-demand and returned directly in the API response — **no table is written**. The engine reads per-ticker beta from `xray_risk_cache` and holdings from Ghostfolio's live API, applies pre-calibrated scenario shocks, and returns the monetary impact report. Run the X-ray nightly job first to ensure `xray_risk_cache` is populated with up-to-date betas.

---

## 5. Scheduler Job Dependency Map (Workflow Monitor)

The Workflow Monitor (Settings UI) does not store a graph in the database — it is derived at request time from a **declarative manifest plus live state**. The scheduler subsystem is split across four modules (all re-exported from `scheduler_engine` for backward compatibility):

| Module | Responsibility |
|---|---|
| `scheduler_engine.py` | APScheduler setup, `start_scheduler`, `reload_scheduler`, job wiring + tracking infrastructure |
| `scheduler_jobs.py` | All `run_*` job runner functions + `resume_interrupted_scans` |
| `scheduler_manifest.py` | `JOB_GRAPH`, `CONFIG_KEY_TO_JOB`, `job_label()`, `scheduler_display_names()`, `_resolve_manifest()` |
| `scheduler_monitor.py` | `build_workflow_graph()`, `detect_workflow_conflicts()` |

* **`JOB_GRAPH`** (`scheduler_manifest.py`): one entry per `scheduler.add_job(... id=X)`. Each entry declares the data **artifacts** a job reads (`consumes`) and writes (`produces`) — e.g. `quant_signals`, `ml_model`, `ml_predictions`, `historical_parquet`, `portfolio`, `sentiment`. Dynamic per-config jobs (ETF predictors, and the per-account Account Price Scraper jobs — `account_scraper_{account_id}_job`, matched via `_DYNAMIC_ACCOUNT_SCRAPER_RE`) are matched in `_resolve_manifest()`.
* **Edges** are derived, never hand-listed: a dependency `A → B` exists iff `A.produces ∩ B.consumes ≠ ∅`. Adding a job with correct declarations auto-wires it into the graph.
* **Live overlay:** `build_workflow_graph()` (`scheduler_monitor.py`) merges the manifest with `scheduler.get_jobs()` (enabled? next run?) and `scheduler_run_log` (last run, avg duration, last status) to colour each node and compute conflicts.
* **Conflict engine** (`detect_workflow_conflicts()` in `scheduler_monitor.py`): `overlap_risk` (consumer scheduled within the producer's average run duration), `backwards_ordering`, `disabled_upstream`, `stale_never_run`, `last_run_error`.
* **Enforcement:** the manifest-completeness test fails CI if any registered job lacks a `JOB_GRAPH` entry, keeping the map in sync with the scheduler automatically.
