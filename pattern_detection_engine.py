from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

import double_top_bottom_engine
import flag_engine
import head_shoulders_engine
import narrow_range_engine
import triangle_engine
import volatility_squeeze_engine
from config import HISTORICAL_DIR, load_config
from database import (
    get_connection,
    log_pattern_detection,
    get_unresolved_pattern_detections,
    batch_update_pattern_detection_actuals,
)
from indicators import compute_rsi, compute_volume_sma
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

# GUI name: "Pattern Detection". This is the canonical extension point for future swing-pattern
# detectors (Triangles, Wedges, Flag & Pennant, ...): each family is a module exposing FAMILY,
# PATTERN_TYPES (pattern_type -> expected breakout direction "up"/"down"), and
# detect(ticker, df, rsi_series, vol_sma, config) -> dict | None returning the generic
# points/lines/key_level result shape. Register it below — no other file needs to change.
# See assets/pattern_detection.md for the full walkthrough.
DETECTORS = {
    head_shoulders_engine.FAMILY: head_shoulders_engine,
    double_top_bottom_engine.FAMILY: double_top_bottom_engine,
    flag_engine.FAMILY: flag_engine,
    triangle_engine.FAMILY: triangle_engine,
    volatility_squeeze_engine.FAMILY: volatility_squeeze_engine,
    narrow_range_engine.FAMILY: narrow_range_engine,
}

_MIN_BARS = 60
_LOOKBACK_BARS = 180
_BACKFILL_STEP_DAYS = 5
_RESOLUTION_HORIZONS: tuple[int, ...] = (14, 30)


class PatternDetectionEngine:
    """Detects forming/confirmed swing patterns from daily Parquet data, dispatching to every
    registered family in DETECTORS on a single shared ticker scan / parquet load / RSI+volume
    computation pass per ticker."""

    def __init__(self, config: dict) -> None:
        self.config = config
        sched_cfg = config.get("SCHEDULING", {}).get("PATTERN_DETECTION", {})
        self.monitor_portfolio: bool = sched_cfg.get("MONITOR_PORTFOLIO", True)
        self.monitor_watchlist: bool = sched_cfg.get("MONITOR_WATCHLIST", False)
        self.ignored_tickers: set = {str(t).strip().upper() for t in config.get("IGNORED_TICKERS", [])}

    def run_scan(self) -> list[dict]:
        tickers = self._get_ticker_list()
        results: list[dict] = []
        for ticker in tickers:
            df = self._load_history(ticker)
            if df is None:
                continue
            results.extend(self._analyse_ticker(ticker, df))

        if results:
            self._save_results(results)
        return results

    def _analyse_ticker(self, ticker: str, df: pd.DataFrame) -> list[dict]:
        try:
            if len(df) < _MIN_BARS:
                return []

            rsi_series = compute_rsi(df["Close"])
            vol_sma = compute_volume_sma(df["Volume"])

            out: list[dict] = []
            for family, module in DETECTORS.items():
                try:
                    result = module.detect(ticker, df, rsi_series, vol_sma, self.config)
                except Exception as e:
                    logger.error("PatternDetectionEngine: %s detector failed for %s: %s", family, ticker, e)
                    continue
                if result:
                    result["ticker"] = ticker
                    result["pattern_family"] = family
                    result["scan_ts"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                    out.append(result)
            return out
        except Exception as e:
            logger.error("PatternDetectionEngine: analysis failed for %s: %s", ticker, e)
            return []

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
                logger.warning("PatternDetectionEngine: could not load portfolio tickers: %s", e)
        if self.monitor_watchlist:
            try:
                from database import get_watchlist_tickers
                from utils import is_excluded_from_yahoo_fetch
                for t in get_watchlist_tickers():
                    if not is_excluded_from_yahoo_fetch(t, self.ignored_tickers):
                        tickers.add(t.upper())
            except Exception as e:
                logger.warning("PatternDetectionEngine: could not load watchlist tickers: %s", e)
        tickers -= self.ignored_tickers
        return sorted(tickers)

    def _load_history(self, ticker: str) -> Optional[pd.DataFrame]:
        path = HISTORICAL_DIR / f"{ticker}.parquet"
        if not path.exists():
            logger.info("PatternDetectionEngine: no parquet for %s — fetching 2-year history.", ticker)
            try:
                data = yahoo_engine.get_price_history([ticker], period="2y", interval="1d")
                df_fetched = data.get(ticker)
                if df_fetched is None or df_fetched.empty:
                    logger.warning("PatternDetectionEngine: no price data returned for %s — skipping.", ticker)
                    return None
                if df_fetched.index.tz is not None:
                    df_fetched.index = df_fetched.index.tz_convert(None)
                HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
                df_fetched.to_parquet(path, engine="pyarrow")
                logger.info("PatternDetectionEngine: fetched and saved history for %s (%d rows).", ticker, len(df_fetched))
            except Exception as e:
                logger.warning("PatternDetectionEngine: failed to fetch history for %s: %s", ticker, e)
                return None
        try:
            df = pd.read_parquet(path, columns=["Open", "High", "Low", "Close", "Volume"])
            df = df.dropna(subset=["Close", "Volume"])
            df = df[df["Volume"] > 0]
            return df.tail(_LOOKBACK_BARS)
        except Exception as e:
            logger.warning("PatternDetectionEngine: failed to load %s: %s", ticker, e)
            return None

    def _save_results(self, results: list[dict]) -> None:
        conn = None
        previous_by_key: dict = {}
        try:
            conn = get_connection()
            cursor = conn.cursor()
            for row in results:
                cursor.execute(
                    "SELECT pattern_type, phase, points_json FROM pattern_detection_results WHERE ticker=? AND pattern_family=?",
                    (row["ticker"], row["pattern_family"]),
                )
                prev = cursor.fetchone()
                if prev:
                    previous_by_key[(row["ticker"], row["pattern_family"])] = dict(prev)

            for row in results:
                points_json = json.dumps(row["points"])
                lines_json = json.dumps(row["lines"])
                cursor.execute(
                    """
                    INSERT INTO pattern_detection_results
                        (ticker, pattern_family, pattern_type, phase, points_json, lines_json,
                         breakout_date, breakout_price, measured_target,
                         volume_confirms, rsi_divergence, pattern_r2, prior_trend_pct, scan_ts)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(ticker, pattern_family) DO UPDATE SET
                        pattern_type=excluded.pattern_type,
                        phase=excluded.phase,
                        points_json=excluded.points_json,
                        lines_json=excluded.lines_json,
                        breakout_date=excluded.breakout_date, breakout_price=excluded.breakout_price,
                        measured_target=excluded.measured_target,
                        volume_confirms=excluded.volume_confirms, rsi_divergence=excluded.rsi_divergence,
                        pattern_r2=excluded.pattern_r2, prior_trend_pct=excluded.prior_trend_pct,
                        scan_ts=excluded.scan_ts
                    """,
                    (
                        row["ticker"], row["pattern_family"], row["pattern_type"], row["phase"],
                        points_json, lines_json,
                        row.get("breakout_date"), row.get("breakout_price"), row.get("measured_target"),
                        int(row["volume_confirms"]), int(row["rsi_divergence"]), row.get("pattern_r2"),
                        row.get("prior_trend_pct"), row["scan_ts"],
                    ),
                )
            conn.commit()
        except Exception as e:
            logger.error("PatternDetectionEngine: failed to save results: %s", e)
        finally:
            if conn:
                conn.close()

        scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for row in results:
            prev = previous_by_key.get((row["ticker"], row["pattern_family"]))
            unchanged = (
                prev is not None
                and prev["pattern_type"] == row["pattern_type"]
                and prev["phase"] == row["phase"]
                and prev["points_json"] == json.dumps(row["points"])
            )
            if unchanged:
                # Same pattern instance, same phase as the previous scan — skip logging a
                # duplicate history row. A genuinely new instance (different points) or a
                # phase transition (FORMING -> CONFIRMED) still logs.
                continue
            log_pattern_detection(
                row["ticker"], row["pattern_family"], row["pattern_type"], row["phase"],
                scan_date, row.get("close_price"), row["scan_ts"],
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

    pending = get_unresolved_pattern_detections(cutoff_14d, cutoff_30d)
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
            module = DETECTORS.get(row["pattern_family"])
            if module is None:
                continue
            expected = module.PATTERN_TYPES.get(row["pattern_type"])
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

    batch_update_pattern_detection_actuals(batch)
    return len(batch)


def backfill_historical_patterns(tickers: Optional[list[str]] = None) -> int:
    """One-time (operator-triggered) historical backtest: walks each ticker's full parquet
    history at ~weekly intervals, detects confirmed patterns (across every registered family)
    as of each historical date, and immediately resolves 14d/30d outcomes from the same
    parquet's later rows — giving a populated accuracy panel before any live alerting is
    relied upon."""
    engine = PatternDetectionEngine(load_config())
    if tickers is None:
        tickers = engine._get_ticker_list()

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
            logger.warning("PatternDetectionEngine backfill: failed to load %s: %s", ticker, e)
            continue
        if len(df) < _MIN_BARS + _BACKFILL_STEP_DAYS:
            continue

        rsi_full = compute_rsi(df["Close"])
        vol_sma_full = compute_volume_sma(df["Volume"])
        idx = df.index

        for family, module in DETECTORS.items():
            last_geometry_by_type: dict = {}
            for cutoff in range(_MIN_BARS, len(df), _BACKFILL_STEP_DAYS):
                window_df = df.iloc[:cutoff + 1]
                window_rsi = rsi_full.iloc[:cutoff + 1]
                window_vol_sma = vol_sma_full.iloc[:cutoff + 1]

                try:
                    result = module.detect(ticker, window_df, window_rsi, window_vol_sma, engine.config)
                except Exception as e:
                    logger.error("PatternDetectionEngine backfill: %s detector failed for %s: %s", family, ticker, e)
                    continue
                if not result or result["phase"] != "CONFIRMED":
                    continue

                geometry = tuple((p["date"], p["price"]) for p in result["points"])
                if last_geometry_by_type.get(result["pattern_type"]) == geometry:
                    # Same pattern instance as the previous step — the reference repo's
                    # `hs_lock` equivalent, so one real formation isn't counted as dozens of
                    # independent "Calls" in the accuracy panel.
                    continue
                last_geometry_by_type[result["pattern_type"]] = geometry

                scan_date = idx[cutoff].strftime("%Y-%m-%d")
                scan_ts = f"{scan_date} 00:00:00"
                logged = log_pattern_detection(
                    ticker, family, result["pattern_type"], result["phase"], scan_date, result["close_price"], scan_ts,
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
        "PatternDetectionEngine: backfill logged %d historical patterns, resolved %d outcomes.",
        total_logged, resolved,
    )
    return total_logged
