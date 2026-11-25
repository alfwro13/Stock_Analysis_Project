import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

import lxml.html
from lxml.etree import ParserError

# Google obfuscates these class names and can change them without notice: a layout change is fixed here.
ITEM_SELECTOR = "div.LIT7If"
SOURCE_SELECTOR = "div.WrUjhf"
AGE_SELECTOR = "div.JQ8Czd"
TITLE_SELECTOR = "div.TQWIEd"

NOT_FOUND_TITLE = "Google Finance"

_AGE_RE = re.compile(r"(\d+)\s+(minute|hour|day|week|month|year)s?\s+ago", re.I)
_UNIT_DAYS = {"minute": 1 / 1440, "hour": 1 / 24, "day": 1, "week": 7, "month": 30, "year": 365}
_NON_LATIN_SHARE = 0.3


@dataclass
class Card:
    title: str
    url: str
    source: str
    age_text: str
    published: Optional[datetime]
    precision: Optional[str]


@dataclass
class QuotePage:
    found: bool
    title: str
    cards: list = field(default_factory=list)


def parse_age(text: str, now: datetime) -> tuple[Optional[datetime], Optional[str]]:
    """Google shows only relative ages, so the time is an estimate good to the unit given."""
    m = _AGE_RE.search(text or "")
    if not m:
        return None, None
    unit = m.group(2).lower()
    return now - timedelta(days=int(m.group(1)) * _UNIT_DAYS[unit]), unit


def _text(element) -> str:
    return " ".join(element.text_content().split()) if element is not None else ""


def _first(element, selector):
    found = element.cssselect(selector)
    return found[0] if found else None


def parse_quote_page(html: str, now: datetime) -> QuotePage:
    if not html or not html.strip():
        return QuotePage(False, "")
    try:
        doc = lxml.html.fromstring(html)
    except ParserError:
        return QuotePage(False, "")
    title = " ".join((doc.findtext(".//title") or "").split())
    # An unknown symbol or exchange still answers 200, with the bare site title.
    if not title or title == NOT_FOUND_TITLE:
        return QuotePage(False, title)
    cards, seen = [], set()
    for item in doc.cssselect(ITEM_SELECTOR):
        link = _first(item, "a[href]")
        headline = _text(_first(item, TITLE_SELECTOR))
        if link is None or not headline:
            continue
        url = link.get("href", "")
        if not url.startswith("https://") or url in seen:
            continue
        seen.add(url)
        age_text = _text(_first(item, AGE_SELECTOR))
        published, precision = parse_age(age_text, now)
        cards.append(Card(headline, url, _text(_first(item, SOURCE_SELECTOR)), age_text, published, precision))
    return QuotePage(True, title, cards)


def looks_english_title(title: str) -> bool:
    letters = [c for c in title if c.isalpha()]
    if not letters:
        return True
    return sum(1 for c in letters if ord(c) > 0x24F) / len(letters) < _NON_LATIN_SHARE
