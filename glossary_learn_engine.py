# GUI name: "Glossary Learning"
import json
import random
from datetime import datetime, timedelta, timezone

from database import get_connection
import learn_cards_seed

INTERVALS = {1: 1, 2: 3, 3: 7, 4: 14, 5: 30}
MCQ_MAX_BOX = 2
UNLOCK_THRESHOLD = 0.8


def _fmt(dt: datetime) -> str:
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def apply_grade(state: dict, grade: str, now: datetime) -> dict:
    box = state.get("box", 0)
    correct_streak = state.get("correct_streak", 0)
    lapses = state.get("lapses", 0)
    total_reviews = state.get("total_reviews", 0)

    if grade == "good":
        box = min(box + 1, 5)
        correct_streak += 1
    elif grade == "hard":
        box = max(box, 1)
        correct_streak = 0
    elif grade == "fail":
        box = 1
        correct_streak = 0
        lapses += 1
    else:
        raise ValueError(f"unknown grade: {grade}")

    due_at = now + timedelta(days=INTERVALS[box])
    return {
        "box": box,
        "due_at": _fmt(due_at),
        "correct_streak": correct_streak,
        "lapses": lapses,
        "total_reviews": total_reviews + 1,
        "last_result": grade,
        "last_reviewed_at": _fmt(now),
    }


def mode_for_box(box: int) -> str:
    return "mcq" if box <= MCQ_MAX_BOX else "recall"


def status_for_row(row: dict) -> str:
    box = row["box"] if row else 0
    total_reviews = row["total_reviews"] if row else 0
    lapses = row["lapses"] if row else 0
    last_result = row["last_result"] if row else None

    if not row or total_reviews == 0:
        return "new"
    if box <= MCQ_MAX_BOX and (lapses >= 2 or last_result == "fail"):
        return "weak"
    if box <= 2:
        return "learning"
    if box == 5:
        return "learned"
    return "strong"


def get_answer(term_key: str, grade: str, now: datetime = None) -> dict:
    now = now or datetime.now(timezone.utc)
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM learn_cards WHERE term_key = ?", (term_key,))
        if cursor.fetchone() is None:
            raise ValueError(f"unknown term_key: {term_key}")

        cursor.execute("SELECT * FROM learn_term_state WHERE term_key = ?", (term_key,))
        row = cursor.fetchone()
        current = dict(row) if row else {"box": 0, "correct_streak": 0, "lapses": 0, "total_reviews": 0}

        updated = apply_grade(current, grade, now)
        cursor.execute('''
            INSERT INTO learn_term_state (
                term_key, box, due_at, correct_streak, lapses, total_reviews, last_result, last_reviewed_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(term_key) DO UPDATE SET
                box=excluded.box, due_at=excluded.due_at, correct_streak=excluded.correct_streak,
                lapses=excluded.lapses, total_reviews=excluded.total_reviews,
                last_result=excluded.last_result, last_reviewed_at=excluded.last_reviewed_at
        ''', (
            term_key, updated["box"], updated["due_at"], updated["correct_streak"],
            updated["lapses"], updated["total_reviews"], updated["last_result"], updated["last_reviewed_at"]
        ))
        conn.commit()
        return {
            "term_key": term_key,
            "box": updated["box"],
            "due_at": updated["due_at"],
            "term_status": status_for_row(updated),
        }
    finally:
        if conn:
            conn.close()


def _level_unlocked_map(cursor) -> dict:
    cursor.execute('''
        SELECT c.section_id, COUNT(*) AS total,
               SUM(CASE WHEN s.total_reviews IS NOT NULL AND s.total_reviews > 0 THEN 1 ELSE 0 END) AS studied
        FROM learn_cards c
        LEFT JOIN learn_term_state s ON s.term_key = c.term_key
        GROUP BY c.section_id
    ''')
    stats = {r["section_id"]: dict(r) for r in cursor.fetchall()}

    unlocked = {}
    prior_unlocked = True
    for section_id, _ in learn_cards_seed.LEVELS:
        unlocked[section_id] = prior_unlocked
        row = stats.get(section_id)
        if not prior_unlocked or not row or row["total"] == 0:
            prior_unlocked = False
            continue
        prior_unlocked = (row["studied"] or 0) / row["total"] >= UNLOCK_THRESHOLD
    return unlocked


def overview(now: datetime = None) -> dict:
    now = now or datetime.now(timezone.utc)
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()
        unlocked = _level_unlocked_map(cursor)

        levels = []
        for section_id, title in learn_cards_seed.LEVELS:
            cursor.execute('''
                SELECT c.term_key, s.box, s.total_reviews, s.lapses, s.last_result
                FROM learn_cards c
                LEFT JOIN learn_term_state s ON s.term_key = c.term_key
                WHERE c.section_id = ?
            ''', (section_id,))
            rows = [dict(r) for r in cursor.fetchall()]
            studied = sum(1 for r in rows if (r["total_reviews"] or 0) > 0)
            learned = sum(1 for r in rows if r["box"] == 5)
            levels.append({
                "section_id": section_id,
                "title": title,
                "total": len(rows),
                "studied": studied,
                "learned": learned,
                "unlocked": unlocked.get(section_id, False),
            })

        cursor.execute(
            "SELECT COUNT(*) AS n FROM learn_term_state WHERE due_at IS NOT NULL AND due_at <= ?",
            (_fmt(now),)
        )
        due_count = cursor.fetchone()["n"]

        cursor.execute('''
            SELECT c.term_title FROM learn_cards c
            JOIN learn_term_state s ON s.term_key = c.term_key
            WHERE s.box <= ? AND (s.lapses >= 2 OR s.last_result = 'fail')
        ''', (MCQ_MAX_BOX,))
        weak_terms = [r["term_title"] for r in cursor.fetchall()]

        cursor.execute("SELECT COUNT(*) AS n FROM learn_term_state WHERE box = 5")
        total_learned = cursor.fetchone()["n"]

        return {
            "levels": levels,
            "due_count": due_count,
            "weak_terms": weak_terms,
            "total_learned": total_learned,
        }
    finally:
        if conn:
            conn.close()


def _serialize_session(cards: list) -> list:
    session = []
    for card in cards:
        box = card.get("box") or 0
        mode = mode_for_box(box)
        item = {
            "term_key": card["term_key"],
            "term_title": card["term_title"],
            "mode": mode,
            "question": card["question"],
            "answer": card["answer"],
        }
        if mode == "mcq":
            options = json.loads(card["distractors"]) + [card["answer"]]
            random.shuffle(options)
            item["options"] = options
        session.append(item)
    return session


def build_session(size: int = 10, now: datetime = None, section_id: str = None) -> list:
    now = now or datetime.now(timezone.utc)
    conn = None
    try:
        conn = get_connection()
        cursor = conn.cursor()

        if section_id:
            cursor.execute('''
                SELECT c.*, s.box, s.due_at FROM learn_cards c
                LEFT JOIN learn_term_state s ON s.term_key = c.term_key
                WHERE c.section_id = ?
            ''', (section_id,))
            rows = [dict(r) for r in cursor.fetchall()]
            now_str = _fmt(now)
            due = sorted((r for r in rows if r["due_at"] and r["due_at"] <= now_str), key=lambda r: r["due_at"])
            others = sorted((r for r in rows if not (r["due_at"] and r["due_at"] <= now_str)), key=lambda r: r["term_key"])
            cards = (due + others)[:size]
            return _serialize_session(cards)

        cursor.execute('''
            SELECT c.*, s.box FROM learn_cards c
            JOIN learn_term_state s ON s.term_key = c.term_key
            WHERE s.due_at IS NOT NULL AND s.due_at <= ?
            ORDER BY s.due_at ASC
            LIMIT ?
        ''', (_fmt(now), size))
        cards = [dict(r) for r in cursor.fetchall()]

        remaining = size - len(cards)
        if remaining > 0:
            unlocked = _level_unlocked_map(cursor)
            due_keys = {c["term_key"] for c in cards}
            for lvl_section_id, _ in learn_cards_seed.LEVELS:
                if remaining <= 0:
                    break
                if not unlocked.get(lvl_section_id, False):
                    continue
                cursor.execute('''
                    SELECT c.* FROM learn_cards c
                    LEFT JOIN learn_term_state s ON s.term_key = c.term_key
                    WHERE c.section_id = ? AND (s.total_reviews IS NULL OR s.total_reviews = 0)
                    ORDER BY c.term_key
                ''', (lvl_section_id,))
                for row in cursor.fetchall():
                    if remaining <= 0:
                        break
                    if row["term_key"] in due_keys:
                        continue
                    card = dict(row)
                    card["box"] = 0
                    cards.append(card)
                    due_keys.add(row["term_key"])
                    remaining -= 1

        return _serialize_session(cards)
    finally:
        if conn:
            conn.close()
