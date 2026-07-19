# Configurable Columns, Views, Advanced Filter & Sticky Header (Portfolio / Watchlist)

Added July 2026, extended later that month with X-ray/Earnings Volatility columns, named
Views, and an Advanced Filter builder. Desktop-only (≥769px). Four independent additions to
the Portfolio (`/portfolio`) and Watchlist (`/watchlist`) DataTables:

1. A **Columns** picker (toolbar dropdown) letting the user show/hide columns, from a catalog
   of ~69 optional columns that weren't previously shown on either page at all (see below).
2. A **Views** picker (toolbar dropdown, same visual style) for saving/applying named column
   presets — a bulk "show exactly these columns" shortcut layered on top of the Columns picker's
   per-column state. Ships with 3 built-in views; the user can add, overwrite, and delete views
   freely.
3. An **Advanced Filter** (modal, opened from a button next to the DataTables length control —
   next to `+ Add Ticker` on Watchlist) for building multi-condition row filters over any column,
   independent of the pre-existing quick-filter dropdowns (Signal/Tags/Score/Sector). See
   "Advanced Filter" below.
4. A **sticky table header** that stays pinned under the navbar while scrolling, using the
   page's own scrollbar — no inner table scroll container.

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
`market_universe m`/`ticker_metadata tmeta`/`xray_risk_cache xrisk`/
`earnings_volatility ev` — most new fields are a one-line addition to an existing
JOIN's SELECT list, not a new JOIN). No template change needed — the
optional-column `<th>`/`<td>` loop is generic. See AGENTS.md's Documentation
Maintenance section — this is a mandatory step whenever new displayable data is
added anywhere in the app, not an optional nice-to-have.

### X-ray / Earnings Volatility columns (added in the same feature's second stage)

Five columns needing a genuinely new JOIN, deliberately deferred from the first stage:

- `xray_beta` / `xray_annualized_vol` — from `xray_risk_cache`, joined on
  `(ticker, benchmark)` where `benchmark` is pinned to the fixed
  `xray_engine.BENCHMARK_SYMBOL` constant (`"SWDA.L"`) — passed as a bound SQL
  parameter, never hardcoded inline, so the query stays correct if that constant
  ever changes. Refreshed by the always-on nightly X-ray job (Mon–Fri 19:00).
- `xray_dividend_yield` — from `xray_dividend_cache.dividend_yield_pct`, read via a
  correlated `ORDER BY last_updated DESC LIMIT 1` subquery (not a plain JOIN) since
  the table's real key is `(ticker, data_source)` and `data_source` varies per
  holding — this mirrors the exact pattern `ai_engine.py`/`xray_engine.py` already
  use to read this table. **Different scale from the existing `dividend_yield`
  column**: this one comes from Ghostfolio already in percentage form (`2.5` = 2.5%,
  `fmt: "pct_raw"`), while `stock_signals.dividend_yield` (Yahoo-sourced) is a
  fraction (`0.025`, `fmt: "pct_from_fraction"`) — do not reuse one formatter for
  both. Also only populated for Ghostfolio-tracked holdings, so it's routinely NULL
  for most Watchlist-only tickers.
- `earnings_edge_score` / `earnings_implied_move` — from `earnings_volatility`,
  a plain `ticker`-keyed JOIN. Both are **sparse by design**: the weekly scan
  (`earnings_vol_engine.py`, Saturday 10:00) only writes a row for tickers with
  earnings within ~14 days, so most rows are NULL most of the time.

## Views — `DEFAULT_PORTFOLIO_VIEWS` / `DEFAULT_WATCHLIST_VIEWS` + `resolve_views()`

A view is `{"name": str, "columns": [key, ...]}` — the explicit set of column keys
that should be visible when applied; everything else (except the pinned Ticker
column) is hidden. Views are defined **per page**, not shared across both, because
several concepts have different core-column keys on each page (e.g. Portfolio's
`change`/`target_price`/`piotroski_f_score` vs. Watchlist's `daily_change`/
`target`/`piotroski`) — a single cross-page view definition would need to special-case
these divergences for no real benefit, so each page gets its own parallel view list
using its own native keys instead.

`resolve_views(config_data, page)` returns the saved `UI_PREFERENCES.{SCOPE}_VIEWS`
list if non-empty, otherwise falls back to the 3 built-in defaults
(`table_columns_helpers.py`, ≤24 columns each — kept well under DataTables'
practical usability ceiling for a focused view):

- **Fundamentals & Quality** — valuation ratios, quality/forensic scores, sector,
  market cap.
- **Technical Signals** — RSI/MACD/trend/volatility/momentum/ML confidence.
- **Position Targets** — Target Price, Low/High Target, Stop-Loss, Entry/Exit Zone,
  Suggested Shares/Position Value. This view is the direct replacement for
  Watchlist's removed `#targetFilter` "Has Target Set" dropdown (see below) — it
  surfaces the same information as a column preset instead of a row filter.

Saving/renaming/deleting a view is a **full-list replacement** via
`POST /api/ui-preferences/views` (`{scope, views: [...]}`) — the client always sends
the complete updated list, matching `POST /api/ui-preferences/columns`'s existing
pattern rather than adding separate add/rename/delete endpoints.

**Applying a view does not introduce a second visibility engine.** `column_picker.js`'s
`ColumnPicker.applyView(columnKeys)` recomputes the same `hidden_core_columns`/
`shown_optional_columns` state the individual checkboxes already maintain, applies it
column-by-column via the same `table.column(idx).visible()` calls, and saves it
through the same `/api/ui-preferences/columns` endpoint — a view is purely a bulk
shortcut for setting that one piece of state, not a separate persisted "current view"
concept. This is why leaving the Columns picker open after applying a view shows the
now-current (view-derived) checkbox state, and why manually tweaking a checkbox
after applying a view doesn't need any special "detach from view" handling.

### Removal of Watchlist's `#targetFilter`

Before Views existed, Watchlist had a `#targetFilter` dropdown ("All Rows" / "Has
Target Set") that did two unrelated things at once: filtered rows via a
`$.fn.dataTable.ext.search` predicate on `data-has-target`, and toggled 5 columns
(Piotroski/Altman/Beneish vs. Low/High Target) via a `column_picker.js`
`applyFilterOverride()` mechanism built specifically to AND that filter's intent
with the user's saved column preference. Both were removed in favor of the
Position Targets view: Piotroski/Altman/Beneish/Low Target/High Target are now
plain, independently toggleable columns like any other (visible by default, since
they're Watchlist *core* columns) — the row-level "only show tickers with a target
set" filtering capability was deliberately not replaced (explicit product decision;
sort/scan manually or via the Position Targets view instead). `applyFilterOverride`
and its supporting `filterOverrides` state were deleted from `column_picker.js`
entirely once nothing called it — do not re-add this pattern without a concrete
second caller; a page-specific filter/column interaction like this is the
exception, not something every page needs.

## Advanced Filter — `static/js/advanced_filter.js`

A per-page modal (`#advFilterModal`) for building multi-condition row filters over any
column — core or optional, regardless of current visibility — on top of the pre-existing
quick-filter dropdowns (Signal/Tags/Score/Sector), which are untouched and keep working
exactly as before. Conditions combine with a single global **AND/OR toggle** (`logic`,
shown once ≥2 conditions exist) — not per-pair grouping, which was considered and rejected
as unneeded complexity for this app's use cases. A worked example needing OR: "Low Target
is not empty OR High Target is not empty" (added 2026-07-19) lists every ticker with any
Position Target set — impossible to express under AND alone, since a single ticker rarely
has both a low and a high target set simultaneously.

**Column metadata drives the operator set.** Every column (`PORTFOLIO_CORE_COLUMNS` /
`WATCHLIST_CORE_COLUMNS` / `OPTIONAL_COLUMNS`, all from `table_columns_helpers.py`) now
carries a `fmt` — core columns didn't need one before this feature, since
`build_optional_column_cells()`/`_format_value()` only ever run over `OPTIONAL_COLUMNS`.
`advanced_filter.js`'s `FMT_FAMILY` map collapses the existing `fmt` vocabulary down to
four operator families:

- **numeric** (`pct_from_fraction`, `pct_raw`, `ratio2`, `int`, `price`, `price_raw`,
  `currency_usd`, `volume`, `client`) — `>`, `≥`, `<`, `≤`, `=`, `≠`, between, is
  empty/not empty.
- **text** (`text`) — contains, does not contain, equals, does not equal, is empty/not
  empty. `trend_50d`/`trend_200d` are deliberately classified `text` rather than
  `bool01` — their cell literally renders `UP`/`DOWN`, so "contains UP" reads far more
  naturally than a generic "is true"/"is false" would for a directional column.
- **date** (`date`) — before, after, on, between, is empty/not empty.
- **bool** (`bool01`, e.g. `kc_entry_signal`/`kc_exit_signal`) — is true / is false only.

**Comparisons read `data-sort`, never the rendered/formatted text.** A cell's display
text is sometimes abbreviated (`_fmt_currency`/`_fmt_volume`) or intentionally rescaled
for readability, which would make "type a number, compare against the number you see" a
different operation per column. Reading `data-sort` sidesteps that — it's already the
same raw value DataTables sorts on — with one normalization: `NUMERIC_SCALE` multiplies
`pct_from_fraction` columns (raw fraction, e.g. `0.03`) by 100 before comparing, so the
user types `3` to mean 3%, matching what `_format_value()` shows on screen. `pct_raw`
columns (already percent-scale at the source, e.g. `ml_conf`) use scale 1. This mirrors
the exact fraction-vs-percent distinction AGENTS.md's "Fundamentals unit conventions"
rule already documents for `roe`/`debt_to_equity` — get it backwards here and a filter
like "VaR (95%) > 3" would silently compare against `0.03` instead of `3`.

**Missing values never satisfy a comparison operator other than is-empty/is-not-empty.**
A cell is "missing" if its trimmed text is one of `N/A`/`-`/`—`/empty (`MISSING_TEXT`) —
covering every placeholder string used across both templates' hand-written `<td>` cells
and `_format_value()`'s own `"N/A"` output. Without this check, a sentinel sort value used
to pin missing rows to the bottom/top of a sort (e.g. `-999`, `9999-12-31`) would silently
satisfy an unrelated numeric/date range filter — a missing RSI (`data-sort="-999"`)
matching `rsi < 70` would be wrong, not just unhelpful.

**Cell lookup is by DataTables column index, not `data-col-key`.** Core-column `<td>`
elements were never tagged with `data-col-key` (only optional/position-sizing columns
are) since `column_picker.js` addresses core columns by index. Rather than retrofitting
every core `<td>` in both templates, `advanced_filter.js` resolves a condition's column
key to its index in the same `allColumns` array the page already exposes
(`window.PORTFOLIO_COLUMNS`/`WATCHLIST_COLUMNS`, core + optional in exact DataTables
column order) and reads the cell via `table.cell(dataIndex, colIndex).node()` — the same
approach the pre-existing Sector quick-filter predicate already uses via
`table.row(dataIndex).node()`. Position Sizing (`client` fmt) columns work the same way:
`renderPositionSizing()` already runs before `.DataTable()` is constructed, so their
`data-sort`/text are populated before the filter predicate (or the page's initial
`localStorage` restore) ever runs.

**Persistence has two independent layers, exactly mirroring how Views already treat
column visibility:**

- **Ad hoc** (not tied to a saved view): kept only in `localStorage`
  (`{scope}_adv_filter`, e.g. `portfolio_adv_filter`) — sticky per browser/machine, never
  sent to the server. Restored on page load via `AdvancedFilter.init()`'s own call to
  `applyFilter(loadFromStorage(), false)` (the trailing `false` skips re-writing what was
  just read).
- **Saved with a View**: `TableView.filter` (`api_routes.py`, `Optional[TableFilterSpec]`
  — `{logic: "AND"|"OR", conditions: [TableFilterCondition, ...]}`) travels through the
  exact same `POST /api/ui-preferences/views` full-list replacement `columns` already
  uses — no new endpoint. `model_dump(exclude_none=True)` keeps a filter-less view's
  stored shape identical to before this feature (no stray `"filter": null` key), so older
  saved views round-trip unchanged.

  Applying a view fully replaces the active filter (its `filter` spec, or none) — there is
  no separate "current view" concept to merge against, matching the "Applying a view does
  not introduce a second visibility engine" principle above. Applying a view's filter also
  overwrites the ad hoc `localStorage` value, since (again matching columns) a view is
  just a bulk shortcut for setting the one piece of "current filter" state, not a second
  persisted concept living alongside it.

  **Wire-format migration (2026-07-19):** the filter's `logic` field was added after the
  feature's initial ship, when `filter` was still a bare `List[TableFilterCondition]` with
  no wrapper object and an implicit AND. `advanced_filter.js`'s `normalizeFilterSpec(raw)`
  treats a bare array (old shape, from `localStorage` or an already-saved view) as
  `{logic: 'AND', conditions: raw}` — the only backward-compat surface this needed, since
  `resolve_views()`/`localStorage` reads are never themselves validated against the
  `TableFilterSpec` Pydantic model (that only gates what the client POSTs going forward).

`static/js/column_picker.js`'s `initViewsMenu(picker, opts)` gained two optional hooks to
wire this in without column_picker.js knowing anything about filters itself:
`opts.getExtraViewData()` (called on Save Current; its return value is merged into the
saved view object — Advanced Filter passes `{filter: advFilter.getCurrentFilter()}`) and
`opts.onApplyView(view)` (called after a saved view is applied; Advanced Filter calls
`advFilter.applyFilter(view.filter || [])`).

**Active-view indicator and delete confirmation (2026-07-19):** the Views dropdown marks
whichever saved view (if any) exactly matches the *current* live state — visible-column
set plus active filter — with a "✓ Active" badge, via `initViewsMenu`'s private
`isViewActive(view)` (column-set equality via `_columnSetsEqual`, filter equality via
`_filtersEqual`, both comparing structurally rather than with `JSON.stringify` so object
key order can't produce a false mismatch). This derives "active" from state comparison on
every render rather than tracking a separate "last-applied view" variable — deliberately
consistent with the no-separate-current-view-concept principle above, and it means the
badge disappears the instant a checkbox/filter edit diverges from every saved view, with
no "detach from view" bookkeeping needed. Recomputed on every `renderMenu()` call
(after apply/save/delete) and additionally on the dropdown's own `show.bs.dropdown` event
(`menuEl.closest('.dropdown')`), since checkbox toggles in the separate Columns dropdown
don't call back into `initViewsMenu` — reopening the Views dropdown is the point the badge
needs to be fresh, not every intervening column-visibility click. Deleting a view now asks
`confirm('Delete the view "..."? This cannot be undone.')` first, matching every other
destructive action's `confirm()` pattern elsewhere in the app (e.g.
`static/js/accounts.js`, `static/js/notifications.js`) — there was previously no
confirmation at all, so a stray click permanently discarded a saved view (columns and any
filter) with no undo.

## Front-end — `static/js/column_picker.js`

Shared module (same pattern as `chart_fullscreen.js` for charts), two entry points:

- `ColumnPicker.init(opts)` — wires the Columns dropdown menu (grouped by
  `category`), returns `{isVisible, applyView, getCurrentVisibleKeys}`.
  `ColumnPicker.resolveVisible(key, allColumns, prefs)` is also exported standalone
  (a pure function, no DOM/table needed) so pages can compute the initial
  `columnDefs` `visible` array *before* `.DataTable()` is even constructed,
  avoiding a flash of all-columns-visible.
- `ColumnPicker.initViewsMenu(picker, opts)` — wires the Views dropdown, given the
  `picker` object `init()` returned. Renders each saved view as a clickable
  name (applies it) + delete button, plus a name input + "Save Current" button
  that upserts (by name) the picker's `getCurrentVisibleKeys()` as a new/updated
  view. `opts.getExtraViewData()`/`opts.onApplyView(view)` (both optional) let a
  caller attach extra state to a saved view and react when one is applied — see
  "Advanced Filter" above for the only current user of these hooks.

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
