from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from pattern_geometry_helpers import (
    latest_alternating_run,
    piecewise_r2,
    volume_confirms,
)

# GUI name: "Pattern Detection" (Bullish/Bearish Divergence family). Canonical scheduled-job
# names live in scheduler_manifest.JOB_GRAPH; the registry contract lives in pattern_detection_engine.py.

_ORDER = 5
_MIN_BARS = 60
_PRIOR_TREND_LOOKBACK = 20

FAMILY = "momentum_divergence"
PATTERN_TYPES: dict[str, str] = {
    "bearish_divergence": "down",
    "bullish_divergence": "up",
}

_PATTERN_LABELS: dict[str, str] = {
    "bearish_divergence": "Bearish Divergence",
    "bullish_divergence": "Bullish Divergence",
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
    min_price_change_pct: float,
    min_rsi_gap: float,
    volume_confirm_multiplier: float,
) -> Optional[dict]:
    """Finds and validates the most recent Bullish (is_bottom=True) or Bearish (is_bottom=False)
    Divergence candidate ending at the last bar of `close`: price makes a genuinely new extreme
    (a higher high for Bearish, a lower low for Bullish) versus the prior comparable extreme,
    while RSI moves the opposite way over the same two points — a leading signal that momentum
    is fading even as price still pushes further. Returns an index-keyed result dict, or None if
    no valid candidate exists."""
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

    if ext1_price <= 0:
        return None
    if is_bottom:
        price_change_pct = (ext1_price - ext2_price) / ext1_price * 100.0
    else:
        price_change_pct = (ext2_price - ext1_price) / ext1_price * 100.0
    if price_change_pct < min_price_change_pct:
        return None

    rsi1 = rsi_series.iloc[ext1]
    rsi2 = rsi_series.iloc[ext2]
    if pd.isna(rsi1) or pd.isna(rsi2):
        return None
    rsi_gap = (rsi2 - rsi1) if is_bottom else (rsi1 - rsi2)
    if rsi_gap < min_rsi_gap:
        return None

    today_idx = len(close) - 1
    last_close = close[today_idx]
    confirmed = (last_close > mid_price) if is_bottom else (last_close < mid_price)
    phase = "CONFIRMED" if confirmed else "FORMING"

    avg_extreme = 0.5 * (ext1_price + ext2_price)
    height = abs(mid_price - avg_extreme)
    measured_target = (mid_price + height) if is_bottom else (mid_price - height)

    r2 = piecewise_r2(close, [ext1, mid, ext2], today_idx)
    vol_confirms = volume_confirms(volume, vol_sma, ext1, ext2, today_idx, confirmed, volume_confirm_multiplier)

    prior_ref_idx = max(0, ext1 - _PRIOR_TREND_LOOKBACK)
    prior_ref = close[prior_ref_idx]
    prior_trend_pct = (ext1_price - prior_ref) / prior_ref * 100.0 if prior_ref > 0 else 0.0

    return {
        "pattern_type": "bullish_divergence" if is_bottom else "bearish_divergence",
        "phase": phase,
        "ext1_idx": ext1, "ext1_price": round(float(ext1_price), 4),
        "mid_idx": mid, "mid_price": round(float(mid_price), 4),
        "ext2_idx": ext2, "ext2_price": round(float(ext2_price), 4),
        "neck_value": round(float(mid_price), 4),
        "breakout_idx": today_idx if confirmed else None,
        "breakout_price": round(float(last_close), 4) if confirmed else None,
        "measured_target": round(float(measured_target), 4),
        "volume_confirms": vol_confirms,
        "rsi_divergence": True,
        "pattern_r2": round(float(r2), 4) if r2 is not None else None,
        "prior_trend_pct": round(float(prior_trend_pct), 2),
        "close_price": round(float(last_close), 4),
    }


def detect(ticker: str, df: pd.DataFrame, rsi_series: pd.Series, vol_sma: pd.Series, config: dict) -> Optional[dict]:
    """Registry contract entrypoint — see pattern_detection_engine.DETECTORS."""
    if len(df) < _MIN_BARS:
        return None

    sched_cfg = config.get("SCHEDULING", {}).get("PATTERN_DETECTION", {}).get("MOMENTUM_DIVERGENCE", {})
    alert_cfg = config.get("NOTIFICATIONS", {}).get("PATTERN_DETECTION_ALERTS", {}).get("MOMENTUM_DIVERGENCE", {})
    bearish_enabled = sched_cfg.get("BEARISH_ENABLED", True)
    bullish_enabled = sched_cfg.get("BULLISH_ENABLED", True)
    min_price_change_pct = alert_cfg.get("MIN_PRICE_CHANGE_PCT", 1.0)
    min_rsi_gap = alert_cfg.get("MIN_RSI_GAP", 3.0)
    volume_confirm_multiplier = alert_cfg.get("VOLUME_CONFIRM_MULTIPLIER", 1.5)

    close = df["Close"].to_numpy()
    volume = df["Volume"].to_numpy()

    candidates = []
    if bearish_enabled:
        c = _detect_and_build(close, volume, rsi_series, vol_sma, False, min_price_change_pct, min_rsi_gap, volume_confirm_multiplier)
        if c:
            candidates.append(c)
    if bullish_enabled:
        c = _detect_and_build(close, volume, rsi_series, vol_sma, True, min_price_change_pct, min_rsi_gap, volume_confirm_multiplier)
        if c:
            candidates.append(c)
    if not candidates:
        return None

    candidates.sort(key=lambda c: (c["phase"] != "CONFIRMED", -(c["pattern_r2"] or 0.0)))
    best = candidates[0]
    is_bottom = best["pattern_type"] == "bullish_divergence"

    idx = df.index
    label1 = "Trough 1" if is_bottom else "Peak 1"
    label_mid = "Peak" if is_bottom else "Trough"
    label2 = "Trough 2 (Lower Low)" if is_bottom else "Peak 2 (Higher High)"
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
