# 🗺️ System Architecture & Comprehensive Data Flow

This document details the high-priority arbitration logic, dual-storage strategy, and frontend rendering pipeline of the Quantamental Web Terminal.


## 🧠 System-Wide Ingestion & Priority Arbitration (The Brain)

This section details how the platform handles system-wide state when external dependencies fail. It is not a simple linear flow; it maps a nested logical decision grid.

The flow moves strictly from left to right:

### 1. External Data Sources & Inputs
Data streams on the far left show the raw inputs the system relies on. Note the fragility of these connections:
* **Ghostfolio API:** Provides the active Portfolio composition, current shares, and VWAP (Volume Weighted Average Price) sourced from Ghostfolio.
* **Freetrade CSV:** Contains user-specific transaction data.
* **Yahoo Finance (YF API):** The primary source for corporate metadata, fundamental health metrics, and End-of-Day price data.
* **Universe Lists:** Ticker lists defining the available asset universe (e.g., all symbols on the LSE or a specific US Stock Universe).

### 2. The Priority Arbitration Logic (The Decision Grid)
The center of the diagram zooms in on the `System-Wide Ingestion & Priority Arbitration Engine`. Data doesn’t just pass through this engine; it is filtered and prioritized. It visualizes the decision logic I have designed to make your platform self-healing:

#### A. Ghostfolio Logic & Delisting Self-Healing
If the Ghostfolio API flags a stock as delisted or invalid during its nightly sync:
* **Action:** The engine immediately pivots. It bans the ticker string and appends it to `freetrade_blacklist.json`. This self-healing action prevents that ticker from polluting your database during the next `market_universe` update.

#### B. Freetrade Synchronization Check
* **Action:** If the CSV data sync is confirmed, the engine marks the corresponding assets as 'is_freetrade' (setting the flag in SQLite) during the fundamental update.

#### C. Yahoo Finance API Status (Rate Limit Fallback)
This is the most critical logic leg for backend stability:
* **Fail Condition (Rate Limit/Ban):** If the Yahoo Finance API repeatedly fails or rejects connections:
    * **Action:** The engine triggers a total fallback. Instead of crashing, the system pivots and leverages the local **Cache** (the Parquet and JSON fundamentals stored in `data/historical/` and `data/fundamentals/`) rather than trying to make real-time network calls.
* **Success Condition:** If the API responds successfully, the storage files are updated with the new End-of-Day price matrices.

#### D. Universe Arbitration (US/LSE Priorities)
The diagram visualizes how multiple, conflicting lists are resolved:
1.  **Priority 1:** The engine treats your active **Portfolio/Watchlist** (synced from Ghostfolio/CSV) with the highest importance.
2.  **Priority 2:** The engine then merges the **LSE Tickers (UK Universe)** data stream.
3.  **Priority 3:** Finally, it merges the **US Stock Universe**. Any duplicate or delisted assets across these lists are resolved in this priority order.

### 3. Dual-Storage Layer (The Divide)
Once clean, prioritized vectors have been created by the Arbitration Engine, they are split for storage efficiency:

#### A. Relational SQLite (`analysis.db`)
Stored metadata and relational signals map to specific SQLite tables:
* **Tables:** `stock_signals`, `freetrade_blacklist`, `market_universe`, `asset_profiles`.
* **Content:** P/E ratios, System Verdict scores (0-100), sector classifications, industry descriptions.

#### B. Time-Series Parquet & JSON (Compressed Offload)
Massive raw data matrices are directed here:
* **Folder Structure:** `data/historical/*.parquet` and `data/fundamentals/*.json`.
* **Content:** 2 years of daily OHLCV (Open, High, Low, Close, Volume) data and raw `.info` JSON dumps.

### 4. Frontend Render Layer ( JINJA2 / JS / Charting)
Finally, the schematic maps how the Web Terminal combines both data streams to render the UI you use:

* **QUANTAmental Web UI (Jinja2):** The FastAPI backend serves the main HTML templates.
* **Relational DataTables (AJAX):** JavaScript utilizes `DataTables.jsdeferRender` with AJAX to query massive amounts of structured data **strictly from SQLite**.
* **Interactive Charts (Plotly.js):** JavaScript utilizes Plotly.js to render interactive price charts by streaming massive datasets **strictly from the Parquet files**.
* **Nextcloud talk (Overnight Alerts):** Alerts are pushed directly to Nextcloud Talk for your morning market briefing.

### 5. Unified Notification Router (`notification_engine.py`)
Every user-facing notification — scheduled-job status (start/success/error) and all alerts — is dispatched through a single function, `notification_engine.notify(source, ...)`. It is the only path that fans an event out to the three delivery channels: the rotating **log file**, the in-app **notification centre** (`system_notifications`), and **Nextcloud Talk**. Which channels a given source uses is read from `NOTIFICATION_ROUTING` in `config.json` (falling back to per-source defaults) and is edited through the **Notification Settings** panel in Settings. Per-job status is attributed automatically: `scheduler_engine` wraps every registered job so the worker thread tags its job id, which `log_sched_notification` resolves into that job's routing. Dedup/cooldown (the `alert_state` ledger) is unchanged — it decides *whether* an alert fires; the router only decides *where it goes*. When an enabled Nextcloud send fails, `notify()` returns `False` so dedup-gated callers can withhold `record_alert_fired` and retry on the next scan. Briefings and the Fear & Greed chart upload file attachments to Talk via their own dispatch path and enable toggles, so they are represented only by their job status row.