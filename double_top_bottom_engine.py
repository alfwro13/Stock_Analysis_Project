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

# GUI name: "Pattern Detection" (Double Top / Double Bottom family). Canonical scheduled-job
# names live in scheduler_manifest.JOB_GRAPH; the registry contract lives in pattern_detection_engine.py.

_ORDER = 5
_MIN_BARS = 60
_PRIOR_TREND_LOOKBACK = 20

FAMILY = "double_top_bottom"
PATTERN_TYPES: dict[str, str] = {
    "double_top": "down",
    "double_bottom": "up",
}

_PATTERN_LABELS: dict[str, str] = {
    "double_top": "Double Top",
    "double_bottom": "Double Bottom",
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
    vol_sma: pd.Series,
    is_bottom: bool,
    prior_trend_min_pct: float,
    volume_confirm_multiplier: float,
    balance_tolerance_pct: float,
    min_separation_pct: float,
) -> Optional[dict]:
    """Finds and validates the most recent Double Top (is_bottom=False) or Double Bottom
    (is_bottom=True) candidate ending at the last bar of `close`. A Double Top is two peaks
    of near-equal height separated by one qualifying trough; a Double Bottom is the mirror
    image. Returns an index-keyed result dict, or None if no valid candidate exists."""
    extrema = latest_alternating_run(close, _ORDER, 3, -1 if is_bottom else 1)
    if not extrema:
        return None
    ext1, mid, ext2 = extrema

    ext1_price = close[ext1]
    mid_price = close[mid]
    ext2_price = close[ext2]

    if is_bottom:
        if not (ext1_price < mid_price and ext2_price < mid_price):
            return None
    else:
        if not (ext1_price > mid_price and ext2_price > mid_price):
            return None

    avg_extreme = 0.5 * (ext1_price + ext2_price)
    if avg_extreme <= 0:
        return None

    diff_pct = abs(ext1_price - ext2_price) / avg_extreme * 100.0
    if diff_pct > balance_tolerance_pct:
        return None

    separation_pct = abs(mid_price - avg_extreme) / avg_extreme * 100.0
    if separation_pct < min_separation_pct:
        return None

    lookback_start = ext1 - _PRIOR_TREND_LOOKBACK
    if lookback_start < 0:
        return None
    prior_ref = close[lookback_start]
    if prior_ref <= 0:
        return None
    prior_change_pct = (ext1_price - prior_ref) / prior_ref * 100.0
    if is_bottom:
        if prior_change_pct > -prior_trend_min_pct:
            return None
    else:
        if prior_change_pct < prior_trend_min_pct:
            return None

    today_idx = len(close) - 1
    last_close = close[today_idx]
    confirmed = (last_close > mid_price) if is_bottom else (last_close < mid_price)
    phase = "CONFIRMED" if confirmed else "FORMING"

    height = abs(mid_price - avg_extreme)
    measured_target = (mid_price + height) if is_bottom else (mid_price - height)

    r2 = piecewise_r2(close, [ext1, mid, ext2], today_idx)
    vol_confirms = volume_confirms(volume, vol_sma, ext1, ext2, today_idx, confirmed, volume_confirm_multiplier)
    rsi_diverges = rsi_divergence(rsi_series, ext1, ext2, is_bottom)

    return {
        "pattern_type": "double_bottom" if is_bottom else "double_top",
        "phase": phase,
        "ext1_idx": ext1, "ext1_price": round(float(ext1_price), 4),
        "mid_idx": mid, "mid_price": round(float(mid_price), 4),
        "ext2_idx": ext2, "ext2_price": round(float(ext2_price), 4),
        "neck_value": round(float(mid_price), 4),
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
    """Registry contract entrypoint — see pattern_detection_engine.DETECTORS."""
    if len(df) < _MIN_BARS:
        return None

    sched_cfg = config.get("SCHEDULING", {}).get("PATTERN_DETECTION", {}).get("DOUBLE_TOP_BOTTOM", {})
    alert_cfg = config.get("NOTIFICATIONS", {}).get("PATTERN_DETECTION_ALERTS", {}).get("DOUBLE_TOP_BOTTOM", {})
    top_enabled = sched_cfg.get("TOP_ENABLED", True)
    bottom_enabled = sched_cfg.get("BOTTOM_ENABLED", True)
    prior_trend_min_pct = alert_cfg.get("PRIOR_TREND_MIN_PCT", 8.0)
    volume_confirm_multiplier = alert_cfg.get("VOLUME_CONFIRM_MULTIPLIER", 1.5)
    balance_tolerance_pct = alert_cfg.get("BALANCE_TOLERANCE_PCT", 3.0)
    min_separation_pct = alert_cfg.get("MIN_SEPARATION_PCT", 3.0)

    close = df["Close"].to_numpy()
    volume = df["Volume"].to_numpy()

    candidates = []
    if top_enabled:
        c = _detect_and_build(close, volume, rsi_series, vol_sma, False, prior_trend_min_pct, volume_confirm_multiplier, balance_tolerance_pct, min_separation_pct)
        if c:
            candidates.append(c)
    if bottom_enabled:
        c = _detect_and_build(close, volume, rsi_series, vol_sma, True, prior_trend_min_pct, volume_confirm_multiplier, balance_tolerance_pct, min_separation_pct)
        if c:
            candidates.append(c)
    if not candidates:
        return None

    candidates.sort(key=lambda c: (c["phase"] != "CONFIRMED", -(c["pattern_r2"] or 0.0)))
    best = candidates[0]
    is_bottom = best["pattern_type"] == "double_bottom"

    idx = df.index
    label1 = "Trough 1" if is_bottom else "Peak 1"
    label_mid = "Peak" if is_bottom else "Trough"
    label2 = "Trough 2" if is_bottom else "Peak 2"
    points = [
        {"label": label1, "date": idx[best["ext1_idx"]].strftime("%Y-%m-%d"), "price": best["ext1_price"]},
        {"label": label_mid, "date": idx[best["mid_idx"]].strftime("%Y-%m-%d"), "price": best["mid_price"]},
        {"label": label2, "date": idx[best["ext2_idx"]].strftime("%Y-%m-%d"), "price": best["ext2_price"]},
    ]
    lines = [{
        "label": "Resistance" if is_bottom else "Support",
        "date_from": points[0]["date"], "price_from": best["mid_price"],
        "date_to": points[2]["date"], "price_to": best["mid_price"],
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
