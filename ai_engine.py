# ai_engine.py
import json
import re
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Literal, Optional

import pandas as pd
import ta

from config import HISTORICAL_DIR
from database import get_connection
from portfolio_service import get_rate_to_base
from time_engine import now_local
from regime_engine import get_latest_regime

logger = logging.getLogger(__name__)


class AIPromptEngine:
    """
    Dedicated engine for compiling Quantamental data into structured LLM prompts.

    Isolates prompt engineering and context aggregation from the web server.
    Each auxiliary context fetcher is independently fault-tolerant: a failure in
    any single block degrades to an explicit "unavailable" note rather than
    aborting the whole prompt, because for an external-chat consumer a partial
    prompt is far more useful than no prompt at all.
    """

    _HTML_TAG_RE = re.compile('<.*?>')

    def __init__(self) -> None:
        self._prompt_cache: Dict[tuple, str] = {}
        self._cache_date: str = ""

    def _cache_key(self, ticker: str, mode: str) -> tuple:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._cache_date:
            self._prompt_cache.clear()
            self._cache_date = today
        return (ticker, mode, today)

    def invalidate_cache(self) -> None:
        """Force-clear the prompt cache (call after a manual data refresh)."""
        self._prompt_cache.clear()
        self._cache_date = ""

    @contextmanager
    def _db(self):
        """Context manager that yields an open cursor and closes the connection on exit."""
        conn = get_connection()
        try:
            yield conn.cursor()
        finally:
            conn.close()

    # ------------------------------------------------------------------ #
    # Low-level helpers
    # ------------------------------------------------------------------ #
    def _clean_html(self, raw_html: Optional[str]) -> str:
        """Strips HTML tags from the database's educational notes for plain-text AI consumption."""
        if not raw_html:
            return "No notes available."

        # Format lists cleanly before stripping tags
        text = raw_html.replace('<li>', '\n- ').replace('</li>', '').replace('<br>', '\n')
        cleantext = self._HTML_TAG_RE.sub('', text)
        return cleantext.replace('&nbsp;', ' ').strip()

    @staticmethod
    def _fmt_pct(val: Optional[float], decimals: int = 1) -> str:
        """Formats a decimal fraction (0.12) as a percentage string ('12.0%')."""
        if val is None:
            return "N/A"
        try:
            return f"{(val * 100):.{decimals}f}%"
        except (TypeError, ValueError):
            return "N/A"

    @staticmethod
    def _fmt_float(val: Optional[float], decimals: int = 2) -> str:
        """Formats a float to a fixed number of decimals, or 'N/A' if missing."""
        if val is None:
            return "N/A"
        try:
            return f"{val:.{decimals}f}"
        except (TypeError, ValueError):
            return "N/A"

    @staticmethod
    def _describe_series_trajectory(series: pd.Series, decimals: int = 1) -> str:
        """
        Renders the last few points of a numeric series as a compact arrow path
        (e.g. '41.2 -> 58.0 -> 71.9') plus a direction verdict. This is the core
        'trajectory over snapshot' upgrade: it shows the LLM momentum, not a freeze-frame.
        """
        clean = series.dropna()
        if clean.empty:
            return "N/A"

        tail = clean.tail(5)
        path = " -> ".join(f"{v:.{decimals}f}" for v in tail)

        if len(tail) >= 2:
            delta = float(tail.iloc[-1]) - float(tail.iloc[0])
            if delta > 0:
                direction = "rising"
            elif delta < 0:
                direction = "falling"
            else:
                direction = "flat"
            return f"{path} ({direction})"
        return path

    # ------------------------------------------------------------------ #
    # Auxiliary context fetchers
    # ------------------------------------------------------------------ #
    def _get_portfolio_context(self, ticker: str) -> Dict[str, Any]:
        """Extracts holdings, VWAP, and account splits for the specific ticker."""
        try:
            from accounts_engine import get_combined_holdings
            return get_combined_holdings().get(ticker, {})
        except Exception as e:
            logger.warning("Could not read portfolio context for %s: %s", ticker, e)
            return {}

    def _get_technical_indicators(self, ticker: str) -> Dict[str, str]:
        """
        Reads the raw Parquet file to extract the *trajectory* of MACD, RSI, OBV
        and volume — not just the latest value. Also computes the Volume
        Contraction Ratio (the literal VCP signal: recent vol vs the prior baseline).
        """
        df_path = HISTORICAL_DIR / f"{ticker}.parquet"
        metrics: Dict[str, str] = {
            "rsi_path": "N/A",
            "macd_path": "N/A",
            "macd_signal_path": "N/A",
            "macd_hist_path": "N/A",
            "obv_trend": "N/A",
            "volume_path": "N/A",
            "average_volume": "N/A",
            "volume_contraction_ratio": "N/A",
            "price_path": "N/A",
            "adx": "N/A",
            "bb_pband": "N/A",
            "bb_wband": "N/A",
        }

        if not df_path.exists():
            logger.info(f"[AI ENGINE] No Parquet history for {ticker}; technical trajectory unavailable.")
            return metrics

        try:
            df = pd.read_parquet(df_path)
            if df.empty or len(df) <= 30:
                return metrics

            close = df['Close']
            volume = df['Volume']

            # --- Momentum trajectories (last 5 sessions) ---
            rsi_series = ta.momentum.RSIIndicator(close=close, window=14).rsi()
            metrics["rsi_path"] = self._describe_series_trajectory(rsi_series, decimals=1)

            macd_indicator = ta.trend.MACD(close=close)
            metrics["macd_path"] = self._describe_series_trajectory(macd_indicator.macd(), decimals=3)
            metrics["macd_signal_path"] = self._describe_series_trajectory(macd_indicator.macd_signal(), decimals=3)
            metrics["macd_hist_path"] = self._describe_series_trajectory(macd_indicator.macd_diff(), decimals=3)

            # --- Price path ---
            metrics["price_path"] = self._describe_series_trajectory(close, decimals=2)

            # --- ADX trend strength (14-period) ---
            # Requires High/Low columns; degrade gracefully if absent.
            if 'High' in df.columns and 'Low' in df.columns:
                adx_indicator = ta.trend.ADXIndicator(
                    high=df['High'], low=df['Low'], close=close, window=14
                )
                adx_val = adx_indicator.adx().iloc[-1]
                if not pd.isna(adx_val):
                    if adx_val >= 50:
                        strength = "STRONG TREND"
                    elif adx_val >= 25:
                        strength = "TRENDING"
                    else:
                        strength = "WEAK/CHOPPY"
                    metrics["adx"] = f"{adx_val:.1f} ({strength})"

            # --- Bollinger Band position (20-period, 2σ) ---
            bb = ta.volatility.BollingerBands(close=close, window=20, window_dev=2)
            pband = bb.bollinger_pband().iloc[-1]
            wband = bb.bollinger_wband().iloc[-1]
            if not pd.isna(pband) and not pd.isna(wband):
                if pband >= 1.0:
                    bb_pos = "ABOVE upper band (overbought risk)"
                elif pband <= 0.0:
                    bb_pos = "BELOW lower band (oversold / breakdown)"
                elif pband >= 0.8:
                    bb_pos = "Near upper band"
                elif pband <= 0.2:
                    bb_pos = "Near lower band"
                else:
                    bb_pos = "Mid-band"
                metrics["bb_pband"] = f"{pband:.2f} ({bb_pos})"
                metrics["bb_wband"] = f"{wband:.4f} (band width as % of price)"

            # --- OBV accumulation/distribution ---
            obv = ta.volume.OnBalanceVolumeIndicator(close=close, volume=volume).on_balance_volume()
            obv_ma = obv.rolling(window=21).mean()
            if not pd.isna(obv.iloc[-1]) and not pd.isna(obv_ma.iloc[-1]):
                metrics["obv_trend"] = (
                    "Accumulation (Bullish)" if obv.iloc[-1] > obv_ma.iloc[-1]
                    else "Distribution (Bearish)"
                )

            # --- Volume trajectory + contraction ratio (VCP detector) ---
            metrics["volume_path"] = self._describe_series_trajectory(volume, decimals=0)
            avg_vol_21 = volume.rolling(21).mean().iloc[-1]
            if not pd.isna(avg_vol_21):
                metrics["average_volume"] = f"{avg_vol_21:,.0f}"

            # Recent 5-day mean volume vs the prior 20-day baseline. A ratio well
            # below 1.0 signals the volume "dry-up" that precedes a VCP breakout.
            recent_vol_5 = volume.tail(5).mean()
            prior_vol_20 = volume.iloc[-25:-5].mean() if len(volume) >= 25 else None
            if prior_vol_20 and not pd.isna(prior_vol_20) and prior_vol_20 > 0 and not pd.isna(recent_vol_5):
                ratio = recent_vol_5 / prior_vol_20
                tag = "CONTRACTING (VCP-supportive)" if ratio < 0.8 else (
                    "EXPANDING (breakout/climax)" if ratio > 1.3 else "stable"
                )
                metrics["volume_contraction_ratio"] = f"{ratio:.2f}x ({tag})"

        except Exception as e:
            logger.error(f"[AI ENGINE] Failed to parse technical trajectory for {ticker}: {e}")

        return metrics

    def _get_momentum_factor_stack(self, ticker: str) -> Dict[str, str]:
        """
        Pulls the persisted academic momentum ladder and volatility factors from
        the latest quant_signals row. These are computed by the enricher and were
        previously dropped entirely from the prompt.
        """
        factors: Dict[str, str] = {
            "mom_1m": "N/A", "mom_3m": "N/A", "mom_6m": "N/A",
            "mom_12m_skip1m": "N/A", "hist_vol_20": "N/A", "atr_pct": "N/A",
            "rel_strength_5d": "N/A", "rel_strength_20d": "N/A",
        }

        try:
            with self._db() as cursor:
                cursor.execute("""
                    SELECT mom_1m, mom_3m, mom_6m, mom_12m_skip1m,
                           hist_vol_20, atr_pct, rel_strength_5d, rel_strength_20d
                    FROM quant_signals
                    WHERE ticker = ?
                    ORDER BY date DESC
                    LIMIT 1
                """, (ticker,))
                row = cursor.fetchone()
                if row:
                    r = dict(row)
                    factors["mom_1m"] = self._fmt_pct(r.get('mom_1m'))
                    factors["mom_3m"] = self._fmt_pct(r.get('mom_3m'))
                    factors["mom_6m"] = self._fmt_pct(r.get('mom_6m'))
                    factors["mom_12m_skip1m"] = self._fmt_pct(r.get('mom_12m_skip1m'))
                    factors["hist_vol_20"] = self._fmt_pct(r.get('hist_vol_20'))
                    factors["atr_pct"] = self._fmt_pct(r.get('atr_pct'))
                    factors["rel_strength_5d"] = self._fmt_pct(r.get('rel_strength_5d'))
                    factors["rel_strength_20d"] = self._fmt_pct(r.get('rel_strength_20d'))
        except Exception as e:
            logger.error(f"[AI ENGINE] Failed to fetch momentum factor stack for {ticker}: {e}")

        return factors

    def _get_macro_context(self) -> str:
        """
        Aggregates the systemic macro backdrop: volatility regime, dual-region
        yield threat levels, DXY, and a 7-day Tier-1 event radar. This was a fully
        populated set of tables the prompt never touched.
        """
        lines: List[str] = []

        # --- Volatility regime (market_regimes via helper) ---
        try:
            regime = get_latest_regime()
            if regime:
                lines.append(
                    f"Volatility Regime — US: {regime.get('us_regime_label', 'Unknown')} "
                    f"(Turbulence {self._fmt_float(regime.get('us_turbulence'))}) | "
                    f"UK: {regime.get('uk_regime_label', 'Unknown')} "
                    f"(Turbulence {self._fmt_float(regime.get('uk_turbulence'))})"
                )
        except Exception as e:
            logger.warning(f"[AI ENGINE] Could not load volatility regime: {e}")

        # --- Systemic yield threat + DXY (macro_regimes) ---
        try:
            with self._db() as cursor:
                cursor.execute("SELECT * FROM macro_regimes ORDER BY date DESC LIMIT 1")
                macro_row = cursor.fetchone()
                if macro_row:
                    m = dict(macro_row)
                    lines.append(
                        f"US Yield Threat: {m.get('us_threat_level', 'GREEN')} "
                        f"(10Y {self._fmt_float(m.get('tnx_close'))}%, "
                        f"velocity {self._fmt_float(m.get('us_yield_velocity'))} bps/3d) | "
                        f"UK Yield Threat: {m.get('uk_threat_level', 'GREEN')} "
                        f"(Gilt {self._fmt_float(m.get('uk_gilt_close'))}%, "
                        f"velocity {self._fmt_float(m.get('uk_yield_velocity'))} bps/3d)"
                    )
                    lines.append(
                        f"US Dollar Index (DXY): {self._fmt_float(m.get('dxy_close'))} | "
                        f"GBP/USD: {self._fmt_float(m.get('gbpusd_close'), 4)}"
                    )

                # --- 7-day Tier-1 event radar (macro_calendar) ---
                now = datetime.now(timezone.utc)
                now_str = now.strftime('%Y-%m-%d %H:%M:%S')
                horizon_7d = (now + timedelta(days=7)).strftime('%Y-%m-%d %H:%M:%S')
                cursor.execute("""
                    SELECT event_date, currency, impact, event_name, forecast_val, previous_val
                    FROM macro_calendar
                    WHERE event_date BETWEEN ? AND ?
                      AND impact = 'High'
                    ORDER BY event_date ASC
                    LIMIT 12
                """, (now_str, horizon_7d))
                events = cursor.fetchall()
                if events:
                    lines.append("Upcoming Tier-1 Macro Events (7-Day Radar):")
                    for ev in events:
                        e = dict(ev)
                        fc = self._fmt_float(e.get('forecast_val'))
                        prev = self._fmt_float(e.get('previous_val'))
                        lines.append(
                            f"  - [{e.get('event_date')}] {e.get('currency')} "
                            f"{e.get('event_name')} (Est: {fc} | Prev: {prev})"
                        )
        except Exception as e:
            logger.error(f"[AI ENGINE] Failed to assemble macro context: {e}")

        if not lines:
            return "Macro regime data unavailable."
        return "\n".join(lines)

    def _get_sector_peer_context(self, ticker: str, sector: Optional[str]) -> str:
        """
        Ranks the ticker's 20-day relative strength against its sector peers using
        heavy SQL-side filtering (no bulk load into Python). Answers the question
        the model previously could not: 'Is this stock leading or lagging its sector?'
        """
        if not sector:
            return "Sector unknown; peer comparison unavailable."

        try:
            with self._db() as cursor:
                # Join each sector peer to its most-recent quant_signals relative-strength
                # reading. Correlated subquery keeps us to the latest row per ticker.
                cursor.execute("""
                    SELECT s.ticker,
                           s.company_name,
                           q.rel_strength_20d
                    FROM stock_signals s
                    JOIN quant_signals q
                      ON s.ticker = q.ticker
                     AND q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = s.ticker)
                    WHERE s.sector = ?
                      AND q.rel_strength_20d IS NOT NULL
                    ORDER BY q.rel_strength_20d DESC
                """, (sector,))
                peers = [dict(r) for r in cursor.fetchall()]

            if not peers or len(peers) < 2:
                return f"Sector '{sector}': insufficient peer data for ranking."

            total = len(peers)
            rank = next((i for i, p in enumerate(peers) if p['ticker'] == ticker), None)

            lines: List[str] = [f"Sector: {sector} ({total} peers with relative-strength data)"]
            if rank is not None:
                percentile = (1.0 - (rank / max(total - 1, 1))) * 100.0
                position = "LEADER" if percentile >= 66 else ("LAGGARD" if percentile <= 33 else "MID-PACK")
                lines.append(
                    f"This stock ranks #{rank + 1} of {total} on 20-day relative strength "
                    f"({percentile:.0f}th percentile — {position})."
                )

            # Show the strongest and weakest peers for context.
            # Cap at total//2 so the two lists never overlap (e.g. 4 peers → top-2/bottom-2).
            n = min(3, total // 2)
            top = peers[:n]
            bottom = peers[-n:]
            lines.append("Strongest in sector (20D rel-strength): " + ", ".join(
                f"{p['ticker']} {self._fmt_pct(p['rel_strength_20d'])}" for p in top
            ))
            lines.append("Weakest in sector (20D rel-strength): " + ", ".join(
                f"{p['ticker']} {self._fmt_pct(p['rel_strength_20d'])}" for p in bottom
            ))
            return "\n".join(lines)

        except Exception as e:
            logger.error(f"[AI ENGINE] Failed to build sector peer context for {ticker}: {e}")
            return "Sector peer comparison unavailable."

    def _get_options_volatility(self, ticker: str) -> str:
        """
        Pulls the earnings options-mispricing edge from earnings_volatility. The
        edge score is Historical Move minus Isolated Implied Move: positive means
        options are underpriced (buy premium), negative means overpriced (IV-crush risk).
        """
        try:
            with self._db() as cursor:
                cursor.execute("""
                    SELECT next_earnings_date, implied_move_pct, historical_avg_move_pct,
                           edge_score, options_volume, last_updated
                    FROM earnings_volatility
                    WHERE ticker = ?
                """, (ticker,))
                row = cursor.fetchone()

            if not row:
                return "No earnings volatility edge data on file for this ticker."

            r = dict(row)
            edge = r.get('edge_score')
            if edge is not None and edge > 0:
                verdict = "Options appear UNDERPRICED (statistical edge to buying premium)."
            elif edge is not None and edge < 0:
                verdict = "Options appear OVERPRICED (IV-crush risk; edge to selling premium)."
            else:
                verdict = "Options fairly priced relative to history."

            # Staleness check — warn the LLM if data is older than 7 days.
            last_updated = r.get('last_updated', '')
            staleness_note = ""
            try:
                lu_dt = datetime.strptime(last_updated[:19], "%Y-%m-%d %H:%M:%S").replace(tzinfo=timezone.utc)
                age_days = (datetime.now(timezone.utc) - lu_dt).days
                if age_days > 7:
                    staleness_note = f"\n[WARNING: This data is {age_days} days old — treat as stale.]"
            except (ValueError, TypeError):
                pass

            return (
                f"Next Earnings: {r.get('next_earnings_date', 'N/A')}\n"
                f"Isolated Implied Move (ATM straddle): {self._fmt_float(r.get('implied_move_pct'))}%\n"
                f"Historical Avg Earnings Move: {self._fmt_float(r.get('historical_avg_move_pct'))}%\n"
                f"Edge Score (Hist - Implied): {self._fmt_float(r.get('edge_score'))}% — {verdict}\n"
                f"Options Liquidity (ATM Open Interest): {r.get('options_volume', 'N/A')}\n"
                f"Data as of: {last_updated or 'N/A'}{staleness_note}"
            )
        except Exception as e:
            logger.error(f"[AI ENGINE] Failed to fetch options volatility for {ticker}: {e}")
            return "Earnings volatility data unavailable."

    def _get_xray_context(self, ticker: str) -> str:
        """
        Pulls portfolio-level risk data from the three X-ray tables:
        beta + annualised vol (xray_risk_cache), top portfolio correlations
        (xray_correlation_matrix), and dividend yield (xray_dividend_cache).

        Only surfaces when data is present; degrades gracefully otherwise so it
        never blocks prompt generation for tickers not yet analysed by the X-ray engine.
        """
        lines: List[str] = []

        try:
            with self._db() as cursor:
                # --- Beta + annualised vol vs benchmark (SWDA.L / iShares MSCI World) ---
                cursor.execute("""
                    SELECT beta, annualized_vol, benchmark, last_updated
                    FROM xray_risk_cache
                    WHERE ticker = ?
                    ORDER BY last_updated DESC
                    LIMIT 1
                """, (ticker,))
                risk_row = cursor.fetchone()
                if risk_row:
                    r = dict(risk_row)
                    lines.append(
                        f"Portfolio Beta vs {r.get('benchmark', 'benchmark')}: "
                        f"{self._fmt_float(r.get('beta'), 3)} | "
                        f"Annualised Vol: {self._fmt_pct(r.get('annualized_vol'), 1)}"
                    )

                # --- Top portfolio correlations (parse JSON matrix) ---
                cursor.execute("""
                    SELECT tickers_json, matrix_json
                    FROM xray_correlation_matrix
                    ORDER BY last_updated DESC
                    LIMIT 1
                """)
                corr_row = cursor.fetchone()
                if corr_row:
                    tickers = json.loads(corr_row["tickers_json"])
                    matrix = json.loads(corr_row["matrix_json"])
                    if ticker in tickers:
                        idx = tickers.index(ticker)
                        row_corrs = [
                            (tickers[j], matrix[idx][j])
                            for j in range(len(tickers))
                            if j != idx and tickers[j] != ticker
                        ]
                        row_corrs.sort(key=lambda x: abs(x[1]), reverse=True)
                        top_corrs = row_corrs[:3]
                        if top_corrs:
                            corr_strs = ", ".join(
                                f"{t} ({v:+.2f})" for t, v in top_corrs
                            )
                            lines.append(f"Top Portfolio Correlations: {corr_strs}")

                # --- Dividend yield ---
                cursor.execute("""
                    SELECT dividend_yield_pct, dividend_in_base_currency
                    FROM xray_dividend_cache
                    WHERE ticker = ?
                    ORDER BY last_updated DESC
                    LIMIT 1
                """, (ticker,))
                div_row = cursor.fetchone()
                if div_row:
                    d = dict(div_row)
                    lines.append(
                        f"Dividend Yield: {self._fmt_float(d.get('dividend_yield_pct'), 2)}% | "
                        f"Annual Dividend (base CCY): {self._fmt_float(d.get('dividend_in_base_currency'), 2)}"
                    )

        except Exception as e:
            logger.warning(f"[AI ENGINE] Could not load X-ray context for {ticker}: {e}")

        if not lines:
            return "X-ray portfolio risk data not yet computed for this ticker."
        return "\n".join(lines)

    # ------------------------------------------------------------------ #
    # Master prompt assembly
    # ------------------------------------------------------------------ #
    _KNOWN_MODES = frozenset({
        "The Devil's Advocate analysis",
        "Risk/Reward Audit",
        "Quantamental Deep-Dive",
        "Earnings Strategy",
    })

    def generate_prompt(
        self,
        ticker: str,
        mode: Literal[
            "The Devil's Advocate analysis",
            "Risk/Reward Audit",
            "Quantamental Deep-Dive",
            "Earnings Strategy",
        ],
    ) -> Optional[str]:
        """
        Compiles the master prompt string based on the requested analysis mode.

        Returns None only when the core stock_signals record is missing — every
        auxiliary context block degrades gracefully to an 'unavailable' note.

        Results are cached by (ticker, mode, trading_date) and auto-invalidated
        at midnight so repeated chat turns in the same session skip all DB I/O.
        """
        cache_key = self._cache_key(ticker, mode)
        if cache_key in self._prompt_cache:
            logger.debug(f"[AI ENGINE] Cache hit for {ticker}/{mode}")
            return self._prompt_cache[cache_key]

        # 1. Fetch Core Database Record & Advanced Metrics
        stock_row = None
        try:
            with self._db() as cursor:
                cursor.execute("""
                    SELECT s.*,
                           q.ml_confidence_score,
                           q.var_95,
                           q.cvar_95,
                           q.sentiment_score
                    FROM stock_signals s
                    LEFT JOIN quant_signals q ON s.ticker = q.ticker
                        AND q.date = (SELECT MAX(date) FROM quant_signals WHERE ticker = s.ticker)
                    WHERE s.ticker = ?
                """, (ticker,))
                stock_row = cursor.fetchone()
        except Exception as e:
            logger.error(f"[AI ENGINE] Core record query failed for {ticker}: {e}")

        if not stock_row:
            logger.warning(f"[AI ENGINE] No stock_signals record found for {ticker}; cannot build prompt.")
            return None

        stock_data: Dict[str, Any] = dict(stock_row)

        # 2. Fetch Auxiliary Context (each independently fault-tolerant)
        portfolio_data = self._get_portfolio_context(ticker)
        technicals = self._get_technical_indicators(ticker)
        factors = self._get_momentum_factor_stack(ticker)
        macro_context = self._get_macro_context()
        peer_context = self._get_sector_peer_context(ticker, stock_data.get('sector'))
        options_context = self._get_options_volatility(ticker)
        xray_context = self._get_xray_context(ticker)
        clean_notes = self._clean_html(stock_data.get('educational_notes', ''))

        # 3. Safe Formatting Block
        rev_growth_str = self._fmt_pct(stock_data.get('revenue_growth'))
        trailing_pe_str = self._fmt_float(stock_data.get('trailing_pe'))
        pl_peg_str = self._fmt_float(stock_data.get('peter_lynch_peg'))
        debt_str = self._fmt_float(stock_data.get('debt_to_equity'))
        beta_str = self._fmt_float(stock_data.get('beta'))

        low_52 = self._fmt_float(stock_data.get('fifty_two_week_low'))
        high_52 = self._fmt_float(stock_data.get('fifty_two_week_high'))

        # Advanced Engine Metrics Formatting
        ml_conf_str = self._fmt_float(stock_data.get('ml_confidence_score'), 1)
        ml_conf_str = f"{ml_conf_str}%" if ml_conf_str != "N/A" else "N/A"
        var_str = self._fmt_pct(stock_data.get('var_95'), 2)
        cvar_str = self._fmt_pct(stock_data.get('cvar_95'), 2)
        sentiment_str = self._fmt_float(stock_data.get('sentiment_score'), 3)

        # --- CACHED EXCHANGE RATE (Native -> Base), no live network call ---
        stock_currency = stock_data.get('currency')
        try:
            exchange_rate = get_rate_to_base(stock_currency)
        except Exception as e:
            logger.warning(f"[AI ENGINE] FX lookup failed for {stock_currency}; defaulting to 1.0: {e}")
            exchange_rate = 1.0

        # get_rate_to_base returns NATIVE->BASE; the portfolio math below needs
        # BASE->NATIVE to express VWAP back in the stock's own currency.
        base_to_native = (1.0 / exchange_rate) if exchange_rate and exchange_rate > 0 else 1.0

        # 4. Format Portfolio String
        portfolio_str = "No active holdings in the current portfolio."
        if portfolio_data and portfolio_data.get('global_shares', 0) > 0:
            global_shares = portfolio_data.get('global_shares', 0)

            # Apply FX Conversion to VWAP (Base -> Native)
            global_vwap_base = portfolio_data.get('global_buy_price', 0)
            vwap_native = global_vwap_base * base_to_native

            # Re-scale if native is LSE pence (GBp)
            if portfolio_data.get('price_in_pence', False):
                vwap_native *= 100

            curr_price = stock_data.get('current_price') or 0.0

            # Math
            cost_basis = global_shares * vwap_native
            current_value = global_shares * curr_price
            pnl = current_value - cost_basis
            pnl_pct = (pnl / cost_basis) * 100 if cost_basis > 0 else 0

            portfolio_str = (
                f"User currently holds {global_shares} shares.\n"
                f"Global VWAP (Cost Basis): {vwap_native:,.2f} {stock_currency}.\n"
                f"Current Value: {current_value:,.2f} {stock_currency}.\n"
                f"Unrealized P&L: {pnl:,.2f} {stock_currency} ({pnl_pct:.2f}%).\n"
                f"CRITICAL INSTRUCTION: Do NOT recalculate these P&L numbers. Use them exactly as stated.\n"
            )

            accounts = portfolio_data.get('accounts', [])
            if len(accounts) > 1:
                portfolio_str += "\nThis holding is split across the following micro-ledgers:\n"
                for acc in accounts:
                    acc_buy_base = acc.get('buy_price', 0)
                    acc_buy_native = acc_buy_base * base_to_native
                    if portfolio_data.get('price_in_pence', False):
                        acc_buy_native *= 100
                    portfolio_str += (
                        f"  - {acc.get('name', 'Unknown')}: {acc.get('shares', 0)} shares "
                        f"at {acc_buy_native:,.2f} {stock_currency}\n"
                    )

        # --- GET CURRENT SYSTEM DATE ---
        current_date_str = now_local().strftime("%Y-%m-%d")

        # Safe core price formatting
        current_price = stock_data.get('current_price')
        current_price_str = f"{current_price:,.2f}" if current_price is not None else "N/A"
        atr_stop = stock_data.get('atr_stop_loss')
        atr_stop_str = f"{atr_stop:,.2f}" if atr_stop is not None else "N/A"
        composite_score = stock_data.get('composite_score', 'N/A')
        overall_signal = stock_data.get('overall_signal', 'UNKNOWN')

        # 5. Build The Master Context Payload
        context_payload = f"""
=========================================================
SYSTEM METADATA & SCORING LOGIC
=========================================================
Current System Date: {current_date_str}
The Quantamental System scores assets from -100 to 100.
- Scores >= 40: STRONG BUY
- Scores >= 20: BULLISH / HOLD
- Scores >= 0:  NEUTRAL
- Scores >= -30: BEARISH / CAUTION
- Scores >= -60: STRONG SELL
- Scores < -60: TOXIC / AVOID
The score is a weighted aggregation of Moving Average alignment (5D/10D/21D/50D/200D), RSI momentum, On-Balance Volume (OBV), and MACD Reversals. It overlays Mark Minervini's Volatility Contraction Pattern (VCP) and hierarchical candlestick recognition.

=========================================================
MACRO REGIME & SYSTEMIC BACKDROP
=========================================================
{macro_context}

=========================================================
USER PORTFOLIO CONTEXT
=========================================================
{portfolio_str}

=========================================================
X-RAY PORTFOLIO RISK METRICS
=========================================================
{xray_context}

=========================================================
ASSET DATA: {stock_data.get('company_name', ticker)} ({stock_data.get('ticker', ticker)})
=========================================================
Sector: {stock_data.get('sector', 'Unknown')}
Current Price: {current_price_str} {stock_currency}
52-Week Range: {low_52} - {high_52}
System Verdict: {overall_signal} (Score: {composite_score}/100)
ATR Stop-Loss: {atr_stop_str} {stock_currency}

--- AI, RISK & SENTIMENT ---
ML Confidence Score (>3% return in 5d): {ml_conf_str}
Parametric VaR (95%): {var_str}
Conditional Log-Return CVaR (95% Tail Risk): {cvar_str}
VADER Media Sentiment: {sentiment_str}

--- FUNDAMENTALS & RISK ---
Wall Street Analyst Rating: {stock_data.get('analyst_rating', 'Unknown')}
Beta (Volatility vs Market): {beta_str}
Trailing P/E: {trailing_pe_str}
Peter Lynch Fair Value PEG: {pl_peg_str}
Debt-to-Equity: {debt_str}
Revenue Growth (YoY): {rev_growth_str}
Next Earnings Date: {stock_data.get('next_earnings_date', 'N/A')}

--- MOMENTUM FACTOR STACK ---
1-Month Momentum: {factors['mom_1m']}
3-Month Momentum: {factors['mom_3m']}
6-Month Momentum: {factors['mom_6m']}
12-Month Momentum (skip recent month): {factors['mom_12m_skip1m']}
20-Day Historical Volatility: {factors['hist_vol_20']}
ATR (% of price): {factors['atr_pct']}
Relative Strength vs Market (5D): {factors['rel_strength_5d']}
Relative Strength vs Market (20D): {factors['rel_strength_20d']}

--- SECTOR / PEER RELATIVE STRENGTH ---
{peer_context}

--- TECHNICAL TRAJECTORY (last 5 sessions) ---
Price Path: {technicals['price_path']} {stock_currency}
50-Day Trend: {stock_data.get('trend_50d', 'N/A')}
200-Day Trend: {stock_data.get('trend_200d', 'N/A')}
RSI (14) Path: {technicals['rsi_path']}
MACD Line Path: {technicals['macd_path']}
MACD Signal Path: {technicals['macd_signal_path']}
MACD Histogram Path: {technicals['macd_hist_path']}
ADX Trend Strength (14): {technicals['adx']}
Bollinger Band Position (20,2σ): {technicals['bb_pband']}
Bollinger Band Width: {technicals['bb_wband']}
OBV Trend: {technicals['obv_trend']}
Volume Path: {technicals['volume_path']} (21D Avg: {technicals['average_volume']})
Volume Contraction Ratio (5D vs prior 20D): {technicals['volume_contraction_ratio']}

--- OPTIONS / EARNINGS VOLATILITY EDGE ---
{options_context}

--- ALGORITHMIC BREAKDOWN ---
{clean_notes}
=========================================================
"""

        # 6. Wrap in the Specific Prompt Mode
        prompt_wrapper = ""

        if mode == "The Devil's Advocate analysis":
            prompt_wrapper = f"""
You are an elite-level, highly analytical Wall Street Risk Manager.
Review the Quantamental context provided below. Your job is to act as the "Devil's Advocate" against the system's "{overall_signal}" verdict.

IMPORTANT: You must acknowledge the stock's Sector, its Beta (stability/risk), and any obvious macroeconomic tailwinds (e.g., AI/Semiconductor supercycles, Blue-Chip stability) that explain its current valuation or momentum. Do not blindly dismiss a strong trend.
However, once you have acknowledged the narrative, carefully point out the mathematical exhaustion risks, valuation traps, or mean-reversion dangers. Be critical, balanced, and professional.

{context_payload}
"""
        elif mode == "Risk/Reward Audit":
            prompt_wrapper = f"""
You are an elite-level Financial Risk Manager.
Review the Quantamental context provided below. Focus heavily on the ATR Stop-Loss, the user's specific Unrealized P&L, the Parametric Value at Risk (VaR), the systemic Macro yield threat, and the 52-Week Range.
Calculate the mathematical risk buffer between the current price, the user's entry, and the ATR floor. Suggest position sizing, profit-taking, or tightening stops based on the current volatility (Beta, 20-Day Hist Vol, ATR%), VaR, the macro regime, and RSI trajectory.

{context_payload}
"""
        elif mode == "Quantamental Deep-Dive":
            prompt_wrapper = f"""
You are an elite-level Hedge Fund Strategist.
Review the Quantamental context provided below. Synthesize the fundamental metrics (e.g., Peter Lynch PEG, Sector, Growth) with the technical trajectory (e.g., RSI/MACD paths, Volume Contraction, 52-Week Range, VCP Breakouts, MAs), the momentum factor stack, the sector relative-strength ranking, and the Machine Learning/Sentiment vectors.
Determine if the fundamental "story" of the business validates the current mathematical price action on the chart, and whether the stock is leading or lagging its sector. Provide a comprehensive 12-month conviction rating.

{context_payload}
"""
        elif mode == "Earnings Strategy":
            prompt_wrapper = f"""
You are a Senior Options & Volatility Analyst.
Review the Quantamental context provided below, paying special attention to the approaching earnings date, the Options/Earnings Volatility Edge (implied vs historical move and the edge score), the stock's Beta, its Expected Shortfall (CVaR), and its Unrealized P&L.
Based on the current technical extensions (RSI/MACD trajectory, MAs), the options mispricing edge, and fundamental valuation, outline a strategic playbook. Should the user hold through earnings, trim the position to lock in gains, or hedge? If there is a clear options edge, explain the trade structure. Explain your logic clearly.

{context_payload}
"""
        else:
            logger.warning(f"[AI ENGINE] Unknown analysis mode '{mode}'; falling back to generic prompt.")
            prompt_wrapper = f"""
You are an elite-level Stock Market Analyst. Review the following data and provide an institutional-grade assessment.

{context_payload}
"""

        result = prompt_wrapper.strip()
        self._prompt_cache[cache_key] = result
        return result