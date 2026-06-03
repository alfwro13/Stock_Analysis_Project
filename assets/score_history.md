# 📈 Score History & Forward Returns

The Score History page is a lightweight **signal quality tracker** — not a backtest. It answers one practical question: *"When my scoring engine rated a stock STRONG BUY, did it actually go up over the next 3, 6, and 12 months?"*

It is explicitly **not** a traditional backtest. A backtest requires defined entry **and** exit rules. This scoring system is a screener — it flags candidates for a human to evaluate. There is no mechanical exit rule, and with 2–3 trades per month, a sample size large enough for statistically reliable conclusions takes at least a year to accumulate. The page is honest about this: all forward-return cells show "pending" until enough time has passed for each horizon to resolve.

---

## 🖥️ 1. How to Access It

The Score History page is a **separate page** (not a panel swap like X-ray).

**From the Portfolio page:**
1. Open **Portfolio**.
2. In the summary bar (next to the **🔮 X-ray** button), click **📈 Score History**.
3. The page opens pre-filtered to **My Portfolio** tickers.

**From the Watchlist page:**
1. Open **Watchlist**.
2. In the controls bar (next to the score range dropdown), click **📈 Score History**.
3. The page opens pre-filtered to **My Watchlist** tickers.

**Direct URL:**

| URL | View |
|---|---|
| `/score-history` | All tracked tickers |
| `/score-history?filter=portfolio` | Portfolio holdings only |
| `/score-history?filter=watchlist` | Watchlist tickers only |

The three filter tabs at the top of the page let you switch between these views without navigating away.

---

## 📊 2. What the Page Shows

### Filter Tabs

Three tabs: **All Tickers · My Portfolio · My Watchlist**. The active tab is highlighted. Clicking any tab reloads the page with that filter applied. The Portfolio and Watchlist tabs use the same ticker lists as `portfolio.json` and `watchlist.json`.

### Data Availability Banner

Always visible at the top. Two states:

**No data yet:**
> *Gathering data — score events will start appearing after the next daily scan completes.*

**Data accumulating:**
> *Tracking since YYYY-MM-DD — N score events recorded.*

Below the tracking date, three horizon chips show when each return window becomes resolvable:
- ✓ **green** — enough time has passed; returns for this horizon are being computed
- ⏳ **amber** — target date has not been reached yet; returns are pending

### Signal Performance Summary

A table grouped by signal bucket (STRONG BUY → TOXIC / AVOID). Each row shows:
- **Events** — total number of scoring instances at that signal level
- **Avg 3M / 6M / 12M Return** — mean percentage return across all events with a resolved forward price
- **(n=X)** — how many events contributed to the average (events still within the window are excluded)
- Cells show "— pending" until the horizon resolves, with a tooltip showing the date it will become available

> ⚠️ The summary averages become statistically meaningful only after 6+ months of data and a minimum of ~30 events per bucket. Treat early figures as directional, not conclusive.

### Score Events Table

One row per scoring instance, showing: **Ticker · Date · Score · Signal · Entry Price · 3M Return · 6M Return · 12M Return**.

- **Green** / **red** percentages for resolved returns
- **—** (grey dash) for pending returns, with a tooltip showing the expected resolution date
- All column headers are sortable (click to toggle asc/desc)
- Capped at the 500 most recent events for performance; the full count is shown in the section header

---

## 🗄️ 3. Data Architecture

### Score Logging (`score_history` table)

Every time `quant_signals.py` completes scoring a ticker (as part of the daily scan), it writes one row to the `score_history` table:

| Column | Type | Description |
|---|---|---|
| `ticker` | TEXT | Asset ticker symbol |
| `date` | TEXT | Last trading day in the parquet data (not wall-clock today — so a Friday scan run on Saturday is stamped Friday) |
| `score` | INTEGER | Final clamped score −100 to +100 |
| `signal` | TEXT | Human label (STRONG BUY, BULLISH / HOLD, etc.) |
| `close_price` | REAL | Entry price on the scoring date |

Primary key is `(ticker, date)` — re-running the scan on the same day upserts (refreshes) rather than duplicating.

Only tickers with sufficient historical data (≥ 21 days of OHLCV) are logged. Tickers with `INSUFFICIENT DATA` signal are excluded.

### Forward Price Lookup (`quant_signals` table)

Forward returns are computed at query time by joining `score_history` with the `quant_signals` time-series table, which holds daily close prices for every tracked ticker going back ~2 years.

For each scoring event, the engine looks up the **first available close price** within a ±3 to +7 day window around the target date (to handle weekends and public holidays). All three horizons are resolved in a single pass over the in-memory price series — no N+1 queries.

If no price exists within the window (e.g. the ticker was delisted, or the horizon hasn't passed yet) the return shows as pending.

---

## 📐 4. Forward Return Formula

```
return_Nm = (close_price_at_T+N − close_price_at_entry) / close_price_at_entry × 100
```

- **T+90 days** → 3M return (window: T+87 to T+97)
- **T+180 days** → 6M return (window: T+177 to T+187)
- **T+365 days** → 12M return (window: T+362 to T+372)

Returns are **price-only** (no dividends). This is a deliberate simplification — the purpose is to evaluate the score's directional signal quality, not to track total portfolio return.

The forward return is computed relative to **the price at the time of scoring**, not your actual purchase price. A ticker may have been scored while you didn't own it.

---

## ⏳ 5. The Pending Period

Since `score_history` logging began in June 2026, the approximate dates when each horizon first becomes calculable are:

| Horizon | Returns available from |
|---|---|
| 3M | ~September 2026 |
| 6M | ~December 2026 |
| 12M | ~June 2027 |

The banner on the page always shows the exact dates based on the actual earliest event in the database — these are estimates.

**During the pending period the page is still useful:** it shows you which tickers the engine has been tracking and at what score levels, forming the dataset that will eventually produce real return figures. The events table acts as a running log of every scoring decision the engine has made.

---

## 📁 6. Key Files

| File | Role |
|---|---|
| `score_analysis.py` | Backend logic: queries `score_history`, loads price series from `quant_signals`, computes all forward returns in one pass, returns structured dict |
| `database.py` | `score_history` table schema (`init_db`) · `log_score_event()` helper |
| `quant_signals.py` | Calls `log_score_event()` after every scoring run (only for tickers with sufficient data) |
| `page_routes.py` | `GET /score-history?filter=` route |
| `templates/score_history.html` | Page template: filter tabs, availability banner, summary table, events table |
| `templates/portfolio.html` | Score History link in the summary bar (next to X-ray) |
| `templates/watchlist.html` | Score History link in the controls bar |

---

## ⚠️ 7. Known Limitations

- **Returns are not risk-adjusted.** A 10% return in a volatile small-cap is not the same as 10% in a blue chip. There is no Sharpe ratio or benchmark-relative alpha here.
- **Survivorship bias is mild but present.** If a ticker is removed from the universe (e.g. delisted), its `score_history` rows remain but forward prices will not resolve. These rows contribute to the event count but not to the return averages.
- **No exit signal.** The score says when to look; it does not say when to sell. The forward returns use fixed holding periods (3/6/12M), which may or may not match how long you actually hold a position.
- **Sample size.** In the first 6 months, any average in the summary table represents a very small sample (n < 30 for most buckets). Do not draw strong conclusions until at least 12 months of data have accumulated.
- **Re-scoring on the same date overwrites the previous row.** If the scan is run twice on the same day (e.g. after a manual re-run), the latest score is kept. This is intentional — the last scan of the day reflects the most current technical picture.
