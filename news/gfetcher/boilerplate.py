"""Removes what a publisher puts in front of an article (cookie banner, menu, wall) before the text is stored."""
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional

from utils import publisher_from_url

from . import store

SHARED_MIN_HEADLINES = 3
SHARED_MIN_LINES = 3
SHARED_MIN_CHARS = 200
LEAD_CHARS = 6000


@dataclass(frozen=True)
class Rule:
    """Text of `domains` opening with `first` (None: any) loses all through an `until` line in its first `within`; no `until`: a wall."""
    domains: tuple
    first: Optional[str] = None
    until: Optional[str] = None
    within: int = 20


# Applied in order, each to what the rules before it left.
RULES = (
    Rule(("zacks.com",), first=r"We use cookies to understand how you use our site",
         until=r"If you wish to go to ZacksTrade, click OK"),
    Rule(("zacks.com",), until=r"Read MoreHide Full Article$", within=4),
    Rule(("live.euronext.com",), first=r"-$", until=r"Designed to help students navigate", within=80),
    Rule(("aroundprague.cz",), until=r"Petrol / Diesel"),
    Rule(("insidehousing.co.uk",), first=r"ao link$", until=r"You are viewing \d+ of your \d+ free articles", within=4),
    Rule(("foodbusinessmea.com",), until=r"Skip to content$", within=1),
    Rule(("qatar-tribune.com",), first=r"\+ A$", until=r"A -$", within=3),
    Rule(("securities.io",), until=r"Add Securities\.io to your preferred sources", within=4),
    Rule(("finance.yahoo.com",), first=r"Oops, something went wrong$"),
    Rule(("cnbc.com",), first=r"Skip Navigation$"),
    Rule(("thesun.co.uk", "thetimes.com"), first=r"Verifying your device, please wait"),
    Rule(("moomoo.com", "futunn.com"), first=r"请启用 JavaScript"),
    Rule(("wsbtv.com",), first=r"We.re Sorry!"),
    Rule(("telecompaper.com",), first=r"Complete profile$"),
    Rule(("hubbis.com",), first=r"Privacy Policy$"),
)


def _lines(text: str) -> list[str]:
    return [line.strip() for line in text.split("\n") if line.strip()]


def _drop_lines(text: str, count: int) -> str:
    """`text` without its first `count` non-blank lines."""
    raw = text.split("\n")
    kept = [i for i, line in enumerate(raw) if line.strip()][count:]
    return "\n".join(raw[kept[0]:]).strip() if kept else ""


def _covers(rule: Rule, host: str) -> bool:
    return any(host == d or host.endswith("." + d) for d in rule.domains)


def strip_known(host: str, text: str) -> str:
    """`text` without the preambles RULES knows for `host`; empty when it was a wall."""
    for rule in RULES:
        lines = _lines(text)
        if not lines or not _covers(rule, host) or (rule.first and not re.match(rule.first, lines[0])):
            continue
        if rule.until is None:
            return ""
        for number, line in enumerate(lines[:rule.within], 1):
            if re.match(rule.until, line):
                text = _drop_lines(text, number)
                break
    return text


def _common_lines(a: list, b: list) -> int:
    count = 0
    for left, right in zip(a, b):
        if left != right:
            break
        count += 1
    return count


def strip_shared(text: str, headline: str, peers: Iterable[tuple[str, str]]) -> str:
    """`text` without the leading whole lines it shares with (headline, lead) `peers` under enough other headlines."""
    lines = _lines(text)
    own = headline.strip().casefold()
    shared: dict = {}
    for peer_headline, lead in peers:
        key = peer_headline.strip().casefold()
        if key != own:
            shared[key] = max(shared.get(key, 0), _common_lines(lines, _lines(lead)))
    ranked = sorted(shared.values(), reverse=True)
    if len(ranked) < SHARED_MIN_HEADLINES - 1:
        return text
    count = ranked[SHARED_MIN_HEADLINES - 2]
    if count < SHARED_MIN_LINES or sum(map(len, lines[:count])) < SHARED_MIN_CHARS:
        return text
    return _drop_lines(text, count)


def clean(url: str, headline: str, text: str, now: datetime) -> str:
    """The article text with known and shared preambles removed; empty when no article is left."""
    host = publisher_from_url(url)
    text = strip_known(host, text)
    if not text:
        return ""
    store.save_lead(url, host, headline, text[:LEAD_CHARS], now)
    return strip_shared(text, headline, store.peer_leads(host, url))
