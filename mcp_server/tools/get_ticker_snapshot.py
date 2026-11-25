import time
from typing import Annotated, Any, Optional

from mcp.server.mcpserver.exceptions import ToolError
from pydantic import Field

from accounts.accounts_engine import current_price_map, get_combined_holdings, native_currencies, oldest_quote_epoch, position_target_accounts
from config import load_config
from constants import MCP_TICKER_MAX_CHARS, ML_FEATURE_MAX_AGE_TRADING_DAYS, QUOTE_CLOSE_GRACE_SECONDS
from db.database import get_watchlist_tickers
from db.account_store import get_holding_price_limits_for_ticker
from db.helpers import get_next_earnings_dates, parse_utc_epoch
from market_pulse import QuoteSessionJudge, get_all_cached_pulse, is_price_fresh
from market_session_helpers import extended_quote_from_snapshot, regular_change_from_snapshot
from pages.page_data_stock import QUANT_ONLY_SIGNAL, UNKNOWN_SIGNAL, load_stock_row
from price_history_helpers import last_session_change
from utils import cached_fundamentals_extra, is_excluded_from_yahoo_fetch, is_iso_date, is_missing_sector, upcoming_iso_date
from yahoo_engine import yahoo_engine

from ..envelope import ML_CONFIDENCE_UNITS, NIGHTLY_STALE_AFTER_SECONDS, date_epoch, dated_part, envelope, iso_utc
from ..scope import valid_ticker
from ..server import READ_ONLY, server

TickerArg = Annotated[str, Field(max_length=MCP_TICKER_MAX_CHARS)]

_UNITS = {
    "price.value": "latest regular-session price in price.currency, in the instrument's own quote unit; GBp means pence, not pounds. Never an extended-hours tick",
    "price.source": "shared_cache: the Market Pulse cache; nightly_price: the nightly stock_signals price, used when the cache was last refreshed before the ticker's own exchange's last close or is stuck behind the nightly price (or the cache has no price); yahoo_live: one live Yahoo quote, used only when no cached price exists, and nothing is stored",
    "price.day_change/day_change_pct": "change since the previous regular close: per share in price.currency, and percent. For nightly_price it is the move between the last two stored daily closes, and null when the stored history does not end at that price",
    "price.stale": f"shared_cache and nightly_price: judged on the ticker's own exchange session, not the envelope's markets. While that exchange is open, true when the quote is older than the dashboard's refresh floor (5 minutes or twice the refresh interval); while it is closed, true when the quote was taken more than {QUOTE_CLOSE_GRACE_SECONDS // 60} minutes before that exchange's last close, so a Watchlist quote nothing has refreshed since an earlier session is stale. A mutual fund (one NAV a day) and a Markets-registry instrument are never stale for being past the last close. yahoo_live is never stale",
    "price.market_state": "Yahoo's own session state (REGULAR, PRE, POST, CLOSED ...); only present for yahoo_live",
    "extended_hours": "pre- or post-market price in price.currency and its change in percent versus the regular close; a separate object, never mixed into price; null outside extended hours. as_of is the UTC time of the tick itself (Yahoo's preMarketTime / postMarketTime), not of the dashboard's last fetch, and null when Yahoo sent none; a thinly traded name's last print can be old, and there is no stale flag: compare as_of with the envelope's clock",
    "trend": "trend_50d/trend_200d: UP when the nightly stock_signals price is above the SMA, else DOWN; null when the SMA or price is missing. SMAs and 52-week range in price.currency; atr_stop_loss is a price",
    "technicals": "rsi_14 0-100; macd, macd_signal, macd_hist in price units; atr_pct, hist_vol_20, mom_* and rel_strength_* are fractions (0.05 = 5%); volume in shares; close_price is the quant scan's daily close",
    "valuation": "trailing_pe, forward_pe, peg_ratio, peter_lynch_peg, price_to_book, price_to_sales, beta are ratios; dividend_yield, short_interest and institutional_ownership are fractions (0.03 = 3%); target_price is the analyst mean target in price.currency; ex_dividend_date is YYYY-MM-DD",
    "fundamentals": "roe, profit_margin and revenue_growth are fractions (0.15 = 15%); debt_to_equity is on Yahoo's own scale where 30 means 30% (not 0.30); current_ratio is a ratio; free_cash_flow and operating_cash_flow are amounts in the company's financial currency (extra.financial_currency)",
    "fundamentals.extra": "from the cached Yahoo company dump: market_cap in the major unit of the quote currency (GBP for a GBp quote); eps, free_cash_flow, total_debt, total_cash and net_cash in financial_currency; roa, earnings_growth, insider_ownership and payout_ratio are fractions; quick_ratio a ratio; average_volume in shares; null when the dump is not cached",
    "forensic_scores": "piotroski_f_score 0-9 (higher is stronger); altman_z_score is the non-manufacturer Z' (above 2.6 safe, 1.1 to 2.6 grey, below 1.1 distress); beneish_m_score above -1.78 signals possible manipulation; each is null when too few inputs could be computed; as_of is the time of the last forensic run",
    "ml": (
        f"confidence is {ML_CONFIDENCE_UNITS}; as_of is the UTC time it was scored. price_q10/price_q90 are the ML Quantile "
        f"Price Band in price.currency, each null once it was scored more than {ML_FEATURE_MAX_AGE_TRADING_DAYS} trading days ago"
    ),
    "position": "held: in any Trading or Ghostfolio account; total_shares across them",
    "position_targets": "one row per Trading account holding the ticker, plus the Watchlist when it is watched; low_limit (buy) and high_limit (sell) are prices in price.currency, null when unset; shares is null for the Watchlist",
    "next_earnings": "date is YYYY-MM-DD from the nightly Quant Scan / weekly Universe Fundamentals sync; null when unknown or when Yahoo has not published a date after the last report (a past date is never returned)",
    "<part>.as_of": "the data date or UTC time of the oldest source behind that part; stale is true when it is older than 5 days (a missed nightly run)",
}


def _resolve(ticker: str) -> tuple:
    stripped = valid_ticker(ticker)
    holdings = get_combined_holdings()
    watchlist = set(get_watchlist_tickers())
    for candidate in dict.fromkeys((stripped, stripped.upper())):
        row, _ = load_stock_row(candidate)
        asset = holdings.get(candidate)
        watched = candidate in watchlist
        if row["overall_signal"] != UNKNOWN_SIGNAL or asset or watched:
            return candidate, row, asset, watched
    raise ToolError(
        "Unknown ticker. get_ticker_snapshot serves tickers the dashboard already tracks: "
        "a Portfolio or Watchlist holding, or a stock in its universe scan."
    )


def _oldest(*epochs) -> Optional[float]:
    known = [e for e in epochs if e]
    return min(known) if known else None


def _extended_part(session: str, price: float, change: Optional[float], change_pct: Optional[float], epoch: Optional[float]) -> dict:
    return {
        "session": session, "price": price, "change": change, "change_pct": change_pct,
        "as_of": None if epoch is None else iso_utc(epoch),
    }


def _pulse_extended(row: dict) -> Optional[dict]:
    if row["extended_price"] is None:
        return None
    return _extended_part(
        row["extended_session"], row["extended_price"], row["extended_change_pts"], row["extended_change_pct"],
        row["extended_quote_time"],
    )


def _snapshot_extended(snapshot: dict) -> Optional[dict]:
    tick = extended_quote_from_snapshot(snapshot)
    return None if tick is None else _extended_part(tick["session"], tick["price"], tick["change"], tick["change_pct"], tick["time"])


def cached_price(ticker: str, currency: Optional[str], pulse: Optional[dict] = None) -> Optional[tuple]:
    """(price part, extended-hours part, epoch of the quote) from the shared caches, or None when neither holds a price. pulse is a get_all_cached_pulse() result a caller already loaded."""
    cached = (get_all_cached_pulse() if pulse is None else pulse).get(ticker)
    if cached and cached["price"]:
        epoch = cached["price_as_of"]
        day_change, day_change_pct = cached["change_pts"], cached["change_pct"]
        if cached["used_fallback"]:
            day_change, day_change_pct = last_session_change(ticker, cached["price"]) or (None, None)
        return {
            "value": cached["price"], "currency": currency, "source": "nightly_price" if cached["used_fallback"] else "shared_cache",
            "as_of": None if epoch is None else iso_utc(epoch), "stale": cached["is_stale"],
            "day_change": day_change, "day_change_pct": day_change_pct,
        }, _pulse_extended(cached), epoch

    priced = current_price_map([ticker]).get(ticker)
    if priced and priced[0]:
        epoch = oldest_quote_epoch([ticker])
        price_currency = priced[1] or currency
        refresh_rate = int(load_config().get("UI_PREFERENCES", {}).get("REFRESH_RATE", 60))
        market_open, floor = QuoteSessionJudge([ticker]).session(ticker, price_currency)
        day_change, day_change_pct = last_session_change(ticker, priced[0]) or (None, None)
        return {
            "value": priced[0], "currency": price_currency, "source": "nightly_price",
            "as_of": None if epoch is None else iso_utc(epoch),
            "stale": epoch is not None and not is_price_fresh(epoch, priced[0], refresh_rate, market_open, floor),
            "day_change": day_change, "day_change_pct": day_change_pct,
        }, None, epoch
    return None


def _price(ticker: str, currency: Optional[str], missing: list) -> tuple:
    """(price part, extended-hours part, epoch of the quote): shared cache first, one live Yahoo quote only when no cached price exists."""
    cached = cached_price(ticker, currency)
    if cached:
        if cached[0]["day_change"] is None:
            missing.append("No day change is available for the cached price: price.day_change is null")
        return cached

    snapshot = None if is_excluded_from_yahoo_fetch(ticker) else yahoo_engine.get_quote_snapshot(ticker)
    if snapshot is None:
        missing.append("No cached price and no live Yahoo quote is available: price is null")
        return None, None, None
    now = time.time()
    day_change, day_change_pct = regular_change_from_snapshot(snapshot, ticker) or (None, None)
    return {
        "value": snapshot["regular_price"], "currency": currency, "source": "yahoo_live",
        "as_of": iso_utc(now), "stale": False,
        "day_change": day_change, "day_change_pct": day_change_pct,
        "previous_close": snapshot["regular_previous_close"], "market_state": snapshot["market_state"],
    }, _snapshot_extended(snapshot), now


def _position_targets(ticker: str, asset: Optional[dict], watched: bool) -> list:
    limits = get_holding_price_limits_for_ticker(ticker)

    def limits_of(account_id: int) -> dict:
        found = limits.get(account_id) or {}
        return {"low_limit": found.get("low_limit"), "high_limit": found.get("high_limit")}

    return [
        {
            "account_type": row["account_type"], "scope_id": row["scope_id"], "account_name": row["name"],
            "shares": row["shares"], **limits_of(row["account_id"]),
        }
        for row in position_target_accounts(asset, watched)
    ]


def _flag(value) -> Optional[bool]:
    return None if value is None else bool(value)


def _trend(row: dict) -> dict:
    price_known = bool(row["current_price"])
    return {
        "trend_50d": row["trend_50d"] if price_known and row["sma_50"] is not None else None,
        "trend_200d": row["trend_200d"] if price_known and row["sma_200"] is not None else None,
        "sma_50": row["sma_50"], "sma_200": row["sma_200"],
        "fifty_two_week_low": row["fifty_two_week_low"], "fifty_two_week_high": row["fifty_two_week_high"],
        "atr_stop_loss": row["atr_stop_loss"],
    }


def _technicals(row: dict, covered: bool) -> dict:
    return {
        "rsi_14": row["rsi_14"], "macd": row["macd"], "macd_signal": row["macd_signal"], "macd_hist": row["macd_hist"],
        "atr_pct": row["atr_pct"], "hist_vol_20": row["hist_vol_20"],
        "mom_1m": row["mom_1m"], "mom_3m": row["mom_3m"], "mom_6m": row["mom_6m"], "mom_12m_skip1m": row["mom_12m_skip1m"],
        "rel_strength_5d": row["rel_strength_5d"], "rel_strength_20d": row["rel_strength_20d"],
        "volume": row["volume"], "volume_surge": _flag(row["volume_surge"]), "bullish_cross": _flag(row["bullish_cross"]),
        "close_price": row["quant_close_price"] if covered else row["current_price"] or None,
    }


def _valuation(row: dict) -> dict:
    return {key: row[key] for key in (
        "trailing_pe", "forward_pe", "peg_ratio", "peter_lynch_peg", "price_to_book", "price_to_sales", "dividend_yield",
        "ex_dividend_date", "target_price", "analyst_rating", "beta", "short_interest", "institutional_ownership",
    )}


def _fundamentals(row: dict) -> dict:
    extra = cached_fundamentals_extra(row["ticker"])
    if extra is not None:
        extra.pop("website")
    return {
        "roe": row["roe"], "debt_to_equity": row["debt_to_equity"], "profit_margin": row["profit_margin"],
        "revenue_growth": row["revenue_growth"], "current_ratio": row["current_ratio"],
        "free_cash_flow": row["free_cash_flow"], "operating_cash_flow": row["operating_cash_flow"],
        "extra": extra,
    }


@server.tool(name="get_ticker_snapshot", annotations=READ_ONLY)
def get_ticker_snapshot(ticker: TickerArg) -> dict[str, Any]:
    """One-stop view of a ticker the dashboard already tracks (held, watched or in its universe scan): regular-session price with extended hours kept apart, trend, technicals, valuation, fundamentals, forensic scores, ML confidence, Position Targets per account and next earnings. Each part has its own as_of and stale flag; a part that is not available is null and listed in missing. When no cached price exists the tool makes one live Yahoo quote request and stores nothing."""
    ticker, row, asset, watched = _resolve(ticker)
    missing: list[str] = []
    epochs: list = []
    covered = row["overall_signal"] not in (UNKNOWN_SIGNAL, QUANT_ONLY_SIGNAL)
    in_universe = row["overall_signal"] != UNKNOWN_SIGNAL
    currency = native_currencies([ticker]).get(ticker)
    if currency is None:
        missing.append("The quote currency is not known, so price.currency is null")

    price, extended, price_epoch = _price(ticker, currency, missing)
    epochs.append(price_epoch)
    if extended is not None and extended["as_of"] is None:
        missing.append("Yahoo sent no time for the extended-hours tick: extended_hours.as_of is null")

    signals_epoch = parse_utc_epoch(row["last_updated"]) or None
    quant_epoch = date_epoch(row["quant_date"])
    trend = technicals = ml = valuation = fundamentals = forensic = None
    if in_universe:
        technical_epoch = _oldest(signals_epoch if covered else None, quant_epoch)
        trend = dated_part(technical_epoch, _trend(row))
        technicals = dated_part(technical_epoch, _technicals(row, covered))
        ml_epoch = parse_utc_epoch(row["ml_scored_at"]) or None
        ml = dated_part(ml_epoch, {
            "confidence": row["ml_confidence_score"], "price_q10": row["price_q10"], "price_q90": row["price_q90"],
        })
        epochs.extend((technical_epoch, ml_epoch))
        if quant_epoch is None:
            missing.append("No quant_signals row: technicals and ML values are null")
    else:
        missing.append("No stock_signals or quant_signals data yet: trend, technicals, valuation, fundamentals, forensic scores, ML values and earnings are null")
    if covered:
        valuation = dated_part(signals_epoch, _valuation(row))
        fundamentals = dated_part(signals_epoch, _fundamentals(row))
        forensic_epoch = parse_utc_epoch(row["forensic_last_updated"]) or None
        forensic = {
            "as_of": None if forensic_epoch is None else iso_utc(forensic_epoch), "stale": False,
            "piotroski_f_score": row["piotroski_f_score"], "altman_z_score": row["altman_z_score"],
            "beneish_m_score": row["beneish_m_score"],
        }
        epochs.append(signals_epoch)
        if row["quote_type"] == "EQUITY" and forensic_epoch is None:
            missing.append("Forensic scores have not been computed for this ticker yet")
    elif in_universe:
        missing.append("Universe scan only (no stock_signals row): valuation, fundamentals, forensic scores and earnings are null")

    stored_earnings = (get_next_earnings_dates([ticker]).get(ticker) or {}).get("next_earnings_date")
    earnings_date = upcoming_iso_date(stored_earnings)
    if is_iso_date(stored_earnings) and earnings_date is None:
        missing.append("Yahoo has not published the next earnings date yet: the last one stored is in the past")
    next_earnings = {"as_of": None if signals_epoch is None else iso_utc(signals_epoch), "date": earnings_date} if covered else None

    sector = None if is_missing_sector(row["sector"]) else row["sector"]
    known = [e for e in epochs if e is not None]
    return envelope(
        {
            "ticker": ticker,
            "name": row["company_name"] if in_universe and row["company_name"] != ticker else None,
            "coverage": "full" if covered else "universe_scan_only" if in_universe else "none",
            "sector": sector if in_universe else None, "industry": row["industry"] if in_universe else None,
            "country": row["country"] if covered else None, "quote_type": row["quote_type"] if covered else None,
            "price": price, "extended_hours": extended, "trend": trend, "technicals": technicals,
            "valuation": valuation, "fundamentals": fundamentals, "forensic_scores": forensic, "ml": ml,
            "position": {"held": bool(asset), "in_watchlist": watched, "total_shares": asset["global_shares"] if asset else None},
            "position_targets": _position_targets(ticker, asset, watched),
            "next_earnings": next_earnings,
        },
        as_of=min(known) if known else None, units=_UNITS, missing=missing,
        stale_after_seconds=NIGHTLY_STALE_AFTER_SECONDS,
    )
