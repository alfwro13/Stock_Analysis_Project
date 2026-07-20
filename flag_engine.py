from __future__ import annotations

import math
from typing import Optional

import numpy as np
import pandas as pd

from pattern_geometry_helpers import find_pivots, linreg, slope_pct_per_day

# GUI name: "Pattern Detection" (Flag family). Canonical scheduled-job names live in
# scheduler_manifest.JOB_GRAPH; the registry contract lives in pattern_detection_engine.py.

_ORDER = 2
_MIN_BARS = 60

FAMILY = "flag"
PATTERN_TYPES: dict[str, str] = {
    "bull_flag": "up",
    "bear_flag": "down",
}

_PATTERN_LABELS: dict[str, str] = {
    "bull_flag": "Bull Flag",
    "bear_flag": "Bear Flag",
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
    is_bear: bool,
    sigma_multiplier: float,
    flagpole_days: int,
    sigma_window_days: int,
    min_consolidation_days: int,
    max_consolidation_days: int,
    max_channel_slope_pct: float,
    parallel_tolerance_pct: float,
) -> Optional[dict]:
    """Finds and validates the most recent Bull Flag (is_bear=False) or Bear Flag (is_bear=True)
    candidate ending at the last bar of `close`. A flagpole is a >1.5-sigma (time-scaled) move
    over `flagpole_days`; the flag itself is an M-day channel — two independently-fit regression
    lines through the consolidation's swing highs/lows, sloped against the pole's direction
    (or flat), within `parallel_tolerance_pct` of each other, with declining volume. Returns an
    index-keyed result dict, or None if no valid candidate exists as of that bar."""
    n = len(close)
    today_idx = n - 1
    daily_returns = np.diff(close) / close[:-1]

    for consolidation_days in range(max_consolidation_days, min_consolidation_days - 1, -1):
        consolidation_start = today_idx - consolidation_days + 1
        pole_end = consolidation_start - 1
        pole_start = pole_end - flagpole_days + 1
        if pole_start - sigma_window_days < 0 or pole_end <= pole_start:
            continue

        pole_start_price = close[pole_start]
        pole_end_price = close[pole_end]
        if pole_start_price <= 0:
            continue
        flagpole_return_pct = (pole_end_price - pole_start_price) / pole_start_price * 100.0

        sigma_window = daily_returns[pole_start - sigma_window_days:pole_start]
        if len(sigma_window) < sigma_window_days:
            continue
        sigma_daily = float(np.std(sigma_window, ddof=1))
        if sigma_daily <= 0:
            continue
        threshold_pct = sigma_multiplier * sigma_daily * math.sqrt(flagpole_days) * 100.0

        if is_bear:
            if flagpole_return_pct > -threshold_pct:
                continue
        else:
            if flagpole_return_pct < threshold_pct:
                continue

        consolidation_close = close[consolidation_start:today_idx + 1]
        top_local, bottom_local = find_pivots(consolidation_close, _ORDER)
        if len(top_local) < 2 or len(bottom_local) < 2:
            continue
        top_idx = np.array([consolidation_start + i for i in top_local])
        bottom_idx = np.array([consolidation_start + i for i in bottom_local])

        high_fit = linreg(top_idx, close[top_idx])
        low_fit = linreg(bottom_idx, close[bottom_idx])
        if high_fit is None or low_fit is None:
            continue
        high_slope, high_intercept, high_r2 = high_fit
        low_slope, low_intercept, low_r2 = low_fit

        ref_price = close[consolidation_start]
        high_slope_pct = slope_pct_per_day(high_slope, ref_price)
        low_slope_pct = slope_pct_per_day(low_slope, ref_price)

        if is_bear:
            if not (0.0 <= high_slope_pct <= max_channel_slope_pct and 0.0 <= low_slope_pct <= max_channel_slope_pct):
                continue
        else:
            if not (-max_channel_slope_pct <= high_slope_pct <= 0.0 and -max_channel_slope_pct <= low_slope_pct <= 0.0):
                continue
        if abs(high_slope_pct - low_slope_pct) > parallel_tolerance_pct:
            continue

        vol_fit = linreg(np.arange(consolidation_start, today_idx + 1), volume[consolidation_start:today_idx + 1])
        if vol_fit is None:
            continue
        vol_slope, _, _ = vol_fit
        vol_confirms = bool(vol_slope < 0)

        upper_today = high_slope * today_idx + high_intercept
        lower_today = low_slope * today_idx + low_intercept
        last_close = close[today_idx]
        confirmed = (last_close < lower_today) if is_bear else (last_close > upper_today)
        phase = "CONFIRMED" if confirmed else "FORMING"

        flagpole_height = abs(pole_end_price - pole_start_price)
        breakout_ref = last_close if confirmed else (lower_today if is_bear else upper_today)
        measured_target = (breakout_ref - flagpole_height) if is_bear else (breakout_ref + flagpole_height)

        rsi_pole_end = rsi_series.iloc[pole_end]
        rsi_today = rsi_series.iloc[today_idx]
        rsi_confirms = (
            bool(rsi_today > rsi_pole_end) if is_bear else bool(rsi_today < rsi_pole_end)
        ) if not (pd.isna(rsi_pole_end) or pd.isna(rsi_today)) else False

        return {
            "pattern_type": "bear_flag" if is_bear else "bull_flag",
            "phase": phase,
            "pole_start_idx": pole_start, "pole_start_price": round(float(pole_start_price), 4),
            "pole_end_idx": pole_end, "pole_end_price": round(float(pole_end_price), 4),
            "consolidation_start_idx": consolidation_start,
            "upper_start": round(float(high_slope * consolidation_start + high_intercept), 4),
            "upper_today": round(float(upper_today), 4),
            "lower_start": round(float(low_slope * consolidation_start + low_intercept), 4),
            "lower_today": round(float(lower_today), 4),
            "key_level": round(float(lower_today if is_bear else upper_today), 4),
            "breakout_idx": today_idx if confirmed else None,
            "breakout_price": round(float(last_close), 4) if confirmed else None,
            "measured_target": round(float(measured_target), 4),
            "volume_confirms": vol_confirms,
            "rsi_divergence": rsi_confirms,
            "pattern_r2": round(float((high_r2 + low_r2) / 2.0), 4),
            "prior_trend_pct": round(float(flagpole_return_pct), 2),
            "close_price": round(float(last_close), 4),
        }

    return None


def detect(ticker: str, df: pd.DataFrame, rsi_series: pd.Series, vol_sma: pd.Series, config: dict) -> Optional[dict]:
    """Registry contract entrypoint — see pattern_detection_engine.DETECTORS."""
    if len(df) < _MIN_BARS:
        return None

    sched_cfg = config.get("SCHEDULING", {}).get("PATTERN_DETECTION", {}).get("FLAG", {})
    alert_cfg = config.get("NOTIFICATIONS", {}).get("PATTERN_DETECTION_ALERTS", {}).get("FLAG", {})
    bull_enabled = sched_cfg.get("BULL_ENABLED", True)
    bear_enabled = sched_cfg.get("BEAR_ENABLED", True)
    sigma_multiplier = alert_cfg.get("SIGMA_MULTIPLIER", 1.5)
    flagpole_days = int(alert_cfg.get("FLAGPOLE_LOOKBACK_DAYS", 10))
    sigma_window_days = int(alert_cfg.get("SIGMA_WINDOW_DAYS", 20))
    min_consolidation_days = int(alert_cfg.get("MIN_CONSOLIDATION_DAYS", 7))
    max_consolidation_days = int(alert_cfg.get("MAX_CONSOLIDATION_DAYS", 15))
    max_channel_slope_pct = alert_cfg.get("MAX_CHANNEL_SLOPE_PCT", 0.75)
    parallel_tolerance_pct = alert_cfg.get("PARALLEL_TOLERANCE_PCT", 0.15)

    close = df["Close"].to_numpy()
    volume = df["Volume"].to_numpy()

    candidates = []
    if bull_enabled:
        c = _detect_and_build(close, volume, rsi_series, False, sigma_multiplier, flagpole_days, sigma_window_days, min_consolidation_days, max_consolidation_days, max_channel_slope_pct, parallel_tolerance_pct)
        if c:
            candidates.append(c)
    if bear_enabled:
        c = _detect_and_build(close, volume, rsi_series, True, sigma_multiplier, flagpole_days, sigma_window_days, min_consolidation_days, max_consolidation_days, max_channel_slope_pct, parallel_tolerance_pct)
        if c:
            candidates.append(c)
    if not candidates:
        return None

    candidates.sort(key=lambda c: (c["phase"] != "CONFIRMED", -(c["pattern_r2"] or 0.0)))
    best = candidates[0]

    idx = df.index
    points = [
        {"label": "Pole Start", "date": idx[best["pole_start_idx"]].strftime("%Y-%m-%d"), "price": best["pole_start_price"]},
        {"label": "Pole End", "date": idx[best["pole_end_idx"]].strftime("%Y-%m-%d"), "price": best["pole_end_price"]},
    ]
    lines = [
        {
            "label": "Upper Channel",
            "date_from": idx[best["consolidation_start_idx"]].strftime("%Y-%m-%d"), "price_from": best["upper_start"],
            "date_to": idx[len(df) - 1].strftime("%Y-%m-%d"), "price_to": best["upper_today"],
            "dash": True,
        },
        {
            "label": "Lower Channel",
            "date_from": idx[best["consolidation_start_idx"]].strftime("%Y-%m-%d"), "price_from": best["lower_start"],
            "date_to": idx[len(df) - 1].strftime("%Y-%m-%d"), "price_to": best["lower_today"],
            "dash": True,
        },
    ]
    breakout_idx = best["breakout_idx"]

    return {
        "pattern_type": best["pattern_type"],
        "phase": best["phase"],
        "points": points,
        "lines": lines,
        "key_level": best["key_level"],
        "breakout_date": idx[breakout_idx].strftime("%Y-%m-%d") if breakout_idx is not None else None,
        "breakout_price": best["breakout_price"],
        "measured_target": best["measured_target"],
        "volume_confirms": best["volume_confirms"],
        "rsi_divergence": best["rsi_divergence"],
        "pattern_r2": best["pattern_r2"],
        "prior_trend_pct": best["prior_trend_pct"],
        "close_price": best["close_price"],
    }
