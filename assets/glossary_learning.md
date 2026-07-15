# Glossary Learning

A spaced-repetition study system layered on top of the Glossary, added July 2026. Reachable
only via the 🎓 **Learn the Glossary** button next to the Glossary page header (`/glossary`) —
there is deliberately no separate navbar entry.

## Why

The Glossary (`templates/glossary/_*.html`) holds ~172 curated explanations of the app's
concepts, metrics, and engines, but reading a definition once rarely makes it stick. Glossary
Learning turns every term-box into a study card and schedules reviews using a **Leitner box**
system — a well-established spaced-repetition method — so weak terms resurface more often than
strong ones, without any scores or gamification beyond visible progress.

## Architecture

| Concern | File |
|---|---|
| Card content (git-tracked, one entry per glossary term-box) | `learn_cards_seed.py` |
| DB tables + idempotent seeding | `db_schema.py` (`learn_cards`, `learn_term_state`, `_seed_learn_cards()`) |
| Leitner-box math, session builder, overview | `glossary_learn_engine.py` |
| API endpoints | `api_routes.py` (`/api/learn/overview`, `/api/learn/session`, `/api/learn/answer`) |
| Page route | `page_routes.py` (`GET /glossary/learn`) |
| Study UI | `templates/learn.html` + `static/js/learn.js` |

Card content lives in a git-tracked Python module (not `data/`, which is gitignored) so it
survives deployments and is upserted into `learn_cards` on every `init_db()` run — editing a
card's question/answer/distractors in `learn_cards_seed.py` and restarting the app is enough to
update it. `learn_term_state` (per-term progress) is never touched by re-seeding.

## Leitner-box spec

Five boxes, box 0 = never studied:

| Box after a "good" answer | Next review interval |
|---|---|
| 1 | 1 day |
| 2 | 3 days |
| 3 | 7 days |
| 4 | 14 days |
| 5 | 30 days (repeats) |

- **Grading:** multiple-choice correct → `good`; wrong → `fail`. Flip-card recall: "Knew it" →
  `good`, "Fuzzy" → `hard`, "Didn't know" → `fail`.
- **`good`** advances a box (capped at 5) and increments the correct streak.
- **`hard`** holds the box at its current level (floor of 1) and resets the streak.
- **`fail`** (a lapse) drops the box straight back to 1, increments the lapse counter.
- **Exercise mode:** box ≤ 2 uses multiple-choice (recognition); box ≥ 3 switches to a
  self-graded flip card (active recall) — a term only reaches recall mode after two consecutive
  correct answers, and a lapse demotes it back to multiple-choice.
- **Derived status** (not stored, computed from `box`/`total_reviews`/`lapses`/`last_result`):
  `new`, `learning` (box 1-2), `strong` (box 3-4), `learned` (box 5), `weak` (box ≤ 2 with 2+
  lapses or the most recent answer was wrong).

All timestamps are UTC (`datetime.now(timezone.utc)`), matching the app-wide time convention.

## Course structure

Terms are grouped into 23 levels matching the glossary's own accordion sections
(`learn_cards_seed.LEVELS`), ordered from foundational to advanced: Market Fundamentals →
Candlestick Anatomy → Technical Analysis → Company Valuation → Trading Strategies →
Investor Psychology → AI & Risk Metrics → … → System Methodology. A level unlocks once at least
80% of the previous level's terms have been studied at least once, so the course can't jump
straight to advanced engine terminology before the fundamentals are covered.

## Session composition

`glossary_learn_engine.build_session(size=10, section_id=None)`:
1. Due reviews first (`due_at <= now`), oldest debt first.
2. Remaining slots filled with unstudied terms from the lowest unlocked, incomplete level.
3. Each item includes `mode` (`mcq`/`recall`), the question, and (for `mcq`) four shuffled
   options. Grading happens client-side (single-user app) via `POST /api/learn/answer`.

**Per-section practice:** clicking an unlocked level tile on the dashboard (`static/js/learn.js`,
`learnStartSession(sectionId, size)`) passes `section_id` through to `build_session()`, which
switches to a section-scoped path — due reviews in that section first, then the rest of its
cards — and does not apply the level-unlock filter (the operator explicitly chose that section,
so there's nothing to gate). Locked levels are not clickable in the UI.

**Answer review:** a multiple-choice answer is submitted to `POST /api/learn/answer` the instant
it's picked (so SRS state updates immediately even if the tab is closed), but the UI holds on
the same card — highlighting the correct option and waiting for an explicit "Next" click —
rather than auto-advancing, so a wrong answer doesn't flash past before it can be read. Both
modes then show the card's `explanation` — the source glossary term-box's own prose, reused
verbatim — not just the bare answer string, so a wrong guess (or a "Fuzzy"/"Didn't know" recall)
surfaces the actual material rather than a one-line fact to memorise by rote. Cards for
candlestick patterns also carry `candle_html` (the term-box's rendered `.candle-display` markup,
extracted at seed-build time), which `static/js/learn.js` renders as part of the question itself
so the pattern being asked about is visible while answering, not only afterward.

## `explanation` / `candle_html` fields

Both are populated once, in `learn_cards_seed.py`, by copying the source-of-truth content
straight out of the matching `templates/glossary/_*.html` term-box (its `<p>` paragraphs for
`explanation`; its `<div class="candle-display">…</div>` block for `candle_html`, on the handful
of candlestick cards that have one) — there is no runtime HTML scraping or templating, and no
separate prose is authored for the Learn feature. This keeps the glossary term-box as the single
source of truth: editing a term-box's explanation and re-running the (one-off, not scheduled)
extraction is how `explanation` stays in sync, exactly like `question`/`answer`/`distractors`
are hand-maintained today. `learn_cards.explanation` is `NOT NULL DEFAULT ''`; `candle_html` is
nullable and only set for cards with a matching candle visual.

## Adding a new glossary term — checklist

When a new `<div class="term-box">` is added to any `templates/glossary/_*.html` partial:

1. Add a matching entry to `learn_cards_seed.CARDS` — `term_key` (unique slug), `section_id`
   (an existing `LEVELS` entry, or a new one added to `LEVELS` if it's a new section),
   `term_title` (must exactly match the term-box's `<span class="term-title">` text, including
   punctuation — entity-decoded and whitespace-normalized for comparison), `question`, `answer`,
   exactly 3 `distractors`, and `explanation` (the term-box's own `<p>` prose, copied verbatim —
   see "`explanation` / `candle_html` fields" above). Add `candle_html` too if the term-box has a
   `.candle-display` block.
2. Restart the app — `_seed_learn_cards()` upserts the new card on the next `init_db()` run.
3. `tests/test_glossary_learn_seed.py`'s coverage tests enforce this 1:1 — a term-box with no
   seed card (or a seed card with no matching term-box) fails the suite, so forgetting this step
   is caught by `./run_tests.sh`, not silently shipped.

Removing a term-box works the same way in reverse: delete its `CARDS` entry, restart — the
seeding prune step removes the orphaned `learn_cards` row (and any `learn_term_state` progress
for it) automatically.
