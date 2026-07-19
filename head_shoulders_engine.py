from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd

from config import HISTORICAL_DIR, load_config
from database import (
    get_connection,
    log_head_shoulders_pattern,
    get_unresolved_head_shoulders_patterns,
    batch_update_head_shoulders_actuals,
)
from indicators import compute_rsi, compute_volume_sma
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

# GUI name: "Head & Shoulders Pattern Detector". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.

_ORDER = 5
_MIN_BARS = 60
_LOOKBACK_BARS = 180
_PRIOR_TREND_LOOKBACK = 20
_TIME_SYMMETRY_MAX_RATIO = 2.75
_BALANCE_TOLERANCE = 0.90
_BACKFILL_STEP_DAYS = 5

_PATTERN_EXPECTED_DIRECTION: dict[str, str] = {
    "regular": "down",
    "inverse": "up",
}
_RESOLUTION_HORIZONS: tuple[int, ...] = (14, 30)

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


def _rw_top(data: np.ndarray, i: int, order: int) -> bool:
    if i < order * 2 + 1:
        return False
    k = i - order
    v = data[k]
    for j in range(1, order + 1):
        if data[k + j] > v or data[k - j] > v:
            return False
    return True


def _rw_bottom(data: np.ndarray, i: int, order: int) -> bool:
    if i < order * 2 + 1:
        return False
    k = i - order
    v = data[k]
    for j in range(1, order + 1):
        if data[k + j] < v or data[k - j] < v:
            return False
    return True


def _find_pivots(closes: np.ndarray, order: int) -> tuple[list[int], list[int]]:
    tops: list[int] = []
    bottoms: list[int] = []
    for i in range(len(closes)):
        if _rw_top(closes, i, order):
            tops.append(i - order)
        if _rw_bottom(closes, i, order):
            bottoms.append(i - order)
    return tops, bottoms


def _merge_adjacent_pivots(raw_events: list[tuple[int, int]], closes: np.ndarray) -> list[tuple[int, int]]:
    """Collapses consecutive same-type pivots (e.g. a double top/bottom, two nearby swing
    highs with no qualifying low between them) into the single most extreme one. Without this,
    a double top/bottom — a common real shape, not an edge case — breaks the strict alternation
    check below and silently discards the whole candidate, even though a human reading the same
    chart sees one broad head or trough. Found 2026-07-19: SMGB.L formed a textbook Head &
    Shoulders (perfectly symmetric timing, balanced shoulders, already-confirmed breakout) that
    went undetected purely because its head was a double top four bars apart."""
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


def _latest_candidate_extrema(closes: np.ndarray, order: int, inverted: bool) -> Optional[list[int]]:
    """Most recent alternating 4-point extrema run [shoulder, armpit, head, armpit] for the
    requested pattern direction — a regular (topping) candidate starts on a top, an inverse
    (bottoming) candidate starts on a bottom. Returns None if no such run exists yet."""
    tops, bottoms = _find_pivots(closes, order)
    raw_events = sorted([(idx, 1) for idx in tops] + [(idx, -1) for idx in bottoms])
    events = _merge_adjacent_pivots(raw_events, closes)
    if len(events) < 4:
        return None
    wanted_first = -1 if inverted else 1
    for end in range(len(events) - 1, 2, -1):
        window = events[end - 3:end + 1]
        types = [t for _, t in window]
        if types[0] != wanted_first:
            continue
        if not all(types[k] != types[k + 1] for k in range(3)):
            continue
        return [idx for idx, _ in window]
    return None


def _pattern_r2(
    closes: np.ndarray,
    l_shoulder: int, l_armpit: int, head: int, r_armpit: int, r_shoulder: int, end_idx: int,
) -> Optional[float]:
    if end_idx <= l_shoulder:
        return None
    xs = np.arange(l_shoulder, end_idx + 1)
    pivots = [l_shoulder, l_armpit, head, r_armpit, r_shoulder, end_idx]
    model = np.interp(xs, pivots, closes[pivots])
    raw = closes[l_shoulder:end_idx + 1]
    if len(raw) < 2:
        return None
    mean = raw.mean()
    ss_tot = float(np.sum((raw - mean) ** 2))
    if ss_tot == 0:
        return None
    ss_res = float(np.sum((raw - model) ** 2))
    return 1.0 - ss_res / ss_tot


def _volume_confirms(
    volume: np.ndarray, vol_sma: pd.Series,
    l_shoulder: int, r_shoulder: int, today_idx: int, confirmed: bool, multiplier: float,
) -> bool:
    declining = bool(volume[r_shoulder] < volume[l_shoulder])
    if not confirmed:
        return declining
    sma_at_breakout = vol_sma.iloc[today_idx]
    if pd.isna(sma_at_breakout) or sma_at_breakout <= 0:
        return declining
    breakout_surge = bool(volume[today_idx] > sma_at_breakout * multiplier)
    return declining and breakout_surge


def _rsi_divergence(rsi_series: pd.Series, l_shoulder: int, head: int, inverted: bool) -> bool:
    rsi_l = rsi_series.iloc[l_shoulder]
    rsi_h = rsi_series.iloc[head]
    if pd.isna(rsi_l) or pd.isna(rsi_h):
        return False
    return bool(rsi_h > rsi_l) if inverted else bool(rsi_h < rsi_l)


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
    extrema = _latest_candidate_extrema(close, _ORDER, inverted)
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

    r2 = _pattern_r2(close, l_shoulder, l_armpit, head, r_armpit, r_shoulder, today_idx)
    volume_confirms = _volume_confirms(volume, vol_sma, l_shoulder, r_shoulder, today_idx, confirmed, volume_confirm_multiplier)
    rsi_divergence = _rsi_divergence(rsi_series, l_shoulder, head, inverted)

    return {
        "pattern_type": "inverse" if inverted else "regular",
        "phase": phase,
        "l_shoulder_idx": l_shoulder, "l_shoulder_price": round(float(l_price), 4),
        "l_armpit_idx": l_armpit, "l_armpit_price": round(float(l_armpit_price), 4),
        "head_idx": head, "head_price": round(float(head_price), 4),
        "r_armpit_idx": r_armpit, "r_armpit_price": round(float(r_armpit_price), 4),
        "r_shoulder_idx": r_shoulder, "r_shoulder_price": round(float(r_price), 4),
        "neck_slope": round(float(neck_slope), 6),
        "neck_value": round(float(neck_val_today), 4),
        "breakout_idx": today_idx if confirmed else None,
        "breakout_price": round(float(last_close), 4) if confirmed else None,
        "measured_target": round(float(measured_target), 4),
        "volume_confirms": volume_confirms,
        "rsi_divergence": rsi_divergence,
        "pattern_r2": round(float(r2), 4) if r2 is not None else None,
        "prior_trend_pct": round(float(prior_change_pct), 2),
        "close_price": round(float(last_close), 4),
    }


class HeadShouldersEngine:
    # Detects forming/confirmed Head & Shoulders and Inverse Head & Shoulders patterns from daily Parquet data.

    def __init__(self, config: dict) -> None:
        sched_cfg = config.get("SCHEDULING", {}).get("HEAD_SHOULDERS", {})
        self.regular_enabled: bool = sched_cfg.get("REGULAR_ENABLED", True)
        self.inverse_enabled: bool = sched_cfg.get("INVERSE_ENABLED", True)
        self.monitor_portfolio: bool = sched_cfg.get("MONITOR_PORTFOLIO", True)
        self.monitor_watchlist: bool = sched_cfg.get("MONITOR_WATCHLIST", False)
        alert_cfg = config.get("NOTIFICATIONS", {}).get("HEAD_SHOULDERS_ALERTS", {})
        self.prior_trend_min_pct: float = alert_cfg.get("PRIOR_TREND_MIN_PCT", 8.0)
        self.volume_confirm_multiplier: float = alert_cfg.get("VOLUME_CONFIRM_MULTIPLIER", 1.5)
        self.ignored_tickers: set = {str(t).strip().upper() for t in config.get("IGNORED_TICKERS", [])}

    def run_scan(self) -> list[dict]:
        tickers = self._get_ticker_list()
        results = []
        for ticker in tickers:
            df = self._load_history(ticker)
            if df is None:
                continue
            row = self._analyse_ticker(ticker, df)
            if row:
                results.append(row)

        if results:
            self._save_results(results)
        return results

    def _analyse_ticker(self, ticker: str, df: pd.DataFrame) -> Optional[dict]:
        try:
            if len(df) < _MIN_BARS:
                return None

            close = df["Close"].to_numpy()
            volume = df["Volume"].to_numpy()
            rsi_series = compute_rsi(df["Close"])
            vol_sma = compute_volume_sma(df["Volume"])

            candidates = []
            if self.regular_enabled:
                c = _detect_and_build(close, volume, rsi_series, vol_sma, False, self.prior_trend_min_pct, self.volume_confirm_multiplier)
                if c:
                    candidates.append(c)
            if self.inverse_enabled:
                c = _detect_and_build(close, volume, rsi_series, vol_sma, True, self.prior_trend_min_pct, self.volume_confirm_multiplier)
                if c:
                    candidates.append(c)

            if not candidates:
                return None

            candidates.sort(key=lambda c: (c["phase"] != "CONFIRMED", -(c["pattern_r2"] or 0.0)))
            best = candidates[0]

            idx = df.index
            for key in ("l_shoulder", "l_armpit", "head", "r_armpit", "r_shoulder"):
                i = best.pop(f"{key}_idx")
                best[f"{key}_date"] = idx[i].strftime("%Y-%m-%d")
            breakout_idx = best.pop("breakout_idx")
            best["breakout_date"] = idx[breakout_idx].strftime("%Y-%m-%d") if breakout_idx is not None else None

            best["ticker"] = ticker
            best["scan_ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            return best
        except Exception as e:
            logger.error("HeadShouldersEngine: analysis failed for %s: %s", ticker, e)
            return None

    def _get_ticker_list(self) -> list[str]:
        tickers: set[str] = set()
        if self.monitor_portfolio:
            try:
                from accounts_engine import get_combined_holdings
                from utils import is_excluded_from_yahoo_fetch
                for t in get_combined_holdings().keys():
                    if not is_excluded_from_yahoo_fetch(t, self.ignored_tickers):
                        tickers.add(t.upper())
            except Exception as e:
                logger.warning("HeadShouldersEngine: could not load portfolio tickers: %s", e)
        if self.monitor_watchlist:
            try:
                from database import get_watchlist_tickers
                from utils import is_excluded_from_yahoo_fetch
                for t in get_watchlist_tickers():
                    if not is_excluded_from_yahoo_fetch(t, self.ignored_tickers):
                        tickers.add(t.upper())
            except Exception as e:
                logger.warning("HeadShouldersEngine: could not load watchlist tickers: %s", e)
        tickers -= self.ignored_tickers
        return sorted(tickers)

    def _load_history(self, ticker: str) -> Optional[pd.DataFrame]:
        path = HISTORICAL_DIR / f"{ticker}.parquet"
        if not path.exists():
            logger.info("HeadShouldersEngine: no parquet for %s — fetching 2-year history.", ticker)
            try:
                data = yahoo_engine.get_price_history([ticker], period="2y", interval="1d")
                df_fetched = data.get(ticker)
                if df_fetched is None or df_fetched.empty:
                    logger.warning("HeadShouldersEngine: no price data returned for %s — skipping.", ticker)
                    return None
                if df_fetched.index.tz is not None:
                    df_fetched.index = df_fetched.index.tz_convert(None)
                HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
                df_fetched.to_parquet(path, engine="pyarrow")
                logger.info("HeadShouldersEngine: fetched and saved history for %s (%d rows).", ticker, len(df_fetched))
            except Exception as e:
                logger.warning("HeadShouldersEngine: failed to fetch history for %s: %s", ticker, e)
                return None
        try:
            df = pd.read_parquet(path, columns=["Open", "High", "Low", "Close", "Volume"])
            df = df.dropna(subset=["Close", "Volume"])
            df = df[df["Volume"] > 0]
            return df.tail(_LOOKBACK_BARS)
        except Exception as e:
            logger.warning("HeadShouldersEngine: failed to load %s: %s", ticker, e)
            return None

    def _save_results(self, results: list[dict]) -> None:
        conn = None
        previous_by_ticker: dict = {}
        try:
            conn = get_connection()
            cursor = conn.cursor()
            tickers = [row["ticker"] for row in results]
            placeholders = ",".join("?" * len(tickers))
            cursor.execute(
                f"SELECT ticker, pattern_type, phase, l_shoulder_date, head_date, r_armpit_date "
                f"FROM head_shoulders_results WHERE ticker IN ({placeholders})",
                tickers,
            )
            previous_by_ticker = {r["ticker"]: dict(r) for r in cursor.fetchall()}

            for row in results:
                cursor.execute(
                    """
                    INSERT INTO head_shoulders_results
                        (ticker, pattern_type, phase,
                         l_shoulder_date, l_shoulder_price, l_armpit_date, l_armpit_price,
                         head_date, head_price, r_armpit_date, r_armpit_price,
                         r_shoulder_date, r_shoulder_price, neck_slope,
                         breakout_date, breakout_price, measured_target,
                         volume_confirms, rsi_divergence, pattern_r2, prior_trend_pct, scan_ts)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(ticker) DO UPDATE SET
                        pattern_type=excluded.pattern_type,
                        phase=excluded.phase,
                        l_shoulder_date=excluded.l_shoulder_date, l_shoulder_price=excluded.l_shoulder_price,
                        l_armpit_date=excluded.l_armpit_date, l_armpit_price=excluded.l_armpit_price,
                        head_date=excluded.head_date, head_price=excluded.head_price,
                        r_armpit_date=excluded.r_armpit_date, r_armpit_price=excluded.r_armpit_price,
                        r_shoulder_date=excluded.r_shoulder_date, r_shoulder_price=excluded.r_shoulder_price,
                        neck_slope=excluded.neck_slope,
                        breakout_date=excluded.breakout_date, breakout_price=excluded.breakout_price,
                        measured_target=excluded.measured_target,
                        volume_confirms=excluded.volume_confirms, rsi_divergence=excluded.rsi_divergence,
                        pattern_r2=excluded.pattern_r2, prior_trend_pct=excluded.prior_trend_pct,
                        scan_ts=excluded.scan_ts
                    """,
                    (
                        row["ticker"], row["pattern_type"], row["phase"],
                        row["l_shoulder_date"], row["l_shoulder_price"], row["l_armpit_date"], row["l_armpit_price"],
                        row["head_date"], row["head_price"], row["r_armpit_date"], row["r_armpit_price"],
                        row["r_shoulder_date"], row["r_shoulder_price"], row["neck_slope"],
                        row["breakout_date"], row["breakout_price"], row["measured_target"],
                        int(row["volume_confirms"]), int(row["rsi_divergence"]), row["pattern_r2"],
                        row["prior_trend_pct"], row["scan_ts"],
                    ),
                )
            conn.commit()
        except Exception as e:
            logger.error("HeadShouldersEngine: failed to save results: %s", e)
        finally:
            if conn:
                conn.close()

        scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for row in results:
            prev = previous_by_ticker.get(row["ticker"])
            unchanged = (
                prev is not None
                and prev["pattern_type"] == row["pattern_type"]
                and prev["phase"] == row["phase"]
                and prev["l_shoulder_date"] == row["l_shoulder_date"]
                and prev["head_date"] == row["head_date"]
                and prev["r_armpit_date"] == row["r_armpit_date"]
            )
            if unchanged:
                # Same pattern instance, same phase as yesterday's scan — skip logging a
                # duplicate history row. A genuinely new instance (different shoulder/head/
                # armpit dates) or a phase transition (FORMING -> CONFIRMED) still logs.
                continue
            log_head_shoulders_pattern(
                row["ticker"], row["pattern_type"], row["phase"], scan_date, row["close_price"], row["scan_ts"],
                measured_target=row.get("measured_target"),
                volume_confirms=row.get("volume_confirms"),
                rsi_divergence=row.get("rsi_divergence"),
                pattern_r2=row.get("pattern_r2"),
                prior_trend_pct=row.get("prior_trend_pct"),
            )


def fill_pattern_outcomes() -> int:
    today = datetime.now(timezone.utc).date()
    cutoff_14d = (today - timedelta(days=14)).strftime("%Y-%m-%d")
    cutoff_30d = (today - timedelta(days=30)).strftime("%Y-%m-%d")

    pending = get_unresolved_head_shoulders_patterns(cutoff_14d, cutoff_30d)
    if not pending:
        return 0

    by_ticker: dict[str, list] = {}
    for row in pending:
        by_ticker.setdefault(row["ticker"], []).append(row)

    batch: list[tuple[int, int, float, str, int]] = []
    for ticker, rows in by_ticker.items():
        path = HISTORICAL_DIR / f"{ticker}.parquet"
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path)
        except Exception as e:
            logger.error("fill_pattern_outcomes: failed to load %s: %s", ticker, e)
            continue
        if df.empty or "Close" not in df.columns:
            continue

        date_strs = pd.to_datetime(df.index).normalize().strftime("%Y-%m-%d").tolist()
        close_vals = df["Close"].tolist()
        date_close = list(zip(date_strs, close_vals))

        for row in rows:
            expected = _PATTERN_EXPECTED_DIRECTION.get(row["pattern_type"])
            if expected is None:
                continue
            ref_price = row.get("close_price")
            if not ref_price or ref_price <= 0:
                continue

            for horizon in _RESOLUTION_HORIZONS:
                col = f"direction_correct_{horizon}d"
                if row.get(col) is not None:
                    continue
                cutoff = (today - timedelta(days=horizon)).strftime("%Y-%m-%d")
                if row["scan_date"] > cutoff:
                    continue

                target = (
                    datetime.strptime(row["scan_date"], "%Y-%m-%d") + timedelta(days=horizon)
                ).strftime("%Y-%m-%d")

                future = [(d, c) for d, c in date_close if d >= target]
                if not future:
                    continue

                actual_date, actual_price = future[0]
                actual_price = round(float(actual_price), 4)
                direction_correct = (
                    1 if (expected == "up" and actual_price > ref_price) or
                         (expected == "down" and actual_price < ref_price)
                    else 0
                )
                batch.append((row["id"], horizon, actual_price, actual_date, direction_correct))

    batch_update_head_shoulders_actuals(batch)
    return len(batch)


def backfill_historical_patterns(tickers: Optional[list[str]] = None) -> int:
    """One-time (operator-triggered) historical backtest: walks each ticker's full parquet
    history at ~weekly intervals, detects confirmed patterns as of each historical date, and
    immediately resolves 14d/30d outcomes from the same parquet's later rows — giving a
    populated accuracy panel before any live alerting is relied upon."""
    engine = HeadShouldersEngine(load_config())
    if tickers is None:
        tickers = engine._get_ticker_list()

    directions = []
    if engine.regular_enabled:
        directions.append(False)
    if engine.inverse_enabled:
        directions.append(True)

    total_logged = 0
    for ticker in tickers:
        path = HISTORICAL_DIR / f"{ticker}.parquet"
        if not path.exists():
            continue
        try:
            df = pd.read_parquet(path, columns=["Open", "High", "Low", "Close", "Volume"])
            df = df.dropna(subset=["Close", "Volume"])
            df = df[df["Volume"] > 0]
        except Exception as e:
            logger.warning("HeadShouldersEngine backfill: failed to load %s: %s", ticker, e)
            continue
        if len(df) < _MIN_BARS + _BACKFILL_STEP_DAYS:
            continue

        close_full = df["Close"].to_numpy()
        volume_full = df["Volume"].to_numpy()
        rsi_full = compute_rsi(df["Close"])
        vol_sma_full = compute_volume_sma(df["Volume"])
        idx = df.index

        for inverted in directions:
            last_geometry = None
            for cutoff in range(_MIN_BARS, len(df), _BACKFILL_STEP_DAYS):
                window_close = close_full[:cutoff + 1]
                window_volume = volume_full[:cutoff + 1]
                window_rsi = rsi_full.iloc[:cutoff + 1]
                window_vol_sma = vol_sma_full.iloc[:cutoff + 1]

                result = _detect_and_build(
                    window_close, window_volume, window_rsi, window_vol_sma,
                    inverted, engine.prior_trend_min_pct, engine.volume_confirm_multiplier,
                )
                if not result or result["phase"] != "CONFIRMED":
                    continue

                geometry = (result["l_shoulder_idx"], result["l_armpit_idx"], result["head_idx"], result["r_armpit_idx"])
                if geometry == last_geometry:
                    # Same pattern instance as the previous step (shoulders/head/armpits
                    # unchanged, only price has kept drifting past the neckline) — the
                    # reference repo's `hs_lock` equivalent, so one real formation isn't
                    # counted as dozens of independent "Calls" in the accuracy panel.
                    continue
                last_geometry = geometry

                scan_date = idx[cutoff].strftime("%Y-%m-%d")
                scan_ts = f"{scan_date} 00:00:00"
                logged = log_head_shoulders_pattern(
                    ticker, result["pattern_type"], result["phase"], scan_date, result["close_price"], scan_ts,
                    measured_target=result.get("measured_target"),
                    volume_confirms=result.get("volume_confirms"),
                    rsi_divergence=result.get("rsi_divergence"),
                    pattern_r2=result.get("pattern_r2"),
                    prior_trend_pct=result.get("prior_trend_pct"),
                )
                if logged:
                    total_logged += 1

    resolved = fill_pattern_outcomes()
    logger.info(
        "HeadShouldersEngine: backfill logged %d historical patterns, resolved %d outcomes.",
        total_logged, resolved,
    )
    return total_logged
