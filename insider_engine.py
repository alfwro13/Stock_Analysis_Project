# insider_engine.py
import os
import json
import requests
import yfinance as yf
import pandas as pd
from datetime import datetime, timedelta
from database import get_connection
from config import PORTFOLIO_PATH, WATCHLIST_PATH, load_config

def send_nextcloud_message(message_text: str, config_data: dict) -> bool:
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

def get_tickers_from_json(filepath: str, is_watchlist: bool = False) -> list:
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
    """Scrapes recent SEC Form 4 filings for massive insider buying and aligns with quant scores."""
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

        # 3. Cache company data and quant scores from SQLite
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ','.join('?' for _ in target_tickers)
        
        # Fetching enhanced quantitative data for alignment checks
        query = f"""
            SELECT ticker, company_name, composite_score, atr_stop_loss, current_price 
            FROM stock_signals 
            WHERE ticker IN ({placeholders})
        """
        cursor.execute(query, list(target_tickers))
        
        # Store as a dict of dicts for easy access
        db_data = {}
        for row in cursor.fetchall():
            db_data[row['ticker']] = {
                'company_name': row['company_name'],
                'composite_score': row['composite_score'],
                'atr_stop_loss': row['atr_stop_loss'],
                'current_price': row['current_price']
            }
        
        cutoff_date = pd.to_datetime(datetime.now() - timedelta(days=days_back), utc=True)
        alerts_sent = 0

        # 4. Scrape & Filter
        for ticker in target_tickers:
            try:
                stock = yf.Ticker(ticker)
                
                # Robust extraction logic bypassing yfinance property/method changes
                insider_df = stock.insider_transactions
                if callable(insider_df):
                    insider_df = insider_df()
                elif insider_df is None or (isinstance(insider_df, pd.DataFrame) and insider_df.empty):
                    try:
                        insider_df = stock.get_insider_transactions()
                    except Exception:
                        pass
                
                if insider_df is None or not isinstance(insider_df, pd.DataFrame) or insider_df.empty:
                    continue
                
                insider_df = insider_df.reset_index()

                # Heuristic 1: Find the Date
                date_col = next((col for col in ['Start Date', 'Date', 'Transaction Date'] if col in insider_df.columns), None)
                if not date_col:
                    date_col = next((c for c in insider_df.columns if 'date' in c.lower()), None)
                    
                if date_col:
                    insider_df['Parsed_Date'] = pd.to_datetime(insider_df[date_col], utc=True, errors='coerce')
                else:
                    continue 
                    
                # Heuristic 2: Find the Action/Text 
                col_action = next((col for col in ['Text', 'Transaction', 'Action'] if col in insider_df.columns), None)
                if not col_action:
                    col_action = next((c for c in insider_df.columns if 'text' in c.lower() or 'trans' in c.lower() or 'action' in c.lower()), None)
                
                if not col_action:
                    continue
                    
                # Heuristic 3: Clean Value column
                val_col = next((col for col in ['Value'] if col in insider_df.columns), None)
                if not val_col:
                    val_col = next((c for c in insider_df.columns if 'value' in c.lower()), None)

                if val_col:
                    insider_df['Clean_Value'] = pd.to_numeric(
                        insider_df[val_col].astype(str).replace(r'[\$,]', '', regex=True), errors='coerce'
                    )
                else:
                    continue

                # Heuristic 4: Clean Shares column
                shares_col = next((col for col in ['Shares'] if col in insider_df.columns), None)
                if not shares_col:
                    shares_col = next((c for c in insider_df.columns if 'share' in c.lower()), None)

                if shares_col:
                    insider_df['Clean_Shares'] = pd.to_numeric(
                        insider_df[shares_col].astype(str).replace(r'[,]', '', regex=True), errors='coerce'
                    )
                else:
                    insider_df['Clean_Shares'] = 0

                # 5. Apply Core Logic Filters
                recent_buys = insider_df[insider_df['Parsed_Date'] >= cutoff_date].copy()
                if recent_buys.empty: 
                    continue
                
                recent_buys = recent_buys[recent_buys[col_action].astype(str).str.contains('Buy|Purchase|Acquisition|P -|P-', case=False, na=False)]
                major_buys = recent_buys[recent_buys['Clean_Value'] >= min_value]
                
                # Retrieve Quant Data for this Ticker
                t_data = db_data.get(ticker, {})
                comp_name = t_data.get('company_name', ticker)
                score = t_data.get('composite_score')
                atr_stop = t_data.get('atr_stop_loss')
                curr_price = t_data.get('current_price')

                # Evaluate Quantamental Alignment Conditions
                # Condition 1: High System Score (Strong Momentum & Volume)
                is_bullish_trend = score is not None and score >= 60
                # Condition 2: Deep Value / Oversold (Price < Stop Loss floor)
                is_buying_dip = curr_price is not None and atr_stop is not None and (0 < curr_price < atr_stop)

                # 6. Dispatch Alerts
                for idx, row in major_buys.iterrows():
                    exec_name = row.get('Insider', 'Unknown Executive')
                    position = row.get('Position', 'Insider')
                    val_str = f"${row['Clean_Value']:,.2f}"
                    share_str = f"{row['Clean_Shares']:,.0f}" if row['Clean_Shares'] > 0 else "Unknown"
                    date_str = row['Parsed_Date'].strftime('%Y-%m-%d')
                    
                    # Construct Alignment Banner if conditions are met
                    alignment_banner = ""
                    if is_bullish_trend or is_buying_dip:
                        alignment_banner = "\n\n🔥 **QUANTAMENTAL ALIGNMENT TRIGGERED** 🔥"
                        if is_bullish_trend:
                            alignment_banner += f"\n✅ **System Score:** {score}/100 (Strong Bullish Trend)"
                        if is_buying_dip:
                            alignment_banner += f"\n📉 **Deep Value:** Price (${curr_price:.2f}) is below ATR Stop-Loss floor (${atr_stop:.2f}). Insider is buying the mathematical dip!"
                    
                    msg = (
                        f"🚨 **INSIDER BUYING DETECTED** 🚨\n"
                        f"Stock: {comp_name} ({ticker})\n"
                        f"Executive: {exec_name} ({position})\n"
                        f"Action: Bought {share_str} shares\n"
                        f"Value: {val_str}\n"
                        f"Date: {date_str}"
                        f"{alignment_banner}"
                    )
                    
                    # Save to Local Notification Center Database
                    cursor.execute(
                        "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)",
                        ("Insider", msg)
                    )
                    conn.commit()
                    
                    # Send to Nextcloud
                    if send_nextcloud_message(msg, config):
                        alerts_sent += 1
                        
            except Exception as e:
                print(f"[ERROR] Evaluating Insider trades for {ticker}: {e}")
                
        conn.close()
        return True, f"Insider check complete. Triggered {alerts_sent} alerts based on ${min_value:,.0f} limit."

    except Exception as e:
        print(f"[ERROR] Fatal crash in run_insider_alert: {e}")
        return False, f"System Crash: {str(e)}"