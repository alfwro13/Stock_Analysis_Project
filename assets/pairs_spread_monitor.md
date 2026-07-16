# Pairs Spread Monitor

The Pairs Spread Monitor is a statistical arbitrage / mean-reversion signal over pairs of
tickers. Every other alert engine in this app (Trap Monitor, Bubble Radar, AI Contagion,
Crash/Moonshot) evaluates one ticker at a time; this is the only engine that evaluates the
*relationship* between two tickers.

Page route: `GET /pairs-spread` (linked from the Reports hub, `/reports`)
Engine: `pairs_spread_engine.py`
Scheduler job: `pairs_spread_monitor_job` (Portfolio + Watchlist scope only)
DB table: `pairs_spread_results`

---

## 1. Scope: Portfolio + Watchlist vs Universe

The page has a scope toggle, and every scan (scheduled or on-demand) is tagged with one:

- **Portfolio + Watchlist** (`SCOPE_PORTFOLIO_WATCHLIST`) — `accounts_engine.get_combined_holdings().keys()`
  ∪ `database.get_watchlist_tickers()`. Small, fast, runs on the nightly schedule, fires alerts.
  Mirrors `insider_engine.py`'s portfolio+watchlist union — not Trap Monitor's proxy-basket
  pattern, since Pairs Spread Monitor has no concept of a fixed proxy basket.
- **Universe** (`SCOPE_UNIVERSE`) — the full market universe via `db_helpers.get_universe_tickers()`
  (the same `market_universe` table the nightly Quant Scan covers, ~thousands of tickers).
  **On-demand only** — no scheduled job, triggered from the page (`POST /api/pairs-spread/run-universe`
  → `scheduler_jobs.run_pairs_spread_universe_scan()`, a plain background task, not an
  APScheduler job). Represented in the Workflow Monitor as the `pairs_spread_universe_source`
  `non_job` entry, not a schedulable job. **Never fires alerts** — see §4.

`pairs_spread_results.scope` keeps the two scopes' rows from clobbering each other: each scan
does `DELETE FROM pairs_spread_results WHERE scope = ?` before inserting, and `pair_key` is
itself scope-prefixed (`"{scope}:{ticker_a}:{ticker_b}"`) so the same pair can independently
exist in both scopes' result sets without a primary-key collision.

## 2. Pairing

1. **Currency bucketing** — each ticker's currency is read from `stock_signals.currency` via
   the shared `db_helpers.get_ticker_currency_map()` (also used by Trap Monitor), normalized so
   `GBp` (LSE pence) and `GBP` collapse to the same bucket. Only tickers in the same currency
   bucket are ever paired; a ticker with no `stock_signals` row (not yet scanned) is dropped
   from the universe entirely.
2. **Correlation filter** — within each currency bucket, daily-return correlation is computed
   via `xray_engine.fetch_close_returns_from_parquet()` (the same parquet-backed helper
   `xray_risk_cache_job` uses — reused rather than re-derived, per the shared-helper rule) over
   the trailing 252-day window, then `.corr(min_periods=60)` — vectorized across the whole
   bucket in one call. A pair survives only if `abs(correlation) >= CORRELATION_THRESHOLD`
   (config, default 0.7).
3. **Per-bucket price cache** — for the surviving pairs' spread math (§3), each ticker's own
   close series is loaded from parquet **at most once per bucket**, cached in a plain dict and
   reused across every pair it appears in (`PairsSpreadEngine._scan_bucket`'s `price_cache`).
   This matters a lot at Universe scale: a naive per-pair reload would mean O(pairs) parquet
   reads rather than O(tickers) — for a large single-sector currency bucket where correlations
   run high (common during broad rallies/selloffs), the number of surviving pairs can be many
   times the number of tickers, so re-reading per pair rather than per ticker would turn an
   already-expensive on-demand scan into an intractable one.

## 3. Spread Z-Score

For each surviving pair `(a, b)`, using each ticker's (cached) trailing-252-day close series:

1. Align on common trading dates.
2. Compute the **log-spread**: `log(close_a) - log(close_b)`. A log ratio (not a raw price
   difference) is currency-unit-invariant — it doesn't matter whether either leg happens to be
   quoted in pence or pounds, only how the two prices move *relative* to each other, so no GBX
   pence-to-pounds conversion is needed anywhere in this engine.
3. Take that window's own mean and standard deviation (not a shorter rolling sub-window — the
   z-score is against the pair's trailing-year historical relationship).
4. `z = (last_log_spread - mean) / std`.

`direction` records which leg is "rich": `"{a} rich vs {b}"` when `z > 0`, else `"{b} rich vs {a}"`.

## 4. Storage & Company Name Enrichment

`pairs_spread_results` holds **one row per (scope, pair), latest scan only** — full-replaced
per scope on every scan (not upserted), so a pair that drops out of the correlation threshold
disappears from that scope's results rather than lingering with a stale `scan_ts`. The
underlying log-spread/price time series is never persisted (heavy time-series data belongs in
Parquet, not SQLite, per the app's dual-storage rule) — `build_chart_series()` recomputes it on
demand from parquet for the chart endpoint.

Company names are **not** stored on `pairs_spread_results` — `GET /api/pairs-spread/results`
joins them in at read time via `db_helpers.get_company_names()` (batch query against
`stock_signals.company_name`), consistent with how the rest of the app avoids duplicating
slowly-changing reference data into every table that references a ticker.

## 5. Alerting (Portfolio + Watchlist scope only)

`run_pairs_spread_monitor_job()` (`scheduler_jobs.py`) runs a `SCOPE_PORTFOLIO_WATCHLIST` scan,
then for every pair with `abs(zscore) >= ZSCORE_THRESHOLD` (config, default 2.0) calls
`IntradayOrchestrator._evaluate_alert_gate("PairsSpreadMonitor", pair_key, abs(zscore), reason, conn)`
before dispatching via `notification_engine.notify("pairs_spread_alert", ...)`.

- **Composite key:** `alert_state`'s primary key is `(engine, ticker)`, so a pair-scoped
  condition uses the scope-prefixed `pair_key` (`"{scope}:{ticker_a}:{ticker_b}"`) in the
  `ticker` column — the same composite-key pattern Position Targets (HoldingLimit) uses for its
  `{account_id}:{ticker}:{direction}` key.
- **Severity value:** `abs(zscore)` is passed as the gate's `current_price` argument — a genuine
  numeric magnitude, not `None`, so the standard worsened/recovered/cooldown model can
  distinguish real widening from a stale re-run of yesterday's result (see AGENTS.md rule 19).
  This engine uses the **default** gate model, not the once-per-day exception reserved for
  static thresholds like Position Targets.
- **Notification source:** `pairs_spread_alert` (job `pairs_spread_monitor_job`), routed
  log/in-app by default — Nextcloud Talk is opt-in via Settings → Notification Settings, same
  default as Trap Monitor/Bubble Radar/AI Contagion.
- **Universe scope never alerts.** `run_pairs_spread_universe_scan()` calls `engine.run_scan()`
  and saves results; it never touches the alert gate or `notify()`. The dedup/cooldown model
  assumes a recurring scan cadence (that's what "cooldown" and "re-arm" are measured against) —
  a one-off manually-triggered scan the operator may not repeat for weeks doesn't fit that
  model, and firing alerts off a scan nobody asked to be alerted from would be surprising.

## 6. Configuration

Settings → Tools → **Pairs Spread Monitor** (`templates/settings/_system.html`,
`pairs-spread-monitor-card`) — this card governs the scheduled Portfolio + Watchlist scan only;
the Universe scan has no schedule/config, it's triggered directly from the page:

| Field | Config key | Default |
|---|---|---|
| Enabled | `SCHEDULING.PAIRS_SPREAD_MONITOR.ENABLED` | off |
| Active Days | `SCHEDULING.PAIRS_SPREAD_MONITOR.DAYS` | Mon–Fri |
| Scan Time (local tz) | `SCHEDULING.PAIRS_SPREAD_MONITOR.TIME` | `19:10` |
| Correlation threshold | `SCHEDULING.PAIRS_SPREAD_MONITOR.CORRELATION_THRESHOLD` | `0.7` |
| Z-score threshold | `SCHEDULING.PAIRS_SPREAD_MONITOR.ZSCORE_THRESHOLD` | `2.0` |
| Alert cooldown (min) | `NOTIFICATIONS.PAIRS_SPREAD_MONITOR_ALERTS.COOLDOWN_MINUTES` | `120` |
| Retrigger % move | `NOTIFICATIONS.PAIRS_SPREAD_MONITOR_ALERTS.RETRIGGER_PERCENT` | `15.0` |
| Re-arm % recovery | `NOTIFICATIONS.PAIRS_SPREAD_MONITOR_ALERTS.REARM_PERCENT` | `50.0` |

The correlation/z-score thresholds are shared by both scopes (one engine config, no separate
Universe-specific thresholds) — keeping this simple was a deliberate choice over adding a
second set of tunables for a scope that's used occasionally and on demand.

The scheduled scan runs 10 minutes after `xray_risk_cache_job`'s 19:00 slot purely to avoid
resource contention on the shared parquet read path — there is no data dependency between the
two jobs (Pairs Spread Monitor computes its own correlation matrix independently, since its
Portfolio + Watchlist universe includes Watchlist tickers that `xray_correlation_matrix` —
holdings-only — does not cover, and its Universe scope isn't something `xray_engine` covers at
all).

## 7. Chart

Clicking a pair row opens a Bootstrap modal (`#psm-chart-modal`) — not an inline block on the
page — showing both tickers' prices **indexed to 100 at the start of the trailing window**
(`GET /api/pairs-spread/chart/{ticker_a}/{ticker_b}`, `build_chart_series()`), alongside a small
correlation/z-score stat readout. Indexed-price overlay was chosen over the raw log-spread line
(the original design) because two prices moving apart is immediately legible at a glance, where
an abstract spread value/z-score line requires the reader to already understand the metric.

Two implementation details worth knowing if touching this chart again:
- The chart only renders **after** the modal's Bootstrap `shown.bs.modal` event fires, not on
  click — Plotly cannot size correctly into a `display: none` container, which a Bootstrap
  modal's body is until it's actually shown.
- Before rendering into the modal's chart div for a *different* pair, the JS calls
  `Plotly.purge()` on it first. Reusing the same target element across multiple `Plotly.newPlot()`
  calls without purging first corrupts Plotly's internal per-element bookkeeping (it was left
  out of sync by a raw `innerHTML` reset), which reproduced as: first pair's chart renders fine,
  every subsequent pair click renders nothing into the same element.

## 8. Deliberate Simplifications

- **No cointegration testing.** A formal Engle-Granger cointegration test (or an OLS-derived
  hedge ratio) would be more statistically rigorous than a plain correlation-threshold + spread
  z-score, but adds real complexity (a new `statsmodels` dependency, more compute per pair, more
  tests to maintain) for a personal-portfolio-sized pair set where the surviving pairs are few
  enough to sanity-check manually. Revisit if false-positive rate in practice warrants it.
- **Same-currency pairs only.** No cross-currency pairing, and therefore no FX conversion
  anywhere in this engine — a cross-currency pair's ratio would otherwise conflate genuine
  equity-relationship divergence with FX-rate movement.
