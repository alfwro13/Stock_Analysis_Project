# Buy-Signal Confluence Pipeline

Four independently-buildable pieces that compose into one pipeline (pillar vote → regime
weighting → cross-engine probability → risk/reward gate) for turning existing per-ticker
signals into a synthesized buy recommendation. Each part is useful on its own and does not
require the others — this is why they shipped as separate PRs rather than one large change.

| Part | Feature | Status |
|---|---|---|
| A | Signal Pillar Confluence | Shipped |
| B | Regime-Weighted Conviction Score | Shipped |
| C | Cross-Engine Alert Referee | Planned — not yet built |
| D | Recommendation Risk/Reward Gate | Planned — not yet built |

Engine: `score_analysis.py` (Parts A and B; Part D is planned to live in `position_sizing.py`
instead, since that module already owns the ATR stop-loss math it needs). No new DB tables,
no new scheduler jobs — both shipped parts are read-only over already-persisted engine
outputs, computed on demand on every Portfolio/Watchlist/Stock Detail page render.

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

## C. Cross-Engine Alert Referee (planned)

Will generalize `alert_referee_engine.py`'s existing calibrated-probability veto (currently
piloted on Trap Monitor only — see `AGENTS.md`'s "Alert Confidence Referee" entry) to also
score the combined Part A/B output, reusing historical accuracy this app already tracks
instead of building new tracking. `train_referee_model()`/`evaluate_alert()` already take an
`engine: str` parameter for exactly this kind of extension. Not yet built — documented here in
advance since it's the third of four pieces in this pipeline; will be fleshed out in this file
when implemented.

## D. Recommendation Risk/Reward Gate (planned)

Will require a minimum risk/reward ratio (computed from this app's existing ATR-based
stop-loss math in `position_sizing.py`) before labeling a ticker a "Buy Recommendation" from
Part A/B. Deliberately **not** a Kelly Criterion calculation — this app already rejected Kelly
sizing in favor of fixed-fractional ATR sizing (see the Position Sizing glossary entry) because
Kelly's "optimal" sizing is too aggressive when the edge estimate isn't reliable; this reuses
that same rationale rather than reopening it. Not yet built — documented here in advance;
will be fleshed out in this file when implemented.
