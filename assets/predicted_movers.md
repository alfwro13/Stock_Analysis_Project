# Predicted Movers

Predicted Movers ranks tickers by ML-**predicted** forward price move — the midpoint of the
10-trading-day-forward quantile regression price band (`price_q10`/`price_q90`) versus current
price — as opposed to the Relative Strength Leaders report (`/relative-strength-leaders`), which
ranks by *actual* historical movement. A second, linked page tracks how reliable those
predictions have actually been.

Page routes: `GET /predicted-movers` (leaderboard), `GET /predicted-movers/accuracy` (linked
from the Reports hub, `/reports`, via the leaderboard page)
Engine: `predicted_movers_engine.py`
Scheduler job: none dedicated — piggybacks on the existing `ml_inference_job`
DB table: `predicted_movers_history`

---

## 1. No scheduled job of its own

Unlike Pairs Spread Monitor, the leaderboard is a pure **on-demand live SELECT** — it reads
already-computed `quant_signals.price_q10`/`price_q90` (written nightly by
`ai_prediction_engine.score_quantile_predictions()`, called from `run_ml_inference()` /
`ml_inference_job`), so there's no scan to trigger and no "Run Scan Now" button. Both scopes
(Portfolio + Watchlist and Universe) are computed identically on every page load.

The accuracy-tracking writes — logging today's prediction and resolving past predictions whose
horizon has elapsed — piggyback directly on `run_ml_inference()`, called right after
`score_quantile_predictions()`:

```python
score_quantile_predictions(tickers)
from predicted_movers_engine import backfill_actual_outcomes, log_predictions
resolved = backfill_actual_outcomes()
logged = log_predictions()
```

This must happen the same run `score_quantile_predictions()` executes — `quant_signals.price_q10`/
`price_q90` are overwritten in place with no history retained, so if a day's prediction isn't
logged into `predicted_movers_history` before the next run overwrites it, that day's prediction
is lost forever, not just delayed.

Represented in the Workflow Monitor: `ml_inference_job`'s `JOB_GRAPH` entry now also `produces`
`predicted_movers_history`; the two pages' read surface is covered by the
`predicted_movers_leaderboard_source` `non_job` entry (mirrors `monte_carlo_source`).

## 2. Scope: Portfolio + Watchlist vs Universe (leaderboard only)

The leaderboard page has a scope toggle, same UX as Pairs Spread Monitor:

- **Portfolio + Watchlist** — `db_helpers.get_portfolio_watchlist_tickers()`, a shared helper
  extracted from Pairs Spread Monitor's own inline union logic (`accounts_engine.get_combined_holdings().keys()`
  ∪ `database.get_watchlist_tickers()`, ignored-ticker-filtered). Both engines now call this one
  function rather than each maintaining their own copy.
- **Universe** — the full market universe via `db_helpers.get_universe_tickers()`.

The **accuracy page has no scope toggle** — `predicted_movers_history` is only ever populated
for Portfolio + Watchlist tickers (`log_predictions()` defaults to
`get_portfolio_watchlist_tickers()`, never the full universe list already in scope inside
`run_ml_inference()`). This is a deliberate asymmetry: tracking prediction accuracy for the
entire ~4,000-ticker universe every day would be a large, mostly-unused write volume for data
nobody is actually holding or watching.

## 3. Leaderboard ranking

`get_leaderboard(scope, sort_mode, limit=200)`:

1. Resolve scope tickers.
2. Pull each ticker's **latest** `quant_signals` row with non-null `price_q10`/`price_q90` (the
   same inline "latest row per ticker" correlated-subquery idiom used throughout the codebase —
   `ai_prediction_engine.py`, `page_routes.py`, `market_pulse.py`).
3. Resolve current price via `accounts_engine.current_price_map()` (AGENTS.md rule 16 — never
   re-derive current price from `quant_signals.close_price`, which is the price *at prediction
   time*, not now). Rows with no resolvable current price are dropped.
4. `predicted_mid = (price_q10 + price_q90) / 2`; `predicted_move_pct = (predicted_mid -
   current_price) / current_price * 100`.
5. Sort: **Gainers** — highest `predicted_move_pct`. **Losers** — lowest (most negative).
   **Movers** (default) — highest `abs(predicted_move_pct)`.

No results table is persisted — this is recomputed on every request, since it's a cheap read
over already-computed columns rather than an expensive pairwise computation like Pairs Spread
Monitor's correlation matrix.

## 4. Prediction accuracy tracking

`predicted_movers_history` — one row per (ticker, predicted_date):

| Column | Meaning |
|---|---|
| `predicted_date` | The `quant_signals.date` the prediction was scored on |
| `close_price` | `quant_signals.close_price` at prediction time (the model's input price) |
| `price_q10` / `price_q90` | The predicted band, snapshotted at logging time |
| `target_date` | ~10 *trading* days forward of `predicted_date` |
| `actual_price` / `actual_date` | First `quant_signals` close on/after `target_date`, once resolved |
| `direction_correct` | `NULL` until resolved; `1`/`0` — did the actual price move the same direction as the predicted midpoint? |
| `within_band_correct` | `NULL` until resolved; `1`/`0` — did the actual price land within `[price_q10, price_q90]`? |

**`target_date` computation** (`_target_date()`): `numpy.busday_offset(predicted_date,
PREDICTION_HORIZON_DAYS, roll="forward")` — a Mon-Fri business-day approximation with no
exchange-holiday awareness. This is an accepted simplification: the only consumer is
`backfill_actual_outcomes()`'s "first `quant_signals` close on/after `target_date`" lookup,
which self-corrects for the few-day slack a missed holiday introduces (it just finds the next
available close after the target, whatever day that turns out to be).

**Logging** (`log_predictions()`) — `INSERT OR IGNORE` on `UNIQUE(ticker, predicted_date)`, so a
same-day rerun of `ml_inference_job` is a safe no-op.

**Resolving** (`backfill_actual_outcomes()`) — every run, scans the *entire* unresolved set
(`WHERE direction_correct IS NULL AND target_date <= today`), not just the newest rows, per
AGENTS.md's catch-up-loop discipline: a missed scheduler run must not permanently strand a
day's prediction unresolved. For each unresolved row, resolves the actual outcome via
`SELECT close_price FROM quant_signals WHERE ticker=? AND date>=? ORDER BY date ASC LIMIT 1`
(mirrors `bubble_radar_engine._backfill_outcomes()`'s resolution idiom — reads `quant_signals`
directly rather than parquet, since these tickers are already tracked there).

**Scoring timeline:** a prediction logged today cannot be graded until ~10 trading days later.
The accuracy page shows every row — resolved or pending — with `resolved`/`pending` counts
alongside the accuracy percentages (computed over resolved rows only), rather than hiding
in-flight predictions. For the first ~10 trading days after this feature first runs, every
ticker will show 0 resolved predictions — expected, not a bug.

## 5. Company Name Enrichment

Like Pairs Spread Monitor, company names are not stored on either table — both
`GET /api/predicted-movers/leaderboard` and `GET /api/predicted-movers/accuracy` join them in at
read time via `db_helpers.get_company_names()`.

## 6. Deliberate simplifications

- **No model-retraining feedback loop.** `predicted_movers_history` is a genuine
  prediction-vs-actual dataset that could, in principle, feed back into calibrating or
  retraining `train_quantile_models()` — but this was explicitly scoped out when the feature was
  built. Revisit once a few weeks of resolved predictions have accumulated.
- **No alerting.** Unlike Pairs Spread Monitor's Portfolio + Watchlist scope, Predicted Movers
  never fires a notification — it's a leaderboard/scorecard the operator checks, not a
  condition-severity alert engine, so it doesn't participate in the `alert_state` dedup/cooldown
  model (AGENTS.md rule 19).
