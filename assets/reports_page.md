# Market Reports — Standalone Pages

Engine: `reports_engine.py` (unchanged)
API endpoints: `GET /api/reports/*` (unchanged — see `assets/api_reference.md` §7)
Scheduler job: none — every report is a live on-demand query, same as before the split
Last updated: 2026-07-17

---

## 1. Overview

The 7 screener reports that used to live together on a single `/market-reports` page (`market_reports.html`, `market_reports.js`) were split 2026-07-17 into 7 standalone pages, each with its own guide-card tile on the Reports hub (`/reports`, `templates/reports.html`). This was a front-end-only change — `reports_engine.py`, the `/api/reports/*` endpoints, and the DB schema were untouched, since each report function was already independent with no shared state or cross-report dependency.

| Report | Route | Template | JS |
|---|---|---|---|
| Quality Compounders | `/quality-compounders` | `templates/quality_compounders.html` | `static/js/quality_compounders.js` |
| GARP Tenbaggers | `/garp-tenbaggers` | `templates/garp_tenbaggers.html` | `static/js/garp_tenbaggers.js` |
| Quality on Sale | `/quality-on-sale` | `templates/quality_on_sale.html` | `static/js/quality_on_sale.js` |
| Sector Trends | `/sector-trends` | `templates/sector_trends.html` | `static/js/sector_trends.js` |
| Relative Strength Leaders | `/relative-strength-leaders` | `templates/relative_strength_leaders.html` | `static/js/relative_strength_leaders.js` |
| Mean Reversion Screener | `/mean-reversion` | `templates/mean_reversion.html` | `static/js/mean_reversion.js` |
| Dividend Harvest | `/dividend-harvest` | `templates/dividend_harvest.html` | `static/js/dividend_harvest.js` |

Each page route in `page_routes.py` renders its template with the same `unread_count`/`config` context the old combined page used (Mean Reversion and Dividend Harvest still read their default filter values from `config.get('REPORTS_DEFAULTS', {})`).

## 2. Shared JS helpers

`formatCurrency()` and `showTableError()` (GBX-aware price formatting, DataTables AJAX error fallback) were promoted from the old monolithic `market_reports.js` into `static/js/utils.js`, since all 7 new per-report JS files need them. Every new report template loads `utils.js` before its own page script. `setButtonLoading()` — used only by Mean Reversion and Dividend Harvest's "Run Query" buttons — stays duplicated in `mean_reversion.js`/`dividend_harvest.js` rather than being promoted, since it's specific to those two pages' filter-and-refetch pattern.

## 3. Retired

`templates/market_reports.html`, `static/js/market_reports.js`, and the `/market-reports` route are removed. The `/markets` page's "Market Reports" header link and the Watchlist glossary cross-link (`templates/glossary/_fundamentals.html`) now point to the `/reports` hub instead.
