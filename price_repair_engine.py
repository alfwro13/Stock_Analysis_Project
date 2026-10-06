import hashlib
import json
import math
import os
import stat
import tempfile
import threading
from datetime import date

import pandas as pd

from config import DATA_DIR, HISTORICAL_DIR
from database import get_connection
from quant_signals import QuantEngine
from utils import normalize_ticker, safe_ticker_filename
from yahoo_engine import yahoo_engine


BAR_FIELDS = ("Open", "High", "Low", "Close", "Volume")
REPAIRS_PATH = DATA_DIR / "price_repairs.json"
_repair_lock = threading.Lock()


class PriceRepairError(ValueError):
    pass


def _history_path(ticker):
    ticker = normalize_ticker(ticker)
    safe = safe_ticker_filename(ticker)
    if not safe or safe != ticker:
        raise PriceRepairError("Invalid ticker.")
    return os.path.join(HISTORICAL_DIR, safe + ".parquet")


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
        index = pd.Timestamp(bar_date)
        if index in df.index:
            for field in BAR_FIELDS:
                df.loc[index, field] = bar[field]
        else:
            df.loc[index, list(BAR_FIELDS)] = [bar[field] for field in BAR_FIELDS]
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
        "previous": _bar(df.iloc[pos - 1]) if pos else None,
        "next": _bar(df.iloc[pos + 1]) if pos + 1 < len(df) else None,
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
    for field, value in replacement.items():
        df.iloc[pos, df.columns.get_loc(field)] = value
    directory = os.path.dirname(path)
    with _repair_lock:
        prior_repairs = _saved_repairs() if REPAIRS_PATH.exists() else None
        updated_repairs = {} if prior_repairs is None else json.loads(json.dumps(prior_repairs))
        updated_repairs.setdefault(ticker, {})[bar_date] = replacement
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
    return {"ticker": ticker, "date": bar_date, "replacement": replacement, "is_latest": pos == len(df) - 1}
