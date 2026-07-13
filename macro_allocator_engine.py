import logging
import math
from typing import Dict, List, Optional, Any

from config import load_config, GHOSTFOLIO_URL, GHOSTFOLIO_TOKEN
from database import get_connection
from xray_engine import GhostfolioXRayClient, _builtin_account_holdings
from fundamentals_helpers import get_instrument_type

logger = logging.getLogger(__name__)

_INSTRUMENT_TO_CLASS = {
    "Equity": "equities",
    "ETF": "equities",
    "Fixed Income": "bonds",
    "Commodity": "commodities",
}


def get_current_regime_state() -> Optional[Dict[str, Any]]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT date, regime_label, yield_curve_inverted, days_inverted, "
            "us_threat_level, uk_threat_level "
            "FROM macro_regimes WHERE regime_label IS NOT NULL ORDER BY date DESC LIMIT 1"
        )
        regime_row = cursor.fetchone()
        if not regime_row:
            return None

        cursor.execute(
            "SELECT us_yield_curve, us_cpi_inflation, us_high_yield_spread, "
            "us_fed_funds_rate, us_real_yield_10y, uk_base_rate "
            "FROM macro_indicators ORDER BY date DESC LIMIT 1"
        )
        ind_row = cursor.fetchone()

        return {
            "date": regime_row["date"],
            "regime_label": regime_row["regime_label"],
            "yield_curve_inverted": bool(regime_row["yield_curve_inverted"]),
            "days_inverted": regime_row["days_inverted"] or 0,
            "us_threat_level": regime_row["us_threat_level"],
            "uk_threat_level": regime_row["uk_threat_level"],
            "key_signals": {
                "us_yield_curve": ind_row["us_yield_curve"] if ind_row else None,
                "us_cpi_inflation": ind_row["us_cpi_inflation"] if ind_row else None,
                "us_high_yield_spread": ind_row["us_high_yield_spread"] if ind_row else None,
                "us_fed_funds_rate": ind_row["us_fed_funds_rate"] if ind_row else None,
                "us_real_yield_10y": ind_row["us_real_yield_10y"] if ind_row else None,
                "uk_base_rate": ind_row["uk_base_rate"] if ind_row else None,
            },
        }
    except Exception as e:
        logger.error("get_current_regime_state failed: %s", e)
        return None
    finally:
        if conn:
            conn.close()


def get_ideal_allocation(regime_label: str) -> Dict[str, float]:
    """Returns midpoint target weights (%) for each asset class given the regime label."""
    config = load_config()
    targets = config.get("REGIME_TARGETS", {})
    regime_config = targets.get(regime_label)
    if not regime_config:
        return {"equities": 65.0, "bonds": 15.0, "commodities": 5.0, "cash": 15.0}
    return {
        asset_class: round((lo + hi) / 2, 1)
        for asset_class, (lo, hi) in regime_config.items()
    }


def _get_portfolio_asset_class_weights() -> tuple:
    """Returns (weights, None) on success or (None, error) when no holdings exist — Ghostfolio if configured, else built-in Trading accounts (AGENTS.md rule 14)."""
    holdings: List[Dict] = []
    total_value = 0.0

    if GHOSTFOLIO_URL and GHOSTFOLIO_TOKEN:
        config = load_config()
        active_ids: List[str] = config.get("GHOSTFOLIO_ACCOUNTS", {}).get("active", [])
        if active_ids:
            client = GhostfolioXRayClient()
            if client.authenticate():
                holdings, total_value = client.get_holdings(active_ids)

    if not holdings or total_value <= 0:
        holdings = _builtin_account_holdings(None)
        total_value = sum(h["value"] for h in holdings)

    if not holdings or total_value <= 0:
        return None, "No portfolio holdings found — add a Trading account holding or configure Ghostfolio in Settings."

    holding_values: Dict[str, float] = {}
    for h in holdings:
        itype = get_instrument_type(h.get("asset_class", ""), h.get("asset_sub_class", ""))
        if itype == "Cash & Equivalents":
            continue  # left uncounted so it falls through to the "cash" complement below
        macro_class = _INSTRUMENT_TO_CLASS.get(itype, "equities")
        holding_values[macro_class] = holding_values.get(macro_class, 0.0) + h["value"]

    weights: Dict[str, float] = {}
    for cls in ("equities", "bonds", "commodities"):
        weights[cls] = round(holding_values.get(cls, 0.0) / total_value * 100, 1)
    weights["cash"] = round(max(0.0, 100.0 - sum(weights[c] for c in ("equities", "bonds", "commodities"))), 1)
    return weights, None


def score_portfolio_alignment(current: Dict[str, float], ideal: Dict[str, float]) -> int:
    """Cosine-similarity alignment score 0–100; 100 = perfect match to ideal regime weights."""
    keys = list(ideal.keys())
    a = [current.get(k, 0.0) for k in keys]
    b = [ideal.get(k, 0.0) for k in keys]

    dot = sum(x * y for x, y in zip(a, b))
    mag_a = math.sqrt(sum(x ** 2 for x in a))
    mag_b = math.sqrt(sum(x ** 2 for x in b))

    if mag_a == 0 or mag_b == 0:
        return 0
    return round(dot / (mag_a * mag_b) * 100)


def get_rebalance_deltas(current: Dict[str, float], ideal: Dict[str, float]) -> Dict[str, float]:
    return {k: round(ideal.get(k, 0.0) - current.get(k, 0.0), 1) for k in ideal}


def get_regime_history(days: int = 90) -> List[Dict[str, Any]]:
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "SELECT date, regime_label FROM macro_regimes "
            "WHERE regime_label IS NOT NULL ORDER BY date DESC LIMIT ?",
            (days,),
        )
        rows = cursor.fetchall()
        return [{"date": r["date"], "regime_label": r["regime_label"]} for r in rows]
    except Exception as e:
        logger.error("get_regime_history failed: %s", e)
        return []
    finally:
        if conn:
            conn.close()


def get_macro_allocation_data() -> Dict[str, Any]:
    regime = get_current_regime_state()
    if not regime:
        return {"status": "no_data", "message": "Run the macro data engine to populate regime data."}

    regime_label = regime["regime_label"]
    ideal = get_ideal_allocation(regime_label)
    current, portfolio_error = _get_portfolio_asset_class_weights()

    config = load_config()
    regime_config = config.get("REGIME_TARGETS", {}).get(regime_label, {})

    result: Dict[str, Any] = {
        "status": "ok",
        "regime_label": regime_label,
        "regime_date": regime["date"],
        "yield_curve_inverted": regime["yield_curve_inverted"],
        "days_inverted": regime["days_inverted"],
        "us_threat_level": regime["us_threat_level"],
        "uk_threat_level": regime["uk_threat_level"],
        "key_signals": regime["key_signals"],
        "ideal_allocation": ideal,
        "regime_ranges": regime_config,
        "regime_history": get_regime_history(90),
    }

    if current:
        result["current_allocation"] = current
        result["alignment_score"] = score_portfolio_alignment(current, ideal)
        result["rebalance_deltas"] = get_rebalance_deltas(current, ideal)
    else:
        result["current_allocation"] = None
        result["alignment_score"] = None
        result["rebalance_deltas"] = None
        result["portfolio_note"] = portfolio_error or "Portfolio data unavailable."

    return result
