# Lightweight helpers with no heavy dependencies — safe to import from any module.
from typing import Any


def normalize_ticker(ticker: str) -> str:
    return str(ticker).strip().upper()


def clamp_beta(raw: Any, lo: float = 0.5, hi: float = 2.0, default: float = 1.0) -> float:
    """Guards against empty strings / SQLite None for the beta column, which raises ValueError inside float()."""
    try:
        if raw is None:
            return default
        return max(lo, min(hi, float(raw)))
    except (TypeError, ValueError):
        return default
