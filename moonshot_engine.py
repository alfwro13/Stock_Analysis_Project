# moonshot_engine.py
import pandas as pd
import ta

class MoonshotEngine:
    """
    Evaluates parabolic upside volatility and 52-week highs.
    Applies technical analysis (RSI, Bollinger Bands) to warn of mean-reversion risks.
    """

    def __init__(self, config):
        self.config = config
        self.moon_cfg = self.config.get("NOTIFICATIONS", {}).get("MOONSHOT_ALERTS", {})
        
        # Pull Thresholds from Config
        self.spike_percent = float(self.moon_cfg.get("SPIKE_PERCENT", 5.0))
        self.spike_days = int(self.moon_cfg.get("SPIKE_DAYS", 3))
        self.sma_length = int(self.moon_cfg.get("SMA_LENGTH", 10))
        self.sma_gap_percent = float(self.moon_cfg.get("SMA_GAP_PERCENT", 3.0))

    def evaluate(self, ticker, current_price, df_combined, asset_meta, df_hist):
        """
        Evaluates the mathematical moonshot signatures.
        Returns an alert dictionary if triggered, else None.
        """
        if df_combined.empty or len(df_combined) < 20:  # Need at least 20 for Bollinger Bands
            return None

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
                reasons.append(f"Breached 52-Week High ({fifty_two_wk_high:.2f})")
            if is_spiking_fast:
                reasons.append(f"Surged +{price_spike_pct:.2f}% in {self.spike_days}d")
            if is_gapping_sma:
                reasons.append(f"Gapped +{above_sma_pct:.2f}% above {self.sma_length}d SMA")

            return {
                'price': current_price,
                'reason': " | ".join(reasons),
                'cautions': caution_notes
            }

        return None