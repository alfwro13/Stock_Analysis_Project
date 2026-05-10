# insider_engine.py
import os
import json
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from database import get_connection
from config import PORTFOLIO_PATH, WATCHLIST_PATH, load_config

def send_nextcloud_message(message_text, config_data):
    """Sends a direct text payload to Nextcloud Talk using dynamic configurations."""
    url = config_data.get("NEXTCLOUD_URL", "")
    token = config_data.get("CONVERSATION_TOKEN", "")
    user = config_data.get("BOT_USERNAME", "")
    pwd = config_data.get("APP_PASSWORD", "")
    
    api_endpoint = f"{url}/ocs/v2.php/apps/spreed/api/v1/chat/{token}"
    payload = {"message": message_text}
    headers = {
        "OCS-APIRequest": "true", 
        "Content-Type": "application/json", 
        "Accept": "application/json"
    }
    try:
        response = requests.post(
            api_endpoint, 
            headers=headers, 
            json=payload, 
            auth=(user, pwd), 
            timeout=10
        )
        response.raise_for_status()
        return True
    except Exception as e:
        print(f"[ERROR] Failed to send Nextcloud insider message: {e}")
        return False

def get_tickers_from_json(filepath, is_watchlist=False):
    """Safely extracts tickers from either portfolio.json or watchlist.json."""
    if not os.path.exists(filepath):
        return []
    try:
        with open(filepath, 'r') as f:
            data = json.load(f)
            if is_watchlist:
                return data.get("watchlist", [])
            else:
                return [v.get('ticker') for v in data.values() if v.get('ticker')]
    except Exception:
        return []

def run_insider_alert():
    """Scrapes recent SEC Form 4 filings for massive insider buying."""
    try:
        print("\n[DEBUG] Starting Insider Trading Alert Check...")
        
        # 1. Load Configurations
        config = load_config()
        insider_cfg = config.get("NOTIFICATIONS", {}).get("INSIDER_TRADING", {})
        
        enable_portfolio = insider_cfg.get("ENABLED_PORTFOLIO", False)
        enable_watchlist = insider_cfg.get("ENABLED_WATCHLIST", False)
        min_value = int(insider_cfg.get("MIN_VALUE", 50000))
        days_back = int(insider_cfg.get("DAYS_BACK", 7))
        
        if not enable_portfolio and not enable_watchlist:
            return True, "Insider checks skipped (Both toggles disabled)."

        # 2. Build target list
        target_tickers = set()
        if enable_portfolio:
            target_tickers.update(get_tickers_from_json(PORTFOLIO_PATH, False))
        if enable_watchlist:
            target_tickers.update(get_tickers_from_json(WATCHLIST_PATH, True))
            
        target_tickers = [t for t in target_tickers if t and not t.startswith('0P')]
        if not target_tickers:
            return True, "No valid equity tickers found to check."

        # 3. Cache company names from SQLite to format the message nicely
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ','.join('?' for _ in target_tickers)
        cursor.execute(f"SELECT ticker, company_name FROM stock_signals WHERE ticker IN ({placeholders})", list(target_tickers))
        name_map = {row['ticker']: row['company_name'] for row in cursor.fetchall()}
        
        cutoff_date = pd.to_datetime(datetime.now() - timedelta(days=days_back), utc=True)
        alerts_sent = 0

        # 4. Scrape & Filter
        for ticker in target_tickers:
            try:
                stock = yf.Ticker(ticker)
                insider_df = stock.insider_transactions
                
                if insider_df is None or insider_df.empty:
                    continue
                    
                # Standardize Yahoo's Dataframe
                if 'Start Date' in insider_df.columns:
                    insider_df['Start Date'] = pd.to_datetime(insider_df['Start Date'], utc=True, errors='coerce')
                else:
                    continue # Cannot evaluate without a date
                    
                # Check for Transaction Type (Sometimes called 'Text' or 'Transaction')
                col_action = 'Transaction' if 'Transaction' in insider_df.columns else 'Text'
                if col_action not in insider_df.columns:
                    continue
                    
                # Clean Value column (Remove $ and , to convert to float)
                if 'Value' in insider_df.columns:
                    insider_df['Clean_Value'] = pd.to_numeric(
                        insider_df['Value'].astype(str).replace(r'[\$,]', '', regex=True), errors='coerce'
                    )
                else:
                    continue

                # Clean Shares column
                if 'Shares' in insider_df.columns:
                    insider_df['Clean_Shares'] = pd.to_numeric(
                        insider_df['Shares'].astype(str).replace(r'[,]', '', regex=True), errors='coerce'
                    )
                else:
                    insider_df['Clean_Shares'] = 0

                # 5. Apply Core Logic Filters
                # Filter 1: Date within bounds
                recent_buys = insider_df[insider_df['Start Date'] >= cutoff_date].copy()
                if recent_buys.empty: continue
                
                # Filter 2: Transaction is a Purchase
                recent_buys = recent_buys[recent_buys[col_action].astype(str).str.contains('Buy|Purchase', case=False, na=False)]
                
                # Filter 3: Value exceeds limit
                major_buys = recent_buys[recent_buys['Clean_Value'] >= min_value]
                
                # 6. Dispatch Alerts
                for idx, row in major_buys.iterrows():
                    comp_name = name_map.get(ticker, ticker)
                    exec_name = row.get('Insider', 'Unknown Executive')
                    position = row.get('Position', 'Insider')
                    val_str = f"${row['Clean_Value']:,.2f}"
                    share_str = f"{row['Clean_Shares']:,.0f}" if row['Clean_Shares'] > 0 else "Unknown"
                    date_str = row['Start Date'].strftime('%Y-%m-%d')
                    
                    msg = (
                        f"🚨 **INSIDER BUYING DETECTED** 🚨\n"
                        f"Stock: {comp_name} ({ticker})\n"
                        f"Executive: {exec_name} ({position})\n"
                        f"Action: Bought {share_str} shares\n"
                        f"Value: {val_str}\n"
                        f"Date: {date_str}"
                    )
                    
                    if send_nextcloud_message(msg, config):
                        alerts_sent += 1
                        
            except Exception as e:
                print(f"[ERROR] Evaluating Insider trades for {ticker}: {e}")
                
        return True, f"Insider check complete. Triggered {alerts_sent} alerts based on ${min_value:,} limit."

    except Exception as e:
        print(f"[ERROR] Fatal crash in run_insider_alert: {e}")
        return False, f"System Crash: {str(e)}"