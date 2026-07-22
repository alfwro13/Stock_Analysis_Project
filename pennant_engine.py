from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from pattern_geometry_helpers import detect_pole, find_pivots, linreg, slope_pct_per_day

# GUI name: "Pattern Detection" (Pennant family). Canonical scheduled-job names live in
# scheduler_manifest.JOB_GRAPH; the registry contract lives in pattern_detection_engine.py.

_ORDER = 2
_MIN_BARS = 60

FAMILY = "pennant"
PATTERN_TYPES: dict[str, str] = {
    "bull_pennant": "up",
    "bear_pennant": "down",
}

_PATTERN_LABELS: dict[str, str] = {
    "bull_pennant": "Bull Pennant",
    "bear_pennant": "Bear Pennant",
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
    min_slope_pct: float,
) -> Optional[dict]:
    """Finds and validates the most recent Bull Pennant (is_bear=False) or Bear Pennant
    (is_bear=True) candidate ending at the last bar of `close`. Shares Flag's pole prerequisite
    (a >=sigma_multiplier-sigma, time-scaled move over `flagpole_days`) via `detect_pole`, but
    the consolidation itself is a micro symmetrical triangle rather than a parallel channel: a
    falling resistance line and a rising support line, each at least `min_slope_pct` steep,
    converging together — the same shape test Triangle's Symmetrical Triangle uses, just over a
    much shorter window. Confirmation direction is fixed by the pole (unlike Symmetrical
    Triangle, where it's unknown until breakout): a Bull Pennant only confirms on a break above
    resistance, a Bear Pennant only on a break below support."""
    n = len(close)
    today_idx = n - 1

    for consolidation_days in range(max_consolidation_days, min_consolidation_days - 1, -1):
        consolidation_start = today_idx - consolidation_days + 1
        pole = detect_pole(close, consolidation_start, is_bear, sigma_multiplier, flagpole_days, sigma_window_days)
        if pole is None:
            continue
        pole_start = pole["pole_start_idx"]
        pole_end = pole["pole_end_idx"]
        pole_start_price = pole["pole_start_price"]
        pole_end_price = pole["pole_end_price"]
        flagpole_return_pct = pole["flagpole_return_pct"]

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
        if high_slope_pct > -min_slope_pct or low_slope_pct < min_slope_pct:
            continue

        resistance_start = high_slope * consolidation_start + high_intercept
        support_start = low_slope * consolidation_start + low_intercept
        resistance_today = high_slope * today_idx + high_intercept
        support_today = low_slope * today_idx + low_intercept
        if resistance_start <= support_start or resistance_today <= support_today:
            continue

        last_close = close[today_idx]
        confirmed = (last_close < support_today) if is_bear else (last_close > resistance_today)
        phase = "CONFIRMED" if confirmed else "FORMING"

        vol_fit = linreg(np.arange(consolidation_start, today_idx + 1), volume[consolidation_start:today_idx + 1])
        vol_confirms = bool(vol_fit is not None and vol_fit[0] < 0)

        flagpole_height = abs(pole_end_price - pole_start_price)
        breakout_ref = last_close if confirmed else (support_today if is_bear else resistance_today)
        measured_target = (breakout_ref - flagpole_height) if is_bear else (breakout_ref + flagpole_height)
        key_level = support_today if is_bear else resistance_today

        rsi_pole_end = rsi_series.iloc[pole_end]
        rsi_today = rsi_series.iloc[today_idx]
        rsi_confirms = (
            bool(rsi_today > rsi_pole_end) if is_bear else bool(rsi_today < rsi_pole_end)
        ) if not (pd.isna(rsi_pole_end) or pd.isna(rsi_today)) else False

        return {
            "pattern_type": "bear_pennant" if is_bear else "bull_pennant",
            "phase": phase,
            "pole_start_idx": pole_start, "pole_start_price": round(float(pole_start_price), 4),
            "pole_end_idx": pole_end, "pole_end_price": round(float(pole_end_price), 4),
            "consolidation_start_idx": consolidation_start,
            "top_idx": top_idx, "bottom_idx": bottom_idx,
            "key_level": round(float(key_level), 4),
            "breakout_idx": today_idx if confirmed else None,
            "breakout_price": round(float(last_close), 4) if confirmed else None,
            "measured_target": round(float(measured_target), 4),
            "volume_confirms": vol_confirms,
            "rsi_divergence": rsi_confirms,
            "pattern_r2": round(float((high_r2 + low_r2) / 2.0), 4),
            "prior_trend_pct": round(float(flagpole_return_pct), 2),
            "close_price": round(float(last_close), 4),
            "resistance_slope": float(high_slope), "resistance_intercept": float(high_intercept),
            "support_slope": float(low_slope), "support_intercept": float(low_intercept),
        }

    return None


def detect(ticker: str, df: pd.DataFrame, rsi_series: pd.Series, vol_sma: pd.Series, config: dict) -> Optional[dict]:
    """Registry contract entrypoint — see pattern_detection_engine.DETECTORS."""
    if len(df) < _MIN_BARS:
        return None

    sched_cfg = config.get("SCHEDULING", {}).get("PATTERN_DETECTION", {}).get("PENNANT", {})
    alert_cfg = config.get("NOTIFICATIONS", {}).get("PATTERN_DETECTION_ALERTS", {}).get("PENNANT", {})
    bull_enabled = sched_cfg.get("BULL_ENABLED", True)
    bear_enabled = sched_cfg.get("BEAR_ENABLED", True)
    sigma_multiplier = alert_cfg.get("SIGMA_MULTIPLIER", 1.5)
    flagpole_days = int(alert_cfg.get("FLAGPOLE_LOOKBACK_DAYS", 10))
    sigma_window_days = int(alert_cfg.get("SIGMA_WINDOW_DAYS", 20))
    min_consolidation_days = int(alert_cfg.get("MIN_CONSOLIDATION_DAYS", 5))
    max_consolidation_days = int(alert_cfg.get("MAX_CONSOLIDATION_DAYS", 12))
    min_slope_pct = alert_cfg.get("MIN_SLOPE_PCT", 0.15)

    close = df["Close"].to_numpy()
    volume = df["Volume"].to_numpy()

    candidates = []
    if bull_enabled:
        c = _detect_and_build(close, volume, rsi_series, False, sigma_multiplier, flagpole_days, sigma_window_days, min_consolidation_days, max_consolidation_days, min_slope_pct)
        if c:
            candidates.append(c)
    if bear_enabled:
        c = _detect_and_build(close, volume, rsi_series, True, sigma_multiplier, flagpole_days, sigma_window_days, min_consolidation_days, max_consolidation_days, min_slope_pct)
        if c:
            candidates.append(c)
    if not candidates:
        return None

    candidates.sort(key=lambda c: (c["phase"] != "CONFIRMED", -(c["pattern_r2"] or 0.0)))
    best = candidates[0]

    idx = df.index
    last_idx = len(df) - 1
    consolidation_start_idx = best["consolidation_start_idx"]
    breakout_idx = best["breakout_idx"]

    points = [
        {"label": "Pole Start", "date": idx[best["pole_start_idx"]].strftime("%Y-%m-%d"), "price": best["pole_start_price"]},
        {"label": "Pole End", "date": idx[best["pole_end_idx"]].strftime("%Y-%m-%d"), "price": best["pole_end_price"]},
    ] + [
        {"label": f"Support Touch {i + 1}", "date": idx[int(p)].strftime("%Y-%m-%d"), "price": round(float(close[int(p)]), 4)}
        for i, p in enumerate(best["bottom_idx"])
    ] + [
        {"label": f"Resistance Touch {i + 1}", "date": idx[int(p)].strftime("%Y-%m-%d"), "price": round(float(close[int(p)]), 4)}
        for i, p in enumerate(best["top_idx"])
    ]
    lines = [
        {
            "label": "Resistance (Falling)",
            "date_from": idx[consolidation_start_idx].strftime("%Y-%m-%d"),
            "price_from": round(best["resistance_slope"] * consolidation_start_idx + best["resistance_intercept"], 4),
            "date_to": idx[last_idx].strftime("%Y-%m-%d"),
            "price_to": round(best["resistance_slope"] * last_idx + best["resistance_intercept"], 4),
            "dash": True,
        },
        {
            "label": "Support (Rising)",
            "date_from": idx[consolidation_start_idx].strftime("%Y-%m-%d"),
            "price_from": round(best["support_slope"] * consolidation_start_idx + best["support_intercept"], 4),
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
