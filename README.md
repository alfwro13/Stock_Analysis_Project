# 📈 Quantamental Portfolio Dashboard

An institutional-grade, self-hosted web application that merges **Quantitative Analysis** (algorithmic momentum and trend-following) with **Fundamental Analysis** (valuation, balance sheet health, and market sentiment). 

Designed for Linux environments, this system pulls live holdings from your [Ghostfolio](https://ghostfol.io/) instance, scrapes multi-dimensional market data via Yahoo Finance, and generates an interactive, Bloomberg-style dashboard using FastAPI and Plotly.

---

## ✨ Core Features

* **Auto-Syncing Portfolio:** Integrates directly with Ghostfolio via API to automatically pull your live holdings and calculate accurate Cost Basis and Unrealized P&L across different currencies (e.g., handling LSE GBp vs GBP conversions).
* **Multi-Dimensional Data Engine:** Downloads 2-year macro daily data, 1-day 5-minute intraday data, and deep fundamental `.info` payloads.
* **Crash-Proof Local Storage:** Persists heavy time-series data locally using highly compressed `.parquet` files and `SQLite3`, ensuring lightning-fast load times and zero API rate-limiting.
* **Proprietary Scoring (0-100):** A custom algorithm that grades stocks based on Moving Average alignment, RSI, Volatility Contraction (3-Weeks-Tight), and On-Balance Volume.
* **Mathematical Risk Management:** Automatically calculates a dynamic Stop-Loss for every asset based on its 14-day Average True Range (ATR).
* **Peter Lynch Fair Value:** Calculates custom PEG ratios based on actual earnings growth to identify undervalued assets.

---

## 🛠️ Architecture

* **Backend:** Python 3.10+, FastAPI, Uvicorn
* **Data Engineering:** `pandas`, `pyarrow`, `yfinance`
* **Quantitative Math:** `ta` (Technical Analysis Library)
* **Frontend:** Jinja2 Templates, HTML/CSS/JS (Vanilla), Plotly (Interactive Charting)

---

## 🚀 Installation & Setup

### 1. Prerequisites
You must have **Python 3.10 or higher** installed on your system. 

### 2. Clone and Install
Clone the repository and install the required dependencies using a virtual environment:

```bash
git clone [https://github.com/yourusername/stock_analysis_project.git](https://github.com/yourusername/stock_analysis_project.git)
cd stock_analysis_project
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

### 3. Configure Secrets (config.json)

You must create a config.json file in the root directory to store your Ghostfolio credentials and specify the web server port. Note: This file is ignored by git for security.

Create config.json:
```bash
{
    "GHOSTFOLIO_URL": "http://YOUR_GHOSTFOLIO_IP:PORT",
    "API_TOKEN": "your_long_lived_ghostfolio_security_token",
    "PORT": 8090
}
```

### 💻 Usage

To start the server, simply run the main application file. The system will automatically build the SQLite database on its first boot.

`python main.py`


- Open your web browser and navigate to **http://localhost:8090** (or your server's IP address).
- Click **"⬇️ Sync Ghostfolio"** to pull your latest portfolio and watchlist.
- Click **"↻ Update Analysis"** to trigger the background data engine. Check your terminal to see the fetching progress.
- **Refresh** the page to view your fully rendered dashboard. Click on any ticker to view the detailed Quantamental analysis and interactive Plotly charts.

### 📚 Built-in Glossary

Not a quantitative expert? The dashboard includes a built-in educational /glossary page and interactive HTML tooltips that explain exactly what metrics like MACD, Relative Strength vs S&P 500, and On-Balance Volume mean in plain English.

### ⚠️ Disclaimer

This software is for informational and educational purposes only. It is not financial advice. The proprietary scoring system and ATR Stop-Loss calculations are mathematical models, not guarantees of market performance. Always do your own due diligence before trading.