import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, Optional

from db.connection import write_or_defer
from db.database import get_connection
from time_engine import parse_stored_utc

logger = logging.getLogger(__name__)

_STAMP = "%Y-%m-%d %H:%M:%S"
PUBLISHER_COUNT_COLUMNS = ("full", "short", "blocked", "paywall", "unreachable", "no_text", "error")


def stamp(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime(_STAMP)


@dataclass
class FetcherState:
    cooldown_until: Optional[datetime]
    cooldown_reason: Optional[str]
    consecutive_refusals: int
    last_success_at: Optional[datetime]


@dataclass
class SymbolRow:
    ticker: str
    google_symbol: Optional[str]
    google_exchange: Optional[str]
    kind: str
    verified_at: Optional[datetime]
    last_probe_status: str


def get_states() -> dict:
    """Cooldown state per interface (`ipv4`, `ipv6`); an interface with no row has never been refused."""
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute("SELECT * FROM gfetcher_state").fetchall()
    finally:
        if conn:
            conn.close()
    return {
        r["interface"]: FetcherState(
            parse_stored_utc(r["cooldown_until"]), r["cooldown_reason"], r["consecutive_refusals"],
            parse_stored_utc(r["last_success_at"]),
        )
        for r in rows
    }


def get_state(interface: str) -> FetcherState:
    return get_states().get(interface) or FetcherState(None, None, 0, None)


def _upsert_state(interface: str, **fields) -> None:
    columns = ", ".join(fields)
    placeholders = ", ".join("?" for _ in fields)
    updates = ", ".join(f"{c} = excluded.{c}" for c in fields)
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            f"INSERT INTO gfetcher_state (interface, {columns}) VALUES (?, {placeholders}) "
            f"ON CONFLICT(interface) DO UPDATE SET {updates}",
            (interface, *fields.values()),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def set_cooldown(interface: str, until: datetime, reason: str, consecutive_refusals: int) -> None:
    _upsert_state(interface, cooldown_until=stamp(until), cooldown_reason=reason, consecutive_refusals=consecutive_refusals)


def mark_success(interface: str, now: datetime) -> None:
    _upsert_state(interface, consecutive_refusals=0, cooldown_until=None, cooldown_reason=None, last_success_at=stamp(now))


def record_request(now: datetime, ticker: Optional[str], interface: str, kind: str, http_status: Optional[int], outcome: str,
                   duration_ms: int, retry_after_s: Optional[int] = None) -> None:
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO gfetcher_request_log (requested_at, ticker, interface, kind, http_status, outcome, duration_ms, retry_after_s) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (stamp(now), ticker, interface, kind, http_status, outcome, duration_ms, retry_after_s),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def google_requests_since(since: datetime) -> int:
    conn = None
    try:
        conn = get_connection()
        return conn.execute("SELECT COUNT(*) FROM gfetcher_request_log WHERE requested_at >= ?", (stamp(since),)).fetchone()[0]
    finally:
        if conn:
            conn.close()


def prune_request_log(before: datetime) -> int:
    conn = None
    try:
        conn = get_connection()
        cur = conn.execute("DELETE FROM gfetcher_request_log WHERE requested_at < ?", (stamp(before),))
        conn.commit()
        return cur.rowcount
    finally:
        if conn:
            conn.close()


def get_symbol(ticker: str) -> Optional[SymbolRow]:
    conn = None
    try:
        conn = get_connection()
        row = conn.execute("SELECT * FROM gfetcher_symbol_map WHERE ticker = ?", (ticker,)).fetchone()
    finally:
        if conn:
            conn.close()
    if row is None:
        return None
    return SymbolRow(row["ticker"], row["google_symbol"], row["google_exchange"], row["kind"],
                     parse_stored_utc(row["verified_at"]), row["last_probe_status"])


def save_symbol(ticker: str, google_symbol: Optional[str], google_exchange: Optional[str], kind: str,
                now: datetime, title: Optional[str], status: str) -> None:
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO gfetcher_symbol_map (ticker, google_symbol, google_exchange, kind, verified_at, verified_title, last_probe_status) "
            "VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT(ticker) DO UPDATE SET google_symbol = excluded.google_symbol, "
            "google_exchange = excluded.google_exchange, kind = excluded.kind, verified_at = excluded.verified_at, "
            "verified_title = excluded.verified_title, last_probe_status = excluded.last_probe_status",
            (ticker, google_symbol, google_exchange, kind, stamp(now), title, status),
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def ticker_last_fetched(tickers: Iterable[str]) -> dict:
    wanted = list(tickers)
    if not wanted:
        return {}
    conn = None
    try:
        conn = get_connection()
        placeholders = ", ".join("?" for _ in wanted)
        rows = conn.execute(
            f"SELECT ticker, last_fetched_at FROM gfetcher_ticker_state WHERE ticker IN ({placeholders})", wanted,
        ).fetchall()
    finally:
        if conn:
            conn.close()
    return {r["ticker"]: parse_stored_utc(r["last_fetched_at"]) for r in rows}


def save_ticker_fetch(ticker: str, scope: str, now: datetime, article_count: int) -> int:
    """Returns the ticker's empty streak after this fetch: runs in a row with a valid page but no news cards."""
    conn = None
    try:
        conn = get_connection()
        conn.execute(
            "INSERT INTO gfetcher_ticker_state (ticker, scope, last_fetched_at, last_article_count, empty_streak) "
            "VALUES (?, ?, ?, ?, ?) ON CONFLICT(ticker) DO UPDATE SET scope = excluded.scope, "
            "last_fetched_at = excluded.last_fetched_at, last_article_count = excluded.last_article_count, "
            "empty_streak = CASE WHEN excluded.last_article_count = 0 THEN gfetcher_ticker_state.empty_streak + 1 ELSE 0 END",
            (ticker, scope, stamp(now), article_count, 1 if article_count == 0 else 0),
        )
        conn.commit()
        return conn.execute("SELECT empty_streak FROM gfetcher_ticker_state WHERE ticker = ?", (ticker,)).fetchone()[0]
    finally:
        if conn:
            conn.close()


def load_publishers() -> dict:
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute("SELECT * FROM gfetcher_publishers").fetchall()
    finally:
        if conn:
            conn.close()
    return {r["domain"]: dict(r) for r in rows}


def save_publishers(rows: Iterable[dict]) -> None:
    columns = ("domain", "policy", "source", "reason", *(f"{c}_count" for c in PUBLISHER_COUNT_COLUMNS), "updated_at")
    updates = ", ".join(f"{c} = excluded.{c}" for c in columns if c != "domain")
    values = [tuple(r[c] for c in columns) for r in rows]
    if not values:
        return
    conn = None
    try:
        conn = get_connection()
        conn.executemany(
            f"INSERT INTO gfetcher_publishers ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)}) "
            f"ON CONFLICT(domain) DO UPDATE SET {updates}",
            values,
        )
        conn.commit()
    finally:
        if conn:
            conn.close()


def save_lead(url: str, domain: str, headline: str, lead: str, now: datetime) -> None:
    def write() -> None:
        conn = None
        try:
            conn = get_connection()
            conn.execute(
                "INSERT INTO gfetcher_leads (url, domain, headline, lead, fetched_at) VALUES (?, ?, ?, ?, ?) "
                "ON CONFLICT(url) DO UPDATE SET domain = excluded.domain, headline = excluded.headline, "
                "lead = excluded.lead, fetched_at = excluded.fetched_at WHERE excluded.fetched_at >= gfetcher_leads.fetched_at",
                (url, domain, headline, lead, stamp(now)),
            )
            conn.commit()
        finally:
            if conn:
                conn.close()

    write_or_defer("Google News Fetcher article opening", write)


def peer_leads(domain: str, exclude_url: str) -> list[tuple[str, str]]:
    """(headline, lead) of every stored opening of the publisher's other articles."""
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT headline, lead FROM gfetcher_leads WHERE domain = ? AND url != ?", (domain, exclude_url),
        ).fetchall()
    finally:
        if conn:
            conn.close()
    return [(r["headline"], r["lead"]) for r in rows]


def prune_leads(before: datetime, keep_per_domain: int) -> int:
    conn = None
    try:
        conn = get_connection()
        cur = conn.execute(
            "DELETE FROM gfetcher_leads WHERE fetched_at < ? OR url IN (SELECT url FROM (SELECT url, ROW_NUMBER() OVER "
            "(PARTITION BY domain ORDER BY fetched_at DESC, url) AS position FROM gfetcher_leads) WHERE position > ?)",
            (stamp(before), keep_per_domain),
        )
        conn.commit()
        return cur.rowcount
    finally:
        if conn:
            conn.close()


def existing_article_ids(article_ids: Iterable[str]) -> set:
    wanted = list(article_ids)
    found: set = set()
    conn = None
    try:
        conn = get_connection()
        for start in range(0, len(wanted), 500):
            chunk = wanted[start:start + 500]
            placeholders = ", ".join("?" for _ in chunk)
            found.update(r[0] for r in conn.execute(f"SELECT article_id FROM news_articles WHERE article_id IN ({placeholders})", chunk))
    finally:
        if conn:
            conn.close()
    return found


_ARTICLE_COLUMNS = (
    "article_id", "ticker", "company_name", "source_list", "headline", "summary", "full_text", "body_fetched",
    "url", "publisher", "published_at", "is_premium", "fetched_at", "feed_source", "published_precision",
)


def insert_articles(rows: Iterable[dict]) -> int:
    values = [tuple(r[c] for c in _ARTICLE_COLUMNS) for r in rows]
    if not values:
        return 0
    conn = None
    try:
        conn = get_connection()
        cur = conn.executemany(
            f"INSERT OR IGNORE INTO news_articles ({', '.join(_ARTICLE_COLUMNS)}) VALUES ({', '.join('?' for _ in _ARTICLE_COLUMNS)})",
            values,
        )
        conn.commit()
        return cur.rowcount
    finally:
        if conn:
            conn.close()


def prune_articles(before_epoch: int) -> int:
    conn = None
    try:
        conn = get_connection()
        cur = conn.execute("DELETE FROM news_articles WHERE feed_source = 'google' AND published_at < ?", (before_epoch,))
        conn.commit()
        return cur.rowcount
    finally:
        if conn:
            conn.close()


def score_unscored_articles() -> None:
    from news.news_feed_engine import _score_unscoredrows
    conn = None
    try:
        conn = get_connection()
        _score_unscoredrows(conn)
    finally:
        if conn:
            conn.close()


def request_counts_since(since: datetime) -> list[dict]:
    """(interface, outcome, count) of the Google requests logged since `since`."""
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT interface, outcome, COUNT(*) AS n FROM gfetcher_request_log WHERE requested_at >= ? "
            "GROUP BY interface, outcome ORDER BY interface, outcome",
            (stamp(since),),
        ).fetchall()
    finally:
        if conn:
            conn.close()
    return [dict(r) for r in rows]


def google_article_counts(since_epoch: int) -> dict:
    conn = None
    try:
        conn = get_connection()
        row = conn.execute(
            "SELECT COUNT(*) AS stored, COALESCE(SUM(fetched_at >= ?), 0) AS added, COALESCE(SUM(body_fetched), 0) AS with_text "
            "FROM news_articles WHERE feed_source = 'google'",
            (since_epoch,),
        ).fetchone()
    finally:
        if conn:
            conn.close()
    return dict(row)


def publisher_policy_counts() -> dict:
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute("SELECT policy, source, COUNT(*) AS n FROM gfetcher_publishers GROUP BY policy, source").fetchall()
    finally:
        if conn:
            conn.close()
    return {f"{r['policy']}:{r['source']}": r["n"] for r in rows}


def list_symbols() -> list[dict]:
    conn = None
    try:
        conn = get_connection()
        rows = conn.execute(
            "SELECT ticker, google_symbol, google_exchange, kind, verified_at, verified_title, last_probe_status "
            "FROM gfetcher_symbol_map ORDER BY kind, ticker"
        ).fetchall()
    finally:
        if conn:
            conn.close()
    return [dict(r) for r in rows]
