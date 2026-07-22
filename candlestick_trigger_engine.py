from __future__ import annotations

from typing import Optional

import pandas as pd

from indicators import compute_bollinger_bands
from pattern_geometry_helpers import (
    candle_body_wick_metrics, is_bullish_engulfing, is_bearish_engulfing, is_hammer, is_shooting_star,
)

# GUI name: "Pattern Detection" (Micro-Structure Candlestick Triggers family). Canonical
# scheduled-job names live in scheduler_manifest.JOB_GRAPH; the registry contract lives in
# pattern_detection_engine.py.
#
# Unlike every other family, these patterns are only ever one bar old: they either fired on
# the most recent bar or they didn't, so detect() only ever reports phase="CONFIRMED" (no
# FORMING state) and only ever looks at df's last bar. This makes them self-expiring by
# construction — the day after a trigger, `today_idx` moves on and the same candle no longer
# qualifies as "today's" bar, so detect() correctly returns None and
# pattern_detection_engine._save_results() clears the stale row.

_MIN_BARS = 60
_RSI_OVERSOLD = 30.0
_RSI_OVERBOUGHT = 70.0
_BB_WINDOW_DAYS = 20
_BB_NUM_STD = 2.0
_WICK_MULTIPLIER = 2.0
_OPPOSITE_WICK_MAX_PCT = 0.2
_VOLUME_CONFIRM_MULTIPLIER = 1.5
_PRIOR_TREND_LOOKBACK_DAYS = 10

FAMILY = "candlestick_trigger"
PATTERN_TYPES: dict[str, str] = {
    "bullish_engulfing": "up",
    "bearish_engulfing": "down",
    "hammer": "up",
    "shooting_star": "down",
}

_PATTERN_LABELS: dict[str, str] = {
    "bullish_engulfing": "Bullish Engulfing",
    "bearish_engulfing": "Bearish Engulfing",
    "hammer": "Hammer",
    "shooting_star": "Shooting Star",
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


def _context_gate(
    rsi_today: float, close_today: float, bb_lower: float, bb_upper: float,
    bullish: bool, rsi_oversold: float, rsi_overbought: float,
) -> tuple[bool, bool]:
    """A trigger only qualifies near an extreme — below the lower Bollinger Band or RSI
    oversold for a bullish trigger (mirrored above/overbought for bearish) — otherwise the
    candle shape alone is noise, not a reversal signal. Returns (gate_passed, rsi_confirms):
    rsi_confirms records whether the RSI half specifically held, so a trigger that qualified
    only via the Bollinger Band still surfaces a genuine (not-just-restating-the-gate) signal
    in the result's rsi_divergence field."""
    rsi_ok = False if pd.isna(rsi_today) else bool(rsi_today < rsi_oversold if bullish else rsi_today > rsi_overbought)
    bb_ok = False if (pd.isna(bb_lower) or pd.isna(bb_upper)) else bool(
        close_today < bb_lower if bullish else close_today > bb_upper
    )
    return (rsi_ok or bb_ok), rsi_ok


def _finalize(
    pattern_type: str, points: list[dict], lines: list[dict], key_level: float, measured_target: float,
    rsi_confirms: bool, today_idx: int, idx, close, volume, vol_sma, volume_confirm_multiplier: float,
) -> dict:
    today_date = idx[today_idx].strftime("%Y-%m-%d")
    vol_sma_today = vol_sma.iloc[today_idx]
    volume_confirms = bool(
        not pd.isna(vol_sma_today) and vol_sma_today > 0
        and volume[today_idx] > vol_sma_today * volume_confirm_multiplier
    )

    prior_ref_idx = max(0, today_idx - _PRIOR_TREND_LOOKBACK_DAYS)
    prior_close = close[prior_ref_idx]
    prior_trend_pct = (close[today_idx] - prior_close) / prior_close * 100.0 if prior_close > 0 else 0.0

    return {
        "pattern_type": pattern_type,
        "phase": "CONFIRMED",
        "points": points,
        "lines": lines,
        "key_level": round(float(key_level), 4),
        "breakout_date": today_date,
        "breakout_price": round(float(close[today_idx]), 4),
        "measured_target": round(float(measured_target), 4),
        "volume_confirms": volume_confirms,
        "rsi_divergence": rsi_confirms,
        "pattern_r2": None,
        "prior_trend_pct": round(float(prior_trend_pct), 2),
        "close_price": round(float(close[today_idx]), 4),
    }


def _build_engulfing(
    is_bullish: bool, today_idx: int, idx, open_, high, low, close, volume, vol_sma,
    rsi_confirms: bool, volume_confirm_multiplier: float,
) -> dict:
    prior_idx = today_idx - 1
    prior_date = idx[prior_idx].strftime("%Y-%m-%d")
    today_date = idx[today_idx].strftime("%Y-%m-%d")
    prior_open, prior_close = float(open_[prior_idx]), float(close[prior_idx])
    curr_open, curr_close = float(open_[today_idx]), float(close[today_idx])

    if is_bullish:
        pattern_type = "bullish_engulfing"
        key_level = min(low[prior_idx], low[today_idx])
        points = [
            {"label": "Prior Close", "date": prior_date, "price": round(prior_close, 4)},
            {"label": "Engulfing Open", "date": today_date, "price": round(curr_open, 4)},
            {"label": "Engulfing Close", "date": today_date, "price": round(curr_close, 4)},
            {"label": "Prior Open", "date": prior_date, "price": round(prior_open, 4)},
        ]
    else:
        pattern_type = "bearish_engulfing"
        key_level = max(high[prior_idx], high[today_idx])
        points = [
            {"label": "Prior Open", "date": prior_date, "price": round(prior_open, 4)},
            {"label": "Engulfing Close", "date": today_date, "price": round(curr_close, 4)},
            {"label": "Engulfing Open", "date": today_date, "price": round(curr_open, 4)},
            {"label": "Prior Close", "date": prior_date, "price": round(prior_close, 4)},
        ]

    body_height = abs(curr_close - curr_open)
    measured_target = curr_close + body_height if is_bullish else curr_close - body_height
    lines = [{
        "label": "Support Level" if is_bullish else "Resistance Level",
        "date_from": prior_date, "price_from": round(float(key_level), 4),
        "date_to": today_date, "price_to": round(float(key_level), 4),
        "dash": True,
    }]

    return _finalize(
        pattern_type, points, lines, key_level, measured_target, rsi_confirms,
        today_idx, idx, close, volume, vol_sma, volume_confirm_multiplier,
    )


def _build_pin_bar(
    is_hammer_type: bool, today_idx: int, idx, open_, high, low, close, volume, vol_sma,
    rsi_confirms: bool, volume_confirm_multiplier: float,
) -> dict:
    today_date = idx[today_idx].strftime("%Y-%m-%d")
    body_top = max(open_[today_idx], close[today_idx])
    body_bottom = min(open_[today_idx], close[today_idx])

    if is_hammer_type:
        pattern_type = "hammer"
        key_level = low[today_idx]
        wick_len = body_bottom - low[today_idx]
        measured_target = close[today_idx] + wick_len
        points = [
            {"label": "Rejection Low", "date": today_date, "price": round(float(low[today_idx]), 4)},
            {"label": "Body Bottom", "date": today_date, "price": round(float(body_bottom), 4)},
        ]
        line_label = "Rejection Level"
    else:
        pattern_type = "shooting_star"
        key_level = high[today_idx]
        wick_len = high[today_idx] - body_top
        measured_target = close[today_idx] - wick_len
        points = [
            {"label": "Rejection High", "date": today_date, "price": round(float(high[today_idx]), 4)},
            {"label": "Body Top", "date": today_date, "price": round(float(body_top), 4)},
        ]
        line_label = "Rejection Level"

    lines = [{
        "label": line_label,
        "date_from": today_date, "price_from": round(float(key_level), 4),
        "date_to": today_date, "price_to": round(float(key_level), 4),
        "dash": True,
    }]

    return _finalize(
        pattern_type, points, lines, key_level, measured_target, rsi_confirms,
        today_idx, idx, close, volume, vol_sma, volume_confirm_multiplier,
    )


def detect(ticker: str, df: pd.DataFrame, rsi_series: pd.Series, vol_sma: pd.Series, config: dict) -> Optional[dict]:
    """Registry contract entrypoint — see pattern_detection_engine.DETECTORS.

    Micro-Structure (Candlestick) Triggers: 1-2 bar execution signals used as a strict
    confirmation trigger once a larger setup has been identified, not as structural analysis
    in their own right — so each variant only qualifies when price is already near an extreme
    (below the lower Bollinger Band or RSI oversold for a bullish trigger, mirrored for
    bearish). Bullish/Bearish Engulfing requires the prior candle to be the opposite color and
    the current candle's body to fully contain it; Hammer/Shooting Star requires a rejection
    wick at least 2x the real body with a negligible opposite wick."""
    if len(df) < _MIN_BARS:
        return None

    sched_cfg = config.get("SCHEDULING", {}).get("PATTERN_DETECTION", {}).get("CANDLESTICK_TRIGGER", {})
    alert_cfg = config.get("NOTIFICATIONS", {}).get("PATTERN_DETECTION_ALERTS", {}).get("CANDLESTICK_TRIGGER", {})
    engulfing_enabled = sched_cfg.get("ENGULFING_ENABLED", True)
    pin_bar_enabled = sched_cfg.get("PIN_BAR_ENABLED", True)
    bullish_enabled = sched_cfg.get("BULLISH_ENABLED", True)
    bearish_enabled = sched_cfg.get("BEARISH_ENABLED", True)

    rsi_oversold = alert_cfg.get("RSI_OVERSOLD", _RSI_OVERSOLD)
    rsi_overbought = alert_cfg.get("RSI_OVERBOUGHT", _RSI_OVERBOUGHT)
    bb_window = int(alert_cfg.get("BB_WINDOW_DAYS", _BB_WINDOW_DAYS))
    bb_num_std = alert_cfg.get("BB_NUM_STD", _BB_NUM_STD)
    wick_multiplier = alert_cfg.get("WICK_MULTIPLIER", _WICK_MULTIPLIER)
    opposite_wick_max_pct = alert_cfg.get("OPPOSITE_WICK_MAX_PCT", _OPPOSITE_WICK_MAX_PCT)
    volume_confirm_multiplier = alert_cfg.get("VOLUME_CONFIRM_MULTIPLIER", _VOLUME_CONFIRM_MULTIPLIER)

    open_ = df["Open"].to_numpy()
    high = df["High"].to_numpy()
    low = df["Low"].to_numpy()
    close = df["Close"].to_numpy()
    volume = df["Volume"].to_numpy()
    idx = df.index
    today_idx = len(df) - 1

    bb = compute_bollinger_bands(df["Close"], window=bb_window, num_std=bb_num_std)
    bb_lower_today = bb["lower"].iloc[today_idx]
    bb_upper_today = bb["upper"].iloc[today_idx]
    rsi_today = rsi_series.iloc[today_idx]

    metrics = candle_body_wick_metrics(open_[today_idx], high[today_idx], low[today_idx], close[today_idx])

    if engulfing_enabled and bullish_enabled and is_bullish_engulfing(
        open_[today_idx - 1], close[today_idx - 1], open_[today_idx], close[today_idx]
    ):
        gate_passed, rsi_confirms = _context_gate(
            rsi_today, close[today_idx], bb_lower_today, bb_upper_today, True, rsi_oversold, rsi_overbought,
        )
        if gate_passed:
            return _build_engulfing(
                True, today_idx, idx, open_, high, low, close, volume, vol_sma,
                rsi_confirms, volume_confirm_multiplier,
            )

    if engulfing_enabled and bearish_enabled and is_bearish_engulfing(
        open_[today_idx - 1], close[today_idx - 1], open_[today_idx], close[today_idx]
    ):
        gate_passed, rsi_confirms = _context_gate(
            rsi_today, close[today_idx], bb_lower_today, bb_upper_today, False, rsi_oversold, rsi_overbought,
        )
        if gate_passed:
            return _build_engulfing(
                False, today_idx, idx, open_, high, low, close, volume, vol_sma,
                rsi_confirms, volume_confirm_multiplier,
            )

    if pin_bar_enabled and bullish_enabled and is_hammer(
        metrics["upper_wick"], metrics["lower_wick"], metrics["body_safe"], metrics["range"],
        wick_multiplier, opposite_wick_max_pct,
    ):
        gate_passed, rsi_confirms = _context_gate(
            rsi_today, close[today_idx], bb_lower_today, bb_upper_today, True, rsi_oversold, rsi_overbought,
        )
        if gate_passed:
            return _build_pin_bar(
                True, today_idx, idx, open_, high, low, close, volume, vol_sma,
                rsi_confirms, volume_confirm_multiplier,
            )

    if pin_bar_enabled and bearish_enabled and is_shooting_star(
        metrics["upper_wick"], metrics["lower_wick"], metrics["body_safe"], metrics["range"],
        wick_multiplier, opposite_wick_max_pct,
    ):
        gate_passed, rsi_confirms = _context_gate(
            rsi_today, close[today_idx], bb_lower_today, bb_upper_today, False, rsi_oversold, rsi_overbought,
        )
        if gate_passed:
            return _build_pin_bar(
                False, today_idx, idx, open_, high, low, close, volume, vol_sma,
                rsi_confirms, volume_confirm_multiplier,
            )

    return None
