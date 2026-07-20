# Pattern Detection — Technical Documentation

**Project:** Stock Analysis Quantitative Trading Terminal
**Engines:** `pattern_detection_engine.py` (registry + orchestrator), `pattern_geometry_helpers.py` (shared swing-point/pivot math), `head_shoulders_engine.py`, `double_top_bottom_engine.py` (per-family detectors)
**Page:** `/pattern-detection` (`templates/pattern_detection.html`, `static/js/pattern_detection.js`)
**API Endpoints:** `GET /api/pattern-detection/results`, `POST /api/pattern-detection/run`, `POST /api/pattern-detection/backfill`, `GET /api/pattern-detection/accuracy`, `GET /api/pattern-detection/chart/{ticker}/{family}`
**Last Updated:** 2026-07-20

---

## 1. Overview

Pattern Detection is a unified swing-pattern-detection tool: one page, one results table, one chart modal, one scheduler job, one DB schema — covering every registered pattern family. It started as the Head & Shoulders Pattern Detector (a single-pattern standalone tool) and was generalized when Double Top / Double Bottom was added, on explicit direction that future patterns (Triangles, Wedges, Flag & Pennant, and others) must plug into the same foundation rather than each becoming its own disconnected tool.

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

`points` and `lines` are what make the schema and chart renderer geometry-agnostic: Head & Shoulders returns 5 points and 1 (neckline) line; Double Top/Bottom returns 3 points and 1 (support/resistance) line; a future Triangle/Wedge could return 0 points and 2 (converging trendline) lines; a Flag/Pennant could return 2 points (pole start/end) and 2 (channel boundary) lines. `pattern_detection.js`'s chart renderer (`_pdRenderChart`) iterates whichever `points`/`lines` a result carries — it needs no changes to support a new family's shape.

## 5. Database

`pattern_detection_results` (one row per `(ticker, pattern_family)`) and `pattern_detection_history` (append-only, `UNIQUE(ticker, scan_date, pattern_family, pattern_type)`) are the two generic tables — see `assets/db_schema_and_architecture.md` for full column definitions. Head & Shoulders' original `head_shoulders_results`/`head_shoulders_history` tables were folded into these via a one-time `db_schema.migrate_db()` copy step and are no longer written to.

## 6. Adding a New Pattern Family

1. Create `<family>_engine.py` implementing the contract in §3 — reuse `pattern_geometry_helpers.py` for pivot detection, R² fit, and the volume/RSI supporting-signal checks wherever the new pattern's validation logic overlaps with an existing family's.
2. Register it: add one entry to `pattern_detection_engine.DETECTORS`.
3. Add config defaults: a family sub-block under `config.py`'s `DEFAULT_CONFIG["SCHEDULING"]["PATTERN_DETECTION"]` and `["NOTIFICATIONS"]["PATTERN_DETECTION_ALERTS"]`.
4. Add a Settings sub-toggle: extend the "Pattern Detection" card in `templates/settings/_system.html` with the new family's checkboxes/fields, and extend `static/js/settings_shared.js`'s harvesting code and `api_routes_system.py`'s `PatternFamilyScheduleConfig`/`PatternFamilyAlertConfig` Pydantic models with any new field names.
5. Add a display label: one entry in `static/js/pattern_detection.js`'s `PATTERN_FAMILY_LABELS`/`PATTERN_TYPE_LABELS` maps — the family filter dropdown, results table, and chart title all derive from these with no other template changes.
6. Document it: a glossary term-box (`templates/glossary/_strategy.html`) and a matching `learn_cards_seed.py` card per pattern type, per AGENTS.md's Glossary rule.

No DB migration, no new scheduler job, no new API route, and no page/chart-renderer changes are needed for a new family that fits the `points`/`lines` shape.
