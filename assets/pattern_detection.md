# Pattern Detection — Technical Documentation

**Project:** Stock Analysis Quantitative Trading Terminal
**Engines:** `pattern_detection_engine.py` (registry + orchestrator), `pattern_geometry_helpers.py` (shared swing-point/pivot math), `head_shoulders_engine.py`, `double_top_bottom_engine.py` (per-family detectors)
**Pages:** `/pattern-detection` (list — `templates/pattern_detection.html`, `static/js/pattern_detection.js`), `/pattern-detection/{ticker}` (per-ticker overlay chart — `templates/pattern_detection_detail.html`, `static/js/pattern_detection_detail.js`)
**API Endpoints:** `GET /api/pattern-detection/results`, `POST /api/pattern-detection/run`, `POST /api/pattern-detection/backfill`, `GET /api/pattern-detection/accuracy`, `GET /api/pattern-detection/chart/{ticker}`
**Last Updated:** 2026-07-20

---

## 1. Overview

Pattern Detection is a unified swing-pattern-detection tool: one results table, one scheduler job, one DB schema — covering every registered pattern family. It started as the Head & Shoulders Pattern Detector (a single-pattern standalone tool) and was generalized when Double Top / Double Bottom was added, on explicit direction that future patterns (Triangles, Wedges, Flag & Pennant, and others) must plug into the same foundation rather than each becoming its own disconnected tool.

The presentation layer went through a second round of generalization once a ticker could realistically carry more than one simultaneous pattern (§7): the results table is ticker-centric (one row per ticker, one badge per active pattern) rather than one row per `(ticker, pattern_family)`, and the modal-opened single-family chart was replaced with a dedicated `/pattern-detection/{ticker}` page overlaying every active pattern for that ticker on one chart.

**The registry is the extension point.** A new pattern family is a single Python module conforming to a three-part contract (§3) and one line added to `pattern_detection_engine.DETECTORS` (§2). Nothing else changes: not the DB schema, not the scheduler job, not the API routes, not the page template, not the chart renderer. If a future pattern's geometry genuinely doesn't fit the `points`/`lines` shape (§4), that is a signal the shared schema itself needs revisiting — not a reason to bolt on a parallel table/page/job for that one pattern.

## 2. Engine Architecture

`pattern_detection_engine.py` owns everything generic:

- **`DETECTORS`** — a `dict[str, module]` registry (`{"head_shoulders": head_shoulders_engine, "double_top_bottom": double_top_bottom_engine}`). Adding a family is adding one entry here.
- **`PatternDetectionEngine`** — the scan orchestrator. `run_scan()` builds the ticker list once (Portfolio/Watchlist per config, same scope for every family), loads each ticker's parquet once, computes RSI/volume-SMA once, then calls every enabled registered detector's `detect(...)` against that same data and collects results. `_save_results()` upserts each result into `pattern_detection_results` (keyed on `ticker, pattern_family`, so families never clobber each other) and logs to `pattern_detection_history`, skipping a duplicate log row when the geometry and phase are unchanged from the previous scan.
- **`fill_pattern_outcomes()`** — resolves 14d/30d directional accuracy for `CONFIRMED` history rows, looking up each registered family's `PATTERN_TYPES` map for the expected breakout direction.
- **`backfill_historical_patterns()`** — one-time historical backtest, walking each ticker's full parquet at ~weekly steps per family and logging `CONFIRMED` candidates, deduping repeated re-detection of the same instance across steps.

`pattern_geometry_helpers.py` holds the swing-point/pivot math shared by every family, so a new detector reuses it rather than reimplementing fractal detection from scratch:

- `find_pivots` / `merge_adjacent_pivots` — rolling-window (Williams fractal-style) local-extremum detection, with adjacent same-type pivots (e.g. a double top standing in for a single head) collapsed into the most extreme one.
- `latest_alternating_run(closes, order, run_length, wanted_first)` — the most recent alternating extrema run of a given length (4 points `[shoulder, armpit, head, armpit]` for Head & Shoulders, 3 points `[peak, trough, peak]` for Double Top).
- `piecewise_r2` — fit quality of the actual close path against a piecewise-linear model threaded through the pattern's structural pivots.
- `volume_confirms` / `rsi_divergence` — the two supporting-signal checks, parameterized on two comparable structural points (shoulders for Head & Shoulders, twin peaks/troughs for Double Top/Bottom) rather than pattern-specific names.

## 3. The Detector Contract

Each registered module exposes:

```python
FAMILY: str  # e.g. "double_top_bottom" — matches the DETECTORS registry key and the DB's pattern_family column
PATTERN_TYPES: dict[str, str]  # pattern_type -> expected breakout direction, "up" or "down"

def detect(ticker: str, df: pd.DataFrame, rsi_series: pd.Series, vol_sma: pd.Series, config: dict) -> dict | None:
    ...  # returns the generic result shape (below), or None if no candidate found
```

`detect()` receives the full ticker DataFrame (`Open`/`High`/`Low`/`Close`/`Volume`, indexed by date) plus the RSI/volume-SMA series the orchestrator already computed once — it should read its own family-scoped config from `config["SCHEDULING"]["PATTERN_DETECTION"][<FAMILY_KEY>]` / `config["NOTIFICATIONS"]["PATTERN_DETECTION_ALERTS"][<FAMILY_KEY>]` and return `None` if disabled or no candidate qualifies.

A module is also expected to expose `phase_label(pattern_type, phase) -> str` (the human-readable label used in alert text and the page table) — see either existing family module for the two-line implementation.

## 4. Generic Result Shape

```python
{
    "pattern_type": str,       # family-specific variant
    "phase": str,              # "FORMING" | "CONFIRMED"
    "points": [                # labeled structural pivots — geometry-agnostic
        {"label": str, "date": "YYYY-MM-DD", "price": float},
        ...
    ],
    "lines": [                 # key line segment(s) — neckline, support/resistance, trendlines...
        {"label": str, "date_from": "YYYY-MM-DD", "price_from": float,
         "date_to": "YYYY-MM-DD", "price_to": float, "dash": bool},
        ...
    ],
    "key_level": float,        # the line value used for phase/severity — not persisted, used in-memory for alerting
    "breakout_date": str | None,
    "breakout_price": float | None,
    "measured_target": float,
    "volume_confirms": bool,
    "rsi_divergence": bool,
    "pattern_r2": float | None,
    "prior_trend_pct": float,
    "close_price": float,
}
```

`points` and `lines` are what make the schema and chart renderer geometry-agnostic: Head & Shoulders returns 5 points and 1 (neckline) line; Double Top/Bottom returns 3 points and 1 (support/resistance) line; a future Triangle/Wedge could return 0 points and 2 (converging trendline) lines; a Flag/Pennant could return 2 points (pole start/end) and 2 (channel boundary) lines. `pattern_detection_detail.js`'s chart renderer (`_pdBuildPatternTraces`) iterates whichever `points`/`lines` a result carries and draws a filled shape through `points` (closed back to the first point) plus the `lines` as key-level segments — it needs no changes to support a new family's shape.

## 5. Database

`pattern_detection_results` (one row per `(ticker, pattern_family)`) and `pattern_detection_history` (append-only, `UNIQUE(ticker, scan_date, pattern_family, pattern_type)`) are the two generic tables — see `assets/db_schema_and_architecture.md` for full column definitions. Head & Shoulders' original `head_shoulders_results`/`head_shoulders_history` tables were folded into these via a one-time `db_schema.migrate_db()` copy step and are no longer written to.

## 6. Adding a New Pattern Family

1. Create `<family>_engine.py` implementing the contract in §3 — reuse `pattern_geometry_helpers.py` for pivot detection, R² fit, and the volume/RSI supporting-signal checks wherever the new pattern's validation logic overlaps with an existing family's.
2. Register it: add one entry to `pattern_detection_engine.DETECTORS`.
3. Add config defaults: a family sub-block under `config.py`'s `DEFAULT_CONFIG["SCHEDULING"]["PATTERN_DETECTION"]` and `["NOTIFICATIONS"]["PATTERN_DETECTION_ALERTS"]`.
4. Add a Settings sub-toggle: extend the "Pattern Detection" card in `templates/settings/_system.html` with the new family's checkboxes/fields, and extend `static/js/settings_shared.js`'s harvesting code and `api_routes_system.py`'s `PatternFamilyScheduleConfig`/`PatternFamilyAlertConfig` Pydantic models with any new field names.
5. Add a display label: one entry in `static/js/pattern_detection.js`'s `PATTERN_FAMILY_LABELS`/`PATTERN_TYPE_LABELS` maps (family filter dropdown + table badges) and `static/js/pattern_detection_detail.js`'s `PD_PATTERN_TYPE_LABELS` map (checkbox/legend labels). A chart color is optional — `PD_PATTERN_COLORS` gives a curated color per pattern_type, but any pattern_type missing from it automatically gets a deterministic fallback color (§7), so this step is cosmetic polish, not a requirement. Add a 2-3 sentence plain-language entry to `PD_PATTERN_EXPLANATIONS` (also in `pattern_detection_detail.js`) — shown at the bottom of the per-ticker page for whichever patterns are present.
6. Document it: a glossary term-box (`templates/glossary/_strategy.html`) and a matching `learn_cards_seed.py` card per pattern type, per AGENTS.md's Glossary rule.

Nothing else needs a new-family update: the Portfolio/Watchlist/Stock Detail "Setups & Tags" badges (§7), the Bullish/Bearish grouping, and the tag color scheme all resolve automatically from `PATTERN_TYPES`/`phase_label()` — the same registry data the scan itself already requires every family to expose.

No DB migration, no new scheduler job, no new API route, and no page/chart-renderer changes are needed for a new family that fits the `points`/`lines` shape — bullish/bearish grouping and chart color both resolve automatically, see §7.

## 7. Presentation Layer

**List page (`/pattern-detection`):** results are grouped client-side by `ticker` (`_pdGroupByTicker` in `static/js/pattern_detection.js`) — one row per ticker, one `.setup-tag` badge per currently-active pattern (reusing the same badge component as the Portfolio/Watchlist "Setups & Tags", §8). Clicking a row navigates to `/pattern-detection/{ticker}` rather than opening a chart inline — per AGENTS.md rule 18, a dedicated page (not a modal) is the right call once the chart itself needs real UI (the checkbox tree below), the same carve-out that already applies to Market Regime and Monte Carlo.

Three independent filters narrow the table, each unambiguously labeled so "all" in one dimension is never confused with "all" in another: **Scope** (Portfolio / Watchlist / All Tickers — defaults to Portfolio; a ticker row is shown if it's in that scope's ticker set, sourced from `GET /api/pattern-detection/results`'s `portfolio_tickers`/`watchlist_tickers` arrays), **Direction** (All Directions / Bullish / Bearish — filters on each result's server-resolved `direction` field), and **Family** (All Families / one per registered `pattern_family`).

**Per-ticker page (`/pattern-detection/{ticker}`):** `GET /api/pattern-detection/chart/{ticker}` returns every currently-active pattern for that ticker (not scoped to one family), each carrying a server-resolved `"direction": "up"|"down"` field looked up from `DETECTORS[pattern_family].PATTERN_TYPES[pattern_type]` — the frontend never hardcodes which pattern types are bullish vs. bearish. `static/js/pattern_detection_detail.js` builds two checkbox groups (Bullish / Bearish) purely from this field:

- A master checkbox per group; checking/unchecking it checks/unchecks (and shows/hides) every pattern checkbox in that group.
- Toggling an individual pattern checkbox re-derives the master's state: checked (all on), unchecked (all off), or `indeterminate` (mixed) — plain DOM, no extra dependency.
- All patterns are enabled by default on page load.

Below the chart, a plain-language explanation (2-3 sentences, `PD_PATTERN_EXPLANATIONS` in `pattern_detection_detail.js`) is shown for each distinct pattern_type currently present on the ticker, so the page doesn't assume the viewer already knows what a Double Top means.

**Overlay chart:** for each *checked* pattern, `_pdBuildPatternTraces` draws a closed, semi-transparent (`fillcolor` ~0.2 alpha) `fill: 'toself'` shape through that pattern's `points` in chronological order (closed back to the first point) — this is what gives Head & Shoulders its recognizable shaded "M" silhouette and Double Top/Bottom its shaded wedge, and lets two overlapping patterns' shaded regions visually blend rather than just stacking outlines. The pattern's `lines` (key level) and point markers/labels draw on top, plus a star marker at `breakout_date`/`breakout_price` if confirmed. Every trace for one pattern shares a Plotly `legendgroup` (keyed on `"{family}:{pattern_type}"`) so the legend shows one entry per pattern, not one per trace.

**Chart shape/fill color assignment** (distinguishing overlapping shapes, not semantic): `PD_PATTERN_COLORS` (in `pattern_detection_detail.js`) gives a curated color per `pattern_type`; any `pattern_type` not in that map gets a deterministic color from `PD_FALLBACK_PALETTE` via a simple string hash, so a brand-new pattern family always renders with a stable, distinct chart color with zero code changes required. This is intentionally a separate, denser palette from the tag color scheme (§8) — the chart needs N visually distinct colors to tell overlapping shapes apart, while tags/badges use a fixed 3-color semantic scheme everywhere else.

## 8. Tags/Badges — Shared Across Portfolio, Watchlist, and Stock Detail

Every currently-active pattern for a ticker also shows up as a badge on the Portfolio and Watchlist tables and on the Stock Detail page header — not just on the Pattern Detection page itself — via `page_helpers.get_pattern_tags_by_ticker(tickers)`, a batch query shared by all three page routes (`page_routes.py`). It resolves each `pattern_detection_results` row to `{label, phase, direction}` using the same `DETECTORS[family].phase_label()`/`.PATTERN_TYPES` registry lookups the rest of this tool uses, then `compute_badge_tags()` (`page_helpers.py`) passes the result through as `pattern_tags`, rendered by the `pattern_tags()` Jinja macro (`templates/partials/_macros.html`) — the single template used by `portfolio.html`, `watchlist.html`, and `stock_detail.html` so the three never drift apart.

**Tag color scheme**, applied identically everywhere a pattern is tagged (these badges, the Pattern Detection list-page badges, and the per-ticker page's checkbox labels): FORMING is always orange (`.pattern-tag-forming`) regardless of direction, since nothing has resolved yet; once CONFIRMED, the tag reflects direction — red (`.pattern-tag-bearish`) or green (`.pattern-tag-bullish`). All three classes use a solid dark-tinted background with light text (mirroring the existing `.quality-grade-*` classes) rather than colored text on a dark background, which read poorly.

Because this batch fetch happens once per page load (not once per ticker), adding it did not change the row-count semantics of the Portfolio/Watchlist queries — those queries no longer join `pattern_detection_results` directly (a ticker can have more than one active family, which would have silently duplicated ticker rows through a plain `LEFT JOIN`); the pattern tags are merged in afterward, keyed by ticker.
