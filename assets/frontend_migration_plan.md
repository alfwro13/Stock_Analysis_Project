# Frontend Migration — Bootstrap 5 + DataTables Responsive

This is the **canonical guide and tracker** for migrating the dashboard frontend to
Bootstrap 5.3 with the DataTables Responsive extension. The goal: one UI that is fully
usable on a large desktop **and** a phone, **without losing any desktop functionality**.
On mobile, less data is shown *by design* — DataTables Responsive collapses low-priority
columns into an expandable child row; nothing is deleted.

The migration is staged **one page at a time**. Each page is migrated by a fresh
chat/agent using the reusable prompt at the bottom of this file, so no single session
carries the whole job.

---

## Target architecture (already in place after Stage 0)

- **Vendored assets** (no CDN at runtime) under `static/vendor/`:
  `bootstrap/` (5.3.3 CSS + bundle JS), `jquery/` (3.7.1), `datatables/`
  (DataTables 1.13.7 core + Bootstrap5 + Responsive 2.5.0 + responsive.bootstrap5).
  Plotly is **not yet vendored** — chart pages keep their existing
  `https://cdn.plot.ly/plotly-2.27.0.min.js` until Stage 3, when it moves to
  `static/vendor/plotly/`.
- **`templates/base.html`** — the single shell. Pages do `{% extends "base.html" %}` and
  fill these blocks: `title`, `extra_head` (page CSS / Plotly), `body_class`,
  `container_class`, `content`, `scripts` (page JS). It sets `<html data-bs-theme="dark">`,
  loads the vendored stack, renders the Bootstrap navbar (`{% if not embed %}`), and
  derives the active nav item from `request.url.path` (no per-page/route change needed).
- **Theming without Sass** — a `BOOTSTRAP 5 THEME LAYER` block at the top of
  `static/css/styles.css` overrides `--bs-*` variables (and a few component-local vars:
  `.btn-primary`, `.card`, `.table`, `.form-control`, `.accordion`, `.pagination`, …) to
  reproduce the dark palette (`#121212` bg, `#4da6ff` primary, `#00ffcc` accent).
  `styles.css` is loaded **last** in base.html so it wins.
- **Legacy navbar** — `templates/navbar.html` is **untouched** and still `{% include %}`-d
  by un-migrated pages (which do not load Bootstrap). base.html has its own Bootstrap
  navbar. The notification poller + freshness badge for base.html pages live in
  `static/js/navbar.js`. Both navbars are removed/consolidated in Stage 6.

### Load order (in base.html — do not reorder)
CSS: `bootstrap.min.css` → `dataTables.bootstrap5.min.css` → `responsive.bootstrap5.min.css` → `styles.css?v=…`
JS: `jquery` → `bootstrap.bundle` → `dataTables` → `dataTables.bootstrap5` → `dataTables.responsive` → `responsive.bootstrap5` → `csrf.js` → `navbar.js` → page `{% block scripts %}`

---

## Coexistence rules (legacy + migrated pages share `styles.css`)

- **Never delete a shared custom CSS class while any un-migrated page still uses it.**
  Removing dead CSS happens only in **Stage 6**, by grepping each class across
  `templates/` and deleting rules with zero references.
- Class-name collisions with Bootstrap were resolved as follows:
  - `.container` / `.container-fluid` / bare `.btn`: **0 template uses** — Bootstrap's
    versions are free. (The bare `.btn` was removed from the legacy grouped selector.)
  - `.badge`: 54 legacy uses — the **legacy `.badge` is kept**; migrated pages keep using
    it rather than switching to a Bootstrap badge that would collide.
  - Utility collisions (`.text-danger`, `.d-none`, `.w-100`, `.text-center`, `.mb-0`…):
    the legacy values load last and win; intent is the same, only shades differ slightly.
    Acceptable; do not fight them.
- The legacy `table.dataTable …` dark overrides still apply to migrated DataTables and
  reinforce the dark theme — that is fine. The DataTables **Bootstrap5** integration styles
  the wrapper/length/info/pagination and the Responsive child row.

---

## Per-page migration recipe

1. Read `AGENTS.md`, this guide, and the reference page **`templates/watchlist.html`** +
   **`static/js/watchlist.js`** (the canonical Stage-0 example).
2. Convert the page to `{% extends "base.html" %}`. Delete its duplicated
   `<!DOCTYPE>`/`<head>`/`<body>`/navbar boilerplate. Move page CSS/Plotly into
   `{% block extra_head %}`, body into `{% block content %}`, page JS into
   `{% block scripts %}`. The page `<h1>` goes at the top of `content` (the navbar brand is
   the logo).
3. Replace custom layout (`.detail-split-grid`, `.settings-split-grid`, `.sentiment-split-grid`,
   ad-hoc flex) with the Bootstrap grid (`.row` + `.col-12 .col-lg-*`) and spacing utilities.
   Use breakpoint classes (`d-none d-md-table-cell`, `col-md-*`) for desktop-vs-mobile density.
4. Replace components with themed Bootstrap: buttons (`.btn .btn-primary` / `.btn-outline-*`),
   cards (`.card`), forms (`.form-control` / `.form-select` / `.input-group`), `<details>`
   settings cards → `.accordion`, tabs → `.nav-tabs`, modals. **Keep element IDs** that JS
   targets.
5. **Tables:** `<table class="table table-hover w-100">`, init with `responsive: true` +
   per-column `responsivePriority` so the full set shows on desktop and the essentials
   (e.g. ticker, signal, price, score) survive on a phone. Keep the client-side full-array
   data load (no server-side processing). Reconcile any custom external filter dropdowns.
6. **Charts (Stage 3+):** keep Plotly `responsive:true`; call `Plotly.Plots.resize(el)` on
   Bootstrap `shown.bs.tab` / `shown.bs.collapse` and on window resize so charts size
   correctly when revealed inside tabs/collapses/grid columns.
7. Preserve: embed mode (`{% if not embed %}` around header/nav-only content), `csrf.js`,
   `position_sizing.js`, and all time display through `time_engine`.
8. Extract any `<script>` block > ~50 lines (with no Jinja) to `static/js/<page>.js`; keep a
   tiny inline bootstrap for `window.*` Jinja-derived globals. Never put `{{ }}` in a `.js` file.
9. Bump `CSS_VERSION` in `constants.py`.
10. `source venv/bin/activate && ./run_tests.sh` — fix every failure before done.
11. Manually verify in a browser: desktop wide, phone width (devtools responsive ~390px),
    and `?embed=true` for portfolio/watchlist. Check the console for load-order errors.
12. Update the tracker row below and any docs the page affects.

---

## Staged page tracker

Status: ☐ todo · ◐ in progress · ☑ done

### Stage 0 — Foundation + pilot
- ☑ Vendored assets, `base.html`, Bootstrap navbar (`navbar.js`), theme layer, `CSS_VERSION` 4.0→5.0
- ☑ `watchlist` (`/watchlist`) — pilot / reference (`watchlist.js`)

### Stage 1 — Simple pages
- ☑ `tools` (`/tools`) · ☑ `notifications` (`/notifications`) · ☑ `score_history` (`/score-history`)
- ☑ `earnings_volatility` (`/earnings-volatility`)
- ☑ `glossary` (`/glossary`, `glossary.js`) · ☑ `etf_predictor` (`/etf-predictor`)
- ☑ `dip_radar_summary` (`/dip-radar`) · ☑ `bubble_radar` (`/bubble-radar`, `bubble_radar.js`)
- ☑ `ai_contagion` (`/ai-contagion`) · ☑ `log_viewer` (`/log-viewer`, `log_viewer.js`)
- ☑ `news` (`/news`, `news.js`) · ☑ `macro_cards.html` partial (consumed by portfolio/watchlist)

### Stage 2 — DataTables-heavy pages
- ☑ `portfolio` (`/portfolio`, `portfolio.js`, embed mode, X-ray panel)
- ☑ `market_screener` (`/market-screener`, `market_screener.js`) · ☑ `market_sentiment` (`/market-sentiment`, `market_sentiment.js`)
- ☑ `market_reports` (originally `/market-reports`, `market_reports.js`, 7 tables + custom filter bars) — split 2026-07-17 into 7 standalone report pages under the `/reports` hub, see `assets/reports_page.md`

### Stage 3 — Chart-heavy pages (vendor Plotly into `static/vendor/plotly/` here)
- ☑ `market_regime` (`/market-regime`, `market_regime.js`) · ☑ `stress_test` (`/stress-test`, `stress_test.js`)
- ☑ `options_sandbox` (`/options-sandbox`, `options_sandbox.js`) · ☑ `etf_predictor_detail` (`/etf-predictor/{id}`, `etf_predictor_detail.js`)
- ☑ `index_detail` (`/index/{ticker}`, `index_detail.js`)

### Stage 4 — Large/complex pages (one chat each)
- ☑ `stock_detail` (`/stock/{ticker}`, `stock_detail.js`) — Bootstrap grid, sticky right column, position-sizing panel, dip radar, all charts preserved; JS extracted to `static/js/stock_detail.js`
- ☑ `settings` (`/settings`, `settings.js`) — Bootstrap grid `.col-12.col-xl-6`, all inputs Bootstrap form classes, uniform `.btn.btn-primary` buttons, Workflow Monitor conflicts-only (graph in new tab), Bubble Radar persist fix

### Stage 5 — Auth pages (navbar suppressed, centered card layout)
- ☑ `login` (`/login`) — Bootstrap card, no navbar (`{% block navbar %}{% endblock %}`), `.auth-card-wrapper` CSS · ☑ `change_password` (`/change-password`) — same pattern; inline script retains `{{ confirm_token }}` Jinja · ☑ `reset_password` (`/reset-password`) — dual-mode (request/set) via `{% if token %}`; Jinja in script keeps it inline · ☑ `admin_reset_password` (`/admin-reset-password`) — `.alert.alert-warning` replaces custom warning banner

### Stage 6 — Cleanup
- ☑ `trap_monitor` (`/trap-monitor`, `trap_monitor.js`) — Bootstrap grid + custom CSS classes; 570-line IIFE extracted to `static/js/trap_monitor.js`; `d-none` used for JS-toggled situation card; signal cards use `.module-blue` / `.module-salmon` variants; arc-step modifiers converted to CSS classes
- ☑ Delete `templates/navbar.html` (no page included it after trap_monitor migration)
- ☑ Dead-CSS sweep: removed `.nav-links`, `.nav-hamburger`, `.header-container`, `.title-wrapper`, `.logo-link`, `.freshness-badge`, `.body-glossary` block, `.body-notifications` scoped rules, `.body-settings .header-container*`, `.header-container-wide`, plus legacy hamburger media-query rules
- ☑ Final `./run_tests.sh` — 1990 passed, 3 skipped · `CSS_VERSION` bumped to 5.32

**Note on partials:** `templates/partials/risk_summary.html` was not listed in the plan but was already Bootstrap-compatible (pure Jinja + existing CSS classes); it requires no migration.

---

## Open items / NEEDS REVIEW

- **Plotly vendored (Stage 3 start).** `static/vendor/plotly/plotly-2.27.0.min.js` added. Remaining Stage 3 pages (`stress_test`, `options_sandbox`, `etf_predictor_detail`, `index_detail`) should load it via `{% block extra_head %}<script src="/static/vendor/plotly/plotly-2.27.0.min.js"></script>{% endblock %}` — do NOT add CDN `<script>` tags.
- **Watchlist filter column indices — FIXED (operator-approved).** The pre-existing
  off-by-one was corrected in `static/js/watchlist.js`: Signal `column(18)`, tags/candle
  `column(17)`, score range `data[16]`.
- **Plotly vendoring** is deferred to Stage 3 (3.5 MB; only 8 chart pages need it).
- **Portfolio signal/tag filter column indices — FIXED.** Entry Zone + Exit Target columns were added after the original `portfolio.js` was written, shifting Signal/Tags indices by 2. Corrected in Stage 2 migration: Signal → `column(20)`, Tags → `column(19)`.

---

## Reusable per-page prompt (fill ONE slot: the template filename)

```
Migrate templates/<PAGE>.html to Bootstrap 5 + DataTables Responsive.

FIRST read, in full: AGENTS.md; assets/frontend_migration_plan.md; and the reference
implementation templates/watchlist.html + static/js/watchlist.js. Follow the "Per-page
migration recipe" in the migration guide exactly.

Discover the context yourself — do NOT rely on me to specify it:
- Find the route(s) that render this template by grepping page_routes.py. Note if it is
  instead an {% include %} partial with no direct route (e.g. macro_cards.html), or a route
  with path params (e.g. /stock/{ticker}).
- Inspect the file to see what it uses: DataTables (which table IDs), Plotly charts, embed
  mode, which /static/js files, and any included partials.

Constraints (from AGENTS.md):
- Full component rewrite: extend base.html; no duplicated <head>/navbar; Bootstrap grid +
  components, themed via the existing theme layer (do NOT add a second theme).
- All CSS in static/css/styles.css (no inline style="="); <abbr title> tooltips only.
- JS blocks > ~50 lines (no Jinja) -> static/js/<page>.js; never put {{ }} in a .js file.
- All time via time_engine; never datetime.now() without timezone.utc.
- Do NOT delete shared custom CSS (Stage 6 only). Keep element IDs that JS targets.
- Preserve embed mode, CSRF, position sizing, and exact data rendering.

Then: bump CSS_VERSION in constants.py; run `source venv/bin/activate && ./run_tests.sh`
and fix failures; manually verify desktop + phone-width (~390px) + (portfolio/watchlist)
?embed=true. Update the tracker row in assets/frontend_migration_plan.md and any docs this
page affects. Do not attempt to render the page yourself - ask operator to test it.

Report: files changed, route(s) found, tests run, docs updated, and any [NEEDS REVIEW]
items. Do not touch any other page.
```
