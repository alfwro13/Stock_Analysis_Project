# **📈 Quantamental Portfolio Dashboard**

Self-hosted web application that merges **Quantitative Analysis** (algorithmic momentum, trend-following, candlestick patterns) with **Fundamental Analysis** (valuation, balance sheet health, and market sentiment).

Designed for Linux environments, this system pulls live holdings from your [Ghostfolio](https://ghostfol.io/) instance, scrapes multi-dimensional market data via Yahoo Finance, and generates an interactive, Bloomberg-style dashboard using FastAPI and Plotly.

Please note that this is a hobby project not an investment platform.

## **✨ Core Features**

* **Auto-Syncing Portfolio (Multi-Account):** Integrates directly with Ghostfolio via API to automatically pull your live holdings. Now supports opt-in account discovery, allowing you to selectively sync specific accounts and calculate accurate global VWAP Cost Basis and Unrealized P&L across different currencies.  
* **Multi-Dimensional Data Engine:** Downloads 2-year macro daily data, 1-day 5-minute intraday data, and deep fundamental .info payloads.  
* **Nextcloud Talk Integration:** A comprehensive alert ecosystem that pushes rich notifications directly to your Nextcloud Talk app.  
* **Hierarchical Candlestick Recognition:** Algorithmically detects and scores Tier-1 (Morning Star), Tier-2 (Engulfing), and Tier-3 (Hammer/Shooting Star) reversal patterns on live intraday data.  
* **Intraday Orchestrator:** High-frequency 5-minute scanning that detects mathematical "Crash" conditions (heavy drops below SMA) and "Moonshot" conditions (parabolic spikes, All-Time Highs) during active market hours.  
* **Market Sentiment & Insider Tracking:** Maps the CNN Fear & Greed Index against the S&P 500 (with visual chart generation) and scrapes SEC Form 4 filings for major insider buying aligning with algorithmic dips.  
* **Proprietary Scoring (0-100):** A custom algorithm that grades stocks based on Moving Average alignment, RSI, Volatility Contraction (3-Weeks-Tight), MACD Reversals, and On-Balance Volume.  
* **Built-in Task Scheduler:** Fully autonomous background scheduling via APScheduler. No external cron jobs required. Manage execution times directly from the web UI.  
* **In-App Management:** Update configurations, test webhooks, perform git pull repository updates, and restart the background service directly from the Settings GUI.  
* **Crash-Proof Local Storage & Maintenance:** Persists heavy time-series data locally using highly compressed .parquet files and SQLite3. An automated Maintenance Engine prunes orphaned files and defragments the database weekly.

Watch list Dashboard:
<img width="2247" height="1633" alt="watchlist_dash" src="https://github.com/user-attachments/assets/22fcd68d-b6a9-4f5d-aacc-69bd88db0bfc" />
Market Sentiment Page:
<img width="2265" height="1630" alt="market_sentiment" src="https://github.com/user-attachments/assets/1c70fd70-6cdf-4ee3-855b-f70d9ca3c5a4" />
Holding detailed view:
<img width="867" height="1805" alt="detailed_view" src="https://github.com/user-attachments/assets/35beacd7-df5e-435c-aa55-dd4f087e5b7b" />




## **🛠️ Architecture**

* **Backend:** Python 3.10+, FastAPI, Uvicorn, APScheduler  
* **Data Engineering:** pandas, pyarrow, yfinance, sqlite3  
* **Quantitative Math:** ta (Technical Analysis Library), numpy  
* **Frontend:** Jinja2 Templates, HTML/CSS/JS (Vanilla), Plotly & Matplotlib (Interactive Charting)  
* **Integrations:** Ghostfolio API, Nextcloud Talk (WebDAV & OCS API)

## **🚀 Installation & Setup**

### **1. Prerequisites**

You must have **Python 3.10 or higher** installed on your system.

### **2. Clone and Install**

Clone the repository and install the required dependencies using a virtual environment:
```
git clone https://github.com/alfwro13/Stock_Analysis_Project.git  
cd Stock_Analysis_Project  
python3 -m venv venv  
source venv/bin/activate  
pip install -r requirements.txt
```

### **3. Initial Configuration**

You must create a config.json file in the root directory to store your credentials. You can start with the bare minimum and configure the rest later via the Web UI.

Create config.json:
```
{  
    "GHOSTFOLIO_URL": "http://YOUR_GHOSTFOLIO_IP:PORT",  
    "API_TOKEN": "your_long_lived_ghostfolio_security_token",  
    "PORT": 8090,  
    "BASE_CURRENCY": "GBP"  
}
```
**Note:** BASE_CURRENCY ensures that foreign assets (like USD stocks) are mathematically converted to your local currency using live FX rates for accurate P&L calculation.

## **💻 Usage & The Web UI**

To start the server, simply run the main application file. The system will automatically build the SQLite database on its first boot.

`python main.py`

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

