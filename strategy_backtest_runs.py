# GUI name: "Strategy Backtester". Run lifecycle and storage: a SQLite row per run (config, summary, decisions) plus Parquet series under data/strategy_backtests/<run_id>/.

import json
import logging
import shutil
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, Optional

import numpy as np
import pandas as pd

import time_engine
from accounts_engine import native_currencies
from config import BACKTEST_RUNS_DIR, load_config
from database import get_connection
from portfolio_optimizer_engine import cap_infeasible_warning, cap_is_infeasible
from strategy_backtest_data import (
    COST_PRESETS,
    BasketError,
    build_price_matrix,
    convert_inputs_to_base,
    input_digest,
    load_close_series,
    resolve_basket,
    resolve_benchmark,
    session_close_resolver,
)
from strategy_backtest_engine import CADENCES, InsufficientHistory, run_backtest
from strategy_backtest_strategies import STRATEGIES
from utils import sanitize_floats

logger = logging.getLogger(__name__)

CONFIG_VERSION = 1
MAX_SAVED_RUNS = 20
INTERRUPTED_AFTER = timedelta(minutes=30)
MAX_TRANSACTIONS_PER_STRATEGY = 500
MAX_LISTED_ISSUES = 200
STATE_QUEUED, STATE_RUNNING, STATE_COMPLETED, STATE_FAILED = "queued", "running", "completed", "failed"
TS_FORMAT = "%Y-%m-%d %H:%M:%S"
OPTIMIZER_STRATEGIES = ("min_variance", "max_sharpe")
DEFAULTS = {
    "cadence": "quarterly", "lookback": 252, "initial_capital": 10000.0, "cost_preset": "typical",
    "cash_rate": 0.0, "band_pp": 5.0, "max_weight": 0.20, "cash_reserve": 0.0, "history": "standard",
}


def _now() -> str:
    return datetime.now(timezone.utc).strftime(TS_FORMAT)


def _native(value):
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.strftime(TS_FORMAT)
    raise TypeError(f"Not JSON serialisable: {type(value)}")


def _dumps(payload) -> str:
    return json.dumps(sanitize_floats(payload), default=_native)


def run_dir(run_id: str) -> Path:
    return Path(BACKTEST_RUNS_DIR) / run_id


def _optimizer_unavailable(config: dict, n: int) -> Optional[str]:
    budget = 1.0 - config["cash_reserve"]
    if cap_is_infeasible(n, config["max_weight"], budget):
        return cap_infeasible_warning(n, config["max_weight"], config["cash_reserve"])
    return None


def build_config(req: Dict, basket: Dict) -> Dict:
    if req["cost_preset"] == "custom":
        costs = {k: req[k] for k in ("commission_bps", "spread_bps", "slippage_bps")}
    else:
        costs = {k: COST_PRESETS[req["cost_preset"]][k] for k in ("commission_bps", "spread_bps", "slippage_bps")}
    return {
        "version": CONFIG_VERSION, "strategies": list(dict.fromkeys(req["strategies"])), "cadence": req["cadence"],
        "lookback": req["lookback"], "initial_capital": req["initial_capital"], "cost_preset": req["cost_preset"],
        **costs, "cash_rate": req["cash_rate"], "band_pp": req["band_pp"], "max_weight": req["max_weight"],
        "cash_reserve": req["cash_reserve"], "risk_free_rate": float(load_config().get("RISK_FREE_RATE", 0.045)),
        "history": req["history"], "benchmark": req["benchmark"], "current_weights": basket["current_weights"],
    }


def create_run(req: Dict) -> Dict:
    """Validates the request and queues a run row; the computation itself is execute_run()."""
    unknown = [s for s in req["strategies"] if s not in STRATEGIES]
    if unknown:
        return {"status": "error", "message": "Unknown strategy: " + ", ".join(unknown)}
    if req["cadence"] not in CADENCES:
        return {"status": "error", "message": "Unknown rebalance cadence."}
    try:
        basket = resolve_basket(req)
        benchmark = resolve_benchmark(req["benchmark"], basket["currency"], basket["convert_currency"])
    except BasketError as e:
        return {"status": "error", "message": str(e)}
    config = build_config({**req, "benchmark": benchmark}, basket)

    skipped: Dict[str, str] = {}
    if basket["current_weights"] is None and "current_weights" in config["strategies"]:
        skipped["current_weights"] = "None of the selected tickers is currently held, so there are no weights to apply."
    cap_problem = _optimizer_unavailable(config, len(basket["tickers"]))
    for sid in OPTIMIZER_STRATEGIES:
        if cap_problem and sid in config["strategies"]:
            skipped[sid] = cap_problem
    runnable = [s for s in config["strategies"] if s not in skipped]
    if not runnable:
        return {"status": "error", "message": " ".join(skipped.values())}
    basket["skipped"] = skipped

    run_id = uuid.uuid4().hex[:12]
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO strategy_backtest_runs (id, state, created_at, config_json, basket_json) VALUES (?, ?, ?, ?, ?)",
            (run_id, STATE_QUEUED, _now(), _dumps(config), _dumps(basket)),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()
    return {"status": "success", "run_id": run_id}


def get_run_row(run_id: str) -> Optional[dict]:
    conn = None
    try:
        conn = get_connection()
        found = conn.execute("SELECT * FROM strategy_backtest_runs WHERE id = ?", (run_id,)).fetchone()
        return dict(found) if found else None
    finally:
        if conn:
            conn.close()


def _update(run_id: str, **fields) -> None:
    conn = None
    try:
        conn = get_connection()
        assignments = ", ".join(f"{k} = ?" for k in fields)
        conn.execute(f"UPDATE strategy_backtest_runs SET {assignments} WHERE id = ?", (*fields.values(), run_id))
        conn.commit()
    finally:
        if conn:
            conn.close()


def expire_interrupted_runs() -> None:
    cutoff = (datetime.now(timezone.utc) - INTERRUPTED_AFTER).strftime(TS_FORMAT)
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "UPDATE strategy_backtest_runs SET state = ?, finished_at = ?, error = ? WHERE state IN (?, ?) AND created_at < ?",
            (STATE_FAILED, _now(), "The run was interrupted (for example by a server restart).", STATE_QUEUED, STATE_RUNNING, cutoff),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def prune_runs() -> int:
    conn = None
    try:
        conn = get_connection()
        stale = [r["id"] for r in conn.execute(
            "SELECT id FROM strategy_backtest_runs WHERE state IN (?, ?) ORDER BY created_at DESC, id DESC LIMIT -1 OFFSET ?",
            (STATE_COMPLETED, STATE_FAILED, MAX_SAVED_RUNS),
        ).fetchall()]
        for run_id in stale:
            conn.execute("DELETE FROM strategy_backtest_runs WHERE id = ?", (run_id,))
        conn.commit()
    finally:
        if conn:
            conn.close()
    for run_id in stale:
        shutil.rmtree(run_dir(run_id), ignore_errors=True)
    return len(stale)


def delete_run(run_id: str) -> Dict:
    row = get_run_row(run_id)
    if row is None:
        return {"status": "error", "message": "Run not found."}
    if row["state"] in (STATE_QUEUED, STATE_RUNNING):
        return {"status": "error", "message": "The run is still in progress."}
    conn = None
    try:
        conn = get_connection()
        conn.execute("DELETE FROM strategy_backtest_runs WHERE id = ?", (run_id,))
        conn.commit()
    finally:
        if conn:
            conn.close()
    shutil.rmtree(run_dir(run_id), ignore_errors=True)
    return {"status": "success"}


def _load_inputs(config: dict, basket: dict, benchmark: Optional[str]):
    tickers = basket["tickers"]
    loaded = {t: load_close_series(t, config["history"]) for t in tickers}
    bench_loaded = load_close_series(benchmark, config["history"]) if benchmark else None
    native = native_currencies(tickers + ([benchmark] if benchmark else []))
    exchanges = {t: time_engine.ticker_exchange(t, native.get(t) or basket["currency"]) for t in tickers}
    bench_exchange = time_engine.ticker_exchange(benchmark, native.get(benchmark) or basket["currency"]) if benchmark else None
    fx_pairs, fx_notes = convert_inputs_to_base(config, basket, benchmark, loaded, bench_loaded)
    return loaded, bench_loaded, exchanges, bench_exchange, fx_pairs, fx_notes


def _compute(config: dict, basket: dict) -> Dict:
    benchmark = config["benchmark"]
    loaded, bench_loaded, exchanges, bench_exchange, fx_pairs, fx_notes = _load_inputs(config, basket, benchmark)
    matrix = build_price_matrix(
        {t: v["close"] for t, v in loaded.items()}, exchanges,
        bench_loaded["close"] if bench_loaded else None, bench_exchange,
    )
    skipped = dict(basket.get("skipped", {}))
    runnable = [s for s in config["strategies"] if s not in skipped]
    close_utc = session_close_resolver(basket["tickers"], exchanges)
    result = run_backtest(
        matrix["prices"], matrix["real"], matrix["bench_prices"], matrix["bench_real"], runnable, config, close_utc,
    )
    notes = [v["note"] for v in loaded.values() if v["note"]] + ([bench_loaded["note"]] if bench_loaded and bench_loaded["note"] else []) + fx_notes
    return {
        "result": result, "matrix": matrix, "skipped": skipped, "notes": notes,
        "sources": {t: {k: v[k] for k in ("source", "start", "end", "sessions")} for t, v in loaded.items()},
        "benchmark_source": ({k: bench_loaded[k] for k in ("source", "start", "end", "sessions")} if bench_loaded else None),
        "exchanges": exchanges, "fx_pairs": fx_pairs,
    }


def _store(run_id: str, config: dict, basket: dict, outcome: Dict) -> None:
    result, matrix = outcome["result"], outcome["matrix"]
    directory = run_dir(run_id)
    directory.mkdir(parents=True, exist_ok=True)

    equity_cols, allocation_cols, transactions, decisions, strategies_summary = {}, {}, [], {}, {}
    for sid in config["strategies"]:
        if sid in outcome["skipped"]:
            strategies_summary[sid] = {"label": STRATEGIES[sid].label, "available": False, "reason": outcome["skipped"][sid]}
            continue
        item = result["strategies"][sid]
        if not item["available"]:
            strategies_summary[sid] = {"label": STRATEGIES[sid].label, "available": False, "reason": item["reason"]}
            continue
        equity_cols[sid] = item["equity"]
        equity_cols[f"{sid}|gross"] = item["gross_equity"]
        equity_cols[f"{sid}|cash"] = item["cash_fraction"]
        for ticker in item["weights"].columns:
            allocation_cols[f"{sid}|{ticker}"] = item["weights"][ticker]
        transactions.extend({"strategy": sid, **t} for t in item["transactions"])
        decisions[sid] = item["decisions"]
        strategies_summary[sid] = {"label": STRATEGIES[sid].label, "available": True, "summary": item["summary"]}
    if not decisions:
        raise BasketError(
            "None of the selected strategies was able to run: "
            + " ".join(f"{v['label']}: {v['reason']}" for v in strategies_summary.values())
        )
    if result["benchmark"]:
        equity_cols["benchmark"] = result["benchmark"]["equity"]

    pd.DataFrame(equity_cols).rename_axis("date").to_parquet(directory / "equity.parquet", engine="pyarrow")
    pd.DataFrame(allocation_cols).rename_axis("date").to_parquet(directory / "allocations.parquet", engine="pyarrow")
    pd.DataFrame(transactions).to_parquet(directory / "transactions.parquet", engine="pyarrow")

    dates = result["dates"]
    issues = matrix["issues"]
    summary = {
        "strategies": strategies_summary,
        "benchmark": result["benchmark"]["summary"] if result["benchmark"] else None,
        "period": {"start": dates[0].strftime("%Y-%m-%d"), "end": dates[-1].strftime("%Y-%m-%d"), "sessions": len(dates)},
        "shared_window": {
            "start": matrix["prices"].index[0].strftime("%Y-%m-%d"), "end": matrix["prices"].index[-1].strftime("%Y-%m-%d"),
            "sessions": len(matrix["prices"]),
        },
        "warnings": basket.get("warnings", []) + outcome["notes"],
        "issues": issues[:MAX_LISTED_ISSUES], "issues_total": len(issues), "incomplete": bool(issues),
    }
    inputs = {
        "digest": input_digest(matrix["prices"], matrix["bench_prices"]), "sources": outcome["sources"],
        "benchmark": config["benchmark"], "benchmark_source": outcome["benchmark_source"],
        "exchanges": outcome["exchanges"], "fx_pairs": outcome["fx_pairs"],
    }
    _update(
        run_id, state=STATE_COMPLETED, finished_at=_now(), summary_json=_dumps(summary),
        decisions_json=_dumps(decisions), inputs_json=_dumps(inputs),
    )


def execute_run(run_id: str) -> None:
    row = get_run_row(run_id)
    if row is None or row["state"] != STATE_QUEUED:
        return
    _update(run_id, state=STATE_RUNNING)
    config, basket = json.loads(row["config_json"]), json.loads(row["basket_json"])
    try:
        _store(run_id, config, basket, _compute(config, basket))
    except (BasketError, InsufficientHistory) as e:
        shutil.rmtree(run_dir(run_id), ignore_errors=True)
        _update(run_id, state=STATE_FAILED, finished_at=_now(), error=str(e))
    except Exception:
        logger.exception("Strategy Backtester run %s failed", run_id)
        shutil.rmtree(run_dir(run_id), ignore_errors=True)
        _update(run_id, state=STATE_FAILED, finished_at=_now(), error="The backtest failed unexpectedly; see the server log.")
    finally:
        prune_runs()
