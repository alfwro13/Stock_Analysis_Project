# intraday_orchestrator.py
import os
import re
import time
import json
import logging
import yfinance as yf
import pandas as pd
from datetime import datetime
from config import load_config, PORTFOLIO_PATH, INTRADAY_DIR, HISTORICAL_DIR, PORT, SERVER_URL
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

def build_stock_url(server_url, port, ticker):
    """
    Intelligently constructs the URL. If the server is a domain/proxy, it drops the port.
    If it's an IP address or localhost, it appends the port automatically.
    """
    base = str(server_url).rstrip('/')
    # Regex to check if the base URL is localhost or an IPv4 address
    is_ip_or_local = bool(re.search(r'localhost|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', base))
    # Check if a port is already explicitly defined in the base URL string
    has_port = bool(re.search(r':\d+$', base))
    
    if is_ip_or_local and not has_port:
        return f"{base}:{port}/stock/{ticker}"
    return f"{base}/stock/{ticker}"


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
        """Fetches currency, ATR stop loss, company name, ML, and Risk metrics in a single bulk SQLite query."""
        if not tickers:
            return {}
            
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ','.join('?' for _ in tickers)
        
        query = f"""
            SELECT s.ticker, s.company_name, s.currency, s.atr_stop_loss,
                   q.ml_confidence_score, q.var_95, q.sentiment_score
            FROM stock_signals s
            LEFT JOIN quant_signals q ON s.ticker = q.ticker 
                AND q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = s.ticker)
            WHERE s.ticker IN ({placeholders})
        """
        cursor.execute(query, tickers)
        
        metadata = {}
        for row in cursor.fetchall():
            metadata[row['ticker']] = {
                'company_name': row['company_name'],
                'currency': row['currency'],
                'atr_stop_loss': row['atr_stop_loss'],
                'ml_confidence_score': row['ml_confidence_score'],
                'var_95': row['var_95'],
                'sentiment_score': row['sentiment_score']
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
        
        # Check active bounds
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
        ignored = self.config.get("IGNORED_TICKERS", [])
        
        # Filter out ignored list AND Mutual Funds
        tickers = [t for t in tickers if t not in ignored and not t.startswith('0P')]
        
        if not tickers:
            print("[ORCHESTRATOR] No valid portfolio items found for intraday scan.")
            return

        metadata = self.get_asset_metadata(tickers)
        
        # Add system yield benchmarks for macro shock detection
        macro_tickers = ["^TYX"]
        download_list = list(set(tickers + macro_tickers))
        
        print(f"[ORCHESTRATOR] Performing bulk YF 5m fetch for {len(download_list)} assets & macro benchmarks...")
        try:
            df_bulk = yf.download(download_list, period="1d", interval="5m", group_by='ticker', auto_adjust=True, progress=False)
        except Exception as e:
            print(f"[ORCHESTRATOR] Bulk download failed: {e}")
            return

        if df_bulk.empty:
            print("[ORCHESTRATOR] Bulk download returned empty DataFrame.")
            return

        # --- MACRO FLASH SURGE DETECTION ---
        for m_ticker in macro_tickers:
            try:
                if isinstance(df_bulk.columns, pd.MultiIndex):
                    if m_ticker not in df_bulk.columns.get_level_values(0):
                        continue
                    m_df = df_bulk[m_ticker].copy()
                else:
                    if 'Close' not in df_bulk.columns:
                        continue
                    m_df = df_bulk.copy() if len(download_list) == 1 else pd.DataFrame()
                    
                m_df.dropna(subset=['Close'], inplace=True)
                if len(m_df) < 5: 
                    continue
                
                m_open = float(m_df['Close'].iloc[0])
                m_curr = float(m_df['Close'].iloc[-1])
                
                if m_open > 0:
                    m_spike = ((m_curr - m_open) / m_open) * 100.0
                    
                    # If yield spikes more than 1.5% intraday, it's a systemic shock
                    if m_spike >= 1.5 and not self.is_alert_suppressed("Macro", m_ticker):
                        name = "US 30Y Treasury" if m_ticker == "^TYX" else "UK 10Y Gilt"
                        msg = (
                            f"🚨 **SYSTEMIC MACRO ALERT: {name} SURGING** 🚨\n\n"
                            f"**Current Yield:** {m_curr:.3f}%\n"
                            f"**Intraday Spike:** +{m_spike:.2f}%\n\n"
                            f"⚠️ The cost of capital is experiencing a violent intraday shock. "
                            f"Expect immediate severe valuation compression across high-multiple and tech equities. "
                            f"Risk-Off environment detected."
                        )
                        send_text_message(msg, self.config)
                        self.log_notification("Macro", f"Systemic Yield Surge detected on {m_ticker} (+{m_spike:.2f}%)")
            except Exception as e:
                print(f"[ORCHESTRATOR] Macro eval failed for {m_ticker}: {e}")

        # --- PHASE 4: AI MACRO DEFENSE OVERRIDE ---
        # Dynamically tighten the crash threshold if the XGBoost AI model predicts an imminent > 2.0% SPY gap today.
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT MAX(ai_volatility_warning) as max_warning 
                FROM macro_calendar 
                WHERE date(event_date) = date('now') 
                AND is_event_passed = 0
            ''')
            ai_warning_row = cursor.fetchone()
            conn.close()

            if ai_warning_row and ai_warning_row['max_warning'] and float(ai_warning_row['max_warning']) > 2.0:
                print(f"[ORCHESTRATOR] 🛡️ AI Volatility Defense Active: Tightening Flash Crash Threshold to 1.5%")
                self.crash_engine.flash_crash_threshold = 1.5
        except Exception as e:
            print(f"[ORCHESTRATOR] Failed to query AI Macro Defense status: {e}")


        crash_alerts_to_send = []
        moonshot_alerts_to_send = []
        
        # Check correct config paths for enablement (SCHEDULING, not NOTIFICATIONS)
        crash_enabled = self.config.get("SCHEDULING", {}).get("CRASH_ALERTS", {}).get("ENABLED", False)
        moonshot_enabled = self.config.get("SCHEDULING", {}).get("MOONSHOT_ALERTS", {}).get("ENABLED", False)

        for ticker in tickers:
            try:
                # Robust MultiIndex handling to prevent yfinance stripping bugs when mixing US/UK markets
                if isinstance(df_bulk.columns, pd.MultiIndex):
                    if ticker not in df_bulk.columns.get_level_values(0):
                        continue
                    df_intraday = df_bulk[ticker].copy()
                else:
                    if 'Close' not in df_bulk.columns:
                        continue
                    df_intraday = df_bulk.copy()

                df_intraday.dropna(subset=['Close'], inplace=True)
                if df_intraday.empty:
                    continue

                # Save Intraday Parquet for the Web Dashboard to keep it live
                df_intraday.index = df_intraday.index.tz_localize(None)
                df_intraday.to_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet", engine='pyarrow')
                
                current_price = float(df_intraday['Close'].iloc[-1])
                
                # Load Historical Data for stitching and math
                hist_path = HISTORICAL_DIR / f"{ticker}.parquet"
                if not hist_path.exists():
                    continue
                    
                df_hist = pd.read_parquet(hist_path)
                if df_hist.empty or len(df_hist) < 20:
                    continue
                    
                # Strict Time-Series Stitching Normalization
                latest_dt = df_intraday.index[-1]
                latest_date_only = latest_dt.normalize()
                hist_last_date_only = df_hist.index[-1].normalize()
                
                if hist_last_date_only == latest_date_only:
                    df_combined = df_hist[['Close']].copy()
                    df_combined.loc[df_hist.index[-1], 'Close'] = current_price
                else:
                    new_row = pd.DataFrame({'Close': [current_price]}, index=[latest_dt])
                    df_combined = pd.concat([df_hist[['Close']], new_row])

                asset_meta = metadata.get(ticker, {})
                currency = asset_meta.get('currency', 'USD')
                
                # --- EVALUATE CRASH ENGINE ---
                if crash_enabled:
                    if not self.is_alert_suppressed("Crash", ticker):
                        crash_alert = self.crash_engine.evaluate(ticker, current_price, df_combined, asset_meta)
                        if crash_alert:
                            crash_alerts_to_send.append((ticker, crash_alert, currency, asset_meta))
                
                # --- EVALUATE MOONSHOT ENGINE ---
                if moonshot_enabled:
                    if not self.is_alert_suppressed("Moonshot", ticker):
                        moonshot_alert = self.moonshot_engine.evaluate(ticker, current_price, df_combined, asset_meta, df_hist)
                        if moonshot_alert:
                            moonshot_alerts_to_send.append((ticker, moonshot_alert, currency, asset_meta))

            except Exception as e:
                print(f"[ORCHESTRATOR] Error processing {ticker}: {e}")

        # --- BATCH DISPATCH ALERTS (One Message Per Ticker) ---
        for ticker, alert, currency, meta in crash_alerts_to_send:
            formatted_price = format_currency(alert['price'], currency)
            url = build_stock_url(SERVER_URL, PORT, ticker)
            
            ml_conf = f"{meta.get('ml_confidence_score'):.1f}%" if meta.get('ml_confidence_score') is not None else "N/A"
            var = f"{(meta.get('var_95') * 100):.2f}%" if meta.get('var_95') is not None else "N/A"
            sent = f"{meta.get('sentiment_score'):.3f}" if meta.get('sentiment_score') is not None else "N/A"

            msg = (
                f"🚨 **INTRADAY CRASH ALERT: {ticker}** 🚨\n\n"
                f"**Price:** {formatted_price}\n"
                f"**Trigger:** {alert['reason']}\n\n"
                f"📊 **Context:**\n"
                f"• AI Confidence: {ml_conf}\n"
                f"• Downside VaR: {var}\n"
                f"• NLP Sentiment: {sent}\n\n"
                f"🔗 [View Breakdown]({url})"
            )
            send_text_message(msg, self.config)
            self.log_notification("Crash", f"Intraday Alert triggered for {ticker}. Reason: {alert['reason']}")
            time.sleep(1) # Prevent Nextcloud rate-limiting

        for ticker, alert, currency, meta in moonshot_alerts_to_send:
            formatted_price = format_currency(alert['price'], currency)
            url = build_stock_url(SERVER_URL, PORT, ticker)
            
            ml_conf = f"{meta.get('ml_confidence_score'):.1f}%" if meta.get('ml_confidence_score') is not None else "N/A"
            var = f"{(meta.get('var_95') * 100):.2f}%" if meta.get('var_95') is not None else "N/A"
            sent = f"{meta.get('sentiment_score'):.3f}" if meta.get('sentiment_score') is not None else "N/A"

            cautions = ""
            for caution in alert.get('cautions', []):
                cautions += f"⚠️ *{caution}*\n"

            msg = (
                f"🚀 **MOONSHOT ALERT: {ticker}** 🚀\n\n"
                f"**Price:** {formatted_price}\n"
                f"**Trigger:** {alert['reason']}\n\n"
                f"{cautions}\n"
                f"📊 **Context:**\n"
                f"• AI Confidence: {ml_conf}\n"
                f"• Downside VaR: {var}\n"
                f"• NLP Sentiment: {sent}\n\n"
                f"🔗 [View Breakdown]({url})"
            )
            send_text_message(msg, self.config)
            self.log_notification("Moonshot", f"Moonshot triggered for {ticker}. Reason: {alert['reason']}")
            time.sleep(1)

        print(f"[ORCHESTRATOR] Scan complete. Dispatched {len(crash_alerts_to_send)} crashes and {len(moonshot_alerts_to_send)} moonshots.")

if __name__ == "__main__":
    engine = IntradayOrchestrator()
    engine.run()