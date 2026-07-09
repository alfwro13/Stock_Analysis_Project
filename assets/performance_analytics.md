# Portfolio Tearsheet

## Purpose

A native performance-analytics report covering the metric set of the [quantstats](https://github.com/ranaroussi/quantstats) Python library — risk-adjusted return ratios, drawdown duration analysis, distribution/tail stats, and win/loss statistics — computed entirely from the app's own cached daily return history, with **no external dependency and no HTML report generator**.

quantstats itself was evaluated and rejected as a dependency: its `__init__.py` unconditionally imports `plots`/`reports`, dragging in `seaborn`/`tabulate` purely to satisfy an import chain for functionality never used, and its report generator renders static matplotlib images incompatible with this app's Bootstrap5 + Plotly.js front end. Instead, the ~18 metrics that are genuine gaps versus what `xray_engine.py` already computes were reimplemented natively in `performance_analytics_engine.py`, in this codebase's own style.

## Engine

**`performance_analytics_engine.py`** — pure computation, on-demand only, no scheduled job, no DB writes. Mirrors `monte_carlo_engine.run_simulation()`'s shape (one call in, one JSON-serialisable dict out).

Key functions:

| Function | Purpose |
|----------|---------|
| `assemble_performance_report(account_id)` | Public entrypoint — resolves the scope's holdings, fetches the return series, computes all metrics and chart data |
| `_sortino_ratio`, `_calmar_ratio`, `_omega_ratio`, `_profit_factor` | Risk-adjusted return ratios |
| `_drawdown_stats` | Longest drawdown duration, time underwater, Ulcer Index — from the full dated drawdown series |
| `_distribution_stats` | Best/worst day, best/worst month, tail ratio |
| `_win_loss_stats`, `_max_consecutive` | Win rate, average win/loss, payoff ratio, longest win/loss streaks |
| `_monthly_returns`, `_monthly_heatmap_matrix` | Calendar-month compounded returns, reshaped into a year × month grid for the heatmap chart |
| `_underwater_chart_data`, `_cumulative_growth_chart_data`, `_histogram_chart_data` | Chart-payload shaping |

It depends on three functions added to `xray_engine.py` in the same change (extracted from what was previously inline in `assemble_xray_report()`):

| Function | Purpose |
|----------|---------|
| `get_scope_return_series(holdings, total_value)` | Weighted daily portfolio + benchmark return series (`pd.Series`, `DatetimeIndex`) for an already-resolved scope, derived from `xray_returns_cache` — single source of truth shared by X-ray and the Tearsheet |
| `annualized_return(returns)` | CAGR-style annualisation of a daily return series |
| `native_max_drawdown(returns)` | Peak-to-trough max drawdown *and* the full dated drawdown series, derived from the return series itself — replaces a previous Ghostfolio-only performance-chart lookup that left X-ray's Calmar Ratio unavailable for portfolios using only built-in Trading accounts |

## Metrics

All metrics require **at least 30 overlapping cached trading days** across the scope's holdings (same threshold X-ray uses for its own return-series-derived metrics). Below that, `metrics`/`charts` are `null` and `data_warnings` explains why.

### Risk-Adjusted Return Ratios

```
Sortino  = (Annualised Return − Risk-Free Rate) / Downside Deviation
           Downside Deviation = std(daily returns < 0) × √252

Calmar   = Annualised Return / |Max Drawdown|

Omega    = Σ(daily excess return above rf/252, where positive)
           / |Σ(daily excess return above rf/252, where negative)|

Profit Factor = Σ(positive daily returns) / |Σ(negative daily returns)|
```

Risk-free rate is read from `config.json`'s `RISK_FREE_RATE` (default 4.5%) — the same key X-ray's Sharpe ratio uses.

### Drawdown Analytics

```
Max Drawdown   = min(cumprod(1 + returns) / running_max(cumprod(1 + returns)) − 1)
Longest Drawdown (days) = longest contiguous run where drawdown < 0
Time Underwater (days)  = days since the drawdown series last touched 0
Ulcer Index    = √(mean(drawdown_pct²))
```

### Distribution / Tail Stats

```
Best/Worst Day    = max/min(daily returns)
Best/Worst Month  = max/min(calendar-month compounded returns)
Tail Ratio         = |95th percentile daily return| / |5th percentile daily return|
```

### Win/Loss Stats

```
Win Rate            = count(returns > 0) / count(returns)
Average Win/Loss    = mean(positive returns) / mean(negative returns)
Payoff Ratio        = Average Win / |Average Loss|
Max Consecutive Wins/Losses = longest run of same-signed daily returns
```

## Charts

| Chart | Data |
|-------|------|
| Underwater / Drawdown | Full dated drawdown series, filled area |
| Cumulative Growth vs. Benchmark | Portfolio and benchmark return series, each indexed to 100 |
| Monthly Returns Heatmap | Year × month grid of compounded monthly returns |
| Daily Return Distribution | Histogram of daily returns with mean and VaR 95% reference lines |

All four are rendered client-side with Plotly (`static/js/performance_analytics.js`), following the same conventions as every other chart in the app (AGENTS.md rule 18): centered titles, legend below the plot, `yaxis.automargin`, a shared responsive-height helper, and the CSS class-toggle fullscreen pattern.

## Relationship to X-ray and Monte Carlo

- **X-ray** (`/portfolio` → X-ray panel) computes Sharpe ratio, historical VaR/CVaR, skewness/kurtosis, beta, volatility, and (as of this feature) Calmar Ratio and Max Drawdown — all from the same `xray_returns_cache`-derived return series the Tearsheet uses. The Tearsheet does not recompute any of these; it only adds metrics X-ray doesn't have.
- **Monte Carlo Wealth Simulator** (`/monte-carlo`) is forward-looking — it projects future wealth via correlated GBM simulation. The Tearsheet is backward-looking — it summarises historical performance. They share no calculations, but do share the account-tile picker component (`accounts_engine.list_scope_accounts_with_values()`, extracted in this change so both pages' account pickers stay in sync).

## Data sources

| Data | Source |
|------|--------|
| Per-ticker daily returns | `xray_returns_cache` (populated by the nightly X-ray risk cache job) |
| Holdings/weights | `xray_engine.resolve_scope_holdings(account_id)` — Ghostfolio (optional) + built-in Trading accounts |
| Risk-free rate | `config.json` → `RISK_FREE_RATE` |

No new database table and no new scheduled job — this feature reads an existing cache and computes everything at request time.

## API

`GET /api/performance-analytics/report?account_id=all|<ghostfolio-uuid>|acct:{id}` — returns `{"status": "success", "account_id": ..., "annualized_return": ..., "metrics": {...}, "charts": {...}, "data_warnings": [...]}` (`metrics`/`charts` are `null` if fewer than 30 overlapping cached days exist).

`GET /api/performance-analytics/accounts` — account-tile list + total value for the scope picker, shared with `GET /api/monte-carlo/accounts` via `accounts_engine.list_scope_accounts_with_values()`.

Full schema: see `assets/api_reference.md`.

## UI surface

`/performance-analytics` (Tools page card: "📊 Portfolio Tearsheet") — on-demand, no scheduler job. `scheduler_manifest.JOB_GRAPH` has no entry for it, matching the Monte Carlo Wealth Simulator precedent (also absent from the graph despite consuming `xray_correlation_matrix`/`xray_risk_cache`). This is a known minor gap versus AGENTS.md rule 13's "every subsystem visible in the Workflow Monitor" guidance, not a considered exception — see `audit/audit.md` Needs Review.

## Limitations

- **Native math, not a quantstats wrapper.** Formulas were reimplemented from quantstats' documented methodology, not copied from its source — while they follow standard, widely-published definitions (Sortino, Calmar, Omega, Ulcer Index), a byte-for-byte numeric match against `quantstats.stats.*` output was not verified.
- **30-day minimum applies per scope.** A newly-added holding, or a scope with sparse cached history, shows all metrics as unavailable until the next nightly risk-cache run accumulates enough overlapping days.
- **Benchmark-dependent chart.** The Cumulative Growth chart needs both the portfolio and benchmark (`SWDA.L`) return series aligned; if the benchmark isn't cached for the scope's tickers, that chart alone shows "no data" while metric cards still populate.
