# GUI name: "Market Regime HMM". Prompt builder for /api/ai-prompt/market-regime.
import logging
from contextlib import contextmanager
from datetime import datetime, timezone
from typing import Dict, Optional

from database import get_connection

logger = logging.getLogger(__name__)

_ALLOWED_MODES = frozenset([
    "Plain English Briefing",
    "What Happens Next?",
    "How Should I Position?",
    "Red Flags Check",
])

_PERSONA = {
    "Plain English Briefing": (
        "Patient financial educator. "
        "Explain the current regime in plain English for a novice in 3 short paragraphs. No jargon."
    ),
    "What Happens Next?": (
        "Probabilistic analyst. "
        "Using the transition matrix, explain the most likely next shift, when it might happen, "
        "and what a novice should watch for."
    ),
    "How Should I Position?": (
        "Cautious portfolio advisor. "
        "Based on regime stats and the current regime, give 3 concrete plain-English portfolio "
        "actions a novice could take today."
    ),
    "Red Flags Check": (
        "Market risk monitor. "
        "Identify 2–3 early-warning signs the regime may shift. Explain each in plain English. "
        "Rate overall risk LOW / MEDIUM / HIGH."
    ),
}


class AIRegimePromptEngine:
    def __init__(self) -> None:
        self._cache: Dict[tuple, str] = {}
        self._cache_date: str = ""

    def _cache_key(self, mode: str) -> tuple:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if today != self._cache_date:
            self._cache.clear()
            self._cache_date = today
        return ("market-regime", mode, today)

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

    def _gather_data(self) -> dict:
        data: dict = {}
        with self._db() as cur:
            try:
                cur.execute(
                    "SELECT price_hmm_state, price_hmm_label, price_hmm_prob, date, "
                    "vix_close, spy_volatility FROM market_regimes ORDER BY date DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    data["regime"] = dict(row)
            except Exception as e:
                logger.error("ai_regime_engine: market_regimes query failed: %s", e)

            try:
                cur.execute(
                    "SELECT date, price_hmm_state, price_hmm_label, price_hmm_prob "
                    "FROM market_regimes WHERE price_hmm_state IS NOT NULL ORDER BY date DESC LIMIT 1825"
                )
                rows = [dict(r) for r in cur.fetchall()]
                data["regime_daily"] = list(reversed(rows))
            except Exception as e:
                logger.error("ai_regime_engine: regime_daily query failed: %s", e)
                data["regime_daily"] = []

            try:
                cur.execute(
                    "SELECT us_threat_level, tnx_close, tyx_close, yield_curve_inverted, days_inverted "
                    "FROM macro_regimes ORDER BY date DESC LIMIT 1"
                )
                row = cur.fetchone()
                if row:
                    data["macro"] = dict(row)
            except Exception as e:
                logger.error("ai_regime_engine: macro_regimes query failed: %s", e)

        return data

    def _compute_transition_matrix(self, regime_daily: list) -> dict:
        """Compute empirical 3×3 transition matrix from consecutive daily state pairs."""
        state_labels = {0: "Bull", 1: "Chop", 2: "Crash"}
        counts: Dict[int, Dict[int, int]] = {0: {0: 0, 1: 0, 2: 0}, 1: {0: 0, 1: 0, 2: 0}, 2: {0: 0, 1: 0, 2: 0}}
        for i in range(len(regime_daily) - 1):
            s_from = regime_daily[i].get("price_hmm_state")
            s_to = regime_daily[i + 1].get("price_hmm_state")
            if s_from is not None and s_to is not None and s_from in counts and s_to in counts[s_from]:
                counts[s_from][s_to] += 1

        matrix_lines = []
        for s_from in [0, 1, 2]:
            row_total = sum(counts[s_from].values())
            if row_total == 0:
                matrix_lines.append(f"  {state_labels[s_from]}: no data")
                continue
            parts = []
            for s_to in [0, 1, 2]:
                prob = counts[s_from][s_to] / row_total
                parts.append(f"→{state_labels[s_to]} {prob:.1%}")
            matrix_lines.append(f"  {state_labels[s_from]}: {', '.join(parts)}")
        return {"text": "\n".join(matrix_lines)}

    def _days_since_last_transition(self, regime_daily: list) -> Optional[int]:
        if len(regime_daily) < 2:
            return None
        current_state = regime_daily[-1].get("price_hmm_state")
        for i in range(len(regime_daily) - 2, -1, -1):
            if regime_daily[i].get("price_hmm_state") != current_state:
                try:
                    last_change = datetime.strptime(regime_daily[i + 1]["date"], "%Y-%m-%d")
                    return (datetime.now(timezone.utc).date() - last_change.date()).days
                except Exception:
                    return None
        return None

    def generate_prompt(self, mode: str) -> str:
        if mode not in _ALLOWED_MODES:
            raise ValueError(f"Unrecognised mode: {mode}")

        key = self._cache_key(mode)
        if key in self._cache:
            return self._cache[key]

        data = self._gather_data()
        regime = data.get("regime", {})
        macro = data.get("macro", {})
        regime_daily = data.get("regime_daily", [])
        transition = self._compute_transition_matrix(regime_daily)
        days_stable = self._days_since_last_transition(regime_daily)

        cur_state = regime.get("price_hmm_label") or "Unknown"
        cur_prob = self._fmt(regime.get("price_hmm_prob"), 1, "")
        cur_date = regime.get("date") or "unknown date"
        vix = self._fmt(regime.get("vix_close"), 2)
        spy_vol = self._fmt(regime.get("spy_volatility"), 2, "%")
        us_threat = macro.get("us_threat_level") or "N/A"
        tnx = self._fmt(macro.get("tnx_close"), 3, "%")
        tyx = self._fmt(macro.get("tyx_close"), 3, "%")
        yield_inverted = "Yes" if macro.get("yield_curve_inverted") else "No"
        days_inverted = macro.get("days_inverted")
        days_inv_str = str(days_inverted) if days_inverted is not None else "N/A"
        days_stable_str = str(days_stable) if days_stable is not None else "N/A"

        data_block = f"""=== MARKET REGIME SNAPSHOT ===
Date: {cur_date}
Current HMM Regime: {cur_state}
Regime Confidence: {cur_prob}
Days in Current Regime: {days_stable_str}
VIX (Fear Gauge): {vix}
SPY Annualised Volatility: {spy_vol}

=== EMPIRICAL REGIME TRANSITION PROBABILITIES ===
(Probability of moving FROM each state TO each state, based on 5-year daily history)
{transition["text"]}

=== MACRO CONTEXT ===
US Macro Threat Level: {us_threat}
10-Year Treasury Yield (TNX): {tnx}
30-Year Treasury Yield (TYX): {tyx}
Yield Curve Inverted: {yield_inverted}
Days Yield Curve Has Been Inverted: {days_inv_str}
"""

        persona = _PERSONA[mode]
        prompt = (
            f"You are a {persona}\n\n"
            f"Below is live quantitative market regime data from a personal investment dashboard. "
            f"Use it to answer the request.\n\n"
            f"{data_block}\n"
            f"Request: {mode}"
        )

        self._cache[key] = prompt
        return prompt
