import json
import logging
from datetime import datetime, timezone
from typing import Dict, List, Optional

import time_engine

import numpy as np
import pandas as pd
from config import HISTORICAL_DIR
from database import get_mutual_fund_tickers
from market_pulse import is_exchange_open, upsert_live_price
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

# GUI name: "Dip Radar — Intraday Bottom Finder". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.

# Score threshold — at or above this, an alert fires and the session is flagged as bottoming.
_BOTTOMING_THRESHOLD = 65


class IntradayBottomEngine:
    """Dip Radar: scores intraday capitulation bottoms 0–100 via RSI/BB/VWAP/volume-climax conditions; fires alert at ≥65."""

    def __init__(self):
        from config import load_config
        from database import get_connection
        self._get_connection = get_connection
        self.config = load_config()

    def get_active_monitors(self) -> List[str]:
        today = datetime.now(timezone.utc).date().isoformat()
        conn = None
        try:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT ticker FROM intraday_monitors WHERE is_active = 1 AND expire_date >= ?",
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
                (ticker, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), datetime.now(timezone.utc).date().isoformat()),
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
                (ticker, datetime.now(timezone.utc).date().isoformat()),
            )
            conn.commit()
        except Exception as e:
            logger.error("DipRadar: failed to arm alert_state for %s: %s", ticker, e)
        finally:
            if conn:
                conn.close()

    @staticmethod
    def _calculate_vwap(df: pd.DataFrame) -> pd.Series:
        typical = (df["High"] + df["Low"] + df["Close"]) / 3
        return (typical * df["Volume"]).cumsum() / df["Volume"].cumsum()

    def analyze_ticker(self, ticker: str, data: Optional[pd.DataFrame] = None) -> Optional[Dict]:
        try:
            if data is None:
                if ticker in get_mutual_fund_tickers([ticker]):
                    return None
                _result = yahoo_engine.get_intraday([ticker], period="1d", interval="1m")
                data = _result.get(ticker, pd.DataFrame())
            if data.empty or len(data) < 32:
                logger.warning("DipRadar: insufficient 1m data for %s (%d bars)", ticker, len(data))
                return None

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

            # Last bar within 2 min is still forming during live hours — use iloc[-2] to avoid partial data.
            try:
                last_ts = df.index[-1]
                now_utc = pd.Timestamp.now(tz=timezone.utc)
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
                    reasons.append(f"Heavily oversold — stock dropped too far, too fast (momentum: {rsi_val:.0f}/100)")
                elif rsi_val < 30:
                    score += 15
                    reasons.append(f"Oversold — strong selling pressure building (momentum: {rsi_val:.0f}/100)")

            close = float(cur["Close"])
            bb_lower = float(cur["BB_Lower"]) if not pd.isna(cur["BB_Lower"]) else None
            if bb_lower is not None and close < bb_lower:
                score += 25
                reasons.append("Price far below its normal range — unusually deep sell-off vs. recent history")

            vwap = float(cur["VWAP"]) if not pd.isna(cur["VWAP"]) else None
            vwap_lower = float(cur["VWAP_Lower"]) if not pd.isna(cur["VWAP_Lower"]) else None
            vwap_dev = (close - vwap) if vwap else None
            if vwap_lower is not None and close < vwap_lower:
                score += 20
                reasons.append("Trading far below today's average price — most buyers today are sitting on losses")

            if bool(cur["Vol_Climax"]) and close < prev_close:
                score += 25
                reasons.append("Panic selling spike — unusually heavy volume on a down move, sellers may be exhausted")

            is_bottoming = score >= _BOTTOMING_THRESHOLD

            try:
                exchange = time_engine.ticker_exchange(ticker)
                mkt_tz = time_engine.exchange_tz(exchange)
                ts = cur.name
                # yfinance returns tz-aware; parquet strips TZ (naive UTC) — localize only if naive.
                if ts.tzinfo is None:
                    ts = ts.tz_localize(timezone.utc)
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

            try:
                hist_path = HISTORICAL_DIR / f"{ticker}.parquet"
                if hist_path.exists():
                    df_hist = pd.read_parquet(hist_path)
                    df_hist = df_hist[df_hist['Close'].notna()]
                    if not df_hist.empty:
                        upsert_live_price(ticker, ticker, close, float(df_hist['Close'].iloc[-1]))
            except Exception:
                logger.warning("DipRadar: failed to share live price for %s", ticker, exc_info=True)

            return result

        except Exception as e:
            logger.error("DipRadar: analysis failed for %s: %s", ticker, e)
            return None

    def _log_notification(self, msg_type: str, msg_text: str) -> None:
        from database import log_notification
        log_notification(msg_type, msg_text)

    def _fire_alert(self, result: dict) -> None:
        from notification_engine import notify
        ticker = result["ticker"]
        price = result["current_price"]
        score = result["reversal_score"]
        reasons = result["reasons"]

        summary = f"🎯 Dip Radar | {ticker} @ {price} | Score: {score}/100"
        detail = " | ".join(reasons) if reasons else "Multiple conditions met"
        nextcloud_msg = f"{summary}\n" + "\n".join(f"• {r}" for r in reasons)
        notify("dip_radar_alert", "DipRadar", f"{summary} — {detail}", nextcloud_text=nextcloud_msg)

        self._disarm_alert(ticker)

    def _get_currency_map(self, tickers: List[str]) -> dict:
        """Batch-fetch currency from stock_signals so ticker_exchange() can resolve plain US tickers correctly."""
        if not tickers:
            return {}
        conn = None
        try:
            conn = self._get_connection()
            placeholders = ",".join("?" * len(tickers))
            rows = conn.execute(
                f"SELECT ticker, currency FROM stock_signals WHERE ticker IN ({placeholders})",
                tickers,
            ).fetchall()
            return {r["ticker"]: r["currency"] for r in rows}
        except Exception as e:
            logger.error("DipRadar: currency lookup failed: %s", e)
            return {}
        finally:
            if conn:
                conn.close()

    def run_scan(self) -> List[Dict]:
        tickers = self.get_active_monitors()
        if not tickers:
            return []

        currency_map = self._get_currency_map(tickers)

        open_tickers = []
        for ticker in tickers:
            currency = currency_map.get(ticker, "")
            exchange = time_engine.ticker_exchange(ticker, currency)
            premarket = exchange in ("NYSE",)
            if is_exchange_open(exchange, include_premarket=premarket):
                open_tickers.append(ticker)
            else:
                logger.debug("DipRadar: %s — %s market closed, skipping.", ticker, exchange)

        if not open_tickers:
            return []

        mutual_funds = get_mutual_fund_tickers(open_tickers)
        fetch_tickers = [t for t in open_tickers if t not in mutual_funds]

        # Batch-fetch 1m data for all open tickers in one yfinance call to avoid per-ticker rate limits
        batch_data: dict = {}
        if fetch_tickers:
            try:
                batch_data = yahoo_engine.get_intraday(fetch_tickers, period="1d", interval="1m")
            except Exception as e:
                logger.error("DipRadar: batch 1m fetch failed: %s", e)

        hits = []
        for ticker in open_tickers:
            df = batch_data.get(ticker)
            result = self.analyze_ticker(ticker, data=df)
            if result and result["is_bottoming"]:
                if self._should_alert(ticker):
                    self._fire_alert(result)
                hits.append(result)
        return hits

    def deactivate_all_today(self) -> None:
        """Deactivate monitors whose monitoring period has ended (expire_date <= today)."""
        today = datetime.now(timezone.utc).date().isoformat()
        conn = None
        try:
            conn = self._get_connection()
            conn.execute(
                "UPDATE intraday_monitors SET is_active = 0 WHERE is_active = 1 AND expire_date <= ?",
                (today,),
            )
            conn.commit()
            self._log_notification("DipRadar", "Session ended — expired Dip Radar monitors deactivated.")
        except Exception as e:
            logger.error("DipRadar: failed to deactivate monitors: %s", e)
        finally:
            if conn:
                conn.close()

    def deactivate_exchange_today(self, exchange: str) -> None:
        """Deactivate monitors for *exchange* whose monitoring period ends today, leaving multi-day monitors running."""
        today = datetime.now(timezone.utc).date().isoformat()
        conn = None
        try:
            conn = self._get_connection()
            rows = conn.execute(
                "SELECT ticker FROM intraday_monitors WHERE is_active = 1 AND expire_date <= ?",
                (today,),
            ).fetchall()
            tickers = [r["ticker"] for r in rows]
            if not tickers:
                return
            placeholders = ",".join("?" * len(tickers))
            currency_rows = conn.execute(
                f"SELECT ticker, currency FROM stock_signals WHERE ticker IN ({placeholders})",
                tickers,
            ).fetchall()
            currency_map = {r["ticker"]: r["currency"] for r in currency_rows}
            to_deactivate = [t for t in tickers
                             if time_engine.ticker_exchange(t, currency_map.get(t, "")) == exchange]
            if not to_deactivate:
                return
            placeholders = ",".join("?" * len(to_deactivate))
            conn.execute(
                f"UPDATE intraday_monitors SET is_active = 0 WHERE ticker IN ({placeholders})",
                to_deactivate,
            )
            conn.commit()
            self._log_notification(
                "DipRadar",
                f"{exchange} session ended — {len(to_deactivate)} Dip Radar monitor(s) completed.",
            )
        except Exception as e:
            logger.error("DipRadar: failed to deactivate %s monitors: %s", exchange, e)
        finally:
            if conn:
                conn.close()
