# 📅 Earnings Volatility, Post-Earnings Drift & Options Arbitrage

The Quantamental Dashboard includes a dedicated engine (`earnings_vol_engine.py`) that analyses what tends to happen to a stock's price around its earnings reports — both for a long-term holder ("where has this stock historically gone after earnings, and where might it go this time?") and for an options trader (statistical mispricings in the options market leading into the report).

This feature powers the **Earnings Volatility** page and the **Earnings Volatility Accuracy** page on your dashboard.

---

### 🧠 1. What Is It?

For a long-term investor, the more useful question isn't "is the options market mispricing this event" but "what has historically happened to the price in the days/weeks after this ticker's earnings, and does the model expect that to continue?" The engine now answers three related things for each tracked ticker with earnings coming up:

1. **Post-Earnings Drift** — a *signed* (directional) historical average move at 1, 5, and 20 trading days after each of the ticker's last 4 earnings reports, plus how many of those 4 events were positive.
2. **ML Quantile Price Band** — the same general-purpose, non-earnings-conditioned 10-trading-day price band already shown on the Stock Detail and Predicted Movers pages, surfaced here as a secondary reference.
3. **Options Mispricing** — the original Implied Move vs. Historical Move Edge Score, still computed but now a secondary section on the page (collapsed by default) since it's only actionable for options traders.

### ⚙️ 2. Post-Earnings Drift & the Prediction Accuracy Tracker

`get_historical_earnings_drift(ticker)` extends the original "2-trading-day window, absolute move" calculation into three **signed** horizons (1/5/20 trading days after the pre-earnings close), each with its own sample size, average % change, and count of "up" events. These are computed by the weekly `weekend_earnings_vol_scan_job` and written into 9 new columns on `earnings_volatility` (`drift_avg_pct_Nd`/`drift_up_count_Nd`/`drift_sample_size_Nd`), so the Earnings Volatility page can render them with no live calls.

**A ticker with no liquid ATM options quote still gets a row** on the page now — since drift stats don't depend on options data, `implied_move_pct`/`edge_score`/`options_volume` are simply left blank for that ticker rather than hiding it entirely (this changed from the original behavior, where a missing options quote silently excluded the ticker from the page).

**The prediction + accuracy tracker** (`earnings_drift_predictions` table) is a separate, new piece: a small daily step, `log_near_earnings_predictions()`, piggybacked onto the existing daily `overnight_quant_scan_job` (not the weekly scan, which can run up to 14 days before the actual earnings date — too early for a good baseline price). Each weekday, for any tracked ticker whose earnings falls within the next 4 calendar days, it independently recomputes the historical drift and logs a prediction — the ticker's own signed historical average projected forward from **today's close** as the pre-earnings baseline. No new ML model is trained; the "prediction" is simply that projected historical average. Running daily until the earnings date re-anchors the baseline to a fresher close each day (via an `ON CONFLICT ... DO UPDATE ... WHERE direction_correct_1d IS NULL` guard, so a row that's already begun resolving is never overwritten), converging on the actual last close before the print.

A second step in the same daily job, `backfill_earnings_drift_outcomes()`, resolves every logged prediction whose 1/5/20-trading-day target date has passed by looking up the first `quant_signals` close on/after that date, and records whether the actual direction matched the predicted direction — independently per horizon, since a prediction can be right at 1 day and wrong at 20, or vice versa. The **Earnings Volatility Accuracy** page (`/earnings-volatility/accuracy`) shows per-ticker and overall resolved/pending counts and direction-accuracy % at each horizon. Clicking a ticker's row expands a per-event breakdown (`db_helpers.get_earnings_drift_events()`, one row per ticker + earnings_date) showing that specific event's predicted % move and the actual % move derived from `actual_price_Nd`/`pre_earnings_close`, since the per-ticker aggregate alone doesn't say what the prediction and outcome actually were once a ticker has more than one logged earnings event.

**Stock Detail page (near-earnings panel):** the Stock Detail page shows the same `drift_avg_pct_Nd`/`drift_up_count_Nd`/`drift_sample_size_Nd` figures from `earnings_volatility` for 1/5/20 trading days (identical formatting to the Earnings Volatility page's table), next to a 👍/👎 icon per horizon — `db_helpers.get_latest_resolved_earnings_drift(ticker)` looks up the ticker's most recent `earnings_drift_predictions` row with a resolved `direction_correct_Nd` for that horizon (independently per horizon, since 1d resolves well before 20d) and shows whether that specific past event moved with or against the historical-average direction. This panel is shown whenever `next_earnings_date` is within the existing near-earnings window, including up to 25 days after the report (extended from 7, so the 20-day horizon has time to resolve before the panel disappears). This replaced the panel's previous Implied Move / Historical Avg Move / Options Edge display, which remains available in the Options Mispricing section of the main Earnings Volatility page.

### 📈 3. Implied Move, Historical Avg Move & Edge Score (Options Section)

The engine also still checks each tracked asset's `next_earnings_date` in `stock_signals` — already refreshed nightly by the Quant Scan from the same Yahoo Finance field this engine used to fetch live — to see whether earnings fall within the next 14 days. This is a local database read, not a Yahoo Finance call, so tickers with no near-term earnings cost nothing here. Only for assets that pass this filter does the engine make any live Yahoo Finance calls, to price the actual options edge:

* **Implied Move (The Market's Expectation):** The engine finds the closest At-The-Money (ATM) Call and Put options expiring immediately after the earnings date. It adds their prices together (an ATM Straddle) and divides by the current stock price. *Example: "The options market is pricing in a 6.50% move."*
* **Historical Avg Move (The Reality):** The engine fetches the exact dates of the last 4 quarterly earnings reports, looks at the historical daily chart, and calculates the average absolute percentage move over a 2-trading-day window (the close before the report to the close after). This captures the full reaction regardless of AMC/BMO timing. *Example: "Historically, this stock moves an average of 9.20% across its 2-day earnings window."*
* **Edge Score:** The mathematical difference between the Historical Move and the Implied Move.

**How to Interpret the Edge Score:**
* **Positive Edge (Green):** Options are *underpriced*. The stock historically moves more violently than the options currently predict. Buying options (like a Straddle or Strangle) here has a statistical edge.
* **Negative Edge (Red):** Options are *overpriced*. The options market is expecting a massive move, but historically the stock barely moves. This is a prime setup for an "IV Crush." Selling options (like an Iron Condor or Credit Spread) here has a statistical edge.
* **Options Volume:** The combined open interest/volume of the ATM Call and Put. If this number is very low (e.g., < 100), the Edge Score may be a false positive due to wide, illiquid bid/ask spreads.

### 🎯 4. Scope & Limitations (Why Not The Entire Universe?)

This engine **only** scans assets present in your **Portfolio** and **Watchlist** (plus any other ticker already in the app's shared nightly fetch universe, e.g. an Account Transactions ticker or a Markets page index — see `AGENTS.md`'s priority-arbitration rule). It does not scan the massive 4,000+ Market Screener universe.

The Earnings Volatility page's **Scope** selector (Portfolio / Watchlist / All, defaulting to Portfolio) filters the rendered table client-side by each row's `data-portfolio`/`data-watchlist` attributes — set server-side in `page_routes.py` from `accounts_engine.get_combined_holdings()` and `database.get_watchlist_tickers()` — so a row already scanned into `earnings_volatility` that isn't in either set (e.g. a Markets page ticker) only shows up under "All".

**Why?**
1. **Extreme API Load:** For each stock that has earnings coming up, the engine must make multiple sequential queries: fetching the options chain, fetching the historical earnings calendar, and fetching multiple historical price slices to measure past gaps. Running this 4,000 times would result in a permanent IP ban from our data providers.
2. **Options Liquidity:** The vast majority of small-cap and mid-cap stocks in the broader market universe have highly illiquid options chains with massive bid/ask spreads. Math derived from illiquid options is inherently flawed. By restricting the scan to your curated Portfolio/Watchlist, we ensure the engine focuses on high-quality, liquid assets.

---

### 🔁 5. Resilience — Retrying Yahoo Fetch Failures

Yahoo's `guce.yahoo.com` consent-gate endpoint has been observed intermittently refusing connections mid-scan when hit at too tight a cadence across 100+ sequential tickers (each ticker involves 2-3 Yahoo calls: earnings dates, options expirations, options chain). Two things guard against this:

* **Slower cadence:** the gap between tickers was widened from a random 0.5-1.5s to a random 2.5-5.0s, to stay well under whatever rate Yahoo's gate is reacting to.
* **A single retry pass:** `run_earnings_vol_scan()` tracks which tickers were due to be scanned (earnings within 14 days) but failed — a genuine fetch failure, not a ticker correctly skipped because its earnings aren't due yet — and returns that list. `run_weekend_earnings_scan()` (`scheduler_jobs.py`) schedules a one-off job (`earnings_vol_retry_job`, ~12 minutes later, `DateTrigger`) that re-runs the scan for just those tickers. If a ticker still fails on retry, it's left for the next scheduled weekly scan — there is no further re-scheduling, so a ticker with genuinely no Yahoo earnings-dates data doesn't retry forever. This retry job is unpersisted (in-memory job store, like all jobs in this app) — a server restart within that 12-minute window silently drops it, which is an acceptable trade-off for a data-freshness nicety rather than load-bearing data.

### 🚀 6. How to Run & Populate the Data

The Historical Avg Move, Drift, and Options Mispricing data on the main page are computed weekly by `weekend_earnings_vol_scan_job`. The near-earnings prediction logging and outcome backfill for the Accuracy page run daily as part of `overnight_quant_scan_job` — no separate trigger exists for those two steps, since they're small, cheap piggyback steps on an already-scheduled job.

#### Option A: Run via the Web GUI (Recommended)
If you want to pull live options data right now:
1. Open the Dashboard and navigate to the **⚙️ Settings** tab.
2. Scroll down to the **🔬 Quantitative Engines (Background Jobs)** section.
3. Open the **📅 Earnings Volatility Engine** card.
4. Click the **"▶️ Run Now"** button.
5. Wait a few moments (monitor the Notifications tab for completion), then navigate to the **Earnings Volatility** page on your top navbar to view the drift stats and arbitrage table.

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