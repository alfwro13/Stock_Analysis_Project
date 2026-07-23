from typing import Optional

from page_helpers import _fmt_currency, _fmt_volume, _fmt_price

PORTFOLIO_CORE_COLUMNS = [
    {"key": "ticker", "label": "Ticker", "pinned": True, "fmt": "text"},
    {"key": "company_name", "label": "Company Name", "pinned": False, "fmt": "text"},
    {"key": "price", "label": "Price", "pinned": False, "fmt": "price"},
    {"key": "change", "label": "Change", "pinned": False, "fmt": "pct_raw"},
    {"key": "global_value", "label": "Global Value", "pinned": False, "fmt": "currency_usd"},
    {"key": "global_pnl", "label": "Global P&L", "pinned": False, "fmt": "currency_usd"},
    {"key": "trend_50d", "label": "50D", "pinned": False, "fmt": "text"},
    {"key": "trend_200d", "label": "200D", "pinned": False, "fmt": "text"},
    {"key": "peg_ratio", "label": "PEG", "pinned": False, "fmt": "ratio2"},
    {"key": "pl_peg", "label": "PL PEG", "pinned": False, "fmt": "ratio2"},
    {"key": "stop_loss", "label": "Stop-Loss", "pinned": False, "fmt": "price"},
    {"key": "entry_zone", "label": "Entry Zone", "pinned": False, "fmt": "price"},
    {"key": "exit_target", "label": "Exit Target", "pinned": False, "fmt": "price"},
    {"key": "rsi", "label": "RSI", "pinned": False, "fmt": "ratio2"},
    {"key": "ml_conf", "label": "ML Conf", "pinned": False, "fmt": "pct_raw"},
    {"key": "var_95", "label": "VaR (95%)", "pinned": False, "fmt": "pct_from_fraction"},
    {"key": "sentiment", "label": "Sentiment", "pinned": False, "fmt": "ratio2"},
    {"key": "earnings", "label": "Earnings", "pinned": False, "fmt": "date"},
    {"key": "score", "label": "Score", "pinned": False, "fmt": "int"},
    {"key": "setup_tags", "label": "Setups & Tags", "pinned": False, "fmt": "text"},
    {"key": "signal", "label": "Signal", "pinned": False, "fmt": "text"},
]

WATCHLIST_CORE_COLUMNS = [
    {"key": "ticker", "label": "Ticker", "pinned": True, "fmt": "text"},
    {"key": "company_name", "label": "Company Name", "pinned": False, "fmt": "text"},
    {"key": "price", "label": "Price", "pinned": False, "fmt": "price"},
    {"key": "daily_change", "label": "Daily Change", "pinned": False, "fmt": "pct_raw"},
    {"key": "target", "label": "Target", "pinned": False, "fmt": "price"},
    {"key": "trend_50d", "label": "50D", "pinned": False, "fmt": "text"},
    {"key": "trend_200d", "label": "200D", "pinned": False, "fmt": "text"},
    {"key": "peg_ratio", "label": "PEG", "pinned": False, "fmt": "ratio2"},
    {"key": "pl_peg", "label": "PL PEG", "pinned": False, "fmt": "ratio2"},
    {"key": "stop_loss", "label": "Stop-Loss", "pinned": False, "fmt": "price"},
    {"key": "entry_zone", "label": "Entry Zone", "pinned": False, "fmt": "price"},
    {"key": "rsi", "label": "RSI", "pinned": False, "fmt": "ratio2"},
    {"key": "ml_conf", "label": "ML Conf", "pinned": False, "fmt": "pct_raw"},
    {"key": "var_95", "label": "VaR (95%)", "pinned": False, "fmt": "pct_from_fraction"},
    {"key": "sentiment", "label": "Sentiment", "pinned": False, "fmt": "ratio2"},
    {"key": "earnings", "label": "Earnings", "pinned": False, "fmt": "date"},
    {"key": "score", "label": "Score", "pinned": False, "fmt": "int"},
    {"key": "piotroski", "label": "Piotroski", "pinned": False, "fmt": "int"},
    {"key": "altman_z", "label": "Altman Z", "pinned": False, "fmt": "ratio2"},
    {"key": "beneish_m", "label": "Beneish M", "pinned": False, "fmt": "ratio2"},
    {"key": "low_target", "label": "Low Target", "pinned": False, "fmt": "price"},
    {"key": "high_target", "label": "High Target", "pinned": False, "fmt": "price"},
    {"key": "setup_tags", "label": "Setups & Tags", "pinned": False, "fmt": "text"},
    {"key": "signal", "label": "Signal", "pinned": False, "fmt": "text"},
]

_BOTH = ("portfolio", "watchlist")

# fmt values: pct_from_fraction, pct_raw, ratio2, price, price_raw, currency_usd,
# volume, date, text, bool01, int. "client" columns have no server-computed
# sort/display — they're rendered by renderPositionSizing() in portfolio.js/watchlist.js.
# Core columns carry the same fmt vocabulary purely so static/js/advanced_filter.js can
# pick an operator family (numeric/text/date/bool) and value scale per column — it's not
# consumed by _format_value(), which only runs over OPTIONAL_COLUMNS.
OPTIONAL_COLUMNS = [
    # Fundamentals / Valuation
    {"key": "trailing_pe", "label": "Trailing P/E", "category": "Fundamentals", "pages": _BOTH, "fmt": "ratio2"},
    {"key": "forward_pe", "label": "Forward P/E", "category": "Fundamentals", "pages": _BOTH, "fmt": "ratio2"},
    {"key": "price_to_book", "label": "Price/Book", "category": "Fundamentals", "pages": _BOTH, "fmt": "ratio2"},
    {"key": "price_to_sales", "label": "Price/Sales", "category": "Fundamentals", "pages": _BOTH, "fmt": "ratio2"},
    {"key": "free_cash_flow", "label": "Free Cash Flow", "category": "Fundamentals", "pages": _BOTH, "fmt": "currency_usd"},
    {"key": "roe", "label": "ROE", "category": "Fundamentals", "pages": _BOTH, "fmt": "pct_from_fraction"},
    {"key": "debt_to_equity", "label": "Debt/Equity", "category": "Fundamentals", "pages": _BOTH, "fmt": "pct_raw"},
    {"key": "profit_margin", "label": "Profit Margin", "category": "Fundamentals", "pages": _BOTH, "fmt": "pct_from_fraction"},
    {"key": "revenue_growth", "label": "Revenue Growth", "category": "Fundamentals", "pages": _BOTH, "fmt": "pct_from_fraction"},
    {"key": "current_ratio", "label": "Current Ratio", "category": "Fundamentals", "pages": _BOTH, "fmt": "ratio2"},
    {"key": "operating_cash_flow", "label": "Operating Cash Flow", "category": "Fundamentals", "pages": _BOTH, "fmt": "currency_usd"},
    {"key": "dividend_yield", "label": "Dividend Yield", "category": "Fundamentals", "pages": _BOTH, "fmt": "pct_from_fraction"},
    {"key": "ex_dividend_date", "label": "Ex-Dividend Date", "category": "Fundamentals", "pages": _BOTH, "fmt": "date"},
    {"key": "beta", "label": "Beta", "category": "Fundamentals", "pages": _BOTH, "fmt": "ratio2"},
    {"key": "short_interest", "label": "Short Interest", "category": "Fundamentals", "pages": _BOTH, "fmt": "pct_from_fraction"},
    {"key": "institutional_ownership", "label": "Institutional Ownership", "category": "Fundamentals", "pages": _BOTH, "fmt": "pct_from_fraction"},
    {"key": "yield_correlation", "label": "Yield Correlation", "category": "Fundamentals", "pages": _BOTH, "fmt": "ratio2"},
    {"key": "analyst_rating", "label": "Analyst Rating", "category": "Fundamentals", "pages": _BOTH, "fmt": "text"},
    {"key": "fifty_two_week_low", "label": "52-Week Low", "category": "Fundamentals", "pages": _BOTH, "fmt": "price"},
    {"key": "fifty_two_week_high", "label": "52-Week High", "category": "Fundamentals", "pages": _BOTH, "fmt": "price"},
    {"key": "ma_50_day", "label": "50D MA Price", "category": "Fundamentals", "pages": _BOTH, "fmt": "price"},
    {"key": "ma_200_day", "label": "200D MA Price", "category": "Fundamentals", "pages": _BOTH, "fmt": "price"},

    # Classification
    {"key": "sector", "label": "Sector", "category": "Classification", "pages": _BOTH, "fmt": "text"},
    {"key": "country", "label": "Country", "category": "Classification", "pages": _BOTH, "fmt": "text"},
    {"key": "quote_type", "label": "Quote Type", "category": "Classification", "pages": _BOTH, "fmt": "text"},
    {"key": "industry", "label": "Industry", "category": "Classification", "pages": _BOTH, "fmt": "text"},
    {"key": "index_membership", "label": "Index Membership", "category": "Classification", "pages": _BOTH, "fmt": "text"},
    {"key": "market_cap", "label": "Market Cap", "category": "Classification", "pages": _BOTH, "fmt": "currency_usd"},

    # Technicals / Risk
    {"key": "macd", "label": "MACD", "category": "Technicals", "pages": _BOTH, "fmt": "price_raw"},
    {"key": "macd_signal", "label": "MACD Signal", "category": "Technicals", "pages": _BOTH, "fmt": "price_raw"},
    {"key": "macd_hist", "label": "MACD Histogram", "category": "Technicals", "pages": _BOTH, "fmt": "price_raw"},
    {"key": "sma_200", "label": "SMA-200", "category": "Technicals", "pages": _BOTH, "fmt": "price"},
    {"key": "atr_pct", "label": "ATR %", "category": "Technicals", "pages": _BOTH, "fmt": "pct_from_fraction"},
    {"key": "cvar_95", "label": "CVaR (95%)", "category": "Technicals", "pages": _BOTH, "fmt": "pct_from_fraction"},
    {"key": "mom_1m", "label": "1M Momentum", "category": "Technicals", "pages": _BOTH, "fmt": "pct_from_fraction"},
    {"key": "mom_3m", "label": "3M Momentum", "category": "Technicals", "pages": _BOTH, "fmt": "pct_from_fraction"},
    {"key": "mom_6m", "label": "6M Momentum", "category": "Technicals", "pages": _BOTH, "fmt": "pct_from_fraction"},
    {"key": "mom_12m_skip1m", "label": "12M Momentum (ex-1M)", "category": "Technicals", "pages": _BOTH, "fmt": "pct_from_fraction"},
    {"key": "rel_strength_5d", "label": "Relative Strength 5D", "category": "Technicals", "pages": _BOTH, "fmt": "pct_from_fraction"},
    {"key": "rel_strength_20d", "label": "Relative Strength 20D", "category": "Technicals", "pages": _BOTH, "fmt": "pct_from_fraction"},
    {"key": "hist_vol_20", "label": "Historical Vol 20D", "category": "Technicals", "pages": _BOTH, "fmt": "pct_from_fraction"},
    {"key": "week52_pct", "label": "52-Week Range %", "category": "Technicals", "pages": _BOTH, "fmt": "pct_from_fraction"},
    {"key": "anomaly_score", "label": "Anomaly Score", "category": "Technicals", "pages": _BOTH, "fmt": "pct_from_fraction"},
    {"key": "vp_poc", "label": "Volume Profile POC", "category": "Technicals", "pages": _BOTH, "fmt": "price"},
    {"key": "vp_val", "label": "Volume Profile VAL", "category": "Technicals", "pages": _BOTH, "fmt": "price"},
    {"key": "vp_vah", "label": "Volume Profile VAH", "category": "Technicals", "pages": _BOTH, "fmt": "price"},
    {"key": "kc_z_score", "label": "Keltner Z-Score", "category": "Technicals", "pages": _BOTH, "fmt": "ratio2"},
    {"key": "kc_entry_signal", "label": "Keltner Entry Signal", "category": "Technicals", "pages": _BOTH, "fmt": "bool01"},
    {"key": "kc_exit_signal", "label": "Keltner Exit Signal", "category": "Technicals", "pages": _BOTH, "fmt": "bool01"},
    {"key": "price_q10", "label": "ML Quantile Low (Q10)", "category": "Technicals", "pages": _BOTH, "fmt": "price"},
    {"key": "price_q90", "label": "ML Quantile High (Q90)", "category": "Technicals", "pages": _BOTH, "fmt": "price"},
    {"key": "volume", "label": "Volume", "category": "Technicals", "pages": _BOTH, "fmt": "volume"},

    # Scores
    {"key": "quality_grade", "label": "Quality Grade", "category": "Scores", "pages": _BOTH, "fmt": "text"},
    {"key": "heat_index", "label": "Heat Index", "category": "Risk (X-ray)", "pages": ("portfolio",), "fmt": "text"},
    {"key": "pillar_confluence", "label": "Pillar Confluence", "category": "Scores", "pages": _BOTH, "fmt": "text"},
    {"key": "regime_weighted_score", "label": "Regime-Weighted Conviction Score", "category": "Scores", "pages": _BOTH, "fmt": "int"},
    {"key": "buy_recommendation", "label": "Buy Recommendation", "category": "Scores", "pages": _BOTH, "fmt": "text"},

    # Portfolio parity gaps (Watchlist already shows these as core columns)
    {"key": "target_price", "label": "Target Price", "category": "Targets", "pages": ("portfolio",), "fmt": "price"},
    {"key": "piotroski_f_score", "label": "Piotroski F-Score", "category": "Scores", "pages": ("portfolio",), "fmt": "int"},
    {"key": "altman_z_score", "label": "Altman Z-Score", "category": "Scores", "pages": ("portfolio",), "fmt": "ratio2"},
    {"key": "beneish_m_score", "label": "Beneish M-Score", "category": "Scores", "pages": ("portfolio",), "fmt": "ratio2"},
    {"key": "low_target", "label": "Low Target", "category": "Targets", "pages": ("portfolio",), "fmt": "price"},
    {"key": "high_target", "label": "High Target", "category": "Targets", "pages": ("portfolio",), "fmt": "price"},

    # Watchlist parity gap (Portfolio already shows this as a core column)
    {"key": "vp_exit_zone", "label": "Exit Target", "category": "Technicals", "pages": ("watchlist",), "fmt": "price"},

    # Risk (X-ray) — from xray_risk_cache/xray_dividend_cache, refreshed by the nightly X-ray job
    {"key": "xray_beta", "label": "Beta (X-ray)", "category": "Risk (X-ray)", "pages": _BOTH, "fmt": "ratio2"},
    {"key": "xray_annualized_vol", "label": "Annualised Vol (X-ray)", "category": "Risk (X-ray)", "pages": _BOTH, "fmt": "pct_from_fraction"},
    {"key": "xray_dividend_yield", "label": "Dividend Yield (Ghostfolio)", "category": "Risk (X-ray)", "pages": _BOTH, "fmt": "pct_raw"},

    # Earnings Volatility — from earnings_volatility, only populated within ~14 days of earnings
    {"key": "earnings_edge_score", "label": "Earnings Edge Score (pp)", "category": "Earnings Volatility", "pages": _BOTH, "fmt": "ratio2"},
    {"key": "earnings_implied_move", "label": "Implied Move %", "category": "Earnings Volatility", "pages": _BOTH, "fmt": "pct_raw"},

    # Position Sizing (rendered client-side by renderPositionSizing())
    {"key": "position_value", "label": "Suggested Position Value", "category": "Position Sizing", "pages": _BOTH, "fmt": "client"},
    {"key": "shares", "label": "Suggested Shares", "category": "Position Sizing", "pages": _BOTH, "fmt": "client"},
    {"key": "stop_price", "label": "Suggested Stop Price", "category": "Position Sizing", "pages": _BOTH, "fmt": "client"},
    {"key": "risk_amount", "label": "Risk Amount", "category": "Position Sizing", "pages": _BOTH, "fmt": "client"},
]

DEFAULT_PORTFOLIO_VIEWS = [
    {"name": "Fundamentals & Quality", "columns": [
        "ticker", "company_name", "price", "change", "score", "signal",
        "trailing_pe", "forward_pe", "price_to_book", "price_to_sales", "roe", "debt_to_equity",
        "dividend_yield", "free_cash_flow", "beta", "piotroski_f_score", "altman_z_score",
        "beneish_m_score", "quality_grade", "market_cap", "sector",
    ]},
    {"name": "Technical Signals", "columns": [
        "ticker", "company_name", "price", "change", "rsi", "macd", "trend_50d", "trend_200d",
        "sma_200", "atr_pct", "entry_zone", "exit_target", "vp_poc", "kc_z_score", "ml_conf",
        "var_95", "mom_1m", "mom_3m", "rel_strength_5d", "score", "signal",
    ]},
    {"name": "Position Targets", "columns": [
        "ticker", "company_name", "price", "change", "target_price", "low_target", "high_target",
        "stop_loss", "entry_zone", "exit_target", "shares", "position_value", "score", "signal",
        "setup_tags",
    ]},
]

DEFAULT_WATCHLIST_VIEWS = [
    {"name": "Fundamentals & Quality", "columns": [
        "ticker", "company_name", "price", "daily_change", "score", "signal",
        "trailing_pe", "forward_pe", "price_to_book", "price_to_sales", "roe", "debt_to_equity",
        "dividend_yield", "free_cash_flow", "beta", "piotroski", "altman_z",
        "beneish_m", "quality_grade", "market_cap", "sector",
    ]},
    {"name": "Technical Signals", "columns": [
        "ticker", "company_name", "price", "daily_change", "rsi", "macd", "trend_50d", "trend_200d",
        "sma_200", "atr_pct", "entry_zone", "vp_exit_zone", "vp_poc", "kc_z_score", "ml_conf",
        "var_95", "mom_1m", "mom_3m", "rel_strength_5d", "score", "signal",
    ]},
    {"name": "Position Targets", "columns": [
        "ticker", "company_name", "price", "daily_change", "target", "low_target", "high_target",
        "stop_loss", "entry_zone", "vp_exit_zone", "shares", "position_value", "score", "signal",
        "setup_tags",
    ]},
]

_NUMERIC_MISSING_SORT = -999999999


def _format_value(raw, fmt: str, currency: Optional[str]):
    """Returns (sort, display) for one cell."""
    if fmt == "client":
        return "", ""
    if raw is None:
        if fmt == "date":
            return "9999-12-31", "N/A"
        if fmt == "text":
            return "", "N/A"
        return _NUMERIC_MISSING_SORT, "N/A"

    if fmt == "pct_from_fraction":
        return float(raw), f"{raw * 100:,.2f}%"
    if fmt == "pct_raw":
        return float(raw), f"{raw:,.1f}%"
    if fmt == "ratio2":
        return float(raw), f"{raw:,.2f}"
    if fmt == "int":
        return float(raw), str(int(raw))
    if fmt == "price":
        return float(raw), _fmt_price(raw, currency)
    if fmt == "price_raw":
        return float(raw), _fmt_price(raw, currency, decimals=3, with_symbol=False)
    if fmt == "currency_usd":
        return float(raw), _fmt_currency(raw)
    if fmt == "volume":
        return float(raw), _fmt_volume(raw)
    if fmt == "date":
        return (raw if raw != "Unknown" else "9999-12-31"), (raw if raw != "Unknown" else "N/A")
    if fmt == "bool01":
        return int(bool(raw)), ("Yes" if raw else "No")
    return str(raw), str(raw)  # "text"


def columns_for_page(page: str) -> list:
    return [c for c in OPTIONAL_COLUMNS if page in c["pages"]]


def all_columns_for_page(page: str) -> list:
    core = PORTFOLIO_CORE_COLUMNS if page == "portfolio" else WATCHLIST_CORE_COLUMNS
    tagged_core = [{**c, "type": "core"} for c in core]
    tagged_optional = [{**c, "type": "optional"} for c in columns_for_page(page)]
    return tagged_core + tagged_optional


def build_optional_column_cells(row_dict: dict, page: str) -> list:
    """Returns [{key, sort, display}, ...] in columns_for_page(page) order.
    Client-rendered (Position Sizing) entries get an empty placeholder — those
    cells are filled in by renderPositionSizing() after the page loads."""
    currency = row_dict.get("currency")
    cells = []
    for col in columns_for_page(page):
        sort, display = _format_value(row_dict.get(col["key"]), col["fmt"], currency)
        cells.append({"key": col["key"], "sort": sort, "display": display, "client": col["fmt"] == "client"})
    return cells


def resolve_column_prefs(config_data: dict, page: str) -> dict:
    prefix = page.upper()
    ui_prefs = config_data.get("UI_PREFERENCES", {})
    return {
        "hidden_core_columns": ui_prefs.get(f"{prefix}_HIDDEN_CORE_COLUMNS", []) or [],
        "shown_optional_columns": ui_prefs.get(f"{prefix}_SHOWN_OPTIONAL_COLUMNS", []) or [],
    }


def resolve_views(config_data: dict, page: str) -> list:
    prefix = page.upper()
    ui_prefs = config_data.get("UI_PREFERENCES", {})
    saved = ui_prefs.get(f"{prefix}_VIEWS")
    if saved:
        return saved
    return DEFAULT_PORTFOLIO_VIEWS if page == "portfolio" else DEFAULT_WATCHLIST_VIEWS
