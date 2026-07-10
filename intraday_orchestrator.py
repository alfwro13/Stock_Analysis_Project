import hashlib
import ipaddress
import re
import time
import logging
import sqlite3
from typing import Any, Dict, List, Optional
from urllib.parse import urlparse
import pandas as pd
from datetime import datetime, timezone
from config import load_config, INTRADAY_DIR, HISTORICAL_DIR, PORT, SERVER_URL
from yahoo_engine import yahoo_engine
import time_engine
from utils import normalize_ticker, is_daily_bar_still_forming
from database import get_connection, get_mutual_fund_tickers
import accounts_engine
from db_accounts import get_all_holding_price_limits, get_accounts
from crash_engine import CrashEngine
from moonshot_engine import MoonshotEngine
from anomaly_engine import AnomalyEngine
from notification_engine import notify
from market_pulse import upsert_live_price
from utils import clamp_beta

logger = logging.getLogger(__name__)

# GUI name: "Crash & Moonshot Alerts". Canonical scheduled-job names live in scheduler_engine.JOB_GRAPH.
_ALERT_SOURCES = {"Crash": "crash_alert", "Moonshot": "moonshot_alert", "Anomaly": "anomaly_alert", "Macro": "macro_yield_alert", "HoldingLimit": "holding_limit_alert"}

_STALE_SECONDS        = 5400   # 90 min: market closed / asset halted circuit breaker
_CORP_ACTION_GAP_PCT  = 10.0   # price gap % that triggers a corporate action lookup
_MACRO_YIELD_SURGE_PCT = 1.5   # intraday yield spike % that fires a systemic macro alert
_AI_DEFENSE_THRESHOLD = 2.0    # predicted SPY gap % that activates AI Volatility Defense
_AI_DEFENSE_CAP       = 1.5    # flash-crash threshold cap (%) applied when defense is active
# N alerts × 1 s blocks the APScheduler thread and holds the DB connection; consider a digest for large portfolios.
_DISPATCH_SLEEP_SECONDS = 1


def format_currency(price: float, currency_code: Optional[str]) -> str:
    """Format price with currency symbol; scales GBp→GBP (/100); falls back to the 3-letter code."""
    if price is None:
        return "N/A"
    if not currency_code:
        currency_code = 'USD'
        
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
    """Build stock detail URL; appends port only for IP/localhost, drops it for domain/proxy."""
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
    """Batches network/disk I/O for the 5-minute intraday scan and feeds context to the quant engines."""
    def __init__(self) -> None:
        self.config = load_config()
        # Instantiate engines with config so they don't reload it internally
        self.crash_engine = CrashEngine(self.config)
        self.moonshot_engine = MoonshotEngine(self.config)
        self.anomaly_engine = AnomalyEngine()
        # Keyed (ticker, date-str) so each ticker is fetched at most once per calendar day.
        self._corp_action_cache: Dict[tuple, bool] = {}

    def get_portfolio_tickers(self) -> List[str]:
        from accounts_engine import get_combined_holdings
        from treasury_bill_engine import parse_tbill_buy_txn_id
        try:
            return [
                normalize_ticker(t) for t in get_combined_holdings().keys()
                if parse_tbill_buy_txn_id(t) is None
            ]
        except Exception:
            logger.error("Failed to load portfolio tickers from accounts engine", exc_info=True)
            return []

    def get_asset_metadata(self, tickers: List[str]) -> Dict[str, Dict[str, Any]]:
        """Fetches currency, ATR stop loss, company name, ML, and Risk metrics in a single bulk SQLite query."""
        if not tickers:
            return {}

        conn = None
        try:
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
            return metadata
        finally:
            if conn:
                conn.close()

    def log_notification_feed(self, msg_type: str, msg_text: str, conn: sqlite3.Connection, status: str = "sent") -> None:
        """Display-only feed write; no effect on alert suppression; conn is caller-scoped."""
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
        """Hashes the first 6 alphabetic tokens of reason so price fluctuations don't create spurious new fingerprints."""
        if not reason:
            return "generic"
        tokens = re.findall(r"[A-Za-z]+", reason.upper())
        descriptor = " ".join(tokens[:6])
        # 6 tokens: every distinct Crash/Moonshot trigger diverges within 3–4 words; no collision risk with current vocabulary.
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
        elif engine == "MarketStress":
            key = "MARKET_STRESS_ALERTS"
        elif engine == "TrapMonitor":
            key = "TRAP_MONITOR_ALERTS"
        elif engine == "HoldingLimit":
            key = "HOLDING_LIMIT_ALERTS"
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
        """Returns True (suppress) or False (fire): no-prior-state/new-fingerprint/armed→fire; recovery re-arms; cooldown+worsening retriggers."""
        settings = self._dedup_settings(engine)
        fingerprint = self._condition_fingerprint(reason)
        # state_date UTC so rollover is consistent; disagreement with exchange-local session bounds is only outside trading hours.
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

        try:
            cursor = conn.cursor()
            cursor.execute(
                "SELECT fingerprint, last_price, last_fired_utc, armed, state_date "
                "FROM alert_state WHERE engine = ? AND ticker = ?",
                (engine, ticker),
            )
            row = cursor.fetchone()

            # Case 1: never fired before for this ticker/engine.
            if row is None:
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

            if engine == "TrapMonitor":
                # current_price carries ema_distance (already a signed %) here, not a price —
                # compare the raw point delta rather than a relative pct-of-pct.
                worsened_pct = last_price - current_price
                recovered_pct = current_price - last_price
            else:
                pct_change = (current_price - last_price) / last_price * 100.0
                if engine == "Crash":
                    # Crash: price falling further is worsening; rising back is recovery.
                    worsened_pct = -pct_change
                    recovered_pct = pct_change
                elif engine == "Moonshot":
                    # Moonshot: price rising further is worsening; falling back is recovery.
                    worsened_pct = pct_change
                    recovered_pct = -pct_change
                elif engine == "HoldingLimit":
                    # ticker carries a composite "{account_id}:{ticker}:low"/":high" key — direction
                    # is encoded in the suffix since the same real ticker can breach either side.
                    if ticker.endswith(":low"):
                        worsened_pct = -pct_change
                        recovered_pct = pct_change
                    else:
                        worsened_pct = pct_change
                        recovered_pct = -pct_change
                else:
                    # Macro (yield surge): yield rising further is worsening; falling back is recovery.
                    worsened_pct = pct_change
                    recovered_pct = -pct_change

            # Case 4a: event materially reversed — re-arm for a future breach, stay silent now.
            if recovered_pct >= settings["rearm_percent"]:
                cursor.execute(
                    "UPDATE alert_state SET armed = 1, state_date = ? WHERE engine = ? AND ticker = ?",
                    (today, engine, ticker),
                )
                conn.commit()
                return True  # suppress this scan; re-armed for next genuine breach

            # Case 4b: cooldown elapsed AND material deterioration.
            try:
                # Attach tzinfo so it can be subtracted from aware datetime.now(timezone.utc).
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
        """Stamps last_fired/price/fingerprint and sets armed=0 after a confirmed send; idempotent on (engine, ticker); conn is caller-scoped."""
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
        """Elapsed seconds to now (UTC); treats naive ts as UTC to match yfinance's stripped-zone convention and avoid BST-offset errors."""
        now_utc = pd.Timestamp.now(tz=timezone.utc)
        if ts.tzinfo is not None:
            return (now_utc - ts.tz_convert(timezone.utc)).total_seconds()
        return (now_utc - ts.tz_localize(timezone.utc)).total_seconds()

    def _refresh_account_performance_cache(self) -> None:
        """Rides along on this scan cycle so account performance figures are refreshed server-side
        once, shared by every browser tab that later polls the account detail page, rather than
        each poll re-deriving MWRR/period-returns from scratch."""
        accounts_engine.refresh_all_trading_performance_caches()

    def _prune_alert_state(self, conn: sqlite3.Connection) -> None:
        """Deletes alert_state rows older than 7 days; only delisted/removed tickers accumulate; conn is caller-scoped."""
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
        """Returns True if Yahoo Finance reports a dividend or split today; memoised per calendar day to avoid redundant HTTP calls."""
        today_date = datetime.now(timezone.utc).date()
        cache_key = (ticker, today_date.isoformat())
        if cache_key in self._corp_action_cache:
            return self._corp_action_cache[cache_key]

        result = False
        try:
            actions = yahoo_engine.get_ticker_actions(ticker)
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
        """Runs format→send→record→feed→sleep for each alert; msg_builder/feed_builder carry per-engine formatting differences."""
        for ticker, alert, currency, meta in alert_tuples:
            formatted_price = format_currency(alert['price'], currency)
            url = build_stock_url(SERVER_URL, PORT, ticker)
            # ml_confidence_score 0–100; var_95/cvar_95 are fractions 0–1 (multiply by 100 for display).
            ml_conf = f"{meta.get('ml_confidence_score'):.1f}%" if meta.get('ml_confidence_score') is not None else "N/A"
            var = f"{(meta.get('var_95') * 100):.2f}%" if meta.get('var_95') is not None else "N/A"
            sent = f"{meta.get('sentiment_score'):.3f}" if meta.get('sentiment_score') is not None else "N/A"
            msg = msg_builder(ticker, formatted_price, alert, ml_conf, var, sent, url)
            if notify(_ALERT_SOURCES[engine], engine, feed_builder(ticker, formatted_price, alert), nextcloud_text=msg, conn=conn):
                self.record_alert_fired(engine, ticker, alert['price'], alert['reason'], conn)
            time.sleep(_DISPATCH_SLEEP_SECONDS)

    @staticmethod
    def _compute_target_only_tickers(held_set: set, holding_limits_by_ticker: Dict[str, Dict[int, Dict[str, Optional[float]]]]) -> List[str]:
        """Tickers with an active low/high target that aren't in held_set — e.g. a Watchlist-only
        ticker with a target set from the Stock Detail page's Position Targets box. Kept out of
        the main portfolio ticker list so they never enter Crash/Moonshot/Anomaly evaluation."""
        return sorted(t for t in holding_limits_by_ticker.keys() if t not in held_set)

    def _check_holding_limits(
        self,
        ticker: str,
        current_price: float,
        currency: str,
        holding_limits_by_ticker: Dict[str, Dict[int, Dict[str, Optional[float]]]],
        account_names: Dict[int, str],
        conn: sqlite3.Connection,
        alerts_to_send: list,
    ) -> None:
        """Shared by the main portfolio loop and the target-only loop (e.g. watchlist tickers with
        a target but no holding) so the price-target check itself is never duplicated."""
        ticker_limits = holding_limits_by_ticker.get(ticker)
        if not ticker_limits:
            return
        for account_id, lim in ticker_limits.items():
            # holding_price_limits rows for a soft-deleted account are never cleaned up;
            # account_names only holds active accounts, so absence here means skip.
            account_name = account_names.get(account_id)
            if account_name is None:
                continue
            low_limit = lim.get("low_limit")
            high_limit = lim.get("high_limit")
            if low_limit is not None and current_price <= low_limit:
                key = f"{account_id}:{ticker}:low"
                if not self._evaluate_alert_gate("HoldingLimit", key, current_price, "LOW TARGET REACHED", conn):
                    alerts_to_send.append((key, ticker, account_name, "low", low_limit, current_price, currency))
            if high_limit is not None and current_price >= high_limit:
                key = f"{account_id}:{ticker}:high"
                if not self._evaluate_alert_gate("HoldingLimit", key, current_price, "HIGH TARGET REACHED", conn):
                    alerts_to_send.append((key, ticker, account_name, "high", high_limit, current_price, currency))

    def _dispatch_holding_limit_alerts(self, alert_tuples: list, conn: sqlite3.Connection) -> None:
        """Set Targets: fires when a held ticker's user-set low_limit/high_limit is reached; shares the alert_state gate with Crash/Moonshot but keys on a composite account_id:ticker:direction since limits are per-account."""
        for key, ticker, account_name, direction, limit_price, current_price, currency in alert_tuples:
            formatted_limit = format_currency(limit_price, currency)
            formatted_price = format_currency(current_price, currency)
            url = build_stock_url(SERVER_URL, PORT, ticker)
            label = "Low Target" if direction == "low" else "High Target"
            reason = "LOW TARGET REACHED" if direction == "low" else "HIGH TARGET REACHED"
            msg = (
                f"🎯 **{label} Reached: {ticker}** ({account_name})\n\n"
                f"**Target:** {formatted_limit}\n"
                f"**Current Price:** {formatted_price}\n\n"
                f"🔗 [View Position]({url})"
            )
            feed_msg = f"{ticker} ({account_name}): {label} of {formatted_limit} reached — current price {formatted_price}."
            if notify(_ALERT_SOURCES["HoldingLimit"], "HoldingLimit", feed_msg, nextcloud_text=msg, conn=conn):
                self.record_alert_fired("HoldingLimit", key, current_price, reason, conn)
            time.sleep(_DISPATCH_SLEEP_SECONDS)

    def run(self) -> None:
        # Opened here (not in __init__) so it is always on the correct APScheduler worker thread.
        conn = get_connection()
        try:
            self._run(conn)
        finally:
            conn.close()

    def _run(self, conn: sqlite3.Connection) -> None:
        self._prune_alert_state(conn)
        logger.info("Scan initiated.")
        
        # START_TIME/END_TIME are entered in the Settings UI as USER_TIMEZONE wall-clock time (matching the
        # scheduler's own CronTrigger(timezone=user_tz)), so they must be localized before comparing to UTC now.
        # When absent, derive from HOME_EXCHANGE via time_engine, which already returns UTC.
        sched_cfg = self.config.get("SCHEDULING", {}).get("CRASH_ALERTS", {})
        today = datetime.now(timezone.utc).date()

        try:
            if sched_cfg.get("START_TIME") and sched_cfg.get("END_TIME"):
                user_tz = time_engine.get_user_tz()
                start_time = datetime.combine(today, datetime.strptime(sched_cfg["START_TIME"], "%H:%M").time(), tzinfo=user_tz).astimezone(timezone.utc).time().replace(tzinfo=None)
                end_time = datetime.combine(today, datetime.strptime(sched_cfg["END_TIME"], "%H:%M").time(), tzinfo=user_tz).astimezone(timezone.utc).time().replace(tzinfo=None)
            else:
                start_time, end_time = time_engine.market_window_utc()
            now = datetime.now(timezone.utc).time().replace(tzinfo=None)
            if not (start_time <= now <= end_time):
                logger.info("Outside active bounds (%s-%s UTC). Aborted.", start_time, end_time)
                return
        except (ValueError, TypeError) as e:
            logger.error("Invalid schedule time config (%s): %s", sched_cfg, e)
            return

        # Without normalisation, partial-day volume vs 50-day average understates the ratio and flags every morning breakout as low-volume.
        _to_min = lambda t: t.hour * 60 + t.minute
        _session_total = _to_min(end_time) - _to_min(start_time)
        _elapsed = max(0, _to_min(datetime.now(timezone.utc).time().replace(tzinfo=None)) - _to_min(start_time))
        session_elapsed_frac = max(0.01, min(1.0, _elapsed / _session_total)) if _session_total > 0 else 1.0

        tickers = self.get_portfolio_tickers()
        ignored = self.config.get("IGNORED_TICKERS", [])
        mutual_funds = get_mutual_fund_tickers(tickers)

        tickers = [t for t in tickers if t not in ignored and t not in mutual_funds]

        holding_limits_by_ticker: Dict[str, Dict[int, Dict[str, Optional[float]]]] = {}
        for (account_id, lim_ticker), lim in get_all_holding_price_limits().items():
            if lim.get("low_limit") is None and lim.get("high_limit") is None:
                continue
            holding_limits_by_ticker.setdefault(lim_ticker, {})[account_id] = lim

        # A ticker with a target set (e.g. via the Watchlist row on the Stock Detail page) but not
        # actually held must still be scanned for the price-target check — but deliberately does
        # NOT join `tickers` itself, so it stays out of Crash/Moonshot/Anomaly evaluation below.
        held_set = set(tickers)
        target_only_tickers = self._compute_target_only_tickers(held_set, holding_limits_by_ticker)

        if not tickers and not target_only_tickers:
            logger.warning("No valid portfolio items found for intraday scan.")
            return

        metadata = self.get_asset_metadata(sorted(held_set | set(target_only_tickers)))
        account_names = {acc["id"]: acc["name"] for acc in get_accounts()}

        # SPY fetched here once so crash_engine never needs a per-scan HTTP call
        macro_tickers = ["^TYX", "SPY"]
        spy_change_pct: Optional[float] = None
        download_list = sorted(set(tickers + target_only_tickers + macro_tickers))
        
        logger.info("Performing bulk YF 5m fetch for %d assets & macro benchmarks.", len(download_list))
        ticker_dfs = yahoo_engine.get_intraday(download_list, period="1d", interval="5m")
        if not ticker_dfs:
            logger.warning("Bulk intraday download returned no data.")
            return

        df_bulk = pd.concat(
            {t: df for t, df in ticker_dfs.items()},
            axis=1,
        )
        if df_bulk.empty:
            logger.warning("Bulk download returned empty DataFrame.")
            return

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

                if self._seconds_since(m_df.index[-1]) > _STALE_SECONDS:
                    continue
                
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
                        if notify(
                            _ALERT_SOURCES["Macro"],
                            "Macro",
                            f"Systemic Yield Surge detected on {m_ticker} (+{m_spike:.2f}%)",
                            nextcloud_text=msg,
                            conn=conn,
                        ):
                            self.record_alert_fired("Macro", m_ticker, m_curr, reason_macro, conn)
            except Exception:
                logger.error("Macro eval failed for %s", m_ticker, exc_info=True)

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
                # Cap is applied post-beta so beta scaling can't widen it back out and defeat the override.
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
        holding_limit_alerts_to_send = []

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

                if self._seconds_since(df_intraday.index[-1]) > _STALE_SECONDS:
                    df_intraday.index = df_intraday.index.tz_localize(None)
                    df_intraday.to_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet", engine='pyarrow')
                    continue

                # Strip TZ before index arithmetic; parquet write deferred until after history validation.
                df_intraday.index = df_intraday.index.tz_localize(None)

                current_price = float(df_intraday['Close'].iloc[-1])
                session_open = float(df_intraday['Open'].iloc[0]) if 'Open' in df_intraday.columns else None

                hist_path = HISTORICAL_DIR / f"{ticker}.parquet"
                if not hist_path.exists():
                    continue

                df_hist = pd.read_parquet(hist_path)
                # NaN last-close propagates into df_combined as prev_close, silently poisoning pct comparisons.
                df_hist = df_hist[df_hist['Close'].notna()]
                if df_hist.empty or len(df_hist) < 20:
                    continue

                df_intraday.to_parquet(INTRADAY_DIR / f"{ticker}_intraday.parquet", engine='pyarrow')

                last_hist_close = df_hist['Close'].iloc[-1]
                # A mid-session refresh can leave today's own still-forming bar as the parquet's last row.
                if is_daily_bar_still_forming(df_hist.index[-1].date(), df_intraday.index[-1].date()) and len(df_hist) >= 2:
                    last_hist_close = df_hist['Close'].iloc[-2]
                if last_hist_close > 0:
                    upsert_live_price(ticker, metadata.get(ticker, {}).get('company_name') or ticker, current_price, float(last_hist_close), conn=conn)
                    raw_gap_pct = abs((current_price - last_hist_close) / last_hist_close) * 100.0
                    if raw_gap_pct > _CORP_ACTION_GAP_PCT:
                        if self._has_corporate_action_today(ticker):
                            logger.info("Corporate action detected for %s; suppressing alert evaluation.", ticker)
                            continue

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

                self._check_holding_limits(
                    ticker, current_price, currency, holding_limits_by_ticker,
                    account_names, conn, holding_limit_alerts_to_send,
                )

                # evaluate() first so reason string is available for fingerprinting; returns None when no condition met.
                if crash_enabled:
                    crash_alert = self.crash_engine.evaluate(
                        ticker, current_price, df_combined, asset_meta, df_hist, session_open
                    )
                    if crash_alert and not self._evaluate_alert_gate(
                        "Crash", ticker, current_price, crash_alert.get("reason", ""), conn
                    ):
                        crash_alerts_to_send.append((ticker, crash_alert, currency, asset_meta))

                if moonshot_enabled:
                    if 'Volume' in df_intraday.columns:
                        # Project to full-day equivalent so it compares correctly against the 50-day average.
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

        for ticker in target_only_tickers:
            try:
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
                if df_intraday.empty:
                    continue

                df_intraday.index = df_intraday.index.tz_localize(None)
                if self._seconds_since(df_intraday.index[-1]) > _STALE_SECONDS:
                    continue

                current_price = float(df_intraday['Close'].iloc[-1])
                asset_meta = metadata.get(ticker, {})
                currency = asset_meta.get('currency', 'USD')

                self._check_holding_limits(
                    ticker, current_price, currency, holding_limits_by_ticker,
                    account_names, conn, holding_limit_alerts_to_send,
                )
            except Exception:
                logger.error("Error processing target-only ticker %s", ticker, exc_info=True)

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
        self._dispatch_holding_limit_alerts(holding_limit_alerts_to_send, conn)

        self._refresh_account_performance_cache()

        logger.info(
            "Scan complete. Dispatched %d crashes, %d moonshots, %d anomalies, %d holding-limit alerts.",
            len(crash_alerts_to_send), len(moonshot_alerts_to_send), len(anomaly_alerts_to_send),
            len(holding_limit_alerts_to_send),
        )

if __name__ == "__main__":
    try:
        engine = IntradayOrchestrator()
        engine.run()
    except Exception:
        logger.critical("Orchestrator failed to start.", exc_info=True)
        raise