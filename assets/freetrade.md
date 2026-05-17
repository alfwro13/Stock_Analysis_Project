# Freetrade Universe Integration

The Quantamental Dashboard includes a fully automated, dedicated engine to synchronize and filter your market data against the official Freetrade Investment Universe. 

Because Freetrade restricts retail investors to a curated list of ~8,000 global assets, this integration allows you to hide unsupported stocks, ensuring that your screener only surfaces actionable, tradable setups.

---

## 🧠 How the Engine Works (Under the Hood)

Integrating Freetrade requires translating their proprietary, internal ticker formats into the globally standardized formats required by Yahoo Finance. The `freetrade_engine.py` handles this automatically using a multi-tiered resolution architecture:

1. **CSV Extraction:** The engine downloads the official, live Freetrade asset list directly from their published Google Sheet via a fast CSV export.
2. **US Fast-Path:** US stocks (Nasdaq, NYSE, BATS) rarely have ticker discrepancies. The engine instantly approves these, saving thousands of unnecessary API calls.
3. **The ISIN Resolution Engine:** European and UK stocks often suffer from ticker mismatches (e.g., Freetrade uses AALBA, but Yahoo expects AALB.AS). To fix this, the engine extracts the asset's globally unique **ISIN**, securely queries Yahoo Finance's internal search API, and retrieves the exact, flawlessly formatted ticker.
4. **Local Caching:** To prevent Yahoo Finance from permanently banning your server IP, resolved ISINs are saved locally to `data/isin_ticker_cache.json`. The first run takes ~1-2 hours, but subsequent syncs take only a couple of seconds.

---

## 🚀 First-Time Initialization (Cold Start)

When you sync the universe for the very first time, the assets are downloaded but remain "dormant." The system knows their names, but it doesn't yet know their technicals, prices, or whether they are Equities vs. Funds. 

You must perform a strict 3-step initialization:

### Step 1: The Initial Sync & ISIN Resolution
1. Open the **⚙️ Settings** tab in the web dashboard.
2. Open the **Freetrade Universe Integration** card.
3. Click **⬇️ Sync Freetrade Universe**.
4. **Wait patiently.** Because the engine is querying Yahoo Finance for ~2,500 European assets while applying randomized sleep throttles to protect your IP, this first run will take **15 to 20 minutes**. You can watch the real-time progress in your terminal logs or via the UI Notification bell.

### Step 2: The Profile Audit (Terminal)
Once the sync finishes, the system needs to fetch the static metadata (Sector, Industry, Quote Type) for these 8,000 assets.
1. Open your Linux terminal and navigate to your `Stock_Analysis_Project` folder.
2. Run the profile engine manually: `python profile_engine.py`
3. *Note:* To prevent IP bans, the profile engine is strictly hardcoded to process a maximum of 5,000 assets per run. Since the Freetrade universe is ~8,000 assets, **you must run this command twice**. (Wait for the first run to finish, then run it again).

### Step 3: The Initial Quant Scan
Now that the system knows what the assets are, it needs to calculate their momentum, RSI, MACD, and VaR.
1. Go back to the **⚙️ Settings** tab in the web UI.
2. Scroll down to **🔬 Quantitative Engines (Background Jobs)**.
3. Under the *Market Universe (Weekend Routine)* section, click **▶️ Run Full Quant Scan**.
4. This massive mathematical crunch will take over an hour. Once complete, your Market Screener will be fully populated.

---

## ⚙️ Settings & Configuration

All Freetrade configurations are housed in a centralized hub at the top of the **Settings** page.

* **Freetrade Only Mode:** A global toggle. When enabled, any asset in the master database that is *not* supported by Freetrade will be entirely hidden from the Market Screener.
* **Manual Data Import:** A manual trigger to immediately pull the latest CSV from Freetrade's servers and update the local database.
* **Automated Sync Scheduler:** Configures APScheduler to run the sync automatically in the background. It is highly recommended to set this to run on **weekends** (e.g., Saturday at 03:00) so it does not compete with active trading data.

---

## 📊 UI Features & Filtering

Once integrated and audited, the Freetrade data actively enhances your dashboard UI:

### The Market Screener
* **Equities vs. Funds Toggle:** Freetrade includes hundreds of ETFs and Mutual Funds. A dedicated toggle at the top of the Market Screener allows you to instantly switch between analyzing standard stock equities and analyzing funds.
* **Freetrade Subtitle:** The screener table includes the official Freetrade "Subtitle" (a 2-3 word summary of the business or fund objective).
* **KIID Documents:** When viewing the "Funds & ETFs" table, a dedicated column provides direct, clickable links to the official KIID (Key Investor Information Document) provided by Freetrade.

### The Watchlist
* **Compatibility Badging:** If you have `FREETRADE_ONLY_MODE` enabled, but you manually add a non-Freetrade stock to your Ghostfolio Watchlist (e.g., via the Options Sandbox), the Watchlist UI will flag it with a prominent red **"Not on Freetrade"** badge directly beneath the company name.
