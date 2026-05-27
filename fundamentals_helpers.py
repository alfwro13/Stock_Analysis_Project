# fundamentals_helpers.py
"""
Shared, pure helpers for fundamental-metric computation.

These utilities are imported by both the deep portfolio/watchlist quant pipeline
(`quant_signals.py`) and the universe fundamentals pipeline
(`universe_fundamentals_engine.py`) so that identical math is used in both
places. No I/O, no DB access, no yfinance calls — pure functions only.
"""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def calculate_peter_lynch_peg(
    forward_pe: Optional[float],
    trailing_pe: Optional[float],
    earnings_growth: Optional[float],
    dividend_yield: Optional[float],
) -> Optional[float]:
    """
    Compute the canonical Peter Lynch PEG ratio (yield-adjusted).

    Definition:
        PL_PEG = PE / (EarningsGrowth_pct + DividendYield_pct)

    Where:
      - PE prefers forward PE (less distorted by one-time charges, per Lynch's
        intent); falls back to trailing PE only when forward is unavailable.
      - earnings_growth and dividend_yield are accepted as DECIMALS as returned
        by yfinance (e.g. 0.20 == 20%) and converted to percentage points
        internally before summation.
      - The denominator is the SUM of growth + yield expressed in percentage
        points — Lynch's "Yield-Adjusted PEG" formulation. A stock is
        considered fair value at PEG <= 1.0.

    Returns:
        Positive float PEG ratio, or None when any input is missing,
        non-positive, or would produce a non-meaningful result (loss-making
        company, zero growth + zero yield, etc.).
    """
    pe_for_lynch: Optional[float] = (
        forward_pe if (forward_pe is not None and forward_pe > 0) else trailing_pe
    )

    if pe_for_lynch is None or pe_for_lynch <= 0:
        return None
    if earnings_growth is None or earnings_growth <= 0:
        return None

    eg_scaled: float = earnings_growth * 100.0

    div_yield_val: float = dividend_yield if dividend_yield is not None else 0.0
    div_yield_scaled: float = div_yield_val * 100.0

    total_growth_yield: float = eg_scaled + div_yield_scaled
    if total_growth_yield <= 0:
        return None

    return pe_for_lynch / total_growth_yield
