# Pattern Detection — Technical Documentation

**Project:** Stock Analysis Quantitative Trading Terminal
**Engines:** `pattern_detection_engine.py` (registry + orchestrator), `pattern_geometry_helpers.py` (shared swing-point/pivot math), `head_shoulders_engine.py`, `double_top_bottom_engine.py`, `flag_engine.py`, `triangle_engine.py`, `volatility_squeeze_engine.py`, `narrow_range_engine.py`, `parabolic_stretch_engine.py`, `momentum_divergence_engine.py` (per-family detectors)
**Pages:** `/pattern-detection` (list — `templates/pattern_detection.html`, `static/js/pattern_detection.js`), `/pattern-detection/{ticker}` (per-ticker overlay chart — `templates/pattern_detection_detail.html`, `static/js/pattern_detection_detail.js`)
**API Endpoints:** `GET /api/pattern-detection/results`, `POST /api/pattern-detection/run`, `POST /api/pattern-detection/backfill`, `GET /api/pattern-detection/accuracy`, `GET /api/pattern-detection/chart/{ticker}`
**Last Updated:** 2026-07-22

---

## 1. Overview

Pattern Detection is a unified swing-pattern-detection tool: one results table, one scheduler job, one DB schema — covering every registered pattern family. It started as the Head & Shoulders Pattern Detector (a single-pattern standalone tool) and was generalized when Double Top / Double Bottom was added, on explicit direction that future patterns must plug into the same foundation rather than each becoming its own disconnected tool. Two continuation-pattern families — Bull/Bear Flag and Ascending/Descending Triangle — were added on the same foundation, extending the registry beyond the original alternating-pivot (Williams fractal) detection style to a regression-based one (see §2's `linreg`/`slope_pct_per_day` helpers and §7). Two quantitative volatility/breakout families — Volatility Squeeze and NR4/NR7 Narrow Range — extended the registry a third way: neither is a swing-pivot or regression shape at all, so both introduced a genuinely new "direction-unknown-until-breakout" phase model (see §7b) and Volatility Squeeze's band-contour geometry required the one deliberate exception to "no chart-renderer changes" this tool has needed so far (see §4b). Two mean-reversion/exhaustion families — Parabolic Stretch and Bullish/Bearish Divergence — were added next (see §7c): both are directional immediately (unlike Volatility Squeeze/Narrow Range), so they follow the original FORMING/CONFIRMED model, but Parabolic Stretch is the first family whose lookback requirement (a 200-day SMA plus a 252-day rolling Z-score window) exceeds the orchestrator's historical load window, which was widened accordingly (see §2).

The presentation layer went through a second round of generalization once a ticker could realistically carry more than one simultaneous pattern (§8): the results table is ticker-centric (one row per ticker, one badge per active pattern) rather than one row per `(ticker, pattern_family)`, and the modal-opened single-family chart was replaced with a dedicated `/pattern-detection/{ticker}` page overlaying every active pattern for that ticker on one chart.

**The registry is the extension point.** A new pattern family is a single Python module conforming to a three-part contract (§3) and one line added to `pattern_detection_engine.DETECTORS` (§2). Nothing else changes: not the DB schema, not the scheduler job, not the API routes, not the page template, not the chart renderer. If a future pattern's geometry genuinely doesn't fit the `points`/`lines` shape (§4), that is a signal the shared schema itself needs revisiting — not a reason to bolt on a parallel table/page/job for that one pattern.

## 2. Engine Architecture

`pattern_detection_engine.py` owns everything generic:

- **`DETECTORS`** — a `dict[str, module]` registry (`{"head_shoulders": head_shoulders_engine, "double_top_bottom": double_top_bottom_engine, "flag": flag_engine, "triangle": triangle_engine, "volatility_squeeze": volatility_squeeze_engine, "narrow_range": narrow_range_engine, "parabolic_stretch": parabolic_stretch_engine, "momentum_divergence": momentum_divergence_engine}`). Adding a family is adding one entry here.
- **`PatternDetectionEngine`** — the scan orchestrator. `run_scan()` builds the ticker list once (Portfolio/Watchlist per config, same scope for every family), loads each ticker's parquet once (`_LOOKBACK_BARS` = 500 trading days — widened from 180 once Parabolic Stretch's 200-day SMA + 252-day Z-score window needed most of the 2-year parquet history; every other family only ever searches the most recent bars regardless of how much earlier history is available, so the wider window is a no-op for them), computes RSI/volume-SMA once, then calls every enabled registered detector's `detect(...)` against that same data and collects results. `_save_results()` upserts each result into `pattern_detection_results` (keyed on `ticker, pattern_family`, so families never clobber each other) and logs to `pattern_detection_history`, skipping a duplicate log row when the geometry and phase are unchanged from the previous scan.
- **`fill_pattern_outcomes()`** — resolves 14d/30d directional accuracy for `CONFIRMED` history rows, looking up each registered family's `PATTERN_TYPES` map for the expected breakout direction.
- **`backfill_historical_patterns()`** — one-time historical backtest, walking each ticker's full parquet at ~weekly steps per family and logging `CONFIRMED` candidates, deduping repeated re-detection of the same instance across steps.

`pattern_geometry_helpers.py` holds the swing-point/pivot math shared by every family, so a new detector reuses it rather than reimplementing fractal detection from scratch:

- `find_pivots` / `merge_adjacent_pivots` — rolling-window (Williams fractal-style) local-extremum detection, with adjacent same-type pivots (e.g. a double top standing in for a single head) collapsed into the most extreme one.
- `latest_alternating_run(closes, order, run_length, wanted_first)` — the most recent alternating extrema run of a given length (4 points `[shoulder, armpit, head, armpit]` for Head & Shoulders, 3 points `[peak, trough, peak]` for Double Top).
- `piecewise_r2` — fit quality of the actual close path against a piecewise-linear model threaded through the pattern's structural pivots.
- `volume_confirms` / `rsi_divergence` — the two supporting-signal checks, parameterized on two comparable structural points (shoulders for Head & Shoulders, twin peaks/troughs for Double Top/Bottom) rather than pattern-specific names.
- `linreg(x, y)` — plain OLS slope/intercept/R² of `y ~ x`, added for Flag/Triangle: neither pattern is a fixed-length alternating-pivot shape, so instead of `latest_alternating_run`'s fixed-length run they fit a straight line through whichever swing highs/lows `find_pivots` returns within their window (Flag's two channel lines, Triangle's flat and sloped sides).
- `slope_pct_per_day(slope, reference_price)` — normalizes a raw price/day regression slope to %-of-price-per-day, so a flatness or steepness threshold (e.g. "is this line basically flat?") means the same thing regardless of a ticker's absolute price level. Used by both `flag_engine.py` and `triangle_engine.py` for every slope comparison against a configured threshold.

## 3. The Detector Contract

Each registered module exposes:

```python
FAMILY: str  # e.g. "double_top_bottom" — matches the DETECTORS registry key and the DB's pattern_family column
PATTERN_TYPES: dict[str, str]  # pattern_type -> expected breakout direction, "up" or "down"

def detect(ticker: str, df: pd.DataFrame, rsi_series: pd.Series, vol_sma: pd.Series, config: dict) -> dict | None:
    ...  # returns the generic result shape (below), or None if no candidate found
```

`PATTERN_TYPES` need not cover every `pattern_type` a family can return. Volatility Squeeze and Narrow Range each have one non-directional `pattern_type` (`"volatility_squeeze"`, `"nr4"`/`"nr7"`) used only while `phase == "FORMING"` — direction is genuinely unknown until a later breakout resolves it, so that `pattern_type` is deliberately absent from `PATTERN_TYPES`. Every reader that resolves direction via `PATTERN_TYPES.get(pattern_type)` (the API's `direction` field, `fill_pattern_outcomes()`, `page_helpers.get_pattern_tags_by_ticker()`) already treats a missing key as `None` rather than erroring, and every UI surface that groups by direction already has a defined behavior for `None` (§7b, §8) — a future family with the same "unknown until breakout" shape can reuse this without further changes.

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
        # OR, for a genuine curve rather than a straight segment (§4b):
        {"label": str, "path": [{"date": "YYYY-MM-DD", "price": float}, ...], "dash": bool},
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

`points` and `lines` are what make the schema and chart renderer geometry-agnostic: Head & Shoulders returns 5 points and 1 (neckline) line; Double Top/Bottom returns 3 points and 1 (support/resistance) line; Flag returns 2 points (pole start/end) and 2 (upper/lower channel) lines; Triangle returns its swing-high/swing-low touch points (count varies — at least 2 per side) and 1 (flat resistance or support) line; NR4/NR7 Narrow Range returns 2 points (the narrow bar's high/low) and 2 (breakout trigger) lines. `pattern_detection_detail.js`'s chart renderer (`_pdBuildPatternTraces`) iterates whichever `points`/`lines` a result carries and draws a filled shape through `points` (closed back to the first point) plus the `lines` as key-level segments — it needs no changes to support a new family's shape, as long as that shape is expressible as a handful of discrete points plus straight line segments.

### 4b. The `path` exception — a line that is a genuine curve, not a straight segment

Volatility Squeeze is the one family so far whose key level (the actual Bollinger Band contour through the squeeze window) is a multi-point curve, not a straight line between two dates — forcing it into a straight `date_from`/`price_from`/`date_to`/`price_to` segment would show a flat line spanning the squeeze rather than the bands' real narrowing shape. Rather than add a parallel schema or bypass the registry (which AGENTS.md's central-engine rule for this tool explicitly forbids), the `lines` schema gained one optional field: a `line` entry may supply `"path": [{"date": ..., "price": ...}, ...]` (an ordered list of 2+ points) instead of `date_from`/`price_from`/`date_to`/`price_to`. `_pdBuildPatternTraces` (`static/js/pattern_detection_detail.js`) checks for `path` first and draws it as a polyline; a `line` without `path` renders exactly as before (a straight 2-point segment) — fully backward compatible with every existing family. Volatility Squeeze also still draws its 4 squeeze-corner `points` as a coarse quadrilateral (the existing `points`-closed-polygon fallback), so the chart shows both the precise band contour (via `path`) and the shaded squeeze region (via the `points` polygon) together. Use `path` only when a family's key level is a genuine curve; a straight support/resistance/channel/neckline should keep using `date_from`/`date_to` as every other family does.

## 5. Database

`pattern_detection_results` (one row per `(ticker, pattern_family)`) and `pattern_detection_history` (append-only, `UNIQUE(ticker, scan_date, pattern_family, pattern_type)`) are the two generic tables — see `assets/db_schema_and_architecture.md` for full column definitions. Head & Shoulders' original `head_shoulders_results`/`head_shoulders_history` tables were folded into these via a one-time `db_schema.migrate_db()` copy step and are no longer written to.

## 6. Adding a New Pattern Family

1. Create `<family>_engine.py` implementing the contract in §3 — reuse `pattern_geometry_helpers.py` for pivot detection, R² fit, and the volume/RSI supporting-signal checks wherever the new pattern's validation logic overlaps with an existing family's.
2. Register it: add one entry to `pattern_detection_engine.DETECTORS`.
3. Add config defaults: a family sub-block under `config.py`'s `DEFAULT_CONFIG["SCHEDULING"]["PATTERN_DETECTION"]` and `["NOTIFICATIONS"]["PATTERN_DETECTION_ALERTS"]`.
4. Add a Settings sub-toggle: extend the "Pattern Detection" card in `templates/settings/_system.html` with the new family's checkboxes/fields, and extend `static/js/settings_shared.js`'s harvesting code and `api_routes_system.py`'s `PatternFamilyScheduleConfig`/`PatternFamilyAlertConfig` Pydantic models with any new field names.
5. Add a display label: one entry in `static/js/pattern_detection.js`'s `PATTERN_FAMILY_LABELS`/`PATTERN_TYPE_LABELS` maps (family filter dropdown + table badges) and `static/js/pattern_detection_detail.js`'s `PD_PATTERN_TYPE_LABELS` map (checkbox/legend labels). A chart color is optional — `PD_PATTERN_COLORS` gives a curated color per pattern_type, but any pattern_type missing from it automatically gets a deterministic fallback color (§8), so this step is cosmetic polish, not a requirement. Add a 2-3 sentence plain-language entry to `PD_PATTERN_EXPLANATIONS` (also in `pattern_detection_detail.js`) — shown at the bottom of the per-ticker page for whichever patterns are present.
6. Document it: a glossary term-box (`templates/glossary/_strategy.html`) and a matching `learn_cards_seed.py` card per pattern type, per AGENTS.md's Glossary rule.

Nothing else needs a new-family update: the Portfolio/Watchlist/Stock Detail "Setups & Tags" badges (§9), the Bullish/Bearish grouping, and the tag color scheme all resolve automatically from `PATTERN_TYPES`/`phase_label()` — the same registry data the scan itself already requires every family to expose.

No DB migration, no new scheduler job, no new API route, and no page/chart-renderer changes are needed for a new family that fits the `points`/`lines` shape — bullish/bearish grouping and chart color both resolve automatically, see §8.

## 7. Flag and Triangle — Regression-Based Detection

Both families validate a swing shape via linear regression rather than a fixed-length alternating-pivot run, since the pattern being detected is a line (or pair of lines), not a small set of discrete extrema.

**Bull/Bear Flag (`flag_engine.py`):** a flagpole (a sharp N-day move) followed by an M-day consolidation channel.
- *Flagpole:* the N-day return must exceed `SIGMA_MULTIPLIER × σ_daily × √N` (time-scaled volatility, not a bare daily-σ comparison — an N-day cumulative return is expected to scale with √N under a random-walk assumption, so comparing it to 1-day σ directly would systematically over/under-flag depending on N). `σ_daily` is the standard deviation of daily returns over `SIGMA_WINDOW_DAYS`, taken *before* the flagpole starts, so the sharp move itself never inflates its own threshold.
- *Channel:* `find_pivots` locates swing highs/lows within the consolidation window; `linreg` fits one line through the highs and one through the lows. Both slopes (in `slope_pct_per_day` terms) must be within `[-MAX_CHANNEL_SLOPE_PCT, 0]` for a Bull Flag (or `[0, MAX_CHANNEL_SLOPE_PCT]` for a Bear Flag) and within `PARALLEL_TOLERANCE_PCT` of each other.
- *Volume:* a `linreg` fit of volume over the same window must have a negative slope (declining volume, the classic "dry-up" during a genuine pause).
- *Breakout/target:* CONFIRMED when the close crosses the channel line in the pole's direction; `measured_target` projects the flagpole's own height from the breakout point (standard measured-move technique).
- The engine searches consolidation lengths from `MAX_CONSOLIDATION_DAYS` down to `MIN_CONSOLIDATION_DAYS`, returning the first (longest, most-established) window that produces a valid candidate.

**Ascending/Descending Triangle (`triangle_engine.py`):** a flat side (resistance for Ascending, support for Descending) and a sloped side (rising support for Ascending, falling resistance for Descending), both fit over a single trailing `WINDOW_DAYS` lookback via `find_pivots` + `linreg`.
- *Flat side:* `|slope_pct_per_day| <= FLAT_SLOPE_EPSILON_PCT`. This is a **regression-slope test, not a variance test** — variance of raw prices doesn't measure "flatness of a trend" and is scale-dependent (a $2,000 stock needs a different epsilon than a $20 stock); a near-zero regression slope is directly comparable to the sloped side's slope test and is scale-invariant once normalized to %/day.
- *Sloped side:* slope must exceed `MIN_SLOPE_PCT` in the pattern's direction (positive/rising for Ascending, negative/falling for Descending) — this floor exists so a genuinely flat-but-noisy line on the "sloped" side doesn't qualify as a trend.
- The flat level itself is the mean price of that side's swing-point touches.
- *Volume:* mirrors Flag's declining-volume check over the full window (the same "dry-up before breakout" convention already documented for VCP Breakout in the glossary).
- *Breakout/target:* CONFIRMED when the close crosses the flat level; `measured_target` projects the triangle's height (flat level vs. the first touch on the sloped side) from the breakout point.

## 7b. Volatility Squeeze and Narrow Range — Direction-Unknown-Until-Breakout Detection

Both families detect a *state* (compressed volatility, or a single unusually narrow bar) whose eventual breakout direction is not knowable from the state itself — unlike every earlier family, whose `pattern_type` already implies a direction (`bull_flag` is always bullish) even while still `FORMING`. Each introduces one non-directional `pattern_type` used only for the FORMING phase, then resolves to one of two directional `pattern_type`s once a later bar's close breaks decisively past a reference level, within a configurable lookahead window — past that window with no resolution, the candidate is dropped (no result, not a stale FORMING carried forward indefinitely).

**Volatility Squeeze (`volatility_squeeze_engine.py`):** a squeeze fires when `indicators.compute_bollinger_bands` (`WINDOW_DAYS`-day SMA ± `NUM_STD` standard deviations) sits fully inside `indicators.compute_keltner_channel_series` (`WINDOW_DAYS`-day EMA ± `KC_MULTIPLIER` × ATR(`WINDOW_DAYS`) — the non-original/EMA+ATR Keltner convention, distinct from `indicators.compute_keltner_channel`'s last-bar-only EMA(21)/ATR(14) z-score variant used elsewhere) for at least `MIN_SQUEEZE_DAYS` consecutive bars.
- *FORMING:* the squeeze is still active as of today — `pattern_type = "volatility_squeeze"`, no direction.
- *CONFIRMED:* the squeeze ended within the last `BREAKOUT_LOOKAHEAD_DAYS` bars and today's close is decisively outside the Bollinger Band — `pattern_type` becomes `volatility_squeeze_bullish` (close above the upper band) or `volatility_squeeze_bearish` (close below the lower band).
- *Volume/RSI:* declining volume through the squeeze (the same "dry-up" convention as Flag/Triangle) plus a breakout-day surge; RSI moving in the breakout's direction since the squeeze began. Both are only meaningful once direction resolves, so they read a plain "declining" check while FORMING.
- *Target:* projects the squeeze's own Bollinger Band width (measured at the squeeze's start) from the breakout point.
- `BULLISH_ENABLED`/`BEARISH_ENABLED` gate which *confirmed* breakout direction gets reported — mirroring Flag's `BULL_ENABLED`/`BEAR_ENABLED` — but do not gate the FORMING state, since direction isn't known yet to filter on.

**NR4/NR7 Narrow Range (`narrow_range_engine.py`):** a bar qualifies as narrow when its True Range (`indicators.compute_true_range` — Wilder's gap-inclusive definition, `max(H-L, |H-PrevClose|, |L-PrevClose|)`, not the bare `High - Low` a naive reading of the classic NR7 definition might suggest) is the smallest of the trailing 4 (NR4) or 7 (NR7) bars *and* it is a strict inside bar vs. the prior bar (`High < PrevHigh` and `Low > PrevLow`). NR7 is checked first and preferred over a simultaneous NR4 candidate on the same ticker (the rarer, stricter signal wins), since only one result can be stored per `(ticker, pattern_family)`.
- *FORMING:* the narrow bar is today's bar — `pattern_type = "nr4"` or `"nr7"`, no direction.
- *CONFIRMED:* a later bar (within `BREAKOUT_LOOKAHEAD_DAYS`) closes above the narrow bar's own high (`..._bullish`) or below its own low (`..._bearish`).
- *Volume/RSI:* below-average volume on the narrow bar itself (genuine indecision, not a low-liquidity fluke) plus a breakout-day surge; RSI moving in the breakout's direction since the narrow bar.
- *Target:* projects the narrow bar's own high-low range from the breakout point.
- `NR4_ENABLED`/`NR7_ENABLED` gate which window(s) are searched at all; `BULLISH_ENABLED`/`BEARISH_ENABLED` gate which confirmed breakout direction gets reported, same as Volatility Squeeze.

## 7c. Parabolic Stretch and Bullish/Bearish Divergence — Mean-Reversion/Exhaustion Detection

Both families are directional from the moment they fire — unlike §7b's Volatility Squeeze/Narrow Range, the sign of the underlying statistic (Z-score, or which way price/RSI diverged) already tells you the expected reversal direction while still `FORMING`, so both reuse the original Head & Shoulders/Double Top-Bottom FORMING→CONFIRMED model rather than the direction-unknown-until-breakout one.

**Parabolic Stretch (`parabolic_stretch_engine.py`):** distance = `Close - SMA(SMA_WINDOW)`. A stock is "stretched" when the Z-score of that distance series against its own trailing `Z_WINDOW_DAYS` mean/std exceeds ±`Z_THRESHOLD` — a Bollinger-Band-style test applied to the distance-from-mean series itself, not to raw price. This is the one family whose data requirement exceeds every other family's: `SMA_WINDOW` (200) + `Z_WINDOW_DAYS` (252) = 452 bars minimum, which is why `pattern_detection_engine._LOOKBACK_BARS` was widened from 180 to 500 (§2) — the 2-year parquet history (~500-507 trading days) just barely covers it.
- *FORMING:* the most recent stretch breach (within `_MAX_STRETCH_LOOKBACK_DAYS`, a fixed 30-bar module constant) is today's bar — `pattern_type` is `parabolic_stretch_overbought` (Z ≥ threshold) or `parabolic_stretch_oversold` (Z ≤ -threshold), direction known immediately from the sign.
- *CONFIRMED:* within `BREAKOUT_LOOKAHEAD_DAYS` of the stretch bar, today's Z-score has retraced back inside ±`CONFIRM_Z_THRESHOLD` **and** price has genuinely moved back toward the mean since the stretch bar (a plain close-vs-close check in the reversion direction, guarding against a Z-score retrace driven purely by the rolling mean/std shifting rather than price actually reverting).
- *Volume/RSI:* climax volume on the stretch bar itself (`volume > vol_sma * VOLUME_CONFIRM_MULTIPLIER`) repurposes the generic `volume_confirms` field as a blow-off/capitulation check; the generic `rsi_divergence` field is repurposed here as "RSI was already at a classic 70/30 extreme on the stretch bar" rather than an actual divergence check (the schema field name is generic across families — see Volatility Squeeze's differently-repurposed use of the same field in §7b).
- *Target:* the 200-day SMA itself, at the breakout bar if confirmed else today — the reversion target is literally the mean the price is stretched away from.
- *Chart:* two `points` (the stretch bar and today), and one `lines` entry using the `path` field (§4b) to draw the actual SMA curve across the stretch-to-today window rather than a straight segment, since a 200-day SMA is never linear over even a short span.
- `OVERBOUGHT_ENABLED`/`OVERSOLD_ENABLED` gate which stretch direction is reported, mirroring Double Top/Bottom's `TOP_ENABLED`/`BOTTOM_ENABLED`.

**Bullish/Bearish Divergence (`momentum_divergence_engine.py`):** reuses Double Top/Bottom's exact 3-point alternating-pivot search (`pattern_geometry_helpers.latest_alternating_run`, `_ORDER = 5`) but inverts its core validation: instead of requiring the two outer extremes to be near-equal (`BALANCE_TOLERANCE_PCT`), it requires the second extreme to be a genuinely **new** extreme — a Higher High for Bearish, a Lower Low for Bullish — by at least `MIN_PRICE_CHANGE_PCT`, while RSI moves the opposite way across the same two points by at least `MIN_RSI_GAP` points. Both conditions are hard gates (not supporting signals), so a returned candidate is always a real divergence — the generic `rsi_divergence` result field is therefore always `true` for this family.
- *FORMING:* the divergence exists (both gates pass) but today's close hasn't yet broken past the level between the two extremes.
- *CONFIRMED:* today's close is below that level (Bearish) or above it (Bullish) — the same "neckline break" concept as Double Top/Bottom, reusing `pattern_geometry_helpers.volume_confirms` for the volume supporting signal and `piecewise_r2` for fit quality.
- *Target:* the same measured-move projection as Double Top/Bottom — the height between the middle pivot and the average of the two outer extremes, projected from the middle pivot in the reversal's direction.
- *Chart:* 3 `points` (two extremes + the middle pivot) and 1 dashed `lines` entry at the middle pivot's level, identical shape to Double Top/Bottom.
- `BULLISH_ENABLED`/`BEARISH_ENABLED` gate which direction is searched at all.

## 8. Presentation Layer

**List page (`/pattern-detection`):** results are grouped client-side by `ticker` (`_pdGroupByTicker` in `static/js/pattern_detection.js`) — one row per ticker, one `.setup-tag` badge per currently-active pattern (reusing the same badge component as the Portfolio/Watchlist "Setups & Tags", §9). Clicking a row navigates to `/pattern-detection/{ticker}` rather than opening a chart inline — per AGENTS.md rule 18, a dedicated page (not a modal) is the right call once the chart itself needs real UI (the checkbox tree below), the same carve-out that already applies to Market Regime and Monte Carlo.

Three independent filters narrow the table, each unambiguously labeled so "all" in one dimension is never confused with "all" in another: **Scope** (Portfolio / Watchlist / All Tickers — defaults to Portfolio; a ticker row is shown if it's in that scope's ticker set, sourced from `GET /api/pattern-detection/results`'s `portfolio_tickers`/`watchlist_tickers` arrays), **Direction** (All Directions / Bullish / Bearish — filters on each result's server-resolved `direction` field), and **Family** (All Families / one per registered `pattern_family`).

**Per-ticker page (`/pattern-detection/{ticker}`):** `GET /api/pattern-detection/chart/{ticker}` returns every currently-active pattern for that ticker (not scoped to one family), each carrying a server-resolved `"direction": "up"|"down"|None` field looked up from `DETECTORS[pattern_family].PATTERN_TYPES[pattern_type]` — the frontend never hardcodes which pattern types are bullish vs. bearish. `static/js/pattern_detection_detail.js` builds three checkbox groups (Bullish / Bearish / Forming — Direction Pending) purely from this field, the third catching any pattern whose direction is `None` (§7b's FORMING volatility_squeeze/nr4/nr7 states):

- A master checkbox per group; checking/unchecking it checks/unchecks (and shows/hides) every pattern checkbox in that group.
- Toggling an individual pattern checkbox re-derives the master's state: checked (all on), unchecked (all off), or `indeterminate` (mixed) — plain DOM, no extra dependency.
- All patterns are enabled by default on page load.

Below the chart, a plain-language explanation (2-3 sentences, `PD_PATTERN_EXPLANATIONS` in `pattern_detection_detail.js`) is shown for each distinct pattern_type currently present on the ticker, so the page doesn't assume the viewer already knows what a Double Top means.

**Overlay chart:** for each *checked* pattern, `_pdBuildPatternTraces` draws a closed, semi-transparent (`fillcolor` ~0.2 alpha) `fill: 'toself'` shape through that pattern's `points` in chronological order (closed back to the first point) — this is what gives Head & Shoulders its recognizable shaded "M" silhouette and Double Top/Bottom its shaded wedge, and lets two overlapping patterns' shaded regions visually blend rather than just stacking outlines. The pattern's `lines` (key level) and point markers/labels draw on top, plus a star marker at `breakout_date`/`breakout_price` if confirmed. Every trace for one pattern shares a Plotly `legendgroup` (keyed on `"{family}:{pattern_type}"`) so the legend shows one entry per pattern, not one per trace.

**Chart shape/fill color assignment** (distinguishing overlapping shapes, not semantic): `PD_PATTERN_COLORS` (in `pattern_detection_detail.js`) gives a curated color per `pattern_type`; any `pattern_type` not in that map gets a deterministic color from `PD_FALLBACK_PALETTE` via a simple string hash, so a brand-new pattern family always renders with a stable, distinct chart color with zero code changes required. This is intentionally a separate, denser palette from the tag color scheme (§9) — the chart needs N visually distinct colors to tell overlapping shapes apart, while tags/badges use a fixed 3-color semantic scheme everywhere else.

## 9. Tags/Badges — Shared Across Portfolio, Watchlist, and Stock Detail

Every currently-active pattern for a ticker also shows up as a badge on the Portfolio and Watchlist tables and on the Stock Detail page header — not just on the Pattern Detection page itself — via `page_helpers.get_pattern_tags_by_ticker(tickers)`, a batch query shared by all three page routes (`page_routes.py`). It resolves each `pattern_detection_results` row to `{label, phase, direction}` using the same `DETECTORS[family].phase_label()`/`.PATTERN_TYPES` registry lookups the rest of this tool uses, then `compute_badge_tags()` (`page_helpers.py`) passes the result through as `pattern_tags`, rendered by the `pattern_tags()` Jinja macro (`templates/partials/_macros.html`) — the single template used by `portfolio.html`, `watchlist.html`, and `stock_detail.html` so the three never drift apart.

**Tag color scheme**, applied identically everywhere a pattern is tagged (these badges, the Pattern Detection list-page badges, and the per-ticker page's checkbox labels): FORMING is always orange (`.pattern-tag-forming`) regardless of direction, since nothing has resolved yet; once CONFIRMED, the tag reflects direction — red (`.pattern-tag-bearish`) or green (`.pattern-tag-bullish`). All three classes use a solid dark-tinted background with light text (mirroring the existing `.quality-grade-*` classes) rather than colored text on a dark background, which read poorly.

Because this batch fetch happens once per page load (not once per ticker), adding it did not change the row-count semantics of the Portfolio/Watchlist queries — those queries no longer join `pattern_detection_results` directly (a ticker can have more than one active family, which would have silently duplicated ticker rows through a plain `LEFT JOIN`); the pattern tags are merged in afterward, keyed by ticker.
