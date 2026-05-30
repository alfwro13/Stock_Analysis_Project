# utils.py — lightweight helpers with no heavy dependencies
from typing import Any


def normalize_ticker(ticker: str) -> str:
    return str(ticker).strip().upper()


def clamp_beta(raw: Any, lo: float = 0.5, hi: float = 2.0, default: float = 1.0) -> float:
    """Returns beta clamped to [lo, hi], falling back to default on None or bad data.

    Guards against empty strings and other non-numeric values that SQLite may return
    for the beta column, which would raise ValueError inside float().
    """
    try:
        if raw is None:
            return default
        return max(lo, min(hi, float(raw)))
    except (TypeError, ValueError):
        return default
