import logging
from datetime import date, datetime, timedelta, timezone

import numpy as np
import pandas as pd
from scipy import stats

import time_engine
from database import log_smgb_prediction
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

# Top-10 SMGB.L semiconductor ETF holdings ordered by weight (US-listed ADRs/shares)
_SEMIS_TICKERS = ["MU", "AMD", "INTC", "AVGO", "NVDA", "TSM", "ASML", "LRCX", "AMAT", "TXN"]
_DEFAULT_TICKERS = _SEMIS_TICKERS
_SMGB = "SMGB.L"
_FX_TICKER = "GBPUSD=X"

# Known ETF weights (source: VanEck SMGB.L factsheet, top-10 = 79.52% of fund).
# Normalised to sum to 1.0 so the tracked basket covers 100% of our prediction.
_KNOWN_WEIGHTS_RAW = {
    "MU":   11.67,
    "AMD":  11.10,
    "INTC":  8.77,
    "AVGO":  8.67,
    "NVDA":  8.55,
    "TSM":   7.98,
    "ASML":  7.58,
    "LRCX":  5.48,
    "AMAT":  5.25,
    "TXN":   4.47,
}
_TOTAL_RAW = sum(_KNOWN_WEIGHTS_RAW.values())
_KNOWN_HOLDINGS = [
    {"ticker": t, "weight": round(w / _TOTAL_RAW, 6)}
    for t, w in _KNOWN_WEIGHTS_RAW.items()
]


def fetch_daily_closes(tickers: list, days: int = 65) -> pd.DataFrame:
    ticker_dfs = yahoo_engine.get_price_history(tickers, period=f"{days}d", interval="1d")
    if not ticker_dfs:
        return pd.DataFrame()
    df = pd.DataFrame({t: df["Close"] for t, df in ticker_dfs.items() if "Close" in df.columns})
    return df.sort_index()


def fetch_fx_rate() -> float:
    """Falls back to 1.0 on any failure."""
    rate = yahoo_engine.get_fx_rate(_FX_TICKER)
    if rate is not None:
        return rate
    logger.warning("fetch_fx_rate returned None, using 1.0")
    return 1.0


def fetch_smgb_holdings() -> list:
    """Returns list of {"ticker": str, "weight": float} or [] on any failure."""
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


def _fallback_holdings() -> list:
    """Known ETF composition used when live holdings fetch fails."""
    return _KNOWN_HOLDINGS


def get_smgb_next_open_date() -> date:
    """Returns the prediction target date: today until LSE closes, next trading day after."""
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    today = datetime.now(timezone.utc).date()
    if today.weekday() == 5:   # Saturday → Monday
        return today + timedelta(days=2)
    if today.weekday() == 6:   # Sunday → Monday
        return today + timedelta(days=1)
    _, lse_close_utc = time_engine.market_window_utc("LSE")
    if now_utc < datetime.combine(today, lse_close_utc):
        return today
    if today.weekday() == 4:   # Friday → Monday
        return today + timedelta(days=3)
    return today + timedelta(days=1)


def _last_trading_date() -> date:
    """Return the most recent weekday whose LSE session has started or completed."""
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    today = datetime.now(timezone.utc).date()
    if today.weekday() == 5:   # Saturday → Friday
        return today - timedelta(days=1)
    if today.weekday() == 6:   # Sunday → Friday
        return today - timedelta(days=2)
    lse_open_utc, _ = time_engine.market_window_utc("LSE")
    if now_utc < datetime.combine(today, lse_open_utc):
        # LSE hasn't opened yet today — use previous trading day
        d = today - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d
    return today


def fetch_intraday_data(period: str = "2d") -> dict[str, pd.DataFrame]:
    """Returned DataFrames have a naive UTC DatetimeIndex (timezone stripped by yahoo_engine)."""
    all_tickers = [_SMGB] + _SEMIS_TICKERS + [_FX_TICKER]
    return yahoo_engine.get_intraday(all_tickers, period=period, interval="5m", prepost=True)


def _lse_close_utc_dt(ref_date: date | None = None) -> datetime:
    """Naive UTC datetime for LSE close on ref_date (today if None), honoring DST."""
    _, close_utc_time = time_engine.market_window_utc("LSE")
    return datetime.combine(ref_date or datetime.now(timezone.utc).date(), close_utc_time)


def _lse_open_utc_dt(ref_date: date | None = None) -> datetime:
    """Naive UTC datetime for LSE open on ref_date, honoring DST."""
    open_utc_time, _ = time_engine.market_window_utc("LSE")
    return datetime.combine(ref_date or datetime.now(timezone.utc).date(), open_utc_time)


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
    ref = ref_date or datetime.now(timezone.utc).date()
    nyse_premarket_open, _ = time_engine.market_window_utc("NYSE", include_premarket=True)
    premarket_start = datetime.combine(ref, nyse_premarket_open)
    return df[(df.index >= premarket_start) & (df.index < lse_open)]


def _compute_intraday_returns(
    intraday: dict[str, pd.DataFrame],
    ref_date: date | None = None,
    daily_df: "pd.DataFrame | None" = None,
) -> tuple[dict[str, float], str]:
    """Priority: post-LSE-close (vs price AT close) → pre-market → daily close. Returns ({ticker: return_fraction}, signal_source)."""
    trading_date = ref_date or _last_trading_date()
    uk_close = _lse_close_utc_dt(trading_date)
    returns: dict[str, float] = {}
    found_post = False
    found_intraday = False

    for ticker in _SEMIS_TICKERS:
        df = intraday.get(ticker)
        if df is None or df.empty or "Close" not in df.columns:
            continue
        closes = df["Close"].dropna()

        # Post-LSE-close signal: current vs price at LSE close
        at_close = closes[closes.index <= uk_close]
        if not at_close.empty:
            ref_price = float(at_close.iloc[-1])
            if ref_price > 0:
                post_close = closes[closes.index > uk_close]
                if not post_close.empty:
                    returns[ticker] = float(post_close.iloc[-1]) / ref_price - 1.0
                    found_post = True
                    continue

        # Intraday signal (LSE open, US pre-market or live): return vs yesterday's daily close
        if daily_df is not None and ticker in daily_df.columns:
            daily_series = daily_df[ticker].dropna()
            if len(daily_series) >= 2:
                yesterday_close = float(daily_series.iloc[-1])
                if yesterday_close > 0:
                    today_bars = closes[closes.index.normalize() == pd.Timestamp(trading_date)]
                    if not today_bars.empty:
                        returns[ticker] = float(today_bars.iloc[-1]) / yesterday_close - 1.0
                        found_intraday = True

    if found_post:
        signal_source = "intraday_post_close"
    elif found_intraday:
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        nyse_open_utc, _ = time_engine.market_window_utc("NYSE")
        signal_source = (
            "intraday_live"
            if now_utc >= datetime.combine(trading_date, nyse_open_utc)
            else "intraday_premarket"
        )
    else:
        signal_source = "daily_close"

    return returns, signal_source


def get_intraday_overlay_data() -> dict:
    """Assembles intraday chart data for a rolling 30h window centred ~2/3 left of 'now'."""
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    trading_date = _last_trading_date()

    # Rolling 25h look-back — larger than the 20h x-axis start so edges are never empty
    window_start = now_utc - timedelta(hours=25)

    # period="5d" ensures we always have data even on weekends/holidays
    intraday = fetch_intraday_data(period="5d")

    smgb_series = pd.Series(dtype=float)
    smgb_df = intraday.get(_SMGB)
    if smgb_df is not None and not smgb_df.empty and "Close" in smgb_df.columns:
        smgb_series = smgb_df.loc[smgb_df.index >= window_start, "Close"].dropna()

    us_intraday: dict[str, pd.Series] = {}
    for ticker in _SEMIS_TICKERS:
        df = intraday.get(ticker)
        if df is None or df.empty or "Close" not in df.columns:
            continue
        bars = df[df.index >= window_start]["Close"].dropna()
        if not bars.empty:
            us_intraday[ticker] = bars

    daily_df = fetch_daily_closes(_SEMIS_TICKERS + [_SMGB], days=10)
    smgb_last_close = (
        float(daily_df[_SMGB].dropna().iloc[-1])
        if _SMGB in daily_df.columns and not daily_df[_SMGB].dropna().empty
        else 0.0
    )
    us_prev_closes = {
        t: float(daily_df[t].dropna().iloc[-1])
        for t in _SEMIS_TICKERS
        if t in daily_df.columns and not daily_df[t].dropna().empty
    }

    return {
        "smgb_intraday": smgb_series,
        "us_intraday": us_intraday,
        "now_utc": now_utc,
        "trading_date": trading_date,
        "smgb_last_close": smgb_last_close,
        "us_prev_closes": us_prev_closes,
        "prediction": run_smgb_prediction(),
        "next_open_date": get_smgb_next_open_date(),
    }


def compute_holdings_prediction(
    df: pd.DataFrame,
    holdings: list,
    fx_rate: float,
    smgb_last_close_gbx: float,
    intraday_returns: dict[str, float] | None = None,
) -> dict | None:
    """Weighted US returns → predicted SMGB.L price (GBX). fx_rate is GBPUSD; rising USD adjusts price up."""
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

        if intraday_returns and ticker in intraday_returns:
            us_return = intraday_returns[ticker]
        else:
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
    """60-day OLS: smgb_next_morning_return = α + β × avg_us_basket_return. Returns price in GBX with 95% CI."""
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
    """Orchestrates holdings + regression engines; signal priority: post-UK-close → pre-market → daily closes."""
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
    intraday_returns: dict[str, float] | None = None

    # Compute post-UK-close intraday returns (vs price AT UK close, not prior daily close)
    try:
        intraday = fetch_intraday_data(period="5d")
        intraday_returns, signal_source = _compute_intraday_returns(
            intraday, ref_date=_last_trading_date(), daily_df=df
        )
        if not intraday_returns:
            intraday_returns = None
    except Exception as exc:
        logger.warning("Intraday signal fetch failed, using daily closes: %s", exc)

    holdings = fetch_smgb_holdings()
    data_source = "holdings"
    if not holdings:
        holdings = _fallback_holdings()
        data_source = "known_weights_fallback"

    holdings_result = compute_holdings_prediction(df, holdings, fx_rate, smgb_last_close, intraday_returns)
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

    result = {
        "status": "success",
        "predicted_price": primary_price,
        "last_smgb_close": round(smgb_last_close, 2),
        "predicted_change_pct": primary_change,
        "data_source": data_source,
        "signal_source": signal_source,
        "fx_rate_gbpusd": round(fx_rate, 4),
        "as_of_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "as_of_local": time_engine.fmt_datetime(datetime.now(timezone.utc)),
        "next_open_date": get_smgb_next_open_date().isoformat(),
        "n_holdings_used": holdings_result["n_holdings_used"] if holdings_result else 0,
        "holdings_engine": holdings_result,
        "regression_engine": regression_result,
        "error": None,
    }
    log_smgb_prediction(result)
    return result


_AI_TICKERS = ["NVDA", "AMD", "AVGO", "GOOGL", "MSFT", "META", "AAPL", "ORCL", "AMZN", "TSLA"]


def get_ai_contagion_data(days: int = 30) -> dict:
    """Returns {"daily_dfs": ..., "intraday_dfs": ..., "error": str|None} for the AI ecosystem basket."""
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
    """Rolling 30-day Pearson correlation vs equal-weighted basket (equal-weight is chart-only; prediction uses ETF weights)."""
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
