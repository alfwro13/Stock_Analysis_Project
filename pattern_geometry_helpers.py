from __future__ import annotations

from typing import Optional

import numpy as np
import pandas as pd


def rw_top(data: np.ndarray, i: int, order: int) -> bool:
    if i < order * 2 + 1:
        return False
    k = i - order
    v = data[k]
    for j in range(1, order + 1):
        if data[k + j] > v or data[k - j] > v:
            return False
    return True


def rw_bottom(data: np.ndarray, i: int, order: int) -> bool:
    if i < order * 2 + 1:
        return False
    k = i - order
    v = data[k]
    for j in range(1, order + 1):
        if data[k + j] < v or data[k - j] < v:
            return False
    return True


def find_pivots(closes: np.ndarray, order: int) -> tuple[list[int], list[int]]:
    tops: list[int] = []
    bottoms: list[int] = []
    for i in range(len(closes)):
        if rw_top(closes, i, order):
            tops.append(i - order)
        if rw_bottom(closes, i, order):
            bottoms.append(i - order)
    return tops, bottoms


def merge_adjacent_pivots(raw_events: list[tuple[int, int]], closes: np.ndarray) -> list[tuple[int, int]]:
    """Collapses consecutive same-type pivots (e.g. a double top/bottom, two nearby swing
    highs with no qualifying low between them) into the single most extreme one — otherwise a
    common real shape silently breaks a strict alternation check. See head_shoulders_engine.py's
    original 2026-07-19 SMGB.L case study for the motivating example."""
    merged: list[tuple[int, int]] = []
    for idx, typ in raw_events:
        if merged and merged[-1][1] == typ:
            prev_idx, _ = merged[-1]
            more_extreme = closes[idx] > closes[prev_idx] if typ == 1 else closes[idx] < closes[prev_idx]
            if more_extreme:
                merged[-1] = (idx, typ)
        else:
            merged.append((idx, typ))
    return merged


def latest_alternating_run(closes: np.ndarray, order: int, run_length: int, wanted_first: int) -> Optional[list[int]]:
    """Most recent alternating extrema run of `run_length` points starting on `wanted_first`
    (1 for a top, -1 for a bottom), e.g. [shoulder, armpit, head, armpit] (run_length=4) for
    Head & Shoulders, or [peak, trough, peak] (run_length=3) for a Double Top. Returns None if
    no such run exists yet."""
    tops, bottoms = find_pivots(closes, order)
    raw_events = sorted([(idx, 1) for idx in tops] + [(idx, -1) for idx in bottoms])
    events = merge_adjacent_pivots(raw_events, closes)
    if len(events) < run_length:
        return None
    for end in range(len(events) - 1, run_length - 2, -1):
        window = events[end - run_length + 1:end + 1]
        types = [t for _, t in window]
        if types[0] != wanted_first:
            continue
        if not all(types[k] != types[k + 1] for k in range(run_length - 1)):
            continue
        return [idx for idx, _ in window]
    return None


def piecewise_r2(closes: np.ndarray, pivots: list[int], end_idx: int) -> Optional[float]:
    """R^2 of the actual close path against a piecewise-linear model threaded through `pivots`
    (the pattern's structural points, in chronological order) and the current bar."""
    start = pivots[0]
    if end_idx <= start:
        return None
    xs = np.arange(start, end_idx + 1)
    full_pivots = list(pivots) if pivots[-1] == end_idx else list(pivots) + [end_idx]
    model = np.interp(xs, full_pivots, closes[full_pivots])
    raw = closes[start:end_idx + 1]
    if len(raw) < 2:
        return None
    mean = raw.mean()
    ss_tot = float(np.sum((raw - mean) ** 2))
    if ss_tot == 0:
        return None
    ss_res = float(np.sum((raw - model) ** 2))
    return 1.0 - ss_res / ss_tot


def volume_confirms(
    volume: np.ndarray, vol_sma: pd.Series,
    point_a: int, point_b: int, today_idx: int, confirmed: bool, multiplier: float,
) -> bool:
    """Declining volume from `point_a` to `point_b` (the pattern's two comparable structural
    points — shoulders for Head & Shoulders, twin peaks/troughs for a Double Top/Bottom)
    signals weakening momentum; a confirmed breakout additionally needs a volume surge."""
    declining = bool(volume[point_b] < volume[point_a])
    if not confirmed:
        return declining
    sma_at_breakout = vol_sma.iloc[today_idx]
    if pd.isna(sma_at_breakout) or sma_at_breakout <= 0:
        return declining
    breakout_surge = bool(volume[today_idx] > sma_at_breakout * multiplier)
    return declining and breakout_surge


def rsi_divergence(rsi_series: pd.Series, first_idx: int, second_idx: int, inverted: bool) -> bool:
    rsi_first = rsi_series.iloc[first_idx]
    rsi_second = rsi_series.iloc[second_idx]
    if pd.isna(rsi_first) or pd.isna(rsi_second):
        return False
    return bool(rsi_second > rsi_first) if inverted else bool(rsi_second < rsi_first)


def linreg(x: np.ndarray, y: np.ndarray) -> Optional[tuple[float, float, float]]:
    """OLS slope/intercept/R^2 of y ~ x. None if fewer than 2 points or x has zero range
    (a degenerate fit) — used by Flag's channel lines and Triangle's flat/sloped sides."""
    x = np.asarray(x, dtype=float)
    y = np.asarray(y, dtype=float)
    if len(x) < 2 or np.ptp(x) == 0:
        return None
    slope, intercept = np.polyfit(x, y, 1)
    pred = slope * x + intercept
    ss_tot = float(np.sum((y - y.mean()) ** 2))
    r2 = 1.0 if ss_tot == 0 else 1.0 - float(np.sum((y - pred) ** 2)) / ss_tot
    return float(slope), float(intercept), r2


def slope_pct_per_day(slope: float, reference_price: float) -> float:
    """Normalizes a raw price/day regression slope to %-of-price-per-day so a flatness/steepness
    threshold means the same thing regardless of a ticker's absolute price level."""
    if reference_price <= 0:
        return 0.0
    return slope / reference_price * 100.0


def candle_body_wick_metrics(open_: float, high: float, low: float, close: float) -> dict:
    """Body/wick decomposition for a single OHLC bar — shared by quant_signals.py's
    Quantamental scoring and candlestick_trigger_engine.py's Pattern Detection family so the
    two never drift apart on what counts as a body vs. a wick."""
    body = abs(open_ - close)
    rng = max(high - low, 0.001)
    return {
        "body": body,
        "body_safe": max(body, 0.001),
        "range": rng,
        "upper_wick": high - max(open_, close),
        "lower_wick": min(open_, close) - low,
        "is_bullish": bool(close > open_),
        "is_bearish": bool(close < open_),
    }


def is_bullish_engulfing(prev_open: float, prev_close: float, curr_open: float, curr_close: float) -> bool:
    """Prior candle bearish, current candle bullish, and the current body fully contains the
    prior body — the directional check is required alongside the containment inequalities;
    containment alone (curr_open < prev_close and curr_close > prev_open) also holds for
    candle pairs that aren't a genuine reversal engulfing."""
    return bool(
        prev_close < prev_open and curr_close > curr_open
        and curr_open <= prev_close and curr_close >= prev_open
    )


def is_bearish_engulfing(prev_open: float, prev_close: float, curr_open: float, curr_close: float) -> bool:
    return bool(
        prev_close > prev_open and curr_close < curr_open
        and curr_open >= prev_close and curr_close <= prev_open
    )


def is_hammer(upper_wick: float, lower_wick: float, body_safe: float, rng: float,
              wick_multiplier: float = 2.0, opposite_wick_max_pct: float = 0.2) -> bool:
    """Long lower rejection wick (>= wick_multiplier x body) with a negligible upper wick."""
    return bool(lower_wick >= wick_multiplier * body_safe and upper_wick <= opposite_wick_max_pct * rng)


def is_shooting_star(upper_wick: float, lower_wick: float, body_safe: float, rng: float,
                      wick_multiplier: float = 2.0, opposite_wick_max_pct: float = 0.2) -> bool:
    """Long upper rejection wick (>= wick_multiplier x body) with a negligible lower wick."""
    return bool(upper_wick >= wick_multiplier * body_safe and lower_wick <= opposite_wick_max_pct * rng)
