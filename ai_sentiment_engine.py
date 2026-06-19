# GUI name: "Market Sentiment". Prompt builder for /api/ai-prompt/market-sentiment/{us|uk}.
import json
import logging
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from typing import Dict, Optional

from database import get_connection

logger = logging.getLogger(__name__)

_ALLOWED_US_MODES = frozenset([
    "US Market Health Check",
    "This Week's US Risk Events",
    "Recession Radar",
    "Inflation & Rate Impact",
])

_ALLOWED_UK_MODES = frozenset([
    "UK Market Health Check",
    "This Week's UK Risk Events",
    "Pound & Gilt Impact",
    "UK vs US Comparison",
])

_US_PERSONA = {
    "US Market Health Check": (
        "Senior market strategist. "
        "One-paragraph plain-English verdict, then the 3 most important numbers and what each "
        "means for an ordinary investor."
    ),
    "This Week's US Risk Events": (
        "Event-risk analyst. "
        "Rank upcoming events by miss-probability. For the top 2, explain in plain English what "
        "a surprise up or down would mean for US stocks."
    ),
    "Recession Radar": (
        "Macro economist. "
        "Using yield curve, credit spread, VIX, and HMM state together, give a plain-English "
        "assessment: expansion, slowdown, or danger? Back each claim with one number."
    ),
    "Inflation & Rate Impact": (
        "Fixed-income strategist. "
        "With CPI and yields at current levels, explain in 3 bullets what this means for: "
        "(a) growth/tech, (b) defensive/dividend, (c) bonds. No jargon."
    ),
}

_UK_PERSONA = {
    "UK Market Health Check": (
        "Senior UK market strategist. "
        "One-paragraph plain-English verdict on UK market health, then the 3 most important "
        "numbers and what each means for an ordinary UK investor."
    ),
    "This Week's UK Risk Events": (
        "UK event-risk analyst. "
        "Rank upcoming UK macro events by miss-probability. For the top 2, explain in plain "
        "English what a surprise up or down would mean for FTSE 100 investors."
    ),
    "Pound & Gilt Impact": (
        "UK fixed-income and FX strategist. "
        "With GBP/USD and 10-year gilt yield at current levels, explain in 3 bullets what this "
        "means for: (a) FTSE 100 exporters, (b) UK domestic stocks, (c) UK bond holders. No jargon."
    ),
    "UK vs US Comparison": (
        "Global macro strategist. "
        "Given the US and UK data below, compare both markets side by side and explain in plain "
        "English which looks more attractive for a long-only equity investor and why."
    ),
}


class AISentimentPromptEngine:
    def __init__(self) -> None:
        self._cache: Dict[tuple, str] = {}
        self._cache_date: str = ""

    def _cache_key(self, region: str, mode: str) -> tuple:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._cache_date:
            self._cache.clear()
            self._cache_date = today
        return (f"sentiment-{region}", mode, today)

    @contextmanager
    def _db(self):
        conn = None
        try:
            conn = get_connection()
            yield conn.cursor()
        finally:
            if conn:
                conn.close()

    @staticmethod
    def _fmt(val, decimals: int = 2, suffix: str = "") -> str:
        if val is None:
            return "N/A"
        try:
            return f"{val:.{decimals}f}{suffix}"
        except (TypeError, ValueError):
            return "N/A"

    def _gather_us_data(self) -> dict:
        data: dict = {}
        with self._db() as cur:
            try:
                cur.execute(
                    "SELECT us_regime_label, us_turbulence, ai_hmm_state, vix_close "
                    "FROM market_regimes ORDER BY date DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    data["regime"] = dict(row)
            except Exception as e:
                logger.error("ai_sentiment_engine: market_regimes (US) query failed: %s", e)

            try:
                cur.execute(
                    "SELECT us_cpi_inflation, us_yield_curve, us_high_yield_spread, us_m2 "
                    "FROM macro_indicators ORDER BY date DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    data["indicators"] = dict(row)
            except Exception as e:
                logger.error("ai_sentiment_engine: macro_indicators (US) query failed: %s", e)

            try:
                cur.execute(
                    "SELECT tnx_close, tyx_close, dxy_close, us_threat_level, "
                    "us_yield_velocity, yield_curve_inverted, days_inverted "
                    "FROM macro_regimes ORDER BY date DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    data["macro_regime"] = dict(row)
            except Exception as e:
                logger.error("ai_sentiment_engine: macro_regimes (US) query failed: %s", e)

            try:
                cutoff = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                cur.execute(
                    "SELECT event_name, event_date, forecast_val, previous_val, "
                    "ai_consensus_miss_prob, ai_volatility_warning "
                    "FROM macro_calendar "
                    "WHERE currency='USD' AND is_event_passed=0 AND event_date BETWEEN ? AND ? "
                    "ORDER BY ai_consensus_miss_prob DESC LIMIT 5",
                    (now_str, cutoff),
                )
                data["us_events"] = [dict(r) for r in cur.fetchall()]
            except Exception as e:
                logger.error("ai_sentiment_engine: macro_calendar (USD) query failed: %s", e)
                data["us_events"] = []

            try:
                cur.execute(
                    "SELECT alert_fired, leader_count, etf_count, payload_json "
                    "FROM ai_contagion_snapshots ORDER BY id DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    d = dict(row)
                    payload = {}
                    if d.get("payload_json"):
                        try:
                            payload = json.loads(d["payload_json"])
                        except Exception:
                            pass
                    data["contagion"] = {
                        "alert_fired": d.get("alert_fired"),
                        "leader_count": d.get("leader_count"),
                        "etf_count": d.get("etf_count"),
                        "severity_score": payload.get("severity_score"),
                    }
            except Exception as e:
                logger.error("ai_sentiment_engine: ai_contagion_snapshots query failed: %s", e)

        return data

    def _gather_uk_data(self) -> dict:
        data: dict = {}
        with self._db() as cur:
            try:
                cur.execute(
                    "SELECT uk_regime_label, uk_turbulence, ftse_volatility "
                    "FROM market_regimes ORDER BY date DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    data["regime"] = dict(row)
            except Exception as e:
                logger.error("ai_sentiment_engine: market_regimes (UK) query failed: %s", e)

            try:
                cur.execute(
                    "SELECT uk_cpi_inflation, uk_corporate_spread, uk_m4 "
                    "FROM macro_indicators ORDER BY date DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    data["indicators"] = dict(row)
            except Exception as e:
                logger.error("ai_sentiment_engine: macro_indicators (UK) query failed: %s", e)

            try:
                cur.execute(
                    "SELECT uk_gilt_close, gbpusd_close, uk_threat_level, uk_yield_velocity "
                    "FROM macro_regimes ORDER BY date DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    data["macro_regime"] = dict(row)
            except Exception as e:
                logger.error("ai_sentiment_engine: macro_regimes (UK) query failed: %s", e)

            try:
                cutoff = (datetime.now(timezone.utc) + timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
                now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
                cur.execute(
                    "SELECT event_name, event_date, forecast_val, previous_val, "
                    "ai_consensus_miss_prob, ai_volatility_warning "
                    "FROM macro_calendar "
                    "WHERE currency='GBP' AND is_event_passed=0 AND event_date BETWEEN ? AND ? "
                    "ORDER BY ai_consensus_miss_prob DESC LIMIT 5",
                    (now_str, cutoff),
                )
                data["uk_events"] = [dict(r) for r in cur.fetchall()]
            except Exception as e:
                logger.error("ai_sentiment_engine: macro_calendar (GBP) query failed: %s", e)
                data["uk_events"] = []

        return data

    def _fmt_events(self, events: list) -> str:
        if not events:
            return "  No events found in the next 7 days."
        lines = []
        for ev in events:
            miss_prob = ev.get("ai_consensus_miss_prob")
            miss_str = f"{miss_prob:.1%}" if miss_prob is not None else "N/A"
            lines.append(
                f"  - {ev.get('event_name', 'Unknown')} on {ev.get('event_date', '?')}: "
                f"forecast={ev.get('forecast_val', 'N/A')}, prev={ev.get('previous_val', 'N/A')}, "
                f"surprise-prob={miss_str}"
            )
        return "\n".join(lines)

    def _build_us_block(self, us: dict) -> str:
        regime = us.get("regime", {})
        ind = us.get("indicators", {})
        macro = us.get("macro_regime", {})
        contagion = us.get("contagion", {})
        events_str = self._fmt_events(us.get("us_events", []))

        yield_inverted = "Yes" if macro.get("yield_curve_inverted") else "No"
        days_inv = macro.get("days_inverted")

        return f"""=== US MARKET DATA ===
Regime Label: {regime.get('us_regime_label') or 'N/A'}
Turbulence Index: {self._fmt(regime.get('us_turbulence'), 2)}
HMM Hidden Macro State: {regime.get('ai_hmm_state') if regime.get('ai_hmm_state') is not None else 'N/A'}
VIX (Fear Gauge): {self._fmt(regime.get('vix_close'), 2)}

US Macro Threat Level: {macro.get('us_threat_level') or 'N/A'}
10-Year Treasury Yield: {self._fmt(macro.get('tnx_close'), 3, '%')}
30-Year Treasury Yield: {self._fmt(macro.get('tyx_close'), 3, '%')}
DXY (US Dollar Index): {self._fmt(macro.get('dxy_close'), 2)}
Yield Curve Inverted: {yield_inverted}{(' for ' + str(days_inv) + ' days') if days_inv else ''}
US Yield Velocity: {self._fmt(macro.get('us_yield_velocity'), 4)}

US CPI Inflation (YoY): {self._fmt(ind.get('us_cpi_inflation'), 2, '%')}
US Yield Curve Spread: {self._fmt(ind.get('us_yield_curve'), 3, '%')}
US High-Yield Credit Spread: {self._fmt(ind.get('us_high_yield_spread'), 2, '%')}
US M2 Money Supply (latest): {self._fmt(ind.get('us_m2'), 0)}

AI Sector Contagion — Alert Fired: {contagion.get('alert_fired') or 'N/A'}, Leaders: {contagion.get('leader_count') or 'N/A'}, ETFs: {contagion.get('etf_count') or 'N/A'}, Severity: {self._fmt(contagion.get('severity_score'), 1)}

Upcoming US Macro Events (next 7 days, ranked by surprise probability):
{events_str}"""

    def _build_uk_block(self, uk: dict) -> str:
        regime = uk.get("regime", {})
        ind = uk.get("indicators", {})
        macro = uk.get("macro_regime", {})
        events_str = self._fmt_events(uk.get("uk_events", []))

        return f"""=== UK MARKET DATA ===
Regime Label: {regime.get('uk_regime_label') or 'N/A'}
Turbulence Index: {self._fmt(regime.get('uk_turbulence'), 2)}
FTSE Volatility: {self._fmt(regime.get('ftse_volatility'), 2, '%')}

UK Macro Threat Level: {macro.get('uk_threat_level') or 'N/A'}
10-Year Gilt Yield: {self._fmt(macro.get('uk_gilt_close'), 3, '%')}
GBP/USD: {self._fmt(macro.get('gbpusd_close'), 4)}
UK Yield Velocity: {self._fmt(macro.get('uk_yield_velocity'), 4)}

UK CPI Inflation (YoY): {self._fmt(ind.get('uk_cpi_inflation'), 2, '%')}
UK Corporate Credit Spread: {self._fmt(ind.get('uk_corporate_spread'), 2, '%')}
UK M4 Money Supply (latest): {self._fmt(ind.get('uk_m4'), 0)}

Upcoming UK Macro Events (next 7 days, ranked by surprise probability):
{events_str}"""

    def generate_us_prompt(self, mode: str) -> str:
        if mode not in _ALLOWED_US_MODES:
            raise ValueError(f"Unrecognised US mode: {mode}")

        key = self._cache_key("us", mode)
        if key in self._cache:
            return self._cache[key]

        us_data = self._gather_us_data()
        data_block = self._build_us_block(us_data)

        persona = _US_PERSONA[mode]
        prompt = (
            f"You are a {persona}\n\n"
            f"Below is live US market sentiment data from a personal investment dashboard. "
            f"Use it to answer the request.\n\n"
            f"{data_block}\n\n"
            f"Request: {mode}"
        )

        self._cache[key] = prompt
        return prompt

    def generate_uk_prompt(self, mode: str) -> str:
        if mode not in _ALLOWED_UK_MODES:
            raise ValueError(f"Unrecognised UK mode: {mode}")

        key = self._cache_key("uk", mode)
        if key in self._cache:
            return self._cache[key]

        uk_data = self._gather_uk_data()
        data_block = self._build_uk_block(uk_data)

        if mode == "UK vs US Comparison":
            us_data = self._gather_us_data()
            data_block = self._build_us_block(us_data) + "\n\n" + data_block

        persona = _UK_PERSONA[mode]
        prompt = (
            f"You are a {persona}\n\n"
            f"Below is live UK market sentiment data from a personal investment dashboard. "
            f"Use it to answer the request.\n\n"
            f"{data_block}\n\n"
            f"Request: {mode}"
        )

        self._cache[key] = prompt
        return prompt
