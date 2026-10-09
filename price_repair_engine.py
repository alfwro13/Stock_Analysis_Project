import hashlib
import json
import logging
import math
import os
import re
import stat
import tempfile
import threading
from datetime import date

import pandas as pd

from cache_refresh_helpers import request_cache_refresh
from config import DATA_DIR, HISTORICAL_DIR
from database import get_connection
from quant_signals import QuantEngine
from utils import normalize_ticker
from yahoo_engine import yahoo_engine


logger = logging.getLogger(__name__)

BAR_FIELDS = ("Open", "High", "Low", "Close", "Volume")
REPAIRS_PATH = DATA_DIR / "price_repairs.json"
_repair_lock = threading.Lock()


class PriceRepairError(ValueError):
    pass


def _history_path(ticker):
    ticker = normalize_ticker(ticker)
    if not re.fullmatch(r"[A-Z0-9^.=_-]+", ticker):
        raise PriceRepairError("Invalid ticker.")
    expected_name = f"{ticker}.parquet"
    with os.scandir(HISTORICAL_DIR) as entries:
        for entry in entries:
            if entry.name == expected_name and entry.is_file(follow_symlinks=False):
                return entry.path
    raise PriceRepairError("No stored daily history exists for this ticker.")


def _bar(row):
    return {field: float(row[field]) for field in BAR_FIELDS}


def _read_history(ticker):
    path = _history_path(ticker)
    if not os.path.isfile(path):
        raise PriceRepairError("No stored daily history exists for this ticker.")
    return path, pd.read_parquet(path)


def _row_for_date(df, bar_date):
    matched = df.loc[df.index.strftime("%Y-%m-%d") == bar_date]
    return _bar(matched.iloc[0]) if len(matched) == 1 else None


def _fingerprint(path):
    with open(path, "rb") as file:
        return hashlib.sha256(file.read()).hexdigest()


def _validate_bar(bar):
    values = {field: float(bar[field]) for field in BAR_FIELDS}
    if any(not math.isfinite(v) for v in values.values()):
        raise PriceRepairError("OHLCV values must be finite numbers.")
    if any(values[field] <= 0 for field in BAR_FIELDS[:4]) or values["Volume"] < 0:
        raise PriceRepairError("Prices must be positive and volume cannot be negative.")
    if values["High"] < max(values["Open"], values["Close"], values["Low"]) or values["Low"] > min(values["Open"], values["Close"]):
        raise PriceRepairError("High and low must contain the open and close prices.")
    if values["Volume"] != int(values["Volume"]):
        raise PriceRepairError("Volume must be a whole number.")
    return values


def _assess_yahoo_bar(bar, previous, following):
    if bar is None:
        return False, "Yahoo has no bar for this date; enter verified OHLCV manually."
    adjacent = [item["Close"] for item in (previous, following) if item]
    if adjacent and bar["Close"] > max(adjacent) * 10 and bar["Volume"] == 0:
        return False, "Yahoo's bar is over 10 times adjacent closes and has zero volume; verify OHLCV manually."
    return True, "Yahoo's bar is available; compare it with adjacent closes before using it."


def _validate_against_neighbors(bar, previous, following):
    adjacent = [item["Close"] for item in (previous, following) if item]
    if adjacent and max(bar[field] for field in BAR_FIELDS[:4]) > max(adjacent) * 10:
        raise PriceRepairError("Replacement prices are over 10 times adjacent closes. Verify the correct historical OHLCV before repairing.")


def _check_repair_neighbors(df, pos):
    previous = _bar(df.iloc[pos - 1]) if pos else None
    following = _bar(df.iloc[pos + 1]) if pos + 1 < len(df) else None
    return previous, following


def yahoo_bar_status(ticker, bar_date, bar):
    _, df = _read_history(ticker)
    positions = [i for i, value in enumerate(df.index.strftime("%Y-%m-%d")) if value == bar_date]
    if len(positions) != 1:
        raise PriceRepairError("That date is not present in stored daily history.")
    pos = positions[0]
    previous, following = _check_repair_neighbors(df, pos)
    return _assess_yahoo_bar(bar, previous, following)


def _saved_repairs():
    if not REPAIRS_PATH.exists():
        return {}
    with open(REPAIRS_PATH) as file:
        return json.load(file)


def apply_saved_repairs(ticker, df):
    corrections = _saved_repairs().get(ticker, {})
    if not corrections or df.empty:
        return df
    df = df.copy()
    for bar_date, bar in corrections.items():
        if bar_date == "_removed_dates":
            continue
        index = pd.Timestamp(bar_date)
        if index in df.index:
            for field in BAR_FIELDS:
                df.loc[index, field] = bar[field]
        else:
            df.loc[index, list(BAR_FIELDS)] = [bar[field] for field in BAR_FIELDS]
    removed_dates = [pd.Timestamp(value) for value in corrections.get("_removed_dates", [])]
    if removed_dates:
        df = df.drop(index=df.index.intersection(removed_dates))
    return df.sort_index()


def _write_repairs(repairs):
    fd, temporary = tempfile.mkstemp(prefix=".price-repairs-", suffix=".json", dir=REPAIRS_PATH.parent)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(repairs, file, indent=2)
        os.replace(temporary, REPAIRS_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _restore_repairs(previous):
    if previous is None:
        REPAIRS_PATH.unlink(missing_ok=True)
    else:
        _write_repairs(previous)


def _refresh_portfolio_caches():
    from accounts_engine import refresh_all_trading_performance_caches
    from risk_orchestrator_engine import run_scan
    from xray_engine import run_xray_precompute

    refreshed = run_xray_precompute()
    run_scan()
    refresh_all_trading_performance_caches()
    return refreshed


def refresh_downstream(ticker, from_date, history):
    from ml_backfill_engine import rebuild_quant_history
    from db_helpers import get_portfolio_watchlist_tickers

    try:
        history_rows = rebuild_quant_history(ticker, history, from_date)
    except Exception:
        logger.exception("Indicator history rebuild failed for %s from %s", ticker, from_date)
        history_rows = None
    if ticker not in get_portfolio_watchlist_tickers():
        caches = "not_in_scope"
    elif request_cache_refresh(f"price-repair:{ticker}", _refresh_portfolio_caches) is None:
        caches = "unavailable"
    else:
        caches = "queued"
    return {"history_rows_rebuilt": history_rows, "portfolio_caches": caches}


def remove_daily_bar(ticker, bar_date, fingerprint):
    date.fromisoformat(bar_date)
    path, df = _read_history(ticker)
    if _fingerprint(path) != fingerprint:
        raise PriceRepairError("Stored history changed since Check. Run Check again.")
    dates = df.index.strftime("%Y-%m-%d")
    positions = [i for i, value in enumerate(dates) if value == bar_date]
    if len(positions) != 1:
        raise PriceRepairError("That date is not present in stored daily history.")
    pos = positions[0]
    if len(df) < 2:
        raise PriceRepairError("The only remaining historical bar cannot be removed.")
    with open(path, "rb") as file:
        original_bytes = file.read()
    original_mode = stat.S_IMODE(os.stat(path).st_mode)
    updated_df = df.drop(df.index[pos])
    latest_bar = _bar(updated_df.iloc[-1])
    with _repair_lock:
        prior_repairs = _saved_repairs() if REPAIRS_PATH.exists() else None
        updated_repairs = {} if prior_repairs is None else json.loads(json.dumps(prior_repairs))
        ticker_repairs = updated_repairs.setdefault(ticker, {})
        ticker_repairs.pop(bar_date, None)
        removed_dates = set(ticker_repairs.get("_removed_dates", []))
        removed_dates.add(bar_date)
        ticker_repairs["_removed_dates"] = sorted(removed_dates)
        try:
            fd, temporary = tempfile.mkstemp(prefix=".price-repair-remove-", suffix=".parquet", dir=HISTORICAL_DIR)
            os.close(fd)
            try:
                updated_df.to_parquet(temporary, engine="pyarrow")
                os.chmod(temporary, original_mode)
                if _fingerprint(path) != fingerprint:
                    raise PriceRepairError("Stored history changed during removal. Run Check again.")
                os.replace(temporary, path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
            if updated_repairs != prior_repairs:
                if updated_repairs:
                    _write_repairs(updated_repairs)
                else:
                    REPAIRS_PATH.unlink(missing_ok=True)
            conn = None
            try:
                conn = get_connection()
                conn.execute("DELETE FROM quant_signals WHERE ticker=? AND date=?", (ticker, bar_date))
                conn.execute("DELETE FROM score_history WHERE ticker=? AND date=?", (ticker, bar_date))
                conn.commit()
            finally:
                if conn:
                    conn.close()
        except Exception:
            fd, rollback_path = tempfile.mkstemp(prefix=".price-repair-remove-rollback-", dir=HISTORICAL_DIR)
            try:
                with os.fdopen(fd, "wb") as file:
                    file.write(original_bytes)
                os.chmod(rollback_path, original_mode)
                os.replace(rollback_path, path)
            finally:
                if os.path.exists(rollback_path):
                    os.unlink(rollback_path)
                _restore_repairs(prior_repairs)
            raise
    QuantEngine().analyze_ticker(ticker)
    conn = None
    try:
        conn = get_connection()
        conn.execute("UPDATE stock_signals SET current_price=? WHERE ticker=?", (latest_bar["Close"], ticker))
        conn.commit()
    finally:
        if conn:
            conn.close()
    return {"ticker": ticker, "date": bar_date, "removed": True,
            "downstream": refresh_downstream(ticker, bar_date, updated_df)}


def check_daily_bar(ticker, bar_date):
    path, df = _read_history(ticker)
    if not bar_date:
        bar_date = df.index[-1].strftime("%Y-%m-%d")
    date.fromisoformat(bar_date)
    dates = df.index.strftime("%Y-%m-%d")
    positions = [i for i, value in enumerate(dates) if value == bar_date]
    if len(positions) != 1:
        raise PriceRepairError("That date is not present in stored daily history.")
    pos = positions[0]
    fresh = yahoo_engine.get_price_history([ticker], period="2y", interval="1d", force_refresh=True).get(ticker)
    yahoo_bar = _row_for_date(fresh, bar_date) if fresh is not None and not fresh.empty else None
    previous, following = _check_repair_neighbors(df, pos)
    yahoo_usable, yahoo_note = _assess_yahoo_bar(yahoo_bar, previous, following)
    conn = None
    try:
        conn = get_connection()
        quant = conn.execute("SELECT close_price, volume FROM quant_signals WHERE ticker=? AND date=?", (ticker, bar_date)).fetchone()
        score = conn.execute("SELECT close_price FROM score_history WHERE ticker=? AND date=?", (ticker, bar_date)).fetchone()
        stock = conn.execute("SELECT current_price FROM stock_signals WHERE ticker=?", (ticker,)).fetchone()
    finally:
        if conn:
            conn.close()
    return {
        "ticker": ticker, "date": bar_date, "fingerprint": _fingerprint(path),
        "stored": _bar(df.iloc[pos]), "yahoo": yahoo_bar,
        "previous": previous,
        "next": following,
        "yahoo_usable": yahoo_usable,
        "yahoo_note": yahoo_note,
        "quant_close": quant[0] if quant else None,
        "score_close": score[0] if score else None,
        "current_price": stock[0] if stock else None,
        "is_latest": pos == len(df) - 1,
        "saved_repair": _saved_repairs().get(ticker, {}).get(bar_date),
    }


def repair_daily_bar(ticker, bar_date, fingerprint, replacement):
    date.fromisoformat(bar_date)
    replacement = _validate_bar(replacement)
    path, df = _read_history(ticker)
    if _fingerprint(path) != fingerprint:
        raise PriceRepairError("Stored history changed since Check. Run Check again.")
    with open(path, "rb") as file:
        original_bytes = file.read()
    original_mode = stat.S_IMODE(os.stat(path).st_mode)
    dates = df.index.strftime("%Y-%m-%d")
    positions = [i for i, value in enumerate(dates) if value == bar_date]
    if len(positions) != 1:
        raise PriceRepairError("That date is not present in stored daily history.")
    pos = positions[0]
    previous, following = _check_repair_neighbors(df, pos)
    _validate_against_neighbors(replacement, previous, following)
    for field, value in replacement.items():
        df.iloc[pos, df.columns.get_loc(field)] = value
    directory = os.path.dirname(path)
    with _repair_lock:
        prior_repairs = _saved_repairs() if REPAIRS_PATH.exists() else None
        updated_repairs = {} if prior_repairs is None else json.loads(json.dumps(prior_repairs))
        ticker_repairs = updated_repairs.setdefault(ticker, {})
        ticker_repairs[bar_date] = replacement
        removed_dates = set(ticker_repairs.get("_removed_dates", []))
        removed_dates.discard(bar_date)
        if removed_dates:
            ticker_repairs["_removed_dates"] = sorted(removed_dates)
        else:
            ticker_repairs.pop("_removed_dates", None)
        _write_repairs(updated_repairs)
        replaced = False
        try:
            fd, temporary = tempfile.mkstemp(prefix=".price-repair-", suffix=".parquet", dir=directory)
            os.close(fd)
            try:
                df.to_parquet(temporary, engine="pyarrow")
                os.chmod(temporary, original_mode)
                if _fingerprint(path) != fingerprint:
                    raise PriceRepairError("Stored history changed during Repair. Run Check again.")
                os.replace(temporary, path)
                replaced = True
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)

            conn = None
            try:
                conn = get_connection()
                conn.execute("UPDATE quant_signals SET close_price=?, volume=? WHERE ticker=? AND date=?",
                             (replacement["Close"], int(replacement["Volume"]), ticker, bar_date))
                conn.execute("UPDATE score_history SET close_price=? WHERE ticker=? AND date=?",
                             (replacement["Close"], ticker, bar_date))
                conn.commit()
            finally:
                if conn:
                    conn.close()
        except Exception:
            try:
                if replaced:
                    fd, rollback_path = tempfile.mkstemp(prefix=".price-repair-rollback-", dir=directory)
                    try:
                        with os.fdopen(fd, "wb") as file:
                            file.write(original_bytes)
                        os.chmod(rollback_path, original_mode)
                        os.replace(rollback_path, path)
                    finally:
                        if os.path.exists(rollback_path):
                            os.unlink(rollback_path)
            finally:
                _restore_repairs(prior_repairs)
            raise
    QuantEngine().analyze_ticker(ticker)
    return {"ticker": ticker, "date": bar_date, "replacement": replacement, "is_latest": pos == len(df) - 1,
            "downstream": refresh_downstream(ticker, bar_date, df)}
