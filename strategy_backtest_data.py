# GUI name: "Strategy Backtester". Basket, currency and price-history preparation; the simulation lives in strategy_backtest_engine.py.

import hashlib
import logging
import tempfile
import threading
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional

import pandas as pd

import data_engine
import time_engine
from accounts_engine import native_currencies
from cache_refresh_helpers import request_cache_refresh
from config import BACKTEST_HISTORY_DIR, BASE_CURRENCY
from fx_conversion_helpers import BaseCurrencyConverter
from portfolio_optimizer_engine import list_candidates
from portfolio_service import fx_pair
from price_history_helpers import normalized_close
from stable_shortlist_engine import SIGNAL_ML, SIGNAL_QUANT
from stable_shortlist_reads import get_shortlist
from strategy_backtest_engine import InsufficientHistory
from utils import is_excluded_from_yahoo_fetch, normalize_currency_bucket, safe_ticker_filename
from xray_engine import resolve_scope_holdings
from yahoo_engine import yahoo_engine

logger = logging.getLogger(__name__)

MAX_TICKERS = 40
MIN_TICKERS = 2
EXTENDED_PERIOD = "5y"
EXTENDED_MAX_LAG_DAYS = 5
DEFAULT_BENCHMARKS = {"GBP": "SWDA.L", "USD": "SPY"}
COST_PRESETS = {
    "none": {"label": "None", "commission_bps": 0.0, "spread_bps": 0.0, "slippage_bps": 0.0},
    "low": {"label": "Low (10 bps per side)", "commission_bps": 5.0, "spread_bps": 3.0, "slippage_bps": 2.0},
    "typical": {"label": "Typical (25 bps per side)", "commission_bps": 10.0, "spread_bps": 10.0, "slippage_bps": 5.0},
}
BENCHMARK_COLUMN = "__benchmark__"
SHORTLIST_BASKETS = (
    (SIGNAL_ML, "portfolio"), (SIGNAL_ML, "watchlist"), (SIGNAL_QUANT, "portfolio"), (SIGNAL_QUANT, "watchlist"),
)

_prepare_state: Dict[str, str] = {}
_prepare_lock = threading.Lock()


class BasketError(ValueError):
    pass


def currency_buckets(tickers: List[str]) -> Dict[str, Optional[str]]:
    native = native_currencies(list(tickers))
    return {t: normalize_currency_bucket(native.get(t)) for t in tickers}


def _benchmark_bucket(symbol: str) -> Optional[str]:
    bucket = currency_buckets([symbol])[symbol]
    if bucket:
        return bucket
    return normalize_currency_bucket((yahoo_engine.get_ticker_info(symbol) or {}).get("currency"))


def _clean_tickers(tickers: List[str]) -> List[str]:
    return [t for t in dict.fromkeys(tickers) if t and not is_excluded_from_yahoo_fetch(t) and safe_ticker_filename(t)]


def _extended_path(ticker: str) -> Path:
    return Path(BACKTEST_HISTORY_DIR) / f"{safe_ticker_filename(ticker)}.parquet"


def _read_extended(ticker: str) -> Optional[pd.DataFrame]:
    path = _extended_path(ticker)
    if not path.exists():
        return None
    try:
        return pd.read_parquet(path)
    except Exception as e:
        logger.error("Failed to read extended backtest history for %s: %s", ticker, e)
        return None


def _coverage(df: Optional[pd.DataFrame]) -> Optional[Dict]:
    if df is None or df.empty:
        return None
    return {"start": df.index[0].strftime("%Y-%m-%d"), "end": df.index[-1].strftime("%Y-%m-%d"), "sessions": int(len(df))}


def _extended_usable(extended: Optional[pd.DataFrame], standard: Optional[pd.DataFrame]) -> bool:
    if extended is None or extended.empty:
        return False
    return standard is None or standard.empty or extended.index[-1] >= standard.index[-1] - pd.Timedelta(days=EXTENDED_MAX_LAG_DAYS)


def history_status(tickers: List[str]) -> Dict:
    """Cache-only coverage per ticker so the page never triggers a blocking fetch."""
    out = {}
    for ticker in _clean_tickers(tickers):
        standard = data_engine.load_or_fetch_daily_history(ticker, cache_only=True)
        extended = _read_extended(ticker)
        with _prepare_lock:
            state = _prepare_state.get(ticker)
        out[ticker] = {
            "standard": _coverage(standard), "extended": _coverage(extended),
            "extended_usable": _extended_usable(extended, standard), "state": state,
        }
    return out


def prepare_history_blocking(tickers: List[str]) -> Optional[Dict]:
    tickers = _clean_tickers(tickers)
    prepared, failed = [], []
    try:
        frames = yahoo_engine.get_price_history(tickers, period=EXTENDED_PERIOD, interval="1d", force_refresh=True)
        Path(BACKTEST_HISTORY_DIR).mkdir(parents=True, exist_ok=True)
        for ticker in tickers:
            df = frames.get(ticker)
            if df is None or df.empty:
                failed.append(ticker)
                continue
            df = df.copy()
            if df.index.tz is not None:
                df.index = df.index.tz_convert(None)
            df = data_engine.prepare_daily_history(ticker, df, None)
            if df.empty:
                failed.append(ticker)
                continue
            temporary = None
            try:
                with tempfile.NamedTemporaryFile(dir=BACKTEST_HISTORY_DIR, suffix=".parquet", delete=False) as handle:
                    temporary = Path(handle.name)
                df.to_parquet(temporary, engine="pyarrow")
                temporary.replace(_extended_path(ticker))
                prepared.append(ticker)
            finally:
                if temporary is not None:
                    temporary.unlink(missing_ok=True)
    finally:
        with _prepare_lock:
            for ticker in tickers:
                _prepare_state[ticker] = "ready" if ticker in prepared else "failed"
    return {"prepared": prepared, "failed": failed} if prepared else None


def _fx_pairs(tickers: List[str]) -> List[str]:
    base = normalize_currency_bucket(BASE_CURRENCY)
    pairs = {fx_pair(b) for b in currency_buckets(tickers).values() if b and b != base}
    return sorted(p for p in pairs if p)


def request_history_preparation(tickers: List[str], convert_currency: bool = False) -> Dict:
    tickers = _clean_tickers(tickers)
    if not tickers:
        return {"status": "error", "message": "No fetchable tickers selected."}
    if len(tickers) > MAX_TICKERS:
        return {"status": "error", "message": f"Select at most {MAX_TICKERS} tickers."}
    if convert_currency:
        tickers = tickers + _fx_pairs(tickers)
    key = "backtest-history:" + hashlib.sha1(",".join(sorted(tickers)).encode()).hexdigest()[:16]
    with _prepare_lock:
        for ticker in tickers:
            _prepare_state[ticker] = "preparing"
    if request_cache_refresh(key, lambda: prepare_history_blocking(tickers)) is None:
        with _prepare_lock:
            for ticker in tickers:
                _prepare_state.pop(ticker, None)
        return {"status": "error", "message": "A recent preparation attempt for these tickers failed, or the background queue is full. Try again in a minute."}
    return {"status": "success", "message": f"Preparing {EXTENDED_PERIOD} of history for {len(tickers)} ticker(s) in the background.", "tickers": tickers}


def list_backtest_candidates(account_id: str) -> Dict:
    result = list_candidates(account_id)
    if result.get("status") != "success":
        return result
    symbols = [c["symbol"] for c in result["candidates"]]
    buckets = currency_buckets(symbols)
    coverage = history_status(symbols)
    for c in result["candidates"]:
        c["currency"] = buckets.get(c["symbol"])
        c["history"] = coverage.get(c["symbol"])
        c.pop("history_days", None)
    counts: Dict[Optional[str], Dict] = {}
    for c in result["candidates"]:
        entry = counts.setdefault(c["currency"], {"currency": c["currency"], "held": 0, "watchlist": 0})
        entry["held" if c["held"] else "watchlist"] += 1
    result["currencies"] = sorted(counts.values(), key=lambda e: (-e["held"], -e["watchlist"], str(e["currency"])))
    return result


def list_shortlist_baskets() -> List[Dict]:
    baskets = []
    for signal_type, scope in SHORTLIST_BASKETS:
        payload = get_shortlist(signal_type, scope)
        members = [m["ticker"] for m in payload["members"]]
        if not payload["snapshot"] or not members:
            continue
        buckets = currency_buckets(members)
        by_currency: Dict[str, List[str]] = {}
        for ticker in members:
            by_currency.setdefault(buckets[ticker] or "Unknown", []).append(ticker)
        baskets.append({
            "signal_type": signal_type, "scope": scope, "label": payload["label"],
            "snapshot_id": payload["snapshot"]["id"], "decision_ts": payload["snapshot"]["decision_ts"],
            "members": [{"symbol": m["ticker"], "name": m.get("company_name") or m["ticker"], "currency": buckets[m["ticker"]]} for m in payload["members"]],
            "by_currency": by_currency,
        })
    return baskets


def _single_currency(tickers: List[str], chosen: Optional[str]) -> Dict:
    buckets = currency_buckets(tickers)
    if chosen:
        kept = [t for t in tickers if buckets[t] == chosen]
        if not kept:
            raise BasketError(f"None of the selected tickers is quoted in {chosen}.")
        return {"tickers": kept, "currency": chosen, "excluded": [t for t in tickers if buckets[t] != chosen]}
    unknown = [t for t, b in buckets.items() if not b]
    if unknown:
        raise BasketError("The quote currency is unknown for " + ", ".join(unknown) + " — remove them or wait for the next data refresh.")
    present = sorted(set(buckets.values()))
    if len(present) > 1:
        raise BasketError("The selected tickers span " + " and ".join(present) + ". Each backtest uses one currency unless you convert everything to your base currency, so pick one currency or convert.")
    return {"tickers": tickers, "currency": present[0], "excluded": []}


def _convertible(tickers: List[str]) -> Dict:
    buckets = currency_buckets(tickers)
    unknown = [t for t, b in buckets.items() if not b]
    if unknown:
        raise BasketError("The quote currency is unknown for " + ", ".join(unknown) + " — remove them or wait for the next data refresh.")
    base = normalize_currency_bucket(BASE_CURRENCY)
    return {
        "tickers": tickers, "currency": base, "excluded": [],
        "converted_from": sorted({b for b in buckets.values() if b != base}),
    }


def resolve_basket(req: Dict) -> Dict:
    warnings: List[str] = []
    current_weights = None
    shortlist = None
    if req["basket_type"] == "shortlist":
        payload = get_shortlist(req.get("shortlist_signal"), req.get("shortlist_scope"))
        if not payload["snapshot"]:
            raise BasketError("That Stable Shortlist has no snapshot yet.")
        tickers = _clean_tickers([m["ticker"] for m in payload["members"]])
        shortlist = {
            "signal_type": req["shortlist_signal"], "scope": req["shortlist_scope"],
            "snapshot_id": payload["snapshot"]["id"], "decision_ts": payload["snapshot"]["decision_ts"],
        }
    else:
        tickers = _clean_tickers(req.get("include_tickers") or [])
    if len(tickers) < MIN_TICKERS:
        raise BasketError(f"Select at least {MIN_TICKERS} tickers.")
    if len(tickers) > MAX_TICKERS:
        raise BasketError(f"Select at most {MAX_TICKERS} tickers.")
    resolved = _convertible(tickers) if req.get("convert_currency") else _single_currency(tickers, req.get("currency"))
    if resolved["excluded"]:
        warnings.append("Excluded (other currency): " + ", ".join(resolved["excluded"]) + ".")
    tickers = resolved["tickers"]
    if len(tickers) < MIN_TICKERS:
        raise BasketError(f"Fewer than {MIN_TICKERS} tickers are quoted in {resolved['currency']}.")

    if req["basket_type"] == "account":
        try:
            holdings, _ = resolve_scope_holdings(req["account_id"])
        except RuntimeError as e:
            raise BasketError(str(e))
        weights = {h["symbol"]: h["weight"] for h in holdings if h.get("weight", 0) > 0 and h["symbol"] in tickers}
        current_weights = weights or None
    converted_from = resolved.get("converted_from", [])
    if converted_from:
        warnings.append(f"Prices quoted in {', '.join(converted_from)} are converted to {resolved['currency']} at each day's FX close.")
    return {
        "tickers": tickers, "currency": resolved["currency"], "convert_currency": bool(req.get("convert_currency")), "converted_from": converted_from,
        "current_weights": current_weights, "shortlist": shortlist, "account_id": req.get("account_id"), "warnings": warnings,
    }


def resolve_benchmark(requested: str, currency: str, convert_currency: bool = False) -> Optional[str]:
    symbol = DEFAULT_BENCHMARKS.get(currency) if requested == "auto" else (requested.strip() or None)
    if not symbol or symbol.lower() == "none":
        return None
    if safe_ticker_filename(symbol) is None:
        raise BasketError(f"{symbol} is not a valid benchmark symbol.")
    bucket = _benchmark_bucket(symbol)
    if convert_currency:
        if not bucket:
            raise BasketError(f"The quote currency of benchmark {symbol} is unknown, so it cannot be converted to {currency}.")
    elif bucket != currency:
        raise BasketError(
            f"Benchmark {symbol} is quoted in {bucket or 'an unknown currency'}, but the basket is in {currency}. "
            "Pick a benchmark in the basket's currency, or convert the basket to your base currency."
        )
    return symbol


def load_close_series(ticker: str, history: str) -> Dict:
    standard = data_engine.load_or_fetch_daily_history(ticker)
    extended = _read_extended(ticker) if history == "extended" else None
    frame, source, note = standard, "standard", None
    if history == "extended":
        if _extended_usable(extended, standard):
            frame, source = extended, "extended"
        elif extended is None:
            note = f"{ticker}: extended history is not prepared — the standard cache was used."
        else:
            note = f"{ticker}: prepared history ends before the standard cache — it was ignored; prepare it again."
    close = normalized_close(frame["Close"]) if frame is not None and "Close" in frame.columns else None
    if close is None:
        raise BasketError(f"No price history is available for {ticker}.")
    return {"close": close, "source": source, "note": note, **_coverage(close.to_frame())}


def build_price_matrix(
    series: Dict[str, pd.Series], exchanges: Dict[str, str], benchmark: Optional[pd.Series] = None,
    benchmark_exchange: Optional[str] = None,
) -> Dict:
    """Union calendar over the shared window; a ticker's missing day is a known closure (carried) or an unexpected gap (listed as an issue)."""
    frames = dict(series)
    sources = {**exchanges}
    if benchmark is not None:
        frames[BENCHMARK_COLUMN] = benchmark
        sources[BENCHMARK_COLUMN] = benchmark_exchange
    first = max(s.index[0] for s in frames.values())
    last = min(s.index[-1] for s in frames.values())
    if first >= last:
        raise InsufficientHistory("The selected tickers and benchmark share no overlapping price history.")
    full = pd.DataFrame(frames).sort_index()
    real = full.notna().loc[first:last]
    filled = full.ffill().loc[first:last]
    issues = []
    for column in frames:
        for date in real.index[~real[column].to_numpy()]:
            if time_engine.exchange_had_session(sources[column], date.date()):
                issues.append({"ticker": "benchmark" if column == BENCHMARK_COLUMN else column, "date": date.strftime("%Y-%m-%d")})
    columns = list(series)
    return {
        "prices": filled[columns], "real": real[columns],
        "bench_prices": filled[BENCHMARK_COLUMN] if benchmark is not None else None,
        "bench_real": real[BENCHMARK_COLUMN] if benchmark is not None else None,
        "issues": issues,
    }


def input_digest(prices: pd.DataFrame, bench_prices: Optional[pd.Series]) -> str:
    digest = hashlib.sha256()
    digest.update(",".join(prices.columns).encode())
    digest.update(prices.index.strftime("%Y-%m-%d").to_numpy().astype("U10").tobytes())
    digest.update(prices.to_numpy(dtype="float64").tobytes())
    if bench_prices is not None:
        digest.update(bench_prices.to_numpy(dtype="float64").tobytes())
    return digest.hexdigest()


def session_close_resolver(tickers: List[str], exchanges: Dict[str, str]):
    zones = sorted({exchanges[t] for t in tickers})

    def close_utc(ts: pd.Timestamp) -> datetime:
        return max(time_engine.session_close_utc(zone, ts.date()) for zone in zones)

    return close_utc


def convert_inputs_to_base(config: dict, basket: dict, benchmark: Optional[str], loaded: Dict, bench_loaded: Optional[Dict]):
    """Rewrites each loaded close series into the base currency with dated FX; returns the FX pair coverage and notes, or ({}, []) for a single-currency basket."""
    if not basket.get("convert_currency"):
        return {}, []
    buckets = currency_buckets(basket["tickers"])
    if benchmark:
        buckets[benchmark] = _benchmark_bucket(benchmark)
    notes: List[str] = []

    def load_fx(pair: str) -> Optional[pd.Series]:
        try:
            fx = load_close_series(pair, config["history"])
        except BasketError:
            return None
        if fx["note"]:
            notes.append(fx["note"])
        return fx["close"]

    converter = BaseCurrencyConverter(buckets, load_fx)
    for ticker, item in [*loaded.items(), *([(benchmark, bench_loaded)] if bench_loaded else [])]:
        item["close"] = converter.prices(ticker, item["close"])
    if converter.issues:
        raise BasketError(" ".join(converter.issues.values()))
    return converter.pairs, list(dict.fromkeys(notes))
