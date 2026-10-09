# Strategy Backtester

The Strategy Backtester replays simple allocation rules over the daily prices this app already stores, so rules can be compared against each other with realistic trading mechanics: decisions from completed closes only, trades at the next eligible close, explicit cash, trading costs on both sides and weight drift between rebalances. It is a comparison tool, not a forecast and not advice, and the app has no order execution.

Page route: `GET /strategy-backtester` (Tools hub card)
Engines: `strategy_backtest_engine.py` (pure simulation), `strategy_backtest_strategies.py` (allocation rules), `strategy_backtest_data.py` (baskets, currency, price history), `strategy_backtest_runs.py` (run lifecycle and storage), `strategy_backtest_reads.py` (saved-run list, result payload, allocation history)
Front end: `templates/strategy_backtester.html`, `static/js/strategy_backtester.js` (basket, rules, run), `static/js/strategy_backtester_results.js` (results, charts, saved runs)
DB table: `strategy_backtest_runs`
Files: `data/strategy_backtests/<run_id>/` (run results), `data/backtest_history/` (extended price history)
Endpoints: `/api/strategy-backtester/*` (see `assets/api_reference.md` §32), router `api_routes_backtester.py`
Workflow Monitor: `strategy_backtester_source` (non-job entry; consumes `historical_parquet`, `portfolio`, `stable_shortlist_snapshots`, produces `strategy_backtest_runs`)

---

## 1. What a run is

Every run is a **fixed-current-basket test**: it takes the tickers chosen today and replays them over the past. Companies that failed or were dropped along the way are not in the basket, so absolute results flatter an investor. Use runs to compare rules on the same basket, not to estimate what you would have earned.

Basket sources:

- **Account Holdings** — the same account-scope picker as the Portfolio Optimizer. Held tickers are ticked, Watchlist tickers can be ticked in.
- **Stable Shortlist** — the members of the latest snapshot of one of the four lists (ML Upside / Quant Score x Portfolio / Watchlist). The list is frozen at that snapshot, so this tests today's members over the past. Shortlists only exist from their first snapshot onwards; nothing is back-filled.

**One currency per run.** Prices are never converted. Pence and pound quotes (GBp/GBX/GBP) share one bucket (`utils.normalize_currency_bucket`); the currency of a ticker is `stock_signals.currency`. A basket spanning several currencies is rejected and the page asks for one; tickers in other currencies are named as excluded. The benchmark must be quoted in the same bucket (default `SWDA.L` for GBP, `SPY` for USD, editable, or `none`). Dated-FX conversion is not part of the first version.

Between 2 and 40 tickers are allowed.

## 2. Strategies

| Strategy | Schedule | Notes |
|---|---|---|
| Buy-and-Hold Equal Weight | first purchase only | Quantities are held, so weights drift |
| Rebalanced Equal Weight | every Rebalance Cadence date | |
| Current Portfolio Weights (Rebalanced) | every cadence date | Today's weights of the selected held tickers, renormalised; account baskets only; skipped if none of the selected tickers is held |
| Inverse-Volatility Weighting | every cadence date | `w_i = (1/σ_i) / Σ(1/σ_j)` over the Training Lookback; zero or undefined volatility makes the decision unavailable |
| Tolerance-Band Rebalancing | **every session** | Start equal weight; trade back to equal weight when `max|w - 1/N|` exceeds the band (default 5 pp). The cadence setting does not apply |
| Rolling Steadiest Mix (Min-Variance) | every cadence date | Long-only SLSQP on the trailing lookback window only (Weight Cap default 20%, Cash Reserve default 0%) |
| Rolling Best Reward-for-Risk Mix (Max-Sharpe) | every cadence date | Same, maximising Sharpe with the configured `RISK_FREE_RATE`; unavailable when no allowed mix beats it |

The two rolling strategies call `portfolio_optimizer_engine.long_only_allocations()`, the same solver the Portfolio Optimizer page uses, so the maths and warnings are identical. A Weight Cap below `(1 - Cash Reserve) / N` is infeasible: those two strategies are skipped with the Optimizer's message while the others still run; if nothing can run the request is rejected.

If a rolling strategy cannot produce a valid mix on its **first** decision it is reported unavailable with the reason. Later failed decisions keep the existing holdings and are written to the decision log as `held`.

## 3. Simulation rules (`strategy_backtest_engine.py`)

- **Common start.** All selected strategies begin on the same day: the latest start any of them needs (a rolling strategy needs `lookback` sessions of history first). The run fails with an explanation if fewer than 30 sessions remain to test.
- **Decisions.** Made after the completed close of a session using only data up to and including that session. Cadence dates are the last session of each month, quarter or year; the last session of the data is never a decision.
- **Next-close execution.** A decision executes at the first later session on which **every** ticker in the basket has a real closing price (a holiday or an unexpected gap is skipped). Until then the old holdings keep earning. A newer decision replaces a still-pending one (logged as `superseded`). Each decision stores its decision and execution time as the UTC session close (`time_engine.session_close_utc`, which honours early closes; for baskets across exchanges the latest close).
- **Drift.** Quantities and cash are held between rebalances; weights are never reset by the simulation.
- **Costs.** Commission, spread and slippage in bps are charged on traded notional for buys and sells. The post-trade equity `V` solves `V + rate x Σ|w_i V - h_i| = E` (bisection), so the cost comes out of the budget, cash is never negative and money is conserved exactly. Cash weights are preserved, never normalised away.
- **Cash.** Earns the configured annual rate (default 0%), compounded per session (`(1 + r)^(1/252)`).
- **Gross vs net.** Each strategy is simulated twice with the same rules, once with costs and once with zero costs.
- **Benchmark.** A single buy-and-hold purchase through the same execution and cost path; it enters on the first session it has a real close on or after the strategies' first execution.
- **Metrics.** Total, gross and annualised return, volatility, Sharpe, and the Portfolio Tearsheet metrics (`performance_analytics_engine.compute_return_metrics()`: Sortino, Calmar, Omega, drawdown analytics, distribution and win/loss statistics) are computed from the daily equity series with at least 30 observations. Turnover per year is one-way (`Σ traded/2/portfolio value` per rebalance after the first purchase, divided by years).

## 4. Prices, calendars and missing data

- **Source.** Adjusted daily closes from the same cache as every other page (`data_engine.load_or_fetch_daily_history`), or the separate Extended History cache. A ticker's series is never spliced from the two (adjusted prices from different downloads do not join cleanly).
- **Calendar.** The union of all session dates, restricted to the window all tickers (and the benchmark) share. A date missing for a ticker is either a **known closure** (`time_engine.exchange_had_session` says the ticker's exchange was shut): the previous price is carried silently for valuation; or an **unexpected gap** (the exchange was open): the price is carried for valuation only, no trade is ever executed on a carried price, the gap is listed with its ticker and date, and the run is flagged **Incomplete**. Nothing is ever back-filled from later data and no price is invented.
- **Extended History.** The "Prepare Extended History" button fetches `5y` of daily data for the chosen tickers and the benchmark into `data/backtest_history/<ticker>.parquet` through `data_engine.prepare_daily_history()` (the same cleaning and saved Repair Data corrections as the nightly writer). It runs on the shared `cache_refresh_helpers` coordinator, so duplicate requests share one job and a failed attempt backs off. A run with "Extended" uses a ticker's prepared file only while it ends within 5 calendar days of the standard cache; otherwise it falls back to the standard cache and says so. The nightly refresh, other pages and `data/historical/` are untouched, and routine refreshes never truncate the extended file. It is part of the `data/` folder and therefore included by Backup & Recovery.

## 5. Storage, reproducibility and retention

SQLite `strategy_backtest_runs`: `id`, `state` (`queued` / `running` / `completed` / `failed`), `created_at` / `finished_at` (UTC), `config_json` (immutable settings incl. the config version, costs, cadence, lookback, risk-free rate, benchmark and current weights used), `basket_json` (tickers, currency, account or shortlist snapshot, skipped strategies, warnings), `inputs_json` (per-ticker source, actual start/end and sessions, exchanges, a SHA-256 digest of the aligned price matrix), `summary_json` (per-strategy metrics, period, shared window, issues, incomplete flag), `decisions_json` (decision log), `error`.

Parquet under `data/strategy_backtests/<run_id>/`: `equity.parquet` (net equity per strategy, `<id>|gross` equity, `<id>|cash` fraction, benchmark), `allocations.parquet` (`<id>|<ticker>` weights), `transactions.parquet`. Returns and drawdowns are derived when read, never stored. A completed run is never rewritten: corrected source data means a new run. The digest identifies the inputs but does not make overwritten prices replayable.

Retention: the newest **20** completed or failed runs are kept; older rows and their folders are deleted after each run, and any run can be deleted by hand. A queued or running run older than 30 minutes (for example after a restart) is marked failed as interrupted.

## 6. Lifecycle and threading

`POST /api/strategy-backtester/run` validates the request, saves a `queued` row and returns the run id immediately; the computation runs as a FastAPI background task (`strategy_backtest_runs.execute_run`), so no web thread is held. The page polls `GET /api/strategy-backtester/runs/{id}` (served by `strategy_backtest_reads.get_run`). Expected failures (single-currency violations, too little history) are stored as the run's `error`; any other exception is logged and shown generically. The run fires no notifications.

## 7. Defaults (all editable on the page)

Rebalance Cadence quarterly; Training Lookback 252 sessions; Starting Capital 10,000; Trading Costs preset Typical (commission 10 + spread 10 + slippage 5 = 25 bps per side; None = 0, Low = 5 + 3 + 2 = 10); Cash Return 0%; Tolerance Band 5 pp; Weight Cap 20%; Cash Reserve 0%; Benchmark auto; Price History standard. UK stamp duty is not modelled.

## 8. Known limits

- A fixed-current-basket test has survivorship bias.
- Standard history is about two years; with a 252-session lookback the rolling strategies are tested on about one year. Extended History (about five years) lengthens that.
- One quote currency per run; no FX conversion.
- Overlapping or very short test windows make differences between strategies statistically weak.
