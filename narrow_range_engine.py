from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd

from indicators import compute_true_range

# GUI name: "Pattern Detection" (Narrow Range family — NR4/NR7 Inside Bar Breakout). Canonical
# scheduled-job names live in scheduler_manifest.JOB_GRAPH; the registry contract lives in
# pattern_detection_engine.py.

_MIN_BARS = 60
_BREAKOUT_LOOKAHEAD_DAYS = 5
_VOLUME_CONFIRM_MULTIPLIER = 1.5
_NR_WINDOWS: dict[str, int] = {"nr4": 4, "nr7": 7}

FAMILY = "narrow_range"
PATTERN_TYPES: dict[str, str] = {
    "nr4_bullish": "up", "nr4_bearish": "down",
    "nr7_bullish": "up", "nr7_bearish": "down",
}

_PATTERN_LABELS: dict[str, str] = {
    "nr4": "NR4 Narrow Range",
    "nr7": "NR7 Narrow Range",
    "nr4_bullish": "NR4 Breakout (Bullish)",
    "nr4_bearish": "NR4 Breakout (Bearish)",
    "nr7_bullish": "NR7 Breakout (Bullish)",
    "nr7_bearish": "NR7 Breakout (Bearish)",
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


def _is_narrow_bar(true_range: np.ndarray, high: np.ndarray, low: np.ndarray, i: int, window: int) -> bool:
    """Bar `i` is a narrow-range candidate: its True Range is the smallest of the trailing
    `window` bars (inclusive) AND it's a strict inside bar vs. the prior bar."""
    if i < window - 1 or i < 1:
        return False
    trailing = true_range[i - window + 1:i + 1]
    if np.any(np.isnan(trailing)):
        return False
    if true_range[i] > trailing.min():
        return False
    return bool(high[i] < high[i - 1] and low[i] > low[i - 1])


def _find_latest_candidate(
    true_range: np.ndarray, high: np.ndarray, low: np.ndarray,
    window: int, today_idx: int, breakout_lookahead_days: int,
) -> Optional[int]:
    """Most recent bar within the trailing lookahead window (inclusive of today) that satisfies
    the narrow-range + inside-bar condition, or None."""
    earliest = max(window - 1, today_idx - breakout_lookahead_days)
    for i in range(today_idx, earliest - 1, -1):
        if _is_narrow_bar(true_range, high, low, i, window):
            return i
    return None


def _build_candidate(
    label: str, window: int,
    close: np.ndarray, high: np.ndarray, low: np.ndarray, volume: np.ndarray,
    rsi_series: pd.Series, vol_sma: pd.Series,
    true_range: np.ndarray, today_idx: int, breakout_lookahead_days: int, volume_confirm_multiplier: float,
    bullish_enabled: bool, bearish_enabled: bool,
) -> Optional[dict]:
    bar_idx = _find_latest_candidate(true_range, high, low, window, today_idx, breakout_lookahead_days)
    if bar_idx is None:
        return None

    bar_high = float(high[bar_idx])
    bar_low = float(low[bar_idx])
    breakout_idx: Optional[int] = None
    pattern_type = label

    if bar_idx == today_idx:
        phase = "FORMING"
    else:
        for i in range(bar_idx + 1, today_idx + 1):
            if close[i] > bar_high:
                breakout_idx = i
                pattern_type = f"{label}_bullish"
                break
            if close[i] < bar_low:
                breakout_idx = i
                pattern_type = f"{label}_bearish"
                break
        if breakout_idx is None:
            return None
        if pattern_type.endswith("_bullish") and not bullish_enabled:
            return None
        if pattern_type.endswith("_bearish") and not bearish_enabled:
            return None
        phase = "CONFIRMED"

    bar_range = bar_high - bar_low
    narrow_bar_vol_sma = vol_sma.iloc[bar_idx]
    narrow_bar_quiet = bool(not pd.isna(narrow_bar_vol_sma) and volume[bar_idx] < narrow_bar_vol_sma)
    if phase == "CONFIRMED":
        breakout_vol_sma = vol_sma.iloc[breakout_idx]
        surge = bool(
            not pd.isna(breakout_vol_sma) and breakout_vol_sma > 0
            and volume[breakout_idx] > breakout_vol_sma * volume_confirm_multiplier
        )
        volume_confirms = narrow_bar_quiet and surge
    else:
        volume_confirms = narrow_bar_quiet

    rsi_bar = rsi_series.iloc[bar_idx]
    rsi_ref_idx = breakout_idx if breakout_idx is not None else today_idx
    rsi_ref = rsi_series.iloc[rsi_ref_idx]
    if pd.isna(rsi_bar) or pd.isna(rsi_ref):
        rsi_confirms = False
    elif pattern_type.endswith("_bullish"):
        rsi_confirms = bool(rsi_ref > rsi_bar)
    elif pattern_type.endswith("_bearish"):
        rsi_confirms = bool(rsi_ref < rsi_bar)
    else:
        rsi_confirms = False

    ref_close = float(close[breakout_idx]) if breakout_idx is not None else float(close[today_idx])
    if pattern_type.endswith("_bullish"):
        measured_target = ref_close + bar_range
        key_level = bar_high
    elif pattern_type.endswith("_bearish"):
        measured_target = ref_close - bar_range
        key_level = bar_low
    else:
        measured_target = ref_close
        key_level = bar_high

    prior_ref_idx = max(0, bar_idx - 10)
    prior_close = close[prior_ref_idx]
    prior_trend_pct = (close[bar_idx] - prior_close) / prior_close * 100.0 if prior_close > 0 else 0.0

    return {
        "pattern_type": pattern_type,
        "phase": phase,
        "bar_idx": bar_idx,
        "bar_high": round(bar_high, 4),
        "bar_low": round(bar_low, 4),
        "breakout_idx": breakout_idx,
        "key_level": round(key_level, 4),
        "measured_target": round(float(measured_target), 4),
        "volume_confirms": volume_confirms,
        "rsi_divergence": rsi_confirms,
        "prior_trend_pct": round(float(prior_trend_pct), 2),
        "close_price": round(float(close[today_idx]), 4),
    }


def detect(ticker: str, df: pd.DataFrame, rsi_series: pd.Series, vol_sma: pd.Series, config: dict) -> Optional[dict]:
    """Registry contract entrypoint — see pattern_detection_engine.DETECTORS.

    NR4/NR7 (Narrow Range): a bar whose True Range is the smallest of the trailing 4 (NR4) or 7
    (NR7) bars, and which is also a strict inside bar vs. the prior bar (High < PrevHigh and
    Low > PrevLow) — a micro-contraction signalling indecision that often precedes a sharp
    breakout. Direction is unknown while the bar is fresh (phase FORMING, pattern_type 'nr4'/
    'nr7'); a decisive close outside the narrow bar's own high/low within BREAKOUT_LOOKAHEAD_DAYS
    resolves it to a directional pattern_type (phase CONFIRMED)."""
    if len(df) < _MIN_BARS:
        return None

    sched_cfg = config.get("SCHEDULING", {}).get("PATTERN_DETECTION", {}).get("NARROW_RANGE", {})
    nr4_enabled = sched_cfg.get("NR4_ENABLED", True)
    nr7_enabled = sched_cfg.get("NR7_ENABLED", True)
    bullish_enabled = sched_cfg.get("BULLISH_ENABLED", True)
    bearish_enabled = sched_cfg.get("BEARISH_ENABLED", True)

    alert_cfg = config.get("NOTIFICATIONS", {}).get("PATTERN_DETECTION_ALERTS", {}).get("NARROW_RANGE", {})
    breakout_lookahead_days = int(alert_cfg.get("BREAKOUT_LOOKAHEAD_DAYS", _BREAKOUT_LOOKAHEAD_DAYS))
    volume_confirm_multiplier = alert_cfg.get("VOLUME_CONFIRM_MULTIPLIER", _VOLUME_CONFIRM_MULTIPLIER)

    close = df["Close"].to_numpy()
    high = df["High"].to_numpy()
    low = df["Low"].to_numpy()
    volume = df["Volume"].to_numpy()
    true_range = compute_true_range(df["High"], df["Low"], df["Close"]).to_numpy()
    today_idx = len(df) - 1

    candidates = []
    if nr7_enabled:
        c = _build_candidate("nr7", _NR_WINDOWS["nr7"], close, high, low, volume, rsi_series, vol_sma,
                              true_range, today_idx, breakout_lookahead_days, volume_confirm_multiplier,
                              bullish_enabled, bearish_enabled)
        if c:
            candidates.append(c)
    if nr4_enabled:
        c = _build_candidate("nr4", _NR_WINDOWS["nr4"], close, high, low, volume, rsi_series, vol_sma,
                              true_range, today_idx, breakout_lookahead_days, volume_confirm_multiplier,
                              bullish_enabled, bearish_enabled)
        if c:
            candidates.append(c)
    if not candidates:
        return None

    # NR7 is the stricter/rarer signal — prefer it over a simultaneous NR4 candidate; otherwise
    # prefer CONFIRMED over FORMING, then the most recently-formed bar.
    candidates.sort(key=lambda c: (c["phase"] != "CONFIRMED", not c["pattern_type"].startswith("nr7"), -c["bar_idx"]))
    best = candidates[0]

    idx = df.index
    bar_idx = best["bar_idx"]
    breakout_idx = best["breakout_idx"]
    label_prefix = "NR7" if best["pattern_type"].startswith("nr7") else "NR4"

    points = [
        {"label": f"{label_prefix} Bar High", "date": idx[bar_idx].strftime("%Y-%m-%d"), "price": best["bar_high"]},
        {"label": f"{label_prefix} Bar Low", "date": idx[bar_idx].strftime("%Y-%m-%d"), "price": best["bar_low"]},
    ]
    line_end_idx = breakout_idx if breakout_idx is not None else today_idx
    lines = [
        {
            "label": "Breakout Trigger (High)",
            "date_from": idx[bar_idx].strftime("%Y-%m-%d"), "price_from": best["bar_high"],
            "date_to": idx[line_end_idx].strftime("%Y-%m-%d"), "price_to": best["bar_high"],
            "dash": True,
        },
        {
            "label": "Breakout Trigger (Low)",
            "date_from": idx[bar_idx].strftime("%Y-%m-%d"), "price_from": best["bar_low"],
            "date_to": idx[line_end_idx].strftime("%Y-%m-%d"), "price_to": best["bar_low"],
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
        "breakout_price": round(float(close[breakout_idx]), 4) if breakout_idx is not None else None,
        "measured_target": best["measured_target"],
        "volume_confirms": best["volume_confirms"],
        "rsi_divergence": best["rsi_divergence"],
        "pattern_r2": None,
        "prior_trend_pct": best["prior_trend_pct"],
        "close_price": best["close_price"],
    }
