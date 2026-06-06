# smgb_predictor.py
import logging
from datetime import datetime, timezone

import numpy as np
import pandas as pd
from scipy import stats

import time_engine
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

_DEFAULT_TICKERS = ["NVDA", "AMD", "MSFT", "META", "GOOGL", "AAPL", "AVGO", "SMH", "SOXX", "QQQ"]
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
    df_smgb = df[_SMGB].dropna()

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
    All prices in GBX (pence). Returns {"status": "error", ...} on total failure.
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
        "fx_rate_gbpusd": round(fx_rate, 4),
        "as_of_utc": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "n_holdings_used": holdings_result["n_holdings_used"] if holdings_result else 0,
        "holdings_engine": holdings_result,
        "regression_engine": regression_result,
        "error": None,
    }


def get_correlation_data(days: int = 60) -> dict:
    """
    Returns normalised-to-100 price DataFrame and rolling 30-day Pearson correlation
    between SMGB.L and the equal-weighted US basket, for chart rendering.
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
