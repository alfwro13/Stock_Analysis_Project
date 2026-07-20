# AI Sector Contagion Monitor

The AI Sector Contagion Monitor tracks the broader AI ecosystem — not just semiconductors, but hyperscalers, cloud platforms, and AI-adjacent companies. It identifies whether a sharp move in one major AI name is spreading ("contagion") or is isolated.

Page route: `GET /ai-contagion`

---

## 1. Tickers Monitored

| Ticker | Company | Category |
|--------|---------|----------|
| NVDA | NVIDIA | Semiconductor (red) |
| AMD | Advanced Micro Devices | Semiconductor (red) |
| AVGO | Broadcom | Semiconductor (red) |
| GOOGL | Alphabet | Hyperscaler (blue) |
| MSFT | Microsoft | Hyperscaler (blue) |
| META | Meta Platforms | Hyperscaler (blue) |
| AAPL | Apple | Hyperscaler (blue) |
| ORCL | Oracle | Cloud (teal) |
| AMZN | Amazon | Cloud (teal) |
| TSLA | Tesla | AI-adjacent (orange) |

The basket deliberately spans beyond pure semiconductors. It adds hyperscalers and cloud companies that are major AI investors or infrastructure providers alongside the semi names, to capture the full contagion surface.

---

## 2. Charts

### 30-Day Normalised Performance (`create_ai_contagion_performance_chart`)

All tickers normalised to 100 at the start of the 30-day window. A value of 95 means the stock is down 5% over the period.

- Colour coding: red = semis, blue = hyperscalers, teal = cloud, orange = TSLA
- Base-100 reference line drawn at `y=100`
- `hovermode="x unified"` for synchronised cross-hair

### Intraday Normalised Performance (`create_ai_contagion_performance_chart` with intraday data)

Today's 5-minute bars normalised to 100 at NYSE open (09:30 ET). Only rendered when intraday data is available (hidden on weekends/market-closed). Uses `prepost=False` — regular session only, since pre/post-market data is sparse for this use case.

### Pairwise Correlation Heatmap (`create_ai_contagion_correlation_heatmap`)

Pearson correlation matrix computed over the trailing 20 trading days of daily returns (`df.pct_change()`). Uses `go.Heatmap` with `colorscale="RdBu_r"` — deep blue = strongly correlated (stocks move together), deep red = strongly inverse, white = uncorrelated.

High correlation across the full grid means the sector is moving as one: a drawdown in any single name is likely to spread. Low or heterogeneous correlation means moves are company-specific.

---

## 3. Data Fetching

`ai_contagion_engine.get_ai_contagion_data(days=30)` handles all data fetching:

- Daily: `yahoo_engine.get_price_history(_AI_PAGE_TICKERS, period="35d", interval="1d")` truncated to `days`
- Intraday: `yahoo_engine.get_intraday(_AI_PAGE_TICKERS, period="1d", interval="5m", prepost=False)`

Both calls use the existing `yahoo_engine` cache layer. Intraday failures are caught and the intraday chart section is silently omitted from the page.

---

## 3a. Flash-Crash Detection & Alerting (`AIContagionEngine.scan()`)

Runs on the scheduled `ai_contagion_job` (default mon-fri 08:20–18:00 UTC, every 20 min). Requires two-tier confirmation: at least one bellwether drops past `LEADER_THRESHOLD_PCT` **and** at least one ETF confirms past `ETF_CONFIRMATION_THRESHOLD_PCT`, both measured as drawdown from the previous trading day's close using 15-min bars (`period="2d"`, `prepost=True`).

`_evaluate_ticker()` requires the frame's most recent bar to actually be **today** (`datetime.now(timezone.utc).date()`) before treating it as current — added 2026-07-16. Without this check, a ticker whose 15-min feed hadn't posted a single bar for today yet (a lagging premarket print right at the scan's early 08:20 UTC start, or a ticker-specific gap) would have its last row still be yesterday's close, and the function would silently recompute yesterday's already-alerted drawdown as if it were a fresh event. Confirmed on production: the alert fired with byte-identical per-ticker percentages on 2026-07-03 and 2026-07-06. Dedup itself is a once-per-UTC-day count (`_evaluate_daily_alert_gate`, `NOTIFICATIONS.AI_CONTAGION.MAX_ALERTS_PER_DAY`) rather than the worsened/recovered model, per AGENTS.md rule 19 — so this staleness check is the only thing standing between a data gap and a spurious "new" alert.

`_evaluate_ticker()` only calls `market_pulse.upsert_live_price()` — sharing the price it just fetched for its own drawdown math into `market_pulse_cache` for other consumers, per AGENTS.md rule 15 — while `is_quote_settled("NYSE")` (regular session, no `include_premarket`) is true, added 2026-07-20. `scan()`'s own gate deliberately passes `include_premarket=True` so a genuine premarket flash crash still gets caught, which means the frame's last bar (fetched with `prepost=True`) can legitimately be a premarket or postmarket tick rather than a regular-session price. Sharing that tick unconditionally used to overwrite `market_pulse_cache`'s settled `price`/`change_pts`/`change_pct` columns — the same columns the Portfolio page and the Home Assistant integration's `market_price` attribute read — with a raw extended-hours print, until the next `market_pulse.fetch_and_save_pulse()` cycle corrected it a cycle or two later. Confirmed on production 2026-07-20: MU briefly showed +3.14% (a premarket tick) as its settled change instead of the correct extended-hours figure already tracked separately in `extended_price`/`extended_change_pct`. The drawdown-vs-previous-close math itself (`_evaluate_ticker`'s own return value, used for the leader/ETF threshold check) is unaffected by this gate — only the shared-cache write is skipped outside regular hours.

---

## 4. Key Files

| File | Role |
|------|------|
| `ai_contagion_engine.py` | `get_ai_contagion_data()`, `_AI_PAGE_TICKERS` constant, flash-crash detector |
| `visuals_ai.py` | `create_ai_contagion_performance_chart()`, `create_ai_contagion_correlation_heatmap()`, `_AI_COLORS` |
| `page_routes.py` | `GET /ai-contagion` route |
| `templates/ai_contagion.html` | Page template |
| `templates/tools.html` | Tool card entry |
