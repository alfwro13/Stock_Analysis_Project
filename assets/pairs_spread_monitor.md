# Pairs Spread Monitor

The Pairs Spread Monitor is a statistical arbitrage / mean-reversion signal over pairs of
portfolio and watchlist tickers. Every other alert engine in this app (Trap Monitor, Bubble
Radar, AI Contagion, Crash/Moonshot) evaluates one ticker at a time; this is the only engine
that evaluates the *relationship* between two tickers.

Page route: `GET /pairs-spread`
Engine: `pairs_spread_engine.py`
Scheduler job: `pairs_spread_monitor_job`
DB table: `pairs_spread_results`

---

## 1. Universe & Pairing

1. **Ticker universe** — `accounts_engine.get_combined_holdings().keys()` ∪
   `database.get_watchlist_tickers()`, filtered through `utils.is_excluded_from_yahoo_fetch()`
   (excludes synthetic `TBILL-*`/`PENSION-*` tickers and the Settings-page Ignored Tickers
   list). Mirrors `insider_engine.py`'s portfolio+watchlist union — not Trap Monitor's
   proxy-basket pattern, since Pairs Spread Monitor has no concept of a fixed proxy basket.
2. **Currency bucketing** — each ticker's currency is read from `stock_signals.currency`
   (batch query), normalized so `GBp` (LSE pence) and `GBP` collapse to the same bucket. Only
   tickers in the same currency bucket are ever paired; a ticker with no `stock_signals` row
   (not yet scanned) is dropped from the universe entirely.
3. **Correlation filter** — within each currency bucket, daily-return correlation is computed
   via `xray_engine.fetch_close_returns_from_parquet()` (the same parquet-backed helper
   `xray_risk_cache_job` uses — reused rather than re-derived, per the shared-helper rule) over
   the trailing 252-day window, then `.corr(min_periods=60)` — vectorized across the whole
   bucket in one call. A pair survives only if `abs(correlation) >= CORRELATION_THRESHOLD`
   (config, default 0.7).

## 2. Spread Z-Score

For each surviving pair `(a, b)`:

1. Load each ticker's own daily closes from `data/historical/*.parquet` (via
   `data_engine.load_or_fetch_daily_history`), aligned on common trading dates, trailing 252
   days.
2. Compute the **log-spread**: `log(close_a) - log(close_b)`. A log ratio (not a raw price
   difference) is currency-unit-invariant — it doesn't matter whether either leg happens to be
   quoted in pence or pounds, only how the two prices move *relative* to each other, so no GBX
   pence-to-pounds conversion is needed anywhere in this engine.
3. Take that window's own mean and standard deviation (not a shorter rolling sub-window — the
   z-score is against the pair's trailing-year historical relationship).
4. `z = (last_log_spread - mean) / std`.

`direction` records which leg is "rich": `"{a} rich vs {b}"` when `z > 0`, else
`"{b} rich vs {a}"`.

## 3. Storage

`pairs_spread_results` holds **one row per pair, latest scan only** — the table is fully
replaced (`DELETE` then re-`INSERT`) on every scan, not upserted, so a pair that drops out of
the correlation threshold disappears from the results rather than lingering with a stale
`scan_ts`. The underlying log-spread time series is never persisted (heavy time-series data
belongs in Parquet, not SQLite, per the app's dual-storage rule) — `build_chart_series()`
recomputes it on demand from parquet for the chart endpoint.

## 4. Alerting

`run_pairs_spread_monitor_job()` (`scheduler_jobs.py`) runs the scan, then for every pair with
`abs(zscore) >= ZSCORE_THRESHOLD` (config, default 2.0) calls
`IntradayOrchestrator._evaluate_alert_gate("PairsSpreadMonitor", pair_key, abs(zscore), reason, conn)`
before dispatching via `notification_engine.notify("pairs_spread_alert", ...)`.

- **Composite key:** `alert_state`'s primary key is `(engine, ticker)`, so a pair-scoped
  condition uses a composite `pair_key` (`"{ticker_a}:{ticker_b}"`, alphabetically ordered) in
  the `ticker` column — the same pattern Position Targets (HoldingLimit) uses for its
  `{account_id}:{ticker}:{direction}` key.
- **Severity value:** `abs(zscore)` is passed as the gate's `current_price` argument — a genuine
  numeric magnitude, not `None`, so the standard worsened/recovered/cooldown model can
  distinguish real widening from a stale re-run of yesterday's result (see AGENTS.md rule 19).
  This engine uses the **default** gate model, not the once-per-day exception reserved for
  static thresholds like Position Targets.
- **Notification source:** `pairs_spread_alert` (job `pairs_spread_monitor_job`), routed
  log/in-app by default — Nextcloud Talk is opt-in via Settings → Notification Settings, same
  default as Trap Monitor/Bubble Radar/AI Contagion.

## 5. Configuration

Settings → Tools → **Pairs Spread Monitor** (`templates/settings/_system.html`,
`pairs-spread-monitor-card`):

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

The scan is scheduled 10 minutes after `xray_risk_cache_job`'s 19:00 slot purely to avoid
resource contention on the shared parquet read path — there is no data dependency between the
two jobs (Pairs Spread Monitor computes its own correlation matrix independently, since its
universe includes Watchlist tickers that `xray_correlation_matrix` — holdings-only — does not
cover).

## 6. Deliberate Simplifications

- **No cointegration testing.** A formal Engle-Granger cointegration test (or an OLS-derived
  hedge ratio) would be more statistically rigorous than a plain correlation-threshold + spread
  z-score, but adds real complexity (a new `statsmodels` dependency, more compute per pair, more
  tests to maintain) for a personal-portfolio-sized pair set where the surviving pairs are few
  enough to sanity-check manually. Revisit if false-positive rate in practice warrants it.
- **Same-currency pairs only.** No cross-currency pairing, and therefore no FX conversion
  anywhere in this engine — a cross-currency pair's ratio would otherwise conflate genuine
  equity-relationship divergence with FX-rate movement.
