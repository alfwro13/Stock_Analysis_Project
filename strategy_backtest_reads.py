# GUI name: "Strategy Backtester". Read side of strategy_backtest_runs.py: the saved-run list, a run's result payload and its allocation history.

import json
from datetime import datetime
from typing import Dict, List, Optional

import pandas as pd

import time_engine
from database import get_connection
from stable_shortlist_reads import SIGNAL_LABELS
from strategy_backtest_runs import (
    MAX_TRANSACTIONS_PER_STRATEGY,
    STATE_COMPLETED,
    TS_FORMAT,
    expire_interrupted_runs,
    get_run_row,
    run_dir,
)
from xray_engine import native_max_drawdown


def _local(ts: Optional[str]) -> Optional[str]:
    return time_engine.fmt_datetime(datetime.strptime(ts, TS_FORMAT)) if ts else None


def _basket_label(basket: dict) -> str:
    if basket.get("shortlist"):
        short = basket["shortlist"]
        return f"{SIGNAL_LABELS[short['signal_type']]} ({short['scope'].title()})"
    return "Account scope: " + ("Global (All Accounts)" if basket.get("account_id") == "all" else str(basket.get("account_id")))


def list_runs() -> List[Dict]:
    expire_interrupted_runs()
    conn = None
    try:
        conn = get_connection()
        rows = [dict(r) for r in conn.execute("SELECT * FROM strategy_backtest_runs ORDER BY created_at DESC, id DESC").fetchall()]
    finally:
        if conn:
            conn.close()
    runs = []
    for r in rows:
        basket, config = json.loads(r["basket_json"]), json.loads(r["config_json"])
        summary = json.loads(r["summary_json"]) if r["summary_json"] else None
        runs.append({
            "id": r["id"], "state": r["state"], "created_at": _local(r["created_at"]), "error": r["error"],
            "basket": _basket_label(basket), "currency": basket["currency"], "tickers": len(basket["tickers"]),
            "strategies": len(config["strategies"]), "period": summary["period"] if summary else None,
            "incomplete": summary["incomplete"] if summary else False,
        })
    return runs


def _series(df: pd.DataFrame, column: str, digits: int) -> List[Optional[float]]:
    return [None if pd.isna(v) else round(float(v), digits) for v in df[column]]


def _drawdown(equity: pd.Series) -> List[float]:
    _, curve = native_max_drawdown(equity.pct_change().fillna(0.0))
    return [round(float(v), 4) for v in curve]


def get_run(run_id: str) -> Dict:
    expire_interrupted_runs()
    row = get_run_row(run_id)
    if row is None:
        return {"status": "error", "message": "Run not found."}
    basket, config = json.loads(row["basket_json"]), json.loads(row["config_json"])
    payload = {
        "status": "success",
        "run": {
            "id": row["id"], "state": row["state"], "created_at": _local(row["created_at"]),
            "finished_at": _local(row["finished_at"]), "error": row["error"], "config": config,
            "basket": {**basket, "label": _basket_label(basket)},
            "inputs": json.loads(row["inputs_json"]) if row["inputs_json"] else None,
        },
        "result": None,
    }
    if row["state"] != STATE_COMPLETED:
        return payload
    summary = json.loads(row["summary_json"])
    decisions = json.loads(row["decisions_json"])
    directory = run_dir(run_id)
    equity = pd.read_parquet(directory / "equity.parquet")
    transactions = pd.read_parquet(directory / "transactions.parquet")
    if "strategy" not in transactions.columns:
        transactions = pd.DataFrame(columns=["strategy"])
    strategies = []
    for sid in config["strategies"]:
        item = summary["strategies"][sid]
        entry = {"id": sid, "label": item["label"], "available": item["available"], "reason": item.get("reason")}
        if item["available"]:
            own = transactions[transactions["strategy"] == sid]
            entry.update({
                "summary": item["summary"], "equity": _series(equity, sid, 2), "drawdown": _drawdown(equity[sid]),
                "cash_fraction": _series(equity, f"{sid}|cash", 4), "decisions": decisions.get(sid, []),
                "transactions": own.drop(columns="strategy").head(MAX_TRANSACTIONS_PER_STRATEGY).to_dict("records"),
                "transactions_total": int(len(own)),
            })
        strategies.append(entry)
    benchmark = None
    if summary["benchmark"] is not None:
        benchmark = {
            "label": config["benchmark"], "summary": summary["benchmark"],
            "equity": _series(equity, "benchmark", 2), "drawdown": _drawdown(equity["benchmark"]),
        }
    payload["result"] = {
        "dates": [d.strftime("%Y-%m-%d") for d in equity.index], "period": summary["period"],
        "shared_window": summary["shared_window"], "warnings": summary["warnings"], "issues": summary["issues"], "issues_total": summary["issues_total"],
        "incomplete": summary["incomplete"], "strategies": strategies, "benchmark": benchmark,
    }
    return payload


def get_allocations(run_id: str, strategy_id: str) -> Dict:
    row = get_run_row(run_id)
    if row is None or row["state"] != STATE_COMPLETED:
        return {"status": "error", "message": "Run not found or not finished."}
    directory = run_dir(run_id)
    allocations = pd.read_parquet(directory / "allocations.parquet")
    prefix = f"{strategy_id}|"
    columns = [c for c in allocations.columns if c.startswith(prefix)]
    if not columns:
        return {"status": "error", "message": "No allocation history for that strategy."}
    equity = pd.read_parquet(directory / "equity.parquet")
    return {
        "status": "success", "dates": [d.strftime("%Y-%m-%d") for d in allocations.index],
        "weights": {c[len(prefix):]: _series(allocations, c, 4) for c in columns},
        "cash": _series(equity, f"{strategy_id}|cash", 4),
    }
