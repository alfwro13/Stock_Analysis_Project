import logging
import time
from datetime import datetime, timezone
from typing import Optional

from accounts_engine import (
    _EPS, _fx, _gross_base, account_summary, current_price_map, derive_account_holdings,
    holdings_with_market_value, refresh_performance_cache, scraped_price_performance, total_value,
)
from config import BASE_CURRENCY
from database import get_connection
from db_accounts import (
    get_accounts, get_all_holding_price_limits, get_performance_cache, get_price_history,
    get_transactions, get_value_history, get_value_history_currency, upsert_holding_price_limit,
)
from portfolio_service import get_rate_to_base

logger = logging.getLogger(__name__)


def _stock_signals_map(tickers: list) -> dict:
    """Batched stock_signals lookup keyed by ticker, for the technicals holdings_with_metrics_all_accounts() needs."""
    if not tickers:
        return {}
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(tickers))
        cursor.execute(
            f"""SELECT ticker, quote_type, rsi_14, trend_50d, trend_200d, next_earnings_date
                FROM stock_signals WHERE ticker IN ({placeholders})""",
            tickers
        )
        return {r["ticker"]: dict(r) for r in cursor.fetchall()}
    except Exception as e:
        logger.error("Failed to load stock_signals map: %s", e)
        return {}
    finally:
        if conn:
            conn.close()


def _market_pulse_change_map(tickers: list) -> dict:
    """Batched market_pulse_cache change_pts/change_pct lookup, mirroring current_price_map()'s
    own market_pulse_cache query style."""
    if not tickers:
        return {}
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        placeholders = ",".join("?" * len(tickers))
        cursor.execute(
            f"SELECT ticker, change_pts, change_pct FROM market_pulse_cache WHERE ticker IN ({placeholders})",
            tickers
        )
        return {r["ticker"]: {"change_pts": r["change_pts"], "change_pct": r["change_pct"]} for r in cursor.fetchall()}
    except Exception as e:
        logger.error("Failed to load market_pulse_cache change map: %s", e)
        return {}
    finally:
        if conn:
            conn.close()


def _dividends_by_ticker(account_id: int) -> dict:
    """Sum of _gross_base() over Dividend transactions, grouped by ticker, for one account."""
    totals: dict = {}
    for txn in get_transactions(account_id):
        if txn["txn_type"] == "Dividend" and txn["ticker"]:
            totals[txn["ticker"]] = totals.get(txn["ticker"], 0.0) + _gross_base(txn)
    return {t: round(v, 2) for t, v in totals.items()}


def holdings_with_metrics_all_accounts() -> dict:
    """One row per (account_id, ticker) across every non-deleted Trading account — the Home
    Assistant integration's Phase 3 per-holding sensor data source. Reuses
    holdings_with_market_value() per account rather than rewriting the ledger math, then enriches
    each row with native price, stock_signals technicals, 24h change, dividends, and stored price
    limits. Holdings are deliberately NOT merged across accounts — the same ticker held in two
    accounts produces two separate rows, matching how the Home Assistant integration surfaces
    each account as its own device."""
    accounts = _trading_accounts()
    account_holdings: dict = {}
    all_tickers: set = set()
    for acc in accounts:
        holdings = holdings_with_market_value(acc["id"])
        account_holdings[acc["id"]] = holdings
        all_tickers.update(h["ticker"] for h in holdings)

    price_map = current_price_map(list(all_tickers))
    signals_map = _stock_signals_map(list(all_tickers))
    pulse_map = _market_pulse_change_map(list(all_tickers))
    limits_map = get_all_holding_price_limits()

    rows = []
    for acc in accounts:
        account_id = acc["id"]
        dividends_by_ticker = _dividends_by_ticker(account_id)
        for h in account_holdings[account_id]:
            ticker = h["ticker"]
            priced = price_map.get(ticker)
            has_price = bool(priced and priced[0])
            native_price, native_currency = priced if has_price else (None, h["currency"])
            market_price_in_base = None
            if native_price is not None:
                market_price_in_base = round(native_price * get_rate_to_base(native_currency or h["currency"]), 4)
            signals = signals_map.get(ticker, {})
            pulse = pulse_map.get(ticker, {})
            limits = limits_map.get((account_id, ticker), {})
            low_limit = limits.get("low_limit")
            high_limit = limits.get("high_limit")
            gain_value = round(h["market_value"] - h["total_investment"], 2)
            rows.append({
                "account_id": account_id,
                "account_name": acc["name"],
                "ticker": ticker,
                "company_name": h["company_name"],
                "shares": h["shares"],
                "currency_asset": h["currency"],
                "currency_base": BASE_CURRENCY,
                "market_price": native_price,
                "market_price_currency": native_currency,
                "market_price_in_base_currency": market_price_in_base,
                "average_buy_price": h["buy_price"],
                "average_buy_price_currency": BASE_CURRENCY,
                "market_value": h["market_value"],
                "total_investment": h["total_investment"],
                "gain_value": gain_value,
                "gain_value_currency": BASE_CURRENCY,
                "gain_pct": h["performance_pct"],
                "profit_and_loss": gain_value,
                "accumulated_dividends": dividends_by_ticker.get(ticker, 0.0),
                "accumulated_dividends_currency": BASE_CURRENCY,
                "trend_vs_buy": "up" if (market_price_in_base is not None and h["buy_price"] and market_price_in_base >= h["buy_price"]) else "down",
                "asset_class": signals.get("quote_type"),
                "data_source": "YAHOO",
                "market_change_24h": pulse.get("change_pts"),
                "market_change_pct_24h": pulse.get("change_pct"),
                "rsi": signals.get("rsi_14"),
                "trend_50d": signals.get("trend_50d"),
                "trend_200d": signals.get("trend_200d"),
                "next_earnings_date": signals.get("next_earnings_date"),
                "priced_at_cost": h["priced_at_cost"],
                "allocation_pct": h["allocation_pct"],
                "low_limit": low_limit,
                "low_limit_set": low_limit is not None,
                "low_limit_reached": bool(low_limit is not None and native_price is not None and native_price <= low_limit),
                "high_limit": high_limit,
                "high_limit_set": high_limit is not None,
                "high_limit_reached": bool(high_limit is not None and native_price is not None and native_price >= high_limit),
            })
    return {"base_currency": BASE_CURRENCY, "holdings": rows}


def set_holding_price_limit(account_id: int, ticker: str, **fields) -> None:
    """Thin wrapper so api_routes_accounts.py never calls db_accounts directly, matching the
    existing convention (list-with-metrics/portfolio-totals routes always call the engine layer).
    Only forwards the kwarg(s) the caller actually passed, so setting one limit never clears the
    other — see db_accounts.upsert_holding_price_limit()'s partial-update semantics."""
    fields["updated_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    upsert_holding_price_limit(account_id, ticker, **fields)


def _trading_accounts() -> list:
    return [a for a in get_accounts() if a["account_type"] == "Trading"]


def portfolio_totals() -> dict:
    """Aggregates live figures across every non-deleted Trading account — the Home Assistant
    portfolio-summary sensor's data source. Zero Trading accounts (fresh install) returns an
    all-zero/None shape rather than raising or dividing by zero."""
    accounts = _trading_accounts()
    result = {
        "account_count": len(accounts),
        "base_currency": BASE_CURRENCY,
        "as_of": time.time(),
        "current_value": 0.0,
        "total_investment": 0.0,
        "portfolio_gain": 0.0,
        "portfolio_gain_pct": None,
        "portfolio_gain_fx": 0.0,
        "portfolio_gain_fx_pct": None,
        "unrealized_pnl": 0.0,
        "unrealized_pnl_pct": None,
        "twr_pct": None,
        "twr_fx_pct": None,
        "portfolio_dividends": 0.0,
    }
    if not accounts:
        return result

    account_ids = [a["id"] for a in accounts]
    current_value = sum(total_value(aid) or 0.0 for aid in account_ids)
    total_investment = 0.0
    unrealized = 0.0
    dividends = 0.0
    for aid in account_ids:
        for row in holdings_with_market_value(aid):
            total_investment += row["total_investment"]
            unrealized += row["market_value"] - row["total_investment"]
        dividends += account_summary(aid).get("dividend", 0.0)

    gain_ex_fx, gain_actual = portfolio_gain_fx_decomposition(account_ids)

    result["current_value"] = round(current_value, 2)
    result["total_investment"] = round(total_investment, 2)
    result["portfolio_gain"] = round(gain_ex_fx, 2)
    result["portfolio_gain_fx"] = round(gain_actual, 2)
    result["unrealized_pnl"] = round(unrealized, 2)
    result["portfolio_dividends"] = round(dividends, 2)
    if total_investment:
        result["portfolio_gain_pct"] = round(gain_ex_fx / total_investment * 100, 2)
        result["portfolio_gain_fx_pct"] = round(gain_actual / total_investment * 100, 2)
        result["unrealized_pnl_pct"] = round(unrealized / total_investment * 100, 2)
    result["twr_pct"] = portfolio_twr_ex_fx(account_ids)
    result["twr_fx_pct"] = portfolio_twr_fx(account_ids)
    return result


def account_metrics_list() -> dict:
    """Per-Trading-account metrics for the Home Assistant per-account sensor set — reads every
    field from the single `account_performance_cache` snapshot (lazily refreshed like the
    live-performance endpoint) so the whole response is consistent as of one point in time."""
    accounts = []
    for acc in _trading_accounts():
        account_id = acc["id"]
        cached = get_performance_cache(account_id)
        if cached is None:
            refresh_performance_cache(account_id)
            cached = get_performance_cache(account_id)
        accounts.append({
            "account_id": account_id,
            "name": acc["name"],
            "cash_balance": cached["cash_balance"],
            "equity_value": cached["equity_value"],
            "unrealized_pnl": cached["unrealized_pnl"],
            "realized_pnl": cached["realized_pnl"],
            "dividend_income": cached["dividend_income"],
            "interest_income": cached["interest_income"],
            "gain_1d": cached["return_1d"],
            "gain_1w": cached["return_1w"],
            "gain_1m": cached["return_1m"],
            "gain_3m": cached["return_3m"],
            "gain_1y": cached["return_1y"],
            "mwrr_pct": cached["mwrr"],
        })
    return {"base_currency": BASE_CURRENCY, "accounts": accounts}


def _other_accounts() -> list:
    return [a for a in get_accounts() if a["account_type"] in ("Pension", "House")]


def other_accounts_list() -> dict:
    """Per-Pension/House-account current value for the Home Assistant Phase 4 sensor set.
    `current_value` deliberately uses `account_summary()`'s `equity_value` rather than
    `total_value()` — for House, `total_value()` adds `cash_balance()`, which starts from
    `initial_cash` (the purchase price memo, not real cash), double-counting it against the
    scraped valuation. `GET /accounts` already sources its own House/Pension tile figure the
    same way (`account_summary(id).get("equity_value")`), so this stays consistent with it."""
    accounts = []
    for acc in _other_accounts():
        account_id = acc["id"]
        summary = account_summary(account_id)
        history = get_price_history(account_id)
        accounts.append({
            "account_id": account_id,
            "name": acc["name"],
            "account_type": acc["account_type"],
            "currency": acc["currency"],
            "current_value": summary.get("equity_value", 0.0),
            "performance": scraped_price_performance(account_id),
            "last_updated": history[-1]["price_date"] if history else None,
        })
    return {"base_currency": BASE_CURRENCY, "accounts": accounts}


def _avg_purchase_fx_rate(account_id: int, tickers: set) -> dict:
    """Purchase-quantity-weighted-average `exchange_rate` across still-open Buy legs, per ticker —
    isolates the FX rate actually paid at purchase, as opposed to today's live rate."""
    weighted: dict[str, float] = {}
    qty_total: dict[str, float] = {}
    for txn in get_transactions(account_id):
        ticker = txn["ticker"]
        if txn["txn_type"] != "Buy" or ticker not in tickers:
            continue
        qty = txn["quantity"] or 0.0
        weighted[ticker] = weighted.get(ticker, 0.0) + qty * _fx(txn)
        qty_total[ticker] = qty_total.get(ticker, 0.0) + qty
    return {t: (weighted[t] / qty_total[t]) for t in tickers if qty_total.get(t)}


def portfolio_gain_fx_decomposition(account_ids: list) -> tuple:
    """Decomposes OPEN-holdings unrealized gain, summed in BASE_CURRENCY across account_ids, into
    (gain_ex_fx, gain_actual). gain_actual re-derives unrealized_pnl()'s math (today's live FX
    rate); gain_ex_fx re-expresses today's market value using each holding's own purchase-
    quantity-weighted-average exchange rate instead, isolating the equity-only return. Unpriced
    holdings are excluded from both legs, matching holdings_with_market_value()'s own behaviour."""
    gain_ex_fx = 0.0
    gain_actual = 0.0
    for aid in account_ids:
        holdings = derive_account_holdings(aid)
        if not holdings:
            continue
        prices = current_price_map(list(holdings.keys()))
        avg_fx = _avg_purchase_fx_rate(aid, set(holdings.keys()))
        for ticker, h in holdings.items():
            total_investment = h["accounts"][0]["total_investment"]
            priced = prices.get(ticker)
            if not priced or not priced[0]:
                continue
            price, currency = priced
            currency = currency or h["currency"]
            shares = h["global_shares"]
            market_value = shares * price * get_rate_to_base(currency)
            gain_actual += market_value - total_investment
            purchase_fx = avg_fx.get(ticker, get_rate_to_base(currency))
            market_value_ex_fx = shares * price * purchase_fx
            gain_ex_fx += market_value_ex_fx - total_investment
    return gain_ex_fx, gain_actual


def _chain_link_twr(series: list) -> Optional[float]:
    """Shared chain-linking core for portfolio_twr_fx()/portfolio_twr_ex_fx(). `series` is a list of
    (date, total_value, net_contributions) tuples, sorted by date. Sub-periods whose starting value
    is ~0 are skipped rather than included as 0% or blown up by near-zero division. Fewer than 2
    points → None (not 0%)."""
    if len(series) < 2:
        return None
    growth = 1.0
    counted = 0
    for (_prev_date, v_start, nc_start), (_date, v_end, nc_end) in zip(series, series[1:]):
        if abs(v_start) < _EPS:
            continue
        flow = nc_end - nc_start
        sub_return = (v_end - v_start - flow) / v_start
        growth *= (1.0 + sub_return)
        counted += 1
    if not counted:
        return None
    return round((growth - 1.0) * 100, 2)


def _merge_value_history(account_ids: list) -> list:
    """Sums `total_value`/`net_contributions` across accounts per date — a date missing from one
    account's history simply contributes 0 for it (not an inner join), so an account opened later
    still contributes from its own start date without distorting earlier dates."""
    by_date: dict[str, dict] = {}
    for aid in account_ids:
        for row in get_value_history(aid):
            d = row["snapshot_date"]
            entry = by_date.setdefault(d, {"total_value": 0.0, "net_contributions": 0.0})
            entry["total_value"] += row["total_value"] or 0.0
            entry["net_contributions"] += row["net_contributions"] or 0.0
    return [(d, by_date[d]["total_value"], by_date[d]["net_contributions"]) for d in sorted(by_date)]


def portfolio_twr_fx(account_ids: list) -> Optional[float]:
    """True chain-linked Time-Weighted Return (%), 'actual/with-FX' variant, from the existing
    account_value_history table merged across account_ids."""
    series = _merge_value_history(account_ids)
    return _chain_link_twr(series)


def portfolio_twr_ex_fx(account_ids: list) -> Optional[float]:
    """FX-neutral TWR variant (%): synthesizes a total_value series where each currency's equity
    contribution is revalued at its own earliest-observed ('baseline') fx_rate instead of the
    fx_rate actually in effect on each date, isolating the equity-only return from FX movement.
    A date/holding with no currency-breakdown row (e.g. a ticker whose price history doesn't reach
    that far back) falls back to its actual (FX-inclusive) equity contribution for that slice rather
    than silently dropping to 0 — collapsing to 0 would fabricate a near-total-loss sub-period the
    first time coverage is incomplete, which previously drove the whole chain-linked result to -100%."""
    by_date_currency: dict[str, dict] = {}
    baseline_fx: dict[str, float] = {}
    baseline_date: dict[str, str] = {}
    covered_base_by_date: dict[str, float] = {}
    for aid in account_ids:
        for row in get_value_history_currency(aid):
            d, currency = row["snapshot_date"], row["currency"]
            entry = by_date_currency.setdefault(d, {})
            entry[currency] = entry.get(currency, 0.0) + (row["equity_value_native"] or 0.0)
            covered_base_by_date[d] = covered_base_by_date.get(d, 0.0) + (row["equity_value_base"] or 0.0)
            if currency not in baseline_date or d < baseline_date[currency]:
                baseline_date[currency] = d
                baseline_fx[currency] = row["fx_rate"]

    cash_and_contrib_by_date: dict[str, dict] = {}
    for aid in account_ids:
        for row in get_value_history(aid):
            d = row["snapshot_date"]
            entry = cash_and_contrib_by_date.setdefault(
                d, {"cash_value": 0.0, "net_contributions": 0.0, "equity_value": 0.0}
            )
            entry["cash_value"] += row["cash_value"] or 0.0
            entry["net_contributions"] += row["net_contributions"] or 0.0
            entry["equity_value"] += row["equity_value"] or 0.0

    series = []
    for d in sorted(cash_and_contrib_by_date):
        cash = cash_and_contrib_by_date[d]["cash_value"]
        nc = cash_and_contrib_by_date[d]["net_contributions"]
        equity_actual = cash_and_contrib_by_date[d]["equity_value"]
        covered_ex_fx = sum(
            native * baseline_fx.get(currency, 1.0)
            for currency, native in by_date_currency.get(d, {}).items()
        )
        uncovered_actual = equity_actual - covered_base_by_date.get(d, 0.0)
        equity_ex_fx = covered_ex_fx + uncovered_actual
        series.append((d, cash + equity_ex_fx, nc))
    return _chain_link_twr(series)
