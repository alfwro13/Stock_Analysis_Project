# position_sizing.py
"""
Risk-parity position sizing calculator.

Computes the number of shares to buy such that the dollar/pound/euro risk
on the trade equals a fixed fraction of the account value, regardless of
the asset's volatility.

Formula
-------
    risk_capital   = account_value * risk_pct
    risk_per_share = entry_price   * atr_pct * stop_multiple   (native currency)
    shares         = floor(risk_capital / risk_per_share_in_base_currency)

This equalises risk across positions: a low-volatility stock receives a
larger capital allocation than a high-volatility stock, but both carry
the same monetary risk.

The calculation function is also implemented in JavaScript for client-side
rendering in watchlist/portfolio tables. Both implementations are kept
identical to ensure consistency between server and client display.

Reference: Van Tharp, "Trade Your Way to Financial Freedom" (1998),
position sizing chapter — the canonical retail formulation of this concept.
"""

from typing import Dict, Optional


DEFAULT_CONFIG = {
    "ACCOUNT_VALUE":   10000.0,   # In user's base currency
    "RISK_PCT":        1.0,       # Percent of account risked per trade (1.0 = 1%)
    "STOP_MULTIPLE":   2.0,       # ATR multiples to the stop loss
}


def get_position_sizing_config(config_data: dict) -> dict:
    """
    Extracts the POSITION_SIZING block from a loaded config_data dict,
    filling in defaults for any missing keys. Always returns a complete dict.
    """
    user_cfg = config_data.get("POSITION_SIZING", {}) or {}
    return {
        "ACCOUNT_VALUE": float(user_cfg.get("ACCOUNT_VALUE", DEFAULT_CONFIG["ACCOUNT_VALUE"])),
        "RISK_PCT":      float(user_cfg.get("RISK_PCT",      DEFAULT_CONFIG["RISK_PCT"])),
        "STOP_MULTIPLE": float(user_cfg.get("STOP_MULTIPLE", DEFAULT_CONFIG["STOP_MULTIPLE"])),
    }


def calculate_position_size(
    account_value:    float,
    entry_price:      float,
    atr_pct:          Optional[float],
    fx_rate_to_base:  float = 1.0,
    risk_pct:         float = 1.0,
    stop_multiple:    float = 2.0,
) -> Dict[str, Optional[float]]:
    """
    Calculates a risk-parity position size.

    Args:
        account_value:    Total account size in BASE currency (e.g. GBP)
        entry_price:      Current price in NATIVE currency (e.g. USD for a US stock)
        atr_pct:          14-day ATR as decimal fraction of price (e.g. 0.04 = 4%)
                          Can be None — function returns nulls in that case.
        fx_rate_to_base:  Multiplier converting NATIVE → BASE.
                          e.g. for GBP base and USD stock, pass GBP/USD rate (~0.79)
        risk_pct:         Percent of account to risk (1.0 = 1%)
        stop_multiple:    ATR multiples to stop loss (typically 1.5–3.0)

    Returns:
        Dict with keys:
            shares             — whole share count (int)
            position_value     — total position value in BASE currency
            stop_price         — stop loss in NATIVE currency
            risk_amount        — actual monetary risk in BASE currency
            risk_per_share     — risk per share in BASE currency
            risk_per_share_native — risk per share in NATIVE currency
        Returns all None values if inputs are invalid (ATR missing,
        negative price, zero account, etc.) — caller should handle gracefully.
    """
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

    # Convert risk_pct from percentage to decimal
    risk_fraction = risk_pct / 100.0

    # Risk budget in base currency
    risk_capital_base = account_value * risk_fraction

    # Risk per share in native currency
    risk_per_share_native = entry_price * atr_pct * stop_multiple

    # Convert risk per share to base currency for the share count division
    risk_per_share_base = risk_per_share_native * fx_rate_to_base

    if risk_per_share_base <= 0:
        return null_result

    # Whole shares only — Freetrade supports fractional but rounding down
    # gives a conservative position size
    shares = int(risk_capital_base // risk_per_share_base)

    # Stop loss in native currency
    stop_price_native = entry_price - (entry_price * atr_pct * stop_multiple)

    # Position value and actual risk (after rounding down to whole shares)
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