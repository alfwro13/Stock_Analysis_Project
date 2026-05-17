# 🌐 Freetrade Investment Universe Ingestion & Filtering Engine

The Quantamental Web Terminal features an automated, configuration-driven Ingestion, Normalization, and Filtering Engine designed to synchronize and scrub local market intelligence against the official Freetrade Investment Universe catalog.

Because Freetrade restricts retail accounts to a curated universe of global assets, this integration prevents processing overhead, eliminates invalid tracking tokens, and ensures that automated background screeners, quantitative technical models, and options sandboxes only evaluate assets that are actively tradable on your broker account.

---

## 🧠 1. The Multi-Tiered Ingestion Pipeline

Integrating Freetrade requires translating proprietary, internal broker symbol formats into standardized Yahoo Finance tickers. The system executes this through an optimized, case-sensitive multi-tiered resolution architecture:

```
[Freetrade Live Google Sheet URL] 
               │
               ▼
   [Download Raw Ingestion DF]
               │
               ▼
         [US Fast-Path Filter] ───► (Matches XNAS, XNYS, ARCX, BATS, PINK) ───► Pass Raw Symbol
               │
               ▼
 [Case-Sensitive Character Strip] ───► Slices exchange tracking chars BEFORE string upper-casing
               │
               ▼
 [ISIN Yahoo API Cross-Match] ───► (Fallback check via Query2 API search)
               │
               ▼
 [Dynamic Blacklist Validation] ───► Drops assets stored in data/freetrade_blacklist.json
               │
               ▼
[Atomic Purge & Bulk SQLite Ingest] ───► Populates market_universe table
```

### Ingestion Stages Deep-Dive
1. **Stream Extraction:** The engine downloads the official, live Freetrade asset list directly from their published Google Sheets server via a structured CSV stream.
2. **US Fast-Path密 Bypass:** US securities traded on major exchanges (`XNAS`, `XNYS`, `ARCX`, `BATS`) as well as `PINK` sheets do not suffer from internal naming layout variances. The engine immediately approves these symbols, maps their internal dots (`.`) to hyphens (`-`), and passes them directly to the database layer, saving thousands of unnecessary HTTP network requests.
3. **Case-Sensitive Suffix Scrubbing:** Freetrade appends unique tracking characters to European tickers to prevent namespace collision across distinct geographic regions. The engine isolates and slices these characters *prior* to string upper-casing to prevent false positives on native symbols (e.g., protecting Stockholm's `WALLB` while correctly parsing Brussels' `CFEBb`).
4. **ISIN Search Resolution:** If an international ticker cannot be resolved natively via fallback suffix concatenation, the engine extracts the asset's globally unique International Securities Identification Number (ISIN) and queries Yahoo Finance's internal search API to capture the exact localized ticker.

---

## ⚙️ 2. Centralized Configuration Mappings (`config.py`)

All asset routing rules, tracking characters, and Yahoo Finance extensions have been completely decoupled from the core application logic and central software layers. They reside natively in `config.py` under the `FREETRADE_MAPPINGS` schema. When the application initializes, these entries are automatically compiled into your root `config.json` file for effortless custom modification.

### Ingestion Map Schema
```json
"FREETRADE_MAPPINGS": {
    "US_MICS": ["XNAS", "XNYS", "ARCX", "BATS", "PINK"],
    "EXCHANGES": {
        "XLON": {"yf_suffix": ".L", "ft_char": "", "ui_name": "LSE"},
        "XFRA": {"yf_suffix": ".DE", "ft_char": "d", "ui_name": "Frankfurt"},
        "XETR": {"yf_suffix": ".DE", "ft_char": "d", "ui_name": "XETRA"},
        "XPAR": {"yf_suffix": ".PA", "ft_char": "p", "ui_name": "Paris"},
        "XAMS": {"yf_suffix": ".AS", "ft_char": "a", "ui_name": "Amsterdam"},
        "XBRU": {"yf_suffix": ".BR", "ft_char": "b", "ui_name": "Brussels"},
        "XDUB": {"yf_suffix": ".IR", "ft_char": "i", "ui_name": "Dublin"},
        "XMAD": {"yf_suffix": ".MC", "ft_char": "e", "ui_name": "Madrid"},
        "XMIL": {"yf_suffix": ".MI", "ft_char": "m", "ui_name": "Milan"},
        "XLIS": {"yf_suffix": ".LS", "ft_char": "u", "ui_name": "Lisbon"},
        "XHEL": {"yf_suffix": ".HE", "ft_char": "h", "ui_name": "Helsinki"},
        "XSTO": {"yf_suffix": ".ST", "ft_char": "", "ui_name": "Stockholm"},
        "XOSL": {"yf_suffix": ".OL", "ft_char": "o", "ui_name": "Oslo"},
        "XCSE": {"yf_suffix": ".CO", "ft_char": "c", "ui_name": "Copenhagen"},
        "XVIE": {"yf_suffix": ".VI", "ft_char": "v", "ui_name": "Vienna"},
        "XSWX": {"yf_suffix": ".SW", "ft_char": "z", "ui_name": "Swiss"},
        "XWBO": {"yf_suffix": ".VI", "ft_char": "v", "ui_name": "Vienna"},
        "MTAA": {"yf_suffix": ".XC", "ft_char": "m", "ui_name": "Borsa Italiana"},
        "MUTUAL_FUND_EXCHANGE": {"yf_suffix": ".L", "ft_char": "", "ui_name": "UK Mutual Fund"}
    }
}
```

### Strict Exchange Circuit Breaker Rule
If Freetrade rolls out a new asset class or introduces a new country Market Identifier Code (MIC) that is completely missing from this configuration schema, the engine will **activate a system safety circuit breaker**. It will completely skip processing the unmapped records to protect your local database from corruption, and log a critical warning badge to your system Notification Center containing the missing MIC codes.

---

## 🛡️ 3. The Automated Blacklist Architecture

Data quality variations across global brokers mean that certain niche international assets or local ETFs listed in the Freetrade catalog may not possess public tracking coverage on Yahoo Finance, resulting in repetitive downstream `HTTP 404 Not Found` errors. 

To eliminate data dead-weight and prevent loop locking, the platform runs a self-healing, automated **Blacklist Architecture** linking the `profile_engine.py` and `freetrade_engine.py` modules:

```
[profile_engine.py] ───► Hits yfinance 404 Error 
                               │
                               ▼
            [Append Ticker to data/freetrade_blacklist.json]
                               │
                               ▼
            [Execute Broad SQL Cascade Purge on analysis.db]
                               │
                               ▼
[freetrade_engine.py] ───► Next Sync reads JSON ───► Filters out matching rows
```

### The 404 Ingestion Loop Breaker
When `profile_engine.py` executes its rolling audit metadata harvest and hits a `404 Not Found` payload from the upstream tracker API, it intercepts the exception and fires an immediate system-wide cleaning routine:

1. **Blacklist Registration:** The malformed or untracked ticker is instantly appended to `data/freetrade_blacklist.json`.
2. **Database Cascade Purge:** To remove the corrupted rows that slow down processing, the engine runs a broad SQL purge, deleting that specific ticker across all master relational database tables:
   ```sql
   DELETE FROM market_universe WHERE ticker = ?;
   DELETE FROM asset_profiles WHERE ticker = ?;
   DELETE FROM stock_signals WHERE ticker = ?;
   DELETE FROM quant_signals WHERE ticker = ?;
   ```
3. **Upstream Ingestion Veto:** The next time `freetrade_engine.py` runs a background sync, it loads `data/freetrade_blacklist.json` directly into a high-speed memory `set()`. As it iterates through the broker rows, any resolved ticker matching the blacklist is **vetoed and discarded instantly**, ensuring it can never pollute your local system data layers again.

---

## 💻 4. Command Line Diagnostics & Isolated Testing

The Freetrade Sync Engine includes an advanced Command Line Interface (CLI) configuration. This allows system administrators to test symbol resolution mappings, trace case transformations, and audit specific global exchanges line-by-line without purging operational database tables.

### CLI Parameter Options
* `--mic`: Explicitly limits ingestion parsing to a single 4-letter Market Identifier Code (e.g., `XPAR`, `XBRU`, `XNYS`).
* `--limit`: Caps the total row count to an exact number for rapid execution and manual trace analysis.

### Useful Diagnostic Commands

* **Audit Paris Ingestion (Casing & Suffix Verification):**
  ```bash
  python freetrade_engine.py --mic XPAR --limit 10
  ```
* **Verify Brussels Extraction Layers:**
  ```bash
  python freetrade_engine.py --mic XBRU --limit 5
  ```
* **Check US Equities Parsing Pipeline:**
  ```bash
  python freetrade_engine.py --mic XNAS --limit 20
  ```

When running in diagnostic mode via these switches, the engine automatically prints high-resolution, string-level evaluation traces directly to your shell window:
```text
2026-05-17 11:05:15,840 - FREETRADE_ENGINE - INFO - TEST: Original: 'CFEBb' (ISIN: BE0003883031) -> Resolved: 'CFEB.BR'
2026-05-17 11:05:16,210 - FREETRADE_ENGINE - INFO - TEST: Original: 'ECONBb' (ISIN: BE0974311413) -> Resolved: 'ECONB.BR'
```
*Note: Using `--mic` or `--limit` flags automatically shifts the engine into Safe Ingestion Mode. The bulk database purge (`DELETE FROM market_universe WHERE is_freetrade = 1`) is bypassed, and rows are cleanly updated/inserted individually.*

---

## 📊 5. Web UI Integration & Preferences

Once integrated and compiled, the Freetrade relational tracking records are utilized across the entire web interface application layer to optimize layout visibility:

### The Global Filter Toggles
Under the **⚙️ Settings** tab, enabling the `FREETRADE_ONLY_MODE` setting injects a master constraint across your local screeners and tools. Any asset existing in your offline database that does not have an active matching `is_freetrade = 1` flag inside the `market_universe` index table is stripped out of sight. This keeps your interface clean and focused entirely on assets you can realistically buy.

### Watchlist Compatibility Badging
If you disable `FREETRADE_ONLY_MODE` but retain Freetrade indicators, the dashboard actively double-checks your tracking boundaries. If you manually track an exotic international stock or complex option derivative via your Ghostfolio account that Freetrade's order routing system cannot trade, the **👀 Watchlist** page flags the row with a clear, warning-red **"Not on Freetrade"** badge directly below the company asset name.

### Funds vs. Equities Segmenting
Freetrade includes hundreds of diversified instruments. The **🌐 Market Screener** page utilizes the database `quote_type` fields to provide an explicit interface layout separation. Clicking **"View Funds & ETFs"** dynamically re-renders your screeners to display asset expense ratios and attaches a live link routing directly to Freetrade's official Key Investor Information Documents (KIIDs) for immediate fundamental research.

---

## 🔄 6. The Recovery and Execution Sequence

To completely purge legacy data corruption, re-index your broker catalog, and run the automated blacklist profiler end-to-end, execute this sequence inside your virtual environment:

1. **Sanitize the Database Layout:** Run the broad database cleaner to strip historical orphans:
   ```bash
   python clean_db.py
   ```
2. **Execute Ingestion Inbound Processing:** Run the master Freetrade sync using the configuration schema:
   ```bash
   python freetrade_engine.py
   ```
3. **Execute the Rolling Profile Audit:** Re-run the profile audit script. The system will harvest valid metadata entries seamlessly, and automatically drop any remaining 404-prone assets straight into the blacklist file:
   ```bash
   python profile_engine.py
   ```