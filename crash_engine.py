# crash_engine.py
import os
import json
import requests
import yfinance as yf
import pandas as pd
import ta
from datetime import datetime
from config import load_config, PORTFOLIO_PATH, INTRADAY_DIR, HISTORICAL_DIR, PORT
from database import get_connection

class CrashEngine:
    def __init__(self):
        """Initializes the Crash Engine with dynamically loaded configurations."""
        self.config = load_config()
        self.crash_cfg = self.config.get("NOTIFICATIONS", {}).get("CRASH_ALERTS", {})
        
        # Pull Thresholds from Config
        self.drop_percent = float(self.crash_cfg.get("DROP_PERCENT", 5.0))
        self.drop_days = int(self.crash_cfg.get("DROP_DAYS", 3))
        self.sma_length = int(self.crash_cfg.get("SMA_LENGTH", 10))
        self.sma_gap_percent = float(self.crash_cfg.get("SMA_GAP_PERCENT", 2.0))
        
        # Nextcloud Credentials
        self.nextcloud_url = self.config.get("NEXTCLOUD_URL", "")
        self.bot_username = self.config.get("BOT_USERNAME", "")
        self.app_password = self.config.get("APP_PASSWORD", "")
        self.token = self.config.get("CONVERSATION_TOKEN", "")

    def get_portfolio_tickers(self):
        """Safely extracts all unique tickers currently held in the portfolio."""
        if not os.path.exists(PORTFOLIO_PATH):
            return {}
        try:
            with open(PORTFOLIO_PATH, 'r') as f:
                data = json.load(f)
                return {v['ticker']: v for k, v in data.items() if 'ticker' in v}
        except Exception:
            return {}

    def is_alert_suppressed(self, ticker):
        """
        Checks the SQLite database to see if we already triggered an alert for 
        this specific stock today, preventing 10-minute notification spam.
        """
        conn = get_connection()
        cursor = conn.cursor()
        
        # Get midnight of the current day
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        
        cursor.execute('''
            SELECT 1 FROM system_notifications 
            WHERE message_type = 'Crash' 
            AND message_text LIKE ? 
            AND timestamp >= ?
        ''', (f"%{ticker}%", today_start))
        
        result = cursor.fetchone()
        conn.close()
        return result is not None

    def log_notification(self, msg):
        """Logs the alert to the local system notification center."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)", 
            ("Crash", msg)
        )
        conn.commit()
        conn.close()

    def send_nextcloud_msg(self, message):
        """Dispatches the Markdown payload directly to Nextcloud Talk."""
        if not all([self.nextcloud_url, self.bot_username, self.app_password, self.token]):
            print("[CRASH ENGINE] Nextcloud credentials missing. Skipping message transmission.")
            return
        
        endpoint = f"{self.nextcloud_url}/ocs/v2.php/apps/spreed/api/v1/chat/{self.token}"
        headers = {
            "OCS-APIRequest": "true", 
            "Content-Type": "application/json", 
            "Accept": "application/json"
        }
        try:
            resp = requests.post(
                endpoint, headers=headers, json={"message": message},
                auth=(self.bot_username, self.app_password), timeout=10
            )
            resp.raise_for_status()
        except Exception as e:
            print(f"[CRASH ENGINE] Failed to send Nextcloud message: {e}")

    def run(self):
        """The Master Execution Method for the Intraday Scanner."""
        print(f"\n--- [CRASH ENGINE] Intraday Scan Initiated @ {datetime.now().strftime('%H:%M:%S')} ---")
        
        # 1. Strict Market Bounds Enforcement
        # Protects against cron rounding pushing a job outside active hours
        sched_cfg = self.config.get("SCHEDULING", {}).get("CRASH_ALERTS", {})
        start_str = sched_cfg.get("START_TIME", "09:30")
        end_str = sched_cfg.get("END_TIME", "16:00")
        
        try:
            now = datetime.now().time()
            start_time = datetime.strptime(start_str, "%H:%M").time()
            end_time = datetime.strptime(end_str, "%H:%M").time()
            if not (start_time <= now <= end_time):
                print(f"[CRASH ENGINE] Time {now.strftime('%H:%M')} is outside active market bounds ({start_str}-{end_str}). Scan aborted.")
                return
        except Exception:
            pass # Proceed if parsing failed gracefully

        portfolio = self.get_portfolio_tickers()
        if not portfolio:
            print("[CRASH ENGINE] No portfolio items found.")
            return

        alerts_to_send = []

        for ticker, asset_data in portfolio.items():
            
            # Phase 1: Fetch 5-Min Data (Updates the Web Dashboard silently)
            try:
                stock = yf.Ticker(ticker)
                df_intraday = stock.history(period="1d", interval="5m")
                
                if df_intraday.empty:
                    continue
                
                # Strip timezone and save immediately so the Web UI always has the latest chart
                df_intraday.index = df_intraday.index.tz_localize(None)
                df_intraday.to_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet", engine='pyarrow')
                
                current_price = df_intraday['Close'].iloc[-1]
            except Exception as e:
                print(f"[CRASH ENGINE] Failed to fetch intraday data for {ticker}: {e}")
                continue

            # Phase 2: Load Historical Data for Math Baseline (Saves API Bandwidth)
            hist_path = HISTORICAL_DIR / f"{ticker}.parquet"
            if not hist_path.exists():
                continue
                
            df_hist = pd.read_parquet(hist_path)
            if df_hist.empty or len(df_hist) < self.sma_length:
                continue
                
            # Temporarily stitch the live 5-minute price to the end of the historical array
            latest_date = df_intraday.index[-1]
            if latest_date not in df_hist.index:
                new_row = pd.DataFrame({'Close': [current_price]}, index=[latest_date])
                df_combined = pd.concat([df_hist[['Close']], new_row])
            else:
                df_combined = df_hist[['Close']].copy()
                df_combined.loc[latest_date, 'Close'] = current_price

            # A. Calculate Percentage Drop
            lookback_idx = -(self.drop_days + 1)
            if abs(lookback_idx) > len(df_combined):
                lookback_idx = 0
                
            past_price = df_combined['Close'].iloc[lookback_idx]
            price_drop_pct = ((current_price - past_price) / past_price) * 100.0

            # B. Calculate SMA Gap
            sma_series = ta.trend.sma_indicator(df_combined['Close'], window=self.sma_length)
            latest_sma = sma_series.iloc[-1]
            below_sma_pct = ((latest_sma - current_price) / latest_sma) * 100.0 if latest_sma else 0.0

            # C. Check Dashboard Database for Quantamental ATR Stop Loss
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("SELECT atr_stop_loss FROM stock_signals WHERE ticker = ?", (ticker,))
            row = cursor.fetchone()
            conn.close()
            
            atr_stop = row['atr_stop_loss'] if row and row['atr_stop_loss'] else None

            # Phase 3: Evaluate Conditions
            is_dropping_fast = price_drop_pct <= -self.drop_percent
            is_breaking_sma = below_sma_pct >= self.sma_gap_percent
            is_below_atr = atr_stop is not None and (0 < current_price < atr_stop)

            # Execution Logic: If (X-Drop AND Y-Gap) OR (Broke Mathematical ATR)
            if (is_dropping_fast and is_breaking_sma) or is_below_atr:
                
                # Check suppression database
                if self.is_alert_suppressed(ticker):
                    print(f"[CRASH ENGINE] {ticker} breached thresholds, but alert is suppressed for the rest of today.")
                    continue
                    
                reason = []
                if is_dropping_fast: reason.append(f"Dropped {abs(price_drop_pct):.2f}% in {self.drop_days}d")
                if is_breaking_sma: reason.append(f"Fell {below_sma_pct:.2f}% below {self.sma_length}d SMA")
                if is_below_atr: reason.append(f"Price (${current_price:.2f}) broke Quantamental ATR floor (${atr_stop:.2f})")
                
                alerts_to_send.append({
                    'ticker': ticker,
                    'price': current_price,
                    'reason': " | ".join(reason)
                })

        # Phase 4: Payload Dispatch
        if alerts_to_send:
            msg = f"🚨 **INTRADAY CRASH ALERT** ({datetime.now().strftime('%H:%M')}) 🚨\n\n"
            
            for a in alerts_to_send:
                t = a['ticker']
                url = f"http://localhost:{PORT}/stock/{t}"
                
                msg += f"📉 **{t}**: ${a['price']:.2f}\n"
                msg += f"⚠️ {a['reason']}\n"
                msg += f"📊 [View Quantamental Breakdown]({url})\n\n"
                
                # Log locally to mute the engine for this specific stock until tomorrow
                self.log_notification(f"Intraday Alert triggered for {t}. Reason: {a['reason']}")

            self.send_nextcloud_msg(msg.strip())
            print(f"[CRASH ENGINE] Dispatched markdown alerts for {len(alerts_to_send)} assets.")
        else:
            print("[CRASH ENGINE] Scan complete. No unsuppressed crash signatures detected.")

if __name__ == "__main__":
    engine = CrashEngine()
    engine.run()