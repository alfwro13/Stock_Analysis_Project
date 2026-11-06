# Market Reports — Standalone Pages

Engine: `reports_engine.py`
API endpoints: `GET /api/reports/*` (see `assets/api_reference.md` §7)
Scheduler job: none — every report is a live on-demand query

---

## 1. Overview

The 7 screener reports are standalone pages, each with its own guide-card tile on the Reports hub (`/reports`, `templates/reports.html`). `reports_engine.py`, the `/api/reports/*` endpoints and the DB schema are shared by all of them; each report function is independent, with no shared state or cross-report dependency.

| Report | Route | Template | JS |
|---|---|---|---|
| Quality Compounders | `/quality-compounders` | `templates/quality_compounders.html` | `static/js/quality_compounders.js` |
| GARP Tenbaggers | `/garp-tenbaggers` | `templates/garp_tenbaggers.html` | `static/js/garp_tenbaggers.js` |
| Quality on Sale | `/quality-on-sale` | `templates/quality_on_sale.html` | `static/js/quality_on_sale.js` |
| Sector Trends | `/sector-trends` | `templates/sector_trends.html` | `static/js/sector_trends.js` |
| Relative Strength Leaders | `/relative-strength-leaders` | `templates/relative_strength_leaders.html` | `static/js/relative_strength_leaders.js` |
| Mean Reversion Screener | `/mean-reversion` | `templates/mean_reversion.html` | `static/js/mean_reversion.js` |
| Dividend Harvest | `/dividend-harvest` | `templates/dividend_harvest.html` | `static/js/dividend_harvest.js` |

Each page route in `page_routes.py` renders its template with the `unread_count`/`config` context (Mean Reversion and Dividend Harvest still read their default filter values from `config.get('REPORTS_DEFAULTS', {})`).

## 2. Shared JS helpers

`formatCurrency()` and `showTableError()` (GBp-aware price formatting, DataTables AJAX error fallback) live in `static/js/utils.js`, because all 7 per-report JS files need them. `base.html` loads `utils.js` on every page, before the page's own script, so a report template must not add its own tag; any text from Yahoo, the Freetrade sheet or Wikipedia that a report script writes into a DataTables cell, an `<option>` or an `href` goes through `escapeHtml()` (DataTables: only for the `display` render type) or `new Option()`. `formatCurrency()` is the single front-end currency formatter, also used by the Portfolio, Watchlist and Stock Detail position-sizing cells: fixed `en-US` `Intl.NumberFormat` (`$1,234.50`, `£1,234.50`, `CHF 1,234.50`), `GBp` pence divided by 100 and shown as GBP, `—` for a missing/non-numeric value, and a plain `1,234.50` when no currency code is given or `Intl` rejects the code (a malformed code is dropped, never echoed, so the result is safe in `innerHTML` and `textContent` alike). A third-party link that becomes an `href` (Yahoo's `website`, a news article `url`) must also be a web link: the News reader and the Market Screener's Freetrade KIID link call `safeHttpUrl()` (`utils.js`, returns `''` unless the value is an absolute `http(s)` URL; the KIID cell shows `N/A` when it is empty), and the Stock Detail Company Info card gets its value from `page_helpers._load_fundamentals_extra()`, which passes `website` through `utils.safe_http_url()` so a `javascript:` value never reaches the template. Operator-entered names (account names, Markets registry display names and tickers) go through `escapeHtml()` in every `innerHTML` sink, and a value an inline `on*=` handler needs travels in an escaped `data-*` attribute behind a delegated listener. `setButtonLoading()` — used only by Mean Reversion and Dividend Harvest's "Run Query" buttons — is defined separately in `mean_reversion.js` and `dividend_harvest.js`, since it is specific to those two pages' filter-and-refetch pattern.

## 3. Navigation

The `/markets` page's "Market Reports" header link and the Watchlist glossary cross-link (`templates/glossary/_fundamentals.html`) point to the `/reports` hub.
