"""boilerplate: known publisher preambles, walls, openings shared across a publisher's articles, and the stored leads.

Covers each RULES entry on a trimmed sample, the guards that leave other text alone (host, first line, `within`),
the shared-opening thresholds, clean() with the gfetcher_leads table, and prune_leads.
"""
import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from db import connection
from db.database import get_connection
from news.gfetcher import boilerplate, store
from news.gfetcher.boilerplate import RULES, clean, strip_known, strip_shared

from .helpers import TABLES

pytestmark = pytest.mark.restore_tables(*TABLES)

NOW = datetime(2026, 11, 27, 9, 0, tzinfo=timezone.utc)
ARTICLE = "Dividend growth ETFs can reduce tech concentration.\nCombining them with momentum may help."

ZACKS_BANNER = (
    "We use cookies to understand how you use our site and to improve your experience.\n"
    "This includes personalizing content and advertising.\n"
    "You are being directed to ZacksTrade, a division of LBMZ Securities and licensed broker-dealer.\n"
    "If you wish to go to ZacksTrade, click OK. If you do not, click Cancel.\n"
)
ZACKS_TEASER = "Why Dividend Growth ETFs Deserve a Place in Tech-Heavy Portfolios\nRead MoreHide Full Article\n"

# (host, preamble) with an article following the preamble.
PREAMBLES = {
    "zacks-banner": ("zacks.com", ZACKS_BANNER),
    "zacks-teaser": ("zacks.com", ZACKS_TEASER),
    "zacks-banner-and-teaser": ("zacks.com", ZACKS_BANNER + ZACKS_TEASER),
    "euronext": ("live.euronext.com", "-\nMarkets\nathexgroup.grAthens Exchange Group\n" + "-\n" * 30 + "Resources\n"
                 "Designed to help students navigate the complexities of financial marketsEuronext Trading game\n"),
    "aroundprague": ("aroundprague.cz", "U.S.\nPrague\n-1°\nUSD 22,28\nPetrol / Diesel38,69 · 43,63 Kč\n"),
    "insidehousing": ("insidehousing.co.uk", "ao link\nYou are viewing 1 of your 1 free articles\n"),
    "foodbusinessmea": ("foodbusinessmea.com", "Skip to content\n"),
    "qatar-tribune": ("qatar-tribune.com", "+ A\nA -\n"),
    "securities": ("securities.io", "Bonds\nLSEG Finance Sets Tender Price\nAdd Securities.io to your preferred sources on Google\n"),
}

WALLS = {
    "yahoo": ("finance.yahoo.com", "Oops, something went wrong\nSkip to navigation\nSkip to main content"),
    "cnbc": ("cnbc.com", "Skip Navigation\nMarkets\nPre-Markets"),
    "thesun": ("thesun.co.uk", "Verifying your device, please wait\nVerification failed. Please try again.\nTry Again"),
    "thetimes": ("thetimes.com", "Verifying your device, please wait\nTry Again"),
    "moomoo": ("moomoo.com", "请启用 JavaScript"),
    "futunn-subdomain": ("news.futunn.com", "请启用 JavaScript"),
    "wsbtv": ("wsbtv.com", "We’re Sorry!\nThis website is unavailable in your location.\nError 451"),
    "wsbtv-straight-apostrophe": ("wsbtv.com", "We're Sorry!\nError 451"),
    "telecompaper": ("telecompaper.com", "Complete profile\nBefore downloading the whitepaper, complete your profile."),
    "hubbis": ("hubbis.com", "Privacy Policy\nHubbis is committed to safeguarding the privacy of our users."),
}


@pytest.fixture(autouse=True)
def _no_leads():
    conn = get_connection()
    try:
        conn.execute("DELETE FROM gfetcher_leads")
        conn.commit()
    finally:
        conn.close()


def _leads():
    conn = get_connection()
    try:
        return [dict(r) for r in conn.execute("SELECT * FROM gfetcher_leads ORDER BY url")]
    finally:
        conn.close()


@pytest.mark.parametrize("host,preamble", PREAMBLES.values(), ids=PREAMBLES)
def test_a_known_preamble_is_removed(host, preamble):
    assert strip_known(host, preamble + ARTICLE) == ARTICLE


@pytest.mark.parametrize("host,preamble", PREAMBLES.values(), ids=PREAMBLES)
def test_the_same_text_from_another_publisher_is_untouched(host, preamble):
    for other in ("example.com", "not" + host, ""):
        assert strip_known(other, preamble + ARTICLE) == preamble + ARTICLE


@pytest.mark.parametrize("host,page", WALLS.values(), ids=WALLS)
def test_a_wall_leaves_nothing(host, page):
    assert strip_known(host, page) == ""
    assert strip_known("example.com", page) == page


@pytest.mark.parametrize("host,page", WALLS.values(), ids=WALLS)
def test_a_wall_phrase_further_down_is_an_article(host, page):
    text = ARTICLE + "\n" + page
    assert strip_known(host, text) == text


def test_every_rule_has_a_sample_that_it_acts_on():
    samples = [(host, text + ARTICLE) for host, text in PREAMBLES.values()] + list(WALLS.values())
    for rule in RULES:
        assert any(
            boilerplate._covers(rule, host) and strip_known(host, text) != text for host, text in samples
        ), rule.domains
        for domain in rule.domains:
            assert any(host == domain or host.endswith("." + domain) for host, _ in samples), domain


def test_a_rule_covers_subdomains():
    assert strip_known("uk.zacks.com", ZACKS_BANNER + ARTICLE) == ARTICLE


@pytest.mark.parametrize("host", ["live.euronext.com", "insidehousing.co.uk", "qatar-tribune.com"])
def test_an_end_marker_without_the_expected_first_line_is_untouched(host):
    preamble = next(text for h, text in PREAMBLES.values() if h == host)
    text = "A real opening sentence.\n" + preamble.split("\n", 1)[1] + ARTICLE
    assert strip_known(host, text) == text


def test_the_zacks_banner_rule_needs_its_first_line():
    text = "Shares rose.\nIf you wish to go to ZacksTrade, click OK. If you do not, click Cancel.\n" + ARTICLE
    assert strip_known("zacks.com", text) == text


@pytest.mark.parametrize("host,first,marker,within", [
    ("zacks.com", "We use cookies to understand how you use our site", "If you wish to go to ZacksTrade, click OK.", 20),
    ("zacks.com", None, "Read MoreHide Full Article", 4),
    ("live.euronext.com", "-", "Designed to help students navigate markets", 80),
    ("aroundprague.cz", None, "Petrol / Diesel38,69", 20),
    ("insidehousing.co.uk", "ao link", "You are viewing 1 of your 1 free articles", 4),
    ("foodbusinessmea.com", None, "Skip to content", 1),
    ("qatar-tribune.com", "+ A", "A -", 3),
    ("securities.io", None, "Add Securities.io to your preferred sources on Google", 4),
])
def test_an_end_marker_counts_only_within_its_window(host, first, marker, within):
    def page(position):
        before = [first] if first and position > 1 else []
        before += [f"Line {i}" for i in range(len(before), position - 1)]
        return "\n\n".join(before + [marker]) + "\n" + ARTICLE

    assert strip_known(host, page(within)) == ARTICLE
    assert strip_known(host, page(within + 1)) == page(within + 1)


def test_a_preamble_with_nothing_after_it_leaves_nothing():
    assert strip_known("zacks.com", ZACKS_BANNER) == ""


def test_empty_text_stays_empty():
    assert strip_known("zacks.com", "") == ""


BLOCK = [f"Standing publisher notice number {i}, repeated at the top of every page we serve to readers." for i in range(3)]


def _text(lines, tail):
    return "\n".join(list(lines) + [tail])


def _peers(lines, *headlines):
    return [(h, _text(lines, f"Body of {h}.")) for h in headlines]


def test_a_block_under_three_headlines_is_removed():
    assert strip_shared(_text(BLOCK, "Own body."), "Own", _peers(BLOCK, "Peer one", "Peer two")) == "Own body."


def test_two_headlines_are_not_enough():
    text = _text(BLOCK, "Own body.")
    assert strip_shared(text, "Own", _peers(BLOCK, "Peer one")) == text
    assert strip_shared(text, "Own", []) == text


@pytest.mark.parametrize("headlines", [("Peer", "Peer"), ("Peer", "  PEER "), ("Peer", "Own"), ("own ", "OWN")])
def test_a_repeated_headline_is_counted_once_and_the_own_headline_not_at_all(headlines):
    text = _text(BLOCK, "Own body.")
    assert strip_shared(text, "Own", _peers(BLOCK, *headlines)) == text


def test_a_repeated_headline_counts_with_its_longest_match():
    peers = [("Peer one", "Something else"), ("Peer one", _text(BLOCK, "b")), ("Peer two", _text(BLOCK, "c"))]
    assert strip_shared(_text(BLOCK, "Own body."), "Own", peers) == "Own body."


def test_two_lines_are_not_a_block_however_long():
    lines = ["x" * 300, "y" * 300]
    text = _text(lines, "Own body.")
    assert strip_shared(text, "Own", _peers(lines, "A", "B")) == text


@pytest.mark.parametrize("total,stripped", [(199, False), (200, True)])
def test_a_block_needs_two_hundred_characters(total, stripped):
    lines = ["a" * 60, "b" * 60, "c" * (total - 120)]
    text = _text(lines, "Own body.")
    assert strip_shared(text, "Own", _peers(lines, "A", "B")) == ("Own body." if stripped else text)


def test_a_text_that_is_only_the_shared_block_leaves_nothing():
    assert strip_shared("\n".join(BLOCK), "Own", _peers(BLOCK, "A", "B")) == ""


def test_only_the_common_opening_is_removed():
    own = "\n".join(BLOCK + ["Own body.", BLOCK[0], "Same closing line."])
    peers = [(h, "\n".join(BLOCK + [f"Body of {h}.", BLOCK[0], "Same closing line."])) for h in ("A", "B")]
    assert strip_shared(own, "Own", peers) == "\n".join(["Own body.", BLOCK[0], "Same closing line."])


def test_a_shared_block_lower_down_is_kept():
    own = "\n".join(["Own opening."] + BLOCK)
    peers = [(h, "\n".join([f"Opening of {h}."] + BLOCK)) for h in ("A", "B")]
    assert strip_shared(own, "Own", peers) == own


def test_the_removed_length_is_what_two_other_headlines_share():
    extra = BLOCK + ["A fourth shared line.", "A fifth shared line."]
    own = _text(extra, "Own body.")
    peers = [("A", _text(extra, "a")), ("B", _text(extra[:4], "b")), ("C", "Unrelated")]
    assert strip_shared(own, "Own", peers) == "A fifth shared line.\nOwn body."


def test_blank_lines_and_padding_do_not_matter():
    own = "\n\n  " + "\n\n".join(BLOCK) + "  \n\n   \nOwn first.\n\nOwn second."
    assert strip_shared(own, "Own", _peers(BLOCK, "A", "B")) == "Own first.\n\nOwn second."


def test_clean_stores_the_opening_without_the_known_preamble():
    assert clean("https://www.zacks.com/a", "Headline A", ZACKS_BANNER + ARTICLE, NOW) == ARTICLE
    assert _leads() == [{
        "url": "https://www.zacks.com/a", "domain": "zacks.com", "headline": "Headline A", "lead": ARTICLE,
        "fetched_at": store.stamp(NOW),
    }]


def test_clean_stores_nothing_for_a_wall():
    host, page = WALLS["cnbc"]
    assert clean(f"https://{host}/a", "H", page, NOW) == ""
    assert _leads() == []


def test_clean_caps_the_stored_opening_but_not_the_text():
    text = "z" * (boilerplate.LEAD_CHARS + 500)
    assert clean("https://x.test/a", "H", text, NOW) == text
    assert len(_leads()[0]["lead"]) == boilerplate.LEAD_CHARS


def test_clean_removes_a_block_from_the_third_headline_on():
    got = [clean(f"https://x.test/{h}", h, _text(BLOCK, f"Body {h}."), NOW) for h in ("a", "b", "c", "d")]
    assert got == [_text(BLOCK, "Body a."), _text(BLOCK, "Body b."), "Body c.", "Body d."]
    assert all(row["lead"].startswith(BLOCK[0]) for row in _leads())


def test_clean_compares_only_with_the_same_publisher():
    clean("https://one.test/a", "A", _text(BLOCK, "a"), NOW)
    clean("https://two.test/b", "B", _text(BLOCK, "b"), NOW)
    assert clean("https://one.test/c", "C", _text(BLOCK, "c"), NOW) == _text(BLOCK, "c")


def test_clean_never_compares_an_article_with_its_own_stored_lead():
    clean("https://x.test/a", "A", _text(BLOCK, "a"), NOW)
    for headline in ("B", "C"):
        assert clean("https://x.test/same", headline, _text(BLOCK, "s"), NOW) == _text(BLOCK, "s")
    assert [(r["url"], r["headline"]) for r in _leads()] == [("https://x.test/a", "A"), ("https://x.test/same", "C")]


def test_clean_applies_the_known_rules_before_the_shared_comparison():
    for name in ("a", "b"):
        clean(f"https://zacks.com/{name}", name, ZACKS_BANNER + _text(BLOCK, name), NOW)
    assert clean("https://zacks.com/c", "c", ZACKS_TEASER + _text(BLOCK, "Body c."), NOW) == "Body c."


def test_a_locked_lead_write_is_deferred_and_the_text_is_kept(monkeypatch):
    monkeypatch.setattr(connection, "_start_deferred_worker", lambda: None)
    monkeypatch.setattr(connection, "_deferred", [])
    attempts = []

    def locked_once():
        attempts.append(1)
        if len(attempts) == 1:
            raise sqlite3.OperationalError("database is locked")
        return get_connection()
    monkeypatch.setattr(store, "get_connection", locked_once)

    assert clean("https://x.test/a", "A", ARTICLE, NOW) == ARTICLE
    assert _leads() == [] and len(connection._deferred) == 1
    connection._run_due_deferred(now=float("inf"))
    assert [r["headline"] for r in _leads()] == ["A"] and connection._deferred == []


def test_an_older_opening_never_replaces_a_newer_one():
    store.save_lead("https://x.test/a", "x.test", "newer", "lead", NOW)
    store.save_lead("https://x.test/a", "x.test", "older", "lead", NOW - timedelta(hours=1))
    assert [r["headline"] for r in _leads()] == ["newer"]


def _seed(domain, name, age):
    store.save_lead(f"https://{domain}/{name}", domain, name, "lead", NOW - age)


def test_prune_leads_drops_old_openings():
    _seed("x.test", "old", timedelta(days=30, seconds=1))
    _seed("x.test", "edge", timedelta(days=30))
    _seed("x.test", "new", timedelta(days=1))
    assert store.prune_leads(NOW - timedelta(days=30), 50) == 1
    assert [r["headline"] for r in _leads()] == ["edge", "new"]


def test_prune_leads_keeps_the_newest_of_each_publisher():
    for hours in range(4):
        _seed("one.test", f"h{hours}", timedelta(hours=hours))
    _seed("two.test", "only", timedelta(hours=9))
    assert store.prune_leads(NOW - timedelta(days=30), 2) == 2
    assert [(r["domain"], r["headline"]) for r in _leads()] == [("one.test", "h0"), ("one.test", "h1"), ("two.test", "only")]


def test_prune_leads_applies_both_limits_at_once():
    _seed("one.test", "fresh", timedelta(hours=1))
    _seed("one.test", "second", timedelta(hours=2))
    _seed("one.test", "third", timedelta(hours=3))
    _seed("two.test", "stale", timedelta(days=40))
    assert store.prune_leads(NOW - timedelta(days=30), 2) == 2
    assert [r["headline"] for r in _leads()] == ["fresh", "second"]
