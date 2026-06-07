"""Pure, side-effect-free TA functions; callers must flatten yfinance MultiIndex columns before passing Series in."""
from __future__ import annotations

from typing import Dict, List, Tuple

import pandas as pd
import ta

RSI_WINDOW: int = 14
MACD_FAST: int = 12
MACD_SLOW: int = 26
MACD_SIGNAL_WINDOW: int = 9
ATR_WINDOW: int = 14
VOL_SMA_WINDOW: int = 20
VOLUME_SURGE_MULTIPLIER: float = 1.5


def compute_rsi(close: pd.Series, window: int = RSI_WINDOW) -> pd.Series:
    """RSI via the ta library (Wilder/RMA smoothing), default window 14."""
    return ta.momentum.RSIIndicator(close=close, window=window).rsi()


def compute_macd(
    close: pd.Series,
    fast: int = MACD_FAST,
    slow: int = MACD_SLOW,
    signal: int = MACD_SIGNAL_WINDOW,
) -> Tuple[pd.Series, pd.Series, pd.Series]:
    """Returns (macd_line, signal_line, histogram) using ta defaults 12/26/9."""
    ind = ta.trend.MACD(
        close=close,
        window_fast=fast,
        window_slow=slow,
        window_sign=signal,
    )
    return ind.macd(), ind.macd_signal(), ind.macd_diff()


def compute_smas(close: pd.Series, windows: List[int]) -> Dict[int, pd.Series]:
    """SMA for each requested window via the ta library. Returns {window: Series}."""
    return {
        w: ta.trend.SMAIndicator(close=close, window=w).sma_indicator()
        for w in windows
    }


def compute_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = ATR_WINDOW,
) -> pd.Series:
    """Average True Range (Wilder smoothing) via the ta library, default window 14."""
    return ta.volatility.AverageTrueRange(
        high=high, low=low, close=close, window=window
    ).average_true_range()


def compute_volume_sma(volume: pd.Series, window: int = VOL_SMA_WINDOW) -> pd.Series:
    """Simple 20-period rolling mean of volume (raw pandas — canonical Option A)."""
    return volume.rolling(window=window).mean()


def compute_volume_surge(
    volume: pd.Series,
    vol_sma: pd.Series,
    multiplier: float = VOLUME_SURGE_MULTIPLIER,
) -> pd.Series:
    """Returns int Series: 1 where volume exceeds vol_sma * multiplier, else 0."""
    return (volume > vol_sma * multiplier).astype(int)


def compute_bullish_cross(macd: pd.Series, signal: pd.Series) -> pd.Series:
    """Returns int Series: 1 on bars where MACD crosses above its signal line, else 0."""
    return (
        (macd > signal) & (macd.shift(1) <= signal.shift(1))
    ).astype(int)
