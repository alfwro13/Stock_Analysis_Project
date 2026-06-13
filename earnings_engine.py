# earnings_engine.py
import os
import json
from datetime import datetime
from database import get_connection
from config import PORTFOLIO_PATH, load_config
from time_engine import now_local
from notification_engine import notify

def run_earnings_alert():
    try:
        config = load_config()
        earnings_cfg = config.get("NOTIFICATIONS", {}).get("EARNINGS_ALERTS", {})
        days_ahead = int(earnings_cfg.get("DAYS_AHEAD", 7))
        alert_type = earnings_cfg.get("ALERT_TYPE", "daily") # "daily" or "once"

        if not os.path.exists(PORTFOLIO_PATH):
            return False, "Portfolio file not found."
        
        with open(PORTFOLIO_PATH, 'r') as f:
            try:
                portfolio = json.load(f)
            except json.JSONDecodeError:
                return False, "Portfolio JSON is corrupted."
                
        # 0P prefix = Morningstar fund IDs (not equities, no earnings dates)
        tickers = [data.get('ticker') for data in portfolio.values()
                   if data.get('ticker') and not data.get('ticker').startswith('0P')]

        if not tickers:
            return True, "No valid equity tickers found in portfolio."

        conn = get_connection()
        try:
            cursor = conn.cursor()

            placeholders = ','.join('?' for _ in tickers)
            query = f"SELECT ticker, company_name, next_earnings_date FROM stock_signals WHERE ticker IN ({placeholders})"
            cursor.execute(query, tickers)
            rows = cursor.fetchall()

            today = now_local().date()
            alerts_sent = 0

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

                        notify("earnings_alert", "Earnings", msg, conn=conn)
                        alerts_sent += 1

                except Exception as e:
                    print(f"[ERROR] Evaluating earnings date for {ticker}: {e}")

            conn.commit()
            return True, f"Earnings check complete. Triggered {alerts_sent} alerts based on current settings."
        finally:
            conn.close()
    
    except Exception as e:
        print(f"[ERROR] Fatal crash in run_earnings_alert: {e}")
        return False, f"System Crash: {str(e)}"