# Configurable Columns & Sticky Header (Portfolio / Watchlist)

Added July 2026. Desktop-only (≥769px). Two independent additions to the
Portfolio (`/portfolio`) and Watchlist (`/watchlist`) DataTables:

1. A **Columns** picker (toolbar dropdown) letting the user show/hide columns,
   including a catalog of ~64 optional columns that weren't previously shown
   on either page at all (see below).
2. A **sticky table header** that stays pinned under the navbar while scrolling,
   using the page's own scrollbar — no inner table scroll container.

## Column registry — `table_columns_helpers.py`

Single source of truth for every column on both pages:

- `PORTFOLIO_CORE_COLUMNS` / `WATCHLIST_CORE_COLUMNS` — the original, always-present
  columns, as a `{key, label, pinned}` list. **Must stay in the exact order of the
  hand-authored `<thead>` `<th>` elements in `templates/portfolio.html` /
  `templates/watchlist.html`** — this list only mirrors the template, it doesn't
  generate it (the original columns keep their existing hand-tuned markup: badges,
  `<abbr>` tooltips, conditional styling — deliberately not made generic).
- `OPTIONAL_COLUMNS` — the new catalog, `{key, label, category, pages, fmt}` per
  entry. `pages` is `("portfolio", "watchlist")` or a single-page tuple for a
  parity-gap column (e.g. `low_target`/`high_target`/`piotroski_f_score` are
  Portfolio-only — Watchlist already had them as core columns before this feature).
- `all_columns_for_page(page)` — core + optional, in exact DataTables column-index
  order. Passed to the template as `window.PORTFOLIO_COLUMNS`/`WATCHLIST_COLUMNS` so
  `column_picker.js` never hand-counts indices.
- `build_optional_column_cells(row_dict, page)` — per-row `{key, sort, display}`
  for every optional column, computed server-side by `_format_value()` from a `fmt`
  type (`pct_from_fraction`, `pct_raw`, `ratio2`, `price`, `price_raw`,
  `currency_usd`, `volume`, `date`, `text`, `bool01`, `int`, `client`). `price`/
  `price_raw` reuse `page_helpers._fmt_price()` (GBp-aware, mirrors the inline
  price-formatting already used throughout both templates); `currency_usd`/`volume`
  reuse the existing `page_helpers._fmt_currency()`/`_fmt_volume()`.
- `fmt == "client"` marks the 4 Position Sizing columns (`shares`, `position_value`,
  `stop_price`, `risk_amount`) — these have no server-computed value; the templates
  render placeholder `<td class="ps-cell...">` markup and `renderPositionSizing()`
  (`portfolio.js`/`watchlist.js`) fills them in after DataTables initializes, using
  `data-col-key` lookups scoped to the row (not sibling-chaining, since a hidden
  column doesn't change DOM order but could sit between them).

**Adding a new optional column:** add one entry to `OPTIONAL_COLUMNS`. If the
source field isn't already in the row dict, add it to the relevant `SELECT` in
`page_routes.portfolio_page()`/`watchlist_page()` (both already `SELECT s.*` from
`stock_signals` and LEFT JOIN `quant_signals q`/`asset_profiles ap`/
`market_universe m`/`ticker_metadata tmeta` — most new fields are a one-line
addition to an existing JOIN's SELECT list, not a new JOIN). No template change
needed — the optional-column `<th>`/`<td>` loop is generic.

## Persistence — `UI_PREFERENCES`

Four `config.json` keys: `PORTFOLIO_HIDDEN_CORE_COLUMNS`, `PORTFOLIO_SHOWN_OPTIONAL_COLUMNS`,
`WATCHLIST_HIDDEN_CORE_COLUMNS`, `WATCHLIST_SHOWN_OPTIONAL_COLUMNS`. Core columns
are opt-*out* (default visible, matching pre-feature behavior); optional columns
are opt-*in* (default hidden, so first load after the feature ships looks
unchanged). Written by `POST /api/ui-preferences/columns` (see
`assets/api_reference.md`), no confirm token — same pattern as
`POST /api/learn/preference`.

## Front-end — `static/js/column_picker.js`

Shared module (same pattern as `chart_fullscreen.js` for charts):
`ColumnPicker.resolveVisible(key, allColumns, prefs)` is a pure function usable
before `.DataTable()` is even constructed (to compute the initial `columnDefs`
`visible` array, avoiding a flash of all-columns-visible). `ColumnPicker.init(...)`
wires the dropdown menu (grouped by `category`) and returns `{isVisible,
applyFilterOverride}`.

`applyFilterOverride(key, bool)` exists for the one case where a column's
visibility is controlled by **two** things at once: Watchlist's pre-existing
`#targetFilter` dropdown already toggles Piotroski/Altman/Beneish vs Low/High
Target based on filter selection. The picker's own user preference is AND-ed with
the filter's current override — a column hidden via the picker stays hidden
regardless of what the filter would otherwise show. `watchlist.js`'s
`#targetFilter` change handler calls `applyFilterOverride` for those 5 keys
instead of calling `table.column(N).visible()` directly.

## Sticky header

Plain CSS `position: sticky` on `#dataTable thead th/td` (`static/css/styles.css`,
gated `@media (min-width: 769px)`), not the DataTables FixedHeader extension —
neither page has an inner scroll container at desktop widths (the mobile-only
`#dataTable_wrapper { overflow-x: auto }` rule is itself inside a
`max-width: 768px` block), so native `position: sticky` works with the browser's
own scrollbar with no extra JS. `top` is a CSS custom property
(`--sticky-thead-top`), set at runtime by `applyStickyTheadOffset()`
(`static/js/utils.js`) from the navbar's actual rendered height (not hardcoded —
the navbar's height varies slightly, e.g. when the freshness badge wraps),
recomputed on `window.resize`.
