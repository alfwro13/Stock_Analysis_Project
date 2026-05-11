# moonshot_engine.py
import os
import json
import requests
import yfinance as yf
import pandas as pd
import ta
from datetime import datetime
from config import load_config, PORTFOLIO_PATH, INTRADAY_DIR, HISTORICAL_DIR, PORT
from database import get_connection


class MoonshotEngine:
    """
    Scans the portfolio for parabolic upside volatility and 52-week highs.
    Applies technical analysis (RSI, Bollinger Bands) to warn of mean-reversion risks.
    """

    def __init__(self):
        self.config = load_config()
        self.moon_cfg = self.config.get("NOTIFICATIONS", {}).get("MOONSHOT_ALERTS", {})
        
        # Pull Thresholds from Config
        self.spike_percent = float(self.moon_cfg.get("SPIKE_PERCENT", 5.0))
        self.spike_days = int(self.moon_cfg.get("SPIKE_DAYS", 3))
        self.sma_length = int(self.moon_cfg.get("SMA_LENGTH", 10))
        self.sma_gap_percent = float(self.moon_cfg.get("SMA_GAP_PERCENT", 3.0))
        
        # Nextcloud Credentials
        self.nextcloud_url = self.config.get("NEXTCLOUD_URL", "")
        self.bot_username = self.config.get("BOT_USERNAME", "")
        self.app_password = self.config.get("APP_PASSWORD", "")
        self.token = self.config.get("CONVERSATION_TOKEN", "")

    def get_portfolio_tickers(self) -> dict:
        """Safely extracts all unique tickers currently held in the portfolio."""
        if not os.path.exists(PORTFOLIO_PATH):
            return {}
        try:
            with open(PORTFOLIO_PATH, 'r') as f:
                data = json.load(f)
                return {v['ticker']: v for k, v in data.items() if 'ticker' in v}
        except Exception as e:
            print(f"[MOONSHOT] Error loading portfolio: {e}")
            return {}

    def is_alert_suppressed(self, ticker: str) -> bool:
        """Checks if a Moonshot alert was already sent for this stock today."""
        conn = get_connection()
        cursor = conn.cursor()
        
        today_start = datetime.now().replace(
            hour=0, minute=0, second=0, microsecond=0
        ).strftime("%Y-%m-%d %H:%M:%S")
        
        cursor.execute('''
            SELECT 1 FROM system_notifications 
            WHERE message_type = 'Moonshot' 
            AND message_text LIKE ? 
            AND timestamp >= ?
        ''', (f"%{ticker}%", today_start))
        
        result = cursor.fetchone()
        conn.close()
        return result is not None

    def log_notification(self, msg: str):
        """Logs the alert locally to suppress duplicates and maintain a history."""
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO system_notifications (message_type, message_text) VALUES (?, ?)", 
            ("Moonshot", msg)
        )
        conn.commit()
        conn.close()

    def send_nextcloud_msg(self, message: str):
        """Dispatches the Markdown payload directly to Nextcloud Talk."""
        if not all([self.nextcloud_url, self.bot_username, self.app_password, self.token]):
            print("[MOONSHOT] Credentials missing. Skipping transmission.")
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
            print(f"[MOONSHOT] Failed to send Nextcloud message: {e}")

    def run(self):
        """Master execution loop for the Moonshot scanner."""
        print(f"\n--- [MOONSHOT ENGINE] Scan Initiated @ {datetime.now().strftime('%H:%M:%S')} ---")
        
        # Enforce Market Bounds
        sched_cfg = self.config.get("SCHEDULING", {}).get("MOONSHOT_ALERTS", {})
        start_str = sched_cfg.get("START_TIME", "09:30")
        end_str = sched_cfg.get("END_TIME", "16:00")
        
        try:
            now = datetime.now().time()
            start_time = datetime.strptime(start_str, "%H:%M").time()
            end_time = datetime.strptime(end_str, "%H:%M").time()
            if not (start_time <= now <= end_time):
                print(f"[MOONSHOT] Outside active bounds ({start_str}-{end_str}). Aborted.")
                return
        except Exception:
            pass 

        portfolio = self.get_portfolio_tickers()
        if not portfolio:
            print("[MOONSHOT] No portfolio items found.")
            return

        alerts_to_send = []

        for ticker, asset_data in portfolio.items():
            try:
                # Phase 1: Fetch Live Intraday Data
                stock = yf.Ticker(ticker)
                df_intraday = stock.history(period="1d", interval="5m")
                
                if df_intraday.empty:
                    continue
                
                df_intraday.index = df_intraday.index.tz_localize(None)
                current_price = df_intraday['Close'].iloc[-1]
                
                # Phase 2: Load Historical Context
                hist_path = HISTORICAL_DIR / f"{ticker}.parquet"
                if not hist_path.exists():
                    continue
                    
                df_hist = pd.read_parquet(hist_path)
                if df_hist.empty or len(df_hist) < 20:  # Need at least 20 for Bollinger Bands
                    continue
                    
                # Temporarily stitch live price to historical DataFrame
                latest_date = df_intraday.index[-1]
                if latest_date not in df_hist.index:
                    new_row = pd.DataFrame({'Close': [current_price]}, index=[latest_date])
                    df_combined = pd.concat([df_hist[['Close']], new_row])
                else:
                    df_combined = df_hist[['Close']].copy()
                    df_combined.loc[latest_date, 'Close'] = current_price

                # Calculation A: Percentage Spike
                lookback_idx = -(self.spike_days + 1)
                if abs(lookback_idx) > len(df_combined):
                    lookback_idx = 0
                    
                past_price = df_combined['Close'].iloc[lookback_idx]
                price_spike_pct = ((current_price - past_price) / past_price) * 100.0

                # Calculation B: SMA Gap (Running too hot)
                sma_series = ta.trend.sma_indicator(df_combined['Close'], window=self.sma_length)
                latest_sma = sma_series.iloc[-1]
                above_sma_pct = ((current_price - latest_sma) / latest_sma) * 100.0 if latest_sma else 0.0

                # Calculation C: 52-Week High Check
                recent_52w = df_hist.tail(252) # Approx 1 trading year
                fifty_two_wk_high = recent_52w['High'].max() if 'High' in recent_52w else recent_52w['Close'].max()
                is_ath = current_price >= fifty_two_wk_high

                # Phase 3: Evaluate Core Trigger Conditions
                is_spiking_fast = price_spike_pct >= self.spike_percent
                is_gapping_sma = above_sma_pct >= self.sma_gap_percent

                if (is_spiking_fast and is_gapping_sma) or is_ath:
                    if self.is_alert_suppressed(ticker):
                        continue
                        
                    # Phase 4: Risk / Caution Technical Overlay
                    caution_notes = []
                    
                    # RSI Overbought Check
                    rsi_series = ta.momentum.rsi(df_combined['Close'], window=14)
                    latest_rsi = rsi_series.iloc[-1]
                    if latest_rsi > 70:
                        caution_notes.append(f"RSI is severely overbought ({latest_rsi:.1f}). Mean-reversion risk is high.")
                    
                    # Bollinger Band Extent Check
                    bb_indicator = ta.volatility.BollingerBands(df_combined['Close'], window=20, window_dev=2)
                    bb_high = bb_indicator.bollinger_hband().iloc[-1]
                    if current_price >= bb_high:
                        caution_notes.append("Price has pierced the Upper Bollinger Band (Statistically over-extended).")

                    # Construct Reason
                    reasons = []
                    if is_ath:
                        reasons.append(f"Breached 52-Week High (${fifty_two_wk_high:.2f})")
                    if is_spiking_fast:
                        reasons.append(f"Surged +{price_spike_pct:.2f}% in {self.spike_days}d")
                    if is_gapping_sma:
                        reasons.append(f"Gapped +{above_sma_pct:.2f}% above {self.sma_length}d SMA")

                    alerts_to_send.append({
                        'ticker': ticker,
                        'price': current_price,
                        'reason': " | ".join(reasons),
                        'cautions': caution_notes
                    })

            except Exception as e:
                print(f"[MOONSHOT] Failed evaluating {ticker}: {e}")

        # Phase 5: Payload Dispatch
        if alerts_to_send:
            msg = f"🚀 **MOONSHOT ALERT** ({datetime.now().strftime('%H:%M')}) 🚀\n\n"
            
            for a in alerts_to_send:
                t = a['ticker']
                url = f"http://localhost:{PORT}/stock/{t}"
                
                msg += f"📈 **{t}**: ${a['price']:.2f}\n"
                msg += f"🔥 {a['reason']}\n"
                
                if a['cautions']:
                    for caution in a['cautions']:
                        msg += f"⚠️ *CAUTION:* {caution}\n"
                        
                msg += f"📊 [View Quantamental Breakdown]({url})\n\n"
                
                self.log_notification(f"Moonshot triggered for {t}. Reason: {a['reason']}")

            self.send_nextcloud_msg(msg.strip())
            print(f"[MOONSHOT] Dispatched alerts for {len(alerts_to_send)} assets.")
        else:
            print("[MOONSHOT] Scan complete. No parabolic setups detected.")


if __name__ == "__main__":
    engine = MoonshotEngine()
    engine.run()