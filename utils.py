# utils.py — lightweight helpers with no heavy dependencies
import re
from typing import Any

# Covers equities, ETFs, indices (^GSPC), FX pairs (GBPUSD=X), LSE (.L suffix), etc.
TICKER_RE = re.compile(r'^[A-Z0-9.\-\^=]{1,20}$')


def normalize_ticker(ticker: str) -> str:
    """Uppercase and strip a ticker symbol, then validate its format."""
    from fastapi import HTTPException
    value = str(ticker).strip().upper()
    if not TICKER_RE.match(value):
        raise HTTPException(status_code=400, detail=f"Invalid ticker symbol: '{ticker}'")
    return value


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
