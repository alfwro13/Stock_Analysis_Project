# intraday_orchestrator.py
import hashlib
import os
import re
import time
import json
import logging
from typing import Dict, Optional
import yfinance as yf
import pandas as pd
from datetime import datetime, timezone
from config import load_config, PORTFOLIO_PATH, INTRADAY_DIR, HISTORICAL_DIR, PORT, SERVER_URL
from utils import normalize_ticker
from database import get_connection
from crash_engine import CrashEngine
from moonshot_engine import MoonshotEngine
from nextcloud_talk import send_text_message

logger = logging.getLogger(__name__)

def format_currency(price, currency_code):
    """
    Formats price with correct symbol, scaling GBp to GBP dynamically.
    Fails over to the 3-letter currency code if symbol mapping is unavailable.
    """
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

def build_stock_url(server_url, port, ticker):
    """
    Intelligently constructs the URL. If the server is a domain/proxy, it drops the port.
    If it's an IP address or localhost, it appends the port automatically.
    """
    base = str(server_url).rstrip('/')
    # Regex to check if the base URL is localhost or an IPv4 address
    is_ip_or_local = bool(re.search(r'localhost|\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', base))
    # Check if a port is already explicitly defined in the base URL string
    has_port = bool(re.search(r':\d+$', base))
    
    if is_ip_or_local and not has_port:
        return f"{base}:{port}/stock/{ticker}"
    return f"{base}/stock/{ticker}"


class IntradayOrchestrator:
    """
    Centralized monitor that batches network and disk I/O operations, 
    feeding raw context into the mathematical quant engines.
    """
    def __init__(self):
        self.config = load_config()
        # Instantiate engines with config so they don't reload it internally
        self.crash_engine = CrashEngine(self.config)
        self.moonshot_engine = MoonshotEngine(self.config)

    def get_portfolio_tickers(self):
        """Safely extracts all unique tickers currently held in the portfolio."""
        if not os.path.exists(PORTFOLIO_PATH):
            return []
        try:
            with open(PORTFOLIO_PATH, 'r') as f:
                data = json.load(f)
                return [normalize_ticker(v['ticker']) for v in data.values() if 'ticker' in v]
        except Exception:
            return []

    def get_asset_metadata(self, tickers):
        """Fetches currency, ATR stop loss, company name, ML, and Risk metrics in a single bulk SQLite query."""
        if not tickers:
            return {}
            
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ','.join('?' for _ in tickers)
        
        query = f"""
            SELECT s.ticker, s.company_name, s.currency, s.atr_stop_loss, s.last_updated, s.beta,
                   q.ml_confidence_score, q.var_95, q.cvar_95, q.sentiment_score
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
                'sentiment_score': row['sentiment_score']
            }
        conn.close()
        return metadata

    def log_notification_feed(self, msg_type: str, msg_text: str) -> None:
        """Writes a row to the user-facing notification feed (display only).

        Fire-and-forget — has NO bearing on alert suppression. Deduplication is
        governed exclusively by the alert_state ledger via _evaluate_alert_gate() /
        record_alert_fired(). A slow or failed feed write can never cause an alert
        to re-fire, and vice versa.
        """
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute(
                "INSERT INTO system_notifications (message_type, message_text, status) "
                "VALUES (?, ?, 'sent')",
                (msg_type, msg_text)
            )
            conn.commit()
        except Exception as e:
            logger.error(f"Failed to write notification feed row for {msg_type}: {e}")
        finally:
            if conn:
                conn.close()

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
        return hashlib.sha1(descriptor.encode("utf-8")).hexdigest()[:16]

    def _dedup_settings(self, engine: str) -> Dict[str, float]:
        """Pulls the per-engine dedup knobs with safe fallbacks."""
        if engine == "Crash":
            key = "CRASH_ALERTS"
        elif engine == "Moonshot":
            key = "MOONSHOT_ALERTS"
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

        conn = None
        try:
            conn = get_connection()
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
                    "UPDATE alert_state SET armed = 1 WHERE engine = ? AND ticker = ?",
                    (engine, ticker),
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
        finally:
            if conn:
                conn.close()

    def record_alert_fired(
        self,
        engine: str,
        ticker: str,
        current_price: Optional[float],
        reason: str,
    ) -> None:
        """Commits suppression state AFTER a successful dispatch.

        Sets armed=0 (now in cooldown), stamps the price/time/fingerprint, and
        bumps the daily fire counter. Idempotent via INSERT OR REPLACE on the
        (engine, ticker) primary key.
        """
        fingerprint = self._condition_fingerprint(reason)
        _now = datetime.now(timezone.utc)
        now_utc = _now.strftime("%Y-%m-%d %H:%M:%S")
        today = _now.strftime("%Y-%m-%d")

        conn = None
        try:
            conn = get_connection()
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
        finally:
            if conn:
                conn.close()

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

    def _prune_alert_state(self) -> None:
        """Deletes alert_state rows older than 7 days to keep the table bounded.
        Active tickers reset daily via state_date comparison so they are unaffected;
        only rows for delisted or removed tickers accumulate and need clearing."""
        conn = None
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute("DELETE FROM alert_state WHERE state_date < date('now', '-7 days')")
            pruned = cursor.rowcount
            conn.commit()
            if pruned:
                print(f"[ORCHESTRATOR] Pruned {pruned} stale alert_state row(s) older than 7 days.")
        except Exception as e:
            logger.error(f"Failed to prune alert_state: {e}")
        finally:
            if conn:
                conn.close()

    def _has_corporate_action_today(self, ticker: str) -> bool:
        """
        Queries Yahoo Finance for dividends or stock splits occurring today.
        Used as a lazy circuit breaker to prevent false anomaly alerts.
        """
        try:
            tk = yf.Ticker(ticker)
            actions = tk.actions
            if actions is not None and not actions.empty:
                if actions.index.tz is not None:
                    actions.index = actions.index.tz_localize(None)
                
                today_date = datetime.now().date()
                if today_date in actions.index.date:
                    return True
        except Exception as e:
            print(f"[ORCHESTRATOR] Failed to check corporate actions for {ticker}: {e}")
        return False

    def run(self):
        self._prune_alert_state()
        print(f"\n--- [INTRADAY ORCHESTRATOR] Scan Initiated @ {datetime.now().strftime('%H:%M:%S')} ---")
        
        # Check active bounds
        sched_cfg = self.config.get("SCHEDULING", {}).get("CRASH_ALERTS", {})
        start_str = sched_cfg.get("START_TIME", "09:30")
        end_str = sched_cfg.get("END_TIME", "16:00")
        
        try:
            now = datetime.now().time()
            start_time = datetime.strptime(start_str, "%H:%M").time()
            end_time = datetime.strptime(end_str, "%H:%M").time()
            if not (start_time <= now <= end_time):
                print(f"[ORCHESTRATOR] Outside active bounds ({start_str}-{end_str}). Aborted.")
                return
        except Exception:
            pass

        tickers = self.get_portfolio_tickers()
        ignored = self.config.get("IGNORED_TICKERS", [])
        
        # Filter out ignored list AND Mutual Funds
        tickers = [t for t in tickers if t not in ignored and not t.startswith('0P')]
        
        if not tickers:
            print("[ORCHESTRATOR] No valid portfolio items found for intraday scan.")
            return

        metadata = self.get_asset_metadata(tickers)
        
        # Add system yield benchmarks and SPY for macro shock detection
        # SPY is fetched here once so crash_engine never needs its own per-crash HTTP call
        macro_tickers = ["^TYX", "SPY"]
        spy_change_pct: float | None = None
        download_list = list(set(tickers + macro_tickers))
        
        print(f"[ORCHESTRATOR] Performing bulk YF 5m fetch for {len(download_list)} assets & macro benchmarks...")
        try:
            df_bulk = yf.download(download_list, period="1d", interval="5m", group_by='ticker', auto_adjust=True, progress=False)
        except Exception as e:
            print(f"[ORCHESTRATOR] Bulk download failed: {e}")
            return

        if df_bulk.empty:
            print("[ORCHESTRATOR] Bulk download returned empty DataFrame.")
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
                    m_df = df_bulk.copy() if len(download_list) == 1 else pd.DataFrame()
                    
                m_df.dropna(subset=['Close'], inplace=True)
                if len(m_df) < 5: 
                    continue

                # --- STALENESS / MARKET CLOSED CIRCUIT BREAKER ---
                # If the last tick is older than 90 mins (5400s), the market is closed or halted.
                if self._seconds_since(m_df.index[-1]) > 5400:
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
                    if m_spike >= 1.5 and not self._evaluate_alert_gate(
                        "Macro", m_ticker, m_curr, reason_macro
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
                            send_text_message(msg, self.config)
                        except Exception as e:
                            logger.error(f"Macro alert dispatch failed for {m_ticker}: {e}")
                            continue
                        self.record_alert_fired("Macro", m_ticker, m_curr, reason_macro)
                        self.log_notification_feed(
                            "Macro",
                            f"Systemic Yield Surge detected on {m_ticker} (+{m_spike:.2f}%)"
                        )
            except Exception as e:
                print(f"[ORCHESTRATOR] Macro eval failed for {m_ticker}: {e}")

        # --- PHASE 4: AI MACRO DEFENSE OVERRIDE ---
        # Dynamically tighten the crash threshold if the XGBoost AI model predicts an imminent > 2.0% SPY gap today.
        try:
            conn = get_connection()
            cursor = conn.cursor()
            cursor.execute('''
                SELECT MAX(ai_volatility_warning) as max_warning 
                FROM macro_calendar 
                WHERE date(event_date) = date('now') 
                AND is_event_passed = 0
            ''')
            ai_warning_row = cursor.fetchone()
            conn.close()

            if ai_warning_row and ai_warning_row['max_warning'] and float(ai_warning_row['max_warning']) > 2.0:
                print(f"[ORCHESTRATOR] 🛡️ AI Volatility Defense Active: Tightening Flash Crash Threshold to 1.5%")
                # Set a post-beta cap, NOT session_crash_threshold directly — mutating the base
                # threshold lets beta scaling widen it back out for high-beta names, defeating
                # the override. The cap is applied after beta multiplication inside evaluate().
                self.crash_engine.ai_threshold_cap = 1.5
            else:
                self.crash_engine.ai_threshold_cap = None
        except Exception as e:
            print(f"[ORCHESTRATOR] Failed to query AI Macro Defense status: {e}")
            self.crash_engine.ai_threshold_cap = None

        # Inject pre-fetched SPY change so crash_engine never makes its own per-crash HTTP call
        self.crash_engine.spy_change_pct = spy_change_pct

        crash_alerts_to_send = []
        moonshot_alerts_to_send = []
        
        # Check correct config paths for enablement (SCHEDULING, not NOTIFICATIONS)
        crash_enabled = self.config.get("SCHEDULING", {}).get("CRASH_ALERTS", {}).get("ENABLED", False)
        moonshot_enabled = self.config.get("SCHEDULING", {}).get("MOONSHOT_ALERTS", {}).get("ENABLED", False)

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

                df_intraday.dropna(subset=['Close'], inplace=True)
                if df_intraday.empty:
                    continue

                # --- STALENESS / MARKET CLOSED CIRCUIT BREAKER ---
                # Completely bypass evaluation if restarting the app over the weekend/holiday
                if self._seconds_since(df_intraday.index[-1]) > 5400:  # 90 minutes
                    # Market is closed, or asset is halted. Save the parquet but skip alert evaluation.
                    df_intraday.index = df_intraday.index.tz_localize(None)
                    df_intraday.to_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet", engine='pyarrow')
                    continue

                # Save Intraday Parquet for the Web Dashboard to keep it live
                df_intraday.index = df_intraday.index.tz_localize(None)
                df_intraday.to_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet", engine='pyarrow')
                
                current_price = float(df_intraday['Close'].iloc[-1])
                session_open = float(df_intraday['Open'].iloc[0]) if 'Open' in df_intraday.columns else None
                
                # Load Historical Data for stitching and math
                hist_path = HISTORICAL_DIR / f"{ticker}.parquet"
                if not hist_path.exists():
                    continue
                    
                df_hist = pd.read_parquet(hist_path)
                if df_hist.empty or len(df_hist) < 20:
                    continue
                    
                # --- PRE-FLIGHT ANOMALY CHECK (CORPORATE ACTION CIRCUIT BREAKER) ---
                # Compare the live price against the last known historical close.
                # If there's a massive gap (>10%), lazily check for a split/dividend to avoid false alerts.
                last_hist_close = df_hist['Close'].iloc[-1]
                if last_hist_close > 0:
                    raw_gap_pct = abs((current_price - last_hist_close) / last_hist_close) * 100.0
                    if raw_gap_pct > 10.0:
                        if self._has_corporate_action_today(ticker):
                            print(f"[ORCHESTRATOR] 🛑 Corporate action detected for {ticker}. Suppressing execution to prevent false signals.")
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
                        "Crash", ticker, current_price, crash_alert.get("reason", "")
                    ):
                        crash_alerts_to_send.append((ticker, crash_alert, currency, asset_meta))

                # --- EVALUATE MOONSHOT ENGINE ---
                if moonshot_enabled:
                    current_volume = (
                        float(df_intraday['Volume'].sum())
                        if 'Volume' in df_intraday.columns else None
                    )
                    moonshot_alert = self.moonshot_engine.evaluate(
                        ticker, current_price, df_combined, asset_meta, df_hist, current_volume
                    )
                    if moonshot_alert and not self._evaluate_alert_gate(
                        "Moonshot", ticker, current_price, moonshot_alert.get("reason", "")
                    ):
                        moonshot_alerts_to_send.append((ticker, moonshot_alert, currency, asset_meta))

            except Exception as e:
                print(f"[ORCHESTRATOR] Error processing {ticker}: {e}")

        # --- BATCH DISPATCH ALERTS (One Message Per Ticker) ---
        for ticker, alert, currency, meta in crash_alerts_to_send:
            formatted_price = format_currency(alert['price'], currency)
            url = build_stock_url(SERVER_URL, PORT, ticker)
            
            ml_conf = f"{meta.get('ml_confidence_score'):.1f}%" if meta.get('ml_confidence_score') is not None else "N/A"
            var = f"{(meta.get('var_95') * 100):.2f}%" if meta.get('var_95') is not None else "N/A"
            sent = f"{meta.get('sentiment_score'):.3f}" if meta.get('sentiment_score') is not None else "N/A"

            msg = (
                f"🚨 **INTRADAY CRASH ALERT: {ticker}** 🚨\n\n"
                f"**Price:** {formatted_price}\n"
                f"**Trigger:** {alert['reason']}\n\n"
                f"📊 **Context:**\n"
                f"• AI Confidence: {ml_conf}\n"
                f"• Downside Log-Return VaR: {var}\n"
                f"• NLP Sentiment: {sent}\n\n"
                f"🔗 [View Breakdown]({url})"
            )
            try:
                send_text_message(msg, self.config)
            except Exception as e:
                logger.error(f"Crash alert dispatch failed for {ticker}: {e}")
                continue
            self.record_alert_fired("Crash", ticker, alert['price'], alert['reason'])
            self.log_notification_feed(
                "Crash",
                f"**Price:** {formatted_price} | Intraday Alert triggered for {ticker}. "
                f"Reason: {alert['reason']}"
            )
            time.sleep(1)  # Prevent Nextcloud rate-limiting

        for ticker, alert, currency, meta in moonshot_alerts_to_send:
            formatted_price = format_currency(alert['price'], currency)
            url = build_stock_url(SERVER_URL, PORT, ticker)
            
            ml_conf = f"{meta.get('ml_confidence_score'):.1f}%" if meta.get('ml_confidence_score') is not None else "N/A"
            var = f"{(meta.get('var_95') * 100):.2f}%" if meta.get('var_95') is not None else "N/A"
            sent = f"{meta.get('sentiment_score'):.3f}" if meta.get('sentiment_score') is not None else "N/A"

            cautions = ""
            for caution in alert.get('cautions', []):
                cautions += f"⚠️ *{caution}*\n"

            msg = (
                f"🚀 **MOONSHOT ALERT: {ticker}** 🚀\n\n"
                f"**Price:** {formatted_price}\n"
                f"**Trigger:** {alert['reason']}\n\n"
                f"{cautions}\n"
                f"📊 **Context:**\n"
                f"• AI Confidence: {ml_conf}\n"
                f"• Value at Risk (95%): {var}\n"
                f"• NLP Sentiment: {sent}\n\n"
                f"🔗 [View Breakdown]({url})"
            )
            try:
                send_text_message(msg, self.config)
            except Exception as e:
                logger.error(f"Moonshot alert dispatch failed for {ticker}: {e}")
                continue
            self.record_alert_fired("Moonshot", ticker, alert['price'], alert['reason'])
            self.log_notification_feed(
                "Moonshot",
                f"**Price:** {formatted_price} | Moonshot triggered for {ticker}. "
                f"Reason: {alert['reason']}"
            )
            time.sleep(1)  # Prevent Nextcloud rate-limiting

        print(f"[ORCHESTRATOR] Scan complete. Dispatched {len(crash_alerts_to_send)} crashes and {len(moonshot_alerts_to_send)} moonshots.")

if __name__ == "__main__":
    engine = IntradayOrchestrator()
    engine.run()