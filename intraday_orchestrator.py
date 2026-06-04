# intraday_orchestrator.py
import hashlib
import ipaddress
import os
import re
import time
import json
import logging
import sqlite3
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import yfinance as yf
import pandas as pd
from datetime import datetime, timezone
from config import load_config, PORTFOLIO_PATH, INTRADAY_DIR, HISTORICAL_DIR, PORT, SERVER_URL
from utils import normalize_ticker
from database import get_connection
from crash_engine import CrashEngine
from moonshot_engine import MoonshotEngine
from anomaly_engine import AnomalyEngine
from nextcloud_talk import send_text_message
from utils import clamp_beta

logger = logging.getLogger(__name__)

# ── Intraday orchestrator thresholds ──────────────────────────────────────────
_STALE_SECONDS        = 5400   # 90 min: market closed / asset halted circuit breaker
_CORP_ACTION_GAP_PCT  = 10.0   # price gap % that triggers a corporate action lookup
_MACRO_YIELD_SURGE_PCT = 1.5   # intraday yield spike % that fires a systemic macro alert
_AI_DEFENSE_THRESHOLD = 2.0    # predicted SPY gap % that activates AI Volatility Defense
_AI_DEFENSE_CAP       = 1.5    # flash-crash threshold cap (%) applied when defense is active
# Delay between consecutive Nextcloud Talk sends. A full second is conservative; if
# Nextcloud's actual rate limit is looser, reduce this. Note: _run() is synchronous on
# an APScheduler worker thread — N alerts × this delay = N seconds of blocked execution,
# during which the DB connection is held and the next scan may queue. With a large
# portfolio under a macro-crash morning, consider batching alerts into a digest message
# instead of N sequential sends.
_DISPATCH_SLEEP_SECONDS = 1


def format_currency(price: float, currency_code: Optional[str]) -> str:
    """
    Formats price with correct symbol, scaling GBp to GBP dynamically.
    Fails over to the 3-letter currency code if symbol mapping is unavailable.
    """
    if price is None:
        return "N/A"
    if not currency_code:
        currency_code = 'USD'  # Fallback
        
    if currency_code == 'GBp':
        price = price / 100.0
        currency_code = 'GBP'

    symbols = {
        'USD': '$',
        'GBP': '£',
        'EUR': '€'
    }
    
    symbol = symbols.get(currency_code)
    if symbol:
        return f"{symbol}{price:,.2f}"
    else:
        return f"{price:,.2f} {currency_code}"

def build_stock_url(server_url: str, port: int, ticker: str) -> str:
    """
    Intelligently constructs the URL. If the server is a domain/proxy, it drops the port.
    If it's an IP address or localhost, it appends the port automatically.
    """
    base = str(server_url).rstrip('/')
    parsed = urlparse(base if "://" in base else f"http://{base}")
    hostname = parsed.hostname or ""

    is_ip_or_local = hostname == "localhost"
    if not is_ip_or_local:
        try:
            ipaddress.ip_address(hostname)
            is_ip_or_local = True
        except ValueError:
            pass

    has_port = parsed.port is not None

    if is_ip_or_local and not has_port:
        return f"{base}:{port}/stock/{ticker}"
    return f"{base}/stock/{ticker}"


class IntradayOrchestrator:
    """
    Centralized monitor that batches network and disk I/O operations, 
    feeding raw context into the mathematical quant engines.
    """
    def __init__(self) -> None:
        self.config = load_config()
        # Instantiate engines with config so they don't reload it internally
        self.crash_engine = CrashEngine(self.config)
        self.moonshot_engine = MoonshotEngine(self.config)
        self.anomaly_engine = AnomalyEngine(self.config)
        # Keyed (ticker, date-str) so each ticker is fetched at most once per calendar day.
        self._corp_action_cache: Dict[tuple, bool] = {}

    def get_portfolio_tickers(self) -> List[str]:
        """Safely extracts all unique tickers currently held in the portfolio."""
        if not os.path.exists(PORTFOLIO_PATH):
            return []
        try:
            with open(PORTFOLIO_PATH, 'r') as f:
                data = json.load(f)
                return [normalize_ticker(v['ticker']) for v in data.values() if 'ticker' in v]
        except Exception:
            logger.error("Failed to load portfolio from %s", PORTFOLIO_PATH, exc_info=True)
            return []

    def get_asset_metadata(self, tickers: List[str]) -> Dict[str, Dict[str, Any]]:
        """Fetches currency, ATR stop loss, company name, ML, and Risk metrics in a single bulk SQLite query."""
        if not tickers:
            return {}
            
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ','.join('?' for _ in tickers)
        
        query = f"""
            SELECT s.ticker, s.company_name, s.currency, s.atr_stop_loss, s.last_updated, s.beta,
                   q.ml_confidence_score, q.var_95, q.cvar_95, q.sentiment_score,
                   q.rsi_14, q.sma_50, q.hist_vol_20
            FROM stock_signals s
            LEFT JOIN quant_signals q ON s.ticker = q.ticker
                AND q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = s.ticker)
            WHERE s.ticker IN ({placeholders})
        """
        cursor.execute(query, tickers)
        
        metadata = {}
        for row in cursor.fetchall():
            metadata[row['ticker']] = {
                'company_name': row['company_name'],
                'currency': row['currency'],
                'atr_stop_loss': row['atr_stop_loss'],
                'atr_last_updated': row['last_updated'],
                'beta': row['beta'],
                'ml_confidence_score': row['ml_confidence_score'],
                'var_95': row['var_95'],
                'cvar_95': row['cvar_95'],
                'sentiment_score': row['sentiment_score'],
                'rsi_14': row['rsi_14'],
                'sma_50': row['sma_50'],
                'hist_vol_20': row['hist_vol_20'],
            }
        conn.close()
        return metadata

    def log_notification_feed(self, msg_type: str, msg_text: str, conn: sqlite3.Connection, status: str = "sent") -> None:
        """Writes a row to the user-facing notification feed (display only).

        Fire-and-forget — has NO bearing on alert suppression. Deduplication is
        governed exclusively by the alert_state ledger via _evaluate_alert_gate() /
        record_alert_fired(). A slow or failed feed write can never cause an alert
        to re-fire, and vice versa.

        conn is the run()-scoped connection; this method does not open or close it.
        """
        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO system_notifications (message_type, message_text, status) "
                "VALUES (?, ?, ?)",
                (msg_type, msg_text, status)
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to write notification feed row for {msg_type}: {e}")

    @staticmethod
    def _condition_fingerprint(reason: str) -> str:
        """Derives a stable identifier for the class of condition that fired.

        Strips digits/punctuation and hashes the leading descriptive phrase so
        that fluctuating prices in the reason string don't change the fingerprint.
        Two alerts with the same fingerprint are the same ongoing event; a changed
        fingerprint means a genuinely different trigger.
        """
        if not reason:
            return "generic"
        tokens = re.findall(r"[A-Za-z]+", reason.upper())
        descriptor = " ".join(tokens[:6])
        # 6 tokens verified sufficient: every distinct trigger from CrashEngine /
        # MoonshotEngine diverges within the first 3–4 alphabetic words, so there
        # is no collision risk with the current reason-string vocabulary.
        return hashlib.sha1(descriptor.encode("utf-8"), usedforsecurity=False).hexdigest()[:16]

    def _dedup_settings(self, engine: str) -> Dict[str, float]:
        """Pulls the per-engine dedup knobs with safe fallbacks."""
        if engine == "Crash":
            key = "CRASH_ALERTS"
        elif engine == "Moonshot":
            key = "MOONSHOT_ALERTS"
        elif engine == "Anomaly":
            key = "ANOMALY_ALERTS"
        elif engine == "AIContagion":
            key = "AI_CONTAGION"
        else:  # Macro and any future engines
            key = "MACRO_ALERTS"
        block = self.config.get("NOTIFICATIONS", {}).get(key, {})
        return {
            "cooldown_minutes": float(block.get("COOLDOWN_MINUTES", 120.0)),
            "retrigger_percent": float(block.get("RETRIGGER_PERCENT", 2.0)),
            "rearm_percent": float(block.get("REARM_PERCENT", 3.0)),
        }

    def _evaluate_alert_gate(
        self,
        engine: str,
        ticker: str,
        current_price: Optional[float],
        reason: str,
        conn: sqlite3.Connection,
    ) -> bool:
        """Single, deterministic suppression decision. Returns True if the alert
        should be SUPPRESSED, False if it is cleared to fire.

        Logic (edge-triggered with hysteresis):
          1. No prior state today -> FIRE.
          2. Different condition fingerprint -> FIRE (new event class).
          3. Same condition, currently armed -> FIRE (first fire of this event).
          4. Same condition, suppressed:
               a. Price recovered >= REARM_PERCENT -> re-arm and SUPPRESS this scan.
               b. Cooldown elapsed AND price worsened >= RETRIGGER_PERCENT -> FIRE.
               c. Otherwise -> SUPPRESS.

        record_alert_fired() must be called by the dispatch loop only after a
        confirmed send, so a failed send leaves us armed and the alert retries.
        """
        settings = self._dedup_settings(engine)
        fingerprint = self._condition_fingerprint(reason)
        # state_date is always UTC so the daily rollover is consistent regardless
        # of the server's local timezone. Session bounds in run() use local time
        # deliberately (market hours are exchange-local); the disagreement window
        # is only between midnight UTC and midnight local, i.e. outside trading hours.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT fingerprint, last_price, last_fired_utc, armed, state_date "
                "FROM alert_state WHERE engine = ? AND ticker = ?",
                (engine, ticker),
            )
            row = cursor.fetchone()

            # Case 1: nothing on record for today.
            if row is None or row["state_date"] != today:
                return False  # fire

            # Case 2: a different condition class -> treat as a new event.
            if row["fingerprint"] != fingerprint:
                return False  # fire

            # Case 3: same condition and currently armed -> fire.
            if int(row["armed"]) == 1:
                return False  # fire

            # Case 4: same condition, suppressed. Need price to make decisions.
            last_price = row["last_price"]
            if current_price is None or last_price in (None, 0):
                return True  # can't compare; stay safe and suppress

            pct_change = (current_price - last_price) / last_price * 100.0
            if engine == "Crash":
                # Crash: price falling further is worsening; rising back is recovery.
                worsened_pct = -pct_change
                recovered_pct = pct_change
            elif engine == "Moonshot":
                # Moonshot: price rising further is worsening; falling back is recovery.
                worsened_pct = pct_change
                recovered_pct = -pct_change
            else:
                # Macro (yield surge): yield rising further is worsening; falling back is recovery.
                worsened_pct = pct_change
                recovered_pct = -pct_change

            # Case 4a: hysteresis re-arm. Event has materially reversed; re-arm so a
            # future breach is a fresh alert, but stay silent now.
            if recovered_pct >= settings["rearm_percent"]:
                cursor.execute(
                    "UPDATE alert_state SET armed = 1, state_date = ? WHERE engine = ? AND ticker = ?",
                    (today, engine, ticker),
                )
                conn.commit()
                return True  # suppress this scan; re-armed for next genuine breach

            # Case 4b: cooldown elapsed AND material deterioration.
            try:
                # Parse the stored UTC string and attach tzinfo so it can be
                # subtracted from the aware datetime.now(timezone.utc) without
                # raising TypeError (aware - naive is an error in Python).
                last_fired = datetime.strptime(
                    row["last_fired_utc"], "%Y-%m-%d %H:%M:%S"
                ).replace(tzinfo=timezone.utc)
            except (TypeError, ValueError):
                last_fired = None

            cooldown_ok = (
                last_fired is not None
                and (datetime.now(timezone.utc) - last_fired).total_seconds()
                >= settings["cooldown_minutes"] * 60.0
            )
            if cooldown_ok and worsened_pct >= settings["retrigger_percent"]:
                return False  # fire: enough time passed AND it materially worsened

            # Case 4c: still inside suppression window.
            return True

        except Exception as e:
            logger.error(f"Alert gate evaluation failed for {engine}/{ticker}: {e}")
            return True  # fail safe: suppress rather than risk spamming

    def record_alert_fired(
        self,
        engine: str,
        ticker: str,
        current_price: Optional[float],
        reason: str,
        conn: sqlite3.Connection,
    ) -> None:
        """Commits suppression state AFTER a successful dispatch.

        Sets armed=0 (now in cooldown), stamps the price/time/fingerprint, and
        bumps the daily fire counter. Idempotent via INSERT OR REPLACE on the
        (engine, ticker) primary key.

        conn is the run()-scoped connection; this method does not open or close it.
        """
        fingerprint = self._condition_fingerprint(reason)
        _now = datetime.now(timezone.utc)
        now_utc = _now.strftime("%Y-%m-%d %H:%M:%S")
        today = _now.strftime("%Y-%m-%d")

        try:
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO alert_state "
                "(engine, ticker, fingerprint, last_price, last_fired_utc, armed, fire_count, state_date) "
                "VALUES (?, ?, ?, ?, ?, 0, 1, ?) "
                "ON CONFLICT(engine, ticker) DO UPDATE SET "
                "  fingerprint=excluded.fingerprint, "
                "  last_price=excluded.last_price, "
                "  last_fired_utc=excluded.last_fired_utc, "
                "  armed=0, "
                "  fire_count=CASE WHEN alert_state.state_date=excluded.state_date "
                "             THEN alert_state.fire_count+1 ELSE 1 END, "
                "  state_date=excluded.state_date",
                (engine, ticker, fingerprint, current_price, now_utc, today),
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to record alert state for {engine}/{ticker}: {e}")

    @staticmethod
    def _seconds_since(ts: pd.Timestamp) -> float:
        """Returns elapsed seconds between ts and now, always in UTC.

        Uses .tzinfo (the correct scalar attribute) rather than .tz, which is a
        DatetimeIndex-level property and can raise AttributeError on a plain
        Timestamp in some pandas versions. Naive timestamps are treated as UTC
        rather than local time, which matches how yfinance writes its index after
        tz_localize(None) strips the zone — comparing a naive local now() against
        a stripped-UTC timestamp would be wrong by the UTC offset (up to ±1 h in
        the UK under BST).
        """
        now_utc = pd.Timestamp.now(tz="UTC")
        if ts.tzinfo is not None:
            return (now_utc - ts.tz_convert("UTC")).total_seconds()
        return (now_utc - ts.tz_localize("UTC")).total_seconds()

    def _prune_alert_state(self, conn: sqlite3.Connection) -> None:
        """Deletes alert_state rows older than 7 days to keep the table bounded.
        Active tickers reset daily via state_date comparison so they are unaffected;
        only rows for delisted or removed tickers accumulate and need clearing.

        conn is the run()-scoped connection; this method does not open or close it.
        """
        try:
            cursor = conn.cursor()
            cursor.execute("DELETE FROM alert_state WHERE state_date < date('now', '-7 days')")
            pruned = cursor.rowcount
            conn.commit()
            if pruned:
                logger.info("Pruned %d stale alert_state row(s) older than 7 days.", pruned)
        except Exception as e:
            logger.error(f"Failed to prune alert_state: {e}")

    def _has_corporate_action_today(self, ticker: str) -> bool:
        """
        Queries Yahoo Finance for dividends or stock splits occurring today.
        Used as a lazy circuit breaker to prevent false anomaly alerts.

        Results are memoized for the calendar day — corporate actions don't change
        intraday, so re-fetching on every 5-minute scan is pure wasted I/O.
        """
        today_date = datetime.now(timezone.utc).date()
        cache_key = (ticker, today_date.isoformat())
        if cache_key in self._corp_action_cache:
            return self._corp_action_cache[cache_key]

        result = False
        try:
            tk = yf.Ticker(ticker)
            actions = tk.actions
            if actions is not None and not actions.empty:
                if actions.index.tz is not None:
                    actions.index = actions.index.tz_localize(None)

                if today_date in set(actions.index.date.tolist()):
                    result = True
        except Exception:
            logger.error("Failed to check corporate actions for %s", ticker, exc_info=True)

        self._corp_action_cache[cache_key] = result
        return result

    def _dispatch_alerts(
        self,
        engine: str,
        alert_tuples: list,
        conn: sqlite3.Connection,
        msg_builder,   # (ticker, formatted_price, alert, ml_conf, var, sent, url) -> str
        feed_builder,  # (ticker, formatted_price, alert) -> str
    ) -> None:
        """Send, record, and feed-log each alert in alert_tuples for the given engine.

        Centralises the shared dispatch sequence (format → send → record → feed → sleep)
        so that per-engine divergences (emoji, cautions block, VaR label) live only in
        the msg_builder/feed_builder callables passed by the caller.
        """
        for ticker, alert, currency, meta in alert_tuples:
            formatted_price = format_currency(alert['price'], currency)
            url = build_stock_url(SERVER_URL, PORT, ticker)
            # ml_confidence_score is stored 0–100 (ai_prediction_engine multiplies prob by 100).
            # var_95 / cvar_95 are stored as fractions 0–1 (risk_engine: 1 - exp(log_return));
            # multiply by 100 here to render as a human-readable percentage.
            ml_conf = f"{meta.get('ml_confidence_score'):.1f}%" if meta.get('ml_confidence_score') is not None else "N/A"
            var = f"{(meta.get('var_95') * 100):.2f}%" if meta.get('var_95') is not None else "N/A"
            sent = f"{meta.get('sentiment_score'):.3f}" if meta.get('sentiment_score') is not None else "N/A"
            msg = msg_builder(ticker, formatted_price, alert, ml_conf, var, sent, url)
            try:
                ok = send_text_message(msg, self.config)
            except Exception as e:
                logger.error(f"{engine} alert dispatch failed for {ticker}: {e}")
                self.log_notification_feed(engine, feed_builder(ticker, formatted_price, alert), conn, status="failed")
                continue
            if not ok:
                logger.error("%s alert Nextcloud send returned False for %s — credentials missing or network error. Alert will retry next scan.", engine, ticker)
                self.log_notification_feed(engine, feed_builder(ticker, formatted_price, alert), conn, status="failed")
                continue
            self.record_alert_fired(engine, ticker, alert['price'], alert['reason'], conn)
            self.log_notification_feed(engine, feed_builder(ticker, formatted_price, alert), conn)
            time.sleep(_DISPATCH_SLEEP_SECONDS)

    def run(self) -> None:
        # One connection for the entire scan. Opened here (not in __init__) so it is
        # always on the correct thread when APScheduler dispatches to a worker.
        conn = get_connection()
        try:
            self._run(conn)
        finally:
            conn.close()

    def _run(self, conn: sqlite3.Connection) -> None:
        self._prune_alert_state(conn)
        logger.info("Scan initiated.")
        
        # Check active bounds
        sched_cfg = self.config.get("SCHEDULING", {}).get("CRASH_ALERTS", {})
        start_str = sched_cfg.get("START_TIME", "09:30")
        end_str = sched_cfg.get("END_TIME", "16:00")
        
        try:
            now = datetime.now(timezone.utc).time()
            start_time = datetime.strptime(start_str, "%H:%M").time()
            end_time = datetime.strptime(end_str, "%H:%M").time()
            if not (start_time <= now <= end_time):
                logger.info("Outside active bounds (%s-%s). Aborted.", start_str, end_str)
                return
        except (ValueError, TypeError) as e:
            logger.error("Invalid schedule time config (%s-%s): %s", start_str, end_str, e)
            return

        # Fraction of the trading session elapsed — used to project intraday cumulative
        # volume to a full-day estimate before passing it to the Moonshot volume check.
        # Without this, partial-day volume is compared to a 50-day full-day average,
        # producing a systematically understated ratio that flags nearly every morning
        # breakout as "low volume" regardless of actual participation.
        _to_min = lambda t: t.hour * 60 + t.minute
        _session_total = _to_min(end_time) - _to_min(start_time)
        _elapsed = max(0, _to_min(datetime.now(timezone.utc).time()) - _to_min(start_time))
        session_elapsed_frac = max(0.01, min(1.0, _elapsed / _session_total)) if _session_total > 0 else 1.0

        tickers = self.get_portfolio_tickers()
        ignored = self.config.get("IGNORED_TICKERS", [])
        
        # Filter out ignored list AND Mutual Funds
        tickers = [t for t in tickers if t not in ignored and not t.startswith('0P')]
        
        if not tickers:
            logger.warning("No valid portfolio items found for intraday scan.")
            return

        metadata = self.get_asset_metadata(tickers)
        
        # Add system yield benchmarks and SPY for macro shock detection
        # SPY is fetched here once so crash_engine never needs its own per-crash HTTP call
        macro_tickers = ["^TYX", "SPY"]
        spy_change_pct: Optional[float] = None
        download_list = sorted(set(tickers + macro_tickers))
        
        logger.info("Performing bulk YF 5m fetch for %d assets & macro benchmarks.", len(download_list))
        try:
            df_bulk = yf.download(download_list, period="1d", interval="5m", group_by='ticker', auto_adjust=True, progress=False)
        except Exception:
            logger.error("Bulk download failed.", exc_info=True)
            return

        if df_bulk.empty:
            logger.warning("Bulk download returned empty DataFrame.")
            return

        # --- MACRO FLASH SURGE DETECTION ---
        for m_ticker in macro_tickers:
            try:
                if isinstance(df_bulk.columns, pd.MultiIndex):
                    if m_ticker not in df_bulk.columns.get_level_values(0):
                        continue
                    m_df = df_bulk[m_ticker].copy()
                else:
                    if 'Close' not in df_bulk.columns:
                        continue
                    if len(download_list) == 1:
                        m_df = df_bulk.copy()
                    else:
                        logger.warning(
                            "Flat columns from bulk fetch with %d tickers requested; "
                            "cannot map macro ticker %s reliably. Skipping macro eval.",
                            len(download_list), m_ticker,
                        )
                        continue
                    
                m_df.dropna(subset=['Close'], inplace=True)
                if len(m_df) < 5: 
                    continue

                # --- STALENESS / MARKET CLOSED CIRCUIT BREAKER ---
                if self._seconds_since(m_df.index[-1]) > _STALE_SECONDS:
                    continue  # Bypass stale data
                
                m_open = float(m_df['Close'].iloc[0])
                m_curr = float(m_df['Close'].iloc[-1])
                
                if m_open > 0:
                    m_spike = ((m_curr - m_open) / m_open) * 100.0

                    if m_ticker == "SPY":
                        spy_change_pct = m_spike
                        continue

                    # If yield spikes more than 1.5% intraday, it's a systemic shock
                    reason_macro = f"YIELD SURGE {m_ticker}"
                    if m_spike >= _MACRO_YIELD_SURGE_PCT and not self._evaluate_alert_gate(
                        "Macro", m_ticker, m_curr, reason_macro, conn
                    ):
                        name = "US 30Y Treasury" if m_ticker == "^TYX" else "UK 10Y Gilt"
                        msg = (
                            f"🚨 **SYSTEMIC MACRO ALERT: {name} SURGING** 🚨\n\n"
                            f"**Current Yield:** {m_curr:.3f}%\n"
                            f"**Intraday Spike:** +{m_spike:.2f}%\n\n"
                            f"⚠️ The cost of capital is experiencing a violent intraday shock. "
                            f"Expect immediate severe valuation compression across high-multiple and tech equities. "
                            f"Risk-Off environment detected."
                        )
                        try:
                            ok = send_text_message(msg, self.config)
                        except Exception as e:
                            logger.error(f"Macro alert dispatch failed for {m_ticker}: {e}")
                            self.log_notification_feed("Macro", f"Systemic Yield Surge detected on {m_ticker} (+{m_spike:.2f}%)", conn, status="failed")
                            continue
                        if not ok:
                            logger.error("Macro alert Nextcloud send returned False for %s — credentials missing or network error.", m_ticker)
                            self.log_notification_feed("Macro", f"Systemic Yield Surge detected on {m_ticker} (+{m_spike:.2f}%)", conn, status="failed")
                            continue
                        self.record_alert_fired("Macro", m_ticker, m_curr, reason_macro, conn)
                        self.log_notification_feed(
                            "Macro",
                            f"Systemic Yield Surge detected on {m_ticker} (+{m_spike:.2f}%)",
                            conn,
                        )
            except Exception:
                logger.error("Macro eval failed for %s", m_ticker, exc_info=True)

        # --- PHASE 4: AI MACRO DEFENSE OVERRIDE ---
        # Dynamically tighten the crash threshold if the XGBoost AI model predicts an imminent > 2.0% SPY gap today.
        try:
            cursor = conn.cursor()
            cursor.execute('''
                SELECT MAX(ai_volatility_warning) as max_warning
                FROM macro_calendar
                WHERE date(event_date) = date('now')
                AND is_event_passed = 0
            ''')
            ai_warning_row = cursor.fetchone()

            if ai_warning_row and ai_warning_row['max_warning'] is not None and float(ai_warning_row['max_warning']) > _AI_DEFENSE_THRESHOLD:
                logger.info("AI Volatility Defense active: tightening flash crash threshold to %.1f%%.", _AI_DEFENSE_CAP)
                # Set a post-beta cap, NOT session_crash_threshold directly — mutating the base
                # threshold lets beta scaling widen it back out for high-beta names, defeating
                # the override. The cap is applied after beta multiplication inside evaluate().
                self.crash_engine.ai_threshold_cap = _AI_DEFENSE_CAP
            else:
                self.crash_engine.ai_threshold_cap = None
        except Exception:
            logger.error("Failed to query AI Macro Defense status.", exc_info=True)
            self.crash_engine.ai_threshold_cap = None

        # Inject pre-fetched SPY change so crash_engine never makes its own per-crash HTTP call
        self.crash_engine.spy_change_pct = spy_change_pct

        crash_alerts_to_send = []
        moonshot_alerts_to_send = []
        anomaly_alerts_to_send = []

        # Check correct config paths for enablement (SCHEDULING, not NOTIFICATIONS)
        crash_enabled = self.config.get("SCHEDULING", {}).get("CRASH_ALERTS", {}).get("ENABLED", False)
        moonshot_enabled = self.config.get("SCHEDULING", {}).get("MOONSHOT_ALERTS", {}).get("ENABLED", False)
        anomaly_enabled = self.config.get("NOTIFICATIONS", {}).get("ANOMALY_ALERTS", {}).get("ENABLED", False)

        for ticker in tickers:
            try:
                # Robust MultiIndex handling to prevent yfinance stripping bugs when mixing US/UK markets
                if isinstance(df_bulk.columns, pd.MultiIndex):
                    if ticker not in df_bulk.columns.get_level_values(0):
                        continue
                    df_intraday = df_bulk[ticker].copy()
                else:
                    if 'Close' not in df_bulk.columns:
                        continue
                    df_intraday = df_bulk.copy()

                if 'Close' in df_intraday.columns:
                    df_intraday.dropna(subset=['Close'], inplace=True)
                if len(df_intraday) < 2:
                    continue

                # --- STALENESS / MARKET CLOSED CIRCUIT BREAKER ---
                # Completely bypass evaluation if restarting the app over the weekend/holiday
                if self._seconds_since(df_intraday.index[-1]) > _STALE_SECONDS:
                    # Market is closed, or asset is halted. Save the parquet but skip alert evaluation.
                    df_intraday.index = df_intraday.index.tz_localize(None)
                    df_intraday.to_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet", engine='pyarrow')
                    continue

                # Strip timezone before any index arithmetic; parquet write is deferred
                # until after history validation so a short/corrupt intraday frame cannot
                # overwrite a good dashboard parquet.
                df_intraday.index = df_intraday.index.tz_localize(None)

                current_price = float(df_intraday['Close'].iloc[-1])
                session_open = float(df_intraday['Open'].iloc[0]) if 'Open' in df_intraday.columns else None

                # Load Historical Data for stitching and math
                hist_path = HISTORICAL_DIR / f"{ticker}.parquet"
                if not hist_path.exists():
                    continue

                df_hist = pd.read_parquet(hist_path)
                # Drop trailing NaN closes before any length or value check.
                # A NaN last-close passes the > 0 corp-action gate (NaN > 0 is False,
                # so the lookup is skipped) but propagates into df_combined, where it
                # becomes prev_close in the crash engine and poisons every pct comparison
                # with NaN — the crash is then silently missed because NaN <= threshold
                # evaluates to False rather than raising.
                df_hist = df_hist[df_hist['Close'].notna()]
                if df_hist.empty or len(df_hist) < 20:
                    continue

                # Save Intraday Parquet for the Web Dashboard — written here, after history
                # validation, so only frames with sufficient context reach the dashboard.
                df_intraday.to_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet", engine='pyarrow')

                # --- PRE-FLIGHT ANOMALY CHECK (CORPORATE ACTION CIRCUIT BREAKER) ---
                # Compare the live price against the last known historical close.
                # If there's a massive gap (>10%), lazily check for a split/dividend to avoid false alerts.
                last_hist_close = df_hist['Close'].iloc[-1]
                if last_hist_close > 0:
                    raw_gap_pct = abs((current_price - last_hist_close) / last_hist_close) * 100.0
                    if raw_gap_pct > _CORP_ACTION_GAP_PCT:
                        if self._has_corporate_action_today(ticker):
                            logger.info("Corporate action detected for %s; suppressing alert evaluation.", ticker)
                            continue

                # Strict Time-Series Stitching Normalization
                latest_dt = df_intraday.index[-1]
                latest_date_only = latest_dt.normalize()
                hist_last_date_only = df_hist.index[-1].normalize()
                
                if hist_last_date_only == latest_date_only:
                    df_combined = df_hist[['Close']].copy()
                    df_combined.loc[df_hist.index[-1], 'Close'] = current_price
                else:
                    new_row = pd.DataFrame({'Close': [current_price]}, index=[latest_dt])
                    df_combined = pd.concat([df_hist[['Close']], new_row])
                    df_combined.sort_index(inplace=True)

                asset_meta = metadata.get(ticker, {})
                currency = asset_meta.get('currency', 'USD')
                
                # --- EVALUATE CRASH ENGINE ---
                # evaluate() runs first so we have the reason string for fingerprinting.
                # The gate check is cheap; evaluate() returns None when no condition is met.
                if crash_enabled:
                    crash_alert = self.crash_engine.evaluate(
                        ticker, current_price, df_combined, asset_meta, df_hist, session_open
                    )
                    if crash_alert and not self._evaluate_alert_gate(
                        "Crash", ticker, current_price, crash_alert.get("reason", ""), conn
                    ):
                        crash_alerts_to_send.append((ticker, crash_alert, currency, asset_meta))

                # --- EVALUATE MOONSHOT ENGINE ---
                if moonshot_enabled:
                    if 'Volume' in df_intraday.columns:
                        # Project cumulative intraday volume to a full-day equivalent so
                        # it is comparable to the 50-day daily average in the engine.
                        projected_volume = float(df_intraday['Volume'].sum()) / session_elapsed_frac
                    else:
                        projected_volume = None
                    moonshot_alert = self.moonshot_engine.evaluate(
                        ticker, current_price, df_combined, asset_meta, df_hist, projected_volume
                    )
                    if moonshot_alert and not self._evaluate_alert_gate(
                        "Moonshot", ticker, current_price, moonshot_alert.get("reason", ""), conn
                    ):
                        moonshot_alerts_to_send.append((ticker, moonshot_alert, currency, asset_meta))

                # --- EVALUATE ANOMALY ENGINE ---
                if anomaly_enabled and 'Volume' in df_intraday.columns:
                    try:
                        vol_ma20 = df_hist['Volume'].tail(20).mean()
                        if vol_ma20 > 0:
                            prev_close_hist = float(df_hist['Close'].iloc[-1])
                            sma_50 = asset_meta.get('sma_50') or current_price
                            feature_vector = [
                                float(df_intraday['Volume'].sum()) / vol_ma20,
                                float(asset_meta.get('rsi_14') or 50.0),
                                ((current_price - prev_close_hist) / prev_close_hist) * 100,
                                ((current_price - sma_50) / sma_50) * 100,
                                float(asset_meta.get('hist_vol_20') or 0.2),
                                clamp_beta(asset_meta.get('beta')),
                            ]
                            anomaly_result = self.anomaly_engine.score(ticker, feature_vector)
                            anomaly_score = anomaly_result['score'] if anomaly_result is not None else None
                            if anomaly_score is not None:
                                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                                try:
                                    conn.execute(
                                        "UPDATE quant_signals SET anomaly_score = ? "
                                        "WHERE ticker = ? AND date = ?",
                                        (anomaly_score, ticker, today_str),
                                    )
                                    conn.commit()
                                except Exception as db_err:
                                    logger.warning("anomaly_score DB write failed for %s: %s", ticker, db_err)

                                threshold = float(
                                    self.config.get("NOTIFICATIONS", {})
                                    .get("ANOMALY_ALERTS", {})
                                    .get("THRESHOLD", 0.7)
                                )
                                if anomaly_score > threshold:
                                    # Corroborate any crash alert that also fired this scan
                                    corroborated = False
                                    for i, (t, alert, *_) in enumerate(crash_alerts_to_send):
                                        if t == ticker:
                                            crash_alerts_to_send[i][1]['reason'] += (
                                                f"\n🤖 *Anomaly Score: {anomaly_score:.2f}* (Isolation Forest)"
                                            )
                                            corroborated = True
                                            break
                                    if not corroborated:
                                        anomaly_alert = {
                                            'price': current_price,
                                            'reason': (
                                                f"Isolation Forest score {anomaly_score:.2f} "
                                                f"exceeds threshold {threshold:.2f}"
                                            ),
                                            'anomaly_score': anomaly_score,
                                            'threshold': threshold,
                                        }
                                        if not self._evaluate_alert_gate(
                                            "Anomaly", ticker, current_price,
                                            anomaly_alert['reason'], conn,
                                        ):
                                            anomaly_alerts_to_send.append(
                                                (ticker, anomaly_alert, currency, asset_meta)
                                            )
                    except Exception:
                        logger.error("Anomaly evaluation failed for %s", ticker, exc_info=True)

            except Exception:
                logger.error("Error processing %s", ticker, exc_info=True)

        # --- BATCH DISPATCH ALERTS (One Message Per Ticker) ---
        self._dispatch_alerts(
            "Crash",
            crash_alerts_to_send,
            conn,
            lambda t, p, a, ml, v, s, u: (
                f"🚨 **INTRADAY CRASH ALERT: {t}** 🚨\n\n"
                f"**Price:** {p}\n"
                f"**Trigger:** {a['reason']}\n\n"
                f"📊 **Context:**\n"
                f"• AI Confidence: {ml}\n"
                f"• Downside Log-Return VaR: {v}\n"
                f"• NLP Sentiment: {s}\n\n"
                f"🔗 [View Breakdown]({u})"
            ),
            lambda t, p, a: (
                f"**Price:** {p} | Intraday Alert triggered for {t}. Reason: {a['reason']}"
            ),
        )
        self._dispatch_alerts(
            "Moonshot",
            moonshot_alerts_to_send,
            conn,
            lambda t, p, a, ml, v, s, u: (
                f"🚀 **MOONSHOT ALERT: {t}** 🚀\n\n"
                f"**Price:** {p}\n"
                f"**Trigger:** {a['reason']}\n\n"
                + "".join(f"⚠️ *{c}*\n" for c in a.get('cautions', []))
                + f"\n📊 **Context:**\n"
                f"• AI Confidence: {ml}\n"
                f"• Value at Risk (95%): {v}\n"
                f"• NLP Sentiment: {s}\n\n"
                f"🔗 [View Breakdown]({u})"
            ),
            lambda t, p, a: (
                f"**Price:** {p} | Moonshot triggered for {t}. Reason: {a['reason']}"
            ),
        )
        self._dispatch_alerts(
            "Anomaly",
            anomaly_alerts_to_send,
            conn,
            lambda t, p, a, ml, v, s, u: (
                f"⚠️ **ANOMALY ALERT: {t}** ⚠️\n\n"
                f"**Price:** {p}\n"
                f"**Anomaly Score:** {a.get('anomaly_score', 0):.2f} / 1.00 "
                f"(Threshold: {a.get('threshold', 0.7):.2f})\n"
                f"**Trigger:** Isolation Forest detected a multi-dimensional statistical outlier.\n\n"
                f"📊 **Context:**\n"
                f"• AI Confidence: {ml}\n"
                f"• Downside Log-Return VaR: {v}\n"
                f"• NLP Sentiment: {s}\n\n"
                f"🔗 [View Breakdown]({u})"
            ),
            lambda t, p, a: (
                f"Anomaly Score: {a.get('anomaly_score', 0):.2f} detected for {t} at {p}"
            ),
        )

        logger.info(
            "Scan complete. Dispatched %d crashes, %d moonshots, %d anomalies.",
            len(crash_alerts_to_send), len(moonshot_alerts_to_send), len(anomaly_alerts_to_send),
        )

if __name__ == "__main__":
    try:
        engine = IntradayOrchestrator()
        engine.run()
    except Exception:
        logger.critical("Orchestrator failed to start.", exc_info=True)
        raise