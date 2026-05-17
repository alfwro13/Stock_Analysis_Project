# 🛡️ Institutional Tail-Risk Management (VaR & CVaR)

The Quantamental Dashboard integrates Hedge-Fund level risk management directly into your daily workflow. Instead of just looking at standard moving averages, the system calculates the statistical probability of a catastrophic downside event for your specific assets.

This feature populates the **VaR (95%)** and **CVaR (95%)** columns in your Portfolio and Watchlist tables.

---

### 🧠 1. What Is It?

* **Parametric Value at Risk (VaR - 95%):** VaR is a statistical technique used to measure the maximum expected financial loss within a specific timeframe at a specific confidence level. We calculate this at a **95% confidence interval** using the last 252 trading days (1 Year) of logarithmic returns.
* **Conditional Value at Risk (CVaR - Expected Shortfall):** While VaR tells you the threshold, CVaR answers the terrifying question: *"If the worst-case 5% scenario actually happens, how bad will the bleeding be?"* It measures the average magnitude of losses that fall *beyond* the VaR threshold.

### ⚙️ 2. What It Does & What It Says

The `risk_engine.py` script automatically downloads 1 year of daily historical prices for the asset. It converts these prices into continuous logarithmic returns, calculates the mean ($\mu$) and standard deviation ($\sigma$), and applies a z-score to find the absolute tail-end of the bell curve.

**How to Interpret the UI Columns:**
* **VaR (95%) = `4.50%`**: This says, *"Based on the last year of volatility, we are 95% confident that this stock will not drop more than 4.50% in a single day."*
* **CVaR = `6.20%`**: This says, *"On the rare 5% of days where the stock DOES crash past the 4.50% VaR threshold, the average expected drop is 6.20%."*

*Color Coding:* The UI highlights VaR in **Red (Poor)** if the daily risk threshold exceeds `5.00%`, warning you that the asset is highly volatile and prone to sudden, violent sell-offs.

### 🎯 3. Scope & Limitations (Why Not The Entire Universe?)

You will notice that VaR and CVaR data **only** populates for assets in your **Portfolio** and **Watchlist**. If you look at the 4,000+ Market Screener, these fields are blank (or `N/A`). 

**Why?**
Calculating accurate parametric risk requires fetching a dedicated 1-year historical dataset for the calculation of continuous log returns. 
Executing this heavy data fetch across 4,000+ US Equities would:
1.  **Trigger API Bans:** Yahoo Finance actively rate-limits connections. Spamming 4,000 historical requests would result in an immediate IP block (HTTP 429).
2.  **Massive Compute Time:** Even with polite throttling (1.5 seconds per request), downloading risk data for the entire universe would add over 1.5 hours to the weekend scan.

**The Architectural Solution:** We run broad, lightweight momentum math (Moving Averages, RSI, MACD) on the massive Universe to help you discover stocks. But we reserve the heavy, compute-intensive statistical risk modeling strictly for the high-priority assets you actually care about (Portfolio/Watchlist).

---

### 🚀 4. How to Run & Populate the Data

Because tail-risk is integrated directly into the core Quantitative Engine, calculating VaR and CVaR requires running a Daily Quant Scan.

#### Option A: Run via the Web GUI (Recommended)
If you have just added a new stock to your Ghostfolio or Watchlist and want to populate its risk metrics immediately:
1. Open the Dashboard and navigate to the **⚙️ Settings** tab.
2. Scroll down to the **🔬 Quantitative Engines (Background Jobs)** section.
3. Open the **📊 Daily Quant Screener (Portfolio & Watchlist)** card.
4. Click the **"▶️ Run Daily Scan Now"** button.
5. Wait 1-10+ minutes (monitor the Notifications tab for completion), then return to your Portfolio to see the updated metrics.

*Note: As long as the scheduler is enabled in your settings, this will run automatically every night.*

#### Option B: Run via Terminal (Headless / SSH)
If you are managing the server via SSH and want to forcefully trigger the risk calculations without opening the UI, you can manually execute the core script. Ensure you are inside your virtual environment (`source venv/bin/activate`):

```bash
# This triggers the core engine to read your portfolio/watchlist JSONs, 
# fetch the historical data, and run the risk_engine.py mathematics.
python3 quant_engine.py
```
### Option C: API Trigger (For Integrations)
You can trigger the update via a simple POST request if you are integrating this dashboard with Home Assistant, Node-RED, or external cron jobs:

```bash
curl -X POST http://localhost:8090/api/trigger-quant-scan
```