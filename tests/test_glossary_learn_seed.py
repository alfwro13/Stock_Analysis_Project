"""tests/test_glossary_learn_seed.py -- integrity of learn_cards_seed.py and its
1:1 coverage against every glossary term-box (templates/glossary/_*.html)."""

import html
import re
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import learn_cards_seed

GLOSSARY_DIR = Path(__file__).parent.parent / "templates" / "glossary"

TERM_TITLE_RE = re.compile(r'<span class="term-title">(.*?)</span>', re.DOTALL)


def _normalize(text: str) -> str:
    return " ".join(html.unescape(text).split())


def _glossary_term_titles() -> set:
    titles = set()
    for path in GLOSSARY_DIR.glob("_*.html"):
        content = path.read_text(encoding="utf-8")
        for match in TERM_TITLE_RE.findall(content):
            titles.add(_normalize(match))
    return titles


def test_term_keys_are_unique():
    keys = [c["term_key"] for c in learn_cards_seed.CARDS]
    assert len(keys) == len(set(keys))


def test_term_titles_are_unique():
    titles = [c["term_title"] for c in learn_cards_seed.CARDS]
    assert len(titles) == len(set(titles))


def test_every_card_has_exactly_3_distinct_nonempty_distractors():
    for card in learn_cards_seed.CARDS:
        distractors = card["distractors"]
        assert len(distractors) == 3, card["term_key"]
        assert len(set(distractors)) == 3, card["term_key"]
        assert all(d.strip() for d in distractors), card["term_key"]


def test_answer_not_among_distractors():
    for card in learn_cards_seed.CARDS:
        assert card["answer"] not in card["distractors"], card["term_key"]


def test_every_level_section_is_nonempty():
    section_ids = {c["section_id"] for c in learn_cards_seed.CARDS}
    for section_id, _ in learn_cards_seed.LEVELS:
        assert section_id in section_ids, f"level '{section_id}' has no cards"


def test_every_card_section_id_is_a_declared_level():
    level_ids = {section_id for section_id, _ in learn_cards_seed.LEVELS}
    for card in learn_cards_seed.CARDS:
        assert card["section_id"] in level_ids, card["term_key"]


def test_glossary_coverage_every_term_box_has_a_seed_card():
    glossary_titles = _glossary_term_titles()
    seed_titles = {_normalize(c["term_title"]) for c in learn_cards_seed.CARDS}
    missing = glossary_titles - seed_titles
    assert not missing, f"glossary term-boxes with no seed card: {missing}"


def test_glossary_coverage_no_orphan_seed_cards():
    glossary_titles = _glossary_term_titles()
    seed_titles = {_normalize(c["term_title"]) for c in learn_cards_seed.CARDS}
    orphans = seed_titles - glossary_titles
    assert not orphans, f"seed cards with no matching glossary term-box: {orphans}"


def test_every_card_has_a_nonempty_explanation():
    for card in learn_cards_seed.CARDS:
        assert card.get("explanation", "").strip(), card["term_key"]


def test_candle_html_cards_contain_candle_display_markup():
    for card in learn_cards_seed.CARDS:
        candle_html = card.get("candle_html")
        if candle_html is not None:
            assert "candle-display" in candle_html, card["term_key"]
