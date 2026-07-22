from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from pattern_geometry_helpers import find_pivots, linreg, slope_pct_per_day

# GUI name: "Pattern Detection" (Wedge family). Canonical scheduled-job names live in
# scheduler_manifest.JOB_GRAPH; the registry contract lives in pattern_detection_engine.py.

_ORDER = 3
_WINDOW_DAYS = 40
_MIN_BARS = 60

FAMILY = "wedge"
PATTERN_TYPES: dict[str, str] = {
    "rising_wedge": "down",
    "falling_wedge": "up",
}

_PATTERN_LABELS: dict[str, str] = {
    "rising_wedge": "Rising Wedge",
    "falling_wedge": "Falling Wedge",
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
    is_falling: bool,
    window_days: int,
    min_slope_pct: float,
    min_convergence_diff_pct: float,
) -> Optional[dict]:
    """Finds and validates the most recent Rising Wedge (is_falling=False) or Falling Wedge
    (is_falling=True) candidate over a trailing `window_days` window ending at the last bar of
    `close`. Unlike Triangle, both the resistance line (through swing highs) and the support
    line (through swing lows) must slope the same direction — both positive for a Rising Wedge,
    both negative for a Falling Wedge, each at least `min_slope_pct` steep — but the two lines
    must still be converging: the support line's slope must exceed the resistance line's slope
    by at least `min_convergence_diff_pct` (support catching up from below in a Rising Wedge;
    resistance falling away faster in a Falling Wedge), otherwise the channel is parallel (a
    Flag) rather than a genuine wedge. Both sides need >=2 swing points to fit a line."""
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

    if is_falling:
        if high_slope_pct > -min_slope_pct or low_slope_pct > -min_slope_pct:
            return None
    else:
        if high_slope_pct < min_slope_pct or low_slope_pct < min_slope_pct:
            return None
    if low_slope_pct - high_slope_pct < min_convergence_diff_pct:
        return None

    resistance_start = high_slope * window_start + high_intercept
    support_start = low_slope * window_start + low_intercept
    resistance_today = high_slope * today_idx + high_intercept
    support_today = low_slope * today_idx + low_intercept
    if resistance_start <= support_start or resistance_today <= support_today:
        # Lines don't actually bracket a range at the back of the window, or have already
        # crossed (apex passed) by today — not a valid, still-converging wedge.
        return None

    last_close = close[today_idx]
    confirmed = (last_close > resistance_today) if is_falling else (last_close < support_today)
    phase = "CONFIRMED" if confirmed else "FORMING"

    breakout_idx = today_idx if confirmed else None
    height = float(resistance_start - support_start)
    breakout_ref = last_close if confirmed else (resistance_today if is_falling else support_today)
    measured_target = (breakout_ref + height) if is_falling else (breakout_ref - height)
    key_level = resistance_today if is_falling else support_today

    vol_fit = linreg(np.arange(window_start, today_idx + 1), volume[window_start:today_idx + 1])
    volume_confirms = bool(vol_fit is not None and vol_fit[0] < 0)

    rsi_first = rsi_series.iloc[int(window_start)]
    rsi_ref_idx = breakout_idx if breakout_idx is not None else today_idx
    rsi_last = rsi_series.iloc[int(rsi_ref_idx)]
    if pd.isna(rsi_first) or pd.isna(rsi_last):
        rsi_confirms = False
    elif is_falling:
        rsi_confirms = bool(rsi_last > rsi_first)
    else:
        rsi_confirms = bool(rsi_last < rsi_first)

    prior_ref_idx = max(0, window_start - 10)
    prior_close = close[prior_ref_idx]
    prior_trend_pct = (close[window_start] - prior_close) / prior_close * 100.0 if prior_close > 0 else 0.0

    return {
        "pattern_type": "falling_wedge" if is_falling else "rising_wedge",
        "phase": phase,
        "top_idx": top_idx, "bottom_idx": bottom_idx,
        "window_start_idx": window_start,
        "breakout_idx": breakout_idx,
        "breakout_price": round(float(last_close), 4) if breakout_idx is not None else None,
        "measured_target": round(float(measured_target), 4),
        "key_level": round(float(key_level), 4),
        "volume_confirms": volume_confirms,
        "rsi_divergence": rsi_confirms,
        "pattern_r2": round(float((high_r2 + low_r2) / 2.0), 4),
        "prior_trend_pct": round(float(prior_trend_pct), 2),
        "close_price": round(float(last_close), 4),
        "resistance_slope": float(high_slope), "resistance_intercept": float(high_intercept),
        "support_slope": float(low_slope), "support_intercept": float(low_intercept),
    }


def detect(ticker: str, df: pd.DataFrame, rsi_series: pd.Series, vol_sma: pd.Series, config: dict) -> Optional[dict]:
    """Registry contract entrypoint — see pattern_detection_engine.DETECTORS."""
    if len(df) < _MIN_BARS:
        return None

    sched_cfg = config.get("SCHEDULING", {}).get("PATTERN_DETECTION", {}).get("WEDGE", {})
    alert_cfg = config.get("NOTIFICATIONS", {}).get("PATTERN_DETECTION_ALERTS", {}).get("WEDGE", {})
    rising_enabled = sched_cfg.get("RISING_ENABLED", True)
    falling_enabled = sched_cfg.get("FALLING_ENABLED", True)
    window_days = int(alert_cfg.get("WINDOW_DAYS", _WINDOW_DAYS))
    min_slope_pct = alert_cfg.get("MIN_SLOPE_PCT", 0.15)
    min_convergence_diff_pct = alert_cfg.get("MIN_CONVERGENCE_DIFF_PCT", 0.1)

    close = df["Close"].to_numpy()
    volume = df["Volume"].to_numpy()

    candidates = []
    if rising_enabled:
        c = _detect_and_build(close, volume, rsi_series, False, window_days, min_slope_pct, min_convergence_diff_pct)
        if c:
            candidates.append(c)
    if falling_enabled:
        c = _detect_and_build(close, volume, rsi_series, True, window_days, min_slope_pct, min_convergence_diff_pct)
        if c:
            candidates.append(c)
    if not candidates:
        return None

    candidates.sort(key=lambda c: (c["phase"] != "CONFIRMED", -(c["pattern_r2"] or 0.0)))
    best = candidates[0]

    idx = df.index
    last_idx = len(df) - 1
    window_start_idx = best["window_start_idx"]
    breakout_idx = best["breakout_idx"]

    points = [
        {"label": f"Support Touch {i + 1}", "date": idx[int(p)].strftime("%Y-%m-%d"), "price": round(float(close[int(p)]), 4)}
        for i, p in enumerate(best["bottom_idx"])
    ] + [
        {"label": f"Resistance Touch {i + 1}", "date": idx[int(p)].strftime("%Y-%m-%d"), "price": round(float(close[int(p)]), 4)}
        for i, p in enumerate(best["top_idx"])
    ]
    lines = [
        {
            "label": "Resistance",
            "date_from": idx[window_start_idx].strftime("%Y-%m-%d"),
            "price_from": round(best["resistance_slope"] * window_start_idx + best["resistance_intercept"], 4),
            "date_to": idx[last_idx].strftime("%Y-%m-%d"),
            "price_to": round(best["resistance_slope"] * last_idx + best["resistance_intercept"], 4),
            "dash": True,
        },
        {
            "label": "Support",
            "date_from": idx[window_start_idx].strftime("%Y-%m-%d"),
            "price_from": round(best["support_slope"] * window_start_idx + best["support_intercept"], 4),
            "date_to": idx[last_idx].strftime("%Y-%m-%d"),
            "price_to": round(best["support_slope"] * last_idx + best["support_intercept"], 4),
            "dash": True,
        },
    ]

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
