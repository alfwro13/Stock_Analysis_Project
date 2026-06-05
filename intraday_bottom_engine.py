# intraday_bottom_engine.py
import json
import logging
from datetime import date, datetime
from typing import Dict, List, Optional

import numpy as np
import pandas as pd
import yfinance as yf

logger = logging.getLogger(__name__)

# Score threshold — at or above this, an alert fires and the session is flagged as bottoming.
_BOTTOMING_THRESHOLD = 65


class IntradayBottomEngine:
    """
    Intraday Dip Radar: detects capitulation/exhaustion bottoms on user-selected tickers.

    Scoring (0–100) based on four independent conditions:
      A. RSI extreme oversold        — up to 30 pts
      B. Price below lower BB (2.5σ) — 25 pts
      C. Price below VWAP - 2.5σ    — 20 pts
      D. Volume climax on down-move  — 25 pts

    Fires an in-app notification when score ≥ 65.
    Fires a Nextcloud Talk message when score ≥ 65 AND config flag is set.
    Uses alert_state table to suppress repeat alerts within the same dip.
    """

    def __init__(self):
        from config import load_config
        from database import get_connection
        self._get_connection = get_connection
        self.config = load_config()

    # ------------------------------------------------------------------
    # DB helpers
    # ------------------------------------------------------------------

    def get_active_monitors(self) -> List[str]:
        today = date.today().isoformat()
        conn = None
        try:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT ticker FROM intraday_monitors WHERE is_active = 1 AND date_added = ?",
                (today,),
            ).fetchall()
            return [r["ticker"] for r in rows]
        except Exception as e:
            logger.error("DipRadar: failed to fetch active monitors: %s", e)
            return []
        finally:
            if conn:
                conn.close()

    def _persist_result(self, result: dict) -> None:
        conn = None
        try:
            conn = self._get_connection()
            conn.execute(
                """INSERT INTO intraday_monitor_results
                   (ticker, scan_ts, current_price, reversal_score, is_bottoming,
                    reasons_json, rsi, bb_lower, vwap, vwap_lower, vwap_deviation, vol_climax)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                   ON CONFLICT(ticker) DO UPDATE SET
                       scan_ts        = excluded.scan_ts,
                       current_price  = excluded.current_price,
                       reversal_score = excluded.reversal_score,
                       is_bottoming   = excluded.is_bottoming,
                       reasons_json   = excluded.reasons_json,
                       rsi            = excluded.rsi,
                       bb_lower       = excluded.bb_lower,
                       vwap           = excluded.vwap,
                       vwap_lower     = excluded.vwap_lower,
                       vwap_deviation = excluded.vwap_deviation,
                       vol_climax     = excluded.vol_climax""",
                (
                    result["ticker"],
                    result["scan_ts"],
                    result["current_price"],
                    result["reversal_score"],
                    1 if result["is_bottoming"] else 0,
                    json.dumps(result["reasons"]),
                    result.get("rsi"),
                    result.get("bb_lower"),
                    result.get("vwap"),
                    result.get("vwap_lower"),
                    result.get("vwap_deviation"),
                    1 if result.get("vol_climax") else 0,
                ),
            )
            conn.commit()
        except Exception as e:
            logger.error("DipRadar: failed to persist result for %s: %s", result["ticker"], e)
        finally:
            if conn:
                conn.close()

    def _should_alert(self, ticker: str) -> bool:
        """Returns True if alert_state says this ticker is still armed for a dip alert."""
        conn = None
        try:
            conn = self._get_connection()
            row = conn.execute(
                "SELECT armed FROM alert_state WHERE engine = 'dip_radar' AND ticker = ?",
                (ticker,),
            ).fetchone()
            return row is not None and bool(row["armed"])
        except Exception as e:
            logger.error("DipRadar: alert_state read failed for %s: %s", ticker, e)
            return False
        finally:
            if conn:
                conn.close()

    def _disarm_alert(self, ticker: str) -> None:
        """After firing, disarm so we don't repeat-alert on the same dip."""
        conn = None
        try:
            conn = self._get_connection()
            conn.execute(
                """INSERT INTO alert_state (engine, ticker, armed, last_fired_utc, fire_count, state_date)
                   VALUES ('dip_radar', ?, 0, ?, 1, ?)
                   ON CONFLICT(engine, ticker) DO UPDATE SET
                       armed          = 0,
                       last_fired_utc = excluded.last_fired_utc,
                       fire_count     = alert_state.fire_count + 1,
                       state_date     = excluded.state_date""",
                (ticker, datetime.utcnow().isoformat(), date.today().isoformat()),
            )
            conn.commit()
        except Exception as e:
            logger.error("DipRadar: failed to disarm alert_state for %s: %s", ticker, e)
        finally:
            if conn:
                conn.close()

    def arm_alert(self, ticker: str) -> None:
        """Arm or re-arm alert_state when user enables monitoring for a ticker."""
        conn = None
        try:
            conn = self._get_connection()
            conn.execute(
                """INSERT INTO alert_state (engine, ticker, armed, state_date)
                   VALUES ('dip_radar', ?, 1, ?)
                   ON CONFLICT(engine, ticker) DO UPDATE SET
                       armed      = 1,
                       state_date = excluded.state_date""",
                (ticker, date.today().isoformat()),
            )
            conn.commit()
        except Exception as e:
            logger.error("DipRadar: failed to arm alert_state for %s: %s", ticker, e)
        finally:
            if conn:
                conn.close()

    # ------------------------------------------------------------------
    # Technical calculations
    # ------------------------------------------------------------------

    @staticmethod
    def _calculate_vwap(df: pd.DataFrame) -> pd.Series:
        typical = (df["High"] + df["Low"] + df["Close"]) / 3
        return (typical * df["Volume"]).cumsum() / df["Volume"].cumsum()

    # ------------------------------------------------------------------
    # Core analysis
    # ------------------------------------------------------------------

    def analyze_ticker(self, ticker: str) -> Optional[Dict]:
        try:
            data = yf.download(ticker, period="1d", interval="1m", progress=False, auto_adjust=True)
            if data.empty or len(data) < 32:
                logger.warning("DipRadar: insufficient 1m data for %s (%d bars)", ticker, len(data))
                return None

            # Flatten MultiIndex columns produced by yfinance ≥0.2
            if isinstance(data.columns, pd.MultiIndex):
                data.columns = data.columns.get_level_values(0)

            df = data.copy()
            df["VWAP"] = self._calculate_vwap(df)
            df["VWAP_Std"] = df["VWAP"].rolling(window=30).std()
            df["VWAP_Lower"] = df["VWAP"] - (2.5 * df["VWAP_Std"])

            # RSI (14-period, manual for no extra deps)
            delta = df["Close"].diff()
            gain = delta.clip(lower=0).rolling(14).mean()
            loss = (-delta.clip(upper=0)).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            df["RSI"] = 100 - (100 / (1 + rs))

            # Bollinger Bands (20-period, 2.5σ)
            df["BB_Mid"] = df["Close"].rolling(20).mean()
            df["BB_Std"] = df["Close"].rolling(20).std()
            df["BB_Lower"] = df["BB_Mid"] - (2.5 * df["BB_Std"])

            # Volume climax
            df["Vol_SMA"] = df["Volume"].rolling(20).mean()
            df["Vol_Std"] = df["Volume"].rolling(20).std()
            df["Vol_Climax"] = df["Volume"] > (df["Vol_SMA"] + 3 * df["Vol_Std"])

            # Select analysis candle: if the last bar started within the past 2 minutes it is
            # still forming (live market hours) — step back one bar. After market close all
            # bars are fully settled so use iloc[-1].
            try:
                last_ts = df.index[-1]
                now_utc = pd.Timestamp.utcnow().tz_localize("UTC")
                last_ts_utc = last_ts.tz_convert("UTC") if last_ts.tzinfo is not None else last_ts.tz_localize("UTC")
                still_forming = (now_utc - last_ts_utc) < pd.Timedelta(minutes=2)
            except Exception:
                still_forming = True  # safe default during market hours
            candle_idx = -2 if still_forming else -1
            cur = df.iloc[candle_idx]
            prev_close = float(df.iloc[candle_idx - 1]["Close"])

            # Skip if all rolling indicators are NaN — session data is still too short
            if pd.isna(cur["RSI"]) and pd.isna(cur["BB_Lower"]) and pd.isna(cur["VWAP_Lower"]):
                logger.warning("DipRadar: all indicators NaN for %s — insufficient session data", ticker)
                return None

            score = 0
            reasons = []

            rsi_val = float(cur["RSI"]) if not pd.isna(cur["RSI"]) else None
            if rsi_val is not None:
                if rsi_val < 25:
                    score += 30
                    reasons.append(f"Extreme Oversold (RSI: {rsi_val:.1f})")
                elif rsi_val < 30:
                    score += 15
                    reasons.append(f"Oversold (RSI: {rsi_val:.1f})")

            close = float(cur["Close"])
            bb_lower = float(cur["BB_Lower"]) if not pd.isna(cur["BB_Lower"]) else None
            if bb_lower is not None and close < bb_lower:
                score += 25
                reasons.append("Price pierced extreme Lower Bollinger Band (2.5σ)")

            vwap = float(cur["VWAP"]) if not pd.isna(cur["VWAP"]) else None
            vwap_lower = float(cur["VWAP_Lower"]) if not pd.isna(cur["VWAP_Lower"]) else None
            vwap_dev = (close - vwap) if vwap else None
            if vwap_lower is not None and close < vwap_lower:
                score += 20
                reasons.append("Extreme negative VWAP deviation (price -2.5σ below VWAP)")

            if bool(cur["Vol_Climax"]) and close < prev_close:
                score += 25
                reasons.append("Volume Capitulation — high-volume down-candle (weak hands washing out)")

            is_bottoming = score >= _BOTTOMING_THRESHOLD

            try:
                mkt_tz = 'Europe/London' if ticker.endswith('.L') else 'America/New_York'
                ts = cur.name
                # yf.download() returns tz-aware timestamps; parquet strips TZ (naive UTC).
                # Handle both: localize only if naive, then convert.
                if ts.tzinfo is None:
                    ts = ts.tz_localize('UTC')
                ts_local = ts.tz_convert(mkt_tz)
                scan_ts = ts_local.strftime("%Y-%m-%d %H:%M %Z")
            except Exception:
                scan_ts = str(cur.name)

            result = {
                "ticker": ticker,
                "scan_ts": scan_ts,
                "current_price": round(close, 4),
                "reversal_score": score,
                "is_bottoming": is_bottoming,
                "reasons": reasons,
                "rsi": round(rsi_val, 2) if rsi_val is not None else None,
                "bb_lower": round(bb_lower, 4) if bb_lower is not None else None,
                "vwap": round(vwap, 4) if vwap is not None else None,
                "vwap_lower": round(vwap_lower, 4) if vwap_lower is not None else None,
                "vwap_deviation": round(vwap_dev, 4) if vwap_dev is not None else None,
                "vol_climax": bool(cur["Vol_Climax"]),
            }
            self._persist_result(result)
            return result

        except Exception as e:
            logger.error("DipRadar: analysis failed for %s: %s", ticker, e)
            return None

    # ------------------------------------------------------------------
    # Notification helpers
    # ------------------------------------------------------------------

    def _log_notification(self, msg_type: str, msg_text: str) -> None:
        from database import log_notification
        log_notification(msg_type, msg_text)

    def _fire_alert(self, result: dict) -> None:
        ticker = result["ticker"]
        price = result["current_price"]
        score = result["reversal_score"]
        reasons = result["reasons"]

        summary = f"🎯 Dip Radar | {ticker} @ {price} | Score: {score}/100"
        detail = " | ".join(reasons) if reasons else "Multiple conditions met"
        self._log_notification("DipRadar", f"{summary} — {detail}")

        if self.config.get("NOTIFICATIONS", {}).get("DIP_RADAR_NEXTCLOUD", False):
            try:
                from nextcloud_talk import send_text_message
                msg = f"{summary}\n" + "\n".join(f"• {r}" for r in reasons)
                send_text_message(msg, self.config)
            except Exception as e:
                logger.error("DipRadar: Nextcloud send failed for %s: %s", ticker, e)

        self._disarm_alert(ticker)

    # ------------------------------------------------------------------
    # Public entry points
    # ------------------------------------------------------------------

    def run_scan(self) -> List[Dict]:
        tickers = self.get_active_monitors()
        if not tickers:
            return []

        hits = []
        for ticker in tickers:
            result = self.analyze_ticker(ticker)
            if result and result["is_bottoming"]:
                if self._should_alert(ticker):
                    self._fire_alert(result)
                hits.append(result)
        return hits

    def deactivate_all_today(self) -> None:
        today = date.today().isoformat()
        conn = None
        try:
            conn = self._get_connection()
            conn.execute(
                "UPDATE intraday_monitors SET is_active = 0 WHERE date_added = ?", (today,)
            )
            conn.commit()
            self._log_notification("DipRadar", "Session ended — all Dip Radar monitors deactivated.")
        except Exception as e:
            logger.error("DipRadar: failed to deactivate monitors: %s", e)
        finally:
            if conn:
                conn.close()
