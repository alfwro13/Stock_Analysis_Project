# **📈 Quantamental Portfolio Dashboard**

Self-hosted web application that merges **Quantitative Analysis** (algorithmic momentum, trend-following, candlestick patterns) with **Fundamental Analysis** (valuation, balance sheet health, and market sentiment), enhanced by **Machine Learning** and **Institutional Tail-Risk Management**.

Designed for Linux environments, this system pulls live holdings from your [Ghostfolio](https://ghostfol.io/) instance, scrapes multi-dimensional market data via Yahoo Finance, and generates an interactive dashboard using FastAPI and Plotly.

Please note that this is a hobby project not an investment platform.

## **✨ Core Features**

* **Ensemble Machine Learning Prediction Engine:** Utilizes a soft-voting classifier (XGBoost + Random Forest) trained on historical vectorized features to calculate the probability (0-100%) of an asset returning >3% over the next 10 trading days (entry at T+1 close, exit at T+10 close).
* **Entry & Exit Zone Analysis:** Three complementary methods computed during the daily quant scan and displayed on the stock detail page and portfolio/watchlist tables: (1) **Volume Profile** — a 180-day volume-at-price histogram identifying the Point of Control (POC), Value Area Low/High, and High Volume Nodes (HVNs) that act as institutional support and resistance; (2) **Keltner Channel Z-Score** — measures how many ATRs price is above or below EMA(21), firing an entry signal when price is 2–3 ATRs below a healthy uptrend and an exit signal when overextended above +3 ATRs with RSI > 75; (3) **ML Quantile Price Bands** — two XGBoost quantile regressors (Q10 floor / Q90 ceiling) that predict the 10th and 90th percentile of the 10-day return distribution and convert them to price targets. Exit targets are only shown for portfolio holdings.
* **Institutional Tail-Risk Management:** Dynamically calculates 1-day Historical-Simulation Value at Risk (VaR) and Conditional VaR (Expected Shortfall) at a 95% confidence interval to quantify extreme downside exposure.
* **Zero-LLM Market Sentiment Pulse:** Leverages FinBERT (ProsusAI/finbert) Natural Language Processing (NLP) to read and score live news headlines, quantifying media narratives on a strict -1.0 (Panic) to +1.0 (Euphoria) scale.
* **Turbulence-Aware Macro Regimes:** Actively monitors the S&P 500's historical volatility alongside implied volatility (VIX) to classify the market as Normal, Volatile, or Crash—dynamically altering the quant screener to prioritize 'Flight to Safety' setups during turbulence.
* **Auto-Syncing Portfolio (Multi-Account):** Integrates directly with Ghostfolio via API to automatically pull your live holdings. Now supports opt-in account discovery, allowing you to selectively sync specific accounts and calculate accurate global VWAP Cost Basis and Unrealized P&L across different currencies.  
* **Multi-Dimensional Data Engine:** Downloads 2-year macro daily data, 1-day 5-minute intraday data, and deep fundamental .info payloads.  
* **Nextcloud Talk Integration:** A comprehensive alert ecosystem that pushes rich notifications directly to your Nextcloud Talk app.  
* **Unified Notification Settings:** A single Settings panel ("Notification Settings") that controls, per scheduled job and per alert, which channels each notification is delivered through — the rotating log file, the in-app notification centre, and/or Nextcloud Talk. Replaces the previously scattered per-engine "send to Nextcloud" toggles with one pane of glass; new channels can be added centrally.  
* **Hierarchical Candlestick Recognition:** Algorithmically detects and scores 11 distinct patterns across three tiers on live daily and intraday data. Tier-1 three-candle patterns: Morning Star (+20), Evening Star (−20), and Three White Soldiers (+18). Tier-2 two-candle patterns: Bullish/Bearish Engulfing (±15), Piercing Line (+10), and Bullish/Bearish Harami Cross (±8). Tier-3 single-candle patterns (mutually exclusive): Hammer (+10), Shooting Star (−10), and Doji (0). Pattern scores accumulate additively into the composite score and are clamped to −100…+100. Each detected pattern emits a tag chip with hover tooltip in the dashboard tables and an annotation marker on the macro chart.  
* **Crash & Moonshot Alerts:** High-frequency 5-minute scanning that detects mathematical "Crash" conditions (heavy drops below SMA) and "Moonshot" conditions (parabolic spikes, All-Time Highs) during active market hours.  
* **Market Sentiment & Insider Tracking:** Maps the CNN Fear & Greed Index against the S&P 500 (with visual chart generation) and scrapes SEC Form 4 filings for major insider buying aligning with algorithmic dips.  
* **Proprietary Scoring (0-100):** A custom algorithm that grades stocks based on Moving Average alignment, RSI, Volatility Contraction (3-Weeks-Tight), MACD Reversals, and On-Balance Volume.  
* **Built-in Task Scheduler:** Fully autonomous background scheduling via APScheduler. No external cron jobs required. Manage execution times directly from the web UI.  
* **Crash-Proof Local Storage & Maintenance:** Persists heavy time-series data locally using highly compressed .parquet files and SQLite3. An automated Maintenance Engine prunes orphaned files and defragments the database weekly.
* **System Health Check Engine:** A daily background job (`system_check_engine.py`) that validates scheduling configuration and ML data coverage. Any detected issues (e.g. ML Training scheduled before Backfill, or inference universe too small) surface immediately as a coloured banner at the top of the Settings page and as a notification in the alerts panel. Schedule configurable via `SCHEDULES.SYSTEM_CHECK` in `config.json`.
* **Workflow Monitor:** A dependency flow-chart of every scheduled job in **Settings → Workflow Monitor**. Each job is a box wired to the jobs that produce the data it depends on (from market-universe ingestion all the way through the quant, ML, sentiment, and dip-radar engines), coloured by a traffic-light recency status — green (ran recently), amber (stale / never run / due), red (failed or overdue), grey (disabled). A conflict-detection engine flags scheduling problems: a job set to run *while or before* the upstream job feeding its data is still running (using each job's measured average run time), a job whose upstream is disabled, and jobs that failed or have gone stale. New scheduler jobs appear automatically once declared in the job manifest.
* **Portfolio X-ray (Risk & Diagnostics):** A same-page risk diagnostics view embedded in the Portfolio tab. Click the **🔮 X-ray** button to swap the holdings table for a full risk report without navigating away. The report contains: six headline risk cards (Portfolio Beta vs SWDA.L, Annualised Volatility, Max Drawdown, VaR 95% 1-day, HHI concentration score, Top-5 Weight); three donut charts (Instrument Type, True Sector Exposure with ETF look-through, Geographic Exposure by continent); a colour-coded position concentration bar chart (amber >10%, red >20%); and an income panel (weighted dividend yield + projected annual income) alongside an unrealised gross P&L bar chart. Risk metrics (beta, volatility, correlation) are pre-computed nightly by an APScheduler job (`xray_risk_cache_job`, Mon–Fri 19:00) and cached in three SQLite tables so page load never triggers a live yfinance call. The X-ray is account-aware: switching the account dropdown re-renders in-place, and "Global" nets positions across all active accounts.
* **ETF Price Predictor:** A generic next-session open price predictor for any ETF (`/etf-predictor`). Configure multiple predictors — each specifying an ETF ticker and up to 20 constituent tickers with weights — and the engine predicts the ETF's next opening price using a holdings-weighted basket return (with automatic FX adjustment when the ETF and its constituents are in different currencies) and an OLS regression fallback. Predictions are logged and accuracy (direction accuracy %, MAE, MAPE) is tracked over time per predictor. Predictors can be enabled/disabled and optionally auto-scheduled (twice daily) from Settings → Tools → ETF Price Predictors.
* **Historical Stress Tester:** A standalone tool (`/stress-test`) that simulates how your portfolio would fare during a historical crash — GFC 2008, Dot-com 2000, COVID-19, or the 2022 inflation shock — using beta-adjusted scenario shocks calibrated per crisis. Each holding's estimated monetary loss is computed as `market_drop × beta × sector_multiplier` and displayed in a sortable breakdown alongside a sector impact chart. Supports per-account or combined portfolio scope. No additional data is required beyond the X-ray risk cache.
* **Market Regime (HMM + Market Stress IF):** A dedicated tool (`/market-regime`) that fits a 3-state Gaussian Hidden Markov Model on 5 years of SPY daily log-returns and EWMA volatility, classifying the market into **Bull** (low vol), **Chop** (elevated vol, indecisive), or **Crash** (high vol, negative returns). States are decoded via the Viterbi algorithm and sorted by mean volatility for a stable label ordering across daily retrains. The page shows the current state with confidence, a full 5-year Viterbi history chart with colour-coded regime bands, an empirical transition probability matrix, and per-regime return/volatility statistics. A compact regime pill is also embedded on the Trap Monitor page for immediate context. A Nextcloud alert fires when the regime transitions. The SPY data is cached in a 5-year Parquet file (`data/historical/SPY_hmm.parquet`) with incremental 1-month tail updates to avoid a full re-download on each daily run. Alongside the HMM, a market-wide **Isolation Forest** (`run_market_stress_if()`) scores six daily macro features — VIX level, VIX/MA ratio, HYG return, 10Y yield change, SPY volume z-score, SPY return — producing a `market_stress_score` in [0,1] stored in `market_regimes`. A Nextcloud alert fires when the score exceeds 0.75 for two consecutive days. Alert cooldown configurable via `ALERTS.MARKET_STRESS_ALERTS` in `config.json`. Raw data is cached in `data/historical/market_stress_if.parquet` with incremental updates.
* **Market Trap & Recovery Monitor:** Scans portfolio holdings and a configurable proxy basket for post-crash lifecycle patterns — Bull Trap, Bear Trap (low-conviction breakdown), Capitulation volume climax, and Wyckoff Accumulation phase — serving each signal as CONFIRMED / POSSIBLE / WATCH / SAFE at `/trap-monitor`. Schedule and alert thresholds configurable via `SCHEDULES.TRAP_MONITORS` and `ALERTS.TRAP_MONITOR_ALERTS` in `config.json`. A **prediction accuracy panel** on the same page shows what percentage of past phase assignments resolved correctly at 14-day and 30-day forward-return horizons; results accumulate automatically via a daily fill job (`trap_accuracy_fill_job`).
* **FX Drag Analyzer:** Decomposes each US stock position's GBP return into two components: equity return in USD and FX effect from GBP/USD movement (`/fx-drag`). Shows which positions are genuinely outperforming and which are riding dollar strength. Reference-period modes (YTD, 1-year, 2-year) use existing 2-year Parquet data. **Lifetime mode** uses the actual weighted-average GBP/USD rate at which each position was purchased, derived directly from Ghostfolio trade history — no exchange-rate API required. A compact three-number FX breakdown (Equity USD / FX Effect / Total GBP YTD) also appears inline on each USD stock's detail page inside the "Your Position" box.
* **Forensic Screener:** Monthly institutional-grade accounting forensics across all portfolio and watchlist tickers (`/forensic-screener`). Computes three models from annual financial statements fetched via Yahoo Finance: the **Piotroski F-Score** (0–9; score < 4 flags structural decay across profitability, leverage, and efficiency), the **Altman Z-Score** (bankruptcy risk; Z < 1.1 = distress zone), and the **Beneish M-Score** (earnings manipulation detector; M > −1.78 flags possible manipulation). Scores are stored in `stock_signals` and updated by two monthly APScheduler jobs (data fetch on the 1st at 06:00 UTC, scoring at 07:00 UTC). Nextcloud alerts fire when any portfolio holding breaches a distress threshold. Both jobs are visible in the Workflow Monitor and configurable in Settings → Forensic Screener. Run-Now buttons on both the Settings panel and the Forensic Screener page allow immediate on-demand execution.
* **Bubble Radar:** A valuation-euphoria detector that scans all portfolio and watchlist tickers for signs of speculative overextension (`/bubble-radar`). Seven independent metrics contribute to a composite Bubble Risk Score (0–100): SMA-200 Extension %, 20-day average RSI, P/S ratio, PEG ratio, FCF Yield vs the US real 10-year yield (DFII10), IV call skew (US tickers only), and the SPY vs RSP 20-day return spread (market breadth). Tickers scoring above a configurable Watch threshold (default 70) receive a yellow flag; those above the Bubble threshold (default 85) receive a red flag. Flagged tickers are highlighted inline on each stock's detail page and listed at the dedicated Bubble Radar tool. Prediction accuracy is tracked at 4-, 8-, and 12-week forward horizons and displayed in a History tab. Thresholds and schedule are configurable in Settings → Bubble Radar.
* **Macro Regime & Yield Curve Allocator:** Embedded in the Portfolio X-ray panel (☢ X-ray button). Synthesises live macro signals — 10y–2y yield curve spread, US CPI, high-yield credit spreads, the 10-year TIPS real yield, and the HMM hidden state — into one of five named economic regimes (Risk-On, Late Cycle, Stagflation, Contraction, Recovery), displayed as a 5-box traffic-light strip with the active regime highlighted. Scores portfolio alignment (0–100), shows the cash target as an absolute amount with a user-acknowledgement toggle, and provides a rebalancing delta table. Regime targets are configurable via `REGIME_TARGETS` in `config.json`. Requires the Macro Data Engine to have run at least once.
* **Custom Display Name Override:** On any stock detail page, click the pencil icon next to the company name heading to set a personal display label for that ticker. The override is saved to the database and shown instead of the system name in the portfolio table, watchlist table, and the detail page heading. Click "Reset to default" to restore the original name.
* **Score History & Forward Returns:** A dedicated signal quality tracker page (`/score-history`) that answers the question *"When my algorithm rated a stock STRONG BUY, did it actually go up?"*. Every daily scan writes one row per ticker to the `score_history` table (ticker, date, score, signal label, close price). The page then joins these events against the `quant_signals` price series to compute 3-month, 6-month, and 12-month forward returns at query time — no pre-computation needed. Results are grouped by signal bucket (STRONG BUY through TOXIC/AVOID) and displayed in a summary performance table alongside a full event log. A data availability banner shows exactly when each return horizon becomes resolvable. The page is accessible directly from the Portfolio and Watchlist summary bars, with a `?filter=portfolio` or `?filter=watchlist` query parameter to pre-scope results.

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

**File Logging:** To capture the full application log to disk, enable `FILE_LOGGING` in Settings → Core System & Currencies. Key options:
- `ENABLED` — toggle file logging on/off without restarting the server
- `LEVEL` — minimum severity written to the file (`DEBUG` | `INFO` | `WARNING` | `ERROR` | `CRITICAL`); the console stays at INFO regardless
- `DAYS_TO_KEEP` — number of daily rotated files to retain (default 30)
- `ARCHIVE` — gzip-compress rotated files to save disk space
- `LOG_DIR` — directory for log files, relative to the project root (default `logs/`)

The active log is always `logs/app.log`. Rotated files are named `app.log.YYYY-MM-DD` (or `.gz` if archive is enabled). Changes take effect immediately without a restart.

Once file logging is enabled, click **📄 Open Log Viewer** in the same Settings panel (or navigate to `/log-viewer` directly) to open a live log viewer in a new browser tab. The viewer displays the last 500 lines on load and then streams new lines in real time (equivalent to `tail -f`). You can filter by severity level (DEBUG / INFO / WARNING / ERROR / CRITICAL) and search across all visible lines.


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


## **💻 Usage & The Web UI**

To start the server, simply run the main application file. The system will automatically build the SQLite database on its first boot.

`python main.py`

* Open your web browser and navigate to **http://localhost:8090** (or your server's IP address).  
* **Settings Tab:** Navigate to ⚙️ Settings to discover your Ghostfolio accounts, set up Nextcloud Talk webhooks, and tweak your algorithmic thresholds.  
* **Notifications Tab:** View a persistent ledger of all system-generated events (Earnings alerts, Insider trades, System maintenance).  
* **Update Data:** Click **"↻ Update Analysis"** to trigger the background data engine manually, or rely on your configured APScheduler rules.  
* **Deep Dive:** Click on any ticker to view the detailed Quantamental analysis, interactive Plotly charts, and live algorithmic candlestick pattern overlays.

## **📱 Mobile-Responsive UI (Bootstrap 5 + DataTables Responsive)**

The interface is being migrated to **Bootstrap 5.3** with the **DataTables Responsive** extension so every page is usable on both a large desktop and a phone. All front-end libraries (Bootstrap, jQuery, DataTables + Responsive) are **vendored locally** under `static/vendor/` — the self-hosted app does not depend on any CDN at runtime. On wide screens the full data set is shown; on narrow screens DataTables Responsive collapses the lowest-priority columns into an expandable per-row detail panel, so nothing is lost — only progressively hidden. The dark theme is reproduced through Bootstrap's CSS variables (no Sass build).

The portfolio page additionally adapts to narrow screens (≤ 768 px): the macro-index cards collapse to two compact rows of four (hiding UK 10Y Gilt and US 30Y Yield); the summary strip shows Total Investment, Market Value, and P&L in a three-column grid; the yield-threat bar stacks US above UK; and the holdings table shows only the five most essential columns (Ticker, Price, Change, Global Value, Global P&L). Tapping any row slides open a detail panel with Company Name, 50-day and 200-day trends, Sentiment, Score, Setups/Tags, and Signal — one row at a time.

## **🏠 Home Assistant & iFrame Integration (Embed Mode)**

If you want to display your Portfolio or Watchlist on an external dashboard (such as Home Assistant, MagicMirror, or Grafana), you can use the built-in **Embed Mode**.

By appending a simple URL parameter, the system will automatically hide the top navigation bar, title, timestamp, and action buttons, leaving only the ultra-compact data table and the search/filter controls. This makes it perfect for clean, edge-to-edge iframe integration.

**Embed URLs:**

* **Portfolio:** http://localhost:8090/portfolio?embed=true  
* **Watchlist:** http://localhost:8090/watchlist?embed=true

*(Note: Replace localhost with your actual server IP if hosting on a network device like a Raspberry Pi or NAS).*

**Example Home Assistant Webpage Card Configuration:**
```
type: iframe  
url: http://192.168.1.71:8090/portfolio?embed=true
aspect_ratio: 100%
```
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