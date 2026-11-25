from datetime import datetime, timedelta
from typing import Optional

from time_engine import parse_stored_utc
from utils import publisher_from_url

from . import store

ALLOW = "allow"
BLOCK_PAYWALL = "block_paywall"
BLOCK_BOTS = "block_bots"
MANUAL = "manual"
AUTO = "auto"

FETCH = "fetch"
HEADLINE_ONLY = "headline_only"
DROP = "drop"

AUTO_BLOCK_MIN = 2
RETRY_BLOCKED_AFTER = timedelta(days=14)
BLOCK_LIKE = ("blocked", "paywall", "unreachable")
SUCCESS = ("full", "short")

SEED = {
    "barrons.com": (BLOCK_PAYWALL, "HTTP 401 paywall/login"),
    "marketwatch.com": (BLOCK_PAYWALL, "HTTP 401 paywall/login"),
    "reuters.com": (BLOCK_PAYWALL, "HTTP 401 paywall/login"),
    "wsj.com": (BLOCK_PAYWALL, "HTTP 401 paywall; same group as Barron's and MarketWatch"),
    "benzinga.com": (BLOCK_BOTS, "HTTP 403 bot block"),
    "bloomberg.com": (BLOCK_BOTS, "HTTP 403 bot block"),
    "ft.com": (BLOCK_BOTS, "HTTP 403 bot block"),
    "gurufocus.com": (BLOCK_BOTS, "HTTP 403 bot block"),
    "investing.com": (BLOCK_BOTS, "HTTP 403 bot block"),
    "kalkine.ca": (BLOCK_BOTS, "HTTP 403 bot block"),
    "lse.co.uk": (BLOCK_BOTS, "HTTP 403 bot block"),
    "marketscreener.com": (BLOCK_BOTS, "HTTP 403 bot block"),
    "seekingalpha.com": (BLOCK_BOTS, "connection reset on every attempt"),
    "share-talk.com": (BLOCK_BOTS, "HTTP 403 bot block"),
    "theguardian.com": (BLOCK_BOTS, "HTTP 403 bot block"),
    "upi.com": (BLOCK_BOTS, "HTTP 403 bot block"),
    "247wallst.com": (ALLOW, "full text"),
    "ad-hoc-news.de": (ALLOW, "full text"),
    "cnbc.com": (ALLOW, "full text"),
    "finance.yahoo.com": (ALLOW, "full text"),
    "fool.com": (ALLOW, "full text"),
    "tradersunion.com": (ALLOW, "full text"),
    "tradingview.com": (ALLOW, "full text"),
    "zacks.com": (ALLOW, "full text"),
}


def _blank(domain: str, policy: str, source: str, reason: Optional[str], now: datetime) -> dict:
    row = {"domain": domain, "policy": policy, "source": source, "reason": reason, "updated_at": store.stamp(now)}
    row.update({f"{c}_count": 0 for c in store.PUBLISHER_COUNT_COLUMNS})
    return row


class PublisherPolicy:
    """What to do with an article from each domain; loaded once per run and written back with save()."""

    def __init__(self, now: datetime):
        self.now = now
        self.rows = store.load_publishers()
        self._dirty: set = set()
        for domain, (policy, reason) in SEED.items():
            if domain not in self.rows:
                self.rows[domain] = _blank(domain, policy, MANUAL, reason, now)
                self._dirty.add(domain)

    def _match(self, host: str) -> Optional[dict]:
        """A rule for 'investing.com' also covers 'uk.investing.com'; the most specific rule wins."""
        best = None
        for domain, row in self.rows.items():
            if (host == domain or host.endswith("." + domain)) and (best is None or len(domain) > len(best["domain"])):
                best = row
        return best

    def decide(self, url: str) -> str:
        row = self._match(publisher_from_url(url))
        if row is None or row["policy"] == ALLOW:
            return FETCH
        if row["source"] == AUTO and self.now - (parse_stored_utc(row["updated_at"]) or self.now) >= RETRY_BLOCKED_AFTER:
            return FETCH
        return DROP if row["policy"] == BLOCK_PAYWALL else HEADLINE_ONLY

    def record(self, url: str, status: str) -> None:
        if status not in store.PUBLISHER_COUNT_COLUMNS:
            return
        host = publisher_from_url(url)
        if not host:
            return
        row = self.rows.get(host) or self._match(host) or _blank(host, ALLOW, AUTO, None, self.now)
        self.rows[row["domain"]] = row
        row[f"{status}_count"] += 1
        if row["source"] == AUTO:
            successes = sum(row[f"{c}_count"] for c in SUCCESS)
            blocks = sum(row[f"{c}_count"] for c in BLOCK_LIKE)
            if successes:
                row["policy"], row["reason"] = ALLOW, None
            elif blocks >= AUTO_BLOCK_MIN:
                paywall = row["paywall_count"] >= row["blocked_count"] + row["unreachable_count"]
                row["policy"] = BLOCK_PAYWALL if paywall else BLOCK_BOTS
                row["reason"] = f"learned: {blocks} refused fetches and no success"
        row["updated_at"] = store.stamp(self.now)
        self._dirty.add(row["domain"])

    def save(self) -> None:
        store.save_publishers(self.rows[d] for d in self._dirty)
        self._dirty.clear()
