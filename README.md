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
* **Predicted Movers** — leaderboard ranked by ML-predicted forward move, with a tracked prediction-accuracy page.
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
* **Macro Regime & Yield Curve Allocator** — classifies the economy into 5 regimes and scores portfolio alignment.
* **FX Drag Analyzer** — decomposes USD-position GBP return into equity return and FX effect.
* **Sovereign Debt Auction Monitor** — flags weak US Treasury auction demand vs. a rolling baseline.

**Portfolio & Accounts**
* **Auto-Syncing Portfolio (Ghostfolio, opt-in)** — live holdings sync with selective account discovery and multi-currency P&L.
* **Built-in Accounts (Native Ledger)** — self-hosted Trading/House/Pension/Watchlist accounts with a full transaction ledger, CSV import/export, Auto Top-up, UK Treasury Bill tracking, and an Account Price Scraper for House/Pension valuations.
* **Change Period (1D/5D/1M/6M/YTD/1Y)** — togglable lookback window on the Portfolio/Watchlist Change column.
* **Pre-Market / After-Hours Prices** — optional display of the latest extended-hours tick alongside the settled close.
* **Monte Carlo Wealth Simulator** — 1,000 correlated GBM paths projecting portfolio wealth 10/20/30 years out.
* **Portfolio Tearsheet** — native quantstats-equivalent performance report (Sortino, Calmar, drawdown analytics, and more).
* **Portfolio Optimizer** — closed-form Min-Variance / Max-Sharpe suggested allocation with an efficient-frontier chart.
* **Watchlist Analytics & Selection Tools** — Quality Grade, Market Reports tags, Trap/Bubble flags, heatmap, sector/score scatter.
* **Custom Display Name Override** — set a personal display label for any ticker.
* **Ticker Notes** — timestamped free-text research notes on any ticker.

**Market Data & Alerts**
* **Zero-LLM Market Sentiment Pulse** — FinBERT NLP scoring of live news headlines (-1.0 to +1.0).
* **Turbulence-Aware Macro Regimes** — S&P/VIX-based Normal/Volatile/Crash classification feeding downstream risk tools.
* **Crash & Moonshot Alerts** — 5-minute intraday scan for crash/parabolic-spike conditions during market hours.
* **Markets Page** — global indexes/commodities/FX overview with session-aware dynamic ordering and spot/futures swaps.
* **Market Sentiment & Insider Tracking** — CNN Fear & Greed vs. S&P 500, plus SEC Form 4 insider-buying scans.
* **Market Reports** — seven cross-universe screeners (Quality Compounders, GARP Tenbaggers, Quality on Sale, Sector Trends, Relative Strength Leaders, Mean Reversion, Dividend Harvest).
* **ETF Price Predictor** — configurable holdings-weighted + OLS next-session open predictor with tracked accuracy.
* **Nextcloud Talk Integration** & **Unified Notification Settings** — rich push alerts routed per job/alert across log/in-app/Nextcloud channels.

**Platform & Infrastructure**
* **Multi-Dimensional Data Engine** — 2-year daily, 1-day intraday, and deep fundamentals data via Yahoo Finance.
* **Built-in Task Scheduler** — fully autonomous APScheduler background jobs, no external cron.
* **Crash-Proof Local Storage & Maintenance** — Parquet + SQLite storage with weekly automated pruning/defrag.
* **System Health Check Engine** — daily validation of scheduling/ML-coverage config with UI banner alerts.
* **Workflow Monitor** — dependency flow-chart of every scheduled job with traffic-light status and conflict detection.
* **Backup & Recovery** — scheduled or on-demand archive of DB/data/models to a local folder or NFS share, with restore.

Watchlist Dashboard:
<img width="1891" height="893" alt="watchlist_dash" src="https://github.com/user-attachments/assets/0ecfe656-45b0-46a9-bc7a-9b9ee3886536" />


Market Sentiment Page:
<img width="1891" height="941" alt="market_sentiment" src="https://github.com/user-attachments/assets/6a11d1ee-5e92-47be-ad5e-92592fc79224" />


Holding detailed view:
<img width="1891" height="847" alt="detailed_view" src="https://github.com/user-attachments/assets/bff422da-435a-4cbe-b152-7ad08b198bbd" />


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

On desktop, both the Portfolio and Watchlist tables offer a **🔖 Columns** picker (in the page toolbar) for choosing exactly which columns are visible, grouped by category — Fundamentals, Technicals, Classification, Scores, Targets, Risk (X-ray), Earnings Volatility, and Position Sizing — on top of the standard column set. Next to it, a **👁 Views** picker lets you save the current column selection (and any Advanced Filter, see below) as a named preset, apply it later with one click, and rename/overwrite/delete presets freely — deleting now asks for confirmation first, and whichever saved view currently matches your live column/filter selection is marked "✓ Active" in the list. Both pages ship with 3 built-in views out of the box — Fundamentals & Quality, Technical Signals, and Position Targets — and you can add as many of your own as you like. All selections are saved per-page and follow you across browsers/devices. The table header also stays pinned to the top of the viewport while scrolling, using the browser's own scrollbar (no separate inner scroll area). All three features are desktop-only; on a phone the table keeps its normal Responsive collapse behavior described above.

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
