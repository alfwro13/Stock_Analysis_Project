# crash_engine.py
import pandas as pd
import ta

class CrashEngine:
    def __init__(self, config):
        """Initializes the Crash Engine with dynamically loaded configurations."""
        self.config = config
        self.crash_cfg = self.config.get("NOTIFICATIONS", {}).get("CRASH_ALERTS", {})
        
        # Pull Thresholds from Config
        self.drop_percent = float(self.crash_cfg.get("DROP_PERCENT", 5.0))
        self.drop_days = int(self.crash_cfg.get("DROP_DAYS", 3))
        self.sma_length = int(self.crash_cfg.get("SMA_LENGTH", 10))
        self.sma_gap_percent = float(self.crash_cfg.get("SMA_GAP_PERCENT", 2.0))

    def evaluate(self, ticker, current_price, df_combined, asset_meta):
        """
        Evaluates the mathematical crash signatures.
        Returns an alert dictionary if triggered, else None.
        """
        if df_combined.empty or len(df_combined) < self.sma_length:
            return None

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

        # C. Extract Quantamental ATR Stop Loss from Metadata
        atr_stop = asset_meta.get('atr_stop_loss')

        # Evaluate Conditions
        is_dropping_fast = price_drop_pct <= -self.drop_percent
        is_breaking_sma = below_sma_pct >= self.sma_gap_percent
        is_below_atr = atr_stop is not None and (0 < current_price < atr_stop)

        # Execution Logic: If (X-Drop AND Y-Gap) OR (Broke Mathematical ATR)
        if (is_dropping_fast and is_breaking_sma) or is_below_atr:
            reason = []
            if is_dropping_fast: reason.append(f"Dropped {abs(price_drop_pct):.2f}% in {self.drop_days}d")
            if is_breaking_sma: reason.append(f"Fell {below_sma_pct:.2f}% below {self.sma_length}d SMA")
            if is_below_atr: reason.append(f"Price ({current_price:.2f}) broke Quantamental ATR floor ({atr_stop:.2f})")
            
            return {
                'price': current_price,
                'reason': " | ".join(reason)
            }
            
        return None