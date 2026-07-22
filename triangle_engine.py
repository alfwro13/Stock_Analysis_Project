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
    "symmetrical_triangle_bullish": "up",
    "symmetrical_triangle_bearish": "down",
}

_PATTERN_LABELS: dict[str, str] = {
    "ascending": "Ascending Triangle",
    "descending": "Descending Triangle",
    "symmetrical_triangle": "Symmetrical Triangle",
    "symmetrical_triangle_bullish": "Symmetrical Triangle (Bullish)",
    "symmetrical_triangle_bearish": "Symmetrical Triangle (Bearish)",
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


def _detect_symmetrical(
    close: np.ndarray,
    volume: np.ndarray,
    rsi_series: pd.Series,
    window_days: int,
    min_slope_pct: float,
    bullish_enabled: bool,
    bearish_enabled: bool,
) -> Optional[dict]:
    """Finds and validates the most recent Symmetrical Triangle candidate: resistance falling
    (<= -min_slope_pct %/day) and support rising (>= min_slope_pct %/day) over the same trailing
    window used by Ascending/Descending, so the two lines are genuinely converging rather than
    running parallel (a wedge, both slopes the same sign) or apart (a megaphone). Direction is
    unknown until price closes decisively past either line — mirrors volatility_squeeze_engine's
    FORMING (neutral) -> CONFIRMED (directional) contract rather than Ascending/Descending's
    direction-known-from-the-start one, since a genuine symmetrical triangle can break either way."""
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
    if high_slope_pct > -min_slope_pct or low_slope_pct < min_slope_pct:
        return None

    resistance_start = high_slope * window_start + high_intercept
    support_start = low_slope * window_start + low_intercept
    resistance_today = high_slope * today_idx + high_intercept
    support_today = low_slope * today_idx + low_intercept
    if resistance_start <= support_start or resistance_today <= support_today:
        # Lines don't actually bracket a range at the back of the window, or have already
        # crossed (apex passed) by today — not a valid, still-converging triangle.
        return None

    last_close = close[today_idx]
    if last_close > resistance_today:
        pattern_type = "symmetrical_triangle_bullish"
        if not bullish_enabled:
            return None
        phase = "CONFIRMED"
    elif last_close < support_today:
        pattern_type = "symmetrical_triangle_bearish"
        if not bearish_enabled:
            return None
        phase = "CONFIRMED"
    else:
        pattern_type = "symmetrical_triangle"
        phase = "FORMING"

    breakout_idx = today_idx if phase == "CONFIRMED" else None
    height = float(resistance_start - support_start)
    if phase == "CONFIRMED":
        measured_target = (
            float(last_close) + height if pattern_type == "symmetrical_triangle_bullish"
            else float(last_close) - height
        )
        key_level = resistance_today if pattern_type == "symmetrical_triangle_bullish" else support_today
    else:
        measured_target = float(last_close)
        gap_up = resistance_today - last_close
        gap_down = last_close - support_today
        key_level = resistance_today if gap_up <= gap_down else support_today

    vol_fit = linreg(np.arange(window_start, today_idx + 1), volume[window_start:today_idx + 1])
    volume_confirms = bool(vol_fit is not None and vol_fit[0] < 0)

    rsi_first = rsi_series.iloc[int(window_start)]
    rsi_ref_idx = breakout_idx if breakout_idx is not None else today_idx
    rsi_last = rsi_series.iloc[int(rsi_ref_idx)]
    if pd.isna(rsi_first) or pd.isna(rsi_last):
        rsi_confirms = False
    elif pattern_type == "symmetrical_triangle_bullish":
        rsi_confirms = bool(rsi_last > rsi_first)
    elif pattern_type == "symmetrical_triangle_bearish":
        rsi_confirms = bool(rsi_last < rsi_first)
    else:
        rsi_confirms = False

    prior_ref_idx = max(0, window_start - 10)
    prior_close = close[prior_ref_idx]
    prior_trend_pct = (close[window_start] - prior_close) / prior_close * 100.0 if prior_close > 0 else 0.0

    return {
        "pattern_type": pattern_type,
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

    sched_cfg = config.get("SCHEDULING", {}).get("PATTERN_DETECTION", {}).get("TRIANGLE", {})
    alert_cfg = config.get("NOTIFICATIONS", {}).get("PATTERN_DETECTION_ALERTS", {}).get("TRIANGLE", {})
    ascending_enabled = sched_cfg.get("ASCENDING_ENABLED", True)
    descending_enabled = sched_cfg.get("DESCENDING_ENABLED", True)
    bullish_enabled = sched_cfg.get("BULLISH_ENABLED", True)
    bearish_enabled = sched_cfg.get("BEARISH_ENABLED", True)
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
    c = _detect_symmetrical(close, volume, rsi_series, window_days, min_slope_pct, bullish_enabled, bearish_enabled)
    if c:
        candidates.append(c)
    if not candidates:
        return None

    candidates.sort(key=lambda c: (c["phase"] != "CONFIRMED", -(c["pattern_r2"] or 0.0)))
    best = candidates[0]
    is_symmetrical = best["pattern_type"].startswith("symmetrical_triangle")

    idx = df.index
    last_idx = len(df) - 1
    breakout_idx = best["breakout_idx"]

    if is_symmetrical:
        window_start_idx = best["window_start_idx"]
        points = [
            {"label": f"Support Touch {i + 1}", "date": idx[int(p)].strftime("%Y-%m-%d"), "price": round(float(close[int(p)]), 4)}
            for i, p in enumerate(best["bottom_idx"])
        ] + [
            {"label": f"Resistance Touch {i + 1}", "date": idx[int(p)].strftime("%Y-%m-%d"), "price": round(float(close[int(p)]), 4)}
            for i, p in enumerate(best["top_idx"])
        ]
        lines = [
            {
                "label": "Resistance (Falling)",
                "date_from": idx[window_start_idx].strftime("%Y-%m-%d"),
                "price_from": round(best["resistance_slope"] * window_start_idx + best["resistance_intercept"], 4),
                "date_to": idx[last_idx].strftime("%Y-%m-%d"),
                "price_to": round(best["resistance_slope"] * last_idx + best["resistance_intercept"], 4),
                "dash": True,
            },
            {
                "label": "Support (Rising)",
                "date_from": idx[window_start_idx].strftime("%Y-%m-%d"),
                "price_from": round(best["support_slope"] * window_start_idx + best["support_intercept"], 4),
                "date_to": idx[last_idx].strftime("%Y-%m-%d"),
                "price_to": round(best["support_slope"] * last_idx + best["support_intercept"], 4),
                "dash": True,
            },
        ]
        key_level = best["key_level"]
    else:
        is_descending = best["pattern_type"] == "descending"
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
        lines = [{
            "label": flat_label,
            "date_from": idx[int(flat_points_idx[0])].strftime("%Y-%m-%d"), "price_from": best["flat_level"],
            "date_to": idx[last_idx].strftime("%Y-%m-%d"), "price_to": best["flat_level"],
            "dash": True,
        }]
        key_level = best["flat_level"]

    return {
        "pattern_type": best["pattern_type"],
        "phase": best["phase"],
        "points": points,
        "lines": lines,
        "key_level": key_level,
        "breakout_date": idx[breakout_idx].strftime("%Y-%m-%d") if breakout_idx is not None else None,
        "breakout_price": best["breakout_price"],
        "measured_target": best["measured_target"],
        "volume_confirms": best["volume_confirms"],
        "rsi_divergence": best["rsi_divergence"],
        "pattern_r2": best["pattern_r2"],
        "prior_trend_pct": best["prior_trend_pct"],
        "close_price": best["close_price"],
    }
