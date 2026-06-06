# smgb_predictor.py
import logging
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
from scipy import stats

import time_engine
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

# Correct top-10 SMGB.L semiconductor ETF holdings (US-listed ADRs/shares)
_SEMIS_TICKERS = ["MU", "AMD", "AVGO", "ASML", "INTC", "TSM", "NVDA", "LRCX", "AMAT", "TXN"]
_DEFAULT_TICKERS = _SEMIS_TICKERS
_SMGB = "SMGB.L"
_FX_TICKER = "GBPUSD=X"


def fetch_daily_closes(tickers: list, days: int = 65) -> pd.DataFrame:
    """Download daily Close prices for all tickers. Returns a wide DataFrame (columns = tickers)."""
    ticker_dfs = yahoo_engine.get_price_history(tickers, period=f"{days}d", interval="1d")
    if not ticker_dfs:
        return pd.DataFrame()
    df = pd.DataFrame({t: df["Close"] for t, df in ticker_dfs.items() if "Close" in df.columns})
    return df.sort_index()


def fetch_fx_rate() -> float:
    """Return the most recent GBPUSD spot rate. Falls back to 1.0 on any failure."""
    rate = yahoo_engine.get_fx_rate(_FX_TICKER)
    if rate is not None:
        return rate
    logger.warning("fetch_fx_rate returned None, using 1.0")
    return 1.0


def fetch_smgb_holdings() -> list:
    """
    Attempt to fetch SMGB.L holdings from yfinance.
    Returns list of {"ticker": str, "weight": float} or [] on any failure.
    """
    holdings_df = yahoo_engine.get_fund_holdings(_SMGB)
    if holdings_df is None or holdings_df.empty:
        return []
    result = []
    for _, row in holdings_df.iterrows():
        symbol = str(row.get("Symbol", row.get("symbol", ""))).strip()
        weight = float(row.get("Holding Percent", row.get("holdingPercent", 0.0)))
        if symbol and weight > 0:
            result.append({"ticker": symbol, "weight": weight / 100.0 if weight > 1 else weight})
    total = sum(h["weight"] for h in result)
    if total > 0 and result:
        for h in result:
            h["weight"] /= total
    return result


def _equal_weight_holdings(tickers: list) -> list:
    w = 1.0 / len(tickers)
    return [{"ticker": t, "weight": w} for t in tickers]


def get_smgb_next_open_date() -> date:
    """Returns next SMGB.L trading day: Monday if today is Friday/Saturday, else tomorrow."""
    today = date.today()
    if today.weekday() == 4:   # Friday
        return today + timedelta(days=3)
    if today.weekday() == 5:   # Saturday
        return today + timedelta(days=2)
    return today + timedelta(days=1)


def fetch_intraday_data(period: str = "2d") -> dict[str, pd.DataFrame]:
    """
    Fetch 5-min bars (with pre/post-market) for SMGB.L + all 10 semiconductor tickers + FX.
    period="2d" covers yesterday's post-market and today's pre-market.
    Returned DataFrames have a naive UTC DatetimeIndex (timezone stripped by yahoo_engine).
    """
    all_tickers = [_SMGB] + _SEMIS_TICKERS + [_FX_TICKER]
    return yahoo_engine.get_intraday(all_tickers, period=period, interval="5m", prepost=True)


def _lse_close_utc_dt(ref_date: date | None = None) -> datetime:
    """Naive UTC datetime for LSE close on ref_date (today if None), honoring DST."""
    _, close_utc_time = time_engine.market_window_utc("LSE")
    return datetime.combine(ref_date or date.today(), close_utc_time)


def _lse_open_utc_dt(ref_date: date | None = None) -> datetime:
    """Naive UTC datetime for LSE open on ref_date, honoring DST."""
    open_utc_time, _ = time_engine.market_window_utc("LSE")
    return datetime.combine(ref_date or date.today(), open_utc_time)


def filter_post_uk_close(df: pd.DataFrame, ref_date: date | None = None) -> pd.DataFrame:
    """Filter intraday DataFrame (naive UTC index) to bars at or after LSE close."""
    if df.empty:
        return df
    return df[df.index >= _lse_close_utc_dt(ref_date)]


def filter_pre_uk_open(df: pd.DataFrame, ref_date: date | None = None) -> pd.DataFrame:
    """Filter intraday DataFrame to US pre-market bars before today's LSE open."""
    if df.empty:
        return df
    lse_open = _lse_open_utc_dt(ref_date)
    ref = ref_date or date.today()
    nyse_premarket_open, _ = time_engine.market_window_utc("NYSE", include_premarket=True)
    premarket_start = datetime.combine(ref, nyse_premarket_open)
    return df[(df.index >= premarket_start) & (df.index < lse_open)]


def _latest_prices_from_intraday(
    intraday: dict[str, pd.DataFrame],
) -> tuple[dict[str, float], str]:
    """
    Extract the most recent Close for each US ticker from post-UK-close data.
    Falls back to pre-market if no post-close bars exist.
    Returns (prices_dict, signal_source).
    """
    prices: dict[str, float] = {}
    found_post = False

    for ticker in _SEMIS_TICKERS:
        df = intraday.get(ticker)
        if df is None or df.empty or "Close" not in df.columns:
            continue
        post = filter_post_uk_close(df)
        if not post.empty:
            val = post["Close"].dropna()
            if not val.empty:
                prices[ticker] = float(val.iloc[-1])
                found_post = True
                continue
        pre = filter_pre_uk_open(df)
        if not pre.empty:
            val = pre["Close"].dropna()
            if not val.empty:
                prices[ticker] = float(val.iloc[-1])

    signal_source = "intraday_post_close" if found_post else (
        "intraday_premarket" if prices else "daily_close"
    )
    return prices, signal_source


def get_intraday_overlay_data() -> dict:
    """
    Assemble data for the time-aligned intraday overlay chart.
    Returns:
      smgb_intraday  — pd.Series of SMGB.L Close prices today (08:00–16:30 BST, naive UTC index)
      us_intraday    — dict[ticker, pd.Series] of US semi Close prices from NYSE open onward
      uk_close_utc   — naive UTC datetime of LSE close today
      smgb_last_close — float
      prediction     — dict from run_smgb_prediction()
      next_open_date — date
    """
    today = date.today()
    lse_open_utc = _lse_open_utc_dt(today)
    uk_close_utc = _lse_close_utc_dt(today)
    nyse_open_utc_time, _ = time_engine.market_window_utc("NYSE")
    nyse_open_utc = datetime.combine(today, nyse_open_utc_time)

    intraday = fetch_intraday_data(period="2d")

    smgb_series = pd.Series(dtype=float)
    smgb_df = intraday.get(_SMGB)
    if smgb_df is not None and not smgb_df.empty and "Close" in smgb_df.columns:
        mask = (smgb_df.index >= lse_open_utc) & (smgb_df.index <= uk_close_utc)
        smgb_series = smgb_df.loc[mask, "Close"].dropna()

    us_intraday: dict[str, pd.Series] = {}
    for ticker in _SEMIS_TICKERS:
        df = intraday.get(ticker)
        if df is None or df.empty or "Close" not in df.columns:
            continue
        bars = df[df.index >= nyse_open_utc]["Close"].dropna()
        if not bars.empty:
            us_intraday[ticker] = bars

    smgb_last_close = float(smgb_series.iloc[-1]) if not smgb_series.empty else 0.0

    return {
        "smgb_intraday": smgb_series,
        "us_intraday": us_intraday,
        "uk_close_utc": uk_close_utc,
        "smgb_last_close": smgb_last_close,
        "prediction": run_smgb_prediction(),
        "next_open_date": get_smgb_next_open_date(),
    }


def compute_holdings_prediction(
    df: pd.DataFrame,
    holdings: list,
    fx_rate: float,
    smgb_last_close_gbx: float,
) -> dict | None:
    """
    Weighted US constituent returns → predicted SMGB.L price in GBX.
    fx_rate is GBPUSD (how many USD per 1 GBP). A rising USD (falling GBP) boosts
    GBX-priced assets that hold USD equities, so we apply the FX ratio as an additive
    component on top of the weighted equity return.
    """
    df_clean = df.dropna(how="all")
    contributions = []
    weighted_equity_change = 0.0
    used = 0

    fx_prev = None
    if _FX_TICKER in df_clean.columns and len(df_clean[_FX_TICKER].dropna()) >= 2:
        fx_series = df_clean[_FX_TICKER].dropna()
        fx_prev = float(fx_series.iloc[-2])

    for h in holdings:
        ticker = h["ticker"]
        weight = h["weight"]
        if ticker not in df_clean.columns:
            continue
        series = df_clean[ticker].dropna()
        if len(series) < 2:
            continue
        us_return = float(series.iloc[-1]) / float(series.iloc[-2]) - 1.0
        contribution = weight * us_return
        contributions.append({
            "ticker": ticker,
            "weight": round(weight, 4),
            "us_return_pct": round(us_return * 100, 3),
            "contribution_pct": round(contribution * 100, 3),
        })
        weighted_equity_change += contribution
        used += 1

    if used < 3:
        return None

    fx_change = 0.0
    if fx_prev and fx_prev > 0:
        fx_change = (fx_rate / fx_prev) - 1.0

    fx_adjustment = -fx_change
    total_return = weighted_equity_change + fx_adjustment
    predicted_price = smgb_last_close_gbx * (1.0 + total_return)

    return {
        "predicted_price": round(predicted_price, 2),
        "predicted_change_pct": round(total_return * 100, 3),
        "contributions": sorted(contributions, key=lambda x: abs(x["contribution_pct"]), reverse=True),
        "fx_adjustment_pct": round(fx_adjustment * 100, 3),
        "n_holdings_used": used,
    }


def compute_regression_prediction(df: pd.DataFrame, smgb_last_close_gbx: float) -> dict | None:
    """
    60-day OLS: smgb_next_morning_return = α + β × avg_us_basket_return.
    Returns predicted price in GBX with 95% confidence interval.
    """
    us_tickers = [t for t in _DEFAULT_TICKERS if t in df.columns]
    if _SMGB not in df.columns or len(us_tickers) < 3:
        return None

    df_us = df[us_tickers].dropna(how="all")
    us_daily_ret = df_us.pct_change().mean(axis=1).dropna()

    _smgb_result = yahoo_engine.get_price_history([_SMGB], period="70d", interval="1d")
    smgb_raw = _smgb_result.get(_SMGB)
    if smgb_raw is None or smgb_raw.empty:
        logger.warning("compute_regression_prediction: SMGB history unavailable")
        return None

    smgb_opens = smgb_raw["Open"]
    smgb_closes_raw = smgb_raw["Close"]

    smgb_opens.index = smgb_opens.index.normalize()
    smgb_closes_raw.index = smgb_closes_raw.index.normalize()

    smgb_next_open_ret = (smgb_opens.shift(-1) / smgb_closes_raw - 1.0).dropna()

    common = us_daily_ret.index.normalize().intersection(smgb_next_open_ret.index.normalize())
    if len(common) < 20:
        return None

    X = us_daily_ret.reindex(common).values
    y = smgb_next_open_ret.reindex(common).values

    mask = ~(np.isnan(X) | np.isnan(y))
    X, y = X[mask], y[mask]
    if len(X) < 20:
        return None

    slope, intercept, r_value, _, _ = stats.linregress(X, y)
    residuals = y - (intercept + slope * X)
    residual_std = float(np.std(residuals))

    current_us_ret = float(us_daily_ret.iloc[-1]) if not us_daily_ret.empty else 0.0
    predicted_ret = intercept + slope * current_us_ret
    predicted_price = smgb_last_close_gbx * (1.0 + predicted_ret)
    interval = 1.96 * residual_std

    return {
        "predicted_price": round(predicted_price, 2),
        "predicted_change_pct": round(predicted_ret * 100, 3),
        "lower_bound": round(smgb_last_close_gbx * (1.0 + predicted_ret - interval), 2),
        "upper_bound": round(smgb_last_close_gbx * (1.0 + predicted_ret + interval), 2),
        "alpha": round(float(intercept), 6),
        "beta": round(float(slope), 4),
        "r_squared": round(float(r_value**2), 4),
        "n_observations": int(len(X)),
        "residual_std_pct": round(residual_std * 100, 3),
    }


def run_smgb_prediction() -> dict:
    """
    Orchestrates holdings + regression engines and returns a unified prediction dict.
    Signal priority: post-UK-close intraday → US pre-market → prior daily closes.
    All prices in GBP (£). Returns {"status": "error", ...} on total failure.
    """
    all_tickers = _DEFAULT_TICKERS + [_SMGB, _FX_TICKER]
    df = fetch_daily_closes(all_tickers)

    if _SMGB not in df.columns or df[_SMGB].dropna().empty:
        _fallback = yahoo_engine.get_price_history([_SMGB], period="65d", interval="1d")
        fallback_df = _fallback.get(_SMGB)
        if fallback_df is not None and not fallback_df.empty:
            close = fallback_df["Close"].copy()
            if close.index.tz is not None:
                close.index = close.index.tz_localize(None)
            close.index = close.index.normalize()
            df.index = df.index.normalize()
            df[_SMGB] = close.reindex(df.index)
        else:
            logger.warning("SMGB direct fallback also failed.")

    if _SMGB not in df.columns or df[_SMGB].dropna().empty:
        return {"status": "error", "error": "SMGB.L price data unavailable", "predicted_price": None}

    smgb_series = df[_SMGB].dropna()
    smgb_last_close = float(smgb_series.iloc[-1])
    fx_rate = fetch_fx_rate()
    signal_source = "daily_close"

    # Augment daily closes with live intraday prices where available
    try:
        intraday = fetch_intraday_data(period="2d")
        live_prices, signal_source = _latest_prices_from_intraday(intraday)
        if live_prices and not df.empty:
            for ticker, live_price in live_prices.items():
                if ticker in df.columns:
                    df.loc[df.index[-1], ticker] = live_price
    except Exception as exc:
        logger.warning("Intraday signal fetch failed, using daily closes: %s", exc)

    holdings = fetch_smgb_holdings()
    data_source = "holdings"
    if not holdings:
        holdings = _equal_weight_holdings(_DEFAULT_TICKERS)
        data_source = "regression_fallback"

    holdings_result = compute_holdings_prediction(df, holdings, fx_rate, smgb_last_close)
    if holdings_result is None:
        data_source = "regression_only"

    regression_result = compute_regression_prediction(df, smgb_last_close)

    if holdings_result is not None:
        primary_price = holdings_result["predicted_price"]
        primary_change = holdings_result["predicted_change_pct"]
    elif regression_result is not None:
        primary_price = regression_result["predicted_price"]
        primary_change = regression_result["predicted_change_pct"]
    else:
        return {"status": "error", "error": "Both prediction engines failed", "predicted_price": None}

    return {
        "status": "success",
        "predicted_price": primary_price,
        "last_smgb_close": round(smgb_last_close, 2),
        "predicted_change_pct": primary_change,
        "data_source": data_source,
        "signal_source": signal_source,
        "fx_rate_gbpusd": round(fx_rate, 4),
        "as_of_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "next_open_date": get_smgb_next_open_date().isoformat(),
        "n_holdings_used": holdings_result["n_holdings_used"] if holdings_result else 0,
        "holdings_engine": holdings_result,
        "regression_engine": regression_result,
        "error": None,
    }


_AI_TICKERS = ["NVDA", "AMD", "AVGO", "GOOGL", "MSFT", "META", "AAPL", "ORCL", "AMZN", "TSLA"]


def get_ai_contagion_data(days: int = 30) -> dict:
    """
    Fetch daily OHLCV for the AI ecosystem basket used by the Contagion Monitor.
    Returns {"daily_dfs": dict[str, DataFrame], "intraday_dfs": dict[str, DataFrame], "error": str|None}.
    """
    try:
        daily_dfs = yahoo_engine.get_price_history(_AI_TICKERS, period=f"{days + 5}d", interval="1d")
        for ticker, df in daily_dfs.items():
            daily_dfs[ticker] = df.tail(days)
    except Exception as exc:
        logger.error("get_ai_contagion_data daily fetch failed: %s", exc)
        return {"daily_dfs": {}, "intraday_dfs": {}, "error": str(exc)}

    intraday_dfs: dict = {}
    try:
        intraday_dfs = yahoo_engine.get_intraday(_AI_TICKERS, period="1d", interval="5m", prepost=False)
    except Exception as exc:
        logger.warning("get_ai_contagion_data intraday fetch failed: %s", exc)

    return {"daily_dfs": daily_dfs, "intraday_dfs": intraday_dfs, "error": None}


def get_correlation_data(days: int = 60) -> dict:
    """
    Returns normalised-to-100 price DataFrame and rolling 30-day Pearson correlation
    between SMGB.L and the equal-weighted US semiconductor basket, for chart rendering.
    """
    all_tickers = _DEFAULT_TICKERS + [_SMGB, _FX_TICKER]
    df = fetch_daily_closes(all_tickers, days=days + 5)

    if df.empty:
        return {"normalized_df": pd.DataFrame(), "rolling_corr": pd.Series(dtype=float), "error": "No data"}

    df = df.dropna(how="all").tail(days)

    first_valid = df.apply(lambda col: col.first_valid_index())
    start = max(v for v in first_valid if v is not None)
    df = df.loc[start:]

    normalized = df.div(df.iloc[0]) * 100

    us_cols = [t for t in _DEFAULT_TICKERS if t in normalized.columns]
    if us_cols and _SMGB in normalized.columns:
        us_basket = normalized[us_cols].mean(axis=1)
        smgb_ret = normalized[_SMGB].pct_change()
        basket_ret = us_basket.pct_change()
        rolling_corr = smgb_ret.rolling(window=30, min_periods=15).corr(basket_ret)
    else:
        rolling_corr = pd.Series(dtype=float)

    return {"normalized_df": normalized, "raw_df": df, "rolling_corr": rolling_corr, "error": None}
