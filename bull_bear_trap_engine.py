from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

import numpy as np
import pandas as pd
import ta

from config import HISTORICAL_DIR
from database import get_connection, log_trap_phase, get_unresolved_trap_phases, batch_update_trap_phase_actuals
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

# GUI name: "Market Trap & Recovery Monitor". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.

_DEFAULT_PROXY_TICKERS = ["QQQ", "SMH", "NVDA", "MSFT", "AAPL"]

_PHASE_EXPECTED_DIRECTION: dict[str, str] = {
    "BULL_TRAP_RISK":       "down",
    "CAPITULATION_FORMING": "up",
    "BEAR_TRAP_RISK":       "up",
    "ACCUMULATION":         "up",
    "ACTIVE_SELLOFF":       "down",
}
_RESOLUTION_HORIZONS: tuple[int, ...] = (14, 30)

# Lifecycle phase labels in severity order (most severe first)
_PHASE_ORDER = [
    "ACTIVE_SELLOFF",
    "BULL_TRAP_RISK",
    "CAPITULATION_FORMING",
    "BEAR_TRAP_RISK",
    "ACCUMULATION",
    "NEUTRAL",
]


class TrapEngine:
    # Detects Bull Trap / Bear Trap / Capitulation / Wyckoff Accumulation phases from daily Parquet data.

    def __init__(self, config: dict) -> None:
        cfg = config.get("NOTIFICATIONS", {}).get("TRAP_MONITOR_ALERTS", {})
        self.bull_vol_ratio_severe: float = cfg.get("BULL_TRAP_VOLUME_RATIO", 0.75)
        self.bull_vol_ratio_elevated: float = 0.90
        self.bear_vol_ratio: float = cfg.get("BEAR_TRAP_VOLUME_RATIO", 1.20)
        self.cap_vol_zscore: float = cfg.get("CAPITULATION_VOL_ZSCORE", 3.0)
        self.wyckoff_bb_squeeze_pct: float = cfg.get("WYCKOFF_BB_SQUEEZE_PCT", 2.0) / 100.0
        self.proxy_tickers: list = cfg.get("PROXY_TICKERS", _DEFAULT_PROXY_TICKERS)
        sched_cfg = config.get("SCHEDULING", {}).get("TRAP_MONITORS", {})
        self.bull_trap_enabled: bool = sched_cfg.get("BULL_TRAP", True)
        self.bear_trap_enabled: bool = sched_cfg.get("BEAR_TRAP", True)
        self.cap_enabled: bool = sched_cfg.get("CAPITULATION", True)
        self.wyckoff_enabled: bool = sched_cfg.get("WYCKOFF", True)
        self.monitor_portfolio: bool = sched_cfg.get("MONITOR_PORTFOLIO", True)
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
            df = df.copy()
            if len(df) < 22:
                return None

            close = df["Close"]
            high  = df["High"]
            low   = df["Low"]
            vol   = df["Volume"]

            ema20 = ta.trend.EMAIndicator(close=close, window=20).ema_indicator()
            rsi14 = ta.momentum.RSIIndicator(close=close, window=14).rsi()
            bb    = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
            atr   = ta.volatility.AverageTrueRange(high=high, low=low, close=close, window=14).average_true_range()

            latest_close = float(close.iloc[-1])
            latest_ema   = float(ema20.iloc[-1])
            latest_rsi   = float(rsi14.iloc[-1]) if not pd.isna(rsi14.iloc[-1]) else 50.0
            ema_distance = (latest_close - latest_ema) / latest_ema if latest_ema > 0 else 0.0

            bull = self._detect_bull_trap(df, close, vol, ema20, rsi14) if self.bull_trap_enabled else _neutral_result()
            bear = self._detect_bear_trap(df, close, vol, low, bb, rsi14) if self.bear_trap_enabled else _neutral_result()
            cap  = self._detect_capitulation(df, close, high, low, vol, ema20, rsi14) if self.cap_enabled else _neutral_result()
            wyk  = self._detect_wyckoff(df, close, vol, bb, atr) if self.wyckoff_enabled else _neutral_result()

            phase = self._derive_phase(bull, bear, cap, wyk, ema_distance)

            return {
                "ticker": ticker,
                "phase": phase,
                "close_price": round(latest_close, 4),
                "bull_trap_level": bull["level"],
                "bull_trap_vol_ratio": bull.get("vol_ratio"),
                "bull_trap_notes": bull.get("notes"),
                "bear_trap_level": bear["level"],
                "bear_trap_notes": bear.get("notes"),
                "cap_level": cap["level"],
                "cap_vol_zscore": cap.get("vol_zscore"),
                "cap_notes": cap.get("notes"),
                "wyckoff_level": wyk["level"],
                "wyckoff_bb_width": wyk.get("bb_width"),
                "wyckoff_notes": wyk.get("notes"),
                "ema_distance": round(ema_distance * 100, 2),
                "rsi": round(latest_rsi, 1),
                "scan_ts": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"),
            }
        except Exception as e:
            logger.error("TrapEngine: analysis failed for %s: %s", ticker, e)
            return None

    def _detect_bull_trap(
        self,
        df: pd.DataFrame,
        close: pd.Series,
        vol: pd.Series,
        ema20: pd.Series,
        rsi14: pd.Series,
    ) -> dict:
        # Bull Trap: low-volume bounce below 20-EMA signals short-covering rather than institutional buying.
        lookback = min(15, len(close))
        recent_close = close.iloc[-lookback:]
        recent_vol   = vol.iloc[-lookback:]
        recent_ema   = ema20.iloc[-lookback:]

        latest_close = float(recent_close.iloc[-1])
        latest_ema   = float(recent_ema.iloc[-1])

        if latest_close >= latest_ema:
            return {"level": "SAFE"}

        returns = recent_close.pct_change().dropna()
        aligned_vol = recent_vol.reindex(returns.index)
        up_mask   = returns > 0
        down_mask = returns < 0

        if up_mask.sum() == 0 or down_mask.sum() == 0:
            return {"level": "SAFE"}

        last_return = float(recent_close.pct_change().iloc[-1])
        if last_return <= 0:
            return {"level": "ACTIVE_SELLOFF", "notes": "Price still declining below 20-EMA."}

        avg_up_vol   = float(aligned_vol[up_mask].mean())
        avg_down_vol = float(aligned_vol[down_mask].mean())
        if avg_down_vol <= 0:
            return {"level": "SAFE"}

        vol_ratio = avg_up_vol / avg_down_vol
        rsi_val   = float(rsi14.iloc[-1]) if not pd.isna(rsi14.iloc[-1]) else 50.0
        notes_parts = []

        if vol_ratio < self.bull_vol_ratio_severe:
            level = "SEVERE_TRAP_RISK"
            notes_parts.append(f"Vol ratio {vol_ratio:.2f} — recovery volume severely below sell-off volume.")
        elif vol_ratio < self.bull_vol_ratio_elevated:
            level = "ELEVATED_RISK"
            notes_parts.append(f"Vol ratio {vol_ratio:.2f} — recovery volume below sell-off volume.")
        else:
            return {"level": "SAFE"}

        if rsi_val < 50:
            notes_parts.append(f"RSI {rsi_val:.0f} still below 50 — momentum not recovered.")
        ema_dist = (latest_close - latest_ema) / latest_ema * 100
        if abs(ema_dist) < 2.0:
            notes_parts.append(f"Price approaching 20-EMA resistance ({ema_dist:+.1f}%).")

        return {"level": level, "vol_ratio": round(vol_ratio, 3), "notes": " ".join(notes_parts)}

    def _detect_bear_trap(
        self,
        df: pd.DataFrame,
        close: pd.Series,
        vol: pd.Series,
        low: pd.Series,
        bb: ta.volatility.BollingerBands,
        rsi14: pd.Series,
    ) -> dict:
        # Bear Trap: intraday breach of BB lower / 20-day low that closes back above on low-conviction volume.
        if len(close) < 21:
            return {"level": "SAFE"}

        bb_lower   = bb.bollinger_lband()
        vol_sma20  = vol.rolling(20).mean()

        recent_low   = float(low.iloc[-1])
        recent_close = float(close.iloc[-1])
        recent_vol   = float(vol.iloc[-1])
        avg_vol      = float(vol_sma20.iloc[-1]) if not pd.isna(vol_sma20.iloc[-1]) else 0.0
        support      = float(bb_lower.iloc[-1]) if not pd.isna(bb_lower.iloc[-1]) else 0.0

        prior_20_low = float(low.iloc[-21:-1].min())
        actual_support = min(support, prior_20_low)

        if actual_support <= 0:
            return {"level": "SAFE"}

        if recent_low >= actual_support:
            return {"level": "SAFE"}
        if recent_close <= actual_support:
            return {"level": "SAFE"}

        if avg_vol <= 0:
            return {"level": "SAFE"}

        vol_ratio = recent_vol / avg_vol
        notes_parts = []

        if vol_ratio < self.bear_vol_ratio:
            level = "CONFIRMED_BEAR_TRAP"
            notes_parts.append(f"Breakdown on {vol_ratio:.2f}× avg vol — low conviction sell-off, likely short squeeze forming.")
        else:
            level = "POSSIBLE_BEAR_TRAP"
            notes_parts.append(f"Support breached and recovered; vol {vol_ratio:.2f}× avg — monitor for follow-through.")

        if len(rsi14) >= 10:
            prior_rsi_trough = float(rsi14.iloc[-11:-1].min())
            current_rsi      = float(rsi14.iloc[-1]) if not pd.isna(rsi14.iloc[-1]) else 50.0
            if current_rsi > prior_rsi_trough and prior_rsi_trough < 40:
                notes_parts.append(f"RSI bullish divergence detected ({current_rsi:.0f} vs prior trough {prior_rsi_trough:.0f}).")

        return {"level": level, "notes": " ".join(notes_parts)}

    def _detect_capitulation(
        self,
        df: pd.DataFrame,
        close: pd.Series,
        high: pd.Series,
        low: pd.Series,
        vol: pd.Series,
        ema20: pd.Series,
        rsi14: pd.Series,
    ) -> dict:
        # Capitulation: volume climax (>3σ) + extreme RSI oversold; long lower wick signals institutional absorption.
        if len(vol) < 22:
            return {"level": "NONE"}

        vol_series = vol.iloc[:-1]  # exclude today from baseline
        vol_mean = float(vol_series.rolling(20).mean().iloc[-1])
        vol_std  = float(vol_series.rolling(20).std().iloc[-1])

        if vol_mean <= 0 or pd.isna(vol_std) or vol_std == 0:
            return {"level": "NONE"}

        today_vol = float(vol.iloc[-1])
        vol_zscore = (today_vol - vol_mean) / vol_std

        rsi_val = float(rsi14.iloc[-1]) if not pd.isna(rsi14.iloc[-1]) else 50.0
        latest_close = float(close.iloc[-1])
        latest_high  = float(high.iloc[-1])
        latest_low   = float(low.iloc[-1])
        latest_ema   = float(ema20.iloc[-1])

        gate_vol  = vol_zscore >= self.cap_vol_zscore
        gate_rsi  = rsi_val < 30
        gate_below_ema = latest_close < latest_ema

        if not (gate_vol and gate_rsi and gate_below_ema):
            return {"level": "NONE", "vol_zscore": round(vol_zscore, 2)}

        notes_parts = [f"Volume {vol_zscore:.1f}σ above 20d mean — extreme volume climax.", f"RSI {rsi_val:.0f} — deeply oversold."]

        day_range = latest_high - latest_low
        if day_range > 0:
            close_position = (latest_close - latest_low) / day_range
            if close_position >= 0.5:
                notes_parts.append(f"Close in upper {close_position*100:.0f}% of range — long lower wick signals absorption.")
                level = "CAPITULATION_FORMING"
            else:
                notes_parts.append("Close in lower half of range — selling pressure may continue.")
                level = "WATCH"
        else:
            level = "WATCH"

        ema_dist = (latest_close - latest_ema) / latest_ema * 100
        if ema_dist < -7:
            notes_parts.append(f"Price {ema_dist:.1f}% below 20-EMA — deeply extended to downside.")

        return {"level": level, "vol_zscore": round(vol_zscore, 2), "notes": " ".join(notes_parts)}

    def _detect_wyckoff(
        self,
        df: pd.DataFrame,
        close: pd.Series,
        vol: pd.Series,
        bb: ta.volatility.BollingerBands,
        atr: pd.Series,
    ) -> dict:
        # Wyckoff Accumulation: BB squeeze + ATR contraction + volume dry-up after a downtrend.
        if len(close) < 25:
            return {"level": "NONE"}

        bb_upper = bb.bollinger_hband()
        bb_mid   = bb.bollinger_mavg()
        bb_lower = bb.bollinger_lband()

        latest_upper = float(bb_upper.iloc[-1])
        latest_lower = float(bb_lower.iloc[-1])
        latest_mid   = float(bb_mid.iloc[-1])

        if latest_mid <= 0 or pd.isna(latest_upper) or pd.isna(latest_lower):
            return {"level": "NONE"}

        bb_width = (latest_upper - latest_lower) / latest_mid
        bb_width_20d_max = float(
            ((bb_upper - bb_lower) / bb_mid).iloc[-21:-1].max()
        )

        gate_squeeze = bb_width <= self.wyckoff_bb_squeeze_pct and bb_width < bb_width_20d_max * 0.7

        if not gate_squeeze:
            return {"level": "NONE", "bb_width": round(bb_width * 100, 2)}

        notes_parts = [f"Bollinger width {bb_width*100:.1f}% — 20-day low; bands squeezing."]
        severity_score = 0

        if len(atr) >= 21:
            atr_now   = float(atr.iloc[-1])
            atr_20avg = float(atr.iloc[-21:-1].mean())
            if atr_20avg > 0 and atr_now < atr_20avg * 0.7:
                notes_parts.append(f"ATR contracted to {atr_now/atr_20avg*100:.0f}% of 20-day avg — volatility drying up.")
                severity_score += 1

        vol_5d  = float(vol.iloc[-5:].mean())
        vol_20d = float(vol.iloc[-25:-5].mean())
        if vol_20d > 0 and vol_5d < vol_20d * 0.7:
            notes_parts.append(f"5-day avg vol {vol_5d/vol_20d*100:.0f}% of 20-day avg — supply exhaustion.")
            severity_score += 1

        prior_change = (float(close.iloc[-1]) - float(close.iloc[-21])) / float(close.iloc[-21]) * 100
        if prior_change < -5:
            notes_parts.append(f"Preceded by {prior_change:.1f}% decline — base forming after downtrend.")
            severity_score += 1

        level = "ACCUMULATION_PHASE" if severity_score >= 2 else "SQUEEZE_FORMING"
        return {"level": level, "bb_width": round(bb_width * 100, 2), "notes": " ".join(notes_parts)}

    def _derive_phase(
        self,
        bull: dict,
        bear: dict,
        cap: dict,
        wyk: dict,
        ema_distance: float,
    ) -> str:
        cap_level  = cap.get("level", "NONE")
        wyk_level  = wyk.get("level", "NONE")
        bull_level = bull.get("level", "SAFE")
        bear_level = bear.get("level", "SAFE")

        if bull_level == "ACTIVE_SELLOFF":
            return "ACTIVE_SELLOFF"
        if cap_level == "CAPITULATION_FORMING":
            return "CAPITULATION_FORMING"
        if bull_level in ("SEVERE_TRAP_RISK", "ELEVATED_RISK"):
            return "BULL_TRAP_RISK"
        if bear_level in ("CONFIRMED_BEAR_TRAP", "POSSIBLE_BEAR_TRAP"):
            return "BEAR_TRAP_RISK"
        if wyk_level == "ACCUMULATION_PHASE":
            return "ACCUMULATION"
        if cap_level == "WATCH" or wyk_level == "SQUEEZE_FORMING":
            return "CAUTION"
        return "NEUTRAL"

    def _get_ticker_list(self) -> list[str]:
        tickers: set[str] = set(self.proxy_tickers)
        if self.monitor_portfolio:
            try:
                from accounts_engine import get_combined_holdings
                for t in get_combined_holdings().keys():
                    tickers.add(t.upper())
            except Exception as e:
                logger.warning("TrapEngine: could not load portfolio tickers: %s", e)
        tickers -= self.ignored_tickers
        return sorted(tickers)

    def _load_history(self, ticker: str) -> Optional[pd.DataFrame]:
        path = HISTORICAL_DIR / f"{ticker}.parquet"
        if not path.exists():
            logger.info("TrapEngine: no parquet for %s — fetching 2-year history.", ticker)
            try:
                data = yahoo_engine.get_price_history([ticker], period="2y", interval="1d")
                df_fetched = data.get(ticker)
                if df_fetched is None or df_fetched.empty:
                    logger.warning("TrapEngine: no price data returned for %s — skipping.", ticker)
                    return None
                if df_fetched.index.tz is not None:
                    df_fetched.index = df_fetched.index.tz_convert(None)
                HISTORICAL_DIR.mkdir(parents=True, exist_ok=True)
                df_fetched.to_parquet(path, engine="pyarrow")
                logger.info("TrapEngine: fetched and saved history for %s (%d rows).", ticker, len(df_fetched))
            except Exception as e:
                logger.warning("TrapEngine: failed to fetch history for %s: %s", ticker, e)
                return None
        try:
            df = pd.read_parquet(path, columns=["Open", "High", "Low", "Close", "Volume"])
            df = df.dropna(subset=["Close", "Volume"])
            df = df[df["Volume"] > 0]
            return df.tail(60)
        except Exception as e:
            logger.warning("TrapEngine: failed to load %s: %s", ticker, e)
            return None

    def _save_results(self, results: list[dict]) -> None:
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            for row in results:
                cursor.execute(
                    """
                    INSERT INTO trap_monitor_results
                        (ticker, phase, bull_trap_level, bull_trap_vol_ratio, bull_trap_notes,
                         bear_trap_level, bear_trap_notes,
                         cap_level, cap_vol_zscore, cap_notes,
                         wyckoff_level, wyckoff_bb_width, wyckoff_notes,
                         ema_distance, rsi, scan_ts)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(ticker) DO UPDATE SET
                        phase=excluded.phase,
                        bull_trap_level=excluded.bull_trap_level,
                        bull_trap_vol_ratio=excluded.bull_trap_vol_ratio,
                        bull_trap_notes=excluded.bull_trap_notes,
                        bear_trap_level=excluded.bear_trap_level,
                        bear_trap_notes=excluded.bear_trap_notes,
                        cap_level=excluded.cap_level,
                        cap_vol_zscore=excluded.cap_vol_zscore,
                        cap_notes=excluded.cap_notes,
                        wyckoff_level=excluded.wyckoff_level,
                        wyckoff_bb_width=excluded.wyckoff_bb_width,
                        wyckoff_notes=excluded.wyckoff_notes,
                        ema_distance=excluded.ema_distance,
                        rsi=excluded.rsi,
                        scan_ts=excluded.scan_ts
                    """,
                    (
                        row["ticker"], row["phase"],
                        row["bull_trap_level"], row.get("bull_trap_vol_ratio"), row.get("bull_trap_notes"),
                        row["bear_trap_level"], row.get("bear_trap_notes"),
                        row["cap_level"], row.get("cap_vol_zscore"), row.get("cap_notes"),
                        row["wyckoff_level"], row.get("wyckoff_bb_width"), row.get("wyckoff_notes"),
                        row.get("ema_distance"), row.get("rsi"), row["scan_ts"],
                    ),
                )
            conn.commit()
        except Exception as e:
            logger.error("TrapEngine: failed to save results: %s", e)
        finally:
            if conn:
                conn.close()

        scan_date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        for row in results:
            log_trap_phase(
                row["ticker"],
                row["phase"],
                scan_date,
                row.get("close_price"),
                row["scan_ts"],
            )



def _neutral_result() -> dict:
    return {"level": "SAFE"}


def _phase_severity(phase: str) -> int:
    """Lower index = higher severity. Used for sort ordering in the API response."""
    try:
        return _PHASE_ORDER.index(phase)
    except ValueError:
        return len(_PHASE_ORDER)


def fill_trap_phase_actuals() -> int:
    today = datetime.now(timezone.utc).date()
    cutoff_14d = (today - timedelta(days=14)).strftime("%Y-%m-%d")
    cutoff_30d = (today - timedelta(days=30)).strftime("%Y-%m-%d")

    pending = get_unresolved_trap_phases(cutoff_14d, cutoff_30d)
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
            logger.error("fill_trap_phase_actuals: failed to load %s: %s", ticker, e)
            continue
        if df.empty or "Close" not in df.columns:
            continue

        date_strs = pd.to_datetime(df.index).normalize().strftime("%Y-%m-%d").tolist()
        close_vals = df["Close"].tolist()
        date_close = list(zip(date_strs, close_vals))

        for row in rows:
            expected = _PHASE_EXPECTED_DIRECTION.get(row["phase"])
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

    batch_update_trap_phase_actuals(batch)
    return len(batch)
