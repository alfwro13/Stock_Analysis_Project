from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from pattern_geometry_helpers import find_pivots, linreg, slope_pct_per_day

# GUI name: "Pattern Detection" (Triangle family). Canonical scheduled-job names live in
# scheduler_manifest.JOB_GRAPH; the registry contract lives in pattern_detection_engine.py.

_ORDER = 3
_WINDOW_DAYS = 40
_MIN_BARS = 60

FAMILY = "triangle"
PATTERN_TYPES: dict[str, str] = {
    "ascending": "up",
    "descending": "down",
}

_PATTERN_LABELS: dict[str, str] = {
    "ascending": "Ascending Triangle",
    "descending": "Descending Triangle",
}


def phase_label(pattern_type: Optional[str], phase: Optional[str]) -> str:
    base = _PATTERN_LABELS.get(pattern_type, pattern_type or "")
    if not base:
        return phase or "Neutral"
    if phase == "CONFIRMED":
        return f"{base} (Confirmed)"
    if phase == "FORMING":
        return f"{base} (Forming)"
    return base


def _detect_and_build(
    close: np.ndarray,
    volume: np.ndarray,
    rsi_series: pd.Series,
    is_descending: bool,
    window_days: int,
    flat_slope_epsilon_pct: float,
    min_slope_pct: float,
) -> Optional[dict]:
    """Finds and validates the most recent Ascending Triangle (is_descending=False) or
    Descending Triangle (is_descending=True) candidate over a trailing `window_days` window
    ending at the last bar of `close`. The flat side (resistance for Ascending, support for
    Descending) is a near-zero-slope regression through its swing points (|slope| <=
    flat_slope_epsilon_pct, in %/day); the sloped side is a regression at least min_slope_pct
    steep in the pattern's direction. Both sides need >=2 swing points to fit a line."""
    n = len(close)
    today_idx = n - 1
    window_start = today_idx - window_days + 1
    if window_start < 0:
        return None

    window_close = close[window_start:today_idx + 1]
    top_local, bottom_local = find_pivots(window_close, _ORDER)
    if len(top_local) < 2 or len(bottom_local) < 2:
        return None
    top_idx = np.array([window_start + i for i in top_local])
    bottom_idx = np.array([window_start + i for i in bottom_local])

    high_fit = linreg(top_idx, close[top_idx])
    low_fit = linreg(bottom_idx, close[bottom_idx])
    if high_fit is None or low_fit is None:
        return None
    high_slope, high_intercept, high_r2 = high_fit
    low_slope, low_intercept, low_r2 = low_fit

    ref_price = close[window_start]
    high_slope_pct = slope_pct_per_day(high_slope, ref_price)
    low_slope_pct = slope_pct_per_day(low_slope, ref_price)

    if is_descending:
        if high_slope_pct > -min_slope_pct:
            return None
        if abs(low_slope_pct) > flat_slope_epsilon_pct:
            return None
        flat_level = float(np.mean(close[bottom_idx]))
        sloped_idx = top_idx
    else:
        if abs(high_slope_pct) > flat_slope_epsilon_pct:
            return None
        if low_slope_pct < min_slope_pct:
            return None
        flat_level = float(np.mean(close[top_idx]))
        sloped_idx = bottom_idx

    last_close = close[today_idx]
    confirmed = (last_close < flat_level) if is_descending else (last_close > flat_level)
    phase = "CONFIRMED" if confirmed else "FORMING"

    first_sloped_price = close[sloped_idx[0]]
    height = abs(flat_level - first_sloped_price)
    breakout_ref = last_close if confirmed else flat_level
    measured_target = (breakout_ref - height) if is_descending else (breakout_ref + height)

    vol_fit = linreg(np.arange(window_start, today_idx + 1), volume[window_start:today_idx + 1])
    vol_confirms = bool(vol_fit is not None and vol_fit[0] < 0)

    rsi_first = rsi_series.iloc[int(sloped_idx[0])]
    rsi_last = rsi_series.iloc[int(sloped_idx[-1])]
    rsi_confirms = (
        bool(rsi_last < rsi_first) if is_descending else bool(rsi_last > rsi_first)
    ) if not (pd.isna(rsi_first) or pd.isna(rsi_last)) else False

    trend_pct = (close[sloped_idx[-1]] - first_sloped_price) / first_sloped_price * 100.0 if first_sloped_price > 0 else 0.0

    return {
        "pattern_type": "descending" if is_descending else "ascending",
        "phase": phase,
        "top_idx": top_idx, "bottom_idx": bottom_idx,
        "flat_level": round(flat_level, 4),
        "window_start_idx": window_start,
        "breakout_idx": today_idx if confirmed else None,
        "breakout_price": round(float(last_close), 4) if confirmed else None,
        "measured_target": round(float(measured_target), 4),
        "volume_confirms": vol_confirms,
        "rsi_divergence": rsi_confirms,
        "pattern_r2": round(float((high_r2 + low_r2) / 2.0), 4),
        "prior_trend_pct": round(float(trend_pct), 2),
        "close_price": round(float(last_close), 4),
    }


def detect(ticker: str, df: pd.DataFrame, rsi_series: pd.Series, vol_sma: pd.Series, config: dict) -> Optional[dict]:
    """Registry contract entrypoint — see pattern_detection_engine.DETECTORS."""
    if len(df) < _MIN_BARS:
        return None

    sched_cfg = config.get("SCHEDULING", {}).get("PATTERN_DETECTION", {}).get("TRIANGLE", {})
    alert_cfg = config.get("NOTIFICATIONS", {}).get("PATTERN_DETECTION_ALERTS", {}).get("TRIANGLE", {})
    ascending_enabled = sched_cfg.get("ASCENDING_ENABLED", True)
    descending_enabled = sched_cfg.get("DESCENDING_ENABLED", True)
    window_days = int(alert_cfg.get("WINDOW_DAYS", _WINDOW_DAYS))
    flat_slope_epsilon_pct = alert_cfg.get("FLAT_SLOPE_EPSILON_PCT", 0.15)
    min_slope_pct = alert_cfg.get("MIN_SLOPE_PCT", 0.15)

    close = df["Close"].to_numpy()
    volume = df["Volume"].to_numpy()

    candidates = []
    if ascending_enabled:
        c = _detect_and_build(close, volume, rsi_series, False, window_days, flat_slope_epsilon_pct, min_slope_pct)
        if c:
            candidates.append(c)
    if descending_enabled:
        c = _detect_and_build(close, volume, rsi_series, True, window_days, flat_slope_epsilon_pct, min_slope_pct)
        if c:
            candidates.append(c)
    if not candidates:
        return None

    candidates.sort(key=lambda c: (c["phase"] != "CONFIRMED", -(c["pattern_r2"] or 0.0)))
    best = candidates[0]
    is_descending = best["pattern_type"] == "descending"

    idx = df.index
    flat_label = "Support" if is_descending else "Resistance"
    sloped_label = "Resistance (Falling)" if is_descending else "Support (Rising)"
    flat_points_idx = best["bottom_idx"] if is_descending else best["top_idx"]
    sloped_points_idx = best["top_idx"] if is_descending else best["bottom_idx"]

    points = [
        {"label": f"{flat_label} Touch {i + 1}", "date": idx[int(p)].strftime("%Y-%m-%d"), "price": round(float(close[int(p)]), 4)}
        for i, p in enumerate(flat_points_idx)
    ] + [
        {"label": f"{sloped_label} Touch {i + 1}", "date": idx[int(p)].strftime("%Y-%m-%d"), "price": round(float(close[int(p)]), 4)}
        for i, p in enumerate(sloped_points_idx)
    ]
    last_idx = len(df) - 1
    lines = [{
        "label": flat_label,
        "date_from": idx[int(flat_points_idx[0])].strftime("%Y-%m-%d"), "price_from": best["flat_level"],
        "date_to": idx[last_idx].strftime("%Y-%m-%d"), "price_to": best["flat_level"],
        "dash": True,
    }]
    breakout_idx = best["breakout_idx"]

    return {
        "pattern_type": best["pattern_type"],
        "phase": best["phase"],
        "points": points,
        "lines": lines,
        "key_level": best["flat_level"],
        "breakout_date": idx[breakout_idx].strftime("%Y-%m-%d") if breakout_idx is not None else None,
        "breakout_price": best["breakout_price"],
        "measured_target": best["measured_target"],
        "volume_confirms": best["volume_confirms"],
        "rsi_divergence": best["rsi_divergence"],
        "pattern_r2": best["pattern_r2"],
        "prior_trend_pct": best["prior_trend_pct"],
        "close_price": best["close_price"],
    }
