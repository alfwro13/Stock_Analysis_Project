# intraday_orchestrator.py
import os
import json
import yfinance as yf
import pandas as pd
from datetime import datetime
from config import load_config, PORTFOLIO_PATH, INTRADAY_DIR, HISTORICAL_DIR, PORT
from database import get_connection
from crash_engine import CrashEngine
from moonshot_engine import MoonshotEngine
from nextcloud_talk import send_text_message

def format_currency(price, currency_code):
    """
    Formats price with correct symbol, scaling GBp to GBP dynamically.
    Fails over to the 3-letter currency code if symbol mapping is unavailable.
    """
    if not currency_code:
        currency_code = 'USD'  # Fallback
        
    if currency_code == 'GBp':
        price = price / 100.0
        currency_code = 'GBP'

    symbols = {
        'USD': '$',
        'GBP': '£',
        'EUR': '€'
    }
    
    symbol = symbols.get(currency_code)
    if symbol:
        return f"{symbol}{price:,.2f}"
    else:
        return f"{price:,.2f} {currency_code}"

class IntradayOrchestrator:
    """
    Centralized monitor that batches network and disk I/O operations, 
    feeding raw context into the mathematical quant engines.
    """
    def __init__(self):
        self.config = load_config()
        # Instantiate engines with config so they don't reload it internally
        self.crash_engine = CrashEngine(self.config)
        self.moonshot_engine = MoonshotEngine(self.config)

    def get_portfolio_tickers(self):
        """Safely extracts all unique tickers currently held in the portfolio."""
        if not os.path.exists(PORTFOLIO_PATH):
            return []
        try:
            with open(PORTFOLIO_PATH, 'r') as f:
                data = json.load(f)
                return [v['ticker'] for v in data.values() if 'ticker' in v]
        except Exception:
            return []

    def get_asset_metadata(self, tickers):
        """Fetches currency, ATR stop loss, and company name in a single bulk SQLite query."""
        if not tickers:
            return {}
            
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ','.join('?' for _ in tickers)
        
        query = f"""
            SELECT ticker, company_name, currency, atr_stop_loss 
            FROM stock_signals 
            WHERE ticker IN ({placeholders})
        """
        cursor.execute(query, tickers)
        
        metadata = {}
        for row in cursor.fetchall():
            metadata[row['ticker']] = {
                'company_name': row['company_name'],
                'currency': row['currency'],
                'atr_stop_loss': row['atr_stop_loss']
            }
        conn.close()
        return metadata

    def log_notification(self, msg_type, msg_text):
        """Logs the alert to the local system notification center to prevent duplicate spam."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)", 
            (msg_type, msg_text)
        )
        conn.commit()
        conn.close()

    def is_alert_suppressed(self, msg_type, ticker):
        """Checks if we already triggered this specific alert type for this stock today."""
        conn = get_connection()
        cursor = conn.cursor()
        
        today_start = datetime.now().replace(hour=0, minute=0, second=0, microsecond=0).strftime("%Y-%m-%d %H:%M:%S")
        
        cursor.execute('''
            SELECT 1 FROM system_notifications 
            WHERE message_type = ? 
            AND message_text LIKE ? 
            AND timestamp >= ?
        ''', (msg_type, f"%{ticker}%", today_start))
        
        result = cursor.fetchone()
        conn.close()
        return result is not None

    def run(self):
        print(f"\n--- [INTRADAY ORCHESTRATOR] Scan Initiated @ {datetime.now().strftime('%H:%M:%S')} ---")
        
        # Check active bounds - We use crash_cfg bounds as the unified boundary to avoid timezone drift
        sched_cfg = self.config.get("SCHEDULING", {}).get("CRASH_ALERTS", {})
        start_str = sched_cfg.get("START_TIME", "09:30")
        end_str = sched_cfg.get("END_TIME", "16:00")
        
        try:
            now = datetime.now().time()
            start_time = datetime.strptime(start_str, "%H:%M").time()
            end_time = datetime.strptime(end_str, "%H:%M").time()
            if not (start_time <= now <= end_time):
                print(f"[ORCHESTRATOR] Outside active bounds ({start_str}-{end_str}). Aborted.")
                return
        except Exception:
            pass

        tickers = self.get_portfolio_tickers()
        if not tickers:
            print("[ORCHESTRATOR] No portfolio items found.")
            return

        metadata = self.get_asset_metadata(tickers)
        
        print(f"[ORCHESTRATOR] Performing bulk YF 5m fetch for {len(tickers)} assets...")
        try:
            # group_by='ticker' structures columns predictably when len(tickers) > 1
            df_bulk = yf.download(tickers, period="1d", interval="5m", group_by='ticker', auto_adjust=True, progress=False)
        except Exception as e:
            print(f"[ORCHESTRATOR] Bulk download failed: {e}")
            return

        if df_bulk.empty:
            print("[ORCHESTRATOR] Bulk download returned empty DataFrame.")
            return

        crash_alerts_to_send = []
        moonshot_alerts_to_send = []

        for ticker in tickers:
            try:
                # Extract single-ticker data from the bulk MultiIndex result
                if len(tickers) > 1:
                    if ticker not in df_bulk.columns.get_level_values(0):
                        continue
                    df_intraday = df_bulk[ticker].copy()
                else:
                    df_intraday = df_bulk.copy()

                df_intraday.dropna(subset=['Close'], inplace=True)
                if df_intraday.empty:
                    continue

                # Save Intraday Parquet for the Web Dashboard to keep it live
                df_intraday.index = df_intraday.index.tz_localize(None)
                df_intraday.to_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet", engine='pyarrow')
                
                current_price = df_intraday['Close'].iloc[-1]
                
                # Load Historical Data for stitching and math
                hist_path = HISTORICAL_DIR / f"{ticker}.parquet"
                if not hist_path.exists():
                    continue
                    
                df_hist = pd.read_parquet(hist_path)
                if df_hist.empty or len(df_hist) < 20:
                    continue
                    
                latest_date = df_intraday.index[-1]
                if latest_date not in df_hist.index:
                    new_row = pd.DataFrame({'Close': [current_price]}, index=[latest_date])
                    df_combined = pd.concat([df_hist[['Close']], new_row])
                else:
                    df_combined = df_hist[['Close']].copy()
                    df_combined.loc[latest_date, 'Close'] = current_price

                asset_meta = metadata.get(ticker, {})
                currency = asset_meta.get('currency', 'USD')
                
                # --- EVALUATE CRASH ENGINE ---
                if self.config.get("NOTIFICATIONS", {}).get("CRASH_ALERTS", {}).get("ENABLED", False):
                    if not self.is_alert_suppressed("Crash", ticker):
                        crash_alert = self.crash_engine.evaluate(ticker, current_price, df_combined, asset_meta)
                        if crash_alert:
                            crash_alerts_to_send.append((ticker, crash_alert, currency))
                
                # --- EVALUATE MOONSHOT ENGINE ---
                if self.config.get("NOTIFICATIONS", {}).get("MOONSHOT_ALERTS", {}).get("ENABLED", False):
                    if not self.is_alert_suppressed("Moonshot", ticker):
                        moonshot_alert = self.moonshot_engine.evaluate(ticker, current_price, df_combined, asset_meta, df_hist)
                        if moonshot_alert:
                            moonshot_alerts_to_send.append((ticker, moonshot_alert, currency))

            except Exception as e:
                print(f"[ORCHESTRATOR] Error processing {ticker}: {e}")

        # --- BATCH DISPATCH ALERTS ---
        combined_message = ""
        
        if crash_alerts_to_send:
            combined_message += f"🚨 **INTRADAY CRASH ALERT** ({datetime.now().strftime('%H:%M')}) 🚨\n\n"
            for ticker, alert, currency in crash_alerts_to_send:
                formatted_price = format_currency(alert['price'], currency)
                url = f"http://localhost:{PORT}/stock/{ticker}"
                combined_message += f"📉 **{ticker}**: {formatted_price}\n⚠️ {alert['reason']}\n📊 [View Breakdown]({url})\n\n"
                self.log_notification("Crash", f"Intraday Alert triggered for {ticker}. Reason: {alert['reason']}")
        
        if moonshot_alerts_to_send:
            combined_message += f"🚀 **MOONSHOT ALERT** ({datetime.now().strftime('%H:%M')}) 🚀\n\n"
            for ticker, alert, currency in moonshot_alerts_to_send:
                formatted_price = format_currency(alert['price'], currency)
                url = f"http://localhost:{PORT}/stock/{ticker}"
                combined_message += f"📈 **{ticker}**: {formatted_price}\n🔥 {alert['reason']}\n"
                for caution in alert.get('cautions', []):
                    combined_message += f"⚠️ *CAUTION:* {caution}\n"
                combined_message += f"📊 [View Breakdown]({url})\n\n"
                self.log_notification("Moonshot", f"Moonshot triggered for {ticker}. Reason: {alert['reason']}")

        if combined_message:
            send_text_message(combined_message.strip(), self.config)
            print(f"[ORCHESTRATOR] Dispatched batch alert: {len(crash_alerts_to_send)} crashes, {len(moonshot_alerts_to_send)} moonshots.")
        else:
            print("[ORCHESTRATOR] Scan complete. No unsuppressed signatures detected.")

if __name__ == "__main__":
    engine = IntradayOrchestrator()
    engine.run()