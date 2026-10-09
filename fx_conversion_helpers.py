from collections import OrderedDict
from threading import Lock
from typing import Callable, Dict, Optional

import pandas as pd

import portfolio_service
from utils import normalize_currency_bucket

FX_MAX_FILL_DAYS = 3

_FX_CLOSE_CACHE_LIMIT = 16
_fx_close_cache = OrderedDict()
_fx_close_cache_lock = Lock()


def _naive_daily(series: pd.Series) -> pd.Series:
    index = series.index
    if isinstance(index, pd.DatetimeIndex) and index.tz is None and index.is_normalized and index.is_monotonic_increasing and index.is_unique and not series.isna().any():
        return series
    out = series.dropna().copy()
    out.index = pd.DatetimeIndex(out.index).tz_localize(None).normalize()
    return out[~out.index.duplicated(keep="last")].sort_index()


def fx_levels_on(dates: pd.DatetimeIndex, fx_close: pd.Series) -> pd.Series:
    """Latest FX close on or before each date, at most FX_MAX_FILL_DAYS old; NaN where none — never a live or 1.0 fallback."""
    fx = _naive_daily(fx_close)
    stamps = pd.DatetimeIndex(dates).tz_localize(None).normalize()
    if fx.empty:
        return pd.Series(float("nan"), index=dates)
    left = pd.DataFrame({"date": stamps, "slot": range(len(stamps))}).sort_values("date")
    right = fx.rename("fx").rename_axis("fx_date").reset_index()
    merged = pd.merge_asof(
        left, right, left_on="date", right_on="fx_date",
        tolerance=pd.Timedelta(days=FX_MAX_FILL_DAYS), direction="backward",
    ).sort_values("slot")
    return pd.Series(merged["fx"].to_numpy(), index=dates)


def fx_level_on(date, fx_close: pd.Series) -> Optional[float]:
    fx = _naive_daily(fx_close)
    stamp = pd.Timestamp(date).tz_localize(None).normalize()
    position = fx.index.searchsorted(stamp, side="right") - 1
    if position < 0 or (stamp - fx.index[position]).days > FX_MAX_FILL_DAYS:
        return None
    level = float(fx.iloc[position])
    return level if level > 0 else None


def cached_fx_close(pair: str, *, cache_only: bool = False) -> Optional[pd.Series]:
    """The pair's cached daily closes, kept in memory per file revision so a per-day loop (value-history backfill) does not re-read the Parquet on every call."""
    import data_engine

    revision = data_engine.daily_history_cache_revision(pair, refresh_stale=cache_only)
    with _fx_close_cache_lock:
        cached = _fx_close_cache.pop(pair, None)
        if revision is not None and cached is not None and cached[0] == revision:
            _fx_close_cache[pair] = cached
            return cached[1]
    history = data_engine.load_or_fetch_daily_history(pair, cache_only=cache_only)
    close = _naive_daily(history["Close"]) if history is not None and "Close" in history.columns else None
    if close is not None and revision is not None and data_engine.daily_history_cache_revision(pair) == revision:
        with _fx_close_cache_lock:
            _fx_close_cache[pair] = (revision, close)
            while len(_fx_close_cache) > _FX_CLOSE_CACHE_LIMIT:
                _fx_close_cache.popitem(last=False)
    return close


def close_in_base(close: pd.Series, fx_close: pd.Series) -> pd.Series:
    return (close * fx_levels_on(close.index, fx_close)).dropna()


def returns_in_base(returns: pd.Series, fx_close: pd.Series) -> pd.Series:
    """Compounds each return with the FX move over the same interval (the previous row of this series to this row)."""
    levels = fx_levels_on(returns.index, fx_close)
    fx_return = levels / levels.shift(1) - 1
    return ((1 + returns) * (1 + fx_return) - 1).dropna()


class BaseCurrencyConverter:
    """Converts per-ticker price or return series to the base currency with dated FX; a ticker that cannot be converted gets an entry in `issues` and None back."""

    def __init__(self, buckets: Dict[str, Optional[str]], load_fx_close: Callable[[str], Optional[pd.Series]]):
        self._buckets = buckets
        self._load = load_fx_close
        self._fx: Dict[str, Optional[pd.Series]] = {}
        self.issues: Dict[str, str] = {}
        self.pairs: Dict[str, Dict] = {}

    def _base(self) -> str:
        return normalize_currency_bucket(portfolio_service.BASE_CURRENCY)

    def _fx_close(self, ticker: str):
        """The FX close series, True when the ticker is already in the base currency, or None (reason recorded)."""
        bucket = self._buckets.get(ticker)
        base = self._base()
        if not bucket:
            self.issues[ticker] = f"{ticker}: its quote currency is unknown, so it cannot be converted to {base}."
            return None
        if bucket == base:
            return True
        pair = portfolio_service.fx_pair(bucket)
        if pair not in self._fx:
            fx = self._load(pair)
            self._fx[pair] = _naive_daily(fx) if fx is not None and not fx.dropna().empty else None
            if self._fx[pair] is not None:
                series = self._fx[pair]
                self.pairs[pair] = {
                    "start": series.index[0].strftime("%Y-%m-%d"), "end": series.index[-1].strftime("%Y-%m-%d"),
                    "sessions": int(len(series)),
                }
        fx_close = self._fx[pair]
        if fx_close is None:
            self.issues[ticker] = f"{ticker}: no {pair} rate history is available yet, so it cannot be converted to {base}."
        return fx_close

    def _convert(self, ticker: str, series: pd.Series, convert) -> Optional[pd.Series]:
        fx_close = self._fx_close(ticker)
        if fx_close is None:
            return None
        if fx_close is True:
            return series
        converted = convert(series, fx_close)
        if converted.empty:
            self.issues[ticker] = f"{ticker}: no {portfolio_service.fx_pair(self._buckets[ticker])} rate falls within {FX_MAX_FILL_DAYS} days of its prices."
            return None
        return converted

    def prices(self, ticker: str, close: pd.Series) -> Optional[pd.Series]:
        return self._convert(ticker, close, close_in_base)

    def returns(self, ticker: str, returns: pd.Series) -> Optional[pd.Series]:
        return self._convert(ticker, returns, returns_in_base)
