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

---

## 1b. Indexes

| Index | Table | Columns | Notes |
|---|---|---|---|
| `idx_macro_event_date` | `macro_calendar` | `event_date` | Range-filtered by 6+ call sites; added June 2026. |
| *(PK)* | `quant_signals` | `ticker, date` | Composite PK; covers all ticker+date lookups. The redundant `idx_qs_ticker_date` explicit index was dropped June 2026. |

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

Tables added after initial schema creation. All managed via `database.py:init_db()` and `migrate_db()`.

#### `ticker_metadata`
* **Purpose:** Lightweight beta + market-cap cache used by ML feature assembly. Created by `ai_prediction_engine.sync_ticker_metadata()` rather than `init_db()` (see code note).
* **Key Columns:** `ticker` (PK), `sector`, `beta`, `market_cap`.

#### `market_pulse_cache`
* **Purpose:** Cached CNN Fear & Greed + major-index snapshot to avoid re-fetching on every page load.
* **Key Columns:** `ticker` (PK), `name`, `price`, `change_pts`, `change_pct`, `is_positive`, `last_updated`.

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
* **Purpose:** Per-ticker beta and annualised volatility vs the configured benchmark, pre-computed by the scheduler job.
* **Key Columns:** `ticker`, `benchmark` (composite PK), `last_updated`, `beta`, `annualized_vol`.

#### `xray_correlation_matrix`
* **Purpose:** Full pairwise correlation matrix stored as JSON blobs (one row per benchmark — cheapest way to reconstruct the N×N matrix).
* **Key Columns:** `benchmark` (PK), `last_updated`, `tickers_json`, `matrix_json`.

#### `xray_dividend_cache`
* **Purpose:** Per-holding dividend yield cache to avoid blocking the page load with live Ghostfolio calls.
* **Key Columns:** `ticker`, `data_source` (composite PK), `last_updated`, `dividend_yield_pct`, `dividend_in_base_currency`.

#### `xray_portfolio_returns_cache`
* **Purpose:** Weighted daily portfolio return series for historical VaR/CVaR, tracking error, Sharpe, and skewness/kurtosis.
* **Key Columns:** `benchmark` (PK), `last_updated`, `dates_json`, `returns_json`, `benchmark_returns_json`.

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
* **Note:** Created by `MacroAIEngine._ensure_training_log_table()` on first use, not by `database.py:init_db()`.

#### `password_reset_tokens`
* **Purpose:** Stores one-time password-reset tokens generated by the self-service reset flow (`POST /api/request-password-reset`). Each token is stored as a SHA-256 hash — the raw token is only ever sent to the user, never stored.
* **Key Columns:** `token_hash` (PK TEXT), `expires_at` (TEXT UTC ISO), `used` (INTEGER 0/1).
* **Lifecycle:** Tokens expire after 1 hour. Once redeemed, `used` is set to 1 and the token cannot be reused.

---

### Stateless Tools (no DB table)

#### Historical Stress Tester (`stress_engine.py`)
Results are computed on-demand and returned directly in the API response — **no table is written**. The engine reads per-ticker beta from `xray_risk_cache` and holdings from Ghostfolio's live API, applies pre-calibrated scenario shocks, and returns the monetary impact report. Run the X-ray nightly job first to ensure `xray_risk_cache` is populated with up-to-date betas.

---

## 5. Scheduler Job Dependency Map (Workflow Monitor)

The Workflow Monitor (`scheduler_engine.py`, Settings UI) does not store a graph in the database — it is derived at request time from a **declarative manifest plus live state**.

* **`JOB_GRAPH`** (`scheduler_engine.py`): one entry per `scheduler.add_job(... id=X)`. Each entry declares the data **artifacts** a job reads (`consumes`) and writes (`produces`) — e.g. `quant_signals`, `ml_model`, `ml_predictions`, `historical_parquet`, `portfolio`, `sentiment`. Dynamic per-config jobs (ETF predictors) are matched in `_resolve_manifest()`.
* **Edges** are derived, never hand-listed: a dependency `A → B` exists iff `A.produces ∩ B.consumes ≠ ∅`. Adding a job with correct declarations auto-wires it into the graph.
* **Live overlay:** `build_workflow_graph()` merges the manifest with `scheduler.get_jobs()` (enabled? next run?) and `scheduler_run_log` (last run, avg duration, last status) to colour each node and compute conflicts.
* **Conflict engine** (`detect_workflow_conflicts()`): `overlap_risk` (consumer scheduled within the producer's average run duration), `backwards_ordering`, `disabled_upstream`, `stale_never_run`, `last_run_error`.
* **Enforcement:** the manifest-completeness test fails CI if any registered job lacks a `JOB_GRAPH` entry, keeping the map in sync with the scheduler automatically.
