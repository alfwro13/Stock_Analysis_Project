# moonshot_engine.py
from __future__ import annotations

from typing import Any
import logging

import pandas as pd
import ta

logger = logging.getLogger(__name__)

class MoonshotEngine:
    """
    Evaluates parabolic upside volatility and 52-week highs.
    Applies technical analysis (RSI, Bollinger Bands) to warn of mean-reversion risks.
    """

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.moon_cfg = self.config.get("NOTIFICATIONS", {}).get("MOONSHOT_ALERTS", {})
        
        # Pull Thresholds from Config
        self.spike_percent = float(self.moon_cfg.get("SPIKE_PERCENT", 5.0))
        self.spike_days = int(self.moon_cfg.get("SPIKE_DAYS", 3))
        self.sma_length = int(self.moon_cfg.get("SMA_LENGTH", 10))
        self.sma_gap_percent = float(self.moon_cfg.get("SMA_GAP_PERCENT", 3.0))
        # Minervini/O'Neil: institutional breakouts require volume >= this multiple of 50d avg
        self.volume_confirmation_ratio = float(self.moon_cfg.get("VOLUME_CONFIRMATION_RATIO", 1.5))

    def evaluate(
        self,
        ticker: str,
        current_price: float,
        df_combined: pd.DataFrame,
        asset_meta: dict[str, Any],
        df_hist: pd.DataFrame,
        current_volume: float | None = None,
    ) -> dict[str, Any] | None:
        """
        Evaluates the mathematical moonshot signatures.
        Returns an alert dictionary if triggered, else None.
        """
        # Exclude the live intraday tick from indicator calculations — it is a partially-formed
        # bar mid-session and skews RSI/Bollinger on volatile open days.
        df_settled = df_combined.iloc[:-1]

        # Guard on df_hist length (stable baseline) rather than df_settled, which loses one row
        # on same-day re-runs due to the orchestrator's overwrite stitching path. Requiring 21
        # historical rows guarantees df_settled always has >= 20 bars for Bollinger regardless.
        if len(df_hist) < 21:
            return None

        # Percentage Spike
        lookback_idx = -(self.spike_days + 1)
        if abs(lookback_idx) > len(df_settled):
            lookback_idx = 0

        past_price = df_settled['Close'].iloc[lookback_idx]
        price_spike_pct = ((current_price - past_price) / past_price) * 100.0

        # SMA Gap (Running too hot)
        sma_series = ta.trend.SMAIndicator(close=df_settled['Close'], window=self.sma_length).sma_indicator()
        latest_sma = sma_series.iloc[-1]
        above_sma_pct = ((current_price - latest_sma) / latest_sma) * 100.0 if latest_sma else 0.0

        # 52-Week High Check
        # NOTE — intentional semantic: this is a 52-week CLOSING high, not an intraday high.
        # Using Close keeps the comparison homogeneous (live close vs historical close) and avoids
        # false ATH triggers where a stock briefly pierces its intraday high on the open then fades.
        # Trade-off: a stock can trade above its true intraday 52w high during a session without
        # firing is_ath — it only fires once a closing price confirms the breakout.
        cutoff_52w = df_hist.index[-1] - pd.DateOffset(weeks=52)
        recent_52w = df_hist[df_hist.index >= cutoff_52w]
        fifty_two_wk_high = recent_52w['Close'].max()
        is_ath = current_price >= fifty_two_wk_high

        # Evaluate Core Trigger Conditions
        # Beta-normalise thresholds: high-beta stocks are naturally more volatile so require a
        # proportionally larger move to qualify; low-beta stocks trip on smaller moves.
        raw_beta = asset_meta.get('beta')
        beta = max(0.5, min(2.0, float(raw_beta))) if raw_beta is not None else 1.0
        adj_spike_pct  = self.spike_percent  * beta
        adj_sma_gap    = self.sma_gap_percent * beta

        is_spiking_fast = price_spike_pct >= adj_spike_pct
        is_gapping_sma = above_sma_pct >= adj_sma_gap

        if (is_spiking_fast and is_gapping_sma) or is_ath:
            caution_notes: list[str] = []

            try:
                rsi_series = ta.momentum.rsi(df_settled['Close'], window=14)
                latest_rsi = rsi_series.iloc[-1]
                if latest_rsi > 70:
                    caution_notes.append(f"RSI is severely overbought ({latest_rsi:.1f}). Mean-reversion risk is high.")
            except Exception as e:
                logger.warning(f"RSI check failed for {ticker}: {e}")

            try:
                bb_indicator = ta.volatility.BollingerBands(df_settled['Close'], window=20, window_dev=2)
                bb_high = bb_indicator.bollinger_hband().iloc[-1]
                if current_price >= bb_high:
                    caution_notes.append("Price has pierced the Upper Bollinger Band (Statistically over-extended).")
            except Exception as e:
                logger.warning(f"Bollinger Band check failed for {ticker}: {e}")

            try:
                if current_volume is not None and 'Volume' in df_hist.columns:
                    avg_50d_vol = df_hist['Volume'].dropna().tail(50).mean()
                    if avg_50d_vol > 0:
                        vol_ratio = current_volume / avg_50d_vol
                        if vol_ratio < self.volume_confirmation_ratio:
                            caution_notes.append(
                                f"Low-volume breakout ({vol_ratio:.2f}x 50d avg). "
                                f"Institutional support is unconfirmed — elevated reversal risk."
                            )
            except Exception as e:
                logger.warning(f"Volume confirmation check failed for {ticker}: {e}")

            # Construct Reason
            reasons: list[str] = []
            if is_ath:
                reasons.append(f"Breached 52-Week High ({fifty_two_wk_high:.2f})")
            if is_spiking_fast:
                reasons.append(f"Surged +{price_spike_pct:.2f}% in {self.spike_days}d")
            if is_gapping_sma:
                reasons.append(f"Gapped +{above_sma_pct:.2f}% above {self.sma_length}d SMA")

            if not reasons:
                return None

            return {
                'price': current_price,
                'reason': " | ".join(reasons),
                'cautions': caution_notes
            }

        return None