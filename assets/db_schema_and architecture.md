# 🗄️ Database Schema & Data Architecture

The Quantamental Dashboard employs a **Dual-Storage Architecture** to guarantee high performance, bypass memory bottlenecks, and prevent API rate-limit bans. 

Structured metadata, fundamental metrics, and system scoring are routed to a relational **SQLite database**, while massive time-series matrices are routed to highly compressed **Parquet files** and flat JSONs.

---

## 1. The Relational Database (`data/analysis.db`)

The SQLite database acts as the central brain of the dashboard. It uses a star-like schema where most tables join on the primary key `ticker`.

### Core Tables & Entities

#### `market_universe`
* **Purpose:** The master tracker of all ~4,000+ available equities, ETFs, and Mutual Funds.
* **Key Columns:** `ticker` (PK), `company_name`, `sector`, `exchange`, `is_freetrade`.
* **Usage:** Powers the "Market Screener" UI and acts as the gatekeeper for what assets the system is allowed to scan.

#### `asset_profiles`
* **Purpose:** Static, slow-moving corporate metadata. Stored separately to prevent querying Yahoo Finance for static data (like business summaries) every single night.
* **Key Columns:** `ticker` (PK), `industry`, `country`, `quote_type`, `business_summary`.
* **Usage:** Provides the descriptive text and macro classifications for the Stock Details page.

#### `stock_signals`
* **Purpose:** The primary aggregation table for the Portfolio & Watchlist UI. It houses the final System Verdicts and Fundamental Health snapshots.
* **Key Columns:** `ticker` (PK), `current_price`, `trend_200d`, `atr_stop_loss`, `trailing_pe`, `debt_to_equity`, `composite_score`, `overall_signal`, `setup_tags`.
* **Usage:** Drives the main data tables on the dashboard. Overwritten daily by `quant_engine.py`.

#### `quant_signals`
* **Purpose:** Stores a daily historical log of technical and quantitative metrics for machine learning and mean-reversion analysis.
* **Key Columns:** `ticker`, `date` (Composite PK), `rsi_14`, `macd_hist`, `sma_200`, `volume_surge`, `ml_confidence_score`, `var_95`, `sentiment_score`.
* **Usage:** Powers the Overnight Quant Screener (Markdown briefing) and the Machine Learning backend.

#### `earnings_volatility`
* **Purpose:** The options arbitrage ledger.
* **Key Columns:** `ticker` (PK), `next_earnings_date`, `implied_move_pct`, `historical_avg_move_pct`, `edge_score`.
* **Usage:** Feeds the Earnings Volatility page.

### System & Macro Tables

#### `market_regimes` & `macro_regimes`
* **Purpose:** Tracks systemic volatility and the global cost of capital.
* **Key Columns:** `date` (PK), `vix_close`, `spy_volatility`, `turbulence_index`, `yield_velocity`, `systemic_threat_level`.
* **Usage:** Contextualizes the Market Sentiment page and applies "Vetoes" to the overnight screener if the market is crashing.

#### `system_notifications` & `market_pulse_cache`
* **Purpose:** Low-latency state management. `system_notifications` logs alerts to prevent spamming Nextcloud. `market_pulse_cache` holds the live 1-minute price tickers to render the UI instantly without waiting on network I/O.

---

## 2. Local File Storage (`data/` Directory)

Why not put everything in SQLite? Storing 2 years of daily prices for 4,000 stocks in a SQL table creates millions of rows, slowing down basic queries. We offload heavy math to file storage.

### Data Directories

* 📁 **`data/historical/` (`.parquet`)**
    * **Contents:** 2 years of daily OHLCV (Open, High, Low, Close, Volume) data for every tracked asset. Also contains macro baselines (`SP500_BASELINE.parquet`, `UK_GILT_BASELINE.parquet`).
    * **Usage:** Used by `ta` (Technical Analysis library) to calculate moving averages and by `plotly` to render the massive 5-row interactive charts.
* 📁 **`data/intraday/` (`.parquet`)**
    * **Contents:** 1 day of 5-minute interval OHLCV data.
    * **Usage:** Used by the `intraday_orchestrator` to detect flash crashes and to render the live pulse charts.
* 📁 **`data/fundamentals/` (`.json`)**
    * **Contents:** The raw, unadulterated `.info` dictionary dump directly from Yahoo Finance.
    * **Usage:** Acts as a local backup cache. If the YF API goes down, the system can still rebuild its `stock_signals` table from this raw JSON cache.

### Core Configuration Files

* 📄 **`data/portfolio.json`**
    * **Purpose:** Contains the exact positions, shares, and VWAP (Volume Weighted Average Price) synced from Ghostfolio. It aggregates macro-holdings but also stores the micro-account ledgers.
* 📄 **`data/watchlist.json`**
    * **Purpose:** A flat list of ticker strings defining the user's active watchlist.
* 📄 **`data/freetrade_blacklist.json`**
    * **Purpose:** A self-healing ledger. If an asset repeatedly crashes the data pipeline (e.g., a delisted Mutual Fund), it is appended here and permanently banned from entering the database.
* 📄 **`data/isin_ticker_cache.json`**
    * **Purpose:** A lookup dictionary mapping European ISINs (e.g., GB00BP9QDL57) to Yahoo Finance symbols (e.g., 0P0001SMTP.L) to drastically speed up Freetrade ingestion.