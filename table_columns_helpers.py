from typing import Optional

from page_helpers import _fmt_currency, _fmt_volume, _fmt_price

PORTFOLIO_CORE_COLUMNS = [
    {"key": "ticker", "label": "Ticker", "pinned": True},
    {"key": "company_name", "label": "Company Name", "pinned": False},
    {"key": "price", "label": "Price", "pinned": False},
    {"key": "change", "label": "Change", "pinned": False},
    {"key": "global_value", "label": "Global Value", "pinned": False},
    {"key": "global_pnl", "label": "Global P&L", "pinned": False},
    {"key": "trend_50d", "label": "50D", "pinned": False},
    {"key": "trend_200d", "label": "200D", "pinned": False},
    {"key": "peg_ratio", "label": "PEG", "pinned": False},
    {"key": "pl_peg", "label": "PL PEG", "pinned": False},
    {"key": "stop_loss", "label": "Stop-Loss", "pinned": False},
    {"key": "entry_zone", "label": "Entry Zone", "pinned": False},
    {"key": "exit_target", "label": "Exit Target", "pinned": False},
    {"key": "rsi", "label": "RSI", "pinned": False},
    {"key": "ml_conf", "label": "ML Conf", "pinned": False},
    {"key": "var_95", "label": "VaR (95%)", "pinned": False},
    {"key": "sentiment", "label": "Sentiment", "pinned": False},
    {"key": "earnings", "label": "Earnings", "pinned": False},
    {"key": "score", "label": "Score", "pinned": False},
    {"key": "setup_tags", "label": "Setups & Tags", "pinned": False},
    {"key": "signal", "label": "Signal", "pinned": False},
]

WATCHLIST_CORE_COLUMNS = [
    {"key": "ticker", "label": "Ticker", "pinned": True},
    {"key": "company_name", "label": "Company Name", "pinned": False},
    {"key": "price", "label": "Price", "pinned": False},
    {"key": "daily_change", "label": "Daily Change", "pinned": False},
    {"key": "target", "label": "Target", "pinned": False},
    {"key": "trend_50d", "label": "50D", "pinned": False},
    {"key": "trend_200d", "label": "200D", "pinned": False},
    {"key": "peg_ratio", "label": "PEG", "pinned": False},
    {"key": "pl_peg", "label": "PL PEG", "pinned": False},
    {"key": "stop_loss", "label": "Stop-Loss", "pinned": False},
    {"key": "entry_zone", "label": "Entry Zone", "pinned": False},
    {"key": "rsi", "label": "RSI", "pinned": False},
    {"key": "ml_conf", "label": "ML Conf", "pinned": False},
    {"key": "var_95", "label": "VaR (95%)", "pinned": False},
    {"key": "sentiment", "label": "Sentiment", "pinned": False},
    {"key": "earnings", "label": "Earnings", "pinned": False},
    {"key": "score", "label": "Score", "pinned": False},
    {"key": "piotroski", "label": "Piotroski", "pinned": False},
    {"key": "altman_z", "label": "Altman Z", "pinned": False},
    {"key": "beneish_m", "label": "Beneish M", "pinned": False},
    {"key": "low_target", "label": "Low Target", "pinned": False},
    {"key": "high_target", "label": "High Target", "pinned": False},
    {"key": "setup_tags", "label": "Setups & Tags", "pinned": False},
    {"key": "signal", "label": "Signal", "pinned": False},
]

_BOTH = ("portfolio", "watchlist")

# fmt values: pct_from_fraction, pct_raw, ratio2, price, price_raw, currency_usd,
# volume, date, text, bool01, int. "client" columns have no server-computed
# sort/display — they're rendered by renderPositionSizing() in portfolio.js/watchlist.js.
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

    # Portfolio parity gaps (Watchlist already shows these as core columns)
    {"key": "target_price", "label": "Target Price", "category": "Targets", "pages": ("portfolio",), "fmt": "price"},
    {"key": "piotroski_f_score", "label": "Piotroski F-Score", "category": "Scores", "pages": ("portfolio",), "fmt": "int"},
    {"key": "altman_z_score", "label": "Altman Z-Score", "category": "Scores", "pages": ("portfolio",), "fmt": "ratio2"},
    {"key": "beneish_m_score", "label": "Beneish M-Score", "category": "Scores", "pages": ("portfolio",), "fmt": "ratio2"},
    {"key": "low_target", "label": "Low Target", "category": "Targets", "pages": ("portfolio",), "fmt": "price"},
    {"key": "high_target", "label": "High Target", "category": "Targets", "pages": ("portfolio",), "fmt": "price"},

    # Watchlist parity gap (Portfolio already shows this as a core column)
    {"key": "vp_exit_zone", "label": "Exit Target", "category": "Technicals", "pages": ("watchlist",), "fmt": "price"},

    # Position Sizing (rendered client-side by renderPositionSizing())
    {"key": "position_value", "label": "Suggested Position Value", "category": "Position Sizing", "pages": _BOTH, "fmt": "client"},
    {"key": "shares", "label": "Suggested Shares", "category": "Position Sizing", "pages": _BOTH, "fmt": "client"},
    {"key": "stop_price", "label": "Suggested Stop Price", "category": "Position Sizing", "pages": _BOTH, "fmt": "client"},
    {"key": "risk_amount", "label": "Risk Amount", "category": "Position Sizing", "pages": _BOTH, "fmt": "client"},
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
