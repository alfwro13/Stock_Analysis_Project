# crash_engine.py
import pandas as pd
import yfinance as yf
import ta
import logging
from datetime import datetime, timedelta

# Initialize module-level logger for production observability
logger = logging.getLogger(__name__)

class CrashEngine:
    def __init__(self, config: dict):
        """Initializes the Crash Engine with dynamically loaded configurations."""
        self.config = config
        self.crash_cfg = self.config.get("NOTIFICATIONS", {}).get("CRASH_ALERTS", {})
        
        # Standard trend thresholds
        self.drop_percent = float(self.crash_cfg.get("DROP_PERCENT", 5.0))
        self.drop_days = int(self.crash_cfg.get("DROP_DAYS", 3))
        self.sma_length = int(self.crash_cfg.get("SMA_LENGTH", 10))
        self.sma_gap_percent = float(self.crash_cfg.get("SMA_GAP_PERCENT", 2.0))
        
        # New Circuit Breaker Threshold for instant Intraday Drops
        self.session_crash_threshold = float(
            self.crash_cfg.get("SESSION_CRASH_THRESHOLD", self.crash_cfg.get("FLASH_CRASH_THRESHOLD", 3.0))
        )

    def _fetch_market_context(self) -> float:
        """Fetches S&P 500 intraday performance to gauge macroeconomic conditions."""
        try:
            # Expand to 5d to guarantee enough rows during long weekends or market holidays
            spy = yf.Ticker("SPY").history(period="5d")
            if len(spy) >= 2:
                # Strictly reference the most recent two sessions
                prev_close = float(spy['Close'].iloc[-2])
                curr_price = float(spy['Close'].iloc[-1])
                
                if prev_close > 0:
                    return ((curr_price - prev_close) / prev_close) * 100.0
        except Exception as e:
            logger.warning(f"Failed to fetch macroeconomic context (SPY): {e}")
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
        # FIX: Pre-initialize to prevent NameError if yf.Ticker initialization fails.
        live_ticker = None 
        
        try:
            live_ticker = yf.Ticker(ticker)
            live_data = live_ticker.history(period="1mo")
            if not live_data.empty and 'Volume' in live_data.columns:
                current_vol = live_data['Volume'].iloc[-1]
                valid_vol = live_data['Volume'].dropna()
                
                # Safely calculate rolling mean avoiding NaN for assets with < 20 days history
                if len(valid_vol) >= 20:
                    avg_vol = valid_vol.rolling(20).mean().iloc[-2]
                else:
                    avg_vol = valid_vol.mean()
                    
                if not pd.isna(avg_vol) and avg_vol > 0:
                    if current_vol > (avg_vol * 1.5):
                        report.append(f"Selling pressure is severe. Intraday volume has already surged to {current_vol:,.0f}, which is massively above its recent average. This indicates heavy institutional distribution.")
                    else:
                        report.append("Interestingly, this price drop is occurring on relatively low/average volume, suggesting a lack of liquidity or an absence of buyers rather than aggressive institutional dumping.")
        except Exception as e:
            logger.warning(f"Volume anomaly check failed for {ticker}: {e}")

        # 3. Technical Damage Assessment
        latest_price = df_combined['Close'].iloc[-1]
        try:
            sma50 = ta.trend.SMAIndicator(close=df_combined['Close'], window=50).sma_indicator().iloc[-1]
            if latest_price < sma50 and df_combined['Close'].iloc[-2] >= sma50:
                report.append("Technical damage is notable: the stock just sliced violently through its 50-day moving average, a key institutional support level.")
        except Exception as e:
            logger.warning(f"Technical damage assessment failed for {ticker}: {e}")

        # 4. Catalyst Extraction (News Headlines)
        try:
            if live_ticker is not None:
                news = live_ticker.news
                if news:
                    report.append("\n**Potential Catalysts / Recent Headlines:**")
                    # Grab the top 3 most recent news articles
                    for item in news[:3]:
                        # Handle new yfinance nested 'content' structure
                        content = item.get('content', item)
                        headline = content.get('title', '')
                        
                        publisher = content.get('publisher', '')
                        if not publisher and isinstance(content.get('provider'), dict):
                            publisher = content['provider'].get('displayName', '')
                            
                        # Extract publish time flexibly
                        pub_time_raw = content.get('pubDate') or content.get('providerPublishTime') or item.get('providerPublishTime', 0)
                        
                        try:
                            # Handle both string (ISO) and float (UNIX) timestamp formats
                            if isinstance(pub_time_raw, str):
                                pub_time = pd.to_datetime(pub_time_raw).tz_localize(None)
                            else:
                                pub_time = datetime.fromtimestamp(float(pub_time_raw))
                                
                            # Only include relevant news from the last 48 hours
                            if datetime.now() - pub_time < timedelta(days=2):
                                report.append(f"- *{publisher}:* {headline}")
                        except Exception as dt_e:
                            logger.debug(f"Date parsing failed for news item on {ticker}: {dt_e}")
            else:
                report.append("\n**Catalysts:** Could not initialize ticker object to fetch live news.")
        except Exception as e:
            logger.warning(f"Catalyst extraction failed for {ticker}: {e}")
            report.append("\n**Catalysts:** No major breaking news headlines found on Yahoo Finance within the last 48 hours.")

        # Final string construction
        return "\n".join(report)

    def evaluate(self, ticker: str, current_price: float, df_combined: pd.DataFrame, asset_meta: dict) -> dict | None:
        """
        Evaluates mathematical crash signatures, now prioritizing Session Crashes.
        Returns an alert dictionary if triggered, else None.
        """
        if df_combined.empty or len(df_combined) < self.sma_length:
            return None

        # The orchestrator appends the live current_price as the final row of df_combined (iloc[-1]).
        # To calculate the true intraday drop, we must reference the prior session's close (iloc[-2]).
        if len(df_combined) >= 2:
            prev_close = df_combined['Close'].iloc[-2]
        else:
            prev_close = current_price

        intraday_drop_pct = ((current_price - prev_close) / prev_close) * 100.0
        is_session_crash = intraday_drop_pct <= -self.session_crash_threshold

        # --- 2. OLD LOGIC: Prolonged Trend Bleed ---
        lookback_idx = -(self.drop_days + 1)
        if abs(lookback_idx) > len(df_combined):
            lookback_idx = 0
            
        past_price = df_combined['Close'].iloc[lookback_idx]
        price_drop_pct = ((current_price - past_price) / past_price) * 100.0

        sma_series = ta.trend.SMAIndicator(close=df_combined['Close'], window=self.sma_length).sma_indicator()
        latest_sma = sma_series.iloc[-1]
        below_sma_pct = ((latest_sma - current_price) / latest_sma) * 100.0 if latest_sma else 0.0

        is_dropping_fast = price_drop_pct <= -self.drop_percent
        is_breaking_sma = below_sma_pct >= self.sma_gap_percent
        
        # --- 3. Quantamental ATR Floor ---
        atr_stop = asset_meta.get('atr_stop_loss')
        is_below_atr = atr_stop is not None and (0 < current_price < atr_stop)

        # Execution Logic: If Session Crash OR (X-Drop AND Y-Gap) OR (Broke Mathematical ATR)
        if is_session_crash or (is_dropping_fast and is_breaking_sma) or is_below_atr:
            reason = []
            context_report = ""
            
            # Prioritize the Session Crash reporting
            if is_session_crash:
                reason.append(f"SESSION CRASH / GAP DOWN: Dropped {abs(intraday_drop_pct):.2f}% today.")
                # Run the heavy Context Analyzer only when a crash actually occurs
                context_report = self._generate_context_report(ticker, intraday_drop_pct, df_combined, asset_meta)
            
            if is_dropping_fast and not is_session_crash:
                reason.append(f"Multi-Day Bleed: Dropped {abs(price_drop_pct):.2f}% in {self.drop_days}d")
            if is_breaking_sma and not is_session_crash:
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