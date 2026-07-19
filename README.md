# **📈 Quantamental Portfolio Dashboard**

Self-hosted web application that merges **Quantitative Analysis** (algorithmic momentum, trend-following, candlestick patterns) with **Fundamental Analysis** (valuation, balance sheet health, and market sentiment), enhanced by **Machine Learning** and **Institutional Tail-Risk Management**.

Designed for Linux environments, this system manages your portfolio through its own Built-in Accounts (native transaction ledger, no external tracker required), with optional live-sync support for a [Ghostfolio](https://ghostfol.io/) instance if you already run one. It scrapes multi-dimensional market data via Yahoo Finance and generates an interactive dashboard using FastAPI and Plotly.

Please note that this is a hobby project not an investment platform.

## **✨ Core Features**

* **Ensemble Machine Learning Prediction Engine:** Utilizes a soft-voting classifier (XGBoost + Random Forest) trained on historical vectorized features to calculate the probability (0-100%) of an asset returning >3% over the next 10 trading days (entry at T+1 close, exit at T+10 close).
* **Entry & Exit Zone Analysis:** Three complementary methods computed during the daily quant scan and displayed on the stock detail page and portfolio/watchlist tables: (1) **Volume Profile** — a 180-day volume-at-price histogram identifying the Point of Control (POC), Value Area Low/High, and High Volume Nodes (HVNs) that act as institutional support and resistance; (2) **Keltner Channel Z-Score** — measures how many ATRs price is above or below EMA(21), firing an entry signal when price is 2–3 ATRs below a healthy uptrend and an exit signal when overextended above +3 ATRs with RSI > 75; (3) **ML Quantile Price Bands** — two XGBoost quantile regressors (Q10 floor / Q90 ceiling) that predict the 10th and 90th percentile of the 10-day return distribution and convert them to price targets. Exit targets are only shown for portfolio holdings. A standalone **Position Targets** box (shown whenever a ticker is held and/or watchlisted — not just held) lets you turn the ML Quantile Bands suggestion into your own low (buy) and/or high (sell) price target, with one row per built-in account holding that ticker plus a separate Watchlist row if it's on your watchlist, and a "set for all" option to apply the same pair to every row at once. Fires an independently toggleable notification (Settings → Notification Settings) the first time the live price crosses a set target — a watchlisted-but-unheld ticker with a target is checked for this alone, without pulling it into Crash/Moonshot/Anomaly detection. The same target is also readable/writable from the Home Assistant integration's Low Limit/High Limit entities.
* **Institutional Tail-Risk Management:** Dynamically calculates 1-day Historical-Simulation Value at Risk (VaR) and Conditional VaR (Expected Shortfall) at a 95% confidence interval to quantify extreme downside exposure.
* **Zero-LLM Market Sentiment Pulse:** Leverages FinBERT (ProsusAI/finbert) Natural Language Processing (NLP) to read and score live news headlines, quantifying media narratives on a strict -1.0 (Panic) to +1.0 (Euphoria) scale.
* **Turbulence-Aware Macro Regimes:** Actively monitors the S&P 500's historical volatility alongside implied volatility (VIX) to classify the market as Normal, Volatile, or Crash, feeding downstream risk tooling like the Historical Stress Tester and the Stock Detail "Yield Sensitive" macro-trap warning.
* **Auto-Syncing Portfolio (Multi-Account):** Integrates directly with Ghostfolio via API to automatically pull your live holdings. Now supports opt-in account discovery, allowing you to selectively sync specific accounts and calculate accurate global VWAP Cost Basis and Unrealized P&L across different currencies. A master **Enable Ghostfolio Integration** switch in Settings lets you turn the whole integration off if you only use Built-in Accounts — disabling it deletes the cached `portfolio.json`/`watchlist.json` files (so the Portfolio page's totals stop reflecting stale synced data) and is enforced as a backstop by both the nightly maintenance job and the System Configuration Check.  
* **Built-in Accounts (Native Ledger):** A self-hosted alternative or companion to Ghostfolio — manage your own brokerage accounts and full transaction history directly in the app (`/accounts`). Each account has a type — **Trading** (default), **House**, **Pension**, or **Watchlist** — and only Trading accounts feed the Portfolio page. The type is locked once an account is created and can't be changed via Edit. Deleting an account (soft-delete — its transaction history is preserved, just hidden) lives in a "Danger Zone" section at the bottom of each account's detail page rather than a one-click button on the tile, and requires ticking a confirmation checkbox before the Delete button enables. **House** and **Pension** accounts are tracked standalone via the **Account Price Scraper**: a generic URL + CSS-selector price feed (configured from the account's own tile, not Settings) that replicates what Ghostfolio's manual-asset scraper does — point it at a daily-updated price page (your own, or anyone's), and it records the value once a day on a per-account schedule, with a "Test" action to validate the selector and a "Scrape Now" action for an ad-hoc run, plus a historical CSV import for backfilling price history. A green/red status dot next to the Scraper button on the account tile shows whether the last scheduled run succeeded or failed. **House** accounts get a dedicated detail page (`/accounts/{id}/house`) instead of the standard ledger view — deliberately minimal, just a House Value Over Time chart (auto-ranging y-axis, so a multi-hundred-thousand valuation doesn't render as a near-flat line dragged down to a 0 baseline) plotting every available point in the scraped/imported price history, plus the Scraper config. The purchase price and date entered when the account is created (or later edited) are automatically seeded as the chart's first data point, so the line starts at the real purchase rather than at the first scrape. **Pension** accounts get a dedicated detail page (`/accounts/{id}/pension`) instead of the standard ledger view, with a unit price chart, a value-over-time chart, Pension Value / Performance % (1 month, YTD, 1 year) tiles derived from the scraped unit price history, and an Activities table with a running total-units column; the internal `PENSION-{id}` ticker can be given a friendly display label from the Edit Account modal. The value-over-time chart can also overlay **Pension Benchmarks** — a UK CPI + Target line (UK CPI YoY%, the same series shown on the Market Sentiment page, plus a user-set target, 4% by default) and any number of ticker benchmarks (defaulting to the MSCI World Index and FTSE All-World Index), each rebased to the pension's own starting value so every line sits on the same value axis; configured from a **🎯 Benchmarks** button on the Pension account's tile. Two dedicated actions live on that page — **Pay In** (turns a contribution amount into fund units at that day's scraped price, previewing both the units added and the resulting new total) and **Admin Fee** (turns the units-remaining reading from the provider's portal into the fee's monetary cost, automating the arithmetic without automating the trigger). The single **Watchlist** account is created automatically by the system (it can't be created, deleted, or retyped manually) and holds the tickers added via the star icon on a stock's detail page or the full `/watchlist` page; its own detail page is a compact search/filter/bulk-delete management view rather than the standard transaction ledger. Its tile shows a ticker count with an Equity/ETF/Fund breakdown instead of a currency/cash line (not meaningful for a Watchlist), and its Edit Account modal only exposes Name and Note — Currency, Initial Cash, and Opening Date are hidden since they don't apply. **Trading** account tiles likewise replace the currency/initial-cash line with live Number of Holdings / Equity Value / Cash Balance. Add Buy, Sell, Dividend, Interest, Fee, and Cash transactions per Trading/House/Pension account, with a ticker/name lookup against Yahoo Finance and automatic historical FX conversion to your base currency when a trade's exchange rate isn't supplied. Holdings derived from built-in accounts are automatically merged into the Portfolio page alongside any Ghostfolio-synced accounts — the same ticker held in both is summed, and both accounts are listed against it. Each account has its own detail page (`/accounts/{id}`) with a value-over-time chart, open Holdings (market value, allocation %, performance), Closed Positions with realized P&L, the full Activities ledger, and Cash Balance History — the chart is fed by a nightly Account Value Snapshot job plus a one-time historical backfill from cached price data when an account is created, and has 1M/1Y/YTD/MAX range buttons whose selection is remembered (via a browser cookie) across every account you open. Each Trading account's detail page also shows live-refreshing return tiles — 1 Day / 1 Week / 1 Month / 3 Month / 6 Month / 1 Year gain/loss in your base currency, Unrealized P&L, and since-inception Money-Weighted Rate of Return (the one percentage figure, since it's a rate) — that update automatically while the page is open (subject to the "Live prices" setting), sourced from the same intraday price feed that powers Crash & Moonshot Alerts. A **Reconcile** button lets you true up small drift (FX rounding, a missed fee) between the app's computed cash balance and your real broker statement — enter the actual balance and it books a single tagged Cash adjustment for the difference, filterable in the Activities table via an "Adjustment" option. An "Import from CSV" control loads a GIA/broker-style activity export file (Top Ups, Interest, Buys/Sells, Dividends) directly into a built-in account, deriving the GBP exchange rate and fees from the file's own dual-currency price columns rather than trusting its FX-rate column's quoting convention; rows with a ticker that can't be resolved are skipped and listed by name rather than imported with a placeholder, and re-importing the same file is safe (see `assets/csv_import_format.md` for the exact column layout). Each transaction's currency is picked from a configurable dropdown (Settings → Core System & Currencies → Account Currencies, default GBP/GBp/USD/EUR) rather than assumed, since the same exchange can list stocks in more than one currency — a live total preview shows the entered amount, the fee, and the resulting cash impact in your base currency before you save. The Fee has its own independent currency selector, since a fee isn't always billed in the trade's own currency (e.g. a broker's FX spread fee quoted directly in your base currency on a foreign-currency trade). A CSV export on the Accounts page mirrors the Import from CSV column layout, doubling as a practical ledger backup that's close to ready for re-import, as well as letting you verify the full ledger and FX math against your own brokerage statements. **Trading** accounts can also set up **Auto Top-up** — a recurring direct-debit schedule (a fixed day of the month, or a day of the week) configured from the account's tile or detail page. Rather than posting cash automatically (bank credit dates drift around weekends/holidays), the scheduled date tags the account `[PENDING ACTION]` and opens a confirmation banner on its detail page, where you can adjust the amount/date to match what actually landed before confirming it as a real Cash deposit, or dismiss it if the payment never came through. **Trading** accounts can also track **UK Treasury Bills** via a dedicated "Buy T-Bill" action — enter the Start Date, Amount (Total Cost), Indicative YTM, and Maturity Date exactly as Freetrade shows them, and Face Value auto-fills as an estimate (Freetrade never states it directly, and the real yield isn't fixed until the Friday DMO tender closes) that you can hand-correct before saving; the account's Holdings table (and Home Assistant) then show the bill's value accreting toward that face value as maturity approaches. Since the tender often closes after a bill is already logged, a "Confirm the final YTM" banner (mirroring Auto Top-up's confirm/dismiss pattern) appears on the account page once the Start Date arrives, letting you enter the real rate to recompute Face Value or keep the original estimate as final. A daily background job automatically closes the position and credits the cash balance on the maturity date, and an optional Auto-Reinvest flag fires a reminder notification when a bill matures (never an automatic re-purchase, since the real yield on the next weekly issue isn't known until tender).  
* **Backup & Recovery:** Archives the database, cached market data, and trained ML models — independently selectable — to a local folder or an NFS share, on a schedule (day-of-week checkboxes + time) or on demand via "Run Backup Now". Retention keeps only the most recent N archives. A Recovery panel lists every stored archive and restores the selected one back into place with one confirmed click; a Backup Status panel in Settings → System Diagnostics shows the last run's outcome, size, and how many archives are currently stored. The NFS option needs a one-time host setup (`tools/setup_nfs_backup.sh`) to grant the app a narrowly-scoped `sudo` rule for mounting — Local Folder backups need no extra setup.  
* **Multi-Dimensional Data Engine:** Downloads 2-year macro daily data, 1-day 5-minute intraday data, and deep fundamental .info payloads.  
* **Nextcloud Talk Integration:** A comprehensive alert ecosystem that pushes rich notifications directly to your Nextcloud Talk app.  
* **Unified Notification Settings:** A single Settings panel ("Notification Settings") that controls, per scheduled job and per alert, which channels each notification is delivered through — the rotating log file, the in-app notification centre, and/or Nextcloud Talk. Replaces the previously scattered per-engine "send to Nextcloud" toggles with one pane of glass; new channels can be added centrally.  
* **Hierarchical Candlestick Recognition:** Algorithmically detects and scores 11 distinct patterns across three tiers on live daily and intraday data. Tier-1 three-candle patterns: Morning Star (+20), Evening Star (−20), and Three White Soldiers (+18). Tier-2 two-candle patterns: Bullish/Bearish Engulfing (±15), Piercing Line (+10), and Bullish/Bearish Harami Cross (±8). Tier-3 single-candle patterns (mutually exclusive): Hammer (+10), Shooting Star (−10), and Doji (0). Pattern scores accumulate additively into the composite score and are clamped to −100…+100. Each detected pattern emits a tag chip with hover tooltip in the dashboard tables and an annotation marker on the macro chart.  
* **Crash & Moonshot Alerts:** High-frequency 5-minute scanning that detects mathematical "Crash" conditions (heavy drops below SMA) and "Moonshot" conditions (parabolic spikes, All-Time Highs) during active market hours. The live prices it pulls for this are also shared into a common cache (alongside Dip Radar's own scans), so the Portfolio page reflects intraday moves rather than only the once-daily quant scan price.  
* **Markets Page:** A global markets overview (`/markets`) covering major indexes, commodities, and FX across Europe, the US, Asia-Pacific, and Commodities & FX. A **Dynamic view** (default) orders the regional sections by which trading session is most relevant right now — Asia-Pacific first in the early hours UK time, Europe first mid-morning, the US moving to the top as New York opens — derived from each region's actual exchange open/pre-market/closed state rather than a fixed clock schedule, so it stays correct across DST changes. A **Static view** toggle keeps a fixed Europe → US → Asia-Pacific → Commodities & FX order for anyone who prefers it. Each tile shows a mini intraday sparkline, price, % change, and sentiment badge, and links through to the same `/index/{ticker}` detail page as Market Pulse. Five major indexes (S&P 500, Nasdaq 100, Dow Jones, Russell 2000, Nikkei 225) automatically swap between their cash price and front-month futures price depending on whether that market is open. The full ticker list is editable from Settings → Markets & Market Pulse — add a new index, commodity, or FX pair with no code change or restart. The existing Market Pulse widget (Portfolio/Watchlist/Stock Detail pages) now reads from the same ticker list and gained an optional dynamic view of its own, alongside configurable desktop/mobile tile counts. Each region badge reads **Open** only when every exchange in it is trading, **Some Open** when the region is a mix (e.g. Hong Kong still open while Tokyo has closed), **Pre-Market**, or **Closed** — and individual tiles are judged the same way, so a closed market's tile greys out with its last price while a tile whose data hasn't refreshed as expected (market open, cache stuck) is greyed with a distinct diagonal-stripe marker. A "Last updated" indicator next to the Live Markets Data status shows exactly how fresh the browser's own data is, and every Home Assistant "Portfolio Refresh Data" call also warms the Markets page's data in the background, so an open Markets tab benefits from the same refresh cadence. The Market Screener link and a Market Reports link (now pointing to the Reports hub, `/reports`) live on this page's header row (next to the Live Markets Data indicator) rather than as separate top navbar items; the top navbar itself now reads Markets, Portfolio, Accounts, Watchlist, News, Earnings Volatility, Market Sentiment, Tools, Reports, Settings, Glossary, Notifications. Each ticker's `/index/{ticker}` detail page now auto-refreshes its Intraday Pulse chart on the same schedule as the Stock Detail page, with a live countdown indicator, and every tracked index/commodity/FX ticker is now included in the nightly data download so the Macro Trend chart and Technicals & Risk panel populate automatically rather than requiring a manual "Refresh" click.
* **Market Sentiment & Insider Tracking:** Maps the CNN Fear & Greed Index against the S&P 500 (with visual chart generation) and scrapes SEC Form 4 filings for major insider buying aligning with algorithmic dips.  
* **Proprietary Scoring (0-100):** A custom algorithm that grades stocks based on Moving Average alignment, RSI, Volatility Contraction (3-Weeks-Tight), MACD Reversals, and On-Balance Volume.  
* **Built-in Task Scheduler:** Fully autonomous background scheduling via APScheduler. No external cron jobs required. Manage execution times directly from the web UI.  
* **Crash-Proof Local Storage & Maintenance:** Persists heavy time-series data locally using highly compressed .parquet files and SQLite3. An automated Maintenance Engine prunes orphaned files and defragments the database weekly.
* **System Health Check Engine:** A daily background job (`system_check_engine.py`) that validates scheduling configuration and ML data coverage. Any detected issues (e.g. ML Training scheduled before Backfill, or inference universe too small) surface immediately as a coloured banner at the top of the Settings page and as a notification in the alerts panel. Schedule configurable via `SCHEDULES.SYSTEM_CHECK` in `config.json`.
* **Workflow Monitor:** A dependency flow-chart of every scheduled job in **Settings → Workflow Monitor**. Each job is a box wired to the jobs that produce the data it depends on (from market-universe ingestion all the way through the quant, ML, sentiment, and dip-radar engines), coloured by a traffic-light recency status — green (ran recently), amber (stale / never run / due), red (failed or overdue), grey (disabled). Non-scheduled data sources also appear on the graph: external sources (cyan, e.g. Yahoo Finance) and manual processes (purple) such as Built-in Accounts' "Manual Account Entry" feeding the Trading/Pension/House account boxes, which in turn feed the same downstream jobs as Ghostfolio Sync or their own Account Price Scraper. A conflict-detection engine flags scheduling problems: a job set to run *while or before* the upstream job feeding its data is still running (using each job's measured average run time), a job whose upstream is disabled, and jobs that failed or have gone stale. New scheduler jobs appear automatically once declared in the job manifest.
* **Change Period (1D/5D/1M/6M/YTD/1Y):** The Portfolio table's Change column and heatmap can be toggled between six lookback windows matching Yahoo Finance's own ranges — 1D stays live off the intraday price feed as before, while 5D/1M/6M/YTD/1Y compare the live price against the closest available close for that calendar cutoff. Switching periods updates the table and heatmap together, and your last-selected period is remembered on your next visit.
* **Pre-Market / After-Hours Prices:** Market Price, P&L, and portfolio totals always reflect the last completed regular-session close — never a pre-market or after-hours tick — determined directly from Yahoo Finance's own market-session flag rather than guessed from the time of day. A **Show After Market Data** switch next to the Portfolio page's Change Period buttons optionally reveals the latest pre-market/after-hours price in brackets under each holding's Market Price, purely for information; the Stock Detail page always shows a Pre-Market/After Hours line when one is currently active for that ticker, with no toggle needed.
* **Portfolio X-ray (Risk & Diagnostics):** A same-page risk diagnostics view embedded in the Portfolio tab. Click the **🔮 X-ray** button to swap the holdings table for a full risk report without navigating away. The report contains: six headline risk cards (Portfolio Beta vs SWDA.L, Annualised Volatility, Max Drawdown, VaR 95% 1-day, HHI concentration score, Top-5 Weight); three donut charts (Instrument Type — including a dedicated Cash & Equivalents bucket for Treasury Bills, True Sector Exposure with ETF/fund look-through, Geographic Exposure by continent approximated from each fund's top-10 holdings); a colour-coded position concentration bar chart (amber >10%, red >20%); and an income panel (weighted dividend yield + projected annual income) alongside an unrealised gross P&L bar chart. Risk metrics (beta, volatility, correlation) are pre-computed nightly by an APScheduler job (`xray_risk_cache_job`, Mon–Fri 19:00) and cached in three SQLite tables so page load never triggers a live yfinance call. The X-ray is account-aware: switching the account dropdown re-renders in-place, and "Global" combines every configured source — Ghostfolio (if configured) plus every built-in Trading account. Each Trading account also has its own **X-ray** button on the Accounts page (`/accounts`), opening the report scoped to just that account. Risk metrics that depend on a full daily return history (historical VaR/CVaR, Sharpe/Calmar ratio, tracking error, skewness) work for any scope — Ghostfolio, built-in, or combined — and are only omitted, with an explanatory note, when fewer than 30 overlapping cached trading days exist yet for the tickers in scope.
* **ETF Price Predictor:** A generic next-session open price predictor for any ETF (`/etf-predictor`). Configure multiple predictors — each specifying an ETF ticker and up to 20 constituent tickers with weights — and the engine predicts the ETF's next opening price using a holdings-weighted basket return (with automatic FX adjustment when the ETF and its constituents are in different currencies) and an OLS regression fallback. Predictions are logged and accuracy (direction accuracy %, MAE, MAPE) is tracked over time per predictor. Two further prices — a bias-corrected prediction and a confidence-weighted blend of the Holdings/Regression engines — are tracked alongside the standard prediction once a predictor has enough history, purely to compare which approach tracks reality more closely over time. Predictors can be enabled/disabled and optionally auto-scheduled (twice daily) from Settings → Tools → ETF Price Predictors.
* **Historical Stress Tester:** A standalone tool (`/stress-test`) that simulates how your portfolio would fare during a historical crash — GFC 2008, Dot-com 2000, COVID-19, or the 2022 inflation shock — using beta-adjusted scenario shocks calibrated per crisis. Each holding's estimated monetary loss is computed as `market_drop × beta × sector_multiplier` and displayed in a sortable breakdown alongside a sector impact chart. Supports per-account or combined portfolio scope. No additional data is required beyond the X-ray risk cache.
* **Market Regime (HMM + Market Stress IF):** A dedicated tool (`/market-regime`) that fits a 3-state Gaussian Hidden Markov Model on 5 years of SPY daily log-returns and EWMA volatility, classifying the market into **Bull** (low vol), **Chop** (elevated vol, indecisive), or **Crash** (high vol, negative returns). States are decoded via the Viterbi algorithm and sorted by mean volatility for a stable label ordering across daily retrains. The page shows the current state with confidence, a full 5-year Viterbi history chart with colour-coded regime bands, an empirical transition probability matrix, and per-regime return/volatility statistics. A compact regime pill is also embedded on the Trap Monitor page for immediate context. A Nextcloud alert fires when the regime transitions. The SPY data is cached in a 5-year Parquet file (`data/historical/SPY_hmm.parquet`) with incremental 1-month tail updates to avoid a full re-download on each daily run. Alongside the HMM, a market-wide **Isolation Forest** (`run_market_stress_if()`) scores six daily macro features — VIX level, VIX/MA ratio, HYG return, 10Y yield change, SPY volume z-score, SPY return — producing a `market_stress_score` in [0,1] stored in `market_regimes`. A Nextcloud alert fires when the score exceeds 0.75 for two consecutive days. Alert cooldown configurable via `ALERTS.MARKET_STRESS_ALERTS` in `config.json`. Raw data is cached in `data/historical/market_stress_if.parquet` with incremental updates.
* **Market Trap & Recovery Monitor:** Scans portfolio holdings, an optional watchlist basket (off by default), and a configurable proxy basket for post-crash lifecycle patterns — Bull Trap, Bear Trap (low-conviction breakdown), Capitulation volume climax, and Wyckoff Accumulation phase — serving each signal as CONFIRMED / POSSIBLE / WATCH / SAFE at `/trap-monitor`. Schedule and alert thresholds configurable via `SCHEDULES.TRAP_MONITORS` and `ALERTS.TRAP_MONITOR_ALERTS` in `config.json`. A **prediction accuracy panel** on the same page shows what percentage of past phase assignments resolved correctly at 14-day and 30-day forward-return horizons; results accumulate automatically via a daily fill job (`trap_accuracy_fill_job`).
* **Alert Confidence Referee (pilot):** A meta-labeling classifier, piloted on the Market Trap & Recovery Monitor, that learns from an alert engine's own historical hit/miss record and can veto a new alert that resembles past false positives — distinct from the existing alert cooldown/dedup gate, which only stops the *same* condition re-firing. Configurable in Settings (Alerts & Reports column): enable/schedule weekly training, choose Shadow (log-only) or Active (enforcing) mode, and set the veto threshold and minimum training-sample count. Active mode only takes effect once enough resolved Trap Monitor history has accumulated; below that the referee stays in Shadow mode regardless of the configured setting, logging what it would have done. A readiness panel shows current vs. required sample count and an estimated readiness date.
* **FX Drag Analyzer:** Decomposes each US stock position's GBP return into two components: equity return in USD and FX effect from GBP/USD movement (`/fx-drag`). Shows which positions are genuinely outperforming and which are riding dollar strength. Reference-period modes (YTD, 1-year, 2-year) use existing 2-year Parquet data. **Lifetime mode** uses the actual weighted-average GBP/USD rate at which each position was purchased, derived directly from the built-in account transaction ledger — no exchange-rate API required. A compact three-number FX breakdown (Equity USD / FX Effect / Total GBP YTD) also appears inline on each USD stock's detail page inside the "Your Position" box.
* **Forensic Screener:** Monthly institutional-grade accounting forensics across all portfolio and watchlist tickers (`/forensic-screener`). Computes three models from annual financial statements fetched via Yahoo Finance: the **Piotroski F-Score** (0–9; score < 4 flags structural decay across profitability, leverage, and efficiency), the **Altman Z-Score** (bankruptcy risk; Z < 1.1 = distress zone), and the **Beneish M-Score** (earnings manipulation detector; M > −1.78 flags possible manipulation). Scores are stored in `stock_signals` and updated by two monthly APScheduler jobs (data fetch on the 1st at 06:00, scoring at 07:00, both in the configured local timezone). Nextcloud alerts fire when any portfolio holding breaches a distress threshold. Both jobs are visible in the Workflow Monitor and configurable in Settings → Forensic Screener. Run-Now buttons on both the Settings panel and the Forensic Screener page allow immediate on-demand execution.
* **Monte Carlo Wealth Simulator:** A forward-looking wealth projection tool (`/monte-carlo`) that runs 1,000 correlated random-walk simulations of the portfolio's value over 10, 20, or 30 years. Uses live per-asset annualised volatility and pairwise correlations from the X-ray engine (Cholesky decomposition) and per-asset-class drift assumptions that the user can override. Produces a percentile fan chart (5th–95th) in both nominal and real (inflation-adjusted) terms, along with the probability of reaching a user-defined target wealth. No scheduler job — results are computed fresh on demand.
* **Portfolio Tearsheet:** A backward-looking performance-analytics report (`/performance-analytics`) covering the metric set of the quantstats library — reimplemented natively, with no external dependency. Shows risk-adjusted ratios (Sortino, Calmar, Omega, Profit Factor), drawdown duration analytics (longest drawdown, time underwater, Ulcer Index), distribution/tail stats, and win/loss statistics, alongside an underwater chart, a cumulative-growth-vs-benchmark chart, a monthly returns heatmap, and a daily-return histogram. Complements the X-ray panel's Sharpe/VaR/CVaR metrics rather than duplicating them — both draw on the same cached return history. Linked directly from the Portfolio page's action-links toolbar (📊 Tearsheet, alongside X-ray/Heatmap/Score History). No scheduler job — results are computed fresh on demand.
* **Portfolio Optimizer:** A suggested-allocation tool (`/portfolio-optimizer`) that computes closed-form Min-Variance and Max-Sharpe target weights for a chosen account scope, using plain matrix algebra rather than a convex-optimization library — no shorting/position-cap constraints, so a suggested weight can be negative and is shown as-is rather than clipped. Held tickers are pre-selected candidates; Watchlist tickers can be opted in via a checklist to see them suggested as a brand-new position. Shows an efficient-frontier chart alongside a table comparing current vs. suggested weight per ticker. Informational only — this app has no order execution, so nothing rebalances automatically. No scheduler job — results are computed fresh on demand.
* **Bubble Radar:** A valuation-euphoria detector that scans all portfolio and watchlist tickers for signs of speculative overextension (`/bubble-radar`). Seven independent metrics contribute to a composite Bubble Risk Score (0–100): SMA-200 Extension %, 20-day average RSI, P/S ratio, PEG ratio, FCF Yield vs the US real 10-year yield (DFII10), IV call skew (US tickers only), and the SPY vs RSP 20-day return spread (market breadth). Tickers scoring above a configurable Watch threshold (default 70) receive a yellow flag; those above the Bubble threshold (default 85) receive a red flag. Flagged tickers are highlighted inline on each stock's detail page and listed at the dedicated Bubble Radar tool. Prediction accuracy is tracked at 4-, 8-, and 12-week forward horizons and displayed in a History tab. Thresholds and schedule are configurable in Settings → Bubble Radar.
* **Pairs Spread Monitor:** A statistical arbitrage / mean-reversion signal (`/pairs-spread`, linked from the new Reports menu) that flags pairs (same currency only) whose price relationship has temporarily diverged. Two scopes, toggled on the page: **Portfolio + Watchlist** (scheduled, alerting) and the full market **Universe** (on-demand only — no schedule, no alerts, since a full-universe scan is expensive). Filters down to pairs with a strong trailing 252-day return correlation (default threshold 0.7), then flags a pair when its log-spread (`log(price_a) − log(price_b)`, currency-unit-invariant) has moved more than a configurable z-score threshold (default 2.0) away from its own trailing-year mean. Unlike every other alert engine in this app, which evaluates one ticker at a time, this is the only one that evaluates the relationship between two tickers. Each row shows both companies' names; click a pair to open both tickers' price history (indexed to 100) in a popup chart. Schedule and thresholds configurable in Settings → Tools → Pairs Spread Monitor.
* **Predicted Movers:** A leaderboard (`/predicted-movers`, linked from the Reports menu) ranking tickers by ML-**predicted** forward price move — the midpoint of the existing ML Quantile Price Band (10-trading-day-forward 10th/90th percentile) versus current price — rather than actual historical movement. Two scopes, toggled on the page: **Portfolio + Watchlist** and the full market **Universe**, with a Gainers/Losers/Movers sort toggle. No scan trigger needed — it's a live query over data the nightly ML Inference job already computes. A linked **Prediction Accuracy** page (`/predicted-movers/accuracy`) tracks, for Portfolio + Watchlist tickers only, each day's prediction against its actual outcome once the ~10-trading-day horizon has passed, grading it two ways: whether the actual price moved the predicted direction, and whether it landed within the predicted band. No configuration needed — it piggybacks on the existing ML Inference schedule.
* **Market Reports:** Seven cross-universe screener reports, each its own dedicated page linked from the Reports menu: **Quality Compounders** (`/quality-compounders`, high ROE/margin/low debt buy-and-hold screen), **GARP Tenbaggers** (`/garp-tenbaggers`, Peter Lynch PEG-based growth-at-a-reasonable-price screen over FTSE100 + S&P500), **Quality on Sale** (`/quality-on-sale`, quality businesses near a 52-week low), **Sector Trends** (`/sector-trends`, average RSI/momentum aggregated by sector and exchange), **Relative Strength Leaders** (`/relative-strength-leaders`, top 500 market-wide momentum stocks), **Mean Reversion Screener** (`/mean-reversion`, oversold RSI within a longer-term uptrend, configurable thresholds), and **Dividend Harvest** (`/dividend-harvest`, high-yield stocks approaching their ex-dividend date, configurable minimum yield/score). Each report is a live query with no scheduler job; previously these seven screens lived together on a single `/market-reports` page, now retired in favour of one dedicated page and Reports-hub tile per report. Engine: `reports_engine.py`.
* **Ticker Notes:** Free-text research notes on any ticker in the app — not limited to portfolio or watchlist holdings. Click **Add Note** on a ticker's Stock Detail page (next to Refresh and AI) to save an observation up to 1000 characters, with line breaks and blank lines preserved on display. Every note is its own permanently timestamped entry — adding a new note never overwrites an earlier one — and can be edited or deleted later. Once a ticker has at least one note, a Notes section appears on its Stock Detail page, above System Verdict. The **Ticker Notes** report (`/ticker-notes`, linked from the Reports menu) lists every ticker with a saved note, with an expandable row per ticker showing its full note history.
* **Earnings Volatility:** `/earnings-volatility` now leads with long-term-investor content: a **signed Post-Earnings Drift** stat (1/5/20 trading days after each of a ticker's last 4 earnings, with a directional average % move and "up X of 4" count), plus the general-purpose **ML Quantile Price Band** shown as a secondary reference. The original options-trading content (Implied Move, Historical Avg Move, Edge Score) remains but is now a collapsed "How to Read" section further down the page, and a ticker with no liquid options quote still appears with its drift stats rather than being hidden entirely. A linked **Earnings Volatility Accuracy** page (`/earnings-volatility/accuracy`) tracks a prediction — the ticker's own signed historical drift projected forward from the last close before earnings — logged shortly before each earnings date and graded independently at each of the three horizons once its target date passes, alongside a chart of the average post-earnings price path. No extra configuration needed — logging/resolution piggyback on the existing daily Overnight Quant Scan.
* **Macro Regime & Yield Curve Allocator:** Embedded in the Portfolio X-ray panel (☢ X-ray button). Synthesises live macro signals — 10y–2y yield curve spread, US CPI, high-yield credit spreads, the 10-year TIPS real yield, and the HMM hidden state — into one of five named economic regimes (Risk-On, Late Cycle, Stagflation, Contraction, Recovery), displayed as a 5-box traffic-light strip with the active regime highlighted. Scores portfolio alignment (0–100) using current asset-class weights from Ghostfolio when configured, falling back to built-in Trading account holdings otherwise — no Ghostfolio required. Shows the cash target as an absolute amount with a user-acknowledgement toggle, and provides a rebalancing delta table. Regime targets are configurable via `REGIME_TARGETS` in `config.json`. Requires the Macro Data Engine to have run at least once.
* **Sovereign Debt Auction Monitor:** Fetches US Treasury auction results from the free `fiscaldata.treasury.gov` API twice daily (13:15 ET and 15:30 ET, Mon–Fri). Compares each auction's bid-to-cover ratio and yield tail against a rolling 6-auction baseline for the same maturity. When either metric signals demand weakness (bid-to-cover > 0.2 below the rolling mean, or yield tail > 2bp above the rolling mean), a Nextcloud and in-app alert fires. Results are stored in `treasury_auction_results`. No API key required. Engine: `treasury_auction_engine.py`. Schedule configurable in Settings → Macroeconomic Data → Sovereign Debt Auction Monitor.
* **Custom Display Name Override:** On any stock detail page, click the pencil icon next to the company name heading to set a personal display label for that ticker. The override is saved to the database and shown instead of the system name in the portfolio table, watchlist table, and the detail page heading. Click "Reset to default" to restore the original name.
* **Score History & Forward Returns:** A dedicated signal quality tracker page (`/score-history`) that answers the question *"When my algorithm rated a stock STRONG BUY, did it actually go up?"*. Every daily scan writes one row per ticker to the `score_history` table (ticker, date, score, signal label, close price). The page then joins these events against the `quant_signals` price series to compute 3-month, 6-month, and 12-month forward returns at query time — no pre-computation needed. Results are grouped by signal bucket (STRONG BUY through TOXIC/AVOID) and displayed in a summary performance table alongside a full event log. A data availability banner shows exactly when each return horizon becomes resolvable. The page is accessible directly from the Portfolio and Watchlist summary bars, with a `?filter=portfolio` or `?filter=watchlist` query parameter to pre-scope results.
* **Watchlist Analytics & Selection Tools:** The Watchlist page (`/watchlist`) mirrors the Portfolio page's mobile usability — tap anywhere on a row to expand its details — and adds a **+ Add Ticker** button (next to the length control, above the table) so a new ticker can be searched and added without leaving the page; newly-added tickers also get an immediate one-off Yahoo profile and price-history fetch instead of waiting for the next nightly scan. Each row is now tagged with a **Quality Grade** (A–D, from ROE/debt/valuation), any **Market Reports screen** it currently qualifies for (Quality Compounder, Quality on Sale, GARP Tenbagger, Mean Reversion Setup, Dividend Harvest), its current **Trap Monitor** phase and **Bubble Radar** flag if any, and its Piotroski F-Score / Altman Z-Score / Beneish M-Score as dedicated columns. When at least one watchlisted ticker has a Position Target set, a **Has Target Set** filter option appears; selecting it swaps those three forensic-score columns for **Low Target** / **High Target** columns and filters the table to only rows with a target. A **⬛ Heatmap** toggle (next to Score History) switches the table to a squarified treemap sized and colored by daily % change, matching the Portfolio page's heatmap. A **Sector Allocation** donut and a **Composite Score vs RSI** scatter (color-coded by signal) sit above the table and respect whatever search/filter is currently applied. The Market Pulse widget and the US 10Y Treasury / UK 10Y Gilt macro cards have been removed from this page to keep it focused on per-ticker analytics — both remain on the Portfolio page.

Watch list Dashboard:
<img width="2247" height="1633" alt="watchlist_dash" src="https://github.com/user-attachments/assets/22fcd68d-b6a9-4f5d-aacc-69bd88db0bfc" />
Market Sentiment Page:
<img width="2265" height="1630" alt="market_sentiment" src="https://github.com/user-attachments/assets/1c70fd70-6cdf-4ee3-855b-f70d9ca3c5a4" />
Holding detailed view:
<img width="867" height="1805" alt="detailed_view" src="https://github.com/user-attachments/assets/35beacd7-df5e-435c-aa55-dd4f087e5b7b" />

## **🚀 Installation & Setup**

### **1. Prerequisites**

You must have **Python 3.10 or higher** installed on your system.

### **2. Clone and Install**

Clone the repository and install the required dependencies using a virtual environment:
```bash
git clone https://github.com/alfwro13/Stock_Analysis_Project.git
cd Stock_Analysis_Project  
python3 -m venv venv  
source venv/bin/activate  
pip install -r requirements.txt
```

### **3. Initial Configuration**

The Quantamental system features an automated configuration engine. You do not need to manually build the settings file from scratch.

1. **Bootstrap the System:** Simply start the server for the first time by running `python main.py`. The engine will automatically detect a fresh install and generate a fully structured `config.json` file in your root directory.
2. **Configure via Web UI:** Open your browser and navigate to **http://localhost:8090**. Go to the **⚙️ Settings** tab.
3. **Connect Your Portfolio:** Enter your `GHOSTFOLIO_URL` and `API_TOKEN`, then click **"Save & Apply"**. 

*Alternatively, you can manually edit the generated `config.json` file in your code editor.*

**Note:** Ensure your `BASE_CURRENCY` (e.g., GBP, USD, EUR) is set correctly in the Settings. This ensures that foreign assets are mathematically converted to your local currency using live FX rates for accurate P&L calculations.

**Font Sizes:** Adjust font sizes for major UI elements in Settings → Core System & Currencies → Font Sizes. Five elements are individually configurable — navigation menu items, data table cells, form controls, action buttons, and section/panel headers — each via a pixel-size dropdown. Changes persist across restarts via `config.json` and take effect on the next page load (no server restart required).

**Yahoo Finance Dual-Stack Routing:** The Advanced Network & Socket Binding panel in Settings exposes three routing modes for Yahoo Finance traffic:
- **IPv4 only** (`YAHOO_USE_IPV4: true`, `YAHOO_USE_IPV6: false`): standard OS routing — the default.
- **IPv6 only** (`YAHOO_USE_IPV4: false`, `YAHOO_USE_IPV6: true`): all requests are bound to the address in `YAHOO_IPV6_ADDRESS`. On a hard IPv6 fault, the session falls back to IPv4 for the remainder of the process lifetime and fires a Nextcloud alert.
- **Dual round-robin** (both `true`): alternates between IPv4 and IPv6 on successive calls to spread load and bypass per-IP rate limits.
The **Yahoo Finance API Usage** panel immediately below shows daily request counts, interface breakdown, HTTP 429 hits, error counts, and a separate yfinance-logged count for the past 8 days (sourced from `GET /api/system/yahoo-api-stats`) — the latter tracks ERROR-level lines the `yfinance` library itself logs (e.g. no data for a ticker/period, a 404 on a module Yahoo doesn't support for that instrument) without raising an exception, so they show up in the Log Viewer but wouldn't otherwise count as a request failure. Click a row to open a detail chart in a new tab, breaking that day's requests into 15-minute intervals stacked by which scheduled job was running at the time (or "Manual / On-Demand" for requests triggered by browsing a page).

**File Logging:** To capture the full application log to disk, enable `FILE_LOGGING` in Settings → Core System & Currencies. Key options:
- `ENABLED` — toggle file logging on/off without restarting the server
- `LEVEL` — minimum severity written to the file (`DEBUG` | `INFO` | `WARNING` | `ERROR` | `CRITICAL`); the console stays at INFO regardless
- `DAYS_TO_KEEP` — number of daily rotated files to retain (default 30)
- `ARCHIVE` — gzip-compress rotated files to save disk space
- `LOG_DIR` — directory for log files, relative to the project root (default `logs/`)

The active log is always `logs/app.log`. Rotated files are named `app.log.YYYY-MM-DD` (or `.gz` if archive is enabled). Changes take effect immediately without a restart.

Once file logging is enabled, click **📄 Open Log Viewer** in the same Settings panel (or navigate to `/log-viewer` directly) to open a live log viewer in a new browser tab. The viewer displays the last 500 lines on load and then streams new lines in real time (equivalent to `tail -f`); a **Load Full File** button loads the entire active log file on demand. You can filter by severity level (DEBUG / INFO / WARNING / ERROR / CRITICAL) and search across all visible lines — your severity selection is remembered across page reloads.


### **Security — Dashboard Credentials**

On first start, `config.py` writes default credentials (`admin` / `changeme`) to `.env` and immediately forces a password change on first login. Passwords are stored as PBKDF2-SHA256 hashes (`DASHBOARD_PASSWORD_HASH` in `.env`); the plaintext key is cleared after the first change.

To configure email-based self-service password reset:
1. Go to **Settings → User Account → Account Email** and save your email address.
2. Add SMTP credentials to `.env`: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` (optional `SMTP_FROM`). If SMTP is not configured, reset links are delivered via Nextcloud Talk or logged to the server.
3. The **Forgot password?** link on the login page initiates the flow. Reset links expire after 1 hour.

If you are locked out entirely, see `assets/system_recovery_and_architecture.md` — *Password Reset Procedures* for three recovery methods including a console script (`python reset_admin_password.py`) and a `FORCE_PASSWORD_RESET` config-file flag.

### **4. Initial AI Training (Cold Start)**
Before the system can provide Machine Learning predictions, it must build its historical training set.

1. Start the server (python main.py) and navigate to http://localhost:8090.
2. Go to the ⚙️ Settings tab.
3. Scroll down to the 🧠 Machine Learning & AI Engine section.
4. Click "⚙️ Initialize AI Engine (Backfill & Train)".

This will run securely in the background. It downloads 2 years of daily data for a curated list of ~250 Blue Chip stocks plus your portfolio, engineers the vectorized features, and trains the global ml_ensemble.joblib model. You can track its progress in the Notifications tab.


### **5. Keeping Dependencies Updated**

[Dependabot](https://docs.github.com/code-security/dependabot) opens a weekly pull request for any outdated package in `requirements.txt` (`.github/dependabot.yml`). Every pull request — Dependabot's or your own — is automatically checked against the full regression suite by GitHub Actions CI (`.github/workflows/tests.yml`) before it's safe to merge. `assets/dependencies.md` documents what each dependency is actually used for and what to test after a major-version upgrade.

The app itself also checks, on every startup, whether the packages actually installed in your virtual environment still match the pins in `requirements.txt`, and logs/notifies (Settings → Notifications) if they've drifted — for example if `requirements.txt` was updated but `pip install -r requirements.txt` was never re-run. This catches drift regardless of how the server was last restarted.

## **💻 Usage & The Web UI**

To start the server, simply run the main application file. The system will automatically build the SQLite database on its first boot.

`python main.py`

* Open your web browser and navigate to **http://localhost:8090** (or your server's IP address).  
* **Settings Tab:** Navigate to ⚙️ Settings to discover your Ghostfolio accounts, set up Nextcloud Talk webhooks, and tweak your algorithmic thresholds.  
* **Notifications Tab:** View a persistent ledger of all system-generated events (Earnings alerts, Insider trades, System maintenance). Filter by one or more event types at once (e.g. Crash and Moonshot together) and/or toggle Unread Only — your filter selection is remembered across visits.  
* **Update Data:** Click **"↻ Update Analysis"** to trigger the background data engine manually, or rely on your configured APScheduler rules.  
* **Deep Dive:** Click on any ticker to view the detailed Quantamental analysis, interactive Plotly charts, and live algorithmic candlestick pattern overlays.

## **📱 Mobile-Responsive UI (Bootstrap 5 + DataTables Responsive)**

The interface uses **Bootstrap 5.3** with the **DataTables Responsive** extension so every page is usable on both a large desktop and a phone. All front-end libraries (Bootstrap, jQuery, DataTables + Responsive) are **vendored locally** under `static/vendor/` — the self-hosted app does not depend on any CDN at runtime. On wide screens the full data set is shown; on narrow screens DataTables Responsive collapses the lowest-priority columns into an expandable per-row detail panel, so nothing is lost — only progressively hidden. The dark theme is reproduced through Bootstrap's CSS variables (no Sass build).

The portfolio page additionally adapts to narrow screens (≤ 768 px): the macro-index cards collapse to two compact rows of four (hiding UK 10Y Gilt and US 30Y Yield); the summary strip shows Total Investment, Market Value, and P&L in a three-column grid; the yield-threat bar stacks US above UK; and the holdings table shows only the five most essential columns (Ticker, Price, Change, Global Value, Global P&L). Tapping any row slides open a detail panel with Company Name, 50-day and 200-day trends, Sentiment, Score, Setups/Tags, and Signal — one row at a time.

On desktop, both the Portfolio and Watchlist tables offer a **🔖 Columns** picker (in the page toolbar) for choosing exactly which columns are visible, grouped by category — Fundamentals, Technicals, Classification, Scores, Targets, Risk (X-ray), Earnings Volatility, and Position Sizing — on top of the standard column set. Next to it, a **👁 Views** picker lets you save the current column selection as a named preset, apply it later with one click, and rename/overwrite/delete presets freely. Both pages ship with 3 built-in views out of the box — Fundamentals & Quality, Technical Signals, and Position Targets — and you can add as many of your own as you like. All selections are saved per-page and follow you across browsers/devices. The table header also stays pinned to the top of the viewport while scrolling, using the browser's own scrollbar (no separate inner scroll area). All three features are desktop-only; on a phone the table keeps its normal Responsive collapse behavior described above.

## **🏠 Home Assistant & iFrame Integration (Embed Mode)**

If you want to display your Portfolio or Watchlist on an external dashboard (such as Home Assistant, MagicMirror, or Grafana), you can use the built-in **Embed Mode**.

By appending a simple URL parameter, the system will automatically hide the top navigation bar, title, timestamp, and action buttons, leaving only the ultra-compact data table and the search/filter controls. This makes it perfect for clean, edge-to-edge iframe integration.

**Embed URLs:**

* **Portfolio:** http://localhost:8090/portfolio?embed=true  
* **Watchlist:** http://localhost:8090/watchlist?embed=true

*(Note: Replace localhost with your actual server IP if hosting on a network device like a Raspberry Pi or NAS).*

**Avoiding the login screen (Embed Token):** a browser normally has no session cookie when loading an embedded iframe from a different origin (e.g. Home Assistant's own domain), so a plain `?embed=true` URL redirects to the login page inside the iframe instead of showing the dashboard. To skip login for embedded pages, generate an **Embed Token** from **Settings → User Account** and append it as an `embed_token` parameter:

* **Portfolio:** http://localhost:8090/portfolio?embed=true&embed_token=\<your-embed-token\>
* **Watchlist:** http://localhost:8090/watchlist?embed=true&embed_token=\<your-embed-token\>

The embed token only bypasses login for `GET` requests to the embeddable pages (`/portfolio`, `/watchlist`, `/stock/{ticker}`) when `embed=true` is also present — it grants no access to any other page or `/api/*` endpoint, unlike the general-purpose API Key used by the Native Home Assistant Integration below. Generating a new embed token immediately invalidates the old one.

**Example Home Assistant Webpage Card Configuration:**
```
type: iframe
url: http://192.168.1.71:8090/portfolio?embed=true&embed_token=<your-embed-token>
aspect_ratio: 100%
```

*(Omit `&embed_token=...` if you're relying on an already-logged-in session in the same browser instead — see above.)*

### **Native Home Assistant Integration (Sensors)**

Beyond the iframe embed above, a dedicated companion project — [Stock Analysis Project](Stock_Analysis_Project_ha_integration/) — installs as a proper HACS custom component and pulls your portfolio totals (value, gain, FX-adjusted gain, Time-Weighted Return, dividends), per-account metrics, per-holding data (market value, gain, dividends, RSI, moving-average trend, earnings date, plus optional low/high price targets — settable from either Home Assistant or the app's own Position Targets box on the Stock Detail page), Pension/House account valuations, and market-wide macro/sentiment signals (Fear & Greed Index, Market Regime, US/UK market classification, US 10Y Treasury and UK 10Y Gilt yield threat level, US Treasury Auction Demand) straight into Home Assistant as native sensors, rather than an embedded webpage. It requires generating an API key from **Settings → User Account** in this app and pointing the integration at this instance's URL. It also automatically skips polling live-market-dependent data (portfolio/account/holdings) while both the UK and US markets are closed, so it isn't re-fetching unchanged prices overnight. See that project's own README for installation and entity details.

## **⚙️ Running as a Background Service (Linux)**

For a true production environment, you should configure the dashboard to run as a systemd background service. This ensures the app boots automatically, runs its internal APScheduler tasks flawlessly, and automatically recovers if it crashes.

### **1. Create the Service File**

Open your terminal and create a new systemd service file:

`sudo nano /etc/systemd/system/stock_analysis_project.service`

### **2. Add the Configuration**

Paste the following block into the file. 

**Important:** Replace yourusername with your actual Linux username, and verify the paths match where you cloned the repository.
```
[Unit]  
Description=Quantamental Stock Analysis Dashboard  
After=network.target

[Service]  
User=yourusername  
Group=www-data  
WorkingDirectory=/home/yourusername/Stock_Analysis_Project

# Point explicitly to the Python executable inside your virtual environment  
ExecStart=/home/yourusername/Stock_Analysis_Project/venv/bin/python main.py

Restart=always  
RestartSec=5  
Environment="PYTHONUNBUFFERED=1"

[Install]  
WantedBy=multi-user.target
```


### **3. Enable and Start the Service**

Run these commands to tell Linux to reload its service list, enable the app to start on boot, and spin it up immediately:
```
sudo systemctl daemon-reload  
sudo systemctl enable stock_analysis_project  
sudo systemctl start stock_analysis_project
```

### **🛠️ Useful Service Commands**

Once deployed as a service, you can manage the dashboard via the Web UI Settings tab, or using standard Linux commands:

* **Check if it's running:** `sudo systemctl status stock_analysis_project  `
* **Restart after manual code updates:** `sudo systemctl restart stock_analysis_project  `
* **View live server logs:** `sudo journalctl -u stock_analysis_project -f`

## **🔒 Nginx Reverse Proxy & Security Headers**

For production deployments it is strongly recommended to place the dashboard behind an Nginx reverse proxy. This adds HTTPS termination, security headers, and clickjacking protection.

### **1. Basic Nginx Site Configuration**

Create a new site config (e.g. `/etc/nginx/sites-available/quantamental`):

```nginx
server {
    listen 443 ssl;
    server_name your.domain.com;

    ssl_certificate     /etc/ssl/certs/your_cert.pem;
    ssl_certificate_key /etc/ssl/private/your_key.pem;

    # --- Security Headers ---

    # Prevent search engines from indexing the dashboard
    add_header X-Robots-Tag none;

    # Force HTTPS for 2 years (only add once SSL is confirmed working)
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload";

    # Control which sites can embed the dashboard in an iframe.
    # List every trusted origin explicitly — 'self' allows the app to embed itself,
    # the two Home Assistant origins allow the HA dashboard iframe card.
    # See the Home Assistant section below if you need to add more origins.
    add_header Content-Security-Policy "frame-ancestors 'self' http://192.168.1.x:8123 https://ha.domain.com;";

    # Prevent MIME-type sniffing attacks
    add_header X-Content-Type-Options nosniff;

    # Legacy XSS filter (belt-and-braces for older browsers)
    add_header X-XSS-Protection "1; mode=block";

    # Prevent the dashboard URL leaking in Referer headers on outbound links
    add_header Referrer-Policy "no-referrer";

    # --- Proxy to the FastAPI app ---
    location / {
        proxy_pass         http://127.0.0.1:8090;
        proxy_set_header   Host              $host;
        proxy_set_header   X-Real-IP         $remote_addr;
        proxy_set_header   X-Forwarded-For   $proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto $scheme;
    }
}

# Redirect plain HTTP to HTTPS
server {
    listen 80;
    server_name your.domain.com;
    return 301 https://$host$request_uri;
}
```

Enable the site and reload Nginx:
```bash
sudo ln -s /etc/nginx/sites-available/quantamental /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### **2. Home Assistant iframe Embedding**

The `frame-ancestors` directive controls which external sites are permitted to embed the dashboard in an `<iframe>`. Without it, any website could embed your dashboard (clickjacking risk).

List every origin that needs to embed the app — both origins for Home Assistant if it is accessible via local IP **and** an external HTTPS domain:

```nginx
add_header Content-Security-Policy "frame-ancestors 'self' http://192.168.1.x:8123 https://ha.domain.com;";
```

If Home Assistant is also reachable via a local hostname, add that too:

```nginx
add_header Content-Security-Policy "frame-ancestors 'self' http://192.168.1.x:8123 https://ha.domain.com http://homeassistant.local:8123;";
```

> **Note:** `X-Frame-Options` is a legacy header that does not support multiple origins and is superseded by `frame-ancestors`. Do not use both — if `frame-ancestors` is present, browsers ignore `X-Frame-Options`.

### **3. Self-Signed Certificate (LAN only)**

If the dashboard is only accessible on your local network and you do not have a public domain, you can generate a self-signed certificate:

```bash
sudo openssl req -x509 -nodes -days 3650 -newkey rsa:2048 \
  -keyout /etc/ssl/private/quantamental.key \
  -out /etc/ssl/certs/quantamental.crt \
  -subj "/CN=quantamental.local"
```

Use these paths in the `ssl_certificate` / `ssl_certificate_key` directives above. Your browser will show a warning on first visit — add a permanent exception to dismiss it.

---

## **📚 Built-in Glossary**

Not a quantitative expert? The dashboard includes a built-in educational glossary page and interactive HTML tooltips that explain exactly what metrics like MACD Reversals, Relative Strength vs S&P 500, Bullish Engulfing patterns, and Peter Lynch PEG mean in plain English.

**🎓 Glossary Learning:** a spaced-repetition study mode, reachable via the Learn button next to the Glossary page header. Every glossary term becomes a study card, scheduled with a Leitner-box reinforcement system so weaker terms come up for review more often than ones you already know — starting with market fundamentals and building up to the app's more advanced engines and metrics. Progress (new/learning/strong/learned/weak) is tracked persistently; there are no points or scores to chase. An "Unlock All" checkbox lets you jump straight into any level out of order, and a "Study All Levels" checkbox lets Start Session itself pull new cards from locked levels once the current level runs out — both leave the underlying course order and unlock progress untouched.


## Support & Disclaimer

**⚠️ Disclaimer: Use at Your Own Risk**

This custom integration is a personal project and is provided strictly "as is" and without warranty of any kind. By choosing to install and use this integration, you acknowledge and agree to the following:

* **Personal Project Disclosure:** I am not a professional developer, nor do I specialize in finance or stock markets. The sole purpose of this repository is to assist me with managing my personal portfolio and to visualize data in ways that exceed Ghostfolio's native capabilities.
* **Coding Bias & Market Focus:** I mainly trade on the UK and US stock markets. As a result, the code contains specific logic to address issues unique to London-traded stocks (such as the "Pence vs. Pounds" glitch). While the integration is designed to work with other markets, it has not been tested for them. There may be unhandled errors related to local currency conversions or data formatting in other regions.
* **No Support Provided:** The author does not provide technical support, setup assistance, or troubleshooting guidance. 
* **No Liability:** The author takes absolutely no responsibility for any damage, data loss, misuse, system instability, or any other issues caused by the installation or operation of this software. This software is for informational and educational purposes only. It is not financial advice. The proprietary scoring system, candlestick recognition, and ATR Stop-Loss calculations are mathematical models, not guarantees of market performance. Always do your own due diligence before trading.
* **Community Driven:** You are free to fork, modify, and use this integration however you see fit. If you encounter bugs, you are welcome to submit a Pull Request, but do not expect immediate fixes or dedicated maintenance.

## **🙌 Credits & Acknowledgments**

[AI4Finance-Foundation:](https://github.com/AI4Finance-Foundation) A massive thank you for the architectural inspiration behind FinRL's market regime switching, and FinGPT's approach to robust NLP sentiment analysis.

[leorigasaki/stock-market-prediction-engine:](https://www.google.com/search?q=https://github.com/leorigasaki/stock-market-prediction-engine&authuser=1) Credit for the core mathematical inspiration governing the Time-Series Walk-Forward validation, feature extraction logic, and the structural foundation of the XGBoost/Random Forest soft-voting ensemble used in this project.

[namuan/trading-utils:](https://github.com/namuan/trading-utils/) Parts of this project's structural inspiration and specific script logic were adapted from this excellent repository. A huge thank you to the author for their open-source contributions to the quantitative trading community!