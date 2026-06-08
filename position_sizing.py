"""
Risk-parity sizing: shares = floor((account*risk_pct/100) / (entry*atr_pct*stop_multiple*fx_rate_to_base)).
Mirrors the JS implementation in watchlist/portfolio tables — both must stay in sync.
"""

from typing import Dict, Optional


DEFAULT_CONFIG = {
    "ACCOUNT_VALUE":   10000.0,   # In user's base currency
    "RISK_PCT":        1.0,       # Percent of account risked per trade (1.0 = 1%)
    "STOP_MULTIPLE":   2.0,       # ATR multiples to the stop loss
}


def get_position_sizing_config(config_data: dict) -> dict:
    """Returns POSITION_SIZING block from config with defaults filled in."""
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