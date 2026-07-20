from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from indicators import compute_bollinger_bands, compute_keltner_channel_series

# GUI name: "Pattern Detection" (Volatility Squeeze family). Canonical scheduled-job names live
# in scheduler_manifest.JOB_GRAPH; the registry contract lives in pattern_detection_engine.py.

_MIN_BARS = 60
_MAX_SQUEEZE_LOOKBACK_DAYS = 90
_WINDOW_DAYS = 20
_NUM_STD = 2.0
_KC_MULTIPLIER = 1.5
_MIN_SQUEEZE_DAYS = 6
_BREAKOUT_LOOKAHEAD_DAYS = 5
_VOLUME_CONFIRM_MULTIPLIER = 1.5

FAMILY = "volatility_squeeze"
PATTERN_TYPES: dict[str, str] = {
    "volatility_squeeze_bullish": "up",
    "volatility_squeeze_bearish": "down",
}

_PATTERN_LABELS: dict[str, str] = {
    "volatility_squeeze": "Volatility Squeeze",
    "volatility_squeeze_bullish": "Volatility Squeeze (Bullish)",
    "volatility_squeeze_bearish": "Volatility Squeeze (Bearish)",
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


def _find_latest_squeeze_run(squeeze_on: np.ndarray, max_lookback: int) -> Optional[tuple[int, int]]:
    """Most recent contiguous True run in `squeeze_on` within the trailing `max_lookback` bars,
    as (run_start_idx, run_end_idx), or None if no run exists in that window."""
    n = len(squeeze_on)
    start_scan = max(0, n - max_lookback)
    run_end = None
    for i in range(n - 1, start_scan - 1, -1):
        if squeeze_on[i]:
            run_end = i
            break
    if run_end is None:
        return None
    run_start = run_end
    while run_start - 1 >= 0 and squeeze_on[run_start - 1]:
        run_start -= 1
    return run_start, run_end


def detect(ticker: str, df: pd.DataFrame, rsi_series: pd.Series, vol_sma: pd.Series, config: dict) -> Optional[dict]:
    """Registry contract entrypoint — see pattern_detection_engine.DETECTORS.

    A squeeze fires when the Bollinger Bands (window-day SMA +/- num_std) sit fully inside the
    Keltner Channel (window-day EMA +/- multiplier*ATR(window)) for at least MIN_SQUEEZE_DAYS
    consecutive bars — volatility compression that historically precedes an explosive directional
    move. Direction is unknown while the squeeze is still on (phase FORMING, pattern_type
    'volatility_squeeze'); once the squeeze releases, a decisive close outside the Bollinger Band
    within BREAKOUT_LOOKAHEAD_DAYS resolves it to a directional pattern_type (phase CONFIRMED)."""
    if len(df) < _MIN_BARS:
        return None

    sched_cfg = config.get("SCHEDULING", {}).get("PATTERN_DETECTION", {}).get("VOLATILITY_SQUEEZE", {})
    bullish_enabled = sched_cfg.get("BULLISH_ENABLED", True)
    bearish_enabled = sched_cfg.get("BEARISH_ENABLED", True)

    alert_cfg = config.get("NOTIFICATIONS", {}).get("PATTERN_DETECTION_ALERTS", {}).get("VOLATILITY_SQUEEZE", {})
    window_days = int(alert_cfg.get("WINDOW_DAYS", _WINDOW_DAYS))
    num_std = alert_cfg.get("NUM_STD", _NUM_STD)
    kc_multiplier = alert_cfg.get("KC_MULTIPLIER", _KC_MULTIPLIER)
    min_squeeze_days = int(alert_cfg.get("MIN_SQUEEZE_DAYS", _MIN_SQUEEZE_DAYS))
    breakout_lookahead_days = int(alert_cfg.get("BREAKOUT_LOOKAHEAD_DAYS", _BREAKOUT_LOOKAHEAD_DAYS))
    volume_confirm_multiplier = alert_cfg.get("VOLUME_CONFIRM_MULTIPLIER", _VOLUME_CONFIRM_MULTIPLIER)

    close = df["Close"]
    high = df["High"]
    low = df["Low"]
    volume = df["Volume"].to_numpy()

    bb = compute_bollinger_bands(close, window=window_days, num_std=num_std)
    kc = compute_keltner_channel_series(high, low, close, ema_period=window_days, atr_window=window_days, multiplier=kc_multiplier)

    bb_upper = bb["upper"].to_numpy()
    bb_mid = bb["mid"].to_numpy()
    bb_lower = bb["lower"].to_numpy()
    kc_upper = kc["upper"].to_numpy()
    kc_lower = kc["lower"].to_numpy()
    close_arr = close.to_numpy()

    valid = ~(np.isnan(bb_upper) | np.isnan(bb_lower) | np.isnan(kc_upper) | np.isnan(kc_lower))
    squeeze_on = valid & (bb_upper < kc_upper) & (bb_lower > kc_lower)

    today_idx = len(df) - 1
    run = _find_latest_squeeze_run(squeeze_on, _MAX_SQUEEZE_LOOKBACK_DAYS)
    if run is None:
        return None
    run_start, run_end = run
    if run_end - run_start + 1 < min_squeeze_days:
        return None

    bars_since_end = today_idx - run_end
    breakout_idx: Optional[int] = None
    if bars_since_end == 0:
        phase = "FORMING"
        pattern_type = "volatility_squeeze"
    elif bars_since_end <= breakout_lookahead_days:
        if close_arr[today_idx] > bb_upper[today_idx]:
            pattern_type = "volatility_squeeze_bullish"
        elif close_arr[today_idx] < bb_lower[today_idx]:
            pattern_type = "volatility_squeeze_bearish"
        else:
            return None
        if pattern_type == "volatility_squeeze_bullish" and not bullish_enabled:
            return None
        if pattern_type == "volatility_squeeze_bearish" and not bearish_enabled:
            return None
        phase = "CONFIRMED"
        breakout_idx = today_idx
    else:
        return None

    idx = df.index

    vol_start = volume[run_start]
    vol_end = volume[run_end]
    declining = bool(vol_end < vol_start)
    if phase == "CONFIRMED":
        sma_at_breakout = vol_sma.iloc[breakout_idx]
        surge = bool(
            not pd.isna(sma_at_breakout) and sma_at_breakout > 0
            and volume[breakout_idx] > sma_at_breakout * volume_confirm_multiplier
        )
        volume_confirms = declining and surge
    else:
        volume_confirms = declining

    rsi_start = rsi_series.iloc[run_start]
    rsi_ref_idx = breakout_idx if breakout_idx is not None else today_idx
    rsi_ref = rsi_series.iloc[rsi_ref_idx]
    if pd.isna(rsi_start) or pd.isna(rsi_ref):
        rsi_confirms = False
    elif pattern_type == "volatility_squeeze_bullish":
        rsi_confirms = bool(rsi_ref > rsi_start)
    elif pattern_type == "volatility_squeeze_bearish":
        rsi_confirms = bool(rsi_ref < rsi_start)
    else:
        rsi_confirms = False

    band_width_start = float(bb_upper[run_start] - bb_lower[run_start])
    ref_close = close_arr[breakout_idx] if breakout_idx is not None else close_arr[today_idx]
    if pattern_type == "volatility_squeeze_bullish":
        measured_target = ref_close + band_width_start
        key_level = float(bb_upper[today_idx])
    elif pattern_type == "volatility_squeeze_bearish":
        measured_target = ref_close - band_width_start
        key_level = float(bb_lower[today_idx])
    else:
        measured_target = ref_close
        key_level = float(bb_mid[today_idx])

    points = [
        {"label": "Squeeze Start (Upper)", "date": idx[run_start].strftime("%Y-%m-%d"), "price": round(float(bb_upper[run_start]), 4)},
        {"label": "Squeeze End (Upper)", "date": idx[run_end].strftime("%Y-%m-%d"), "price": round(float(bb_upper[run_end]), 4)},
        {"label": "Squeeze End (Lower)", "date": idx[run_end].strftime("%Y-%m-%d"), "price": round(float(bb_lower[run_end]), 4)},
        {"label": "Squeeze Start (Lower)", "date": idx[run_start].strftime("%Y-%m-%d"), "price": round(float(bb_lower[run_start]), 4)},
    ]
    upper_path = [
        {"date": idx[i].strftime("%Y-%m-%d"), "price": round(float(bb_upper[i]), 4)}
        for i in range(run_start, run_end + 1)
    ]
    lower_path = [
        {"date": idx[i].strftime("%Y-%m-%d"), "price": round(float(bb_lower[i]), 4)}
        for i in range(run_start, run_end + 1)
    ]
    lines = [
        {"label": "Upper Band (Squeeze)", "path": upper_path, "dash": False},
        {"label": "Lower Band (Squeeze)", "path": lower_path, "dash": False},
    ]

    prior_ref_idx = max(0, run_start - 10)
    prior_close = close_arr[prior_ref_idx]
    prior_trend_pct = (close_arr[run_start] - prior_close) / prior_close * 100.0 if prior_close > 0 else 0.0

    return {
        "pattern_type": pattern_type,
        "phase": phase,
        "points": points,
        "lines": lines,
        "key_level": round(key_level, 4),
        "breakout_date": idx[breakout_idx].strftime("%Y-%m-%d") if breakout_idx is not None else None,
        "breakout_price": round(float(close_arr[breakout_idx]), 4) if breakout_idx is not None else None,
        "measured_target": round(float(measured_target), 4),
        "volume_confirms": volume_confirms,
        "rsi_divergence": rsi_confirms,
        "pattern_r2": None,
        "prior_trend_pct": round(float(prior_trend_pct), 2),
        "close_price": round(float(close_arr[today_idx]), 4),
    }
