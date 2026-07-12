import logging
from datetime import datetime
from database import get_connection
from config import load_config
from db_helpers import get_next_earnings_dates
from time_engine import now_local
from notification_engine import notify

logger = logging.getLogger(__name__)

def run_earnings_alert():
    try:
        from accounts_engine import get_combined_holdings
        config = load_config()
        earnings_cfg = config.get("NOTIFICATIONS", {}).get("EARNINGS_ALERTS", {})
        days_ahead = int(earnings_cfg.get("DAYS_AHEAD", 7))
        alert_type = earnings_cfg.get("ALERT_TYPE", "daily") # "daily" or "once"

        # 0P prefix = Morningstar fund IDs (not equities, no earnings dates)
        tickers = [t for t in get_combined_holdings().keys() if not t.startswith('0P')]

        if not tickers:
            return True, "No valid equity tickers found in portfolio."

        conn = get_connection()
        try:
            earnings_data = get_next_earnings_dates(tickers)

            today = now_local().date()
            alerts_sent = 0

            for ticker, data in earnings_data.items():
                name = data['company_name']
                earnings_date_str = data['next_earnings_date']

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
                    logger.error("Evaluating earnings date for %s: %s", ticker, e)

            conn.commit()
            return True, f"Earnings check complete. Triggered {alerts_sent} alerts based on current settings."
        finally:
            conn.close()
    
    except Exception as e:
        logger.error("Fatal crash in run_earnings_alert: %s", e)
        return False, f"System Crash: {str(e)}"