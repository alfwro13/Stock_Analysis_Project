# Stable Shortlist

Stable Shortlist turns two nightly rankings into slow-moving weekly shortlists. A strict top-N
re-rank whipsaws on small score changes; this keeps a list for a week, swaps only a few names at a
time, records why every name was in or out, and judges the lists only on what happens after each
snapshot. It makes suggestions only — the app has no order execution.

Page: `GET /predicted-movers`, tabs **ML Upside Shortlist** and **Quant Score Shortlist**
Engines: `stable_shortlist_engine.py` (selection, snapshots, outcome resolution, job entry point) and `stable_shortlist_reads.py` (page/API payload, forward-only track record, Portfolio/Watchlist column values)
Scheduler job: `stable_shortlist_job` (Settings → Stable Shortlist card; default Friday 19:30 local, enabled)
DB tables: `stable_shortlist_snapshots`, `stable_shortlist_members`
Endpoints: `GET /api/predicted-movers/shortlist`, `POST /api/predicted-movers/shortlist/run` (see `assets/api_reference.md` §27)
Optional Portfolio/Watchlist columns: ML Upside / Quant Score Shortlist Member and Rank (see `assets/configurable_columns.md`)

---

## 1. Four lists

Two signals × two scopes, each with its own membership, snapshots and track record:

| Signal | Ranks by | Qualifies when |
|---|---|---|
| **ML Upside** (`ml_upside`) | Signed predicted upside, `((Q10 + Q90) / 2) / reference_close − 1`, from the existing 10-trading-day quantile models | Upside is positive. The list is never padded with non-positive names, so it can be shorter than the list size |
| **Quant Score** (`quant_score`) | The nightly 0–100 composite quant score | Score is at least **Min Quant Score** (default 50) |

Scopes are **Portfolio** (`db_helpers.get_portfolio_tickers()`) and **Watchlist**
(`get_watchlist_only_tickers()` — Watchlist tickers you do not hold). A ticker in both belongs to
Portfolio only. The two signals are never blended into one score.

The reference close is `quant_signals.close_price` on the signal date — the completed close the
prediction was made from, in the same quote units as `price_q10`/`price_q90` (so pence-quoted
tickers need no conversion). It is not the live price, and the live leaderboard is never called.

## 2. Selection (turnover-controlled top-k)

`select_members()` is a pure function of the ranked candidates and the previous members.

1. Rank eligible candidates by signal, highest first; ties break alphabetically by ticker.
2. Carry over previous members that are still eligible. A member that is no longer eligible (stale
   signal, non-positive upside, below the minimum score, left the Portfolio/Watchlist, no signal) is
   dropped (`dropped_ineligible`); over-cap sectors (`dropped_sector_cap`) and a shrunken list size
   (`dropped_rank`) trim the worst-ranked carried members. These forced exits do **not** use up the
   swap allowance.
3. Fill any free slots with the best-ranked non-members whose sector has room (`entered`); a name
   skipped only because its sector is full is `blocked_sector_cap`.
4. Swap: for the best-ranked non-members in turn, replace the worst-ranked member that ranks below
   them, up to **Max Swaps per Snapshot** swaps. A member can only be swapped out once it has been on
   the list for **Minimum Hold** snapshots (a protected member that would otherwise have been
   swapped is `blocked_hold_thresh`). A swap into a full sector needs a partner from that sector. Names
   that entered this cycle are never swapped out in the same cycle.

Defaults (**Calm**): list size 10, max swaps 2, minimum hold 2 snapshots, sector cap 2. Names with no
sector on file share one `Unknown` group with the same cap.

## 3. Snapshots

- One snapshot per signal, scope and **ISO week** (`UNIQUE(signal_type, scope, cycle_key)`). A second
  run in the same week writes nothing for lists that already have one.
- A snapshot stores the config in force, the signal version (ML: the quantile model files' save time;
  Quant: the score date), the decision time (UTC) and **every tracked candidate** with its rank,
  eligibility, reason and the previous snapshot's rank (rank movement is kept separately from
  membership changes).
- **Signal Max Age** (default 4 calendar days): a signal older than this at snapshot time is stale,
  cannot qualify and is never tracked for outcomes. If a list has no fresh signal at all, or no
  tickers, **nothing is written** — a failed overnight job never wipes a shortlist; the skip is named
  in the job notification and a later run that week can still take the snapshot.
- Snapshots are immutable. Corrected source data produces next week's snapshot, not a rewrite.

## 4. Forward-only track record

History is never rebuilt backwards: the training pipeline does not persist dated out-of-sample
predictions, so replaying today's model on past dates would use information that did not exist.

`resolve_outcomes()` runs at the start of every job run and scans the **whole** unresolved backlog
(a missed run strands nothing, per the catch-up rule). For each tracked row whose `target_date`
(≈10 trading days after the signal date) has passed, the outcome is the first `quant_signals` close on
or after it (`db_helpers.get_first_close_on_or_after()`, shared with
`predicted_movers_engine.backfill_actual_outcomes()`), giving `forward_return_pct` from the frozen
reference close. ML rows also get `direction_correct` and `within_band_correct`.

The page compares each snapshot's **members' equal-weight average return** with the average of the
snapshot's **other tracked names**, and shows the share of snapshots where members were ahead.
Weekly snapshots overlap (10-session windows, a new snapshot every 5), so the figures are not
independent trades. Predicted Movers' own daily accuracy (`predicted_movers_history`) is unchanged.

## 5. Scheduling and Settings

`stable_shortlist_job` runs on the weekday and local time set in **Settings → Stable Shortlist**. The
default Friday 19:30 sits after the Quantamental Analysis Engine (default 18:00, which writes the
composite scores) and the morning ML Inference job (default 01:30, which writes the quantile bands);
the Workflow Monitor flags a backwards order. `JOB_GRAPH["stable_shortlist_job"]` consumes
`quant_signals`, `ml_predictions`, `portfolio` and `stock_signals` and produces
`stable_shortlist_snapshots`.

The card has: enable, snapshot day, run time, a **Preset** (Calm / Balanced / Active / Custom), List
Size, Max Swaps per Snapshot, Minimum Hold, Sector Cap, Signal Max Age and Min Quant Score, plus Run
Now. Selecting a preset fills the four turnover fields (which stay editable; editing one switches the
preset to Custom):

| Preset | List size | Max swaps | Minimum hold | Sector cap |
|---|---|---|---|---|
| Calm (default) | 10 | 2 | 2 | 2 |
| Balanced | 10 | 3 | 1 | 3 |
| Active | 10 | 5 | 1 | 4 |

Config lives in `SCHEDULING.STABLE_SHORTLIST` (`ENABLED`, `DAYS`, `TIME`, `TOPK`, `N_DROP`,
`HOLD_THRESH`, `SECTOR_CAP`, `MAX_SIGNAL_AGE_DAYS`, `MIN_QUANT_SCORE`); the values are bounded at the
settings API. The job reports through the standard scheduled-job status notification and fires no
alerts.

## 6. Limits

- Forward-only: a new install shows no track record until snapshots are at least ~10 trading days old.
- The Quant Score list is only as stable as an integer 0–100 score allows; many ties are broken
  alphabetically, which is deterministic but not meaningful.
- Sectors come from `stock_signals.sector`; a missing sector is an `Unknown` group, not an error.
- No basket backtest: evaluating a shortlist as a rebalanced basket over longer history belongs to the
  parked Strategy Backtester idea.
