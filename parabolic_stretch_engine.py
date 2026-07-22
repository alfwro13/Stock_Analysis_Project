from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from indicators import compute_smas

# GUI name: "Pattern Detection" (Parabolic Stretch / Rubber Band family). Canonical scheduled-job
# names live in scheduler_manifest.JOB_GRAPH; the registry contract lives in pattern_detection_engine.py.

_SMA_WINDOW = 200
_Z_WINDOW_DAYS = 252
_Z_THRESHOLD = 3.0
_CONFIRM_Z_THRESHOLD = 2.0
_MAX_STRETCH_LOOKBACK_DAYS = 30
_BREAKOUT_LOOKAHEAD_DAYS = 10
_VOLUME_CONFIRM_MULTIPLIER = 1.5
_PRIOR_TREND_LOOKBACK = 20

FAMILY = "parabolic_stretch"
PATTERN_TYPES: dict[str, str] = {
    "parabolic_stretch_overbought": "down",
    "parabolic_stretch_oversold": "up",
}

_PATTERN_LABELS: dict[str, str] = {
    "parabolic_stretch_overbought": "Parabolic Stretch (Overbought)",
    "parabolic_stretch_oversold": "Parabolic Stretch (Oversold)",
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


def _find_latest_stretch(z: np.ndarray, valid: np.ndarray, threshold: float, max_lookback: int) -> Optional[tuple[int, str]]:
    """Most recent bar within `max_lookback` whose Z-score breaches +/-threshold, as
    (index, "overbought"|"oversold"), or None if no breach exists in that window."""
    n = len(z)
    start_scan = max(0, n - 1 - max_lookback)
    for i in range(n - 1, start_scan - 1, -1):
        if not valid[i]:
            continue
        if z[i] >= threshold:
            return i, "overbought"
        if z[i] <= -threshold:
            return i, "oversold"
    return None


def detect(ticker: str, df: pd.DataFrame, rsi_series: pd.Series, vol_sma: pd.Series, config: dict) -> Optional[dict]:
    """Registry contract entrypoint — see pattern_detection_engine.DETECTORS.

    Distance = Close - SMA(sma_window). A stock is "stretched" when the Z-score of that
    distance series against its own trailing z_window_days mean/std exceeds +/-z_threshold
    (a Bollinger-Band-style test applied to the distance-from-mean series rather than raw
    price). Direction is known immediately from the sign of Z (stretched above the mean implies
    an overbought reversion-down setup, stretched below implies oversold reversion-up) unlike
    Volatility Squeeze/Narrow Range's direction-unknown-until-breakout model — phase FORMING
    while still stretched, CONFIRMED once Z has retraced back under confirm_z_threshold and
    price has genuinely moved back toward the mean, within breakout_lookahead_days."""
    sched_cfg = config.get("SCHEDULING", {}).get("PATTERN_DETECTION", {}).get("PARABOLIC_STRETCH", {})
    overbought_enabled = sched_cfg.get("OVERBOUGHT_ENABLED", True)
    oversold_enabled = sched_cfg.get("OVERSOLD_ENABLED", True)

    alert_cfg = config.get("NOTIFICATIONS", {}).get("PATTERN_DETECTION_ALERTS", {}).get("PARABOLIC_STRETCH", {})
    sma_window = int(alert_cfg.get("SMA_WINDOW", _SMA_WINDOW))
    z_window_days = int(alert_cfg.get("Z_WINDOW_DAYS", _Z_WINDOW_DAYS))
    z_threshold = alert_cfg.get("Z_THRESHOLD", _Z_THRESHOLD)
    confirm_z_threshold = alert_cfg.get("CONFIRM_Z_THRESHOLD", _CONFIRM_Z_THRESHOLD)
    breakout_lookahead_days = int(alert_cfg.get("BREAKOUT_LOOKAHEAD_DAYS", _BREAKOUT_LOOKAHEAD_DAYS))
    volume_confirm_multiplier = alert_cfg.get("VOLUME_CONFIRM_MULTIPLIER", _VOLUME_CONFIRM_MULTIPLIER)

    min_bars_needed = sma_window + z_window_days
    if len(df) < min_bars_needed:
        return None

    close = df["Close"]
    close_arr = close.to_numpy()
    volume = df["Volume"].to_numpy()

    sma = compute_smas(close, [sma_window])[sma_window]
    distance = close - sma
    roll_mean = distance.rolling(z_window_days, min_periods=z_window_days).mean()
    roll_std = distance.rolling(z_window_days, min_periods=z_window_days).std()
    roll_std = roll_std.where(roll_std > 0)
    z = ((distance - roll_mean) / roll_std).to_numpy()
    valid = ~np.isnan(z)

    today_idx = len(df) - 1
    found = _find_latest_stretch(z, valid, z_threshold, _MAX_STRETCH_LOOKBACK_DAYS)
    if found is None:
        return None
    stretch_idx, direction = found

    if direction == "overbought" and not overbought_enabled:
        return None
    if direction == "oversold" and not oversold_enabled:
        return None

    bars_since = today_idx - stretch_idx
    breakout_idx: Optional[int] = None
    if bars_since == 0:
        phase = "FORMING"
    elif bars_since <= breakout_lookahead_days:
        if not valid[today_idx] or abs(z[today_idx]) > confirm_z_threshold:
            return None
        reverted_price = (
            close_arr[today_idx] < close_arr[stretch_idx] if direction == "overbought"
            else close_arr[today_idx] > close_arr[stretch_idx]
        )
        if not reverted_price:
            return None
        phase = "CONFIRMED"
        breakout_idx = today_idx
    else:
        return None

    pattern_type = "parabolic_stretch_overbought" if direction == "overbought" else "parabolic_stretch_oversold"

    idx = df.index
    sma_arr = sma.to_numpy()

    point_label = "Stretch Peak" if direction == "overbought" else "Stretch Trough"
    points = [
        {"label": point_label, "date": idx[stretch_idx].strftime("%Y-%m-%d"), "price": round(float(close_arr[stretch_idx]), 4)},
        {"label": "Current", "date": idx[today_idx].strftime("%Y-%m-%d"), "price": round(float(close_arr[today_idx]), 4)},
    ]
    sma_path = [
        {"date": idx[i].strftime("%Y-%m-%d"), "price": round(float(sma_arr[i]), 4)}
        for i in range(stretch_idx, today_idx + 1)
    ]
    lines = [{"label": f"{sma_window}-Day SMA (Mean)", "path": sma_path, "dash": False}]

    key_level = float(sma_arr[today_idx])
    measured_target = float(sma_arr[breakout_idx]) if breakout_idx is not None else float(sma_arr[today_idx])

    vol_sma_at_stretch = vol_sma.iloc[stretch_idx]
    volume_confirms = bool(
        not pd.isna(vol_sma_at_stretch) and vol_sma_at_stretch > 0
        and volume[stretch_idx] > vol_sma_at_stretch * volume_confirm_multiplier
    )

    rsi_at_stretch = rsi_series.iloc[stretch_idx]
    if pd.isna(rsi_at_stretch):
        rsi_confirms = False
    elif direction == "overbought":
        rsi_confirms = bool(rsi_at_stretch >= 70)
    else:
        rsi_confirms = bool(rsi_at_stretch <= 30)

    prior_ref_idx = max(0, stretch_idx - _PRIOR_TREND_LOOKBACK)
    prior_ref = close_arr[prior_ref_idx]
    prior_trend_pct = (close_arr[stretch_idx] - prior_ref) / prior_ref * 100.0 if prior_ref > 0 else 0.0

    return {
        "pattern_type": pattern_type,
        "phase": phase,
        "points": points,
        "lines": lines,
        "key_level": round(key_level, 4),
        "breakout_date": idx[breakout_idx].strftime("%Y-%m-%d") if breakout_idx is not None else None,
        "breakout_price": round(float(close_arr[breakout_idx]), 4) if breakout_idx is not None else None,
        "measured_target": round(measured_target, 4),
        "volume_confirms": volume_confirms,
        "rsi_divergence": rsi_confirms,
        "pattern_r2": None,
        "prior_trend_pct": round(float(prior_trend_pct), 2),
        "close_price": round(float(close_arr[today_idx]), 4),
    }
