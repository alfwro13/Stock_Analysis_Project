# ai_contagion_engine.py
import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Optional

import pandas as pd
import yfinance as yf

from config import HISTORICAL_DIR
from time_engine import is_market_open
from tools.network_engine import yahoo_connection_boundary

logger = logging.getLogger(__name__)

_DEFAULT_BELLWETHERS = ["NVDA", "AMD", "MSFT", "META", "GOOGL", "AAPL", "AVGO"]
_DEFAULT_ETFS = ["SMH", "SOXX", "QQQ"]


class AIContagionEngine:
    """
    Monitors AI bellwether stocks and semiconductor ETFs for intraday flash crashes
    using 15-minute pre/post-market data.

    Requires two-tier confirmation: at least one bellwether drops > LEADER_THRESHOLD_PCT
    AND at least one ETF drops > ETF_CONFIRMATION_THRESHOLD_PCT. Returns a single event
    dict keyed against synthetic ticker "SECTOR" — the scheduler wrapper runs this through
    the existing _evaluate_alert_gate() dedup logic before dispatching.
    """

    def __init__(self, config: dict) -> None:
        cfg = config.get("NOTIFICATIONS", {}).get("AI_CONTAGION", {})
        self.bellwethers: list = cfg.get("BELLWETHER_TICKERS", _DEFAULT_BELLWETHERS)
        self.etfs: list = cfg.get("ETF_BASKET", _DEFAULT_ETFS)
        # Store as negative fraction for direct comparison with drawdown values
        self.leader_threshold: float = -abs(cfg.get("LEADER_THRESHOLD_PCT", 4.0)) / 100.0
        self.etf_threshold: float = -abs(cfg.get("ETF_CONFIRMATION_THRESHOLD_PCT", 2.5)) / 100.0
        self.volume_spike_multiplier: float = cfg.get("VOLUME_SPIKE_MULTIPLIER", 1.8)

    # ── public API ─────────────────────────────────────────────────────────────

    def scan(self, conn: sqlite3.Connection) -> list:
        """
        Runs a full contagion check. Returns [] if no contagion or outside active window.
        Returns [event_dict] (single-element list) when a contagion event is confirmed.

        The event dict uses ticker="SECTOR" as a synthetic dedup key so alert_state tracks
        one cooldown slot for the whole sector rather than per-ticker slots.
        conn is used for snapshot recording and passed through; not closed here.
        """
        if not is_market_open("NYSE", include_premarket=True):
            return []

        df_basket = self._fetch_basket_data()
        if df_basket is None or df_basket.empty:
            return []

        leader_shocks = []
        for ticker in self.bellwethers:
            hit = self._evaluate_ticker(ticker, df_basket, is_etf=False)
            if hit is not None:
                leader_shocks.append(hit)

        if not leader_shocks:
            return []

        etf_hits = []
        for ticker in self.etfs:
            hit = self._evaluate_ticker(ticker, df_basket, is_etf=True)
            if hit is not None:
                etf_hits.append(hit)

        if not etf_hits:
            return []

        worst_leader_pct = min(s["intraday_pct"] for s in leader_shocks)
        all_shocks = leader_shocks + etf_hits
        avg_shock_pct = sum(abs(s["intraday_pct"]) for s in all_shocks) / len(all_shocks)
        breadth = len(leader_shocks) / max(len(self.bellwethers), 1)
        severity_score = round(breadth * 0.5 + min(avg_shock_pct / 10.0, 1.0) * 0.5, 3)
        return [{
            "ticker": "SECTOR",
            # price field is used by the gate's retrigger/rearm math as a magnitude proxy
            "price": abs(worst_leader_pct),
            "intraday_pct": worst_leader_pct,
            "leader_shocks": leader_shocks,
            "etf_hits": etf_hits,
            # reason must be fingerprint-stable (alphabetic words only, no numerics)
            "reason": "FLASH CRASH LEADER SHOCK",
            "volume_spikes": [s["ticker"] for s in leader_shocks if s["volume_spike"]],
            "severity_score": severity_score,
        }]

    # ── internals ──────────────────────────────────────────────────────────────

    def _fetch_basket_data(self) -> Optional[pd.DataFrame]:
        tickers = self.bellwethers + self.etfs
        try:
            with yahoo_connection_boundary("AI Contagion Basket") as session:
                df = yf.download(
                    tickers,
                    period="2d",
                    interval="15m",
                    group_by="ticker",
                    auto_adjust=True,
                    prepost=True,
                    progress=False,
                    session=session,
                )
            if df is None or df.empty:
                logger.warning("AIContagionEngine: empty data returned from yfinance.")
                return None
            return df
        except Exception as e:
            logger.error(f"AIContagionEngine: yfinance fetch failed: {e}")
            return None

    def _evaluate_ticker(self, ticker: str, df_basket: pd.DataFrame, is_etf: bool) -> Optional[dict]:
        """
        Slices per-ticker data from the basket download (MultiIndex-aware), calculates
        drawdown vs the previous day's close, and returns a hit dict if the threshold is
        breached. Returns None when below threshold or data is insufficient.
        """
        try:
            if not isinstance(df_basket.columns, pd.MultiIndex):
                return None
            if ticker not in df_basket.columns.get_level_values(0):
                return None
            df = df_basket[ticker].copy()

            df = df.dropna(subset=["Close"])
            if len(df) < 2:
                return None

            df["_date"] = df.index.date
            unique_dates = sorted(df["_date"].unique())
            if len(unique_dates) < 2:
                return None

            current_price = float(df["Close"].iloc[-1])
            prev_df = df[df["_date"] == unique_dates[-2]]
            if prev_df.empty:
                return None
            prev_close = float(prev_df["Close"].iloc[-1])
            if prev_close <= 0.0:
                return None

            drawdown = (current_price - prev_close) / prev_close
            threshold = self.etf_threshold if is_etf else self.leader_threshold
            if drawdown > threshold:
                return None

            return {
                "ticker": ticker,
                "price": current_price,
                "intraday_pct": round(drawdown * 100.0, 2),
                "volume_spike": self._check_volume_spike(ticker, df),
                "is_etf": is_etf,
            }
        except Exception as e:
            logger.error(f"AIContagionEngine: evaluate_ticker failed for {ticker}: {e}")
            return None

    def _check_volume_spike(self, ticker: str, df: pd.DataFrame) -> bool:
        """Compares current 15-min volume against 20-day historical average.
        Falls back to False if historical parquet is absent (e.g. ETFs not in portfolio store)."""
        try:
            df_hist = pd.read_parquet(HISTORICAL_DIR / f"{ticker}.parquet", columns=["Volume"])
            avg_vol = float(df_hist["Volume"].tail(20).mean())
            if avg_vol <= 0 or pd.isna(avg_vol):
                return False
            recent_vol = float(df["Volume"].iloc[-1]) if "Volume" in df.columns else 0.0
            # avg_vol is from daily OHLCV parquet; normalize to per-15min-bar equivalent
            # (~26 bars per regular trading day: 6.5 hours × 4 bars/hr)
            avg_vol_per_bar = avg_vol / 26.0
            return recent_vol >= self.volume_spike_multiplier * avg_vol_per_bar
        except Exception:
            return False


# ── module-level persistence ────────────────────────────────────────────────────

def record_scan_snapshot(conn: sqlite3.Connection, alerts: list) -> None:
    """
    Records every scan run into ai_contagion_snapshots regardless of whether an alert fired.
    This powers the /market-sentiment status panel (last scan time, hit counts).
    Prunes rows older than 7 days inline to keep the table bounded.
    """
    scan_ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    if alerts:
        event = alerts[0]
        leader_count = len(event.get("leader_shocks", []))
        etf_count = len(event.get("etf_hits", []))
        payload = json.dumps({
            "tickers": [
                {
                    "ticker": s["ticker"],
                    "pct": s["intraday_pct"],
                    "vol_spike": s["volume_spike"],
                    "is_etf": s["is_etf"],
                }
                for s in event.get("leader_shocks", []) + event.get("etf_hits", [])
            ],
            "severity_score": event.get("severity_score", 0.0),
        })
    else:
        leader_count = 0
        etf_count = 0
        payload = json.dumps({"tickers": [], "severity_score": 0.0})

    try:
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO ai_contagion_snapshots "
            "(scan_ts, leader_count, etf_count, alert_fired, payload_json) "
            "VALUES (?, ?, ?, ?, ?)",
            (scan_ts, leader_count, etf_count, 1 if alerts else 0, payload),
        )
        cursor.execute(
            "DELETE FROM ai_contagion_snapshots WHERE scan_ts < datetime('now', '-7 days')"
        )
        conn.commit()
    except Exception as e:
        logger.error(f"AIContagionEngine: failed to record scan snapshot: {e}")
