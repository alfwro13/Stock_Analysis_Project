# FX Drag Analyzer

## Purpose

Decomposes each US stock position's GBP return into two independent components:

- **Equity return (USD):** how much the stock's price moved in dollars
- **FX effect:** how much GBP/USD exchange rate movement added to or subtracted from that return in sterling terms

This separates real stock-level alpha from "currency illusion" — the distortion that arises because GBPUSD rate changes silently inflate or deflate the GBP value of dollar-denominated holdings.

## Engine

**`fx_drag_engine.py`** — on-demand only, no scheduled job.

Key functions:

| Function | Purpose |
|----------|---------|
| `_load_gbpusd_series()` | Loads GBPUSD daily close prices from `data/historical/GBPUSD_BASELINE.parquet`; falls back to a live yfinance fetch if the file is missing |
| `compute_fx_breakdown(ticker, period_days)` | Returns equity%, fx%, total_gbp%, ref_date, gbpusd_ref, gbpusd_now for a single ticker |
| `portfolio_fx_breakdown(period_days)` | Loops over all USD positions in `portfolio.json`, calls `compute_fx_breakdown` for each, appends GBP exposure |
| `_compute_activities_gbpusd(activities, ticker)` | Filters Ghostfolio activities for a ticker and returns `(vwap_buy_usd, weighted_avg_gbpusd_buy, buy_count, earliest_buy)` |
| `portfolio_lifetime_fx_breakdown()` | Lifetime mode: fetches activities from Ghostfolio, derives per-ticker purchase GBPUSD, computes full decomposition |

`ghostfolio_sync.GhostfolioSyncEngine.fetch_activities()` is called by the lifetime function to retrieve raw activities.

## Math

Given a reference date `R` and today `T`:

```
equity_return  = price_T / price_R - 1          (USD price move)
fx_return      = gbpusd_R / gbpusd_T - 1        (positive = USD strengthened = GBP tailwind)
total_gbp_return = (1 + equity_return) × (1 + fx_return) - 1
```

The difference between `total_gbp_return` and `equity_return + fx_return` is the cross-term `equity_return × fx_return`, which is small but non-zero when both components are large.

## Reference-period approach

For YTD / 1Y / 2Y periods, a shared reference date is used rather than the individual purchase date:

| Period | Reference date |
|--------|---------------|
| YTD | First trading day of the current calendar year |
| 1Y | 365 calendar days before today |
| 2Y | 730 calendar days before today |

The reference price is the first `Close` row in the ticker's Parquet file on or after the reference date. The reference GBPUSD rate is the corresponding row in `GBPUSD_BASELINE.parquet`.

If no data exists within the period range, `compute_fx_breakdown` returns `None` and the ticker is omitted from the output.

## Lifetime mode

Lifetime mode replaces the shared reference date with the **actual weighted-average GBPUSD rate at which each position was purchased**, derived directly from Ghostfolio trade history. No exchange-rate API is required.

### How it works

`GET /api/v1/activities` is called on the configured Ghostfolio instance. For each USD position, all BUY trades are collected and aggregated:

```
total_usd = Σ (quantity_i × unitPriceInAssetProfileCurrency_i)
total_gbp = Σ (quantity_i × unitPrice_i)
total_qty = Σ quantity_i

vwap_buy_usd            = total_usd / total_qty
weighted_avg_gbpusd_buy = total_usd / total_gbp
```

`unitPriceInAssetProfileCurrency` is the USD price of the asset at time of purchase; `unitPrice` is what was paid in the account currency (GBP). Their ratio gives the implied GBP/USD rate at the time of the trade — no external exchange rate lookup needed.

Then:

```
equity_pct    = (current_price_usd / vwap_buy_usd - 1) × 100
fx_pct        = (weighted_avg_gbpusd_buy / gbpusd_now - 1) × 100
total_gbp_pct = ((1 + equity_pct/100) × (1 + fx_pct/100) - 1) × 100
```

`gbpusd_now` is read from the last row of `GBPUSD_BASELINE.parquet`.

### Activity filters

Only activities matching all of the following are included:
- `type == "BUY"`
- `isDraft == False`
- `SymbolProfile.currency == "USD"`

### Approximation

Uses all historical BUY orders. For positions with partial sells, the activities-derived VWAP may differ slightly from Ghostfolio's official average-cost (which deducts sold lots). For a buy-and-hold portfolio the difference is zero.

### Ghostfolio dependency

Lifetime mode returns an empty list if `GHOSTFOLIO_URL` or `GHOSTFOLIO_TOKEN` are not configured, or if the Ghostfolio instance is unreachable. The YTD / 1Y / 2Y modes are unaffected.

## Data sources

| Data | Source |
|------|--------|
| Stock price history | `data/historical/{ticker}.parquet` (2-year daily OHLCV) |
| GBPUSD history | `data/historical/GBPUSD_BASELINE.parquet` (fetched by `data_engine.py`) |
| USD position list | `portfolio.json` + `stock_signals.currency` column in SQLite |

No new data pipeline or scheduled job is required.

## API

`GET /api/fx-drag?period=ytd|1y|2y|lifetime` — returns `{"status": "success", "period": "ytd", "data": [...]}`.

Full schema: see `assets/api_reference.md` → section 20.

## UI surfaces

### `/fx-drag` (Tools page)
Full portfolio-level view: period selector, summary hero cards (total USD exposure, avg equity return, FX effect, avg GBP return), table per position, and a Plotly stacked bar chart (equity vs FX contribution per ticker).

### Stock detail page (`/stock/{ticker}`)
A compact inline "FX Breakdown (YTD)" row appended to the "Your Position" box for USD-quoted stocks that have a position. Shows three numbers: Equity (USD) / FX Effect / Total GBP (YTD). Links to `/fx-drag` for the full breakdown.

## Limitations

- **Reference periods are approximations.** YTD / 1Y / 2Y show how the stock and the currency have moved since the reference date, not since the position was opened. Use Lifetime mode for the real purchase-date decomposition.
- **2-year Parquet cap.** The `GBPUSD_BASELINE.parquet` covers ~2 years of daily data. The 2Y period may have sparse or missing data near its boundary, in which case `compute_fx_breakdown` returns `None`.
- **USD positions only.** Positions where `stock_signals.currency != 'USD'` are excluded. EUR, GBX, GBP positions have different FX dynamics not covered by this tool.
- **BASE_CURRENCY guard.** If `BASE_CURRENCY` in `config.json` is not `"GBP"`, `portfolio_fx_breakdown` returns an empty list — the analysis is only meaningful for GBP-base investors.
