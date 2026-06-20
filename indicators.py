"""Pure, side-effect-free TA functions; callers must flatten yfinance MultiIndex columns before passing Series in."""
from __future__ import annotations

from typing import Dict, List, Optional, Tuple

import numpy as np
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


def compute_volume_profile(
    df: pd.DataFrame,
    bins: int = 50,
    window: int = 180,
) -> Dict[str, object]:
    """Volume-at-Price distribution over the last `window` bars; returns {poc, val, vah, hvns, lvns, entry_zone, exit_zone}; entry_zone = highest support below price, exit_zone = lowest resistance above; all None when data is insufficient."""
    _empty: Dict[str, object] = {
        "poc": None, "val": None, "vah": None,
        "hvns": [], "lvns": [], "entry_zone": None, "exit_zone": None,
    }

    subset = df.tail(window)
    if len(subset) < 20:
        return _empty

    vol = subset["Volume"].fillna(0).values
    if vol.sum() == 0:
        return _empty

    lo = float(subset["Low"].min())
    hi = float(subset["High"].max())
    if hi <= lo:
        return _empty

    edges = np.linspace(lo, hi, bins + 1)
    centres = (edges[:-1] + edges[1:]) / 2.0

    indices = np.searchsorted(edges[1:], subset["Close"].values, side="left")
    indices = np.clip(indices, 0, bins - 1)

    bucket_vol = np.zeros(bins)
    for idx, v in zip(indices, vol):
        bucket_vol[idx] += v

    poc_idx = int(np.argmax(bucket_vol))
    poc = float(centres[poc_idx])

    # Value Area covers 70% of total volume, expanding outward from POC
    total_vol = bucket_vol.sum()
    target = 0.70 * total_vol
    lo_idx = hi_idx = poc_idx
    accumulated = bucket_vol[poc_idx]

    while accumulated < target and (lo_idx > 0 or hi_idx < bins - 1):
        can_lo = lo_idx > 0
        can_hi = hi_idx < bins - 1
        if can_lo and can_hi:
            if bucket_vol[lo_idx - 1] >= bucket_vol[hi_idx + 1]:
                lo_idx -= 1
                accumulated += bucket_vol[lo_idx]
            else:
                hi_idx += 1
                accumulated += bucket_vol[hi_idx]
        elif can_lo:
            lo_idx -= 1
            accumulated += bucket_vol[lo_idx]
        else:
            hi_idx += 1
            accumulated += bucket_vol[hi_idx]

    val = float(edges[lo_idx])
    vah = float(edges[hi_idx + 1])

    nonzero = bucket_vol[bucket_vol > 0]
    threshold = 1.5 * float(np.median(nonzero)) if len(nonzero) else 0.0

    hvns: List[float] = []
    lvns: List[float] = []
    for i in range(bins):
        left = bucket_vol[i - 1] if i > 0 else 0.0
        right = bucket_vol[i + 1] if i < bins - 1 else 0.0
        if bucket_vol[i] > left and bucket_vol[i] > right and bucket_vol[i] > threshold:
            hvns.append(float(centres[i]))
        elif bucket_vol[i] < left and bucket_vol[i] < right and bucket_vol[i] < threshold:
            lvns.append(float(centres[i]))

    current = float(subset["Close"].iloc[-1])

    below = [p for p in hvns + [val] if p < current]
    entry_zone: Optional[float] = float(max(below)) if below else None

    above = [p for p in hvns + [vah] if p > current]
    exit_zone: Optional[float] = float(min(above)) if above else None

    return {
        "poc": poc, "val": val, "vah": vah,
        "hvns": hvns, "lvns": lvns,
        "entry_zone": entry_zone, "exit_zone": exit_zone,
    }


def compute_keltner_channel(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    ema_period: int = 21,
) -> Dict[str, Optional[float]]:
    """Last-bar Keltner Channel: EMA(ema_period) centre with ±2/±3 ATR(14) bands; z_score = (Close − EMA)/ATR; all None when ATR is unavailable or zero."""
    if len(close) < ATR_WINDOW + 1:
        return {
            "ema_21": None, "upper_2": None, "upper_3": None,
            "lower_2": None, "lower_3": None, "z_score": None,
        }

    ema = close.ewm(span=ema_period, adjust=False).mean()
    atr = compute_atr(high, low, close)

    last_ema = ema.iloc[-1]
    last_atr = atr.iloc[-1]
    last_close = close.iloc[-1]

    if pd.isna(last_ema) or pd.isna(last_atr) or last_atr == 0:
        return {
            "ema_21": None if pd.isna(last_ema) else float(last_ema),
            "upper_2": None, "upper_3": None,
            "lower_2": None, "lower_3": None,
            "z_score": None,
        }

    e = float(last_ema)
    a = float(last_atr)
    return {
        "ema_21": e,
        "upper_2": e + 2.0 * a,
        "upper_3": e + 3.0 * a,
        "lower_2": e - 2.0 * a,
        "lower_3": e - 3.0 * a,
        "z_score": (float(last_close) - e) / a,
    }
