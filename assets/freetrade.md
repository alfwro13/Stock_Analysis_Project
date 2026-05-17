# **🌐 Freetrade Investment Universe Ingestion & Filtering Engine**

The Quantamental Web Terminal features an automated, configuration-driven Ingestion, Normalization, and Filtering Engine designed to synchronize and scrub local market intelligence against the official Freetrade Investment Universe catalog.

Because Freetrade restricts retail accounts to a curated universe of global assets, this integration prevents processing overhead, eliminates invalid tracking tokens, and ensures that automated background screeners, quantitative technical models, and options sandboxes only evaluate assets that are actively tradable on your broker account.

## **🧠 1\. The Multi-Tiered Ingestion Pipeline**

To maximize API efficiency and prevent downstream 404 errors, the engine has been optimized to exclusively process **US Equities, UK Equities (LSE), and UK Mutual Funds**, purposefully discarding unreliable European composite tickers.

The system executes this through a streamlined resolution architecture:

\[Freetrade Live Google Sheet URL\]   
               │  
               ▼  
   \[Download Raw Ingestion DF\]  
               │  
               ▼  
         \[US Fast-Path Filter\] ───► (Matches XNAS, XNYS, ARCX, BATS, PINK) ───► Pass Raw Symbol  
               │  
               ▼  
 \[Mutual Fund ISIN Resolution\] ───► Translates ISINs to Yahoo identifiers via internal Search API  
               │  
               ▼  
      \[UK Equities Mapping\]    ───► Appends standardized .L suffix to XLON tickers  
               │  
               ▼  
  \[Strict Exchange Rejection\]  ───► Actively discards EU markets (XFRA, XETR, XPAR, etc.)  
               │  
               ▼  
 \[Dynamic Blacklist Validation\] ───► Drops assets stored in data/freetrade\_blacklist.json  
               │  
               ▼  
\[Atomic Purge & Bulk SQLite Ingest\] ───► Populates market\_universe table

### **Ingestion Stages Deep-Dive**

1. **Stream Extraction:** The engine downloads the official, live Freetrade asset list directly from their published Google Sheets server via a structured CSV stream.  
2. **US Fast-Path Bypass:** US securities traded on major exchanges (XNAS, XNYS, ARCX, BATS) as well as PINK sheets do not suffer from internal naming layout variances. The engine immediately approves these symbols, maps their internal dots (.) to hyphens (-), and passes them directly to the database layer.  
3. **Mutual Fund ISIN Resolution:** Because UK Mutual Funds lack standardized ticker codes on external financial platforms, the engine extracts the asset's globally unique International Securities Identification Number (ISIN) and queries Yahoo Finance's internal search API to capture its proprietary local identifier (e.g., translating GB00BP9QDL57 to 0P0001SMTP.L).  
4. **UK Equities Standardization:** Standard London Stock Exchange equities (XLON) simply have the standard Yahoo Finance .L suffix appended.  
5. **Strict Market Rejection:** To protect the database from low-quality, untracked, or highly fragmented European ticker data, the engine actively ignores all Market Identifier Codes (MICs) not explicitly defined in the configuration schema, quietly dropping thousands of messy records from the pipeline.

## **⚙️ 2\. Centralized Configuration Mappings (config.py)**

All asset routing rules, tracking formats, and allowed exchanges reside natively in config.py under the FREETRADE\_MAPPINGS schema. By keeping this dictionary intentionally small, we act as a rigid gatekeeper for data quality.

### **Ingestion Map Schema**

"FREETRADE\_MAPPINGS": {  
    "US\_MICS": \["XNAS", "XNYS", "ARCX", "BATS", "PINK"\],  
    "EXCHANGES": {  
        "XLON": {"yf\_suffix": ".L", "ui\_name": "LSE"},  
        "MUTUAL\_FUND\_EXCHANGE": {"yf\_suffix": ".L", "ui\_name": "UK Mutual Fund"}  
    }  
}

### **Strict Exchange Circuit Breaker Rule**

If a record flows in with a MIC that isn't listed in US\_MICS or EXCHANGES (such as XPAR, XETR, or XSTO), the resolve\_ticker function will return False and the engine will skip the row. This safely ignores European exotics without throwing critical system errors.

## **🛡️ 3\. The Automated Blacklist Architecture**

Even within the standard US and UK markets, some newly listed assets or niche funds might not immediately populate on Yahoo Finance.

To eliminate data dead-weight and prevent loop locking, the platform runs a self-healing, automated **Blacklist Architecture** linking the profile\_engine.py and freetrade\_engine.py modules:

\[profile\_engine.py\] ───► Hits Empty Identity Payload   
                               │  
                               ▼  
            \[Append Ticker to data/freetrade\_blacklist.json\]  
                               │  
                               ▼  
            \[Execute Broad SQL Cascade Purge on analysis.db\]  
                               │  
                               ▼  
\[freetrade\_engine.py\] ───► Next Sync reads JSON ───► Filters out matching rows

### **The Ingestion Loop Breaker**

When profile\_engine.py executes its rolling audit metadata harvest, it evaluates the upstream payload. The engine utilizes a **softened verification check** designed specifically to accommodate Mutual Funds (which often return extremely sparse metadata). However, if an asset returns absolutely no recognizable tracking identity (shortName, longName, or symbol), the system intercepts the exception and fires an immediate system-wide cleaning routine:

1. **Blacklist Registration:** The malformed or untracked ticker is instantly appended to data/freetrade\_blacklist.json.  
2. **Database Cascade Purge:** To remove the corrupted rows that slow down processing, the engine runs a broad SQL purge, deleting that specific ticker across all master relational database tables:  
   DELETE FROM market\_universe WHERE ticker \= ?;  
   DELETE FROM asset\_profiles WHERE ticker \= ?;  
   DELETE FROM stock\_signals WHERE ticker \= ?;  
   DELETE FROM quant\_signals WHERE ticker \= ?;

3. **Upstream Ingestion Veto:** The next time freetrade\_engine.py runs a background sync, it loads data/freetrade\_blacklist.json directly into a high-speed memory set(). As it iterates through the broker rows, any resolved ticker matching the blacklist is **vetoed and discarded instantly**, ensuring it can never pollute your local system data layers again.

## **💻 4\. Command Line Diagnostics & Isolated Testing**

The Freetrade Sync Engine includes an advanced Command Line Interface (CLI) configuration. This allows system administrators to test symbol resolution mappings and audit specific exchanges line-by-line without purging operational database tables.

### **CLI Parameter Options**

* \--mic: Explicitly limits ingestion parsing to a single Market Identifier Code (e.g., XLON, XNAS).  
* \--limit: Caps the total row count to an exact number for rapid execution and manual trace analysis.

### **Useful Diagnostic Commands**

* **Audit UK Equities Ingestion:**  
  python freetrade\_engine.py \--mic XLON \--limit 10

* **Verify Mutual Fund ISIN Extraction:**  
  python freetrade\_engine.py \--mic MUTUAL\_FUND\_EXCHANGE \--limit 5

* **Check US Equities Parsing Pipeline:**  
  python freetrade\_engine.py \--mic XNAS \--limit 20

*Note: Using \--mic or \--limit flags automatically shifts the engine into Safe Ingestion Mode. The bulk database purge (DELETE FROM market\_universe WHERE is\_freetrade \= 1\) is bypassed, and rows are cleanly updated/inserted individually.*

## **📊 5\. Web UI Integration & Preferences**

Once integrated and compiled, the Freetrade relational tracking records are utilized across the entire web interface application layer to optimize layout visibility:

### **The Global Filter Toggles**

Under the **⚙️ Settings** tab, enabling the FREETRADE\_ONLY\_MODE setting injects a master constraint across your local screeners and tools. Any asset existing in your offline database that does not have an active matching is\_freetrade \= 1 flag inside the market\_universe index table is stripped out of sight. This keeps your interface clean and focused entirely on assets you can realistically buy.

### **Watchlist Compatibility Badging**

If you disable FREETRADE\_ONLY\_MODE but retain Freetrade indicators, the dashboard actively double-checks your tracking boundaries. If you manually track an exotic stock or complex option derivative via your Ghostfolio account that Freetrade's order routing system cannot trade, the **👀 Watchlist** page flags the row with a clear, warning-red **"Not on Freetrade"** badge directly below the company asset name.

### **Funds vs. Equities Segmenting**

Freetrade includes hundreds of diversified instruments. The **🌐 Market Screener** page utilizes the database quote\_type fields to provide an explicit interface layout separation. Clicking **"View Funds & ETFs"** dynamically re-renders your screeners to display asset expense ratios and attaches a live link routing directly to Freetrade's official Key Investor Information Documents (KIIDs) for immediate fundamental research.

## **🔄 6\. The Recovery and Execution Sequence**

To completely purge legacy data corruption, re-index your broker catalog, and run the automated blacklist profiler end-to-end, execute this sequence inside your virtual environment:

1. **Sanitize the Database Layout:** Run the broad database cleaner to strip historical orphans:  
   python clean\_db.py

2. **Execute Ingestion Inbound Processing:** Run the master Freetrade sync using the configuration schema:  
   python freetrade\_engine.py

3. **Execute the Rolling Profile Audit:** Re-run the profile audit script. The system will harvest valid metadata entries seamlessly, and automatically drop any remaining missing-payload assets straight into the blacklist file:  
   python profile\_engine.py  
