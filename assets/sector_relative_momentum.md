# Sector-Relative Momentum

Sector-Relative Momentum answers "is this stock beating its own sector?". For every equity it compares the stock's cumulative return over the last 63 or 126 trading sessions with the median return of its sector peers, and ranks the stock inside that peer group. It measures strength against **peers**, not against the market — that is what `rel_strength_5d` / `rel_strength_20d` (ticker return minus SPY return) already do. It is not a forecast and not a conviction score.

Page route: `GET /sector-relative-momentum` (Reports hub card)
Engine: `sector_relative_momentum_engine.py`
Scheduler job: `sector_relative_momentum_job` (Settings → Sector-Relative Momentum card; default Mon–Fri 19:20 local, enabled)
DB table: `sector_relative_momentum_results`
Shared helper: `price_history_helpers.session_window_returns()`
Endpoints: `GET /api/sector-relative-momentum/results`, `POST /api/sector-relative-momentum/run` (see `assets/api_reference.md` §31)

---

## 1. Peer pool and cohorts

- **Peer pool:** every `stock_signals` row whose `quote_type` classifies as an Equity (`fundamentals_helpers.get_instrument_type`), with a known sector and quote currency, not on the Ignored Tickers list. ETFs, funds and indices are never ranked and never counted as peers.
- **Reference pool is independent of the rows displayed.** Peers always come from the whole pool, so adding a ticker to the Watchlist never changes anyone else's score. Portfolio + Watchlist is only a display filter on top.
- **Cohort = sector x currency.** The sector is Yahoo's sector from `stock_signals.sector`; `Unclassified`, `Unknown`, `None` and blank count as missing. The currency is the quote currency with LSE pence and pounds (GBp/GBX/GBP) collapsed into one `GBP` bucket (`utils.normalize_currency_bucket`), so returns are comparable without FX conversion and LSE and US calendars are never mixed.
- **Minimum 5 other peers.** A cohort of fewer than 6 eligible members scores nobody; each member is reported as `small_cohort` with its peer count.

## 2. Calculation

For each cohort and window (63 or 126 sessions):

1. **Cohort calendar.** A date counts as a session only if at least 50% of the cohort has a close that day. The window is the last `sessions + 1` such dates (`sessions` returns), and the last one is the as-of date. A date only a few members traded never becomes the as-of date.
2. **Eligibility.** A member needs closes on both window endpoints and on at least 90% of the window's dates. Others are reported as `insufficient_history`; nothing is back-filled from later data.
3. **Own return** `R_i = close(as_of) / close(start) - 1`, from the adjusted daily closes already in `data/historical/*.parquet`.
4. **Leave-one-out peer return** `R_peers = median of the other eligible members' R`, excluding the stock itself. The median keeps one extreme performer from skewing every peer return. (Upstream FinRL-X includes the asset in its own group return; excluding it avoids self-influence in small cohorts.)
5. **Relative return** `relative_return_pp = 100 x (R_i - R_peers)` — percentage points.
6. **Rank and percentile.** Rank 1 is the strongest, ties broken by ticker. Percentile is `(average ascending rank - 1) / (n - 1) x 100`, so ties share a percentile and 100 is strongest.

No price data is fetched by the job. `_load_close()` skips a ticker with no parquet, then reads the cached closes through the shared `price_history_helpers.load_daily_close(cache_only=True)`; the existing cache-refresh coordinator handles stale files. A ticker with no cached history is `no_history`.

## 3. Storage and freshness

`sector_relative_momentum_results`, primary key `(ticker, window_sessions, as_of_date)`. Scored rows are stored for the whole pool; unavailable-reason rows (`no_metadata`, `not_equity`, `no_sector`, `no_currency`, `no_history`, `insufficient_history`, `small_cohort`) only for Portfolio/Watchlist tickers. A same-day rerun replaces rows. After each run rows more than 30 days older than the newest as-of date are deleted. Each run stamps a short `input_revision` digest of the window's scored inputs.

Readers always take the latest `as_of_date` per ticker and window and show it, so a value from an earlier session is never presented as today's. A ticker the job has not stored a row for shows `not_computed`, never a neutral zero.

## 4. Where it is shown

- **Reports → Sector-Relative Momentum** — scope toggle (Portfolio + Watchlist / Universe) and window toggle (63 / 126 Sessions).
- **Portfolio and Watchlist optional columns** (category "Sector-Relative Momentum"): `sector_rel_mom_63`, `sector_rel_rank_63`, `sector_rel_mom_126`, `sector_rel_rank_126` — see `assets/configurable_columns.md`.
- **AI prompt** — `ai_engine._get_sector_peer_context()` now reads the persisted 63-session standing (rank, percentile, LEADER / MID-PACK / LAGGARD, strongest and weakest cohort members) instead of ranking `rel_strength_20d` itself. One calculation, three surfaces, identical numbers.

The Reports card "Relative Strength Leaders" is unrelated: it is an RSI / MACD screen of stocks above their 50-day SMA.

## 5. Scheduling and the Workflow Monitor

`JOB_GRAPH["sector_relative_momentum_job"]` consumes `historical_parquet` and `stock_signals` (written by the Quantamental Analysis Engine, default 18:00) and produces `sector_relative_momentum_results`. The default 19:20 run time sits after that job; the Workflow Monitor flags a backwards order or an overlap with a still-running upstream. The Settings card (`#sector-relative-momentum-card`) sets enable, days and time, and has a Run Now button. Notifications use the standard scheduled-job status row; the job fires no alerts.

## 6. Limits

- The daily cache is about 2 years deep, which comfortably covers 126 sessions plus warm-up; a ticker with a shorter history is `insufficient_history`.
- Sector and currency are today's classification, so a window covering a reclassification uses the new sector throughout.
- Sectors are Yahoo's coarse GICS-style groups; a large sector can hold very different businesses.
- Cohorts are formed from tickers the app already tracks (`stock_signals`), not the whole listed market.
- The peer return is a **median** (the plan proposed an equal-weighted mean; the operator chose the median because a single extreme performer, for example a penny stock up several hundred percent, would otherwise lift every member's peer return). Because the median of the others shifts slightly as each stock is left out, rank follows relative return, which can differ marginally from the order of own returns.
- A member whose last cached bar is older than the cohort's as-of date (halted, delisted, or a stale cache) is dropped as `insufficient_history` rather than being carried forward.
