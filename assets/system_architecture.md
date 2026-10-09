# 🗺️ System Architecture & Comprehensive Data Flow

This document details the high-priority arbitration logic, dual-storage strategy, and frontend rendering pipeline of the Quantamental Web Terminal.


## 🧠 System-Wide Ingestion & Priority Arbitration (The Brain)

This section details how the platform handles system-wide state when external dependencies fail. It is not a simple linear flow; it maps a nested logical decision grid.

The flow moves strictly from left to right:

### 1. External Data Sources & Inputs
Data streams on the far left show the raw inputs the system relies on. Note the fragility of these connections:
* **Built-in Accounts:** Primary portfolio holdings and transaction ledger; the singleton Watchlist account supplies watchlist membership.
* **Ghostfolio API:** Optional additional holdings, merged through `accounts_engine.get_combined_holdings()` when enabled.
* **Freetrade CSV:** Contains user-specific transaction data.
* **Yahoo Finance (YF API):** The primary source for corporate metadata, fundamental health metrics, and End-of-Day price data.
* **Universe Lists:** Ticker lists defining the available asset universe (e.g., all symbols on the LSE or a specific US Stock Universe).

### 2. The Priority Arbitration Logic (The Decision Grid)
The center of the diagram zooms in on the `System-Wide Ingestion & Priority Arbitration Engine`. Data doesn’t just pass through this engine; it is filtered and prioritized. It visualizes the decision logic I have designed to make your platform self-healing:

#### A. Holdings and Delisting Handling
All portfolio consumers read `accounts_engine.get_combined_holdings()`; watchlist
consumers read `database.get_watchlist_tickers()`. Ghostfolio JSON files are optional
inputs to the canonical holdings merge, rather than independent portfolio sources.
Synthetic and explicitly ignored tickers are excluded from Yahoo requests through
`utils.is_excluded_from_yahoo_fetch()`. The local ignored-ticker list is stored in
`data/freetrade_blacklist.json`.

#### B. Freetrade Synchronization Check
* **Action:** If the CSV data sync is confirmed, the engine marks the corresponding assets as 'is_freetrade' (setting the flag in SQLite) during the fundamental update.

#### C. Yahoo Finance API Status (Rate Limit Fallback)
This is the most critical logic leg for backend stability:
* **Fail Condition (Rate Limit/Ban):** If the Yahoo Finance API repeatedly fails or rejects connections:
    * **Action:** The engine triggers a total fallback. Instead of crashing, the system pivots and leverages the local **Cache** (the Parquet and JSON fundamentals stored in `data/historical/` and `data/fundamentals/`) rather than trying to make real-time network calls.
* **Success Condition:** If the API responds successfully, the storage files are updated with the new End-of-Day price matrices.

**In-memory `yahoo_engine` cache vs. the authoritative nightly writers.** Separate from the Parquet/JSON fallback cache above, `yahoo_engine.YahooEngine` also holds a short-lived in-process cache (`_TTLS`, e.g. 4 hours for `get_price_history()`'s daily-bar requests) purely to spare Yahoo from redundant requests for the same ticker within a short window. This is safe for most callers, but the *authoritative* nightly writers — `data_engine.DataEngine.update_all_data()`'s `fetch_market_baseline()` and `bulk_download_historical()`, which write the verified daily close into `stock_signals`/`data/historical/*.parquet` — call `get_price_history(..., force_refresh=True)` to bypass it entirely. Found 2026-07-08: a manual single-ticker refresh (`fetch_and_save_single_ticker`, run mid-afternoon before market close) cached that day's still-current close under the normal 4-hour TTL; the scheduled nightly job then ran later that same evening, fell inside that same TTL window, and was served the *same stale, pre-close* DataFrame back — but it still wrote that stale data into `stock_signals` with a brand-new `last_updated` timestamp, which fooled `accounts_engine.current_price_map()`'s freshness comparison (it now requires the stock-signals timestamp to lead the pulse timestamp by at least six hours before overriding a cached pulse price) into treating hours-old prices as the verified close. `force_refresh=True` on these two call sites guarantees the authoritative path always queries Yahoo fresh, regardless of what any other caller fetched earlier that day.

**Nightly FX pair histories.** After the main download, `DataEngine.update_all_data()` also bulk-downloads the daily history of every `{currency}{BASE_CURRENCY}=X` pair in scope (`accounts_engine.in_scope_currencies()`: the configured `ACCOUNT_CURRENCIES` plus the native currencies of the fetch universe; the same currency set the pulse-cache retention uses). They go through the same `prepare_daily_history()` writer but are not added to `get_all_tickers()`, so the Quant Scan, ML training, sentiment and tail-risk universes are unchanged. The dated-FX readers therefore find a current `USDGBP=X`-style file instead of waiting for a lazy first-read fetch; a pair outside the universe (for example a Strategy Backtester basket in a new currency) is still fetched on first read.

**Cache memory reclamation.** Expired entries are deleted on lookup. Cache reads
and writes also sweep all expired keys at most once per minute, so entries for
symbols or request parameters that are never revisited can be released. Global
Model Training (Walk-Forward) explicitly calls `YahooEngine.prune_expired()` before
its memory check. Sweeps use the cache lock only for in-memory operations; no
network request or sleep occurs under that lock. Valid entries retain their TTLs,
and the on-disk Parquet/JSON fallback is unaffected.

#### D. Universe Arbitration (US/LSE Priorities)
The diagram visualizes how multiple, conflicting lists are resolved:
1. **Priority 1:** Built-in portfolio, Watchlist, and account-transaction tickers.
2. **Priority 2:** LSE universe tickers.
3. **Priority 3:** US universe tickers.

The daily fetch also includes enabled market-registry tickers and their futures.
Duplicate symbols are fetched once. Portfolio holdings include Ghostfolio when enabled.

### 3. Dual-Storage Layer (The Divide)
Once clean, prioritized vectors have been created by the Arbitration Engine, they are split for storage efficiency:

#### A. Relational SQLite (`analysis.db`)
Stored metadata and relational signals map to specific SQLite tables:
* **Tables:** `stock_signals`, `quant_signals`, `market_universe`, `asset_profiles`.
* **Content:** P/E ratios, System Verdict scores (0-100), sector classifications, industry descriptions.

#### B. Time-Series Parquet & JSON (Compressed Offload)
Massive raw data matrices are directed here:
* **Folder Structure:** `data/historical/*.parquet` and `data/fundamentals/*.json`.
* **Content:** 2 years of daily OHLCV (Open, High, Low, Close, Volume) data and raw `.info` JSON dumps.
* **Shared reader:** `data_engine.load_or_fetch_daily_history(ticker)` is the canonical way for any engine to get a ticker's daily history — it reads the parquet already written for that ticker and only falls back to a live Yahoo fetch (caching the result for next time) when no parquet exists yet. `quant_engine.py`, `risk_engine.py`, `earnings_vol_engine.py`, `ai_prediction_engine.py`, and `xray_engine.py` all read through it rather than independently re-downloading the same 1–2 year window from Yahoo.

### 4. Frontend Render Layer ( JINJA2 / JS / Charting)
Finally, the schematic maps how the Web Terminal combines both data streams to render the UI you use:

* **QUANTAmental Web UI (Jinja2):** The FastAPI backend serves the main HTML templates.
* **Relational DataTables (AJAX):** JavaScript utilizes DataTables with AJAX to query massive amounts of structured data **strictly from SQLite**.
* **Interactive Charts (Plotly.js):** Charts use engine-provided time series and summaries; heavy price history comes from Parquet while relational results and caches come from SQLite.
* **Nextcloud Talk:** Alerts are delivered through the unified notification router according to each source's configured channels.

Stock Detail converts the recorded base-currency average cost into the ticker's native quote units before calculating unrealized P&L, for both the global position and each account. The pence conversion follows `stock_signals.currency == "GBp"`, independently of the transaction currency or `price_in_pence` flag. A pence-quoted holding bought in GBP therefore keeps the same cost basis and percentage return as Portfolio.

### Request latency diagnostics

`main.py` times Portfolio, Watchlist, Stock Detail, intraday refresh, and the four Home Assistant account-list/summary routes. Their `Server-Timing` response header reports application handler time plus measured stages when present: `sql`, `fx_context`, `fx_rate`, `history_anchors`, `yahoo_lock_wait`, `yahoo_fetch`, `chart`, `template`, and `metrics`. The `template` stage measures Jinja response rendering after context values are assembled. Stage values are elapsed milliseconds and can overlap when one measured call invokes another. The header ends when the response is assembled; it does not measure browser rendering or network transfer. Requests over one second write an INFO `request_timing` line; faster requests write the same line at DEBUG. Logs use fixed route patterns, method, status, and durations only, without query strings, tickers, cookies, headers, or response bodies.

A lifecycle task samples the event loop every 250 ms and writes `event_loop_lag` at WARNING when a sample is at least 100 ms late. A watchdog thread samples the loop stack at the 100 ms threshold and adds `suspected_blocker=module.py:function:line` for the nearest project frame, plus `callers=` with up to 11 outer project frames in call order; each entry contains only a source filename, function and line. This shows which route or helper reached a generic wrapper such as `database._retry_on_locked()`. A sample at that wrapper's `call(*args, **kwargs)` line means a SQLite execute or commit was in progress; it does not show that a lock error or retry occurred. A field is `unavailable` if the watchdog could not capture it. This is a snapshot of the code running during the delay, not proof that the named function caused the full delay. The lag value is how late the probe resumed, not a request duration. A long GIL hold in another thread can prevent the watchdog from sampling; external-library work is attributed to its nearest project caller. Use nearby `request_timing` lines and `Server-Timing` stages to distinguish a slow handler from a request waiting before dispatch. `debug_scripts/performance_baseline.py` repeats an offline route matrix against an isolated development data snapshot and includes a controlled stalled intraday fetch. It blocks yfinance calls and stubs FX rates; it must never be pointed at production data or interpreted as a browser first-byte measurement. Remove its copied development snapshot from the worktree before running the regression suite.

### Request execution during blocking work

The Fundamentals Profiler status endpoint awaits its SQLite queue breakdown in a request worker. A slow status query can still delay that response, while unrelated requests continue to run.

Portfolio, Watchlist, Stock Detail, built-in account pages, intraday chart refresh, Home Assistant account summaries and holding limits, Market Pulse/Markets registry operations, and system/market checks run their synchronous database, file, chart, and Yahoo work in Starlette's request worker pool. A stalled call still delays its own response, but no longer occupies the event loop that must dispatch unrelated requests. Database connections are created and closed inside the same worker invocation; response bodies, authentication, background refresh scheduling, and awaited refresh completion retain their existing contracts. The shared worker pool and Yahoo lock remain finite resources; other upstream waits and expensive queries remain for later steps. The Portfolio page now resolves the selected holdings before querying `stock_signals`, runs the signal/quant/risk enrichment only for those tickers, and keeps the global freshness timestamp from the full table. Position-sizing FX preparation uses only displayed signal rows; the same resolved rate is reused for each holding's market value. Holdings with no signal row retain their existing unavailable display behavior, and the Watchlist query is unchanged.

### 5. Unified Notification Router (`notification_engine.py`)
Every user-facing notification — scheduled-job status (start/success/error) and all alerts — is dispatched through a single function, `notification_engine.notify(source, ...)`. It is the only path that fans an event out to the three delivery channels: the rotating **log file**, the in-app **notification centre** (`system_notifications`), and **Nextcloud Talk**. Which channels a given source uses is read from `NOTIFICATION_ROUTING` in `config.json` (falling back to per-source defaults) and is edited through the **Notification Settings** panel in Settings. Per-job status is attributed automatically: `scheduler_engine` wraps every registered job so the worker thread tags its job id, which `log_sched_notification` resolves into that job's routing. Dedup/cooldown (the `alert_state` ledger) is unchanged — it decides *whether* an alert fires; the router only decides *where it goes*. When an enabled Nextcloud send fails, `notify()` returns `False` so dedup-gated callers can withhold `record_alert_fired` and retry on the next scan. Briefings and the Fear & Greed chart upload file attachments to Talk via their own dispatch path and enable toggles, so they are represented only by their job status row.


### 6. Persisted Model Compatibility

`model_compatibility_engine.py` is the shared persistence boundary for every saved scikit-learn estimator. Training writes the joblib artifact and a sibling `.sklearn-version` file. Loading compares that sidecar with the installed version before unpickling; legacy artifacts without a sidecar are loaded with `InconsistentVersionWarning` promoted to an exception.

After `reload_scheduler()` completes during startup, the compatibility scan probes each persisted model family. A mismatch queues the owning canonical training job. `scheduler_engine.py` pauses later affected jobs and advances the queue from APScheduler completion events, so only one model family retrains at a time. Existing recurring jobs retain their normal schedules; disabled or otherwise absent jobs are added as one-time jobs under their canonical job IDs. Until retraining replaces an incompatible artifact, its loader returns through the feature's existing unavailable-model path.

### Cached Navigation

On Watchlist, the navbar Prices line includes a plain clickable FX timestamp. It shows the oldest successful update among required pairs, or unavailable when a conversion is missing/expired; no required pairs shows “not required.” The centered FX status modal replaces the page-top warnings, lists fresh and stale pairs, and polls cache-only status while open. Force Refresh awaits each pair sequentially, displays the active pair and success/completion counts, continues after failures, and retains last-good cache data. The modal and timestamp update without reloading; reload the page to apply refreshed rates to table/position-sizing values. Closing the modal stops status polling; an explicitly started refresh continues to completion.

Portfolio, Watchlist and Stock Detail request assembly uses cache-only FX and daily-history reads. These reads never wait for Yahoo, its session lock or its rate-limit cooldown. Stale or absent data requests a coordinated background refresh through `cache_refresh_helpers.py`: `request_cache_refresh()` queues fire-and-forget work on two background workers with at most 64 pending background keys, one outstanding refresh per key, and configurable retry suppression after failure. Callers that block on a result (`submit_cache_refresh()`: legacy FX fetches, explicit FX refreshes, missing-history fetches for engines, intraday chart checks) use four separate awaited workers and are never refused by the background cap. If a matching background job is still queued, it is cancelled and run on the awaited workers instead, so a page-triggered backlog cannot delay or fail an awaited caller. Completed futures release their payloads. This pool is separate from the web request pool; it is on-demand work, not a new scheduled job. `scheduler_manifest.JOB_GRAPH["cached_navigation_source"]` exposes the artifact flow in the Workflow Monitor.

Edit the following section in `config.json`; missing entries inherit `config.py:DEFAULT_CONFIG` and readers reload it at runtime. These keys can be exposed by a future Settings → Performance panel without changing their consumers. There is no new Settings panel in this step.

```json
"PERFORMANCE": {
  "FX_FRESH_SECONDS": 600,
  "FX_MAX_USABLE_SECONDS": 604800,
  "CACHE_REFRESH_RETRY_SECONDS": 60
}
```

All values are finite, non-negative seconds, and the maximum usable FX age must be at least the fresh age. An invalid `PERFORMANCE` value prints a warning and resets only the `PERFORMANCE` section to its defaults; the rest of `config.json` is kept. FX age means time since the last successful fetch, stored as a UTC epoch timestamp; a process restart does not reset it. Daily history is stale only when its Parquet was last written before the most recent completed session's close became available, from `time_engine.last_settled_session_close_utc()` (holiday/early-close aware, plus the exchange's `quote_delay_minutes`), so a file is downloaded at most once per completed session and not at all over weekends or holidays.

`yahoo_engine.get_cached_fx_rate(pair)` returns `rate`, `updated_at`, `stale`, and `available`, and can request refresh without awaiting it. Last-successful FX quotes are stored in the existing `market_pulse_cache.fx_rate/fx_updated_at` columns. They survive ordinary pulse updates and failed refreshes. Successful FX price writers update the same snapshot; readers can also use reciprocal quotes. Legacy pulse rows without a successful-FX timestamp remain unavailable until a successful refresh, because ordinary pulse timestamps can advance on failed fetches. `get_fx_rate(pair, force=True)` awaits a coordinated refresh and persists successful results; failures never replace a successful quote. Existing engine calls without cache-only mode retain their awaited fetch behaviour. When that fetch returns nothing, `portfolio_service` uses the persisted last-successful quote while it is within `FX_MAX_USABLE_SECONDS`, and otherwise returns no rate: there is no in-process last-known layer and no 1.0 fallback. A holding whose rate is unavailable is valued like an unpriced holding (at cost basis, which is already in base currency), Home Assistant's `market_price_in_base` is `null`, the FX gain decomposition skips it, and the AI prompt states that the rate is unavailable. `accounts_engine.fx_rate_on_date` accepts a dated daily close at most 3 days old (today or no date uses the bounded cached quote) and otherwise returns None, so transaction entry, CSV import and Auto Top-up confirmation are rejected with a request for a manual rate and the value-history backfill skips that date. The ETF Predictor skips its FX adjustment when its pair is unavailable (`fx_rate` stored as NULL).

Fresh FX is used immediately. Older FX remains usable through the configured maximum, with a displayed last-successful-update time. Missing/too-old FX leaves converted portfolio values, P&L and position sizing unavailable; incomplete portfolio totals are labelled explicitly and client-side live-price updates cannot turn them into partial totals. Native prices and recorded cost information remain available. Page freshness notices require a reload after a successful refresh. In-scope FX pairs are exempt from orphan cleanup, and otherwise usable FX snapshots survive the pulse cache's usual 24-hour cutoff.

`load_or_fetch_daily_history(ticker, cache_only=True)` returns the local file or `None` immediately and requests a refresh when absent, unreadable or missing the latest completed session. Default engine calls continue to await missing-history fetches. All daily history writers (nightly bulk download, single-ticker refresh and navigation refresh) share `data_engine.prepare_daily_history()`: rows without a Close are discarded, zero Open/High/Low values take the Close, an in-progress final bar is trimmed, and saved Repair Data corrections and removals are reapplied before persisting. Refresh writes replace Parquet files atomically, retain last-good files on failure, and exclude synthetic/ignored tickers through the canonical filter. Background file refreshes bypass Yahoo’s transient history cache before completed-bar checks, so a pre-close memory response cannot be stamped as a new post-close file. Completed-bar checks use `time_engine` and `utils.is_daily_bar_still_forming`. Period-return anchors use cache-only mode during navigation. Their derived closes are reused in a bounded 512-ticker process cache keyed by the UTC calculation date and Parquet revision (resolved path, device/inode, size, nanosecond modification/change times). Replacement, in-place updates, deletion/recreation, cache-root changes and date rollover invalidate reuse; missing/unreadable results are not retained. Cache hits still request coordinated stale-history refreshes, and each caller receives an independent anchor dictionary. No DataFrame or holdings/valuation summary is shared across requests. The Stock Detail FX-breakdown fallback also uses cached GBPUSD history rather than fetching inline when its baseline is missing.

Dated FX readers (the Portfolio Optimizer, Strategy Backtester, FX Drag Analyzer and `accounts_engine.fx_rate_on_date`) share one rule in `fx_conversion_helpers`: the latest cached `{ccy}{BASE}=X` daily close on or before the date, at most 3 days old, never a live or 1.0 fallback. `fx_rate_on_date` awaits a missing history and falls back to a 5-year fetch for dates before the 2-year cache; the others are cache-only.

Explicit Stock Detail Refresh and Home Assistant Refresh Now await required FX refresh attempts before reporting success. Every required pair is attempted. An FX failure no longer skips the rest of the refresh: the remaining analysis or performance-cache work completes first, then the existing error response reports the failed pairs and cached data is retained; the HA immediate re-poll contract and all response field names are preserved. Intraday chart polling also retains awaited completion: `load_or_fetch_intraday_history()` reuses fresh shared Parquet, otherwise awaits a coordinated Yahoo request and atomic Parquet replacement. The 300-second window comes from Yahoo's existing five-minute-bar TTL. A successful Yahoo download records `yahoo_fetched_at` in the DataFrame/Parquet metadata, so rewriting a cached frame does not extend its real fetch age; legacy files fall back to their modification time. `get_intraday()` rechecks its cache after acquiring the existing Yahoo session lock and publishes successful results before releasing it, preventing a waiting scanner/chart request from repeating a completed download. Scanners retain their existing fetch, market-window and settlement behavior.

Initial chart HTML carries a source revision from `page_helpers.intraday_chart_revision()`. This incorporates historical/intraday source-file modification time and size, currency and configured display timezone. A freshness check still happens before comparing revisions. Identical revisions skip chart rendering and DOM replacement; successful source changes return new HTML. Failed refreshes keep the last-good file/chart and show an unavailable-update message; they do not advance the revision. Concurrent fetches and overlapping chart renders use the existing shared refresh coordinator. No additional scheduler or cache service is introduced. `cached_navigation_source` includes the intraday artifact in the Workflow Monitor.

Stock Detail and Index Detail keep one load/resume freshness check, with `_intradayBusy` preventing overlap and one visible-tab interval. Hiding the tab clears that browser chart interval; resuming starts one interval and one check, with no missed-tick catch-up burst. This does not control APScheduler, Crash & Moonshot Alerts, Dip Radar, Home Assistant or Market Pulse's price refreshes. The existing stale-price gray/red/green display remains unchanged.
