# earnings_engine.py
import os
import json
import requests
from datetime import datetime
from database import get_connection
from config import PORTFOLIO_PATH, load_config

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
        print(f"[ERROR] Failed to send Nextcloud earnings message: {e}")
        return False

def run_earnings_alert():
    """Reads the local DB and sends alerts for portfolio items with upcoming earnings."""
    try:
        print("\n[DEBUG] Starting Earnings Alert Check...")
        
        # 1. Load Configurations dynamically
        config = load_config()
        earnings_cfg = config.get("NOTIFICATIONS", {}).get("EARNINGS_ALERTS", {})
        days_ahead = int(earnings_cfg.get("DAYS_AHEAD", 7))
        alert_type = earnings_cfg.get("ALERT_TYPE", "daily") # "daily" or "once"

        # 2. Load Portfolio Tickers
        if not os.path.exists(PORTFOLIO_PATH):
            return False, "Portfolio file not found."
        
        with open(PORTFOLIO_PATH, 'r') as f:
            try:
                portfolio = json.load(f)
            except json.JSONDecodeError:
                return False, "Portfolio JSON is corrupted."
                
        # Filter out mutual funds (0P) and ensure the ticker exists
        tickers = [data.get('ticker') for data in portfolio.values() 
                   if data.get('ticker') and not data.get('ticker').startswith('0P')]

        if not tickers:
            return True, "No valid equity tickers found in portfolio."

        # 3. Query Local Database
        conn = get_connection()
        cursor = conn.cursor()
        
        placeholders = ','.join('?' for _ in tickers)
        query = f"SELECT ticker, company_name, next_earnings_date FROM stock_signals WHERE ticker IN ({placeholders})"
        cursor.execute(query, tickers)
        rows = cursor.fetchall()
        
        today = datetime.now().date()
        alerts_sent = 0
        
        # 4. Evaluate Dates
        for row in rows:
            ticker = row['ticker']
            name = row['company_name']
            earnings_date_str = row['next_earnings_date']
            
            if not earnings_date_str or earnings_date_str == 'Unknown':
                continue
                
            try:
                e_date = datetime.strptime(earnings_date_str, '%Y-%m-%d').date()
                days_to_earnings = (e_date - today).days
                
                send_alert = False
                
                # Logic branch for configuration preferences
                if alert_type == "once":
                    if days_to_earnings == days_ahead:
                        send_alert = True
                elif alert_type == "daily":
                    if 0 <= days_to_earnings <= days_ahead:
                        send_alert = True
                        
                if send_alert:
                    if days_to_earnings == 0:
                        time_str = "TODAY"
                    elif days_to_earnings == 1:
                        time_str = "TOMORROW"
                    else:
                        time_str = f"in {days_to_earnings} days"
                        
                    msg = f"📅 *Upcoming Earnings Report*\n\nStock: {name} ({ticker})\nDate: {earnings_date_str} ({time_str})"
                    
                    # Log the alert to the local SQLite Notification Center
                    cursor.execute(
                        "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)",
                        ("Earnings", msg)
                    )
                    conn.commit()
                    
                    # Dispatch to Nextcloud
                    if send_nextcloud_message(msg, config):
                        alerts_sent += 1
                        
            except Exception as e:
                print(f"[ERROR] Evaluating earnings date for {ticker}: {e}")
                
        conn.close()
        return True, f"Earnings check complete. Triggered {alerts_sent} alerts based on current settings."
    
    except Exception as e:
        print(f"[ERROR] Fatal crash in run_earnings_alert: {e}")
        return False, f"System Crash: {str(e)}"