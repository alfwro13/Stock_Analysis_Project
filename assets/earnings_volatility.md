# 📅 Earnings Volatility & Options Arbitrage

The Quantamental Dashboard includes a dedicated engine (`earnings_vol_engine.py`) designed to identify statistical mispricings in the options market leading up to major corporate earnings reports. 

This feature powers the **Earnings Volatility** page on your dashboard, identifying where Wall Street is mathematically underestimating (or overestimating) a stock's post-earnings price movement.

---

### 🧠 1. What Is It?

When a company releases earnings, the stock usually gaps up or down. Options traders try to predict the size of this gap, which inflates the price of options (Implied Volatility). 

The engine acts as an arbitrage scanner. It compares **what the options market expects to happen** against **what has historically actually happened**. If the options market is pricing in a 5% move, but the stock historically moves 10% on earnings, the options are mathematically "underpriced"—presenting a statistical edge.

### ⚙️ 2. What It Does & What It Says

The engine first checks each tracked asset's `next_earnings_date` in `stock_signals` — already refreshed nightly by the Quant Scan from the same Yahoo Finance field this engine used to fetch live — to see whether earnings fall within the next 14 days. This is a local database read, not a Yahoo Finance call, so tickers with no near-term earnings cost nothing here. Only for assets that pass this filter does the engine make any live Yahoo Finance calls, to price the actual options edge:

* **Implied Move (The Market's Expectation):** The engine finds the closest At-The-Money (ATM) Call and Put options expiring immediately after the earnings date. It adds their prices together (an ATM Straddle) and divides by the current stock price. *Example: "The options market is pricing in a 6.50% move."*
* **Historical Avg Move (The Reality):** The engine fetches the exact dates of the last 4 quarterly earnings reports, looks at the historical daily chart, and calculates the average absolute percentage move over a 2-trading-day window (the close before the report to the close after). This captures the full reaction regardless of AMC/BMO timing. *Example: "Historically, this stock moves an average of 9.20% across its 2-day earnings window."*
* **Edge Score:** The mathematical difference between the Historical Move and the Implied Move. 

**How to Interpret the Edge Score:**
* **Positive Edge (Green):** Options are *underpriced*. The stock historically moves more violently than the options currently predict. Buying options (like a Straddle or Strangle) here has a statistical edge.
* **Negative Edge (Red):** Options are *overpriced*. The options market is expecting a massive move, but historically the stock barely moves. This is a prime setup for an "IV Crush." Selling options (like an Iron Condor or Credit Spread) here has a statistical edge.
* **Options Volume:** The combined open interest/volume of the ATM Call and Put. If this number is very low (e.g., < 100), the Edge Score may be a false positive due to wide, illiquid bid/ask spreads.

### 🎯 3. Scope & Limitations (Why Not The Entire Universe?)

This engine **only** scans assets present in your **Portfolio** and **Watchlist**. It does not scan the massive 4,000+ Market Screener universe.

**Why?**
1. **Extreme API Load:** For each stock that has earnings coming up, the engine must make multiple sequential queries: fetching the options chain, fetching the historical earnings calendar, and fetching multiple historical price slices to measure past gaps. Running this 4,000 times would result in a permanent IP ban from our data providers.
2. **Options Liquidity:** The vast majority of small-cap and mid-cap stocks in the broader market universe have highly illiquid options chains with massive bid/ask spreads. Math derived from illiquid options is inherently flawed. By restricting the scan to your curated Portfolio/Watchlist, we ensure the engine focuses on high-quality, liquid assets.

---

### 🚀 4. How to Run & Populate the Data

Because options prices change rapidly, this engine runs independently of the daily quant screener. 

#### Option A: Run via the Web GUI (Recommended)
If you want to pull live options data right now:
1. Open the Dashboard and navigate to the **⚙️ Settings** tab.
2. Scroll down to the **🔬 Quantitative Engines (Background Jobs)** section.
3. Open the **📅 Earnings Volatility Engine** card.
4. Click the **"▶️ Run Now"** button.
5. Wait a few moments (monitor the Notifications tab for completion), then navigate to the **Earnings Volatility** page on your top navbar to view the arbitrage table.

*Note: As long as the scheduler is enabled in your settings, this will run automatically every weekend to prep you for the week ahead.*

#### Option B: Run via Terminal (Headless / SSH)
If you are managing the server via SSH, activate your virtual environment and run the dedicated engine script directly:

```bash
# This triggers the engine to read your portfolio/watchlist JSONs, 
# fetch the live options chains, and execute the arbitrage math.
python3 earnings_vol_engine.py
```

### Option C: API Trigger (For Integrations)
You can trigger the update via a simple POST request for external automation:
```bash
curl -X POST http://localhost:8090/api/trigger-earnings-scan
```