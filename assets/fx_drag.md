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

## Math

Given a reference date `R` and today `T`:

```
equity_return  = price_T / price_R - 1          (USD price move)
fx_return      = gbpusd_R / gbpusd_T - 1        (positive = USD strengthened = GBP tailwind)
total_gbp_return = (1 + equity_return) × (1 + fx_return) - 1
```

The difference between `total_gbp_return` and `equity_return + fx_return` is the cross-term `equity_return × fx_return`, which is small but non-zero when both components are large.

## Reference-period approach

Purchase-date FX rates are not stored — Ghostfolio returns only the current position value and shares. Instead, a reference date is used:

| Period | Reference date |
|--------|---------------|
| YTD | First trading day of the current calendar year |
| 1Y | 365 calendar days before today |
| 2Y | 730 calendar days before today |

The reference price is the first `Close` row in the ticker's Parquet file on or after the reference date. The reference GBPUSD rate is the corresponding row in `GBPUSD_BASELINE.parquet`.

If no data exists within the period range, `compute_fx_breakdown` returns `None` and the ticker is omitted from the output.

## Data sources

| Data | Source |
|------|--------|
| Stock price history | `data/historical/{ticker}.parquet` (2-year daily OHLCV) |
| GBPUSD history | `data/historical/GBPUSD_BASELINE.parquet` (fetched by `data_engine.py`) |
| USD position list | `portfolio.json` + `stock_signals.currency` column in SQLite |

No new data pipeline or scheduled job is required.

## API

`GET /api/fx-drag?period=ytd|1y|2y` — returns `{"status": "success", "period": "ytd", "data": [...]}`.

Full schema: see `assets/api_reference.md` → section 20.

## UI surfaces

### `/fx-drag` (Tools page)
Full portfolio-level view: period selector, summary hero cards (total USD exposure, avg equity return, FX effect, avg GBP return), table per position, and a Plotly stacked bar chart (equity vs FX contribution per ticker).

### Stock detail page (`/stock/{ticker}`)
A compact inline "FX Breakdown (YTD)" row appended to the "Your Position" box for USD-quoted stocks that have a position. Shows three numbers: Equity (USD) / FX Effect / Total GBP (YTD). Links to `/fx-drag` for the full breakdown.

## Limitations

- **No purchase-date FX.** The reference-period approach is an approximation. It shows how the stock and the currency have moved since Jan 1 (or N days ago), not since the position was opened. For long-held positions opened in a different rate environment, the decomposition does not reflect the investor's actual lifetime cost basis in GBP.
- **2-year Parquet cap.** The `GBPUSD_BASELINE.parquet` covers ~2 years of daily data. The 2Y period may have sparse or missing data near its boundary, in which case `compute_fx_breakdown` returns `None`.
- **USD positions only.** Positions where `stock_signals.currency != 'USD'` are excluded. EUR, GBX, GBP positions have different FX dynamics not covered by this tool.
- **BASE_CURRENCY guard.** If `BASE_CURRENCY` in `config.json` is not `"GBP"`, `portfolio_fx_breakdown` returns an empty list — the analysis is only meaningful for GBP-base investors.
