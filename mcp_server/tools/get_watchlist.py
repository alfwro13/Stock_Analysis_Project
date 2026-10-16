import time
from typing import Any, Optional

from accounts_engine import get_combined_holdings, native_currencies
from database import get_watchlist_tickers
from db_accounts import get_all_holding_price_limits, get_watchlist_account
from db_helpers import get_next_earnings_dates, parse_utc_epoch
from market_pulse import get_all_cached_pulse
from page_data_signal_rows import fetch_watchlist_signal_rows
from score_analysis import (
    compute_regime_weighted_score_batch,
    evaluate_buy_recommendation_batch,
    evaluate_pillar_confluence_batch,
)
from utils import is_iso_date, is_missing_sector
from xray_engine import BENCHMARK_SYMBOL

from ..envelope import NIGHTLY_STALE_AFTER_SECONDS, envelope, iso_utc
from ..server import READ_ONLY, server
from .get_ticker_snapshot import cached_price

_UNITS = {
    "price.value": "latest regular-session price in price.currency, in the instrument's own quote unit; GBp means pence, not pounds. Never an extended-hours tick",
    "price.day_change/day_change_pct": "change since the previous regular close: per share in price.currency, and percent. null when the price came from the nightly price",
    "price.stale": "true when the cached quote is past its refresh interval while a market is open",
    "held": "true when the ticker is also held in a Trading or Ghostfolio account; the Watchlist page lists held tickers too",
    "overall_signal/composite_score": "the nightly Quant Scan's System Verdict and its 0-100 composite score",
    "trend_50d/trend_200d": "UP when the nightly price is above the 50- or 200-day average, else DOWN; null when the average or price is missing",
    "rsi_14": "0-100",
    "atr_pct, mom_1m, mom_3m, week52_pct": "fractions (0.05 = 5%); week52_pct is the position inside the 52-week range, 0 at the low and 1 at the high",
    "ml_confidence": "0-100: the latest model probability for the ticker, with no timestamp of its own",
    "trap_phase": "the ticker's current Market Trap & Recovery Monitor phase, null when it has none",
    "pillar_confluence": "bullish or bearish when at least two of the technical, statistical and ML pillars agree over the last 5 trading days and none disagrees; null means no confluence, not missing data",
    "buy_recommendation": "present only for a bullish pillar confluence with the inputs for a reward:risk estimate; recommended is true when risk_reward (a ratio) reaches the Settings minimum",
    "regime_weighted_score": "0-100 Regime-Weighted Conviction Score; null when the market regime vetoes it (Crash, or market stress at the threshold), no regime exists yet, or the ticker lacks a composite score or ML confidence",
    "position_targets": "the Watchlist's low_limit (buy) and high_limit (sell) prices in price.currency, null when unset",
    "next_earnings_date": "YYYY-MM-DD from the nightly Quant Scan / weekly Universe Fundamentals sync, null when unknown",
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


@server.tool(name="get_watchlist", annotations=READ_ONLY)
def get_watchlist() -> dict[str, Any]:
    """Every Watchlist ticker with its key signals, as the Watchlist page shows them: regular-session price, System Verdict and composite score, trend, RSI, momentum, ML confidence, Trap Monitor phase, Signal Pillar Confluence, Buy Recommendation, Regime-Weighted Conviction Score, Position Targets and next earnings. Tickers you also hold are included and flagged held. A value that is not available is null and listed in missing."""
    tickers = list(dict.fromkeys(get_watchlist_tickers()))
    rows, _ = fetch_watchlist_signal_rows(BENCHMARK_SYMBOL, tickers)
    by_ticker = {row["ticker"]: dict(row) for row in rows}

    held = set(get_combined_holdings())
    pulse = get_all_cached_pulse()
    currencies = native_currencies(tickers)
    earnings = get_next_earnings_dates(tickers)
    confluence = evaluate_pillar_confluence_batch(tickers)
    regime_scores = compute_regime_weighted_score_batch(tickers)
    recommendations = evaluate_buy_recommendation_batch(
        tickers, confluence_by_ticker=confluence, regime_score_by_ticker=regime_scores,
    )
    account = get_watchlist_account()
    limits = get_all_holding_price_limits()

    entries = []
    epochs = []
    no_signals = []
    no_price = []
    for ticker in tickers:
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
        entries.append({
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
            "next_earnings_date": earnings_date if is_iso_date(earnings_date) else None,
            "signals_as_of": None if signals_epoch is None else iso_utc(signals_epoch),
            "signals_stale": signals_epoch is not None and time.time() - signals_epoch > NIGHTLY_STALE_AFTER_SECONDS,
        })

    missing = []
    if no_signals:
        missing.append(f"{len(no_signals)} Watchlist ticker(s) have no stock_signals row yet, so their signals are null: " + ", ".join(no_signals))
    if no_price:
        missing.append(f"{len(no_price)} Watchlist ticker(s) have no cached price, so price is null: " + ", ".join(no_price))
    known = [e for e in epochs if e is not None]
    return envelope(
        {"count": len(entries), "tickers": entries},
        as_of=min(known) if known else None, units=_UNITS, missing=missing,
        stale_after_seconds=NIGHTLY_STALE_AFTER_SECONDS,
    )
