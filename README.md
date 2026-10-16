# **📈 Quantamental Portfolio Dashboard**

Self-hosted web application that merges **Quantitative Analysis** (algorithmic momentum, trend-following, candlestick patterns) with **Fundamental Analysis** (valuation, balance sheet health, and market sentiment), enhanced by **Machine Learning** and **Institutional Tail-Risk Management**.

Designed for Linux environments, this system manages your portfolio through its own Built-in Accounts (native transaction ledger, no external tracker required), with optional live-sync support for a [Ghostfolio](https://ghostfol.io/) instance if you already run one. It scrapes multi-dimensional market data via Yahoo Finance and generates an interactive dashboard using FastAPI and Plotly.

Please note that this is a hobby project not an investment platform.

## **✨ Core Features**

Full detail on every feature lives in the in-app Glossary and `assets/` docs — this list is a scannable index, not a manual.

**Prediction & Signals**
* **Ensemble ML Prediction Engine** — XGBoost + Random Forest soft-voting classifier scoring 10-day upside probability.
* **Entry & Exit Zone Analysis** — Volume Profile, Keltner Channel Z-Score, and ML Quantile Price Bands, with a **Position Targets** box to set your own buy/sell alerts.
* **Proprietary Scoring (0–100)** — composite technical grade from MA alignment, RSI, volatility contraction, MACD, and OBV.
* **Hierarchical Candlestick Recognition** — 11 scored candlestick patterns across three tiers, tagged inline and on the macro chart.
* **Pattern Detection** — daily swing-pattern scan (Head & Shoulders, Double Top/Bottom, Flags, Pennants, Triangles, Wedges, Volatility Squeeze, NR4/NR7, Parabolic Stretch, Divergence, candlestick triggers) via an extensible per-family registry.
* **Signal Pillar Confluence** — flags a ticker when Technical, Statistical, and ML signals independently agree.
* **Regime-Weighted Conviction Score** — a composite 0–100 score reweighted by the current market regime.
* **Buy Recommendation** — bullish Pillar Confluence gated by a minimum reward:risk ratio from ATR-based stop math.
* **Predicted Movers** — leaderboard ranked by ML-predicted forward move for your Portfolio, Watchlist or the wider universe, with a tracked prediction-accuracy page.
* **Stable Shortlist** — two slow-moving weekly shortlists (ML Upside and Quant Score) for your Portfolio and Watchlist, with limited swaps, a minimum hold, a sector cap, optional Settings presets, optional Portfolio/Watchlist columns and a forward-only track record.
* **Score History & Forward Returns** — tracks 3/6/12-month forward returns per historical signal bucket.
* **Earnings Volatility** — signed post-earnings drift stats plus implied-vs-historical move edge scoring.

**Risk & Portfolio Diagnostics**
* **Institutional Tail-Risk Management** — 1-day Historical-Simulation VaR and CVaR at 95% confidence.
* **Portfolio X-ray** — in-page risk report: beta, volatility, drawdown, VaR, concentration, sector/geographic exposure.
* **Portfolio Heat Index (Risk Orchestrator)** — single 0–100 traffic-light risk score per account, plus a per-ticker Risk Contribution tier and a Pre-Trade Check.
* **Historical Stress Tester** — simulates the portfolio through GFC 2008, Dot-com 2000, COVID-19, and the 2022 inflation shock.
* **Market Regime (HMM + Market Stress IF)** — Gaussian HMM Bull/Chop/Crash classifier plus an Isolation Forest market-stress score.
* **Market Trap & Recovery Monitor** — post-crash lifecycle detector (Bull Trap, Bear Trap, Capitulation, Wyckoff Accumulation) with tracked accuracy.
* **Alert Confidence Referee (pilot)** + **Cross-Engine Alert Referee** — meta-labeling classifiers that can veto low-quality alerts, Shadow or Active mode.
* **Bubble Radar** — composite valuation-euphoria score across seven metrics with tracked prediction accuracy.
* **Forensic Screener** — monthly Piotroski F-Score, Altman Z-Score, and Beneish M-Score across holdings.
* **Pairs Spread Monitor** — statistical-arbitrage z-score signal on correlated same-currency pairs.
* **Sector-Relative Momentum** — ranks each stock's 63- and 126-session return against its sector peers, with a Reports page, optional Portfolio/Watchlist columns and a matching AI-prompt comparison. The nightly update refreshes price history for the whole scored universe so every peer group is measured over the same sessions.
* **Macro Regime & Yield Curve Allocator** — classifies the economy into 5 regimes and scores portfolio alignment.
* **FX Drag Analyzer** — decomposes USD-position GBP return into equity return and FX effect.
* **Sovereign Debt Auction Monitor** — flags weak US Treasury auction demand vs. a rolling baseline.

**Portfolio & Accounts**
* **Auto-Syncing Portfolio (Ghostfolio, opt-in)** — live holdings sync with selective account discovery and multi-currency P&L.
* **Built-in Accounts (Native Ledger)** — self-hosted Trading/House/Pension/Watchlist accounts with a full transaction ledger, purchase and dividend FX calculation from the broker total, CSV import/export, Auto Top-up, UK Treasury Bill tracking, and an Account Price Scraper for House/Pension valuations.
* **Change Period (1D/5D/1M/6M/YTD/1Y)** — togglable lookback window on the Portfolio/Watchlist Change column.
* **Pre-Market / After-Hours Prices** — optional display of the latest extended-hours tick alongside the settled close.
* **Monte Carlo Wealth Simulator** — 1,000 correlated GBM paths projecting portfolio wealth 10/20/30 years out.
* **Portfolio Tearsheet** — native quantstats-equivalent performance report (Sortino, Calmar, drawdown analytics, and more).
* **Portfolio Optimizer** — Min-Variance / Max-Sharpe suggested allocations next to an Equal Weight baseline, with the change from your current weights and an efficient-frontier chart. Long-Only mode (the default) keeps every suggestion holdable with a per-ticker Weight Cap and an optional Cash Reserve; Unconstrained mode shows the pure textbook maths. Returns are converted to your base currency with each day's exchange rate, so US and UK holdings are compared fairly. A Strategy picker adds any Strategy Backtester rule as another target mix, with the buy/sell amounts needed to reach it. A growth chart draws one line per mix so they can be compared on the same history.
* **Strategy Backtester** — Replays rules such as Buy-and-Hold, Rebalanced Equal Weight, your current portfolio weights, Inverse-Volatility Weighting, Tolerance-Band Rebalancing and rolling versions of the Optimizer's Steadiest and Best Reward-for-Risk mixes over the price history the app already holds. Decisions use only past data, trades happen at the next close, and trading costs, cash and weight drift are included, with a benchmark alongside. It tests today's tickers over the past, so it compares rules rather than forecasting; an optional separate ~5-year history can be prepared for the tickers you choose, and a basket mixing currencies can be converted to your base currency using each day's exchange rate. Past runs are saved and can be reopened. A *Show explanations* option explains each strategy step by step using your current settings, and the Portfolio Optimizer and Strategy Backtester open each other so a strategy you like in one can be tested or turned into buy/sell amounts in the other.
* **Watchlist Analytics & Selection Tools** — Quality Grade, Market Reports tags, Trap/Bubble flags, heatmap, sector/score scatter.
* **Custom Display Name Override** — set a personal display label for any ticker.
* **Ticker Notes** — timestamped free-text research notes on any ticker.

**Market Data & Alerts**
* **Zero-LLM Market Sentiment Pulse** — FinBERT NLP scoring of live news headlines (-1.0 to +1.0).
* **Turbulence-Aware Macro Regimes** — S&P/VIX-based Normal/Volatile/Crash classification feeding downstream risk tools.
* **Crash & Moonshot Alerts** — 5-minute intraday scan for crash/parabolic-spike conditions during market hours.
* **Markets Page** — global indexes/commodities/FX overview with session-aware dynamic ordering and spot/futures swaps.
* **Market Sentiment & Insider Tracking** — CNN Fear & Greed vs. S&P 500, plus SEC Form 4 insider-buying scans.
* **Yield vs Nominal GDP** — the 10-year US Treasury and UK Gilt yield charted against nominal GDP growth on the Market Sentiment page, with a one-line summary in the Treasury and Gilt warning boxes; context for whether rising yields are growth-driven, not a signal.
* **Market Reports** — seven cross-universe screeners (Quality Compounders, GARP Tenbaggers, Quality on Sale, Sector Trends, Relative Strength Leaders, Mean Reversion, Dividend Harvest).
* **ETF Price Predictor** — configurable holdings-weighted + OLS next-session open predictor with tracked accuracy.
* **Nextcloud Talk Integration** & **Unified Notification Settings** — rich push alerts routed per job/alert across log/in-app/Nextcloud channels.

**Platform & Infrastructure**
* **Multi-Dimensional Data Engine** — 2-year daily, 1-day intraday, and deep fundamentals data via Yahoo Finance.
* **Built-in Task Scheduler** — fully autonomous APScheduler background jobs, no external cron, with the latest 20 scheduled and manual runs available from each job name in the Settings matrix.
* **Crash-Proof Local Storage & Maintenance** — Parquet + SQLite storage with weekly automated pruning/defrag. Maintenance also restores URTH and VWRL.L to the Watchlist for Pension benchmark history and adds a Stock Detail reminder when either ticker has no note.
* **System Health Check Engine** — daily validation of scheduling/ML-coverage config with UI banner alerts.
* **Workflow Monitor** — dependency flow-chart of every scheduled job with traffic-light status and conflict detection.
* **Claude MCP Server** — a read-only `/mcp` endpoint so Claude (Claude Code, Claude Desktop) can query your accounts in natural language instead of you copying prompts across. It has its own key, `MCP_API_KEY` in `.env`, separate from the Home Assistant API key, and refuses every request until you set it. See `assets/mcp_integration.md`.
* **Backup & Recovery** — scheduled or on-demand archive of the DB (a consistent snapshot, not a copy of the live file), data and models to a local folder or NFS share, with a checksum-verified restore.

Watchlist Dashboard:
<img width="1891" height="893" alt="watchlist_dash" src="https://github.com/user-attachments/assets/0ecfe656-45b0-46a9-bc7a-9b9ee3886536" />


Market Sentiment Page:
<img width="1891" height="941" alt="market_sentiment" src="https://github.com/user-attachments/assets/6a11d1ee-5e92-47be-ad5e-92592fc79224" />


Holding detailed view:
<img width="1891" height="847" alt="detailed_view" src="https://github.com/user-attachments/assets/bff422da-435a-4cbe-b152-7ad08b198bbd" />


## **🚀 Installation & Setup**

### **1. Prerequisites**

You must have **Python 3.13 or higher** installed on your system.

### **2. Clone and Install**

Clone the repository and install the required dependencies using a virtual environment:
```bash
git clone https://github.com/alfwro13/Stock_Analysis_Project.git
cd Stock_Analysis_Project  
python3 -m venv venv  
source venv/bin/activate  
pip install --require-hashes -r requirements.lock
```

### **3. Initial Configuration**

The Quantamental system features an automated configuration engine. You do not need to manually build the settings file from scratch.

1. **Bootstrap the System:** Simply start the server for the first time by running `python main.py`. The engine will automatically detect a fresh install and generate a fully structured `config.json` file in your root directory.
2. **Configure via Web UI:** Open your browser and navigate to **http://localhost:8090**. Go to the **⚙️ Settings** tab.
3. **Connect Your Portfolio:** Enter your `GHOSTFOLIO_URL` and `API_TOKEN`, then click **"Save & Apply"**. An `https://` Ghostfolio is checked against its certificate; if it uses a self-signed or private-CA certificate, set `REQUESTS_CA_BUNDLE` in `.env` to that CA's PEM file. Plain `http://` LAN addresses work as before.

*Alternatively, you can manually edit the generated `config.json` file in your code editor.*

**Note:** Ensure your `BASE_CURRENCY` (e.g., GBP, USD, EUR) is set correctly in the Settings. This ensures that foreign assets are mathematically converted to your local currency using live FX rates for accurate P&L calculations.

**Font Sizes:** Adjust font sizes for major UI elements in Settings → Core System & Currencies → Font Sizes. Five elements are individually configurable — navigation menu items, data table cells, form controls, action buttons, and section/panel headers — each via a pixel-size dropdown. Changes persist across restarts via `config.json` and take effect on the next page load (no server restart required).

**Yahoo Finance Dual-Stack Routing:** The Advanced Network & Socket Binding panel in Settings exposes three routing modes for Yahoo Finance traffic:
- **IPv4 only** (`YAHOO_USE_IPV4: true`, `YAHOO_USE_IPV6: false`): standard OS routing — the default.
- **IPv6 only** (`YAHOO_USE_IPV4: false`, `YAHOO_USE_IPV6: true`): all requests are bound to the address in `YAHOO_IPV6_ADDRESS`. On a hard IPv6 fault, the session falls back to IPv4 for the remainder of the process lifetime and fires a Nextcloud alert.
- **Dual round-robin** (both `true`): alternates between IPv4 and IPv6 on successive calls to spread load and bypass per-IP rate limits.
The **Yahoo Finance API Usage** panel immediately below shows daily request counts, interface breakdown, HTTP 429 hits, error counts, and a separate yfinance-logged count for the past 8 days (sourced from `GET /api/system/yahoo-api-stats`) — the latter tracks ERROR-level lines the `yfinance` library itself logs (e.g. no data for a ticker/period, a 404 on a module Yahoo doesn't support for that instrument) without raising an exception, so they show up in the Log Viewer but wouldn't otherwise count as a request failure. Click a row to open a detail chart in a new tab, breaking that day's requests into 15-minute intervals stacked by which scheduled job was running at the time (or "Manual / On-Demand" for requests triggered by browsing a page).

**Request Timing:** Portfolio, Watchlist, Stock Detail, intraday refresh, and Home Assistant account responses expose stage durations in the browser's `Server-Timing` header. Slow requests and event loop pauses are logged for navigation troubleshooting; lag warnings include the sampled project function and its callers when available. The timing logs omit tickers and financial values. Blocking work in the portfolio, market, account, refresh, settings, diagnostics, notification, report, and analysis routes runs in request workers so a slow query or upstream call does not freeze unrelated navigation.

Portfolio, Watchlist and Stock Detail use cached FX and historical data during navigation, with visible FX freshness and unavailable values when required conversion data is missing or too old. Cache age limits are configurable under `PERFORMANCE` in `config.json`; see [Cached Navigation](assets/system_architecture.md#cached-navigation). Explicit Refresh waits for its FX refresh attempt to complete. On Watchlist, click the FX timestamp beside Prices to view pair details and Force Refresh progress.

Stock Detail's **Refresh** button offers **Refresh Values** and **Repair Data**. The repair dialog checks an incorrect daily price bar against fresh Yahoo data and also accepts manually verified OHLCV when Yahoo remains wrong. Corrections remain in effect after later data refreshes. Dependent indicator history and, for held or watched tickers, the portfolio risk figures are refreshed automatically.

**Network & Request Hardening:** Settings → Core System & Currencies also holds Bind Host, Secure Cookies, Allowed Hosts and Embed Frame Ancestors (restart required). The app answers only to the listed host names (`localhost` is always allowed), sends its own security headers (including a Content-Security-Policy whose `frame-ancestors` lists the embed origins), and marks its cookies `Secure` automatically when the Server URL is https.  See the Nginx section below.

**Account Price Scraper Allowed Hosts:** the House/Pension price scraper fetches `https://` pages only and refuses any address on your own network (private, loopback or link-local) by default. To scrape a page you host on the LAN, add its host name or IP under Settings → Core System & Currencies → Account Price Scraper Allowed Hosts (exact entries, comma-separated; any port on a listed host is allowed). It takes effect on the next scrape, with no restart.

**File Logging:** To capture the full application log to disk, enable `FILE_LOGGING` in Settings → Core System & Currencies. Key options:
- `ENABLED` — toggle file logging on/off without restarting the server
- `LEVEL` — minimum severity written to the file (`DEBUG` | `INFO` | `WARNING` | `ERROR` | `CRITICAL`); the console stays at INFO regardless
- `DAYS_TO_KEEP` — number of daily rotated files to retain (default 30)
- `ARCHIVE` — gzip-compress rotated files to save disk space
- `LOG_DIR` — directory for log files, relative to the project root and kept inside it (default `logs/`); an absolute path or `..` is refused

The active log is always `logs/app.log`. Rotated files are named `app.log.YYYY-MM-DD` (or `.gz` if archive is enabled). Changes take effect immediately without a restart. A line break inside a logged value is written as `\n`, so one record is always one line, and API keys or tokens that appear in a logged URL are masked as `***`.

Once file logging is enabled, click **📄 Open Log Viewer** in the same Settings panel (or navigate to `/log-viewer` directly) to open a live log viewer in a new browser tab. The viewer displays the last 500 lines on load and then streams new lines in real time (equivalent to `tail -f`); a **Load Full File** button loads the entire active log file on demand. You can filter by severity level (DEBUG / INFO / WARNING / ERROR / CRITICAL) and search across all visible lines — your severity selection is remembered across page reloads.


### **Security — Dashboard Credentials**

On first start, `config.py` writes default credentials (`admin` / `changeme`) to `.env` and immediately forces a password change on first login. Passwords are stored as PBKDF2-SHA256 hashes (`DASHBOARD_PASSWORD_HASH` in `.env`); the plaintext key is cleared after the first change and the default is never written back.

To configure email-based self-service password reset:
1. Go to **Settings → User Account → Account Email** and save your email address.
2. Add SMTP credentials to `.env`: `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASS` (optional `SMTP_FROM`). If SMTP is not configured, reset links are delivered via Nextcloud Talk; if neither works no link is issued.

**Stored credentials are write-only in Settings.** The SMTP password, Nextcloud app password and conversation token, Ghostfolio token, FRED key and HuggingFace token are never sent back to the browser: each field shows only whether a value is stored, an empty field keeps it, and a **Clear the stored value on save** checkbox removes it. The API key and embed token are displayed once, when generated. The Account Price Scraper's custom request headers follow the same rule.

**Passwords and sessions.** A new password must be at least 12 characters and not a commonly used one. Changing or resetting the password signs out every other browser, and **Settings → User Account → Log Out Everywhere** signs out all of them on demand. Five wrong passwords in a row lock sign-in for 30 seconds, doubling up to 15 minutes; failures are logged and the lockout raises a **Failed Login Alert** notification. API-key access is not affected.

**Sensitive Settings actions ask for your password.** Saving settings or credentials, pulling updates, restarting, restoring a backup and rotating secrets prompt for the dashboard password once; that unlocks them for 5 minutes. The API key works only on the endpoints the Home Assistant integration calls; every other route and page refuses it, so it cannot reach these actions or the Settings page. The Claude MCP Server uses a separate `MCP_API_KEY` that works only on `/mcp` (generate it with `python -c "import secrets; print(secrets.token_hex(32))"` and add it to `.env`); neither key works on the other's routes.
3. The **Forgot password?** link on the login page initiates the flow. Reset links expire after 1 hour, a new request invalidates older links, and the reply never reveals whether the email matched. Link requests are limited to 3 per hour.

If you are locked out entirely, see `assets/system_recovery_and_architecture.md` — *Password Reset Procedures* for the recovery methods, including a console script (`python reset_admin_password.py`). There is no unauthenticated browser reset page.

**File permissions:** on every start the app restricts its project folder to its own user (`0700`), keeps `.env` and `config.json` at `0600`, and removes group/other write access under `data/` and `models/`. Set `UMask=0077` in the service file below (and leave out `Group=`) so files created later stay private; after changing the unit, run `sudo systemctl daemon-reload` and restart.

### **4. Initial AI Training (Cold Start)**
Before the system can provide Machine Learning predictions, it must build its historical training set.

1. Start the server (python main.py) and navigate to http://localhost:8090.
2. Go to the ⚙️ Settings tab.
3. Scroll down to the 🧠 Machine Learning & AI Engine section.
4. Click "⚙️ Initialize AI Engine (Backfill & Train)".

This will run securely in the background. It downloads 2 years of daily data for a curated list of ~250 Blue Chip stocks plus your portfolio, engineers the vectorized features, and trains the global ml_ensemble.joblib model. You can track its progress in the Notifications tab.


### **5. Keeping Dependencies Updated**

`requirements.txt` lists the packages the app uses directly. `requirements.lock` is generated from it and pins every package, including each transitive one, with its SHA-256 hashes. The lock is what you install (`pip install --require-hashes -r requirements.lock`), what **Restart Service** installs and what CI tests, so every machine runs exactly the files that were tested and pip refuses any file that does not match. [Dependabot](https://docs.github.com/code-security/dependabot) opens a weekly pull request for any outdated package in `requirements.txt` (`.github/dependabot.yml`). Every pull request — Dependabot's or your own — is automatically checked against the full regression suite by GitHub Actions CI (`.github/workflows/tests.yml`) before it's safe to merge. `assets/dependencies.md` documents what each dependency is actually used for and what to test after a major-version upgrade.

When `requirements.txt` changes on `main`, the **Refresh dependency lock** workflow (`.github/workflows/refresh-lock.yml`) regenerates the lock and pushes it to a `refresh-lock` branch; the run's summary links to the page where you open the pull request, which then runs the tests as usual. On a Dependabot pull request the same workflow commits the regenerated lock to the PR branch itself, so CI runs against the packages that would actually be installed (it needs a `LOCK_REFRESH_TOKEN` Dependabot secret; see `assets/dependencies.md`). It keeps every version that still satisfies `requirements.txt`, so only what changed moves. To regenerate it yourself, on a machine with [uv](https://docs.astral.sh/uv/):

```bash
uv pip compile requirements.txt --universal --python-version 3.13 --generate-hashes -o requirements.lock
```

Add `--upgrade` to take the newest allowed version of every package. To drop Python 3.13 or move to a newer Python once its wheels exist, change `--python-version` and run the command again. A weekly **Dependency audit** workflow (`.github/workflows/pip-audit.yml`) checks the lock against published security advisories.

The app itself also checks, on every startup, whether the packages actually installed in your virtual environment still match the pins in `requirements.lock`, and logs/notifies (Settings → Notifications) if they've drifted — for example if the lock was updated but `pip install --require-hashes -r requirements.lock` was never re-run. This catches drift regardless of how the server was last restarted.

When packages are out of date, **Restart Service** (Settings → System Updates & Power) installs them first and restarts only when that succeeds. A large upgrade such as PyTorch can take several minutes; the page shows the progress, and a second click does not start a second install. If the installation fails or times out the service is **not** restarted: the Settings warning stays and the notification gives the exact command to run in a shell on the server (`<venv>/bin/python3 -m pip install --require-hashes -r requirements.lock`), after which you restart the service.

Persisted scikit-learn models carry a version sidecar. After a scikit-learn upgrade, startup rejects incompatible artifacts and queues only their owning training jobs through APScheduler. Affected model families run sequentially, beginning within seconds of startup, while their inference paths remain unavailable until compatible artifacts are written. Settings → System Updates & Power warns about this before restart when Pull Latest from GitHub detects a changed scikit-learn pin.

Settings → System Updates & Power also shows a **Restart pending** banner whenever the code on disk is newer than the code the running service started with — after Pull Latest from GitHub or a manual `git pull` — and clears it once the service restarts.

## **💻 Usage & The Web UI**

To start the server, simply run the main application file. The system will automatically build the SQLite database on its first boot.

`python main.py`

* Open your web browser and navigate to **http://localhost:8090** (or your server's IP address).  
* **Settings Tab:** Navigate to ⚙️ Settings to discover your Ghostfolio accounts, set up Nextcloud Talk webhooks, and tweak your algorithmic thresholds.  
* **Notifications Tab:** View a persistent ledger of all system-generated events (Earnings alerts, Insider trades, System maintenance). Filter by one or more event types at once (e.g. Crash and Moonshot together) and/or toggle Unread Only — your filter selection is remembered across visits.  
* **Update Data:** Click **"↻ Update Analysis"** to trigger the background data engine manually, or rely on your configured APScheduler rules.  
* **Deep Dive:** Click on any ticker to view the detailed Quantamental analysis, interactive Plotly charts, and live algorithmic candlestick pattern overlays.

## **📱 Mobile-Responsive UI (Bootstrap 5 + DataTables Responsive)**

The interface uses **Bootstrap 5.3** with the **DataTables Responsive** extension so every page is usable on both a large desktop and a phone. All front-end libraries (Bootstrap, jQuery, DataTables + Responsive, Plotly, Mermaid) are **vendored locally** under `static/vendor/` — the self-hosted app does not depend on any CDN at runtime. On wide screens the full data set is shown; on narrow screens DataTables Responsive collapses the lowest-priority columns into an expandable per-row detail panel, so nothing is lost — only progressively hidden. The dark theme is reproduced through Bootstrap's CSS variables (no Sass build).

The portfolio page additionally adapts to narrow screens (≤ 768 px): the macro-index cards collapse to two compact rows of four (hiding UK 10Y Gilt and US 30Y Yield); the summary strip shows Total Investment, Market Value, and P&L in a three-column grid; the yield-threat bar stacks US above UK; and the holdings table shows only the five most essential columns (Ticker, Price, Change, Global Value, Global P&L). Tapping any row slides open a detail panel with Company Name, 50-day and 200-day trends, Sentiment, Score, Setups/Tags, and Signal — one row at a time.

On desktop, both the Portfolio and Watchlist tables offer a **🔖 Columns** picker (in the page toolbar) for choosing exactly which columns are visible, grouped by category — Fundamentals, Technicals, Classification, Scores, Targets, Risk (X-ray), Earnings Volatility, and Position Sizing — on top of the standard column set. Next to it, a **👁 Views** picker lets you save the current column selection and order (and any Advanced Filter, see below) as a named preset, apply it later with one click, and rename/overwrite/delete presets freely — deleting now asks for confirmation first, and whichever saved view currently matches your live column selection, order and filter is marked "✓ Active" in the list. Both pages ship with 3 built-in views out of the box — Fundamentals & Quality, Technical Signals, and Position Targets — and you can add as many of your own as you like. All selections are saved per-page and follow you across browsers/devices. The table header also stays pinned to the top of the viewport while scrolling, using the browser's own scrollbar (no separate inner scroll area). Drag column headers with a mouse on desktop to rearrange them; Ticker stays visible and first. The order saves automatically per page, including hidden columns’ positions, and is restored when a saved View is applied. Dragging is disabled on narrow screens and for touch input. The Columns/Views controls and sticky header are desktop-only; on a phone the table keeps its normal Responsive collapse behavior described above.

Both pages also offer a **🔍 Advanced Filter** — next to the length control on the Portfolio page, next to **+ Add Ticker** on Watchlist — for building multi-condition row filters on top of the existing quick-filter dropdowns (Signal, Setup Tags, Score, Sector). Each condition picks any column (core or optional), an operator appropriate to that column's data type (numeric: `>`, `<`, `=`, between, is empty…; text: contains, equals…; date: before/after/on; boolean: is true/false), and a value. Once you have two or more conditions, a Match toggle lets you choose whether all of them must match (AND) or any one of them (OR) — e.g. "Low Target is not empty OR High Target is not empty" to list every ticker with any Position Target set. A filter built ad hoc (not saved to a view) stays applied only in that browser via local storage; saving it as a named View (via the Save Current action in the Views picker) persists it to your account so it's reapplied automatically whenever that view is selected.

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

The embed token only bypasses login for `GET` requests to the embeddable pages (`/portfolio`, `/watchlist`, `/stock/{ticker}`) when `embed=true` is also present — it grants no access to any other page or `/api/*` endpoint, unlike the API Key used by the Native Home Assistant Integration below, which is limited to that integration's own endpoints. Generating a new embed token immediately invalidates the old one. The token is shown only once, when it is generated — copy it then; Settings afterwards shows only that a token is set.

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
UMask=0077  
WorkingDirectory=/home/yourusername/Stock_Analysis_Project

# Point explicitly to the Python executable inside your virtual environment  
ExecStart=/home/yourusername/Stock_Analysis_Project/venv/bin/python main.py

Restart=always  
RestartSec=5  
Environment="PYTHONUNBUFFERED=1"
Environment="HF_HUB_OFFLINE=1"

# Optional - uncomment only if a reverse proxy on a different machine forwards requests to the app (see below)
# Environment="FORWARDED_ALLOW_IPS=<proxy-ip>"

[Install]  
WantedBy=multi-user.target
```


**`HF_HUB_OFFLINE=1`:** the FinBERT sentiment model is loaded from the local Hugging Face cache at a pinned revision, and this setting stops the app from contacting the Hub on every load. On a fresh install, download the model once with the variable unset (`HF_HUB_OFFLINE=0 venv/bin/python -c "from huggingface_hub import snapshot_download; snapshot_download('ProsusAI/finbert', revision='<NLP_FINBERT_REVISION in constants.py>', allow_patterns=['*.json', '*.txt', 'model.safetensors'])"`), run as the service user, before starting the service. The filter fetches only the safetensors weights and tokenizer files (about 420 MB); the app refuses to load a pickle `.bin`. The model weights carry the CC BY-NC-SA 4.0 license (see Credits & Acknowledgments).

**Optional - `FORWARDED_ALLOW_IPS`:** the app only trusts the client address that a reverse proxy forwards (`X-Forwarded-For`) when the proxy connects from the same machine. Leave the line commented out if Nginx runs on the same machine as the dashboard. If the proxy runs on a different machine, uncomment it and set it to that machine's address (a comma-separated list is accepted); otherwise every visitor appears to come from the proxy, they share one login rate-limit bucket, and real client addresses never reach the logs. Never set it to `*`: any host that can reach the app could then forge its address. Changing it needs `sudo systemctl daemon-reload` and a restart of the service.

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

For production deployments it is strongly recommended to place the dashboard behind an Nginx reverse proxy. This adds HTTPS termination and keeps the app's port off the network.

The app sends its own security headers (a Content-Security-Policy including `frame-ancestors`, `X-Content-Type-Options`, `Referrer-Policy`, `X-XSS-Protection`, `X-Robots-Tag`), so Nginx only needs to add HSTS. It also rejects any request whose `Host` header is not on its allow-list — before it will answer through your domain, add that domain under **Settings → Core System & Currencies → Allowed Hosts** (restart required; `localhost` is always allowed).

### **1. Basic Nginx Site Configuration**

Create a new site config (e.g. `/etc/nginx/sites-available/quantamental`):

```nginx
server {
    listen 443 ssl;
    server_name your.domain.com;

    ssl_certificate     /etc/ssl/certs/your_cert.pem;
    ssl_certificate_key /etc/ssl/private/your_key.pem;

    # Force HTTPS for 2 years (only add once SSL is confirmed working).
    # Every other security header is sent by the app itself.
    add_header Strict-Transport-Security "max-age=63072000; includeSubDomains; preload" always;

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

`proxy_set_header Host $host` matters: the app checks that header against **Allowed Hosts**. If the proxy runs on another machine, also set **Bind Host** to the server's LAN address and block the app's port from every client except the proxy.

Enable the site and reload Nginx:
```bash
sudo ln -s /etc/nginx/sites-available/quantamental /etc/nginx/sites-enabled/
sudo nginx -t
sudo systemctl reload nginx
```

### **2. Home Assistant iframe Embedding**

The `frame-ancestors` part of the app's Content-Security-Policy controls which external sites may embed the dashboard in an `<iframe>`. Without it, any website could embed your dashboard (clickjacking risk).

List every origin that needs to embed the app under **Settings → Core System & Currencies → Embed Frame Ancestors** (comma-separated, restart required) — both origins for Home Assistant if it is accessible via local IP **and** an external HTTPS domain, plus a local hostname if you use one:

```
http://192.168.1.x:8123, https://ha.domain.com, http://homeassistant.local:8123
```

The dashboard's own origin (`'self'`) is always included. Do not also add a `Content-Security-Policy` or `X-Frame-Options` header in Nginx: the browser would enforce both, and `X-Frame-Options` cannot list multiple origins.

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

[ProsusAI/finbert:](https://huggingface.co/ProsusAI/finbert) The FinBERT model that scores news sentiment. It is not part of this repository; the app downloads it from Hugging Face. Its weights are licensed by their owner under [CC BY-NC-SA 4.0](https://creativecommons.org/licenses/by-nc-sa/4.0/) (non-commercial, share-alike), which is separate from this project's license, and the model is provided without warranty. Anyone running this software is responsible for confirming their use complies with those terms.
