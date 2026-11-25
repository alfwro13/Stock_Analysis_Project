from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Optional

from . import store
from .client import GoogleClient
from .parse import QuotePage, parse_quote_page

SUFFIX_TO_EXCHANGE = {
    "L": "LON", "DE": "ETR", "PA": "EPA", "AS": "AMS", "MI": "BIT", "MC": "BME", "SW": "SWX",
    "ST": "STO", "TO": "TSE", "AX": "ASX", "HK": "HKG", "T": "TYO",
}
US_EXCHANGES = ("NASDAQ", "NYSE", "NYSEARCA", "NYSEAMERICAN", "BATS")

# Registry ticker -> Google index code. Each was checked against the live page; ^KS200 and ^IBEX have no known code yet.
INDEX_SYMBOLS = {
    "^FTSE": ("UKX", "INDEXFTSE"),
    "^FTMC": ("MCX", "INDEXFTSE"),
    "^GSPC": (".INX", "INDEXSP"),
    "^NDX": ("NDX", "INDEXNASDAQ"),
    "^DJI": (".DJI", "INDEXDJX"),
    "^RUT": ("RUT", "INDEXRUSSELL"),
    "^VIX": ("VIX", "INDEXCBOE"),
    "^N225": ("NI225", "INDEXNIKKEI"),
    "^HSI": ("HSI", "INDEXHANGSENG"),
    "000001.SS": ("000001", "SHA"),
    "^AXJO": ("XJO", "INDEXASX"),
    "^STOXX50E": ("SX5E", "INDEXSTOXX"),
    "^GDAXI": ("DAX", "INDEXDB"),
    "^FCHI": ("PX1", "INDEXEURO"),
}
NOT_FOUND_RETRY = timedelta(days=7)


@dataclass
class Resolved:
    symbol: str
    exchange: str
    page: QuotePage


def candidates(ticker: str) -> list[tuple[str, str]]:
    if ticker in INDEX_SYMBOLS:
        return [INDEX_SYMBOLS[ticker]]
    if ticker.startswith("^") or "=" in ticker:
        return []
    base, dot, suffix = ticker.rpartition(".")
    if dot:
        exchange = SUFFIX_TO_EXCHANGE.get(suffix.upper())
        return [(base.upper(), exchange)] if exchange and base else []
    return [(ticker.upper(), exchange) for exchange in US_EXCHANGES]


def kind_of(ticker: str) -> str:
    return "index" if ticker in INDEX_SYMBOLS else "listing"


def resolve(ticker: str, client: GoogleClient, now: datetime) -> Optional[Resolved]:
    """The quote page for `ticker`, trying the stored mapping first; None when Google has no page for it."""
    stored = store.get_symbol(ticker)
    if stored and stored.last_probe_status == "not_found" and stored.verified_at and now - stored.verified_at < NOT_FOUND_RETRY:
        return None
    tried = []
    if stored and stored.google_symbol and stored.google_exchange:
        tried.append((stored.google_symbol, stored.google_exchange))
    tried += [c for c in candidates(ticker) if c not in tried]
    if not tried:
        return None
    for symbol, exchange in tried:
        html = client.get_quote_page(ticker, symbol, exchange)
        if html is None:
            return None
        page = parse_quote_page(html, now)
        if page.found:
            store.save_symbol(ticker, symbol, exchange, kind_of(ticker), now, page.title, "found")
            return Resolved(symbol, exchange, page)
    store.save_symbol(ticker, None, None, kind_of(ticker), now, None, "not_found")
    return None
