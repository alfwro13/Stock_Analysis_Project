# **📈 Quantamental Portfolio Dashboard**

Self-hosted web application that merges **Quantitative Analysis** (algorithmic momentum, trend-following, candlestick patterns) with **Fundamental Analysis** (valuation, balance sheet health, and market sentiment).

Designed for Linux environments, this system pulls live holdings from your [Ghostfolio](https://ghostfol.io/) instance, scrapes multi-dimensional market data via Yahoo Finance, and generates an interactive, Bloomberg-style dashboard using FastAPI and Plotly.

## **✨ Core Features**

* **Auto-Syncing Portfolio (Multi-Account):** Integrates directly with Ghostfolio via API to automatically pull your live holdings. Now supports opt-in account discovery, allowing you to selectively sync specific accounts and calculate accurate global VWAP Cost Basis and Unrealized P\&L across different currencies.  
* **Multi-Dimensional Data Engine:** Downloads 2-year macro daily data, 1-day 5-minute intraday data, and deep fundamental .info payloads.  
* **Nextcloud Talk Integration:** A comprehensive alert ecosystem that pushes rich notifications directly to your Nextcloud Talk app.  
* **Hierarchical Candlestick Recognition:** Algorithmically detects and scores Tier-1 (Morning Star), Tier-2 (Engulfing), and Tier-3 (Hammer/Shooting Star) reversal patterns on live intraday data.  
* **Intraday Orchestrator:** High-frequency 5-minute scanning that detects mathematical "Crash" conditions (heavy drops below SMA) and "Moonshot" conditions (parabolic spikes, All-Time Highs) during active market hours.  
* **Market Sentiment & Insider Tracking:** Maps the CNN Fear & Greed Index against the S\&P 500 (with visual chart generation) and scrapes SEC Form 4 filings for major insider buying aligning with algorithmic dips.  
* **Proprietary Scoring (0-100):** A custom algorithm that grades stocks based on Moving Average alignment, RSI, Volatility Contraction (3-Weeks-Tight), MACD Reversals, and On-Balance Volume.  
* **Built-in Task Scheduler:** Fully autonomous background scheduling via APScheduler. No external cron jobs required. Manage execution times directly from the web UI.  
* **In-App Management:** Update configurations, test webhooks, perform git pull repository updates, and restart the background service directly from the Settings GUI.  
* **Crash-Proof Local Storage & Maintenance:** Persists heavy time-series data locally using highly compressed .parquet files and SQLite3. An automated Maintenance Engine prunes orphaned files and defragments the database weekly.

## **🛠️ Architecture**

* **Backend:** Python 3.10+, FastAPI, Uvicorn, APScheduler  
* **Data Engineering:** pandas, pyarrow, yfinance, sqlite3  
* **Quantitative Math:** ta (Technical Analysis Library), numpy  
* **Frontend:** Jinja2 Templates, HTML/CSS/JS (Vanilla), Plotly & Matplotlib (Interactive Charting)  
* **Integrations:** Ghostfolio API, Nextcloud Talk (WebDAV & OCS API)

## **🚀 Installation & Setup**

### **1\. Prerequisites**

You must have **Python 3.10 or higher** installed on your system.

### **2\. Clone and Install**

Clone the repository and install the required dependencies using a virtual environment:

git clone \[https://github.com/alfwro13/Stock\_Analysis\_Project.git\](https://github.com/alfwro13/Stock\_Analysis\_Project.git)  
cd Stock\_Analysis\_Project  
python3 \-m venv venv  
source venv/bin/activate  
pip install \-r requirements.txt

### **3\. Initial Configuration**

You must create a config.json file in the root directory to store your credentials. You can start with the bare minimum and configure the rest later via the Web UI.

Create config.json:

{  
    "GHOSTFOLIO\_URL": "http://YOUR\_GHOSTFOLIO\_IP:PORT",  
    "API\_TOKEN": "your\_long\_lived\_ghostfolio\_security\_token",  
    "PORT": 8090,  
    "BASE\_CURRENCY": "GBP"  
}

**Note:** BASE\_CURRENCY ensures that foreign assets (like USD stocks) are mathematically converted to your local currency using live FX rates for accurate P\&L calculation.

## **💻 Usage & The Web UI**

To start the server, simply run the main application file. The system will automatically build the SQLite database on its first boot.

python main.py

* Open your web browser and navigate to **http://localhost:8090** (or your server's IP address).  
* **Settings Tab:** Navigate to ⚙️ Settings to discover your Ghostfolio accounts, set up Nextcloud Talk webhooks, and tweak your algorithmic thresholds.  
* **Notifications Tab:** View a persistent ledger of all system-generated events (Earnings alerts, Insider trades, System maintenance).  
* **Update Data:** Click **"↻ Update Analysis"** to trigger the background data engine manually, or rely on your configured APScheduler rules.  
* **Deep Dive:** Click on any ticker to view the detailed Quantamental analysis, interactive Plotly charts, and live algorithmic candlestick pattern overlays.

## **🏠 Home Assistant & iFrame Integration (Embed Mode)**

If you want to display your Portfolio or Watchlist on an external dashboard (such as Home Assistant, MagicMirror, or Grafana), you can use the built-in **Embed Mode**.

By appending a simple URL parameter, the system will automatically hide the top navigation bar, title, timestamp, and action buttons, leaving only the ultra-compact data table and the search/filter controls. This makes it perfect for clean, edge-to-edge iframe integration.

**Embed URLs:**

* **Portfolio:** http://localhost:8090/portfolio?embed=true  
* **Watchlist:** http://localhost:8090/watchlist?embed=true

*(Note: Replace localhost with your actual server IP if hosting on a network device like a Raspberry Pi or NAS).*

**Example Home Assistant Webpage Card Configuration:**

type: iframe  
url: \[http://192.168.1.71:8090/portfolio?embed=true\](http://192.168.1.71:8090/portfolio?embed=true)  
aspect\_ratio: 100%

## **⚙️ Running as a Background Service (Linux)**

For a true production environment, you should configure the dashboard to run as a systemd background service. This ensures the app boots automatically, runs its internal APScheduler tasks flawlessly, and automatically recovers if it crashes.

### **1\. Create the Service File**

Open your terminal and create a new systemd service file:

sudo nano /etc/systemd/system/stock\_analysis\_project.service

### **2\. Add the Configuration**

Paste the following block into the file. **Important:** Replace yourusername with your actual Linux username, and verify the paths match where you cloned the repository.

\[Unit\]  
Description=Quantamental Stock Analysis Dashboard  
After=network.target

\[Service\]  
User=yourusername  
Group=www-data  
WorkingDirectory=/home/yourusername/Stock\_Analysis\_Project

\# Point explicitly to the Python executable inside your virtual environment  
ExecStart=/home/yourusername/Stock\_Analysis\_Project/venv/bin/python main.py

Restart=always  
RestartSec=5  
Environment="PYTHONUNBUFFERED=1"

\[Install\]  
WantedBy=multi-user.target

Save and exit (CTRL \+ O, Enter, CTRL \+ X).

### **3\. Enable and Start the Service**

Run these commands to tell Linux to reload its service list, enable the app to start on boot, and spin it up immediately:

sudo systemctl daemon-reload  
sudo systemctl enable stock\_analysis\_project  
sudo systemctl start stock\_analysis\_project

### **🛠️ Useful Service Commands**

Once deployed as a service, you can manage the dashboard via the Web UI Settings tab, or using standard Linux commands:

* **Check if it's running:** sudo systemctl status stock\_analysis\_project  
* **Restart after manual code updates:** sudo systemctl restart stock\_analysis\_project  
* **View live server logs:** sudo journalctl \-u stock\_analysis\_project \-f

## **📚 Built-in Glossary**

Not a quantitative expert? The dashboard includes a built-in educational /glossary page and interactive HTML tooltips that explain exactly what metrics like MACD Reversals, Relative Strength vs S\&P 500, Bullish Engulfing patterns, and Peter Lynch PEG mean in plain English.

## **⚠️ Disclaimer**

This software is for informational and educational purposes only. It is not financial advice. The proprietary scoring system, candlestick recognition, and ATR Stop-Loss calculations are mathematical models, not guarantees of market performance. Always do your own due diligence before trading.