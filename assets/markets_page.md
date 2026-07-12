# Markets Page — Technical Documentation

**Project:** Stock Analysis Quantitative Trading Terminal
**Engines:** `markets_engine.py` (region/session logic, tile assembly), `market_pulse.py` (raw fetch/cache layer, ticker registry accessors)
**Page:** `/markets` (`templates/markets.html`, `static/js/markets.js`)
**API Endpoints:** `GET /api/markets`, `GET /api/system/market-status/all`, `GET|POST /api/markets/registry`, `PUT|DELETE /api/markets/registry/{ticker}`
**Last Updated:** 2026-07-12

---

## Table of Contents

1. [Overview](#1-overview)
2. [Ticker Registry](#2-ticker-registry)
3. [Region/Session Classification](#3-regionsession-classification)
4. [Dynamic Ordering Algorithm](#4-dynamic-ordering-algorithm)
5. [Static View](#5-static-view)
6. [Spot/Future Auto-Swap](#6-spotfuture-auto-swap)
7. [Sparkline Persistence](#7-sparkline-persistence)
8. [Market Pulse Integration](#8-market-pulse-integration)
8b. [ETF Crash-Alert Benchmark Resolution](#8b-etf-crash-alert-benchmark-resolution)
9. [Index Detail Page Data Lifecycle](#9-index-detail-page-data-lifecycle)
10. [API Endpoints](#10-api-endpoints)
11. [Settings](#11-settings)
12. [Known Limitations & Judgment Calls](#12-known-limitations--judgment-calls)

---

## 1. Overview

The Markets page shows global indexes, commodities, and FX ordered by which regional trading session is most relevant right now — "follow the sun." At 5am UK time, Asia-Pacific indexes rank first (Europe below, still closed); mid-morning UK, Europe ranks first with the US market approaching; around UK lunchtime, the US moves to the top as NYSE opens. A Static view (Europe → US → Asia → Commodities & FX, always) is available as a toggle for users who don't want the ordering to move.

Every tile shows: ticker/index name, a mini intraday sparkline, price + currency, % change, and a sentiment badge (reusing the FinBERT sentiment already computed for Market Pulse). Clicking a tile opens `/index/{ticker}`, exactly like Market Pulse tiles do today.

**Navigation (added 2026-07-09):** the Market Screener and Market Reports links were moved off the top navbar and onto the Markets page's own header row (`templates/markets.html`), immediately after the Live Markets Data indicator — Markets is now the entry point for both, rather than three separate top-level nav items. `/market-screener` and `/market-reports` gained a `← Back` button (`javascript:history.back()`, matching `stock_detail.html`'s pattern) since they're no longer always one navbar click away. The top navbar itself was also reordered: Accounts now sits directly after Portfolio (`Markets, Portfolio, Accounts, Watchlist, News, Earnings Volatility, Market Sentiment, Tools, Settings, Glossary, Notifications`).

The feature reworks Market Pulse (the widget on Portfolio/Watchlist/Stock Detail) to read from the same ticker registry, with an optional dynamic mode of its own — see §8.

---

## 2. Ticker Registry

`market_ticker_registry` (see `assets/db_schema_and_architecture.md`) is the single source of truth for every tracked index/commodity/FX ticker — it replaced the old hardcoded `market_pulse.INDEX_TICKERS` dict. 25 rows are seeded on `init_db()`, covering:

- **Commodities/FX** (no exchange gating): Gold, Silver, Copper, WTI Crude, Brent Crude, US Dollar Index, GBP/USD, EUR/USD.
- **Asia**: Nikkei 225 (+ future), Hang Seng, Shanghai Composite, S&P/ASX 200.
- **Europe**: Euro Stoxx 50, FTSE 100, FTSE 250, DAX, CAC 40, UK 10Y Gilt.
- **US**: S&P 500 (+ future), Nasdaq 100 (+ future), Dow Jones (+ future), Russell 2000 (+ future), VIX, 30Y/10Y Treasury yields.

Editable from Settings → Markets & Market Pulse without a restart — a write immediately busts `market_pulse`'s in-process ticker cache (`market_pulse.reload_ticker_registry()`).

---

## 3. Region/Session Classification

`markets_engine.get_exchange_state(exchange) -> "open"|"pre"|"closed"` is built on `market_pulse.is_exchange_open()` (holiday-aware via a Yahoo `marketState` proxy ticker for 8 exchanges — NYSE, LSE, XETRA, TSE, HKEX, SSE, ASX, Euronext — falling back to `time_engine`'s weekday+hours heuristic for anything else).

`markets_engine.get_region_exchanges(region)` is **derived from the registry** (`{row.exchange for row in registry if row.region == region}`), not a second hardcoded map — adding a new exchange to a region via Settings changes region membership with zero code change.

`markets_engine.get_region_state(region)` aggregates the constituent exchanges' states:
- `"open"` if **every** constituent exchange is open.
- `"partial"` ("Some Open" badge) if at least one, but not all, are open — e.g. Hong Kong still trading while Tokyo has already closed for the day.
- `"pre"` if none are open but at least one is in its pre-market window — gated to exchanges with a `premarket_open` entry in `exchange_hours.json` (NYSE only, currently), so this only ever fires for the US region. `market_pulse.is_exchange_open(exchange, include_premarket=True)` enforces this gate even against the live Yahoo `marketState` proxy: Yahoo can report `"PRE"`/`"PREPRE"` for the entire gap since an exchange's previous close on markets with no real extended-hours session of their own, so honoring it unconditionally there misclassified a market that had simply finished for the day as "about to open" (fixed 2026-07-09 — see §11).
- `"closed"` otherwise.

It also computes a `recency_seconds` tie-break value: seconds since the most-recently-opened constituent opened (open/partial state), or seconds until the soonest constituent opens (pre/closed state). `Commodities_FX` always reports `{"state": "open", "recency_seconds": 0}` — it has no exchange, per §11.

Each tile also carries its **own** `market_state` (`markets_engine.get_exchange_state(row.exchange)`, or `"open"` for exchange-less Commodities/FX rows) independent of the region-level aggregate above — this is what lets a Hong Kong tile render in full color while its region badge reads "Some Open" because Tokyo has closed. A tile whose own `market_state != "open"` renders greyed out (`markets-closed-card`) showing the last available price — expected, not an error. Separately, `stale_data = is_stale and market_state == "open"` flags the genuinely anomalous case — the market is supposed to be live but the cache hasn't refreshed within the freshness window (`market_pulse.is_price_fresh()`) — rendered as a grey tile with a diagonal-stripe overlay (`markets-stale-data-card`) to distinguish "this needs investigating" from "this market is simply closed."

---

## 4. Dynamic Ordering Algorithm

`markets_engine.dynamic_region_order() -> List[str]`:

1. Rank `US`, `Europe`, `Asia` into tiers: `open` > `pre` > `closed`.
2. Within the open tier, sort by `recency_seconds` ascending — **the most recently opened region ranks first.** This is the confirmed tie-break rule for the two real overlap windows:
   - Europe/Asia (~07:00–08:00 UTC): the instant XETRA/LSE open while HKEX/SSE are still open, Europe (freshly opened) outranks Asia (open for hours).
   - Europe/US (~14:30–16:30 UTC): the instant NYSE opens, US outranks a Europe session that's been running since the morning.
3. Within the closed tier, sort by `recency_seconds` (time-until-open) ascending — the soonest-to-open closed region ranks just below the open tier. This reproduces "Europe below Asia" at 5am UK: Asia open, Europe closed-but-soon, US closed-and-further.
4. `Commodities_FX` is inserted at index 1 — always the section directly beneath whichever region currently ranks first. Commodities/FX trade near-continuously and matter regardless of which regional equity session is live, so this keeps them consistently visible near the top rather than buried at the bottom (as in the static view) or literally pinned at rank 0 (which would bury the genuinely time-sensitive "follow the sun" signal the dynamic view exists to surface).

---

## 5. Static View

`markets_engine.static_region_order()` is a fixed `["Europe", "US", "Asia", "Commodities_FX"]`, ignoring all session state — exactly the user's specified always-on fallback order.

---

## 6. Spot/Future Auto-Swap

Five registry rows carry a paired front-month future: S&P 500 (`^GSPC`/`ES=F`), Nasdaq 100 (`^NDX`/`NQ=F`), Dow Jones (`^DJI`/`YM=F`), Russell 2000 (`^RUT`/`RTY=F`), and Nikkei 225 (`^N225`/`NIY=F`). Each gets **one tile**, not two.

`markets_engine.resolve_tile(row) -> (ticker, display_name, is_future)` is gated on the row's **own** `exchange` column, not aggregated region state (precise per-ticker, avoids ambiguity when a region straddles an open/closed tier boundary):

- Spot ticker/name while `market_pulse.is_exchange_open(row.exchange, include_premarket=False)` is `True` (strict regular session).
- Future ticker/name during pre-market **and** while fully closed — futures are the more informative pre-market instrument; cash pre-market prints are thin to nonexistent.

This is what produces "US futures shown at UK lunchtime" without a separate futures section: the S&P 500/Nasdaq/Dow/Russell tiles on the Markets page (and, if selected, on Market Pulse) simply swap to their futures ticker once NYSE's regular session ends and until it reopens.

A direct hit on a future ticker's own `/index/{ticker}` URL (e.g. `/index/ES=F`) redirects (302) to its paired spot ticker's detail page — one detail page per index.

---

## 7. Sparkline Persistence

`market_pulse_sparkline` stores today's-session intraday points per ticker. `market_pulse.fetch_and_save_pulse()` already fetches intraday data for its own price-update purpose; on each cycle with fresh data, it down-samples to ~50-60 points and does a full `DELETE` + re-`INSERT` for that ticker (not an append — the sparkline is inherently "today's session"). When the intraday fetch returns empty (market closed), the write step is skipped entirely for that ticker, so the last session's points persist untouched — this produces the flat/last-known line on a closed-market tile, with no extra branching needed.

Rendering is a lightweight inline SVG `<polyline>` built client-side from the small point array already in the `GET /api/markets` response (`static/js/markets.js:marketsSparklineSVG()`) — not a Plotly instance per tile. At ~27+ tiles rendering simultaneously, per-tile Plotly overhead (DOM weight, JS init cost) doesn't scale, whereas an SVG polyline is effectively free. Trade-off: no hover tooltip/zoom on the sparkline — acceptable, since the tile links to the real Plotly intraday chart on `/index/{ticker}`.

---

## 8. Market Pulse Integration

Market Pulse (the widget on Portfolio/Watchlist/Stock Detail, `templates/macro_cards.html` + `static/js/macro_cards.js`) was reworked to read from the same registry rather than its own hardcoded ticker/color/mobile-visibility lists:

- `invert_color` (rising = risk-off styling) and `asset_type === 'FX'` (neutral currency-pair styling) now come from the registry via additive fields on `/api/market-pulse`'s response, replacing the old hardcoded `invertedTickers`/`isForex` arrays in `macro_cards.js`.
- Mobile tile visibility is now `is_pulse_mobile`-driven (a registry column), replacing the old hardcoded `['UK10YG', '^TYX']` exclusion list.
- `UI_PREFERENCES.MARKET_PULSE_DYNAMIC` (default `false`) toggles Market Pulse itself into dynamic mode: `market_pulse._select_active_pulse_tickers()` calls `markets_engine.select_pulse_tickers(dynamic=True, ...)`, flattening `dynamic_region_order()`'s tile ordering exactly like the Markets page. Static mode (default) keeps today's exact `is_pulse_tile=1` picked list, ordered by `pulse_sort_order`.
- `UI_PREFERENCES.MARKET_PULSE_DESKTOP_COUNT`/`MARKET_PULSE_MOBILE_COUNT` (default 10/8) parameterize the previously hardcoded tile counts. Mobile is always a sub-filter of the desktop selection (`is_pulse_mobile=1` rows, first N of them, server order preserved) — never an independently-ranked list, so desktop and mobile never disagree about which tickers are "in scope" today.

No sparklines were added to Market Pulse tiles — kept Markets-page-only, to minimize regression risk on the already-proven widget embedded on three pages.

---

## 8b. ETF Crash-Alert Benchmark Resolution

Added July 2026. `crash_engine.py`'s Crash Alert context report used to always compare a crashing ticker's move to the S&P 500, which is misleading for an ETF that doesn't actually track the US market (e.g. an LSE-listed Asia-Pacific ex-Japan ETF). The registry is now used as the lookup target for a holdings-derived benchmark:

- `universe_fundamentals_engine.sync_etf_holdings_cache(tickers)` fetches `yahoo_engine.get_fund_holdings(ticker)` (top-10 holdings DataFrame) for every held/watchlisted ticker with `stock_signals.quote_type == 'ETF'`, and caches the result as JSON in `stock_signals.top_holdings` (with `holdings_updated_at` as a 30-day freshness marker) — see the "ETF Holdings Columns" note in `assets/db_schema_and_architecture.md`. Called from `run_update_pipeline` (`quant_analysis_job`, Mon–Fri 18:00 UTC) right after the quant scan, over the same ticker universe `DataEngine.get_all_tickers()` already processed that run.
- `markets_engine.resolve_benchmark_for_holdings(top_holdings)` aggregates each holding's weight by its home exchange and returns the registry's canonical index for whichever exchange dominates, via the new `db_helpers.get_ticker_registry_row_by_exchange(exchange)` (lowest `sort_order` enabled Index row for that exchange, e.g. FTSE 100 over FTSE 250 for `LSE`). **Exchange detection is not a second hardcoded map** — it calls `time_engine.ticker_exchange_or_none(symbol)`, which resolves a ticker's Yahoo suffix (`.T`, `.HK`, `.KS`, …) against `data/exchange_hours.json`, the same suffix registry `time_engine.ticker_exchange()`/`ticker_exchange_from_suffix()` and `etf_predictor_engine.py` already use — so an exchange added there (e.g. adding KRX via Settings → Markets & Market Pulse, which already had `.KS`/`.KQ` registered in `exchange_hours.json`) becomes usable for benchmark resolution immediately, with no code change. A holding with an unrecognised suffix is skipped (not guessed); a recognised exchange with no `market_ticker_registry` Index row is likewise skipped in favour of the next-most-represented exchange. Returns `None` only when nothing resolves at all (holdings not cached yet, or every exchange present is unmapped/unregistered — e.g. a broad/thematic global ETF).
- `intraday_orchestrator.py`'s `_run()` resolves each scanned ETF's benchmark up front, adds any newly-needed registry index tickers to the same bulk 5-minute intraday fetch already used for SPY/^TYX (no extra HTTP call), and injects the resulting `{ticker: change_pct}` map into `crash_engine.benchmark_changes` — mirroring the existing `spy_change_pct` injection pattern.
- `crash_engine._generate_context_report()`: for a non-ETF ticker, behavior is unchanged (S&P 500 comparison). For an ETF with a resolved benchmark, the comparison sentence names the resolved index (e.g. "Nikkei 225: -2.34%") instead of S&P 500, using the injected live change or falling back to a single live fetch when not injected (e.g. standalone/test invocation, mirroring the existing SPY fallback). For an ETF with no resolvable benchmark, **the comparison sentence is omitted entirely** rather than defaulting to a misleading S&P 500 comparison — an explicit operator decision, since a global/thematic ETF (e.g. ARKK) has no single relevant regional index.
- Scope: Crash Alerts only, ETFs only. Moonshot Alerts and non-ETF equities are unaffected.

---

## 9. Index Detail Page Data Lifecycle

`/index/{ticker}` (`templates/index_detail.html` + `page_routes_macro.index_detail()`) shows an Intraday Pulse chart, a Macro Trend chart (2Y daily), and a Technicals & Risk panel (RSI/MACD/SMA/momentum/VaR/CVaR/sentiment from `quant_signals`).

**Intraday auto-refresh (added 2026-07-12):** mirroring the Stock Detail page, `static/js/index_detail.js` starts a timer on page load (gated on `UI_PREFERENCES.LIVE_DETAILS`, interval from `UI_PREFERENCES.REFRESH_RATE`) that calls `POST /api/intraday-chart/refresh` and swaps in the freshly rendered chart HTML in place, with a "Next update in M:SS" countdown (`#refresh-status`) matching the Stock Detail page's indicator. No new endpoint — `/api/intraday-chart/refresh` already took a bare `ticker` and was Stock-Detail-only in practice.

**Nightly historical/technicals coverage (added 2026-07-12):** every enabled `market_ticker_registry` row's `ticker` **and** `future_ticker` (if any) is now included in `DataEngine.get_all_tickers()` — the same daily fetch universe documented in AGENTS.md rule 3 for Portfolio/Watchlist/Accounts — so the nightly Update Pipeline job (`run_update_pipeline` → `DataEngine.update_all_data()` → `QuantEngine.run_all()`) downloads 2Y daily history and populates `quant_signals` technicals for every Markets page ticker automatically. Before this, a registry ticker only got a historical parquet (and therefore a populated Macro Trend chart / Technicals panel) after a manual click of the Index Detail page's own "Refresh" button (`POST /api/index/refresh`); now that button is a manual on-demand override, not the only path to real data. `QuantEngine.run_all()` needed no change — it already scans every non-baseline `.parquet` file in `data/historical/`, so a ticker's technicals populate as soon as its price history exists there, regardless of which job wrote it. This also means these tickers are now in scope for the other consumers of `get_all_tickers()` — the ML ensemble backfill/training universe (see `assets/ML_MODEL_DOCUMENTATION.md` §3), the daily tail-risk (VaR/CVaR) scan, the sentiment scan, and the earnings-volatility scan; non-equity tickers have no fundamentals/earnings data and are handled by each of those engines' existing NULL/missing-data paths (e.g. the ML model's cross-sectional median imputation), not a new special case.

`data_engine.get_all_tickers()`'s registry loop and `markets_engine.registry_lookup_tickers()` (§10) both build their ticker lists off the same `db_helpers.get_registry_spot_future_tickers()` helper (added 2026-07-12, re-exported via `database.py`) — every active registry row's `ticker` and `future_ticker`, flat, with no live market-state read or side effects. Each caller still layers its own concern on top: `get_all_tickers()` filters the result through `is_excluded_from_yahoo_fetch()`; `registry_lookup_tickers()` additionally prepends each row's currently-resolved tile ticker (`resolve_tile(row)[0]`, which needs a live `is_exchange_open()` read `data_engine.py` has no business making). This closes the `data_engine`↔`markets_engine` dependency-direction gap flagged when the registry loop was first added — both engines now depend downward on `db_helpers`/`database` instead of one depending on the other.

---

## 10. API Endpoints

See `assets/api_reference.md` §24 (Markets) for full request/response shapes. Summary:

| Endpoint | Purpose |
|---|---|
| `GET /api/markets?view=dynamic\|static` | Full tile payload for the Markets page |
| `GET /api/system/market-status/all` | Net-new, all-exchanges status (does not touch the existing 2-exchange `/system/market-status` HA contract) |
| `GET /api/markets/registry` | List all registry rows (Settings UI) |
| `POST /api/markets/registry` | Create a registry row |
| `PUT /api/markets/registry/{ticker}` | Update a registry row |
| `DELETE /api/markets/registry/{ticker}` | Soft-disable a registry row |

Refresh is page-traffic-driven, exactly like Market Pulse today — `GET /api/markets` computes `needs_refresh` per tile and fires a `fetch_and_save_pulse()` background task inline. No new scheduler job; `scheduler_manifest.JOB_GRAPH["markets_page_source"]` is a `non_job` entry for Workflow Monitor visibility.

**Home Assistant refresh piggyback:** every `POST /api/accounts/refresh-now` call (fired by the HA integration's "Portfolio Refresh Data" button/service) also warms `market_pulse_cache`/`market_pulse_sparkline` for the **entire** ticker registry via `markets_engine.registry_lookup_tickers()`, fired as a `BackgroundTasks` job so it doesn't slow down the awaited portfolio-holdings fetch HA's own response depends on (see `api_routes_accounts._refresh_markets_registry`). This means any open Markets tab sees fresh data on its very next poll instead of waiting on its own independent per-tile staleness cycle. `scheduler_manifest.JOB_GRAPH["ha_refresh_now_source"]["produces"]` includes `market_pulse_sparkline` accordingly. The page also shows a client-side "Last updated Xs/Xm ago" ticker next to the Live/stale dot (`static/js/markets.js:updateMarketsLastUpdatedText`), driven purely by the browser's own last successful poll timestamp — independent of which path (this page's own poll, or the HA piggyback) actually populated the cache.

**Home Assistant passive-poll piggyback (added 2026-07-10):** `refresh-now` above only fires on an explicit button/service call — it does **not** cover HA's own normal, unattended coordinator polling. Before this addition, an install that never pressed "Refresh Data" and never had a `/markets` browser tab open had **no** background warm-up path for the registry's ~15+ non-legacy tickers at all, so any cold visit to `/markets` after a gap showed every tile flagged `stale_data` (the grey diagonal-stripe overlay) for roughly one 60s poll cycle until the page's own first-load fetch caught up — exactly the "tiles crossed over, then self-corrected after ~1 minute" symptom a user hits even while both UK and US markets are open. `GET /api/system/market-status` is the one endpoint every HA install is guaranteed to hit unconditionally every poll cycle (see AGENTS.md's "Always-on polling" note — it is not gated on market hours), so it now also calls `market_pulse.tickers_needing_refresh(markets_engine.registry_lookup_tickers())` and fires the same `fetch_and_save_pulse()` background task for anything stale, alongside its pre-existing narrower self-heal of the 8 `_MARKET_STATUS_PROXY` tickers (both checks now share the generalized `market_pulse.tickers_needing_refresh(tickers, max_age_seconds)` helper — `proxy_tickers_needing_refresh()` is now a thin wrapper over it). Represented in the Workflow Monitor as `scheduler_manifest.JOB_GRAPH["ha_market_status_source"]`, a `non_job` entry alongside `ha_refresh_now_source`. This does not change `/system/market-status`'s response shape — additive background behavior only, no HA-integration-side change required.

---

## 11. Settings

Settings → Markets & Market Pulse (`templates/settings/_markets.html`, `static/js/settings_markets.js`):
- Market Pulse Tile Selection: dynamic-view toggle, desktop/mobile tile counts (saved with the rest of the settings form).
- Ticker Registry: add/edit/delete rows, mirroring the ETF Predictor CRUD's structural pattern (inline edit-toggle per row, an inline "+ Add Ticker" form) since no prior "structured add/edit/remove list" widget existed in Settings for this shape of data.

---

## 12. Known Limitations & Judgment Calls

- **Commodities/FX have no session-hours model.** None of Gold/Silver/Copper/WTI/Brent/DXY/GBPUSD/EURUSD map cleanly to a single exchange in `time_engine.EXCHANGE_HOURS` (COMEX/NYMEX/Globex/FX aren't modeled), so these rows have `exchange = NULL` and are always considered "open" for fetch/display purposes. A future refinement could add real Globex/FX session hours if genuine weekend/maintenance-window awareness is wanted.
- **Euro Stoxx 50 (`^STOXX50E`) is gated on the `Euronext` exchange** as a pragmatic approximation — it's a pan-eurozone index traded via Eurex, which has no dedicated entry in `exchange_hours.json`.
- **Only NYSE and LSE are genuinely holiday-aware.** The other 6 proxy exchanges (XETRA, TSE, HKEX, SSE, ASX, Euronext) fall back to `time_engine`'s weekday+hours heuristic, which has no holiday calendar.
- **The "most recently opened ranks first" tie-break** is a design choice, not a law of markets — it prioritizes "what's newly actionable" over "what's been open longest." Confirmed with the user during design; documented here in case a future session wants to revisit it.
- **Fixed 2026-07-09 — Asia region showing "Pre-Market" hours after actually closing.** `market_pulse.is_exchange_open(exchange, include_premarket=True)` used to trust Yahoo's live `marketState` `"PRE"`/`"PREPRE"` values for all 8 proxied exchanges uniformly. Only NYSE has a genuine multi-hour extended-trading session; Yahoo appears to use `"PRE"` as a catch-all "not currently in regular session, hasn't opened yet" bucket for markets with no real extended-hours concept (HKEX, SSE, TSE, ASX, XETRA, LSE, Euronext), which can span the entire gap since the previous close — so a market that had simply finished its session for the day read as "about to open." Fix: `is_exchange_open()` now only honors the live `"PRE"`/`"PREPRE"` state (and the same-named heuristic fallback flag) for exchanges with a `"premarket_open"` entry in `exchange_hours.json` — currently NYSE only. Every other proxied exchange now reports `"closed"` outside its regular session, matching what the fallback heuristic already did.
