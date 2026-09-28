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

### 5. Unified Notification Router (`notification_engine.py`)
Every user-facing notification — scheduled-job status (start/success/error) and all alerts — is dispatched through a single function, `notification_engine.notify(source, ...)`. It is the only path that fans an event out to the three delivery channels: the rotating **log file**, the in-app **notification centre** (`system_notifications`), and **Nextcloud Talk**. Which channels a given source uses is read from `NOTIFICATION_ROUTING` in `config.json` (falling back to per-source defaults) and is edited through the **Notification Settings** panel in Settings. Per-job status is attributed automatically: `scheduler_engine` wraps every registered job so the worker thread tags its job id, which `log_sched_notification` resolves into that job's routing. Dedup/cooldown (the `alert_state` ledger) is unchanged — it decides *whether* an alert fires; the router only decides *where it goes*. When an enabled Nextcloud send fails, `notify()` returns `False` so dedup-gated callers can withhold `record_alert_fired` and retry on the next scan. Briefings and the Fear & Greed chart upload file attachments to Talk via their own dispatch path and enable toggles, so they are represented only by their job status row.
