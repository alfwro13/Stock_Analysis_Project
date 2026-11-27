# GUI name: "Google News Fetcher". Canonical scheduled-job names live in scheduler_manifest.JOB_GRAPH.
import hashlib
import logging
import time
from dataclasses import dataclass, field, replace
from datetime import datetime, time as dt_time, timedelta, timezone
from itertools import combinations
from typing import Callable, Optional

from db.database import get_mutual_fund_tickers
from utils import ignored_tickers_set, is_synthetic_ticker, is_valid_ticker, publisher_from_url

from . import boilerplate, store, symbols
from .article_text import ArticleText, fetch_article, text_status
from .client import BudgetSpent, GoogleClient, GoogleRefused, NoRoute
from .parse import Card, looks_english_title
from .publishers import DROP, FETCH, SUCCESS, PublisherPolicy

logger = logging.getLogger(__name__)

SCOPE_PORTFOLIO = "portfolio"
SCOPE_WATCHLIST = "watchlist"
SCOPE_INDEXES = "indexes"
SCOPES = (SCOPE_PORTFOLIO, SCOPE_WATCHLIST, SCOPE_INDEXES)

FEED_SOURCE = "google"
MARKET_TICKER = "MARKET"
MARKET_SOURCE_LIST = "market"
INDEX_SOURCE_LIST = "index"
_SCOPE_RANK = {"portfolio": 0, "both": 0, "watchlist": 1, INDEX_SOURCE_LIST: 2}

GENERIC_OVERLAP = 0.8
GENERIC_MIN_LIST = 5
SUMMARY_CHARS = 300
EMPTY_STREAK_ALARM = 3
REQUEST_LOG_RETENTION = timedelta(days=30)
LEAD_RETENTION = timedelta(days=30)
LEADS_PER_PUBLISHER = 50
CRASH_ALARM = 3
CRASH_RETRIES = 3
CRASH_RETENTION = timedelta(days=30)
_PAGE_DATE_UNITS = ("day", "week", "month", "year")


@dataclass
class FetchSettings:
    scopes: tuple = SCOPES
    max_tickers_per_run: int = 12
    min_refetch_minutes: int = 60
    max_requests_per_day: int = 300
    google_delay_seconds: float = 3.0
    fetch_full_text: bool = True
    max_per_ticker: int = 10
    max_age_days: int = 7
    publisher_delay_seconds: float = 1.0


def settings_from_config(block: dict) -> FetchSettings:
    """FetchSettings from the SCHEDULING.GFETCHER block of config.json; a missing key keeps the default."""
    defaults = FetchSettings()
    scopes = tuple(s for s in block.get("SCOPES", defaults.scopes) if s in SCOPES)
    return FetchSettings(
        scopes=scopes,
        max_tickers_per_run=int(block.get("MAX_TICKERS_PER_RUN", defaults.max_tickers_per_run)),
        max_requests_per_day=int(block.get("MAX_REQUESTS_PER_DAY", defaults.max_requests_per_day)),
        google_delay_seconds=float(block.get("GOOGLE_DELAY_SECONDS", defaults.google_delay_seconds)),
        fetch_full_text=bool(block.get("FETCH_FULL_TEXT", defaults.fetch_full_text)),
        max_per_ticker=int(block.get("MAX_PER_TICKER", defaults.max_per_ticker)),
        max_age_days=int(block.get("MAX_AGE_DAYS", defaults.max_age_days)),
    )


@dataclass
class RunResult:
    status: str
    tickers_fetched: int = 0
    google_requests: int = 0
    articles_new: int = 0
    articles_pruned: int = 0
    refused_reason: Optional[str] = None
    cooldown_until: Optional[datetime] = None
    layout_suspects: list = field(default_factory=list)
    routes: list = field(default_factory=list)
    refused_routes: list = field(default_factory=list)
    unreachable_routes: list = field(default_factory=list)
    recovered_routes: list = field(default_factory=list)
    text_statuses: dict = field(default_factory=dict)
    crashed_downloads: int = 0
    articles_held: int = 0


@dataclass
class _Found:
    card: Card
    tickers: list


def _now() -> datetime:
    return datetime.now(timezone.utc)


def scope_tickers(settings: FetchSettings) -> dict:
    """{ticker: source_list} of every ticker in the selected scopes that Google can have a page for."""
    from market_pulse import get_index_tickers
    from news.news_feed_engine import _build_ticker_source_map

    wanted: dict = {}
    if SCOPE_PORTFOLIO in settings.scopes or SCOPE_WATCHLIST in settings.scopes:
        for ticker, source_list in _build_ticker_source_map().items():
            held = source_list in ("portfolio", "both")
            if (held and SCOPE_PORTFOLIO in settings.scopes) or (source_list != "portfolio" and SCOPE_WATCHLIST in settings.scopes):
                wanted[ticker] = source_list
    if SCOPE_INDEXES in settings.scopes:
        for ticker in get_index_tickers():
            if ticker in symbols.INDEX_SYMBOLS:
                wanted.setdefault(ticker, INDEX_SOURCE_LIST)
    ignored = ignored_tickers_set()
    funds = get_mutual_fund_tickers(list(wanted))
    return {t: s for t, s in wanted.items()
            if is_valid_ticker(t) and not is_synthetic_ticker(t) and t not in funds and t.upper() not in ignored}


def select_tickers(settings: FetchSettings, now: datetime) -> list[tuple[str, str]]:
    """(ticker, source_list) pairs due for a fetch: never-fetched first in scope order, then the oldest fetch."""
    usable = scope_tickers(settings)
    last = store.ticker_last_fetched(usable)
    floor = now - timedelta(minutes=settings.min_refetch_minutes)
    due = [(t, s) for t, s in usable.items() if last.get(t) is None or last[t] <= floor]
    due.sort(key=lambda p: (last.get(p[0]) is not None, last.get(p[0]) or now, _SCOPE_RANK.get(p[1], 3), p[0]))
    return due[: settings.max_tickers_per_run]


def find_generic(url_sets: dict) -> set:
    """Tickers showing Google's market-wide fallback list: real per-ticker lists never overlap this much, and one such ticker alone cannot be detected."""
    generic = set()
    for a, b in combinations(url_sets, 2):
        sa, sb = url_sets[a], url_sets[b]
        if len(sa) >= GENERIC_MIN_LIST and len(sb) >= GENERIC_MIN_LIST and len(sa & sb) / len(sa | sb) >= GENERIC_OVERLAP:
            generic |= {a, b}
    return generic


def merge(cards_by_ticker: dict) -> list[_Found]:
    """One entry per article URL with every ticker it was listed under; an article seen only in fallback lists belongs to MARKET."""
    generic = find_generic({t: {c.url for c in cards} for t, cards in cards_by_ticker.items()})
    by_url: dict = {}
    for ticker, cards in cards_by_ticker.items():
        for card in cards:
            found = by_url.setdefault(card.url, _Found(card, []))
            if ticker not in found.tickers:
                found.tickers.append(ticker)
    for found in by_url.values():
        specific = [t for t in found.tickers if t not in generic]
        found.tickers = specific or [MARKET_TICKER]
    return list(by_url.values())


def article_id(ticker: str, url: str) -> str:
    return hashlib.sha256(f"{FEED_SOURCE}|{ticker}|{url}".encode()).hexdigest()


def _published(card: Card, page_date, now: datetime) -> tuple[Optional[datetime], Optional[str]]:
    if page_date and (card.precision is None or card.precision in _PAGE_DATE_UNITS):
        stamped = datetime.combine(page_date, dt_time(12, 0), tzinfo=timezone.utc)
        if stamped <= now:
            return stamped, "day"
    return card.published, card.precision


def _download(card: Card, now: datetime) -> ArticleText:
    """One article's cleaned text, a wall counting as `blocked`; any failure costs this article only."""
    try:
        got = fetch_article(card.url)
        if got.text is None:
            return got
        text = boilerplate.clean(card.url, card.title, got.text, now)
        if not text:
            return replace(got, text=None, status="blocked", page_date=None)
        return replace(got, text=text, status=text_status(text))
    except Exception:
        logger.exception("Google News Fetcher: an article from %s failed.", publisher_from_url(card.url))
        return ArticleText(None, "error", crashed=True)


def _hold_crashed(rows: list, crashed: set, result: RunResult, now: datetime) -> list:
    """The rows to store: in a run with no article text, CRASH_ALARM crashes (or an earlier hold) leave a crashed article for a later run until it has had its retries."""
    if not crashed or result.text_statuses.keys() & SUCCESS:
        return rows
    widespread = len(crashed) >= CRASH_ALARM
    counted = crashed if widespread else store.with_crashes(crashed)
    held = {url for url, count in store.count_crashes(counted, now).items() if count <= CRASH_RETRIES}
    if widespread:
        result.crashed_downloads = len(crashed)
        result.articles_held = len({row["url"] for row in rows} & held)
    return [row for row in rows if row["url"] not in held]


def run_fetch(settings: FetchSettings, *, sleep: Callable[[float], None] = time.sleep) -> RunResult:
    """One run of the Google News Fetcher; stops at Google's first refusal and leaves stored rows as they are on any failure."""
    from news.news_feed_engine import _get_company_name

    now = _now()
    store.prune_request_log(now - REQUEST_LOG_RETENTION)
    store.prune_leads(now - LEAD_RETENTION, LEADS_PER_PUBLISHER)
    store.prune_crashes(now - CRASH_RETENTION)
    client = GoogleClient(settings.google_delay_seconds, settings.max_requests_per_day, sleep=sleep)
    if not client.active:
        refused = client.refusal()
        return RunResult("cooling_down", refused_reason=refused.reason, cooldown_until=refused.until)
    due = select_tickers(settings, now)
    if not due:
        return RunResult("no_tickers")

    result = RunResult("ok", routes=[r.name for r in client.active])
    source_lists = dict(due)
    cards_by_ticker: dict = {}
    cutoff = now - timedelta(days=settings.max_age_days)
    for ticker, source_list in due:
        try:
            resolved = symbols.resolve(ticker, client, now)
        except GoogleRefused as e:
            result.status, result.refused_reason, result.cooldown_until = "refused", e.reason, e.until
            break
        except BudgetSpent:
            result.status = "budget_spent"
            break
        except NoRoute:
            result.status = "unreachable"
            break
        if resolved is None:
            continue
        cards = [c for c in resolved.page.cards
                 if looks_english_title(c.title) and (c.published is None or c.published >= cutoff)][: settings.max_per_ticker]
        result.tickers_fetched += 1
        if store.save_ticker_fetch(ticker, source_list, now, len(resolved.page.cards)) == EMPTY_STREAK_ALARM:
            result.layout_suspects.append(ticker)
        cards_by_ticker[ticker] = cards
    result.google_requests = client.requests_made
    result.refused_routes = client.refused_now
    result.unreachable_routes = sorted(client.dropped)
    result.recovered_routes = client.finish()

    found = merge(cards_by_ticker)
    stored = store.existing_article_ids(article_id(t, f.card.url) for f in found for t in f.tickers)
    fresh = [f for f in found if any(article_id(t, f.card.url) not in stored for t in f.tickers)]
    policy = PublisherPolicy(now)
    rows = []
    crashed: set = set()
    for item in fresh:
        decision = policy.decide(item.card.url)
        if decision == DROP:
            result.text_statuses["dropped_paywall"] = result.text_statuses.get("dropped_paywall", 0) + 1
            continue
        text, status, page_date = None, "skipped", None
        if decision == FETCH and settings.fetch_full_text:
            got = _download(item.card, now)
            policy.record(item.card.url, got.status)
            text, status, page_date = got.text, got.status, got.page_date
            if got.crashed:
                crashed.add(item.card.url)
            sleep(settings.publisher_delay_seconds)
        elif not settings.fetch_full_text:
            status = "not_requested"
        result.text_statuses[status] = result.text_statuses.get(status, 0) + 1
        if status in ("paywall", "non_english"):
            continue
        published, precision = _published(item.card, page_date, now)
        if published is None or published < cutoff:
            continue
        fetched_at = int(now.timestamp())
        for ticker in item.tickers:
            rows.append({
                "article_id": article_id(ticker, item.card.url),
                "ticker": ticker,
                "company_name": None if ticker == MARKET_TICKER else _get_company_name(ticker),
                "source_list": MARKET_SOURCE_LIST if ticker == MARKET_TICKER else source_lists.get(ticker, INDEX_SOURCE_LIST),
                "headline": item.card.title,
                "summary": text[:SUMMARY_CHARS] if text else None,
                "full_text": text,
                "body_fetched": 1 if text else 0,
                "url": item.card.url,
                "publisher": item.card.source or None,
                "published_at": int(published.timestamp()),
                "is_premium": 0,
                "fetched_at": fetched_at,
                "feed_source": FEED_SOURCE,
                "published_precision": precision,
            })
    policy.save()
    rows = _hold_crashed(rows, crashed, result, now)
    result.articles_new = store.insert_articles(rows)
    store.clear_crashes({row["url"] for row in rows})
    result.articles_pruned = store.prune_articles(int(cutoff.timestamp()))
    if result.articles_new:
        store.score_unscored_articles()
    logger.info(
        "Google News Fetcher: %s; %d tickers, %d Google requests over %s, %d new articles, %d pruned.",
        result.status, result.tickers_fetched, result.google_requests, "/".join(result.routes), result.articles_new,
        result.articles_pruned,
    )
    return result


def probe_symbols(settings: FetchSettings, *, sleep: Callable[[float], None] = time.sleep) -> dict:
    """Check the Google symbol of every index in scope and of every ticker without a found mapping, one request each, under the same pacing, budget and cooldown as a run; stores the result and no news."""
    now = _now()
    client = GoogleClient(settings.google_delay_seconds, settings.max_requests_per_day, sleep=sleep)
    if not client.active:
        refused = client.refusal()
        return {"status": "cooling_down", "reason": refused.reason, "checked": 0, "found": [], "not_found": []}
    targets = []
    for ticker in sorted(scope_tickers(settings)):
        stored = store.get_symbol(ticker)
        if ticker in symbols.INDEX_SYMBOLS or stored is None or stored.last_probe_status != "found":
            targets.append(ticker)
    summary = {"status": "ok", "reason": None, "checked": 0, "found": [], "not_found": []}
    for ticker in targets:
        try:
            resolved = symbols.resolve(ticker, client, now, force=True)
        except GoogleRefused as e:
            summary.update(status="refused", reason=e.reason)
            break
        except (BudgetSpent, NoRoute) as e:
            summary.update(status="budget_spent" if isinstance(e, BudgetSpent) else "unreachable")
            break
        summary["checked"] += 1
        row = store.get_symbol(ticker)
        if resolved is not None:
            summary["found"].append(f"{ticker} -> {resolved.symbol}:{resolved.exchange}")
        elif row is not None and row.last_probe_status == "not_found":
            summary["not_found"].append(ticker)
    client.finish()
    return summary
