"""tests/test_glossary_learn_engine.py -- Leitner-box SRS math and session building."""

import sys
from pathlib import Path
from datetime import datetime, timezone

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

import glossary_learn_engine as engine
import database as _db
import db_schema


NOW = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)


def _blank_state():
    return {"box": 0, "correct_streak": 0, "lapses": 0, "total_reviews": 0}


@pytest.mark.db
def test_apply_grade_good_advances_box_and_streak():
    result = engine.apply_grade(_blank_state(), "good", NOW)
    assert result["box"] == 1
    assert result["correct_streak"] == 1
    assert result["due_at"] == "2026-01-02 12:00:00"


@pytest.mark.db
def test_apply_grade_good_caps_at_box_5():
    state = {"box": 5, "correct_streak": 3, "lapses": 0, "total_reviews": 10}
    result = engine.apply_grade(state, "good", NOW)
    assert result["box"] == 5
    assert result["due_at"] == "2026-01-31 12:00:00"


@pytest.mark.db
def test_apply_grade_fail_resets_to_box_1_and_increments_lapses():
    state = {"box": 4, "correct_streak": 3, "lapses": 1, "total_reviews": 8}
    result = engine.apply_grade(state, "fail", NOW)
    assert result["box"] == 1
    assert result["correct_streak"] == 0
    assert result["lapses"] == 2
    assert result["due_at"] == "2026-01-02 12:00:00"


@pytest.mark.db
def test_apply_grade_hard_holds_box_floor_1_and_resets_streak():
    result = engine.apply_grade({"box": 0, "correct_streak": 0, "lapses": 0, "total_reviews": 0}, "hard", NOW)
    assert result["box"] == 1
    state2 = {"box": 3, "correct_streak": 2, "lapses": 0, "total_reviews": 5}
    result2 = engine.apply_grade(state2, "hard", NOW)
    assert result2["box"] == 3
    assert result2["correct_streak"] == 0


@pytest.mark.db
def test_apply_grade_total_reviews_always_increments():
    result = engine.apply_grade(_blank_state(), "good", NOW)
    assert result["total_reviews"] == 1


@pytest.mark.db
def test_apply_grade_rejects_unknown_grade():
    with pytest.raises(ValueError):
        engine.apply_grade(_blank_state(), "meh", NOW)


@pytest.mark.parametrize("box,expected_mode", [(0, "mcq"), (1, "mcq"), (2, "mcq"), (3, "recall"), (5, "recall")])
def test_mode_for_box_boundary(box, expected_mode):
    assert engine.mode_for_box(box) == expected_mode


@pytest.mark.db
def test_get_answer_persists_state_and_advances_box():
    conn = None
    try:
        conn = _db.get_connection()
        term_key = "stocks-and-shares"
        result = engine.get_answer(term_key, "good", now=NOW)
        assert result["box"] == 1
        assert result["term_status"] == "learning"

        row = conn.execute("SELECT box FROM learn_term_state WHERE term_key = ?", (term_key,)).fetchone()
        assert row["box"] == 1
    finally:
        if conn:
            conn.execute("DELETE FROM learn_term_state WHERE term_key = ?", (term_key,))
            conn.commit()
            conn.close()


@pytest.mark.db
def test_get_answer_unknown_term_key_raises():
    with pytest.raises(ValueError):
        engine.get_answer("not-a-real-term", "good", now=NOW)


@pytest.mark.db
def test_build_session_returns_new_cards_from_first_level():
    conn = None
    try:
        conn = _db.get_connection()
        conn.execute("DELETE FROM learn_term_state")
        conn.commit()

        session = engine.build_session(size=5, now=NOW)
        assert len(session) == 5
        for card in session:
            assert card["mode"] == "mcq"
            assert "options" in card
            assert card["answer"] in card["options"]
            assert card["explanation"].strip()
    finally:
        if conn:
            conn.close()


@pytest.mark.db
def test_build_session_prioritises_due_reviews():
    conn = None
    try:
        conn = _db.get_connection()
        conn.execute("DELETE FROM learn_term_state")
        conn.execute(
            "INSERT INTO learn_term_state (term_key, box, due_at, total_reviews) VALUES (?, 3, ?, 1)",
            ("stocks-and-shares", "2025-12-31 00:00:00")
        )
        conn.commit()

        session = engine.build_session(size=1, now=NOW)
        assert session[0]["term_key"] == "stocks-and-shares"
        assert session[0]["mode"] == "recall"
    finally:
        if conn:
            conn.execute("DELETE FROM learn_term_state WHERE term_key = 'stocks-and-shares'")
            conn.commit()
            conn.close()


@pytest.mark.db
def test_level_2_locked_until_80pct_of_level_1_studied():
    conn = None
    try:
        conn = _db.get_connection()
        conn.execute("DELETE FROM learn_term_state")
        conn.commit()

        result = engine.overview(now=NOW)
        levels_by_id = {lvl["section_id"]: lvl for lvl in result["levels"]}
        assert levels_by_id["market-fundamentals"]["unlocked"] is True
        assert levels_by_id["candlesticks"]["unlocked"] is False
    finally:
        if conn:
            conn.close()


@pytest.mark.db
def test_build_session_include_locked_pulls_from_locked_levels():
    conn = None
    try:
        conn = _db.get_connection()
        conn.execute("DELETE FROM learn_term_state")
        conn.commit()

        overview_result = engine.overview(now=NOW)
        levels_by_id = {lvl["section_id"]: lvl for lvl in overview_result["levels"]}
        assert levels_by_id["candlesticks"]["unlocked"] is False
        level1_total = levels_by_id["market-fundamentals"]["total"]

        gated_session = engine.build_session(size=level1_total + 5, now=NOW)
        assert len(gated_session) == level1_total

        unlocked_session = engine.build_session(size=level1_total + 5, now=NOW, include_locked=True)
        assert len(unlocked_session) == level1_total + 5
    finally:
        if conn:
            conn.close()


@pytest.mark.db
def test_build_session_with_section_id_returns_only_that_sections_cards():
    conn = None
    try:
        conn = _db.get_connection()
        conn.execute("DELETE FROM learn_term_state")
        conn.commit()

        import learn_cards_seed
        expected_total = sum(1 for c in learn_cards_seed.CARDS if c["section_id"] == "candlesticks")

        session = engine.build_session(size=30, now=NOW, section_id="candlesticks")
        assert len(session) == expected_total
    finally:
        if conn:
            conn.close()


@pytest.mark.db
def test_build_session_with_section_id_bypasses_level_lock():
    """A locked (not-yet-unlocked) section must still be sessionable when explicitly requested."""
    conn = None
    try:
        conn = _db.get_connection()
        conn.execute("DELETE FROM learn_term_state")
        conn.commit()

        overview_result = engine.overview(now=NOW)
        levels_by_id = {lvl["section_id"]: lvl for lvl in overview_result["levels"]}
        assert levels_by_id["candlesticks"]["unlocked"] is False

        session = engine.build_session(size=5, now=NOW, section_id="candlesticks")
        assert len(session) > 0
    finally:
        if conn:
            conn.close()


@pytest.mark.db
def test_build_session_with_section_id_prioritises_due_before_new():
    conn = None
    try:
        conn = _db.get_connection()
        conn.execute("DELETE FROM learn_term_state")
        conn.execute(
            "INSERT INTO learn_term_state (term_key, box, due_at, total_reviews) VALUES (?, 3, ?, 1)",
            ("hammer-bullish-rejection", "2025-12-31 00:00:00")
        )
        conn.commit()

        session = engine.build_session(size=1, now=NOW, section_id="candlesticks")
        assert session[0]["term_key"] == "hammer-bullish-rejection"
        assert session[0]["mode"] == "recall"
        assert "candle-display" in session[0]["candle_html"]
    finally:
        if conn:
            conn.execute("DELETE FROM learn_term_state WHERE term_key = 'hammer-bullish-rejection'")
            conn.commit()
            conn.close()
