# fundamentals_helpers.py
"""Pure fundamental-metric helpers; no I/O, no DB — imported by both quant_signals.py and universe_fundamentals_engine.py."""
import logging
from typing import Optional

logger = logging.getLogger(__name__)


def calculate_peter_lynch_peg(
    forward_pe: Optional[float],
    trailing_pe: Optional[float],
    earnings_growth: Optional[float],
    dividend_yield: Optional[float],
) -> Optional[float]:
    """Lynch yield-adjusted PEG; growth/yield accepted as yfinance decimals (0.20 = 20%, scaled ×100 internally); forward PE preferred over trailing."""
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
