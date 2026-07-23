# Buy-Signal Confluence Pipeline

A four-stage pipeline that turns existing per-ticker signals this app already computes into one
synthesized **🎯 Buy Recommendation**, with no new fetch and (mostly) no new tables — each stage
reads what an earlier stage (or an already-existing engine) produced:

```
Part A                Part B                    Part C                     Part D
Signal Pillar   →   Regime-Weighted   →   Cross-Engine Alert    →   Recommendation
Confluence          Conviction Score       Referee (meta-veto,        Risk/Reward Gate
(pillar vote)       (regime weighting)     shadow-only today)         (risk/reward gate)
                                                                            ↓
                                                                   🎯 Buy Recommendation
```

Each part is independently useful — Part A's badge, Part B's score, and Part D's gate all
render on their own regardless of the others — but Part D's Buy Recommendation is the pipeline's
combined output: it requires a bullish Part A confluence call that also clears the Part D gate.
Part B's score and Part C's referee both sit alongside this combination as complementary lenses
rather than hard requirements — see each part's own section for exactly how it participates.

| Part | Feature | Reads |
|---|---|---|
| A | Signal Pillar Confluence | Pattern Detection, Trap Monitor, Earnings Volatility, ML confidence |
| B | Regime-Weighted Conviction Score | Quant Score, ML confidence, Part A's two Technical-pillar sources, Market Regime |
| C | Cross-Engine Alert Referee | Part A + Part B's combined signal, trained on Trap Monitor's and Pattern Detection's own resolved history |
| D | Recommendation Risk/Reward Gate | Part A's bullish call, ATR stop-loss math, confirmed pattern targets / ML Quantile Regression |

**Engines:** `score_analysis.py` owns Parts A and B, the as-of variants Part C's historical
backfill uses, and the combined Buy Recommendation logic (Part D's own risk/reward math lives
in `position_sizing.py`, since that module already owns the ATR stop-loss math it needs).
`alert_referee_engine.py` owns Part C, generalizing the pre-existing Trap Monitor referee pilot
to a second engine. Parts A, B and D are read-only over already-persisted engine outputs with no
new DB tables or scheduler jobs; Part C is the one exception, adding 5 columns to
`trap_phase_history`/`pattern_detection_history` (populated at scan time, no new fetch) and one
new scheduler job (`confluence_referee_training_job`, training only — the shadow evaluation
itself reuses the existing `trap_monitor_job` intraday cadence rather than adding a second one).

---

## A. Signal Pillar Confluence

Flags a ticker when at least two of three independent signal "pillars" agree on direction
within a 5-trading-day rolling window, with none of the three disagreeing. Adds no new signal
of its own — it only checks whether signals this app already computes corroborate each other.

`score_analysis.evaluate_pillar_confluence(ticker)` / `evaluate_pillar_confluence_batch(tickers)`
return `{"bullish_pillars": [...], "bearish_pillars": [...], "confluence": bool, "direction":
"bullish"|"bearish"|None}`.

**The three pillars:**
- **Technical** — every `pattern_detection_history` row with `phase='CONFIRMED'` for the
  ticker in the window, direction resolved by iterating `pattern_detection_engine.DETECTORS`
  (the same registry Pattern Detection itself is built on — see `assets/pattern_detection.md`)
  and looking up each row's `pattern_type` in that family module's own `PATTERN_TYPES` dict.
  Plus `trap_phase_history.phase`, resolved via `bull_bear_trap_engine._PHASE_EXPECTED_DIRECTION`.
  **Never hardcodes a family list** — a new pattern family registered in `DETECTORS` is picked
  up automatically with zero changes to this pillar's code.
- **Statistical** — `earnings_volatility_history.drift_avg_pct_5d` sign, gated on
  `edge_score > 0` (only counted when there's a real measured mispricing edge, not noise).
- **ML** — `quant_signals.ml_confidence_score` above/below 50.

**Per-pillar voting.** A pillar only casts a vote when every signal it saw in the window points
the same way (`score_analysis._pillar_vote()`) — if a pillar's own signals conflict with each
other (e.g. one confirmed pattern says up, another says down), that pillar abstains rather than
forcing a side. Bullish confluence: ≥2 pillars vote "up" and none votes "down". Bearish
confluence is the mirror case. A ticker with one bullish and one bearish pillar never reaches
confluence regardless of the third pillar's vote — genuine disagreement blocks the flag.

**5-trading-day window.** Derived from `quant_signals`' own dates (written every trading day
the nightly quant scan runs), not from `pattern_detection_history`'s own `scan_date` column —
`pattern_detection_engine.PatternDetectionEngine.run_scan()` deliberately skips logging a
history row when a pattern instance is unchanged from the previous scan, so that table's own
distinct dates are sparse and can silently span far more than 5 trading days for a quiet
ticker. `score_analysis._trading_windows_batch()` computes the shared window once per batch;
`_pattern_signals_batch()`/`_trap_signals_batch()` filter against it. Earnings Volatility only
scans a ticker within ~14 days of its next earnings date, so its own "last 5 scans" (from
`earnings_volatility_history`, added alongside the previously latest-only `earnings_volatility`
table specifically to support this rolling window) can span several calendar weeks for a
ticker that isn't near-term — expected, not a gap.

**Statistical pillar's sibling column.** `pairs_spread_results` gained `cheap_ticker`/
`rich_ticker` columns in the same change (previously only a human-readable `direction` string
like `"AAA rich vs BBB"` existed) so a pair's cheap side can be read as a value without
string-parsing — added for future extensibility, not currently wired into this pillar's vote
(the Statistical pillar is earnings drift only; Pairs Spread Monitor evaluates a relationship
between two tickers, not one ticker in isolation, and isn't folded in here).

**Surfaced as:** a `📈`/`📉` "Pillar Confluence" badge on Portfolio/Watchlist (in the same
"Setups & Tags" cell as Trap Monitor/Bubble Radar/Pattern Detection tags), a "Pillar
Confluence" row on Stock Detail, and an optional "Pillar Confluence" column via the Columns
picker. Glossary: `templates/glossary/_pillar_confluence.html`.

---

## B. Regime-Weighted Conviction Score

A 0-100 blend of four existing per-ticker signals, weighted by the current market regime
rather than a single fixed weight vector — patterns and mean-reversion setups matter more in
a range-bound (Chop) market, trend-following scores matter more in a trending (Bull) market.

`score_analysis.compute_regime_weighted_score(ticker)` /
`compute_regime_weighted_score_batch(tickers)` return `{"score": float, "regime": str,
"components": {...}}` or `None` ("no signal").

**Inputs**, each normalized to 0-100 before weighting:
- `stock_signals.composite_score` (already 0-100).
- `stock_signals.ml_confidence` — **previously a dead schema column with no write site
  anywhere in the codebase.** `ai_prediction_engine.update_daily_ml_predictions()` now mirrors
  `quant_signals.ml_confidence_score` onto it in the same `UPDATE` pass, every time the daily
  ML Inference job (`ml_inference_job`) runs — needed as a one-row-per-ticker join target this
  score can read without a second `quant_signals` subselect.
- **Pattern Detection** direction, windowed the same way as Part A's Technical pillar but kept
  **separate** rather than merged into one vote — `_pillar_vote()` over
  `_pattern_signals_batch()`'s result only, mapped to 100 (up) / 50 (neutral/no signal) / 0
  (down).
- **Trap Monitor** direction, same treatment via `_trap_signals_batch()`.

Pattern Detection and Trap Monitor are weighted independently here specifically because the
weight table below needs to move them by different amounts — Part A's single merged Technical-
pillar vote can't be reused directly for this.

**Regime-switched weights** (`META_SCORING.REGIME_WEIGHTS`, `config.py`, editable in Settings
→ Position Sizing Defaults / X-Ray Allocation Targets column → "Regime-Weighted Conviction
Score" card, `id="meta-scoring-card"`):

| Regime | composite_score | ml_confidence | pattern | trap |
|---|---|---|---|---|
| Bull | 40% | 30% | 20% | 10% |
| Chop | 25% | 25% | 35% | 15% |

Regime is read from `regime_engine.get_latest_regime()`'s `price_hmm_label`
(Bull/Chop/Crash — the same 3-state Gaussian HMM classification the Market Regime page shows).

**Crash veto.** Suppressed entirely — `None`, never a reweighted number — whenever:
- `price_hmm_label == "Crash"`, or
- `market_regimes.market_stress_score` (the Isolation Forest market-wide stress score) is at
  or above `META_SCORING.CRASH_VETO.MARKET_STRESS_THRESHOLD` (default 0.75), or
- no regime has been computed yet (`get_latest_regime()` returns `None`).

None of the four inputs were validated to mean anything during a genuine crash regime, and the
market-stress score itself is non-directional (high = unusual, not bullish/bearish), so it
can't be folded into the weighted blend as a fifth input — a hard veto is more honest than
fabricating a number from inputs whose real-world meaning has broken down. A ticker missing
`composite_score`/`ml_confidence` entirely (never scanned, or never run through ML inference)
also resolves to `None` — a missing required input, not a veto condition, but the same outcome.

**Malformed-config safety.** `META_SCORING.REGIME_WEIGHTS` is user-editable config, not
internal state — the final weighted sum uses `weights.get(key, 0.0)` rather than `weights[key]`
so a manually-edited config.json missing one weight key degrades that component to a zero
contribution instead of throwing an uncaught `KeyError` that would crash the whole
Portfolio/Watchlist page for every ticker.

**Surfaced as:** a labeled "Regime-Weighted Conviction Score" row on Stock Detail
(`{score}/100 ({regime} regime)` or "No signal") and an optional column on Portfolio/Watchlist
— deliberately **never** rendered as a second, competing Buy/Sell verdict next to
`stock_signals.overall_signal`; it's a complementary regime-aware lens on the same underlying
data, not a replacement. Glossary: `templates/glossary/_regime_weighted_score.html`.

---

## C. Cross-Engine Alert Referee

Generalizes `alert_referee_engine.py`'s calibrated-probability veto — previously piloted on
Trap Monitor only (see `AGENTS.md`'s "Alert Confidence Referee" entry) — to a second engine,
`CONFLUENCE_ENGINE = "Confluence"`, that scores the combined Part A/B signal itself (Part A's
3 pillar votes + Part B's regime-weighted score) rather than either source engine's own raw
features. Same shared architecture as the Trap Monitor pilot — `CalibratedClassifierCV`
(isotonic) on a `RandomForestClassifier`, `_HARD_MIN_SAMPLES` hard floor, Shadow→Active gating —
but with its own model file (`models/alert_referee_confluence.joblib`), its own training
schedule/config (`SCHEDULING.ALERT_REFEREE_TRAINING_CONFLUENCE`, mirroring
`ALERT_REFEREE_TRAINING`'s shape), and its own Settings card ("🔀 Cross-Engine Alert Referee",
`_alerts.html`) with a separate readiness panel and shadow log.

**Feature set (`_CONFLUENCE_FEATURE_COLUMNS`)** — deliberately compact, disjoint from Trap
Monitor's own RSI/EMA/volume-ratio features:
- `pillar_technical_up`/`_down`, `pillar_statistical_up`/`_down`, `pillar_ml_up`/`_down` — one-hot
  per Part A pillar (both 0 means that pillar abstained that day).
- `regime_weighted_score` — Part B's 0-100 score (or `NaN`→0 via the existing `fillna(0.0)`
  pattern when Part B itself returned "no signal").

**New columns, forward-populated only.** `trap_phase_history` and `pattern_detection_history`
each gained 5 columns (`pillar_technical`, `pillar_statistical`, `pillar_ml`,
`regime_weighted_score`, `confluence_features_ts`), populated at scan time by
`bull_bear_trap_engine.TrapEngine._save_results()` / `pattern_detection_engine
.PatternDetectionEngine._save_results()` via `score_analysis.evaluate_pillar_confluence_batch()`/
`compute_regime_weighted_score_batch()` — the same functions Part A/B themselves call, so no
new signal logic exists anywhere. `confluence_features_ts` (a timestamp, not a boolean) is the
"has this row been through Confluence-feature computation" marker, needed because the 3 pillar
votes are each legitimately `NULL` on a row that *was* computed but abstained on every pillar —
a bare `IS NOT NULL` check on any single pillar column can't distinguish "abstained" from "never
computed."

**Historical backfill.** `alert_referee_engine.backfill_historical_confluence_features()`
reconstructs these columns for rows logged before this shipped
(`confluence_features_ts IS NULL`), using `score_analysis.evaluate_pillar_confluence_as_of(ticker,
scan_date)` / `compute_regime_weighted_score_as_of(ticker, scan_date)` — as-of variants of Part
A/B's own batch functions (an optional `as_of` cutoff threaded through `_trading_windows_batch()`/
`_pattern_signals_batch()`/`_trap_signals_batch()`/`_statistical_signals_batch()`/
`_ml_signals_batch()`, plus a `market_regimes`/`quant_signals`-history read in place of
`regime_engine.get_latest_regime()`/`stock_signals` for the regime score) — so an already-resolved
historical trap/pattern row gets scored with the pillar votes/regime that genuinely existed on
its own `scan_date`, not today's. Unlike `backfill_historical_features()` (Trap Monitor's own
RSI/EMA backfill, which re-runs `TrapEngine._analyse_ticker()` against parquet), this needs no
engine re-run — pillar votes and regime are themselves just windowed reads over other tables.
`train_referee_model(CONFLUENCE_ENGINE)` runs this backfill first, same as the Trap Monitor
trainer runs its own equivalent first.

**Training rows** union three sources, since a resolved historical call from either underlying
engine is usable data for the combined model:
- `trap_phase_history` — one row per resolved `direction_correct_14d` (14d-only, matching Trap
  Monitor's own trainer; trap_phase_history's `direction_correct_30d` is not used here or there).
- `pattern_detection_history` — **two** independent training rows per pattern instance whenever
  both horizons are resolved: one keyed on `direction_correct_14d`, one on `direction_correct_30d`
  — same point-in-time pillar/regime features, a different-horizon label. `readiness_status()`'s
  `current` count reflects this (a single pattern row with both horizons resolved counts as 2).

**Evaluation trigger.** Rather than a new scheduled job, the Confluence shadow evaluation
piggybacks on the existing `trap_monitor_job` (frequent intraday cadence) with a once-per-ticker-
per-day gate: `db_helpers.log_trap_phase()` now returns `True` only when it actually inserted a
new row (blocked by `trap_phase_history`'s `UNIQUE(ticker, scan_date)` on every later call that
day) — `TrapEngine._save_results()` surfaces this as `row["_new_trap_history_row"]`, alongside
`row["confluence_direction"]`/`pillar_*`/`regime_weighted_score`. `scheduler_jobs
.run_trap_monitor_job()` calls `evaluate_alert(CONFLUENCE_ENGINE, ...)` only when
`_new_trap_history_row` is true and a confluence direction exists — recomputing pillar
votes/regime every 5-minute intraday tick would just re-log an identical daily-granularity
result, the same spurious-repeat failure mode AGENTS.md's rule 19 already flags for Trap
Monitor's own alert dedup. `pattern_detection_job`'s own scan populates the training-data
columns on its own history rows but does not independently trigger a shadow evaluation, avoiding
duplicate same-day log rows for one ticker's confluence state.

**Training schedule/config.** A separate `confluence_referee_training_job` (own `JOB_GRAPH`
entry, own `CronTrigger` from `SCHEDULING.ALERT_REFEREE_TRAINING_CONFLUENCE.{DAYS,TIME}`,
default Sunday 05:30 local — 30 minutes after Trap Monitor's own referee training) trains via
`scheduler_jobs.run_confluence_referee_training_job()`. `POST /api/alert-referee/train` and
`GET /api/alert-referee/status`/`log` all take an `engine` query param (`TrapMonitor` default,
or `Confluence`) rather than duplicating routes.

**Expect Shadow mode for a long stretch after launch** — same as the Trap Monitor pilot — since
the combined feature set has never been scored before; `confluence_features_ts` only starts
populating from the day this shipped forward (aside from the one-time historical backfill), so
`readiness_status(CONFLUENCE_ENGINE)`'s `current` count starts low regardless of how much history
`trap_phase_history`/`pattern_detection_history` already had before this change.

---

## D. Recommendation Risk/Reward Gate

Requires a minimum reward:risk ratio, computed entirely from this app's existing ATR-based
stop-loss math, before a ticker is labeled a **Buy Recommendation** — the pipeline's final,
synthesized output. Deliberately **not** a Kelly Criterion calculation — this app already
rejected Kelly sizing in favor of fixed-fractional ATR sizing (see the Position Sizing glossary
entry) because Kelly's "optimal" sizing is too aggressive when the edge estimate isn't reliable;
this reuses that same rationale rather than reopening it.

`position_sizing.passes_risk_reward_gate(ticker)` / `passes_risk_reward_gate_batch(tickers)`
return `None` (no signal) when a required input is missing, otherwise `{"entry_price",
"stop_price", "take_profit", "take_profit_source", "risk_reward", "min_risk_reward", "passes"}`.

**Math (all existing functions/tables, no new fetch):**
- **Entry** — `stock_signals.current_price`.
- **Stop** — `calculate_position_size()`'s ATR-based `stop_price` output (the exact same
  function and formula the Position Sizing tool already uses), fed the latest `quant_signals.atr_pct`
  for the ticker and the configured `ACCOUNT_VALUE`/`RISK_PCT`/`STOP_MULTIPLE` (which don't
  actually affect `stop_price`'s formula, but `calculate_position_size()` is called as specified
  rather than reimplementing its stop-price formula a second time).
- **Take-profit** — the highest `pattern_detection_results.measured_target` among **CONFIRMED**
  rows whose own family/pattern_type resolves to an "up" direction (via the same
  `pattern_detection_engine.DETECTORS` registry Part A's Technical pillar reads — never a
  hardcoded family list, so a new pattern family is automatically eligible with no changes here).
  If no qualifying confirmed pattern exists, falls back to the ML Quantile Regression Q90 band
  (`db_helpers.get_latest_quantile_bands()` — the same band already shown on the Position
  Targets panel). `None` if neither is available.
- **Gate:** `risk_reward = (take_profit - entry_price) / (entry_price - stop_price)`; `passes`
  is true when `risk_reward >= MIN_RISK_REWARD` (a new key in the existing `POSITION_SIZING`
  config block, default 1.5, editable in Settings → Position Sizing Defaults — no new panel).

**Buy Recommendation itself** (`score_analysis.evaluate_buy_recommendation(ticker)` /
`evaluate_buy_recommendation_batch(tickers)`) combines Part A and Part D:
`recommended = (Part A's Pillar Confluence direction == "bullish") AND (Part D's gate passes)`.
Part B's Regime-Weighted Conviction Score is attached to the result as context only — it's
documented elsewhere as deliberately never a competing Buy/Sell verdict, so it isn't part of
this boolean either; Pillar Confluence's own bullish/bearish call is the only directional
trigger between Parts A and B. Returns `None` (not a candidate) whenever confluence isn't
bullish or the Risk/Reward Gate itself returned `None` — a non-`None`, non-recommended result
(bullish confluence that failed the gate) is still returned so the UI can show *why* a ticker
didn't qualify rather than omitting it silently.

**Surfaced as:** a `🎯 Buy Recommendation (R:R X:1)` badge on Portfolio/Watchlist (in the same
"Setups & Tags" cell as Pillar Confluence/Trap Monitor/Bubble Radar/Pattern Detection tags), a
"Buy Recommendation" row on Stock Detail (showing the computed ratio and, when it fails, the
reason), and an optional "Buy Recommendation" column via the Columns picker. Glossary:
`templates/glossary/_buy_recommendation.html`.
