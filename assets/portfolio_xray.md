# 🔮 Portfolio X-ray — Risk & Diagnostics Report

The Portfolio X-ray is a same-page risk diagnostics view focused on the real blind spots of a small retail trader: hidden concentration, hidden correlation between holdings, and market/sector exposure that isn't visible from the holdings table alone.

It is explicitly **not** an institutional X-ray. There are no factor-model loadings, no tracking error, no benchmark attribution. It answers one ruthless question: *"Where am I secretly over-exposed, and what could hurt me that I'm not seeing?"*

---

## 🖥️ 1. How to Access It

The X-ray is embedded in the **Portfolio page**. It is not a separate page.

1. Open the **Portfolio** view.
2. In the **Total Investment bar** (the coloured bar directly below Market Pulse), click the **🔮 X-ray** button on the right side.
3. The portfolio table disappears and the X-ray report renders in its place.
4. To return to the table, click **← Back to Portfolio**.

**Account awareness:** The X-ray reads from the same account dropdown at the top-left of the page as the rest of the dashboard.

- Changing the dropdown **while in X-ray mode** re-renders the X-ray for the newly selected account without navigating away.
- Selecting **Global (All Accounts)** runs the X-ray across all active accounts combined, netting positions held in multiple accounts into a single weight (no double-counting).
- The **Total Investment / Market Value / P&L** figures in the bar above update live as you switch accounts in X-ray mode, and revert to the original values when you click back.

> **Cash is excluded everywhere.** All weights and percentages represent % of *invested capital* only. A tooltip on every percentage field states this explicitly.

---

## 📊 2. What the Report Shows

The report is split into four visual rows.

### Row 1 — Headline Risk Cards

Six scannable single-number cards, each with a `?` tooltip explaining the metric in plain English:

| Card | What it measures | Colour thresholds |
|---|---|---|
| **Portfolio Beta** | How much the portfolio moves relative to the market. 1.4 = portfolio tends to drop ~14% when the market drops 10%. | Amber > 1.1 · Red > 1.5 |
| **Ann. Volatility** | Annualised standard deviation of daily returns — typical magnitude of annual price swings. | — |
| **Max Drawdown** | Worst peak-to-trough decline over the full history available in Ghostfolio. | Amber < −15% · Red < −25% |
| **VaR 95% (1-day)** | Parametric Value at Risk: on a bad day (worst 5% of days), you could lose approximately this amount. | — |
| **HHI Score** | Herfindahl-Hirschman Index — portfolio concentration score from 0 to 1. Below 0.15 = well diversified; above 0.25 = concentrated. | Amber > 0.15 · Red > 0.25 |
| **Top-5 Weight** | Percentage of the portfolio held in the five largest positions. | Amber > 35% · Red > 50% |

### Row 2 — Allocation Overview (Three Donut Charts)

| Chart | What it shows |
|---|---|
| **Instrument Type** | Direct equity vs ETF vs commodity — what you actually hold, at face value. |
| **True Sector Exposure** | ETFs are decomposed into their underlying sector weights (look-through). A global ETF that is 30% Technology contributes 30% of *its portfolio weight* to the Technology slice. Labelled **look-through** to make it unambiguous. |
| **Geographic Exposure** | Regional breakdown by continent, also look-through via Ghostfolio's `countries[]` data. |

### Row 3 — Position Concentration

Horizontal bar chart of all holdings sorted by weight descending. Each bar is colour-coded:
- **Blue** — below the 10% amber threshold  
- **Amber** — between 10% and 20%  
- **Red** — above 20%

Dotted threshold lines at 10% and 20% are drawn on the chart. This makes over-concentration immediately visible without reading numbers.

### Row 4 — Income & Unrealised P&L

Two income cards (weighted dividend yield + projected annual income) sit alongside a bar chart of every holding's unrealised gross P&L in base currency. Winners are teal; losers are red. Percentages are overlaid on each bar.

---

## 🗄️ 3. Data Architecture — Two Tiers

The X-ray uses two data tiers to keep page load fast.

### Tier A — Live Ghostfolio (fetched on demand)

Every time the X-ray loads or the account dropdown changes, a fresh API call is made to your Ghostfolio instance:

| Data | Ghostfolio endpoint |
|---|---|
| Holdings, weights, P&L | `GET /api/v1/portfolio/details?accounts=<scope>&range=max&withMarkets=true&withSummary=true` |
| Sector & geographic look-through | `holdings[].assetProfile.sectors[]` and `.countries[]` |
| Portfolio net-worth chart (for Max Drawdown) | `GET /api/v2/portfolio/performance?range=max&accounts=<scope>` |
| Per-holding dividend yield | `GET /api/v1/portfolio/holding/{dataSource}/{symbol}` (cached by scheduler, not fetched live) |

**Cash exclusion:** holdings are filtered by `assetProfile.assetClass == "CASH"` and by an explicit set of ISO 4217 currency codes. The portfolio total is derived from the sum of `valueInBaseCurrency` across included holdings — `summary.totalValueInBaseCurrency` is deliberately ignored (it is contaminated by excluded accounts in some Ghostfolio configurations).

**Account scoping:** `Global` passes the explicit list of in-scope account IDs to Ghostfolio's `?accounts=` parameter. Individual accounts pass the single selected ID. The in-scope list is read from `config["GHOSTFOLIO_ACCOUNTS"]["active"]` — never from Ghostfolio's own `isExcluded` flag.

### Tier C — yfinance + SQLite Cache (pre-computed nightly)

The expensive calculations — beta, annualised volatility, and the pairwise correlation used for portfolio vol and the diversification score — are pre-computed by an APScheduler background job and stored in SQLite. **Page load never triggers a yfinance call.**

| SQLite Table | Contents |
|---|---|
| `xray_risk_cache` | Per-ticker beta vs SWDA.L and annualised volatility |
| `xray_correlation_matrix` | Full N×N pairwise correlation matrix as a JSON blob |
| `xray_dividend_cache` | Per-holding dividend yield and projected income |

**Benchmark:** `SWDA.L` (iShares MSCI World ETF, London Stock Exchange). It is always fetched independently from Yahoo Finance regardless of whether it is a holding. The portfolio is GBP-denominated and globally diversified, making a global equity benchmark more appropriate than a US-only index.  
**Lookback window:** 252 trading days (1 year).

---

## ⚙️ 4. Risk Metric Formulae

| Metric | Formula |
|---|---|
| **Beta (per asset)** | `Cov(r_asset, r_benchmark) / Var(r_benchmark)` |
| **Portfolio Beta** | `Σ wᵢ · βᵢ` over de-cashed weights |
| **Ann. Volatility (per asset)** | `std(daily returns) × √252` |
| **Portfolio Volatility** | `√(wᵀ · Σ · w)` where `Σᵢⱼ = σᵢ · σⱼ · ρᵢⱼ` (daily), then annualised × √252 |
| **VaR 95% 1-day (parametric)** | `portfolio_daily_vol × 1.6449 × portfolio_value` |
| **Max Drawdown** | `min over t of (valueₜ / running_max(value) − 1)` from the Ghostfolio net-worth chart |
| **HHI** | `Σ wᵢ²` |

---

## 🔄 5. The Scheduler Job

**Job ID:** `xray_risk_cache_job`  
**Schedule:** Mon–Fri at 19:00 (after market close)  
**Entry point:** `xray_engine.run_xray_precompute()`

The job:
1. Reads all portfolio tickers from `portfolio.json`.
2. Fetches 1-year daily adjusted returns for every ticker **plus** the SWDA.L benchmark from Yahoo Finance.
3. Computes beta and annualised volatility per ticker and writes to `xray_risk_cache`.
4. Computes the full pairwise correlation matrix and writes to `xray_correlation_matrix`.
5. Authenticates with Ghostfolio and fetches per-holding dividend yield, writing to `xray_dividend_cache`.

**First-run / immediate population:** Go to **Settings → 🤖 Background Automation Schedulers → 🔮 Portfolio X-ray Risk Cache** and click **▶️ Run Now**. This queues the job immediately. The job may take 30–60 seconds depending on portfolio size and Yahoo Finance response times. Check System Notifications for the completion message.

If the cache has not yet run, the X-ray still loads — the allocation charts and concentration bar are driven entirely by live Ghostfolio data. The headline risk cards will show `N/A` for beta, volatility, VaR, and the diversification score, and a yellow warning banner will explain why.

---

## 📁 6. Key Files

| File | Role |
|---|---|
| `xray_engine.py` | All backend logic: `GhostfolioXRayClient`, `XRayRiskComputer`, `assemble_xray_report()`, centralised `XRAY_TOOLTIPS` glossary |
| `database.py` | Schema for the three X-ray cache tables (`xray_risk_cache`, `xray_correlation_matrix`, `xray_dividend_cache`) |
| `scheduler_jobs.py` | `run_xray_risk_cache_job()` wrapper |
| `scheduler_engine.py` | Always-on CronTrigger registration in `reload_scheduler()` |
| `api_routes.py` | `GET /api/xray?account_id=` (report) · `POST /api/xray/trigger` (manual trigger) |
| `templates/portfolio.html` | Plotly CDN, X-ray link in summary bar, panel `<div>`, all chart/render JavaScript |
| `static/css/styles.css` | All X-ray CSS (appended at end of file under `Portfolio X-ray Panel` comment) |

---

## ⚠️ 7. Known Limitations

- **Risk metrics require the cache to have run at least once.** On a brand-new installation, click **▶️ Run Now** in Settings before using the X-ray.
- **New holdings added after the last cache run** will show `N/A` for beta/vol until the next scheduled run. A warning banner lists the affected tickers.
- **Dividend data** is per-holding and fetched via Ghostfolio one call at a time, so it is cached by the scheduler rather than on page load. Accumulating funds (e.g. most Vanguard ETFs) correctly return 0.
- **ETF look-through sub-holdings** (`assetProfile.holdings[]`) are top-10 only in Ghostfolio and are not used for concentration math. Sector and geographic look-through via `assetProfile.sectors[]` and `assetProfile.countries[]` are complete and accurate.
- **Max drawdown** reflects the full history available in Ghostfolio (from first recorded transaction), not a fixed rolling window.
