import json
import logging
from datetime import date, datetime, time as dtime, timedelta, timezone

import numpy as np
import pandas as pd
from scipy import stats

import time_engine
from database import (
    fill_etf_actual,
    get_etf_predictor_config,
    get_etf_predictor_configs,
    log_etf_prediction,
    log_notification,
)
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

def _exchange_currency(exchange: str) -> str:
    info = time_engine.EXCHANGE_HOURS.get(exchange, {})
    return info.get("currency", "USD")


def detect_etf_info(etf_ticker: str) -> dict:
    """Returns {"exchange": str, "currency": str, "name": str}. Never raises."""
    exchange = time_engine.ticker_exchange(etf_ticker)
    currency = _exchange_currency(exchange)
    name = etf_ticker
    try:
        info = yahoo_engine.get_ticker_info(etf_ticker)
        if info:
            raw_ccy = info.get("currency") or info.get("financialCurrency") or ""
            if raw_ccy:
                currency = "GBP" if raw_ccy in ("GBp", "GBX") else raw_ccy
            long_name = info.get("longName") or info.get("shortName") or ""
            if long_name:
                name = long_name
    except Exception as exc:
        logger.warning("detect_etf_info: ticker_info failed for %s: %s", etf_ticker, exc)
    return {"exchange": exchange, "currency": currency, "name": name}


def detect_fx_pair(etf_currency: str, constituent_currencies: list) -> str | None:
    """Returns Yahoo FX pair or None; pair as {etf_ccy}{constituent_ccy}=X so fx_adjustment sign is positive when ETF currency is stronger."""
    normalised_etf = "GBP" if etf_currency in ("GBp", "GBX") else etf_currency
    normalised_constituents = [
        "GBP" if c in ("GBp", "GBX") else c for c in constituent_currencies
    ]
    if not normalised_constituents:
        return None
    counts: dict[str, int] = {}
    for c in normalised_constituents:
        counts[c] = counts.get(c, 0) + 1
    most_common = max(counts, key=counts.__getitem__)
    if normalised_etf == most_common:
        return None
    return f"{normalised_etf}{most_common}=X"


def get_next_open_date(etf_exchange: str) -> date:
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    today = datetime.now(timezone.utc).date()
    if today.weekday() == 5:
        return today + timedelta(days=2)
    if today.weekday() == 6:
        return today + timedelta(days=1)
    _, etf_close_utc = time_engine.market_window_utc(etf_exchange)
    if now_utc < datetime.combine(today, etf_close_utc):
        return today
    if today.weekday() == 4:
        return today + timedelta(days=3)
    return today + timedelta(days=1)


def _last_trading_date_for_exchange(exchange: str) -> date:
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    today = datetime.now(timezone.utc).date()
    if today.weekday() == 5:
        return today - timedelta(days=1)
    if today.weekday() == 6:
        return today - timedelta(days=2)
    open_utc, _ = time_engine.market_window_utc(exchange)
    if now_utc < datetime.combine(today, open_utc):
        d = today - timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        return d
    return today


def _exchange_close_utc_dt(exchange: str, ref_date: date | None = None) -> datetime:
    _, close_utc_time = time_engine.market_window_utc(exchange)
    return datetime.combine(ref_date or datetime.now(timezone.utc).date(), close_utc_time)


def _exchange_open_utc_dt(exchange: str, ref_date: date | None = None) -> datetime:
    open_utc_time, _ = time_engine.market_window_utc(exchange)
    return datetime.combine(ref_date or datetime.now(timezone.utc).date(), open_utc_time)


def _filter_post_etf_close(df: pd.DataFrame, etf_exchange: str, ref_date: date | None = None) -> pd.DataFrame:
    if df.empty:
        return df
    return df[df.index >= _exchange_close_utc_dt(etf_exchange, ref_date)]


def _filter_pre_constituent_open(
    df: pd.DataFrame,
    constituent_exchange: str,
    ref_date: date | None = None,
) -> pd.DataFrame:
    """Pre-market bars before constituent_exchange opens; empty df when exchange has no premarket_open (e.g. LSE)."""
    if df.empty:
        return df
    exchange_info = time_engine.EXCHANGE_HOURS.get(constituent_exchange, {})
    if "premarket_open" not in exchange_info:
        return df.iloc[:0]
    constituent_open = _exchange_open_utc_dt(constituent_exchange, ref_date)
    ref = ref_date or datetime.now(timezone.utc).date()
    premarket_open_str = exchange_info["premarket_open"]
    h, m = map(int, premarket_open_str.split(":"))
    premarket_start = datetime.combine(ref, dtime(h, m))
    return df[(df.index >= premarket_start) & (df.index < constituent_open)]


def _ticker_exchange_explicit(ticker: str) -> str:
    """Exchange from ticker suffix only — never falls back to HOME_EXCHANGE; plain tickers default to NYSE."""
    suffix_map = time_engine._SUFFIX_TO_EXCHANGE
    # Try longest suffix first to handle multi-part suffixes like .TWO
    for length in (4, 3, 2, 1):
        dot_pos = -(length + 1)
        if len(ticker) > length and ticker[dot_pos] == ".":
            candidate = ticker[dot_pos:].upper()
            if candidate in suffix_map:
                return suffix_map[candidate]
    return "NYSE"


def _infer_constituent_exchanges(tickers: list) -> list[str]:
    """Distinct exchanges ordered by constituent count; first element drives signal timing and FX."""
    counts: dict[str, int] = {}
    for t in tickers:
        ex = _ticker_exchange_explicit(t)
        counts[ex] = counts.get(ex, 0) + 1
    if not counts:
        return ["NYSE"]
    return sorted(counts, key=counts.__getitem__, reverse=True)


def _constituent_currencies(tickers: list) -> list[str]:
    return [_exchange_currency(_ticker_exchange_explicit(t)) for t in tickers]


def _session_relationship(etf_exchange: str, primary_constituent_exchange: str) -> str:
    """'ahead': constituents close before ETF opens; 'behind': extend past ETF close; 'same': overlapping."""
    etf_open, etf_close = time_engine.market_window_utc(etf_exchange)
    con_open, con_close = time_engine.market_window_utc(primary_constituent_exchange)
    if con_close <= etf_open:
        return "ahead"
    if con_close > etf_close:
        return "behind"
    return "same"


def find_unknown_exchange_tickers(tickers: list) -> list[str]:
    """Tickers with unrecognised .XX suffixes; plain tickers (no dot) never flagged."""
    suffix_map = time_engine._SUFFIX_TO_EXCHANGE
    unknown = []
    for ticker in tickers:
        if "." not in ticker:
            continue
        suffix = "." + ticker.rsplit(".", 1)[1]
        if suffix.upper() not in suffix_map:
            unknown.append(ticker)
    return unknown


def _fetch_constituent_closes(config: dict, days: int = 65) -> pd.DataFrame:
    constituent_tickers = [h["ticker"] for h in config["constituents"]]
    etf_ticker = config["etf_ticker"]
    fx_pair = config.get("_fx_pair")
    all_tickers = constituent_tickers + [etf_ticker]
    if fx_pair:
        all_tickers.append(fx_pair)
    ticker_dfs = yahoo_engine.get_price_history(all_tickers, period=f"{days}d", interval="1d")
    if not ticker_dfs:
        return pd.DataFrame()
    df = pd.DataFrame({t: df["Close"] for t, df in ticker_dfs.items() if "Close" in df.columns})
    return df.sort_index()


def _fetch_intraday_data(config: dict, period: str = "5d") -> dict[str, pd.DataFrame]:
    constituent_tickers = [h["ticker"] for h in config["constituents"]]
    etf_ticker = config["etf_ticker"]
    fx_pair = config.get("_fx_pair")
    all_tickers = [etf_ticker] + constituent_tickers
    if fx_pair:
        all_tickers.append(fx_pair)
    return yahoo_engine.get_intraday(all_tickers, period=period, interval="5m", prepost=True)


def _compute_intraday_returns(
    intraday: dict[str, pd.DataFrame],
    constituent_tickers: list,
    etf_exchange: str,
    constituent_exchanges: list[str],
    ref_date: date | None = None,
    daily_df: pd.DataFrame | None = None,
) -> tuple[dict[str, float], str]:
    """Returns ({ticker: return_fraction}, signal_source) with priority: post-ETF-close → intraday → daily."""
    primary_exchange = constituent_exchanges[0] if constituent_exchanges else "NYSE"
    trading_date = ref_date or _last_trading_date_for_exchange(etf_exchange)
    etf_close = _exchange_close_utc_dt(etf_exchange, trading_date)
    returns: dict[str, float] = {}
    found_post = False
    found_intraday = False

    for ticker in constituent_tickers:
        df = intraday.get(ticker)
        if df is None or df.empty or "Close" not in df.columns:
            continue
        closes = df["Close"].dropna()
        ticker_exchange = _ticker_exchange_explicit(ticker)
        same_exchange = etf_exchange == ticker_exchange

        if not same_exchange:
            at_close = closes[closes.index <= etf_close]
            if not at_close.empty:
                ref_price = float(at_close.iloc[-1])
                if ref_price > 0:
                    post_close = closes[closes.index > etf_close]
                    if not post_close.empty:
                        r = float(post_close.iloc[-1]) / ref_price - 1.0
                        if abs(r) > 0.5:
                            continue
                        returns[ticker] = r
                        found_post = True
                        continue

        ticker_trading_date = _last_trading_date_for_exchange(ticker_exchange)
        if daily_df is not None and ticker in daily_df.columns:
            daily_series = daily_df[ticker].dropna()
            if len(daily_series) >= 2:
                yesterday_close = float(daily_series.iloc[-1])
                if yesterday_close > 0:
                    today_bars = closes[closes.index.normalize() == pd.Timestamp(ticker_trading_date)]
                    if not today_bars.empty:
                        r = float(today_bars.iloc[-1]) / yesterday_close - 1.0
                        if abs(r) <= 0.5:
                            returns[ticker] = r
                            found_intraday = True

    if found_post:
        signal_source = "intraday_post_close"
    elif found_intraday:
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
        primary_open_utc, _ = time_engine.market_window_utc(primary_exchange)
        primary_trading_date = _last_trading_date_for_exchange(primary_exchange)
        signal_source = (
            "intraday_live"
            if now_utc >= datetime.combine(primary_trading_date, primary_open_utc)
            else "intraday_premarket"
        )
    else:
        signal_source = "daily_close"

    return returns, signal_source


def _compute_holdings_prediction(
    df: pd.DataFrame,
    constituents: list,
    fx_rate: float,
    last_etf_close: float,
    intraday_returns: dict[str, float] | None,
    fx_pair: str | None,
) -> dict | None:
    """Weighted constituent returns → predicted ETF price. fx_pair column in df provides FX history."""
    df_clean = df.dropna(how="all")
    contributions = []
    weighted_equity_change = 0.0
    used = 0

    fx_prev = None
    if fx_pair and fx_pair in df_clean.columns and len(df_clean[fx_pair].dropna()) >= 2:
        fx_series = df_clean[fx_pair].dropna()
        fx_prev = float(fx_series.iloc[-2])

    for h in constituents:
        ticker = h["ticker"]
        weight = h["weight"]

        if intraday_returns and ticker in intraday_returns:
            constituent_return = intraday_returns[ticker]
        else:
            if ticker not in df_clean.columns:
                continue
            series = df_clean[ticker].dropna()
            if len(series) < 2:
                continue
            constituent_return = float(series.iloc[-1]) / float(series.iloc[-2]) - 1.0

        if abs(constituent_return) > 0.5:
            logger.warning("Skipping %s from prediction: implausible daily return %.1f%% (possible split mismatch)", ticker, constituent_return * 100)
            continue

        contribution = weight * constituent_return
        contributions.append({
            "ticker": ticker,
            "weight": round(weight, 4),
            "return_pct": round(constituent_return * 100, 3),
            "contribution_pct": round(contribution * 100, 3),
        })
        weighted_equity_change += contribution
        used += 1

    if used < 3:
        return None

    fx_change = 0.0
    if fx_pair and fx_prev and fx_prev > 0:
        fx_change = (fx_rate / fx_prev) - 1.0

    fx_adjustment = -fx_change
    total_return = weighted_equity_change + fx_adjustment
    predicted_price = last_etf_close * (1.0 + total_return)

    return {
        "predicted_price": round(predicted_price, 2),
        "predicted_change_pct": round(total_return * 100, 3),
        "contributions": sorted(contributions, key=lambda x: abs(x["contribution_pct"]), reverse=True),
        "fx_adjustment_pct": round(fx_adjustment * 100, 3),
        "n_holdings_used": used,
    }


def _compute_regression_prediction(
    df: pd.DataFrame,
    etf_ticker: str,
    last_etf_close: float,
    constituent_tickers: list,
) -> dict | None:
    """OLS: etf_next_morning_return = α + β × avg_constituent_return. Returns price with 95% CI."""
    us_tickers = [t for t in constituent_tickers if t in df.columns]
    if etf_ticker not in df.columns or len(us_tickers) < 3:
        return None

    df_us = df[us_tickers].dropna(how="all")
    us_daily_ret = df_us.pct_change().mean(axis=1).dropna()

    etf_result = yahoo_engine.get_price_history([etf_ticker], period="70d", interval="1d")
    etf_raw = etf_result.get(etf_ticker)
    if etf_raw is None or etf_raw.empty:
        return None

    etf_opens = etf_raw["Open"]
    etf_closes_raw = etf_raw["Close"]
    etf_opens.index = etf_opens.index.normalize()
    etf_closes_raw.index = etf_closes_raw.index.normalize()

    etf_next_open_ret = (etf_opens.shift(-1) / etf_closes_raw - 1.0).dropna()

    common = us_daily_ret.index.normalize().intersection(etf_next_open_ret.index.normalize())
    if len(common) < 20:
        return None

    X = us_daily_ret.reindex(common).values
    y = etf_next_open_ret.reindex(common).values

    mask = ~(np.isnan(X) | np.isnan(y))
    X, y = X[mask], y[mask]
    if len(X) < 20:
        return None

    slope, intercept, r_value, _, _ = stats.linregress(X, y)
    residuals = y - (intercept + slope * X)
    residual_std = float(np.std(residuals))

    current_us_ret = float(us_daily_ret.iloc[-1]) if not us_daily_ret.empty else 0.0
    predicted_ret = intercept + slope * current_us_ret
    predicted_price = last_etf_close * (1.0 + predicted_ret)
    interval = 1.96 * residual_std

    return {
        "predicted_price": round(predicted_price, 2),
        "predicted_change_pct": round(predicted_ret * 100, 3),
        "lower_bound": round(last_etf_close * (1.0 + predicted_ret - interval), 2),
        "upper_bound": round(last_etf_close * (1.0 + predicted_ret + interval), 2),
        "alpha": round(float(intercept), 6),
        "beta": round(float(slope), 4),
        "r_squared": round(float(r_value**2), 4),
        "n_observations": int(len(X)),
        "residual_std_pct": round(residual_std * 100, 3),
    }


def run_prediction(config_id: int) -> dict:
    config = get_etf_predictor_config(config_id)
    if config is None:
        return {"status": "error", "error": f"Config {config_id} not found", "predicted_price": None}

    etf_ticker = config["etf_ticker"]
    constituents = config["constituents"]
    constituent_tickers = [h["ticker"] for h in constituents]

    etf_info = detect_etf_info(etf_ticker)
    etf_exchange = etf_info["exchange"]
    etf_currency = etf_info["currency"]

    constituent_exchanges = _infer_constituent_exchanges(constituent_tickers)
    constituent_ccys = _constituent_currencies(constituent_tickers)
    fx_pair = detect_fx_pair(etf_currency, constituent_ccys)

    config["_fx_pair"] = fx_pair

    df = _fetch_constituent_closes(config)

    if etf_ticker not in df.columns or df[etf_ticker].dropna().empty:
        fallback = yahoo_engine.get_price_history([etf_ticker], period="65d", interval="1d")
        fallback_df = fallback.get(etf_ticker)
        if fallback_df is not None and not fallback_df.empty:
            close = fallback_df["Close"].copy()
            if close.index.tz is not None:
                close.index = close.index.tz_localize(None)
            close.index = close.index.normalize()
            df.index = df.index.normalize()
            df[etf_ticker] = close.reindex(df.index)

    if etf_ticker not in df.columns or df[etf_ticker].dropna().empty:
        return {"status": "error", "error": f"{etf_ticker} price data unavailable", "predicted_price": None}

    etf_series = df[etf_ticker].dropna()
    last_etf_close = float(etf_series.iloc[-1])

    fx_rate = 1.0
    if fx_pair:
        fetched = yahoo_engine.get_fx_rate(fx_pair)
        if fetched is not None:
            fx_rate = fetched
        else:
            logger.warning("FX rate unavailable for %s, using 1.0", fx_pair)

    signal_source = "daily_close"
    intraday_returns: dict[str, float] | None = None

    try:
        intraday = _fetch_intraday_data(config)
        intraday_returns, signal_source = _compute_intraday_returns(
            intraday, constituent_tickers,
            etf_exchange, constituent_exchanges,
            ref_date=_last_trading_date_for_exchange(etf_exchange),
            daily_df=df,
        )
        if not intraday_returns:
            intraday_returns = None
    except Exception as exc:
        logger.warning("Intraday signal fetch failed for config %s, using daily closes: %s", config_id, exc)

    holdings_result = _compute_holdings_prediction(
        df, constituents, fx_rate, last_etf_close, intraday_returns, fx_pair
    )
    data_source = "holdings" if holdings_result is not None else "regression_only"
    regression_result = _compute_regression_prediction(df, etf_ticker, last_etf_close, constituent_tickers)

    if holdings_result is not None:
        primary_price = holdings_result["predicted_price"]
        primary_change = holdings_result["predicted_change_pct"]
    elif regression_result is not None:
        primary_price = regression_result["predicted_price"]
        primary_change = regression_result["predicted_change_pct"]
    else:
        return {
            "status": "error",
            "error": "Insufficient constituent data (< 3 with prices)",
            "predicted_price": None,
        }

    primary_constituent = constituent_exchanges[0] if constituent_exchanges else etf_exchange
    session_rel = _session_relationship(etf_exchange, primary_constituent)

    # "us_open_impact" when constituents trade after ETF close ("behind"); otherwise "next_open".
    if session_rel == "behind" and signal_source in ("intraday_premarket", "intraday_live"):
        prediction_type = "us_open_impact"
    else:
        prediction_type = "next_open"

    unknown = find_unknown_exchange_tickers(constituent_tickers)
    if unknown:
        log_notification(
            "etf_unknown_exchange",
            f"{etf_ticker}: constituent tickers {unknown} use suffixes not found in "
            f"exchange_hours.json. Exchange markers will default to NYSE. "
            f"Add the exchange definition to data/exchange_hours.json to fix.",
        )

    as_of_utc = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    result = {
        "status": "success",
        "config_id": config_id,
        "predicted_price": primary_price,
        "last_etf_close": round(last_etf_close, 2),
        "predicted_change_pct": primary_change,
        "data_source": data_source,
        "signal_source": signal_source,
        "prediction_type": prediction_type,
        "session_relationship": session_rel,
        "constituent_exchanges": constituent_exchanges,
        "fx_rate": round(fx_rate, 4),
        "fx_pair": fx_pair,
        "as_of_utc": as_of_utc,
        "as_of_local": time_engine.fmt_datetime(datetime.now(timezone.utc)),
        "next_open_date": get_next_open_date(etf_exchange).isoformat(),
        "n_holdings_used": holdings_result["n_holdings_used"] if holdings_result else 0,
        "holdings_engine": holdings_result,
        "regression_engine": regression_result,
        "constituent_snapshot": json.dumps(constituents),
        "etf_info": etf_info,
        "error": None,
    }
    log_etf_prediction(config_id, result)
    return result


def get_etf_correlation_data(config: dict, days: int = 60) -> dict:
    constituent_tickers = [h["ticker"] for h in config["constituents"]]
    etf_ticker = config["etf_ticker"]
    etf_info = detect_etf_info(etf_ticker)
    constituent_ccys = _constituent_currencies(constituent_tickers)
    fx_pair = detect_fx_pair(etf_info["currency"], constituent_ccys)

    all_tickers = constituent_tickers + [etf_ticker]
    if fx_pair:
        all_tickers.append(fx_pair)

    ticker_dfs = yahoo_engine.get_price_history(all_tickers, period=f"{days + 5}d", interval="1d")
    if not ticker_dfs:
        return {"normalized_df": pd.DataFrame(), "rolling_corr": pd.Series(dtype=float), "error": "No data"}

    df = pd.DataFrame({t: d["Close"] for t, d in ticker_dfs.items() if "Close" in d.columns})
    df = df.sort_index().dropna(how="all").tail(days)

    if df.empty:
        return {"normalized_df": pd.DataFrame(), "rolling_corr": pd.Series(dtype=float), "error": "No data"}

    valid_starts = [v for v in df.apply(lambda col: col.first_valid_index()) if v is not None]
    if valid_starts:
        df = df.loc[max(valid_starts):]

    normalized = df.div(df.iloc[0]) * 100
    valid_cols = [
        c for c in normalized.columns
        if c == etf_ticker or (50.0 <= float(normalized[c].dropna().iloc[-1]) <= 150.0)
    ] if not normalized.empty else []
    normalized = normalized[valid_cols]

    us_cols = [t for t in constituent_tickers if t in normalized.columns]
    rolling_corr = pd.Series(dtype=float)
    if us_cols and etf_ticker in normalized.columns:
        us_basket = normalized[us_cols].mean(axis=1)
        rolling_corr = (
            normalized[etf_ticker].pct_change()
            .rolling(window=30, min_periods=15)
            .corr(us_basket.pct_change())
        )

    return {"normalized_df": normalized, "raw_df": df, "rolling_corr": rolling_corr, "error": None}


def get_etf_intraday_overlay_data(config: dict, prediction: dict | None = None) -> dict:
    now_utc = datetime.now(timezone.utc).replace(tzinfo=None)
    etf_ticker = config["etf_ticker"]
    constituent_tickers = [h["ticker"] for h in config["constituents"]]
    etf_info = detect_etf_info(etf_ticker)
    etf_exchange = etf_info["exchange"]
    trading_date = _last_trading_date_for_exchange(etf_exchange)

    window_start = now_utc - timedelta(hours=25)

    all_tickers = [etf_ticker] + constituent_tickers
    intraday = yahoo_engine.get_intraday(all_tickers, period="5d", interval="5m", prepost=True)

    def _strip_tz(df: pd.DataFrame) -> pd.DataFrame:
        df = df.copy()
        if df.index.tz is not None:
            df.index = df.index.tz_convert("UTC").tz_localize(None)
        return df

    etf_series = pd.Series(dtype=float)
    etf_df = intraday.get(etf_ticker)
    if etf_df is not None and not etf_df.empty and "Close" in etf_df.columns:
        etf_df = _strip_tz(etf_df)
        etf_series = etf_df.loc[etf_df.index >= window_start, "Close"].dropna()

    constituent_series: dict[str, pd.Series] = {}
    for ticker in constituent_tickers:
        df = intraday.get(ticker)
        if df is None or df.empty or "Close" not in df.columns:
            continue
        df = _strip_tz(df)
        bars = df[df.index >= window_start]["Close"].dropna()
        if not bars.empty:
            constituent_series[ticker] = bars

    daily_dfs = yahoo_engine.get_price_history([etf_ticker] + constituent_tickers, period="10d", interval="1d")
    daily_df = pd.DataFrame(
        {t: d["Close"] for t, d in (daily_dfs or {}).items() if "Close" in d.columns}
    )

    etf_last_close = 0.0
    if etf_ticker in daily_df.columns:
        s = daily_df[etf_ticker].dropna()
        if not s.empty:
            etf_last_close = float(s.iloc[-1])

    prev_closes = {}
    for t in constituent_tickers:
        if t in daily_df.columns:
            s = daily_df[t].dropna()
            if not s.empty:
                prev_closes[t] = float(s.iloc[-1])

    constituent_exchanges = _infer_constituent_exchanges(constituent_tickers) if constituent_tickers else [etf_exchange]
    primary_constituent = constituent_exchanges[0]
    session_rel = _session_relationship(etf_exchange, primary_constituent)

    return {
        "etf_series": etf_series,
        "constituent_series": constituent_series,
        "now_utc": now_utc,
        "trading_date": trading_date,
        "etf_last_close": etf_last_close,
        "constituent_prev_closes": prev_closes,
        "prediction": prediction or {},
        "next_open_date": get_next_open_date(etf_exchange),
        "constituent_exchanges": constituent_exchanges,
        "session_relationship": session_rel,
    }


def fill_actuals_for_config(config_id: int) -> None:
    config = get_etf_predictor_config(config_id)
    if config is None:
        return
    etf_ticker = config["etf_ticker"]
    try:
        hist = yahoo_engine.get_price_history([etf_ticker], period="5d", interval="1d")
        etf_df = hist.get(etf_ticker)
        if etf_df is None or etf_df.empty or "Open" not in etf_df.columns:
            return

        etf_df = etf_df.copy()
        etf_df.index = etf_df.index.normalize()

        today = datetime.now(timezone.utc).date()
        today_str = today.isoformat()

        today_row = etf_df[etf_df.index == pd.Timestamp(today)]
        if not today_row.empty:
            today_open = float(today_row["Open"].iloc[0])
            if today_open > 0:
                fill_etf_actual(config_id, today_str, today_open, "next_open")

        # us_open_impact: close of reference day fills that day's impact prediction
        if len(etf_df) >= 1:
            last_close_row = etf_df.iloc[-1]
            last_close_date = etf_df.index[-1].date().isoformat()
            close_val = float(last_close_row.get("Close", 0) or 0)
            if close_val > 0:
                fill_etf_actual(config_id, last_close_date, close_val, "us_open_impact")
    except Exception as exc:
        logger.warning("fill_actuals_for_config %s failed: %s", config_id, exc)


def run_all_active_predictions() -> None:
    for cfg in get_etf_predictor_configs():
        if cfg.get("enabled"):
            try:
                run_prediction(cfg["id"])
            except Exception as exc:
                logger.error("run_all_active_predictions: config %s failed: %s", cfg["id"], exc)


def fill_all_actuals() -> None:
    for cfg in get_etf_predictor_configs():
        if cfg.get("enabled"):
            try:
                fill_actuals_for_config(cfg["id"])
            except Exception as exc:
                logger.error("fill_all_actuals: config %s failed: %s", cfg["id"], exc)
