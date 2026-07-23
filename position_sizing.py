"""
Risk-parity sizing: shares = floor((account*risk_pct/100) / (entry*atr_pct*stop_multiple*fx_rate_to_base)).
Mirrors the JS implementation in watchlist/portfolio tables — both must stay in sync.
"""

import logging
from typing import Dict, Optional

from database import get_connection

logger = logging.getLogger(__name__)

DEFAULT_CONFIG = {
    "ACCOUNT_VALUE":    10000.0,   # In user's base currency
    "RISK_PCT":         1.0,       # Percent of account risked per trade (1.0 = 1%)
    "STOP_MULTIPLE":    2.0,       # ATR multiples to the stop loss
    "MIN_RISK_REWARD":  1.5,       # Minimum reward:risk ratio required by passes_risk_reward_gate()
}


def get_position_sizing_config(config_data: dict) -> dict:
    """Returns POSITION_SIZING block from config with defaults filled in."""
    user_cfg = config_data.get("POSITION_SIZING", {}) or {}
    return {
        "ACCOUNT_VALUE":   float(user_cfg.get("ACCOUNT_VALUE",   DEFAULT_CONFIG["ACCOUNT_VALUE"])),
        "RISK_PCT":        float(user_cfg.get("RISK_PCT",        DEFAULT_CONFIG["RISK_PCT"])),
        "STOP_MULTIPLE":   float(user_cfg.get("STOP_MULTIPLE",   DEFAULT_CONFIG["STOP_MULTIPLE"])),
        "MIN_RISK_REWARD": float(user_cfg.get("MIN_RISK_REWARD", DEFAULT_CONFIG["MIN_RISK_REWARD"])),
    }


def calculate_position_size(
    account_value:    float,
    entry_price:      float,
    atr_pct:          Optional[float],
    fx_rate_to_base:  float = 1.0,
    risk_pct:         float = 1.0,
    stop_multiple:    float = 2.0,
) -> Dict[str, Optional[float]]:
    """Returns position size dict (shares/stop_price/risk in base ccy); all-None on invalid inputs."""
    null_result = {
        "shares":               None,
        "position_value":       None,
        "stop_price":           None,
        "risk_amount":          None,
        "risk_per_share":       None,
        "risk_per_share_native": None,
    }

    # Validation — fail soft, return nulls
    if (atr_pct is None
            or entry_price is None
            or entry_price <= 0
            or atr_pct <= 0
            or account_value is None
            or account_value <= 0
            or fx_rate_to_base is None
            or fx_rate_to_base <= 0):
        return null_result

    risk_fraction = risk_pct / 100.0
    risk_capital_base = account_value * risk_fraction
    risk_per_share_native = entry_price * atr_pct * stop_multiple
    risk_per_share_base = risk_per_share_native * fx_rate_to_base

    if risk_per_share_base <= 0:
        return null_result

    # Freetrade supports fractional but floor gives a conservative position size
    shares = int(risk_capital_base // risk_per_share_base)

    stop_price_native = entry_price - (entry_price * atr_pct * stop_multiple)
    position_value_base = shares * entry_price * fx_rate_to_base
    actual_risk_base    = shares * risk_per_share_base

    return {
        "shares":                shares,
        "position_value":        round(position_value_base, 2),
        "stop_price":            round(stop_price_native, 2),
        "risk_amount":           round(actual_risk_base, 2),
        "risk_per_share":        round(risk_per_share_base, 4),
        "risk_per_share_native": round(risk_per_share_native, 4),
    }


def _latest_atr_pct_batch(tickers: list, conn) -> Dict[str, float]:
    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""SELECT qs.ticker, qs.atr_pct FROM quant_signals qs
            WHERE qs.ticker IN ({placeholders}) AND qs.atr_pct IS NOT NULL
              AND qs.date = (
                  SELECT MAX(qs2.date) FROM quant_signals qs2
                  WHERE qs2.ticker = qs.ticker AND qs2.atr_pct IS NOT NULL
              )""",
        tickers,
    ).fetchall()
    return {row["ticker"]: row["atr_pct"] for row in rows}


def _bullish_measured_targets_batch(tickers: list, conn) -> Dict[str, float]:
    """Best available take-profit target from a CONFIRMED pattern whose own family/pattern_type
    resolves to an 'up' direction, read through the pattern_detection_engine.DETECTORS registry
    — never a hardcoded family list, same convention as score_analysis's Technical pillar. A
    ticker can carry more than one simultaneous confirmed pattern across families; the most
    optimistic (highest) qualifying measured_target is used."""
    from pattern_detection_engine import DETECTORS

    if not tickers:
        return {}
    placeholders = ",".join("?" * len(tickers))
    rows = conn.execute(
        f"""SELECT ticker, pattern_family, pattern_type, measured_target FROM pattern_detection_results
            WHERE ticker IN ({placeholders}) AND phase='CONFIRMED' AND measured_target IS NOT NULL""",
        tickers,
    ).fetchall()
    targets: Dict[str, float] = {}
    for row in rows:
        module = DETECTORS.get(row["pattern_family"])
        if module is None:
            continue
        if module.PATTERN_TYPES.get(row["pattern_type"]) != "up":
            continue
        ticker = row["ticker"]
        if ticker not in targets or row["measured_target"] > targets[ticker]:
            targets[ticker] = row["measured_target"]
    return targets


def passes_risk_reward_gate_batch(tickers: list, min_rr: Optional[float] = None) -> Dict[str, Optional[dict]]:
    """Recommendation Risk/Reward Gate (Buy-Signal Confluence Pipeline Part D): gates whether a
    ticker's setup clears a minimum reward:risk ratio before it can be labeled a Buy
    Recommendation, computed entirely from this app's existing ATR-based stop-loss math — no
    Kelly Criterion, deliberately (this app already rejected Kelly sizing in favor of
    fixed-fractional ATR sizing — see the Position Sizing glossary entry — because Kelly's
    'optimal' sizing is too aggressive when the edge estimate isn't reliable; this reuses that
    same rationale rather than reopening it).

    Entry: stock_signals.current_price. Stop: calculate_position_size()'s ATR-based stop_price.
    Take-profit: the highest CONFIRMED, bullish-oriented pattern's measured_target, else the ML
    Quantile Regression Q90 band (db_helpers.get_latest_quantile_bands()). Returns None (no
    signal) for a ticker missing any required input, mirroring compute_regime_weighted_score()'s
    convention elsewhere in this pipeline; otherwise a dict with the computed values and a
    `passes` bool (risk_reward >= the configured/passed-in minimum)."""
    from config import load_config
    from db_helpers import get_latest_quantile_bands

    tickers = list(dict.fromkeys(tickers))
    results: Dict[str, Optional[dict]] = {t: None for t in tickers}
    if not tickers:
        return results

    cfg = get_position_sizing_config(load_config())
    threshold = min_rr if min_rr is not None else cfg["MIN_RISK_REWARD"]

    conn = None
    try:
        conn = get_connection()
        placeholders = ",".join("?" * len(tickers))
        price_rows = conn.execute(
            f"SELECT ticker, current_price FROM stock_signals WHERE ticker IN ({placeholders})",
            tickers,
        ).fetchall()
        entry_prices = {row["ticker"]: row["current_price"] for row in price_rows if row["current_price"]}

        atr_by_ticker = _latest_atr_pct_batch(tickers, conn)
        bullish_targets = _bullish_measured_targets_batch(tickers, conn)
    except Exception as e:
        logger.error("passes_risk_reward_gate_batch failed: %s", e)
        return results
    finally:
        if conn:
            conn.close()

    quantile_q90 = {row["ticker"]: row["price_q90"] for row in get_latest_quantile_bands(tickers)}

    for ticker in tickers:
        entry_price = entry_prices.get(ticker)
        atr_pct = atr_by_ticker.get(ticker)
        if entry_price is None or atr_pct is None:
            continue

        sizing = calculate_position_size(
            account_value=cfg["ACCOUNT_VALUE"], entry_price=entry_price, atr_pct=atr_pct,
            risk_pct=cfg["RISK_PCT"], stop_multiple=cfg["STOP_MULTIPLE"],
        )
        stop_price = sizing["stop_price"]
        if stop_price is None:
            continue

        take_profit = bullish_targets.get(ticker)
        take_profit_source = "pattern"
        if take_profit is None:
            take_profit = quantile_q90.get(ticker)
            take_profit_source = "quantile_q90"
        if take_profit is None:
            continue

        risk = entry_price - stop_price
        if risk <= 0:
            continue
        risk_reward = round((take_profit - entry_price) / risk, 2)

        results[ticker] = {
            "entry_price": round(entry_price, 4),
            "stop_price": stop_price,
            "take_profit": round(take_profit, 4),
            "take_profit_source": take_profit_source,
            "risk_reward": risk_reward,
            "min_risk_reward": threshold,
            "passes": risk_reward >= threshold,
        }

    return results


def passes_risk_reward_gate(ticker: str, min_rr: Optional[float] = None) -> Optional[dict]:
    return passes_risk_reward_gate_batch([ticker], min_rr=min_rr).get(ticker)