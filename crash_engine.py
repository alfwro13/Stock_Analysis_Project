# crash_engine.py
import pandas as pd
import yfinance as yf
import ta
from datetime import datetime, timedelta

class CrashEngine:
    def __init__(self, config):
        """Initializes the Crash Engine with dynamically loaded configurations."""
        self.config = config
        self.crash_cfg = self.config.get("NOTIFICATIONS", {}).get("CRASH_ALERTS", {})
        
        # Standard trend thresholds
        self.drop_percent = float(self.crash_cfg.get("DROP_PERCENT", 5.0))
        self.drop_days = int(self.crash_cfg.get("DROP_DAYS", 3))
        self.sma_length = int(self.crash_cfg.get("SMA_LENGTH", 10))
        self.sma_gap_percent = float(self.crash_cfg.get("SMA_GAP_PERCENT", 2.0))
        
        # New Circuit Breaker Threshold for instant Intraday Drops
        self.flash_crash_threshold = float(self.crash_cfg.get("FLASH_CRASH_THRESHOLD", 3.0))

    def _fetch_market_context(self) -> float:
        """Fetches S&P 500 intraday performance to gauge macroeconomic conditions."""
        try:
            spy = yf.Ticker("SPY").history(period="2d")
            if len(spy) >= 2:
                prev_close = spy['Close'].iloc[0]
                curr_price = spy['Close'].iloc[1]
                return ((curr_price - prev_close) / prev_close) * 100.0
        except Exception:
            pass
        return 0.0

    def _generate_context_report(self, ticker: str, drop_pct: float, df_combined: pd.DataFrame, asset_meta: dict) -> str:
        """
        Fetches live news, volume anomalies, and macro context to construct a 
        5-10 sentence analytical conclusion of why the crash is happening.
        """
        report = []
        company_name = asset_meta.get('company_name', ticker)
        
        # 1. Macro Context (Systematic vs Idiosyncratic)
        spy_drop = self._fetch_market_context()
        if spy_drop <= -1.5:
            report.append(f"The broader market is currently experiencing a heavy sell-off (S&P 500: {spy_drop:.2f}%). The weakness in {company_name} is likely being amplified by macro-economic panic rather than purely isolated company issues.")
        elif spy_drop >= 0:
            report.append(f"This appears to be an isolated (idiosyncratic) event. While {company_name} is crashing, the broader market remains green/flat (S&P 500: {spy_drop:.2f}%).")
        else:
            report.append(f"The broader market is slightly weak (S&P 500: {spy_drop:.2f}%), but {company_name} is significantly underperforming the baseline.")

        # 2. Volume Anomaly Check
        try:
            live_ticker = yf.Ticker(ticker)
            live_data = live_ticker.history(period="1mo")
            if not live_data.empty and 'Volume' in live_data.columns:
                avg_vol = live_data['Volume'].rolling(20).mean().iloc[-2] # 20-day average
                current_vol = live_data['Volume'].iloc[-1]
                
                if current_vol > (avg_vol * 1.5):
                    report.append(f"Selling pressure is severe. Intraday volume has already surged to {current_vol:,.0f}, which is massively above its 20-day average. This indicates heavy institutional distribution.")
                else:
                    report.append("Interestingly, this price drop is occurring on relatively low/average volume, suggesting a lack of liquidity or an absence of buyers rather than aggressive institutional dumping.")
        except Exception:
            pass

        # 3. Technical Damage Assessment
        latest_price = df_combined['Close'].iloc[-1]
        try:
            sma50 = ta.trend.sma_indicator(df_combined['Close'], window=50).iloc[-1]
            if latest_price < sma50 and df_combined['Close'].iloc[-2] >= sma50:
                report.append("Technical damage is notable: the stock just sliced violently through its 50-day moving average, a key institutional support level.")
        except Exception:
            pass

        # 4. Catalyst Extraction (News Headlines)
        try:
            news = live_ticker.news
            if news:
                report.append("\n**Potential Catalysts / Recent Headlines:**")
                # Grab the top 3 most recent news articles
                for item in news[:3]:
                    headline = item.get('title', '')
                    publisher = item.get('publisher', '')
                    # Only include relevant news from the last 48 hours
                    pub_time = datetime.fromtimestamp(item.get('providerPublishTime', 0))
                    if datetime.now() - pub_time < timedelta(days=2):
                        report.append(f"- *{publisher}:* {headline}")
        except Exception:
            report.append("\n**Catalysts:** No major breaking news headlines found on Yahoo Finance within the last 48 hours.")

        # Final string construction
        return "\n".join(report)


    def evaluate(self, ticker, current_price, df_combined, asset_meta):
        """
        Evaluates mathematical crash signatures, now prioritizing Flash Crashes.
        Returns an alert dictionary if triggered, else None.
        """
        if df_combined.empty or len(df_combined) < self.sma_length:
            return None

        # --- 1. NEW LOGIC: Daily Intraday Flash Crash ---
        # Look at yesterday's close vs today's live price
        prev_close = df_combined['Close'].iloc[-2] if len(df_combined) >= 2 else current_price
        intraday_drop_pct = ((current_price - prev_close) / prev_close) * 100.0
        
        is_flash_crash = intraday_drop_pct <= -self.flash_crash_threshold

        # --- 2. OLD LOGIC: Prolonged Trend Bleed ---
        lookback_idx = -(self.drop_days + 1)
        if abs(lookback_idx) > len(df_combined):
            lookback_idx = 0
            
        past_price = df_combined['Close'].iloc[lookback_idx]
        price_drop_pct = ((current_price - past_price) / past_price) * 100.0

        sma_series = ta.trend.sma_indicator(df_combined['Close'], window=self.sma_length)
        latest_sma = sma_series.iloc[-1]
        below_sma_pct = ((latest_sma - current_price) / latest_sma) * 100.0 if latest_sma else 0.0

        is_dropping_fast = price_drop_pct <= -self.drop_percent
        is_breaking_sma = below_sma_pct >= self.sma_gap_percent
        
        # --- 3. Quantamental ATR Floor ---
        atr_stop = asset_meta.get('atr_stop_loss')
        is_below_atr = atr_stop is not None and (0 < current_price < atr_stop)

        # Execution Logic: If Flash Crash OR (X-Drop AND Y-Gap) OR (Broke Mathematical ATR)
        if is_flash_crash or (is_dropping_fast and is_breaking_sma) or is_below_atr:
            reason = []
            context_report = ""
            
            # Prioritize the Flash Crash reporting
            if is_flash_crash: 
                reason.append(f"INTRADAY CRASH: Dropped {abs(intraday_drop_pct):.2f}% today.")
                # Run the heavy Context Analyzer only when a crash actually occurs
                context_report = self._generate_context_report(ticker, intraday_drop_pct, df_combined, asset_meta)
            
            if is_dropping_fast and not is_flash_crash: 
                reason.append(f"Multi-Day Bleed: Dropped {abs(price_drop_pct):.2f}% in {self.drop_days}d")
            if is_breaking_sma and not is_flash_crash: 
                reason.append(f"Fell {below_sma_pct:.2f}% below {self.sma_length}d SMA")
            if is_below_atr: 
                reason.append(f"Price ({current_price:.2f}) broke Quantamental ATR floor ({atr_stop:.2f})")
            
            final_reason = " | ".join(reason)
            if context_report:
                final_reason += f"\n\n**🔍 Crash Analysis:**\n{context_report}"
            
            return {
                'price': current_price,
                'reason': final_reason
            }
            
        return None