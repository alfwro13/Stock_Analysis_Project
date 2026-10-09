# GUI name: "FX Drag Analyzer". No scheduled job — on-demand only.
import logging
from datetime import datetime, timezone, timedelta

import pandas as pd

from config import HISTORICAL_DIR, BASE_CURRENCY
from database import get_connection
from db_accounts import get_accounts, get_transactions
from fx_conversion_helpers import cached_fx_close, fx_level_on
from portfolio_service import fx_pair
from utils import safe_ticker_filename

logger = logging.getLogger(__name__)


def _load_usdgbp_series() -> pd.Series:
    close = cached_fx_close(fx_pair("USD"), cache_only=True)
    return close.sort_index() if close is not None else pd.Series(dtype=float)


def _ytd_days() -> int:
    today = datetime.now(timezone.utc).date()
    return (today - today.replace(month=1, day=1)).days or 1


def compute_fx_breakdown(ticker: str, period_days: int) -> dict | None:
    safe_ticker = safe_ticker_filename(ticker)
    if not safe_ticker:
        return None
    parquet_path = HISTORICAL_DIR / f"{safe_ticker}.parquet"
    if not parquet_path.exists():
        return None

    usdgbp = _load_usdgbp_series()
    if usdgbp.empty:
        return None

    try:
        df = pd.read_parquet(parquet_path)
        prices = df["Close"].sort_index()
    except Exception as e:
        logger.error("Failed to read parquet for %s: %s", ticker, e)
        return None

    cutoff = datetime.now(timezone.utc).date() - timedelta(days=period_days)
    cutoff_ts = pd.Timestamp(cutoff)

    prices_in_range = prices[prices.index >= cutoff_ts]
    if prices_in_range.empty:
        return None

    price_ref = float(prices_in_range.iloc[0])
    price_now = float(prices_in_range.iloc[-1])
    usdgbp_ref = fx_level_on(prices_in_range.index[0], usdgbp)
    usdgbp_now = fx_level_on(prices_in_range.index[-1], usdgbp)

    if price_ref == 0 or usdgbp_ref is None or usdgbp_now is None:
        return None

    gbpusd_ref = 1 / usdgbp_ref
    gbpusd_now = 1 / usdgbp_now
    equity_pct = (price_now / price_ref - 1) * 100
    # Positive = USD strengthened vs GBP (tailwind for UK investor)
    fx_pct = (gbpusd_ref / gbpusd_now - 1) * 100
    total_gbp_pct = ((1 + equity_pct / 100) * (1 + fx_pct / 100) - 1) * 100

    return {
        "equity_pct": round(equity_pct, 2),
        "fx_pct": round(fx_pct, 2),
        "total_gbp_pct": round(total_gbp_pct, 2),
        "ref_date": str(prices_in_range.index[0].date()),
        "gbpusd_ref": round(gbpusd_ref, 4),
        "gbpusd_now": round(gbpusd_now, 4),
    }


def _get_usd_tickers_from_db(tickers: list[str]) -> set[str]:
    if not tickers:
        return set()
    conn = None
    try:
        conn = get_connection()
        placeholders = ",".join("?" * len(tickers))
        rows = conn.execute(
            f"SELECT ticker FROM stock_signals WHERE ticker IN ({placeholders}) AND currency = 'USD'",
            tickers,
        ).fetchall()
        return {r[0] for r in rows}
    except Exception as e:
        logger.error("Failed to fetch USD tickers from DB: %s", e)
        return set()
    finally:
        if conn:
            conn.close()


def portfolio_fx_breakdown(period_days: int) -> list[dict]:
    if BASE_CURRENCY != "GBP":
        return []

    try:
        from accounts_engine import get_combined_holdings
        portfolio = get_combined_holdings()
    except Exception as e:
        logger.error("Failed to load portfolio holdings: %s", e)
        return []

    all_tickers = [v["ticker"] for v in portfolio.values() if v.get("ticker")]
    usd_tickers = _get_usd_tickers_from_db(all_tickers)

    results = []
    for entry in portfolio.values():
        ticker = entry.get("ticker")
        if not ticker or ticker not in usd_tickers:
            continue

        breakdown = compute_fx_breakdown(ticker, period_days)
        if breakdown is None:
            continue

        shares = entry.get("global_shares", 0)
        buy_price_usd = entry.get("global_buy_price", 0)
        gbp_exposure = None
        if shares and buy_price_usd:
            try:
                safe_ticker = safe_ticker_filename(ticker)
                if not safe_ticker:
                    raise ValueError(f"Unsafe ticker: {ticker!r}")
                parquet_path = HISTORICAL_DIR / f"{safe_ticker}.parquet"
                df = pd.read_parquet(parquet_path)
                current_price_usd = float(df["Close"].iloc[-1])
                gbp_exposure = round((shares * current_price_usd) / breakdown["gbpusd_now"], 2)
            except Exception:
                gbp_exposure = None

        results.append({
            "ticker": ticker,
            "period_days": period_days,
            "gbp_exposure": gbp_exposure,
            **breakdown,
        })

    results.sort(key=lambda x: abs(x.get("fx_pct", 0)), reverse=True)
    return results


def _lifetime_buy_stats(ticker: str) -> tuple[float, float, int, str] | None:
    """Returns (vwap_buy_usd, weighted_avg_gbpusd_buy, buy_count, earliest_buy) from every Buy
    transaction for `ticker` across all built-in accounts. `exchange_rate` on each row already
    converts the trade's USD cost to BASE_CURRENCY (GBP), so the implied GBPUSD rate at buy time
    is recovered as total_usd / total_gbp — consistent with GBPUSD's USD-per-GBP quoting."""
    total_usd = 0.0
    total_gbp = 0.0
    total_qty = 0.0
    buy_count = 0
    earliest_buy: str | None = None

    for acc in get_accounts():
        for txn in get_transactions(acc["id"]):
            if txn["txn_type"] != "Buy" or txn["ticker"] != ticker or txn["currency"] != "USD":
                continue
            qty = float(txn["quantity"] or 0)
            usd_price = float(txn["unit_price"] or 0)
            if qty <= 0 or usd_price <= 0:
                continue
            fx = txn["exchange_rate"] if txn["exchange_rate"] is not None else 1.0

            total_usd += qty * usd_price
            total_gbp += qty * usd_price * fx
            total_qty += qty
            buy_count += 1
            date_str = (txn["txn_date"] or "")[:10]
            if date_str and (earliest_buy is None or date_str < earliest_buy):
                earliest_buy = date_str

    if buy_count == 0 or total_gbp == 0 or total_qty == 0:
        return None

    vwap_buy_usd = total_usd / total_qty
    weighted_avg_gbpusd_buy = total_usd / total_gbp
    return vwap_buy_usd, weighted_avg_gbpusd_buy, buy_count, earliest_buy


def portfolio_lifetime_fx_breakdown() -> list[dict]:
    if BASE_CURRENCY != "GBP":
        return []

    try:
        from accounts_engine import get_combined_holdings
        portfolio = get_combined_holdings()
    except Exception as e:
        logger.error("Failed to load portfolio holdings: %s", e)
        return []

    all_tickers = [v["ticker"] for v in portfolio.values() if v.get("ticker")]
    usd_tickers = _get_usd_tickers_from_db(all_tickers)

    usdgbp = _load_usdgbp_series()
    if usdgbp.empty:
        return []

    results = []
    for entry in portfolio.values():
        ticker = entry.get("ticker")
        if not ticker or ticker not in usd_tickers:
            continue

        stats = _lifetime_buy_stats(ticker)
        if stats is None:
            continue
        vwap_buy_usd, weighted_avg_gbpusd_buy, buy_count, earliest_buy = stats

        safe_ticker = safe_ticker_filename(ticker)
        if not safe_ticker:
            continue
        parquet_path = HISTORICAL_DIR / f"{safe_ticker}.parquet"
        try:
            df = pd.read_parquet(parquet_path)
            current_price_usd = float(df["Close"].iloc[-1])
            usdgbp_now = fx_level_on(df.index[-1], usdgbp)
        except Exception:
            continue

        if vwap_buy_usd == 0 or usdgbp_now is None:
            continue
        gbpusd_now = 1 / usdgbp_now

        equity_pct = (current_price_usd / vwap_buy_usd - 1) * 100
        fx_pct = (weighted_avg_gbpusd_buy / gbpusd_now - 1) * 100
        total_gbp_pct = ((1 + equity_pct / 100) * (1 + fx_pct / 100) - 1) * 100

        shares = entry.get("global_shares", 0)
        gbp_exposure = round((shares * current_price_usd) / gbpusd_now, 2) if shares else None

        results.append({
            "ticker": ticker,
            "period_days": None,
            "equity_pct": round(equity_pct, 2),
            "fx_pct": round(fx_pct, 2),
            "total_gbp_pct": round(total_gbp_pct, 2),
            "gbpusd_buy": round(weighted_avg_gbpusd_buy, 4),
            "gbpusd_now": round(gbpusd_now, 4),
            "earliest_buy": earliest_buy,
            "buy_count": buy_count,
            "gbp_exposure": gbp_exposure,
            "ref_date": earliest_buy,
        })

    results.sort(key=lambda x: abs(x.get("fx_pct", 0)), reverse=True)
    return results
