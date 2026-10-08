from __future__ import annotations

import hashlib
from datetime import datetime, timedelta, timezone
from typing import Optional

import pandas as pd

from config import load_config
from data_engine import daily_history_cache_revision, load_or_fetch_daily_history
from database import get_connection
from db_helpers import get_company_names, get_portfolio_watchlist_tickers
from fundamentals_helpers import get_instrument_type
from price_history_helpers import session_window_returns
from utils import ignored_tickers_set, is_excluded_from_yahoo_fetch, normalize_currency_bucket

# GUI name: "Sector-Relative Momentum". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.

WINDOWS = (63, 126)
DEFAULT_WINDOW = 63
MIN_PEERS = 5
RETENTION_DAYS = 30
HISTORY_TAIL_ROWS = 260
READ_BATCH = 900

SCOPE_PORTFOLIO_WATCHLIST = "portfolio_watchlist"
SCOPE_UNIVERSE = "universe"

STATUS_OK = "ok"
STATUS_LABELS = {
    STATUS_OK: "Scored",
    "no_metadata": "No sector data on file",
    "not_equity": "Not an equity (funds and ETFs are not ranked)",
    "no_sector": "Sector unknown",
    "no_currency": "Quote currency unknown",
    "no_history": "No cached price history",
    "insufficient_history": "Not enough aligned price history",
    "small_cohort": f"Fewer than {MIN_PEERS} comparable peers",
    "not_computed": "Not computed yet",
}

_MISSING_SECTORS = {"", "none", "unclassified", "unknown"}


def _now_str() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")


def _is_missing_sector(sector: Optional[str]) -> bool:
    return (sector or "").strip().lower() in _MISSING_SECTORS


def _classify(row: Optional[dict]) -> str:
    """Why a stock_signals row cannot join a peer cohort ('ok' when it can)."""
    if row is None:
        return "no_metadata"
    if get_instrument_type(row.get("quote_type"), "") != "Equity":
        return "not_equity"
    if _is_missing_sector(row.get("sector")):
        return "no_sector"
    if not row.get("currency"):
        return "no_currency"
    return STATUS_OK


def _load_signal_rows(conn, tickers: Optional[list[str]] = None) -> dict[str, dict]:
    sql = "SELECT ticker, company_name, sector, currency, quote_type FROM stock_signals"
    if tickers is None:
        return {r["ticker"]: dict(r) for r in conn.execute(sql).fetchall()}
    rows: dict[str, dict] = {}
    for start in range(0, len(tickers), READ_BATCH):
        batch = tickers[start:start + READ_BATCH]
        placeholders = ",".join("?" * len(batch))
        for r in conn.execute(f"{sql} WHERE ticker IN ({placeholders})", batch).fetchall():
            rows[r["ticker"]] = dict(r)
    return rows


def _load_close(ticker: str) -> Optional[pd.Series]:
    if daily_history_cache_revision(ticker) is None:
        return None
    df = load_or_fetch_daily_history(ticker, cache_only=True)
    if df is None or "Close" not in df.columns:
        return None
    close = df["Close"].dropna().tail(HISTORY_TAIL_ROWS)
    if close.empty:
        return None
    close.index = pd.DatetimeIndex(close.index).tz_localize(None).normalize()
    return close[~close.index.duplicated(keep="last")]


def score_cohort(
    closes: pd.DataFrame, sessions: int, *, sector: str, currency: str, computed_at: str,
) -> tuple[list[dict], dict[str, str]]:
    """Scores one sector x currency cohort. Returns (rows for every eligible member, {ticker: status} for the members that could not be scored)."""
    window = session_window_returns(closes, sessions)
    if window is None:
        return [], {t: "insufficient_history" for t in closes.columns}
    start, as_of, returns = window
    failed = {t: "insufficient_history" for t in closes.columns if t not in returns.index}

    n = len(returns)
    base = {
        "window_sessions": sessions,
        "as_of_date": as_of.strftime("%Y-%m-%d"),
        "window_start_date": start.strftime("%Y-%m-%d"),
        "sector": sector,
        "currency": currency,
        "cohort_size": n,
        "peer_count": n - 1,
        "computed_at": computed_at,
    }
    if n - 1 < MIN_PEERS:
        return [{**base, "ticker": t, "status": "small_cohort"} for t in returns.index], failed

    total = float(returns.sum())
    peer_return = (total - returns) / (n - 1)
    relative_pp = ((returns - peer_return) * 100).round(8)
    percentile = (relative_pp.rank(method="average") - 1) / (n - 1) * 100
    order = sorted(returns.index, key=lambda t: (-relative_pp[t], t))
    rows = [
        {
            **base,
            "ticker": t,
            "status": STATUS_OK,
            "own_return": float(returns[t]),
            "peer_return": float(peer_return[t]),
            "relative_return_pp": float(relative_pp[t]),
            "rank": rank,
            "percentile": float(percentile[t]),
        }
        for rank, t in enumerate(order, start=1)
    ]
    return rows, failed


def _revision(rows: list[dict], sessions: int) -> str:
    parts = [f"{r['ticker']}:{r['own_return']:.8f}" for r in sorted(rows, key=lambda r: r["ticker"])]
    return hashlib.sha1(f"{sessions}|{'|'.join(parts)}".encode()).hexdigest()[:12]


def _save(rows: list[dict]) -> None:
    conn = None
    try:
        conn = get_connection()
        conn.executemany(
            """
            INSERT OR REPLACE INTO sector_relative_momentum_results
                (ticker, window_sessions, as_of_date, status, sector, currency, window_start_date,
                 cohort_size, peer_count, own_return, peer_return, relative_return_pp, rank,
                 percentile, input_revision, computed_at)
            VALUES (:ticker, :window_sessions, :as_of_date, :status, :sector, :currency, :window_start_date,
                    :cohort_size, :peer_count, :own_return, :peer_return, :relative_return_pp, :rank,
                    :percentile, :input_revision, :computed_at)
            """,
            [{k: r.get(k) for k in (
                "ticker", "window_sessions", "as_of_date", "status", "sector", "currency", "window_start_date",
                "cohort_size", "peer_count", "own_return", "peer_return", "relative_return_pp", "rank",
                "percentile", "input_revision", "computed_at",
            )} for r in rows],
        )
        if rows:
            newest = datetime.strptime(max(r["as_of_date"] for r in rows), "%Y-%m-%d")
            cutoff = (newest - timedelta(days=RETENTION_DAYS)).strftime("%Y-%m-%d")
            conn.execute("DELETE FROM sector_relative_momentum_results WHERE as_of_date < ?", (cutoff,))
        conn.commit()
    finally:
        if conn:
            conn.close()


def run_sector_relative_momentum() -> dict:
    """Ranks every eligible equity in the stock_signals peer pool against its sector x currency cohort over each window; persists scored rows for the whole pool and unavailable-reason rows only for Portfolio/Watchlist tickers."""
    computed_at = _now_str()
    ignored = ignored_tickers_set(load_config())
    display = set(get_portfolio_watchlist_tickers())

    conn = None
    try:
        conn = get_connection()
        signal_rows = _load_signal_rows(conn)
    finally:
        if conn:
            conn.close()

    cohorts: dict[tuple, list[str]] = {}
    unavailable: dict[str, tuple[str, Optional[str], Optional[str]]] = {}
    for ticker in display - set(signal_rows):
        unavailable[ticker] = ("no_metadata", None, None)
    for ticker, row in signal_rows.items():
        if is_excluded_from_yahoo_fetch(ticker, ignored):
            continue
        status = _classify(row)
        if status != STATUS_OK:
            if ticker in display:
                unavailable[ticker] = (status, row.get("sector"), row.get("currency"))
            continue
        cohorts.setdefault((row["sector"].strip(), normalize_currency_bucket(row["currency"])), []).append(ticker)

    closes_by_cohort: dict[tuple, pd.DataFrame] = {}
    for (sector, currency), tickers in cohorts.items():
        series = {}
        for t in tickers:
            close = _load_close(t)
            if close is not None:
                series[t] = close
            elif t in display:
                unavailable[t] = ("no_history", sector, currency)
        if series:
            closes_by_cohort[(sector, currency)] = pd.DataFrame(series).sort_index()

    out_rows: list[dict] = []
    scored_by_window: dict[int, int] = {}
    for sessions in WINDOWS:
        window_rows: list[dict] = []
        window_failed: dict[str, tuple] = {}
        for (sector, currency), closes in closes_by_cohort.items():
            rows, failed = score_cohort(
                closes, sessions, sector=sector, currency=currency, computed_at=computed_at,
            )
            window_rows.extend(rows)
            window_failed.update({t: (s, sector, currency) for t, s in failed.items()})
        scored_rows = [r for r in window_rows if r["status"] == STATUS_OK]
        scored_by_window[sessions] = len(scored_rows)
        revision = _revision(scored_rows, sessions)
        for r in window_rows:
            r["input_revision"] = revision
            if r["status"] == STATUS_OK or r["ticker"] in display:
                out_rows.append(r)

        run_as_of = max(
            (r["as_of_date"] for r in window_rows), default=datetime.now(timezone.utc).strftime("%Y-%m-%d")
        )
        present = {r["ticker"] for r in window_rows}
        for ticker in display - present:
            status, sector, currency = unavailable.get(ticker) or window_failed.get(ticker) or ("not_computed", None, None)
            out_rows.append({
                "ticker": ticker, "window_sessions": sessions, "as_of_date": run_as_of, "status": status,
                "sector": sector, "currency": currency, "computed_at": computed_at,
            })

    _save(out_rows)
    return {"scored": scored_by_window[DEFAULT_WINDOW], "cohorts": len(closes_by_cohort), "stored": len(out_rows)}


def _latest_rows(conn, window: int, tickers: Optional[list[str]] = None) -> dict[str, dict]:
    sql = """
        SELECT r.* FROM sector_relative_momentum_results r
        WHERE r.window_sessions = ?
          AND r.as_of_date = (SELECT MAX(as_of_date) FROM sector_relative_momentum_results
                              WHERE ticker = r.ticker AND window_sessions = r.window_sessions)
    """
    if tickers is None:
        return {r["ticker"]: dict(r) for r in conn.execute(sql, (window,)).fetchall()}
    rows: dict[str, dict] = {}
    for start in range(0, len(tickers), READ_BATCH):
        batch = tickers[start:start + READ_BATCH]
        placeholders = ",".join("?" * len(batch))
        for r in conn.execute(f"{sql} AND r.ticker IN ({placeholders})", (window, *batch)).fetchall():
            rows[r["ticker"]] = dict(r)
    return rows


def get_latest_results(tickers: list[str], window: int = DEFAULT_WINDOW) -> dict[str, dict]:
    """Latest persisted row per ticker for `window` — the one read path shared by the report, the Portfolio/Watchlist columns and the AI prompt."""
    if not tickers:
        return {}
    conn = None
    try:
        conn = get_connection()
        return _latest_rows(conn, window, tickers)
    finally:
        if conn:
            conn.close()


def get_column_values(tickers: list[str]) -> dict[str, dict]:
    """{ticker: {sector_rel_mom_63: pp, sector_rel_rank_63: rank, ...}} for the Portfolio/Watchlist optional columns; only scored rows contribute values."""
    values: dict[str, dict] = {}
    for window in WINDOWS:
        for ticker, row in get_latest_results(tickers, window).items():
            if row["status"] == STATUS_OK:
                values.setdefault(ticker, {}).update({
                    f"sector_rel_mom_{window}": row["relative_return_pp"],
                    f"sector_rel_rank_{window}": row["rank"],
                })
    return values


def get_report_rows(scope: str, window: int) -> list[dict]:
    """Rows for the report: scored rows first (strongest relative return first), then unavailable ones with their reason. Portfolio+Watchlist scope lists every member, including ones with no stored row yet."""
    if scope == SCOPE_PORTFOLIO_WATCHLIST:
        tickers = get_portfolio_watchlist_tickers()
        latest = get_latest_results(tickers, window)
        rows = [latest.get(t) or {"ticker": t, "window_sessions": window, "status": "not_computed"} for t in tickers]
    else:
        conn = None
        try:
            conn = get_connection()
            rows = list(_latest_rows(conn, window).values())
        finally:
            if conn:
                conn.close()
    names = get_company_names([r["ticker"] for r in rows])
    for r in rows:
        r["company_name"] = names.get(r["ticker"])
        r["status_label"] = STATUS_LABELS.get(r["status"], r["status"])
    rows.sort(key=lambda r: (
        r["status"] != STATUS_OK,
        -(r.get("relative_return_pp") if r.get("relative_return_pp") is not None else 0.0),
        r["ticker"],
    ))
    return rows


def get_cohort_standing(ticker: str, window: int = DEFAULT_WINDOW, edge: int = 3) -> Optional[dict]:
    """The ticker's scored row plus the strongest/weakest members of its own cohort (same sector, currency and as-of date); None when the ticker has no scored row. Top and bottom lists never overlap."""
    conn = None
    try:
        conn = get_connection()
        row = _latest_rows(conn, window, [ticker]).get(ticker)
        if row is None or row["status"] != STATUS_OK:
            return {"row": row, "top": [], "bottom": []}
        members = [dict(r) for r in conn.execute(
            """
            SELECT ticker, relative_return_pp, rank FROM sector_relative_momentum_results
            WHERE window_sessions = ? AND as_of_date = ? AND sector = ? AND currency = ? AND status = ?
            ORDER BY rank
            """,
            (window, row["as_of_date"], row["sector"], row["currency"], STATUS_OK),
        ).fetchall()]
    finally:
        if conn:
            conn.close()
    n = min(edge, len(members) // 2)
    return {"row": row, "top": members[:n], "bottom": members[len(members) - n:] if n else []}
