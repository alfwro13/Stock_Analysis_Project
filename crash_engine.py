# crash_engine.py
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any
import logging

import pandas as pd
import ta

from utils import clamp_beta
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

class CrashEngine:
    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config
        self.crash_cfg = self.config.get("NOTIFICATIONS", {}).get("CRASH_ALERTS", {})
        self.drop_percent = float(self.crash_cfg.get("DROP_PERCENT", 5.0))
        self.drop_days = int(self.crash_cfg.get("DROP_DAYS", 3))
        self.sma_length = int(self.crash_cfg.get("SMA_LENGTH", 10))
        self.sma_gap_percent = float(self.crash_cfg.get("SMA_GAP_PERCENT", 2.0))
        self.session_crash_threshold = float(
            self.crash_cfg.get("SESSION_CRASH_THRESHOLD", self.crash_cfg.get("FLASH_CRASH_THRESHOLD", 3.0))
        )
        # Injected by the orchestrator once per run to avoid a per-crash SPY HTTP call
        self.spy_change_pct: float | None = None
        # Ceiling applied AFTER beta scaling so high-beta names can't widen the AI Volatility Defense cap back out.
        self.ai_threshold_cap: float | None = None

    def _fetch_market_context(self) -> float:
        # Fallback used when spy_change_pct was not pre-injected by the orchestrator.
        try:
            _result = yahoo_engine.get_intraday(["SPY"], period="1d", interval="5m")
            spy = _result.get("SPY")
            if spy is not None and len(spy) >= 2:
                session_open = float(spy['Close'].iloc[0])
                curr_price = float(spy['Close'].iloc[-1])
                if session_open > 0:
                    return ((curr_price - session_open) / session_open) * 100.0
        except Exception as e:
            logger.warning(f"Failed to fetch macroeconomic context (SPY): {e}")
        return 0.0

    def _generate_context_report(
        self,
        ticker: str,
        drop_pct: float,
        df_combined: pd.DataFrame,
        asset_meta: dict[str, Any],
        df_hist: pd.DataFrame | None = None,
    ) -> str:
        report = []
        company_name = asset_meta.get('company_name', ticker)

        # Use pre-fetched SPY value injected by the orchestrator; fall back to live fetch only if unavailable.
        if self.spy_change_pct is not None:
            spy_drop = self.spy_change_pct
        else:
            logger.warning(
                "spy_change_pct not injected for %s — falling back to live SPY fetch. "
                "This is expected only outside the orchestrator (tests, ad-hoc scripts).",
                ticker,
            )
            spy_drop = self._fetch_market_context()
        if spy_drop <= -1.5:
            report.append(f"The broader market is currently experiencing a heavy sell-off (S&P 500: {spy_drop:.2f}%). The weakness in {company_name} is likely being amplified by macro-economic panic rather than purely isolated company issues.")
        elif spy_drop >= 0:
            report.append(f"This appears to be an isolated (idiosyncratic) event. While {company_name} is crashing, the broader market remains green/flat (S&P 500: {spy_drop:.2f}%).")
        else:
            report.append(f"The broader market is slightly weak (S&P 500: {spy_drop:.2f}%), but {company_name} is significantly underperforming the baseline.")

        # Use df_hist (already loaded by orchestrator) to avoid a per-crash HTTP call.
        try:
            vol_data = df_hist if df_hist is not None and not df_hist.empty else None
            if vol_data is None:
                _result = yahoo_engine.get_price_history([ticker], period="1mo", interval="1d")
                vol_data = _result.get(ticker)
            if not vol_data.empty and 'Volume' in vol_data.columns:
                current_vol = vol_data['Volume'].iloc[-1]
                valid_vol = vol_data['Volume'].dropna()

                avg_vol = valid_vol.iloc[:-1].tail(20).mean()

                if not pd.isna(avg_vol) and avg_vol > 0:
                    if current_vol > (avg_vol * 1.5):
                        report.append(f"Selling pressure is severe. Intraday volume has already surged to {current_vol:,.0f}, which is massively above its recent average. This indicates heavy institutional distribution.")
                    else:
                        report.append("Interestingly, this price drop is occurring on relatively low/average volume, suggesting a lack of liquidity or an absence of buyers rather than aggressive institutional dumping.")
        except Exception as e:
            logger.warning(f"Volume anomaly check failed for {ticker}: {e}")

        # Use settled bars only for SMA; the live tick is a partially-formed bar.
        df_settled = df_combined.iloc[:-1]
        latest_price = df_combined['Close'].iloc[-1]
        prev_settled_close = df_settled['Close'].iloc[-1]
        try:
            sma50 = ta.trend.SMAIndicator(close=df_settled['Close'], window=50).sma_indicator().iloc[-1]
            if latest_price < sma50 and prev_settled_close >= sma50:
                report.append("Technical damage is notable: the stock just sliced violently through its 50-day moving average, a key institutional support level.")
        except Exception as e:
            logger.warning(f"Technical damage assessment failed for {ticker}: {e}")

        # News requires a live yfinance call (never in df_hist), but _generate_context_report only runs on confirmed alerts — one call per fired event is acceptable.
        try:
            news = yahoo_engine.get_news(ticker)
            if news:
                # Sort newest-first after parsing; news[:3] could otherwise be stale while breaking news sits at position 4+.
                parsed: list[tuple[datetime, str, str]] = []
                for item in news:
                    content = item.get('content', item)
                    headline = content.get('title', '')
                    publisher = content.get('publisher', '')
                    if not publisher and isinstance(content.get('provider'), dict):
                        publisher = content['provider'].get('displayName', '')
                    pub_time_raw = (
                        content.get('pubDate')
                        or content.get('providerPublishTime')
                        or item.get('providerPublishTime', 0)
                    )
                    try:
                        if isinstance(pub_time_raw, str):
                            # ISO strings from yfinance are UTC; parse with utc=True then strip tz
                            pub_time = pd.to_datetime(pub_time_raw, utc=True).tz_convert(None)
                        else:
                            # UNIX timestamps are always UTC seconds since epoch
                            pub_time = datetime.fromtimestamp(float(pub_time_raw), tz=timezone.utc).replace(tzinfo=None)
                        parsed.append((pub_time, publisher, headline))
                    except Exception as dt_e:
                        logger.debug(f"Date parsing failed for news item on {ticker}: {dt_e}")

                cutoff = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=2)
                recent = sorted(
                    (entry for entry in parsed if entry[0] >= cutoff),
                    key=lambda x: x[0],
                    reverse=True,
                )[:3]

                if recent:
                    report.append("\n**Potential Catalysts / Recent Headlines:**")
                    for pub_time, publisher, headline in recent:
                        report.append(f"- *{publisher}:* {headline}")
        except Exception as e:
            logger.warning(f"Catalyst extraction failed for {ticker}: {e}")
            report.append("\n**Catalysts:** No major breaking news headlines found on Yahoo Finance within the last 48 hours.")

        return "\n".join(report)

    def evaluate(
        self,
        ticker: str,
        current_price: float,
        df_combined: pd.DataFrame,
        asset_meta: dict[str, Any],
        df_hist: pd.DataFrame | None = None,
        session_open: float | None = None,
    ) -> dict[str, Any] | None:
        # df_combined contract: 'Close' column only (df_hist[['Close']] + live tick). Adding OHLCV here requires updating intraday_orchestrator.py too.
        # Exclude the live tick from indicator calculations — partially-formed bar skews trend signals on volatile open days.
        df_settled = df_combined.iloc[:-1]

        if df_settled.empty or len(df_settled) < self.sma_length:
            return None

        prev_close = df_settled['Close'].iloc[-1]
        if not prev_close:
            logger.debug("Non-positive prev_close for %s; skipping.", ticker)
            return None

        # Beta-scale all thresholds (clamped to [0.5, 2.0]) so high-beta names need a bigger move and low-beta names trip earlier.
        beta = clamp_beta(asset_meta.get('beta'))
        adj_session_threshold = self.session_crash_threshold * beta
        # Cap AFTER beta scaling so high-beta names cannot widen the AI Volatility Defense back out.
        if self.ai_threshold_cap is not None:
            adj_session_threshold = min(adj_session_threshold, self.ai_threshold_cap)
        adj_drop_percent      = self.drop_percent            * beta
        adj_sma_gap_percent   = self.sma_gap_percent         * beta

        intraday_drop_pct = ((current_price - prev_close) / prev_close) * 100.0

        # Gap & Crash: opened down AND still below open. Gap & Recovery: opened down but since recovered. Without session_open, fall back to prev_close comparison.
        since_open_pct: float | None = None
        gap_pct: float | None = None
        if session_open is not None and session_open > 0:
            gap_pct = ((session_open - prev_close) / prev_close) * 100.0
            since_open_pct = ((current_price - session_open) / session_open) * 100.0
            is_session_crash = intraday_drop_pct <= -adj_session_threshold and since_open_pct <= 0
        else:
            is_session_crash = intraday_drop_pct <= -adj_session_threshold

        lookback_idx = -(self.drop_days + 1)
        if abs(lookback_idx) > len(df_settled):
            logger.debug(
                "Insufficient history for %s: need %d settled bars, have %d. Skipping trend-bleed check.",
                ticker, self.drop_days + 1, len(df_settled),
            )
            return None

        past_price = df_settled['Close'].iloc[lookback_idx]
        if not past_price:
            logger.debug("Non-positive past_price for %s; skipping.", ticker)
            return None
        price_drop_pct = ((current_price - past_price) / past_price) * 100.0

        sma_series = ta.trend.SMAIndicator(close=df_settled['Close'], window=self.sma_length).sma_indicator()
        latest_sma = sma_series.iloc[-1]
        below_sma_pct = ((latest_sma - current_price) / latest_sma) * 100.0 if latest_sma else 0.0

        is_dropping_fast = price_drop_pct <= -adj_drop_percent
        is_breaking_sma = below_sma_pct >= adj_sma_gap_percent
        
        atr_stop = asset_meta.get('atr_stop_loss')
        atr_last_updated = asset_meta.get('atr_last_updated')
        atr_is_fresh = False
        if atr_last_updated is not None:
            try:
                updated_dt = pd.to_datetime(atr_last_updated)
                now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
                atr_is_fresh = (now_utc - updated_dt.to_pydatetime()) <= timedelta(days=3)
            except Exception:
                logger.debug("Could not parse ATR last_updated timestamp '%s', treating as stale", atr_last_updated)
        is_below_atr = atr_stop is not None and atr_is_fresh and (0 < current_price < atr_stop)

        if is_session_crash or (is_dropping_fast and is_breaking_sma) or is_below_atr:
            reason = []
            context_report = ""

            if is_session_crash:
                if gap_pct is not None and since_open_pct is not None:
                    gap_dir = "up" if gap_pct >= 0 else "down"
                    reason.append(
                        f"SESSION CRASH: Gapped {gap_dir} {abs(gap_pct):.2f}% at open and "
                        f"continuing lower ({abs(since_open_pct):.2f}% since open, "
                        f"{abs(intraday_drop_pct):.2f}% vs. prev close)."
                    )
                else:
                    reason.append(f"SESSION CRASH / GAP DOWN: Dropped {abs(intraday_drop_pct):.2f}% today.")
                context_report = self._generate_context_report(ticker, intraday_drop_pct, df_combined, asset_meta, df_hist)
            
            if is_dropping_fast and not is_session_crash:
                reason.append(f"Multi-Day Bleed: Dropped {abs(price_drop_pct):.2f}% in {self.drop_days}d")
            if is_breaking_sma and not is_session_crash:
                reason.append(f"Fell {below_sma_pct:.2f}% below {self.sma_length}d SMA")
            if is_below_atr:
                reason.append(f"Price ({current_price:.2f}) broke Quantamental ATR floor ({atr_stop:.2f})")
            
            final_reason = " | ".join(reason)
            if context_report:
                final_reason += f"\n\n**🔍 Crash Analysis:**\n{context_report}"
            
            return {
                'price': current_price,
                'reason': final_reason
            }
            
        return None