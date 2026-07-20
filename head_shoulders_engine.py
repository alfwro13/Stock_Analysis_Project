from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from pattern_geometry_helpers import (
    latest_alternating_run,
    piecewise_r2,
    volume_confirms,
    rsi_divergence,
)

# GUI name: "Pattern Detection" (Head & Shoulders family). Canonical scheduled-job names
# live in scheduler_manifest.JOB_GRAPH; the registry contract lives in pattern_detection_engine.py.

_ORDER = 5
_MIN_BARS = 60
_PRIOR_TREND_LOOKBACK = 20
_TIME_SYMMETRY_MAX_RATIO = 2.75
_BALANCE_TOLERANCE = 0.90

FAMILY = "head_shoulders"
PATTERN_TYPES: dict[str, str] = {
    "regular": "down",
    "inverse": "up",
}

_PATTERN_LABELS: dict[str, str] = {
    "regular": "Head & Shoulders",
    "inverse": "Inverse Head & Shoulders",
}


def phase_label(pattern_type: Optional[str], phase: Optional[str]) -> str:
    """Canonical human-readable label combining pattern type and phase, e.g. for the
    Portfolio/Watchlist "Setups & Tags" badge — used wherever the two need to read as one tag."""
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
    vol_sma: pd.Series,
    inverted: bool,
    prior_trend_min_pct: float,
    volume_confirm_multiplier: float,
) -> Optional[dict]:
    """Finds and validates the most recent Head & Shoulders (or Inverse) candidate ending at
    the last bar of `close`. Returns an index-keyed result dict, or None if no valid candidate
    exists as of that bar."""
    extrema = latest_alternating_run(close, _ORDER, 4, -1 if inverted else 1)
    if not extrema:
        return None
    l_shoulder, l_armpit, head, r_armpit = extrema

    tail = close[r_armpit + 1:]
    if len(tail) == 0:
        return None
    r_shoulder = r_armpit + 1 + (int(np.argmin(tail)) if inverted else int(np.argmax(tail)))

    head_price = close[head]
    l_price = close[l_shoulder]
    r_price = close[r_shoulder]
    l_armpit_price = close[l_armpit]
    r_armpit_price = close[r_armpit]

    if inverted:
        if head_price >= min(l_price, r_price):
            return None
    else:
        if head_price <= max(l_price, r_price):
            return None

    r_midpoint = 0.5 * (r_price + r_armpit_price)
    l_midpoint = 0.5 * (l_price + l_armpit_price)
    if r_midpoint <= 0 or l_midpoint <= 0:
        return None
    if inverted:
        if l_price > r_midpoint / _BALANCE_TOLERANCE or r_price > l_midpoint / _BALANCE_TOLERANCE:
            return None
    else:
        if l_price < _BALANCE_TOLERANCE * r_midpoint or r_price < _BALANCE_TOLERANCE * l_midpoint:
            return None

    r_to_h_time = r_shoulder - head
    l_to_h_time = head - l_shoulder
    if r_to_h_time <= 0 or l_to_h_time <= 0:
        return None
    if r_to_h_time > _TIME_SYMMETRY_MAX_RATIO * l_to_h_time or l_to_h_time > _TIME_SYMMETRY_MAX_RATIO * r_to_h_time:
        return None

    lookback_start = l_shoulder - _PRIOR_TREND_LOOKBACK
    if lookback_start < 0:
        return None
    prior_ref = close[lookback_start]
    if prior_ref <= 0:
        return None
    prior_change_pct = (l_price - prior_ref) / prior_ref * 100.0
    if inverted:
        if prior_change_pct > -prior_trend_min_pct:
            return None
    else:
        if prior_change_pct < prior_trend_min_pct:
            return None

    neck_run = r_armpit - l_armpit
    if neck_run <= 0:
        return None
    neck_slope = (r_armpit_price - l_armpit_price) / neck_run

    today_idx = len(close) - 1
    neck_val_today = l_armpit_price + (today_idx - l_armpit) * neck_slope
    last_close = close[today_idx]
    confirmed = (last_close > neck_val_today) if inverted else (last_close < neck_val_today)
    phase = "CONFIRMED" if confirmed else "FORMING"

    neck_val_at_head = l_armpit_price + (head - l_armpit) * neck_slope
    head_height = abs(head_price - neck_val_at_head)
    measured_target = (neck_val_today + head_height) if inverted else (neck_val_today - head_height)

    r2 = piecewise_r2(close, [l_shoulder, l_armpit, head, r_armpit, r_shoulder], today_idx)
    vol_confirms = volume_confirms(volume, vol_sma, l_shoulder, r_shoulder, today_idx, confirmed, volume_confirm_multiplier)
    rsi_diverges = rsi_divergence(rsi_series, l_shoulder, head, inverted)

    return {
        "pattern_type": "inverse" if inverted else "regular",
        "phase": phase,
        "l_shoulder_idx": l_shoulder, "l_shoulder_price": round(float(l_price), 4),
        "l_armpit_idx": l_armpit, "l_armpit_price": round(float(l_armpit_price), 4),
        "head_idx": head, "head_price": round(float(head_price), 4),
        "r_armpit_idx": r_armpit, "r_armpit_price": round(float(r_armpit_price), 4),
        "r_shoulder_idx": r_shoulder, "r_shoulder_price": round(float(r_price), 4),
        "neck_value": round(float(neck_val_today), 4),
        "breakout_idx": today_idx if confirmed else None,
        "breakout_price": round(float(last_close), 4) if confirmed else None,
        "measured_target": round(float(measured_target), 4),
        "volume_confirms": vol_confirms,
        "rsi_divergence": rsi_diverges,
        "pattern_r2": round(float(r2), 4) if r2 is not None else None,
        "prior_trend_pct": round(float(prior_change_pct), 2),
        "close_price": round(float(last_close), 4),
    }


def detect(ticker: str, df: pd.DataFrame, rsi_series: pd.Series, vol_sma: pd.Series, config: dict) -> Optional[dict]:
    """Registry contract entrypoint — see pattern_detection_engine.DETECTORS. Picks the
    best-scoring candidate across whichever of regular/inverse are enabled and reshapes it
    into the engine's generic points/lines/key_level result shape."""
    if len(df) < _MIN_BARS:
        return None

    sched_cfg = config.get("SCHEDULING", {}).get("PATTERN_DETECTION", {}).get("HEAD_SHOULDERS", {})
    alert_cfg = config.get("NOTIFICATIONS", {}).get("PATTERN_DETECTION_ALERTS", {}).get("HEAD_SHOULDERS", {})
    regular_enabled = sched_cfg.get("REGULAR_ENABLED", True)
    inverse_enabled = sched_cfg.get("INVERSE_ENABLED", True)
    prior_trend_min_pct = alert_cfg.get("PRIOR_TREND_MIN_PCT", 8.0)
    volume_confirm_multiplier = alert_cfg.get("VOLUME_CONFIRM_MULTIPLIER", 1.5)

    close = df["Close"].to_numpy()
    volume = df["Volume"].to_numpy()

    candidates = []
    if regular_enabled:
        c = _detect_and_build(close, volume, rsi_series, vol_sma, False, prior_trend_min_pct, volume_confirm_multiplier)
        if c:
            candidates.append(c)
    if inverse_enabled:
        c = _detect_and_build(close, volume, rsi_series, vol_sma, True, prior_trend_min_pct, volume_confirm_multiplier)
        if c:
            candidates.append(c)
    if not candidates:
        return None

    candidates.sort(key=lambda c: (c["phase"] != "CONFIRMED", -(c["pattern_r2"] or 0.0)))
    best = candidates[0]

    idx = df.index
    points = [
        {"label": "L Shoulder", "date": idx[best["l_shoulder_idx"]].strftime("%Y-%m-%d"), "price": best["l_shoulder_price"]},
        {"label": "L Armpit", "date": idx[best["l_armpit_idx"]].strftime("%Y-%m-%d"), "price": best["l_armpit_price"]},
        {"label": "Head", "date": idx[best["head_idx"]].strftime("%Y-%m-%d"), "price": best["head_price"]},
        {"label": "R Armpit", "date": idx[best["r_armpit_idx"]].strftime("%Y-%m-%d"), "price": best["r_armpit_price"]},
        {"label": "R Shoulder", "date": idx[best["r_shoulder_idx"]].strftime("%Y-%m-%d"), "price": best["r_shoulder_price"]},
    ]
    lines = [{
        "label": "Neckline",
        "date_from": points[1]["date"], "price_from": best["l_armpit_price"],
        "date_to": points[3]["date"], "price_to": best["r_armpit_price"],
        "dash": True,
    }]
    breakout_idx = best["breakout_idx"]

    return {
        "pattern_type": best["pattern_type"],
        "phase": best["phase"],
        "points": points,
        "lines": lines,
        "key_level": best["neck_value"],
        "breakout_date": idx[breakout_idx].strftime("%Y-%m-%d") if breakout_idx is not None else None,
        "breakout_price": best["breakout_price"],
        "measured_target": best["measured_target"],
        "volume_confirms": best["volume_confirms"],
        "rsi_divergence": best["rsi_divergence"],
        "pattern_r2": best["pattern_r2"],
        "prior_trend_pct": best["prior_trend_pct"],
        "close_price": best["close_price"],
    }
