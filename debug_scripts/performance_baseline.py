import argparse
import logging
import math
import os
import secrets
import statistics
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from unittest.mock import patch

os.environ.setdefault("DASHBOARD_USERNAME", "baseline")
os.environ.setdefault("DASHBOARD_PASSWORD", secrets.token_urlsafe(24))
os.environ.setdefault("ADMIN_CONFIRM_TOKEN", secrets.token_urlsafe(24))
os.environ.setdefault("APP_SECRET_KEY", secrets.token_urlsafe(24))
os.environ.setdefault("API_KEY", secrets.token_urlsafe(24))

project_root = Path(__file__).resolve().parent.parent
if Path.cwd().resolve() != project_root or not (project_root / "audit").is_symlink():
    raise SystemExit("Run from the root of an isolated worktree with the canonical audit symlink")
for path in (project_root / "data", project_root / "data/analysis.db", project_root / "data/historical", project_root / "data/intraday", project_root / "config.json"):
    if path.is_symlink():
        raise SystemExit("Benchmark inputs must be local copies inside the isolated worktree")
if not (project_root / "data/analysis.db").is_file() or not (project_root / "config.json").is_file():
    raise SystemExit("Copy the development database snapshot and config into this worktree first")
sys.path.insert(0, str(project_root))

from fastapi.testclient import TestClient

from accounts_engine import get_combined_holdings
from database import get_accounts, get_connection, get_watchlist_tickers
from main import app


def summarize(label, samples):
    handler = sorted(item[0] for item in samples)
    total = sorted(item[1] for item in samples)
    status = sorted({item[2] for item in samples})
    rank = math.ceil(0.95 * len(samples)) - 1
    print(
        f"{label}: n={len(samples)} status={status} "
        f"handler_median_ms={statistics.median(handler):.1f} handler_p95_ms={handler[rank]:.1f} "
        f"body_median_ms={statistics.median(total):.1f} body_p95_ms={total[rank]:.1f}"
    )


def response_sample(response, started):
    if response.status_code != 200:
        raise RuntimeError(f"Baseline request returned HTTP {response.status_code}")
    header = response.headers.get("Server-Timing", "")
    if not header.startswith("app;dur="):
        raise RuntimeError("Baseline request is missing Server-Timing")
    handler = float(header.split("app;dur=")[1].split(",")[0])
    return handler, (time.perf_counter() - started) * 1000, response.status_code


def sample(client, path, count):
    values = []
    for _ in range(count):
        started = time.perf_counter()
        values.append(response_sample(client.get(path), started))
    return values


def sample_intraday(client, ticker):
    started = time.perf_counter()
    response = client.post("/api/intraday-chart/refresh", json={"ticker": ticker})
    return [response_sample(response, started)]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=int, default=5)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")
    logging.getLogger("httpx").setLevel(logging.WARNING)
    head = subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], text=True).strip()
    print(f"development_snapshot head={head} python={sys.version.split()[0]} samples={args.samples}")
    holdings = {item["ticker"] for item in get_combined_holdings().values() if item.get("ticker")}
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute("SELECT ticker, currency FROM stock_signals").fetchall()
    finally:
        if conn:
            conn.close()
    currency = {row["ticker"]: row["currency"] for row in rows}
    us = next((t for t in sorted(holdings) if currency.get(t) == "USD"), None)
    lse = next((t for t in sorted(holdings) if currency.get(t) == "GBp" and t.endswith(".L")), None)
    if lse is None:
        lse = next((t for t in sorted(holdings) if currency.get(t) == "GBP" and t.endswith(".L")), None)
    print(f"held_lse_quote_currency={currency.get(lse) if lse else 'unavailable'}")
    watchlist = next((t for t in get_watchlist_tickers() if t not in holdings), None)
    trading = next((a for a in get_accounts() if a["account_type"] == "Trading"), None)
    routes = [("portfolio_all", "/portfolio")]
    if trading:
        routes.append(("portfolio_trading", f"/portfolio?account_id=acct:{trading['id']}"))
    for label, ticker in (("detail_held_us", us), ("detail_held_lse", lse), ("detail_watchlist_only", watchlist)):
        if ticker:
            routes.append((label, f"/stock/{ticker}"))
        else:
            print(f"{label}: unavailable in snapshot")
    routes.extend([
        ("ha_totals", "/api/accounts/portfolio-totals"),
        ("ha_metrics", "/api/accounts/list-with-metrics"),
        ("ha_holdings", "/api/accounts/holdings-list"),
        ("ha_other", "/api/accounts/other-accounts-list"),
    ])
    with (
        patch("main.run_yfinance_smoke_test"),
        patch("main.start_scheduler"),
        patch("main.reload_scheduler"),
        patch("main.shutdown_scheduler"),
        patch("main.start_model_compatibility_guard"),
        patch("main.resume_interrupted_scans"),
        patch("utils.ensure_workflow_assets"),
        patch("utils.notify_requirements_drift"),
        patch("api_routes_accounts.fetch_and_save_pulse"),
        patch("yahoo_engine.yf.download", side_effect=RuntimeError("baseline offline")),
        patch("yahoo_engine.yf.Ticker", side_effect=RuntimeError("baseline offline")),
        patch("yahoo_engine.yahoo_engine.get_fx_rate", return_value=1.0),
    ):
        with TestClient(app, headers={"X-API-Key": os.environ["API_KEY"]}, raise_server_exceptions=False) as client:
            for label, path in routes:
                summarize(label, sample(client, path, args.samples))
            def delayed_fx(pair):
                time.sleep(0.2)
                return 1.0

            with patch("yahoo_engine.yahoo_engine.get_fx_rate", side_effect=delayed_fx):
                summarize("portfolio_slow_fx", sample(client, "/portfolio", args.samples))
            if us:
                parquet = Path("data/historical") / f"{us}.parquet"
                if parquet.exists():
                    parked = parquet.with_suffix(".parquet.baseline-parked")
                    parquet.rename(parked)
                    try:
                        summarize("detail_missing_history", sample(client, f"/stock/{us}", args.samples))
                    finally:
                        parked.rename(parquet)
            with ThreadPoolExecutor(max_workers=2) as pool:
                first = pool.submit(sample, client, "/portfolio", 1)
                second = pool.submit(sample, client, "/api/accounts/portfolio-totals", 1)
                summarize("concurrent_portfolio", first.result())
                summarize("concurrent_ha_totals", second.result())
            if us:
                entered = Event()
                release = Event()

                def stalled_intraday(*args, **kwargs):
                    entered.set()
                    release.wait(10)
                    return {}

                with patch("api_routes.yahoo_engine.get_intraday", side_effect=stalled_intraday):
                    with ThreadPoolExecutor(max_workers=2) as pool:
                        pending = pool.submit(sample_intraday, client, us)
                        if not entered.wait(10):
                            release.set()
                            raise RuntimeError("Intraday stub was not reached")
                        unrelated = pool.submit(sample, client, "/api/accounts/other-accounts-list", 1)
                        time.sleep(0.5)
                        completed_during_block = unrelated.done()
                        release.set()
                        summarize("stalled_intraday", pending.result())
                        summarize("ha_during_stalled_intraday", unrelated.result())
                        print(f"ha_completed_before_upstream_release={completed_during_block}")


if __name__ == "__main__":
    main()
