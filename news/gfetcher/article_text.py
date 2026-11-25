import logging
import re
from dataclasses import dataclass
from datetime import date
from typing import Optional

import requests
import trafilatura

from outbound_fetch_helpers import FetchRefused, fetch_body

from .client import HEADERS

logger = logging.getLogger(__name__)

MIN_FULL_CHARS = 500
MAX_REDIRECTS = 2
_LANG_RE = re.compile(r"<html[^>]*?\blang\s*=\s*[\"']?([A-Za-z]{2,3})", re.I)
_PUBLISHER_HEADERS = {k: v for k, v in HEADERS.items() if k != "Cookie"}


@dataclass
class ArticleText:
    text: Optional[str]
    status: str
    http_status: Optional[int] = None
    page_date: Optional[date] = None


def _status_for_http(code: Optional[int]) -> str:
    if code in (401, 402):
        return "paywall"
    if code in (403, 429):
        return "blocked"
    return "error"


def fetch_article(url: str) -> ArticleText:
    """Download one article and extract its body; `status` says what was got (full, short, paywall, blocked, unreachable, no_text, non_english, error)."""
    try:
        html = fetch_body(url, _PUBLISHER_HEADERS, max_redirects=MAX_REDIRECTS)
    except requests.HTTPError as e:
        code = e.response.status_code if e.response is not None else None
        return ArticleText(None, _status_for_http(code), code)
    except (requests.ConnectionError, requests.Timeout):
        return ArticleText(None, "unreachable")
    except (requests.RequestException, FetchRefused, OSError) as e:
        logger.debug("Google News Fetcher: article fetch refused or failed: %s", type(e).__name__)
        return ArticleText(None, "error")
    lang = _LANG_RE.search(html[:5000])
    if lang and lang.group(1).lower() != "en":
        return ArticleText(None, "non_english", 200)
    try:
        doc = trafilatura.bare_extraction(
            html, url=url, with_metadata=True, include_comments=False, include_tables=False, favor_recall=True,
        )
    except Exception as e:
        logger.debug("Google News Fetcher: text extraction failed: %s", type(e).__name__)
        return ArticleText(None, "no_text", 200)
    text = (getattr(doc, "text", None) or "").strip() if doc is not None else ""
    page_date = None
    raw_date = getattr(doc, "date", None) if doc is not None else None
    if raw_date:
        try:
            page_date = date.fromisoformat(str(raw_date)[:10])
        except ValueError:
            page_date = None
    if not text:
        return ArticleText(None, "no_text", 200, page_date)
    return ArticleText(text, "full" if len(text) >= MIN_FULL_CHARS else "short", 200, page_date)
