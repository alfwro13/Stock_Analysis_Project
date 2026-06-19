"""Pure fundamental-metric helpers; no I/O, no DB — imported by both quant_signals.py and universe_fundamentals_engine.py."""
import logging
import math
from typing import Optional

try:
    import pandas as pd
    _PANDAS_AVAILABLE = True
except ImportError:
    _PANDAS_AVAILABLE = False

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


def _fs(df, key: str, col: int = 0) -> Optional[float]:
    """Extract a single float from a yfinance annual statement DataFrame (rows=metrics, cols=dates)."""
    if df is None or not _PANDAS_AVAILABLE:
        return None
    try:
        if df.empty or key not in df.index or col >= len(df.columns):
            return None
        v = df.iloc[df.index.get_loc(key), col]
        f = float(v)
        return None if (math.isnan(f) or math.isinf(f)) else f
    except (TypeError, ValueError, KeyError):
        return None


def calculate_piotroski_f_score(bs, fin, cf) -> Optional[int]:
    """9-point Piotroski F-Score from annual financial statements (rows=metrics, cols=dates newest-first).
    Returns None when fewer than 5 of the 9 criteria can be evaluated."""
    if not _PANDAS_AVAILABLE or bs is None or fin is None or cf is None:
        return None
    if bs.empty or fin.empty or cf.empty:
        return None

    has_prior = len(bs.columns) >= 2 and len(fin.columns) >= 2 and len(cf.columns) >= 2

    ta_t    = _fs(bs, 'Total Assets')
    ta_p    = _fs(bs, 'Total Assets', 1)   if has_prior else None
    ni_t    = _fs(fin, 'Net Income')
    ni_p    = _fs(fin, 'Net Income', 1)    if has_prior else None
    ocf_t   = _fs(cf,  'Operating Cash Flow')
    rev_t   = _fs(fin, 'Total Revenue')
    rev_p   = _fs(fin, 'Total Revenue', 1) if has_prior else None
    gp_t    = _fs(fin, 'Gross Profit')
    gp_p    = _fs(fin, 'Gross Profit', 1)  if has_prior else None
    ltd_t   = _fs(bs, 'Long Term Debt')
    ltd_p   = _fs(bs, 'Long Term Debt', 1) if has_prior else None
    ca_t    = _fs(bs, 'Current Assets')
    ca_p    = _fs(bs, 'Current Assets', 1)  if has_prior else None
    cl_t    = _fs(bs, 'Current Liabilities')
    cl_p    = _fs(bs, 'Current Liabilities', 1) if has_prior else None
    sh_t    = _fs(bs, 'Ordinary Shares Number') or _fs(bs, 'Share Issued')
    sh_p    = (_fs(bs, 'Ordinary Shares Number', 1) or _fs(bs, 'Share Issued', 1)) if has_prior else None

    score = 0
    available = 0

    if ta_t and ta_t > 0 and ni_t is not None:
        available += 1
        if (ni_t / ta_t) > 0:
            score += 1

    if ocf_t is not None:
        available += 1
        if ocf_t > 0:
            score += 1

    if ta_t and ta_t > 0 and ni_t is not None and ta_p and ta_p > 0 and ni_p is not None:
        available += 1
        if (ni_t / ta_t) > (ni_p / ta_p):
            score += 1

    if ta_t and ta_t > 0 and ocf_t is not None and ni_t is not None:
        available += 1
        if (ocf_t / ta_t) > (ni_t / ta_t):
            score += 1

    if ta_t and ta_t > 0 and ltd_t is not None and ta_p and ta_p > 0 and ltd_p is not None:
        available += 1
        if (ltd_t / ta_t) < (ltd_p / ta_p):
            score += 1

    if ca_t and cl_t and cl_t > 0 and ca_p is not None and cl_p and cl_p > 0:
        available += 1
        if (ca_t / cl_t) > (ca_p / cl_p):
            score += 1

    if sh_t is not None and sh_p is not None:
        available += 1
        if sh_t <= sh_p * 1.01:
            score += 1

    if rev_t and rev_t > 0 and gp_t is not None and rev_p and rev_p > 0 and gp_p is not None:
        available += 1
        if (gp_t / rev_t) > (gp_p / rev_p):
            score += 1

    if ta_t and ta_t > 0 and rev_t is not None and ta_p and ta_p > 0 and rev_p is not None:
        available += 1
        if (rev_t / ta_t) > (rev_p / ta_p):
            score += 1

    return score if available >= 5 else None


def calculate_altman_z_score(info: dict, bs, fin) -> Optional[float]:
    """Altman Z' (non-manufacturer, 4-variable): 6.56*X1 + 3.26*X2 + 6.72*X3 + 1.05*X4.
    Zones: >2.6 safe, 1.1–2.6 grey, <1.1 distress. Returns None if key inputs are absent."""
    if not _PANDAS_AVAILABLE or bs is None or fin is None:
        return None
    if bs.empty or fin.empty:
        return None

    ta = _fs(bs, 'Total Assets')
    if not ta or ta <= 0:
        return None

    wc = _fs(bs, 'Working Capital')
    if wc is None:
        ca = _fs(bs, 'Current Assets')
        cl = _fs(bs, 'Current Liabilities')
        wc = (ca - cl) if (ca is not None and cl is not None) else None

    retained  = _fs(bs, 'Retained Earnings')
    ebit      = _fs(fin, 'EBIT') or _fs(fin, 'Operating Income')
    equity_bv = _fs(bs, 'Common Stock Equity') or _fs(bs, 'Stockholders Equity')
    total_liab = _fs(bs, 'Total Liabilities Net Minority Interest')
    revenue   = _fs(fin, 'Total Revenue')
    market_cap = info.get('marketCap') if isinstance(info, dict) else None

    if wc is None or ebit is None or revenue is None:
        return None

    x1 = wc / ta
    x2 = (retained / ta) if retained is not None else 0.0
    x3 = ebit / ta

    if equity_bv is not None and total_liab and total_liab > 0:
        x4 = equity_bv / total_liab
    elif market_cap and total_liab and total_liab > 0:
        x4 = market_cap / total_liab
    else:
        return None

    return round(6.56 * x1 + 3.26 * x2 + 6.72 * x3 + 1.05 * x4, 2)


def calculate_beneish_m_score(bs, fin, cf) -> Optional[float]:
    """8-variable Beneish M-Score for earnings manipulation detection. Requires 2 annual periods.
    M > -1.78 signals possible manipulation. Returns None when fewer than 4 variables can be computed."""
    if not _PANDAS_AVAILABLE or bs is None or fin is None or cf is None:
        return None
    if bs.empty or fin.empty or cf.empty:
        return None
    if len(bs.columns) < 2 or len(fin.columns) < 2:
        return None

    rev_t   = _fs(fin, 'Total Revenue')
    rev_p   = _fs(fin, 'Total Revenue', 1)
    ta_t    = _fs(bs, 'Total Assets')
    ta_p    = _fs(bs, 'Total Assets', 1)

    if not all([rev_t, rev_p, ta_t, ta_p]) or rev_p <= 0 or ta_p <= 0:
        return None

    ar_t    = _fs(bs, 'Accounts Receivable') or _fs(bs, 'Receivables')
    ar_p    = _fs(bs, 'Accounts Receivable', 1) or _fs(bs, 'Receivables', 1)
    gp_t    = _fs(fin, 'Gross Profit')
    gp_p    = _fs(fin, 'Gross Profit', 1)
    ca_t    = _fs(bs, 'Current Assets')
    ca_p    = _fs(bs, 'Current Assets', 1)
    ppe_t   = _fs(bs, 'Net PPE')
    ppe_p   = _fs(bs, 'Net PPE', 1)
    dep_t   = _fs(cf, 'Depreciation And Amortization') or _fs(cf, 'Depreciation Amortization Depletion')
    dep_p   = _fs(cf, 'Depreciation And Amortization', 1) or _fs(cf, 'Depreciation Amortization Depletion', 1)
    sga_t   = _fs(fin, 'Selling General And Administration')
    sga_p   = _fs(fin, 'Selling General And Administration', 1)
    tl_t    = _fs(bs, 'Total Liabilities Net Minority Interest')
    tl_p    = _fs(bs, 'Total Liabilities Net Minority Interest', 1)
    ni_t    = _fs(fin, 'Net Income')
    ocf_t   = _fs(cf, 'Operating Cash Flow')

    computed = 0
    total = 0.0

    if ar_t is not None and ar_p is not None and rev_p > 0:
        dsri = (ar_t / rev_t) / (ar_p / rev_p) if (ar_p / rev_p) > 0 else 1.0
        total += 0.920 * dsri
        computed += 1
    else:
        total += 0.920

    if gp_t is not None and gp_p is not None and rev_t > 0 and rev_p > 0:
        gmi = (gp_p / rev_p) / (gp_t / rev_t) if (gp_t / rev_t) > 0 else 1.0
        total += 0.528 * gmi
        computed += 1
    else:
        total += 0.528

    if ca_t is not None and ppe_t is not None and ca_p is not None and ppe_p is not None and ta_t > 0 and ta_p > 0:
        aqi_t   = 1.0 - (ca_t + ppe_t) / ta_t
        aqi_p   = 1.0 - (ca_p + ppe_p) / ta_p
        total += 0.404 * (aqi_t / aqi_p if aqi_p != 0 else 1.0)
        computed += 1
    else:
        total += 0.404

    sgi = rev_t / rev_p
    total += 0.892 * sgi
    computed += 1

    if dep_t is not None and dep_p is not None and ppe_t is not None and ppe_p is not None:
        dep_rate_t = abs(dep_t) / (abs(dep_t) + ppe_t) if (abs(dep_t) + ppe_t) > 0 else 0.0
        dep_rate_p = abs(dep_p) / (abs(dep_p) + ppe_p) if (abs(dep_p) + ppe_p) > 0 else 0.0
        total += 0.115 * (dep_rate_p / dep_rate_t if dep_rate_t > 0 else 1.0)
        computed += 1
    else:
        total += 0.115

    if sga_t is not None and sga_p is not None and rev_t > 0 and rev_p > 0:
        sgai_p = sga_p / rev_p
        total += 0.172 * ((sga_t / rev_t) / sgai_p if sgai_p > 0 else 1.0)
        computed += 1
    else:
        total += 0.172

    if tl_t is not None and tl_p is not None and ta_t > 0 and ta_p > 0:
        lvgi_p = tl_p / ta_p
        total -= 0.327 * ((tl_t / ta_t) / lvgi_p if lvgi_p > 0 else 1.0)
        computed += 1
    else:
        total -= 0.327

    if ni_t is not None and ocf_t is not None and ta_t > 0:
        total += 4.679 * ((ni_t - ocf_t) / ta_t)
        computed += 1

    if computed < 4:
        return None

    return round(-4.840 + total, 3)
