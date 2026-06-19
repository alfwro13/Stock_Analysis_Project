# GUI name: "FX Drag Analyzer". No scheduled job — on-demand only.
import json
import logging
from datetime import datetime, timezone, timedelta
from pathlib import Path

import pandas as pd

from config import HISTORICAL_DIR, PORTFOLIO_PATH, BASE_CURRENCY
from database import get_connection
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

_GBPUSD_PARQUET = HISTORICAL_DIR / "GBPUSD_BASELINE.parquet"


def _load_gbpusd_series() -> pd.Series:
    try:
        df = pd.read_parquet(_GBPUSD_PARQUET)
        return df["Close"].sort_index()
    except Exception:
        pass
    try:
        raw = yahoo_engine.get_price_history(["GBPUSD=X"], period="2y")
        df = raw.get("GBPUSD=X")
        if df is not None and not df.empty:
            return df["Close"].sort_index()
    except Exception as e:
        logger.error("Failed to fetch GBPUSD=X fallback: %s", e)
    return pd.Series(dtype=float)


def _ytd_days() -> int:
    today = datetime.now(timezone.utc).date()
    return (today - today.replace(month=1, day=1)).days or 1


def compute_fx_breakdown(ticker: str, period_days: int) -> dict | None:
    parquet_path = HISTORICAL_DIR / f"{ticker}.parquet"
    if not parquet_path.exists():
        return None

    gbpusd = _load_gbpusd_series()
    if gbpusd.empty:
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
    gbpusd_in_range = gbpusd[gbpusd.index >= cutoff_ts]

    if prices_in_range.empty or gbpusd_in_range.empty:
        return None

    price_ref = float(prices_in_range.iloc[0])
    price_now = float(prices_in_range.iloc[-1])
    gbpusd_ref = float(gbpusd_in_range.iloc[0])
    gbpusd_now = float(gbpusd_in_range.iloc[-1])

    if price_ref == 0 or gbpusd_now == 0:
        return None

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
        with open(PORTFOLIO_PATH) as f:
            portfolio = json.load(f)
    except Exception as e:
        logger.error("Failed to read portfolio.json: %s", e)
        return []

    all_tickers = [v["ticker"] for v in portfolio.values() if v.get("ticker")]
    usd_tickers = _get_usd_tickers_from_db(all_tickers)

    gbpusd_now_rate = _load_gbpusd_series()
    gbpusd_now = float(gbpusd_now_rate.iloc[-1]) if not gbpusd_now_rate.empty else None

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
        if gbpusd_now and gbpusd_now > 0 and shares and buy_price_usd:
            try:
                parquet_path = HISTORICAL_DIR / f"{ticker}.parquet"
                df = pd.read_parquet(parquet_path)
                current_price_usd = float(df["Close"].iloc[-1])
                gbp_exposure = round((shares * current_price_usd) / gbpusd_now, 2)
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
