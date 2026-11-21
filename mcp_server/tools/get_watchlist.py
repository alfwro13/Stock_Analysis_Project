import time
from typing import Annotated, Any, Literal, Optional

from pydantic import Field

from accounts.accounts_engine import native_currencies
from constants import (
    MCP_WATCHLIST_DEFAULT_ROWS,
    MCP_WATCHLIST_MAX_OFFSET,
    MCP_WATCHLIST_MAX_ROWS,
    ML_FEATURE_MAX_AGE_TRADING_DAYS,
)
from db.database import get_watchlist_tickers
from db.account_store import get_all_holding_price_limits, get_watchlist_account
from db.helpers import get_next_earnings_dates, parse_utc_epoch
from market_pulse import get_all_cached_pulse
from pages.page_data_signal_rows import fetch_watchlist_signal_rows
from score_analysis import (
    compute_regime_weighted_score_batch,
    evaluate_buy_recommendation_batch,
    evaluate_pillar_confluence_batch,
)
from utils import is_missing_sector, upcoming_iso_date
from xray_engine import BENCHMARK_SYMBOL

from ..discovery import Score, list_flags
from ..envelope import NIGHTLY_STALE_AFTER_SECONDS, envelope, iso_utc
from ..server import READ_ONLY, server
from .get_ticker_snapshot import cached_price

_UNITS = {
    "filters": "scope (all, held, or watchlist_only = not held), min_composite_score and only_buy_recommended all apply before paging; a ticker with no composite score never matches min_composite_score, and one with no Buy Recommendation never matches only_buy_recommended",
    "limit/offset": f"limit is 1 to {MCP_WATCHLIST_MAX_ROWS} (default {MCP_WATCHLIST_DEFAULT_ROWS}), offset counts matching tickers in the Watchlist page's order; a larger limit is refused. total_matches counts every match, truncated says more remain, and next_offset is the offset of the next page (null on the last). missing names only the tickers on the returned page",
    "compact": "when true a field whose value is null is left out of the row (price, position_targets and the like disappear when empty), so an absent field means not available, never zero",
    "price.value": "latest regular-session price in price.currency, in the instrument's own quote unit; GBp means pence, not pounds. Never an extended-hours tick",
    "price.day_change/day_change_pct": "change since the previous regular close: per share in price.currency, and percent. null when the price came from the nightly price",
    "price.stale": "true when the cached quote is past its refresh interval while a market is open",
    "held": "true when the ticker is also held in a Trading or Ghostfolio account; the Watchlist page lists held tickers too",
    "overall_signal/composite_score": "the nightly Quant Scan's System Verdict and its 0-100 composite score",
    "trend_50d/trend_200d": "UP when the nightly price is above the 50- or 200-day average, else DOWN; null when the average or price is missing",
    "rsi_14": "0-100",
    "atr_pct, mom_1m, mom_3m, week52_pct": "fractions (0.05 = 5%); week52_pct is the position inside the 52-week range, 0 at the low and 1 at the high",
    "ml_confidence": f"0-100: the latest model probability for the ticker; null once it is older than {ML_FEATURE_MAX_AGE_TRADING_DAYS} trading days",
    "trap_phase": "the ticker's current Market Trap & Recovery Monitor phase, null when it has none",
    "pillar_confluence": "bullish or bearish when at least two of the technical, statistical and ML pillars agree over the last 5 trading days and none disagrees; null means no confluence, not missing data",
    "buy_recommendation": "present only for a bullish pillar confluence with the inputs for a reward:risk estimate; recommended is true when risk_reward (a ratio) reaches the Settings minimum",
    "regime_weighted_score": "0-100 Regime-Weighted Conviction Score; null when the market regime vetoes it (Crash, or market stress at the threshold), no regime exists yet, or the ticker lacks a composite score or ML confidence",
    "position_targets": "the Watchlist's low_limit (buy) and high_limit (sell) prices in price.currency, null when unset",
    "next_earnings_date": "YYYY-MM-DD from the nightly Quant Scan / weekly Universe Fundamentals sync; null when unknown or when Yahoo has not published a date after the last report (a past date is never returned)",
    "signals_as_of": "UTC time of the nightly stock_signals row behind the signals; signals_stale is true when it is older than 5 days (a missed nightly run)",
}


def _trend(label: Optional[str], average: Optional[float], price: Optional[float]) -> Optional[str]:
    return label if price and average is not None else None


def _confluence(result: Optional[dict]) -> Optional[dict]:
    if not result or not result.get("confluence"):
        return None
    pillars = result["bullish_pillars"] if result["direction"] == "bullish" else result["bearish_pillars"]
    return {"direction": result["direction"], "pillars": pillars}


def _buy_recommendation(result: Optional[dict]) -> Optional[dict]:
    if not result:
        return None
    return {"recommended": result["recommended"], "risk_reward": result["risk_reward"]["risk_reward"]}


def _regime_score(result: Optional[dict]) -> Optional[dict]:
    return None if not result else {"score": result["score"], "regime": result["regime"]}


def _without_nulls(value: Any) -> Any:
    if not isinstance(value, dict):
        return value
    kept = {key: _without_nulls(item) for key, item in value.items()}
    return {key: item for key, item in kept.items() if item is not None and item != {}}


@server.tool(name="get_watchlist", annotations=READ_ONLY)
def get_watchlist(
    scope: Annotated[Literal["all", "held", "watchlist_only"], Field(description="all, only tickers you also hold, or only tickers you do not hold")] = "all",
    min_composite_score: Annotated[Optional[Score], Field(description="keep tickers whose composite score is at least this (0-100); a ticker with no score is left out")] = None,
    only_buy_recommended: Annotated[bool, Field(description="keep only tickers with a Buy Recommendation marked recommended")] = False,
    limit: Annotated[int, Field(ge=1, le=MCP_WATCHLIST_MAX_ROWS)] = MCP_WATCHLIST_DEFAULT_ROWS,
    offset: Annotated[int, Field(ge=0, le=MCP_WATCHLIST_MAX_OFFSET, description="matching tickers to skip, to read the next page")] = 0,
    compact: Annotated[bool, Field(description="leave out every field that is null, to shorten each row")] = False,
) -> dict[str, Any]:
    """Watchlist tickers with their key signals, as the Watchlist page shows them and in its order: regular-session price, System Verdict and composite score, trend, RSI, momentum, ML confidence, Trap Monitor phase, Signal Pillar Confluence, Buy Recommendation, Regime-Weighted Conviction Score, Position Targets and next earnings. Tickers you also hold are included and flagged held. Returns one page (default 25, at most 50 tickers); total_matches, truncated and next_offset say whether to ask for more with offset, and scope, min_composite_score and only_buy_recommended narrow the list first. A value that is not available is null and listed in missing."""
    tickers = list(dict.fromkeys(get_watchlist_tickers()))
    held = {ticker for ticker, flag in list_flags().items() if flag == "portfolio"}
    if scope != "all":
        tickers = [t for t in tickers if (t in held) == (scope == "held")]
    rows, _ = fetch_watchlist_signal_rows(BENCHMARK_SYMBOL, tickers)
    by_ticker = {row["ticker"]: dict(row) for row in rows}
    if min_composite_score is not None:
        tickers = [
            t for t in tickers
            if t in by_ticker and by_ticker[t]["composite_score"] is not None and by_ticker[t]["composite_score"] >= min_composite_score
        ]

    scored = tickers if only_buy_recommended else tickers[offset:offset + limit]
    confluence = evaluate_pillar_confluence_batch(scored)
    regime_scores = compute_regime_weighted_score_batch(scored)
    recommendations = evaluate_buy_recommendation_batch(
        scored, confluence_by_ticker=confluence, regime_score_by_ticker=regime_scores,
    )
    if only_buy_recommended:
        tickers = [t for t in tickers if (recommendations.get(t) or {}).get("recommended")]
    total = len(tickers)
    page = tickers[offset:offset + limit]

    pulse = get_all_cached_pulse()
    currencies = native_currencies(page)
    earnings = get_next_earnings_dates(page)
    account = get_watchlist_account()
    limits = get_all_holding_price_limits()

    entries = []
    epochs = []
    no_signals = []
    no_price = []
    for ticker in page:
        row = by_ticker.get(ticker)
        currency = (row or {}).get("currency") or currencies.get(ticker)
        priced = cached_price(ticker, currency, pulse)
        price = priced[0] if priced else None
        if priced:
            epochs.append(priced[2])
        else:
            no_price.append(ticker)

        signals_epoch = None
        if row is None:
            no_signals.append(ticker)
        else:
            signals_epoch = parse_utc_epoch(row["last_updated"]) or None
            epochs.append(signals_epoch)

        earnings_date = (earnings.get(ticker) or {}).get("next_earnings_date")
        target = limits.get((account["id"], ticker), {}) if account else {}
        name = row["resolved_company_name"] if row else None
        entry = {
            "ticker": ticker,
            "name": None if name == ticker else name,
            "sector": None if row is None or is_missing_sector(row["sector"]) else row["sector"],
            "currency": currency,
            "held": ticker in held,
            "price": price,
            "overall_signal": row["overall_signal"] if row else None,
            "composite_score": row["composite_score"] if row else None,
            "trend_50d": _trend(row["trend_50d"], row["ma_50_day"], row["current_price"]) if row else None,
            "trend_200d": _trend(row["trend_200d"], row["ma_200_day"], row["current_price"]) if row else None,
            "rsi_14": row["rsi_14"] if row else None,
            "atr_pct": row["atr_pct"] if row else None,
            "mom_1m": row["mom_1m"] if row else None,
            "mom_3m": row["mom_3m"] if row else None,
            "week52_pct": row["week52_pct"] if row else None,
            "ml_confidence": row["ml_confidence_score"] if row else None,
            "trap_phase": row["trap_phase"] if row else None,
            "pillar_confluence": _confluence(confluence.get(ticker)),
            "buy_recommendation": _buy_recommendation(recommendations.get(ticker)),
            "regime_weighted_score": _regime_score(regime_scores.get(ticker)),
            "position_targets": {"low_limit": target.get("low_limit"), "high_limit": target.get("high_limit")},
            "next_earnings_date": upcoming_iso_date(earnings_date),
            "signals_as_of": None if signals_epoch is None else iso_utc(signals_epoch),
            "signals_stale": signals_epoch is not None and time.time() - signals_epoch > NIGHTLY_STALE_AFTER_SECONDS,
        }
        entries.append(_without_nulls(entry) if compact else entry)

    missing = []
    if no_signals:
        missing.append(f"{len(no_signals)} Watchlist ticker(s) have no stock_signals row yet, so their signals are null: " + ", ".join(no_signals))
    if no_price:
        missing.append(f"{len(no_price)} Watchlist ticker(s) have no cached price, so price is null: " + ", ".join(no_price))
    known = [e for e in epochs if e is not None]
    next_offset = offset + len(entries) if offset + len(entries) < total else None
    return envelope(
        {"count": len(entries), "total_matches": total, "truncated": next_offset is not None, "next_offset": next_offset, "tickers": entries},
        as_of=min(known) if known else None, units=_UNITS, missing=missing,
        stale_after_seconds=NIGHTLY_STALE_AFTER_SECONDS,
    )
