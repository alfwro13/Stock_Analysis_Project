"""Pure fundamental-metric helpers; no I/O, no DB — imported by both quant_signals.py and universe_fundamentals_engine.py."""
import logging
import math
from datetime import datetime
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


def compute_quality_grade(row: dict) -> str:
    """A/B/C/D grade from ROE, debt/equity, PE/PEG: D=loss-making or over-leveraged, A=high-quality compounder.

    roe is a Yahoo-style fraction (0.15 = 15%); debt_to_equity is Yahoo's own percentage-like
    scale (debtToEquity≈30 means 30% D/E, per universe_fundamentals_engine.py) — these must match
    the units actually stored in stock_signals, not an arbitrary ratio/percentage of the caller's choosing.
    """
    roe  = row.get('roe')
    debt = row.get('debt_to_equity')
    pe   = row.get('trailing_pe')
    peg  = row.get('peg_ratio')

    if (roe is not None and roe < 0) or (debt is not None and debt > 200):
        return 'D'

    a_roe  = roe is not None and roe > 0.15
    a_debt = debt is None or debt < 50
    a_val  = (pe is not None and pe < 25) or (peg is not None and peg < 1.5)
    if a_roe and a_debt and a_val:
        return 'A'

    b_roe  = roe is not None and roe > 0.10
    b_debt = debt is None or debt < 100
    b_val  = pe is None or pe < 35
    if b_roe and b_debt and b_val:
        return 'B'

    return 'C'


def get_earnings_days(row: dict, target_date: str) -> Optional[int]:
    """Returns days until next earnings, or None if unknown or already passed."""
    raw = row.get('next_earnings_date')
    if not raw or raw == 'Unknown':
        return None
    try:
        earnings_dt = datetime.strptime(raw[:10], '%Y-%m-%d').date()
        today_dt = datetime.strptime(target_date, '%Y-%m-%d').date()
        delta = (earnings_dt - today_dt).days
        return delta if delta >= 0 else None
    except (ValueError, TypeError):
        return None


def get_instrument_type(asset_class: str, asset_sub_class: str) -> str:
    sub = (asset_sub_class or "").upper()
    cls = (asset_class or "").upper()
    if sub == "ETF" or cls == "ETF":
        return "ETF"
    if cls == "EQUITY" or sub == "STOCK":
        return "Equity"
    if cls == "COMMODITY" or sub == "COMMODITY":
        return "Commodity"
    if cls == "FIXED_INCOME":
        return "Fixed Income"
    if cls == "CASH":
        return "Cash & Equivalents"
    if cls == "MUTUALFUND":
        return "Mutual Fund"
    if cls:
        return cls.title()
    return "Other"


# Single source of truth for the report-screen thresholds below — reports_engine.py's five
# SQL WHERE clauses bind to these same constants instead of duplicating the literal values.
QUALITY_COMPOUNDER_MIN_ROE = 0.15
QUALITY_COMPOUNDER_MAX_DEBT_TO_EQUITY = 100
QUALITY_COMPOUNDER_MIN_MARGIN = 0.10
QUALITY_COMPOUNDER_MIN_GROWTH = 0.05
QUALITY_COMPOUNDER_MIN_CURRENT_RATIO = 1.5
QUALITY_COMPOUNDER_MIN_SCORE = 60
QUALITY_COMPOUNDER_MIN_PE = 10
QUALITY_COMPOUNDER_MAX_PE = 35

QUALITY_ON_SALE_MAX_PRICE_VS_52W_LOW = 1.15
QUALITY_ON_SALE_MIN_ROE = 0.10
QUALITY_ON_SALE_MAX_DEBT_TO_EQUITY = 150
QUALITY_ON_SALE_MIN_MARGIN = 0.05
QUALITY_ON_SALE_MAX_PE = 25
QUALITY_ON_SALE_MIN_SCORE = 50

GARP_MAX_PEG = 1.0
GARP_MIN_GROWTH = 0.15
GARP_MIN_ROE = 0.10
GARP_MIN_FORWARD_PE = 10
GARP_MAX_FORWARD_PE = 40
GARP_MIN_MARKET_CAP = 500_000_000

MEAN_REVERSION_DEFAULT_MAX_RSI = 30.0

DIVIDEND_HARVEST_DEFAULT_MIN_YIELD = 0.02
DIVIDEND_HARVEST_DEFAULT_MIN_SCORE = 50


def is_quality_compounder(row: dict) -> bool:
    """Mirrors reports_engine.get_quality_compounders()'s WHERE clause via the shared constants above."""
    roe, debt, margin, growth, current_ratio, pe, score = (
        row.get('roe'), row.get('debt_to_equity'), row.get('profit_margin'),
        row.get('revenue_growth'), row.get('current_ratio'), row.get('trailing_pe'), row.get('composite_score'),
    )
    if None in (roe, debt, margin, growth, current_ratio, pe, score):
        return False
    return (
        roe > QUALITY_COMPOUNDER_MIN_ROE and debt < QUALITY_COMPOUNDER_MAX_DEBT_TO_EQUITY
        and margin > QUALITY_COMPOUNDER_MIN_MARGIN and growth > QUALITY_COMPOUNDER_MIN_GROWTH
        and current_ratio > QUALITY_COMPOUNDER_MIN_CURRENT_RATIO and score >= QUALITY_COMPOUNDER_MIN_SCORE
        and QUALITY_COMPOUNDER_MIN_PE <= pe <= QUALITY_COMPOUNDER_MAX_PE
    )


def is_quality_on_sale(row: dict) -> bool:
    """Mirrors reports_engine.get_quality_on_sale()'s WHERE clause via the shared constants above."""
    close, low_52w, roe, debt, margin, pe, score = (
        row.get('close_price'), row.get('fifty_two_week_low'), row.get('roe'),
        row.get('debt_to_equity'), row.get('profit_margin'), row.get('trailing_pe'), row.get('composite_score'),
    )
    if None in (close, low_52w, roe, margin, pe, score) or low_52w <= 0:
        return False
    if debt is not None and debt >= QUALITY_ON_SALE_MAX_DEBT_TO_EQUITY:
        return False
    return (
        close <= low_52w * QUALITY_ON_SALE_MAX_PRICE_VS_52W_LOW and roe > QUALITY_ON_SALE_MIN_ROE
        and margin > QUALITY_ON_SALE_MIN_MARGIN and 0 < pe < QUALITY_ON_SALE_MAX_PE and score >= QUALITY_ON_SALE_MIN_SCORE
    )


def is_garp_tenbagger(row: dict, market_cap: Optional[float]) -> bool:
    """Mirrors reports_engine.get_garp_tenbaggers()'s WHERE clause via the shared constants above, minus its
    market_universe.is_index=1 restriction (a user's Watchlist pick needn't be an index member for this to be a useful tag)."""
    peg, growth, roe, fwd_pe = row.get('peter_lynch_peg'), row.get('revenue_growth'), row.get('roe'), row.get('forward_pe')
    if None in (peg, growth, roe, fwd_pe) or not market_cap:
        return False
    return (
        0 < peg <= GARP_MAX_PEG and growth > GARP_MIN_GROWTH and roe > GARP_MIN_ROE
        and GARP_MIN_FORWARD_PE <= fwd_pe <= GARP_MAX_FORWARD_PE and market_cap > GARP_MIN_MARKET_CAP
    )


def is_mean_reversion_setup(row: dict, max_rsi: float = MEAN_REVERSION_DEFAULT_MAX_RSI) -> bool:
    """Mirrors reports_engine.get_mean_reversion_setups()'s WHERE clause via the shared constant above."""
    close, sma_200, rsi = row.get('close_price'), row.get('sma_200'), row.get('rsi_14')
    if None in (close, sma_200, rsi):
        return False
    return close > sma_200 and rsi <= max_rsi


def is_dividend_harvest_candidate(
    row: dict,
    min_yield: float = DIVIDEND_HARVEST_DEFAULT_MIN_YIELD,
    min_score: int = DIVIDEND_HARVEST_DEFAULT_MIN_SCORE,
) -> bool:
    """Mirrors reports_engine.get_dividend_harvest_setups()'s WHERE clause via the shared constants above."""
    yield_, score, ex_div = row.get('dividend_yield'), row.get('composite_score'), row.get('ex_dividend_date')
    if yield_ is None or score is None or not ex_div or ex_div == 'Unknown':
        return False
    return yield_ >= min_yield and score >= min_score
