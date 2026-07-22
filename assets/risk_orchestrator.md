# Portfolio Heat Index (Risk Orchestrator)

Synthesizes three previously-independent risk signals — VaR, correlation, and drawdown —
into a single 0-100 Portfolio Heat Index (PHI) per account scope, plus a per-ticker Risk
Contribution tier. Mirrors `quant_signals.py`'s composite "System Verdict" score, but risk-
focused and portfolio-scoped rather than per-ticker-technicals-scoped.

Page routes: `GET /portfolio-heat-index` (Reports hub), plus compact widgets on `/portfolio`
(the `"all"` scope) and `/accounts/{id}` (that account's own scope).
Engine: `risk_orchestrator_engine.py`
Scheduler job: `risk_orchestrator_job` (daily Mon-Fri, config `SCHEDULING.RISK_ORCHESTRATOR`)
DB tables: `portfolio_heat_index`, `ticker_risk_contribution`

This is Phase 1 (scoring + dashboard) of a larger plan. Pre-trade gatekeeper checks (wiring
a "what-if I buy N shares" check into the Position Sizing panel) and a tiered alerting layer
(daily digest + critical escalations) are deliberately out of scope here — see
`audit/risk_orchestrator_plan.md` for that follow-up plan.

---

## 1. Scope convention

Reuses `xray_engine.resolve_scope_holdings()`'s existing scope strings rather than inventing
a new one: `"all"` (every configured source combined) or `"acct:{id}"` (one built-in Trading
account). `risk_orchestrator_engine.run_scan()` computes the `"all"` scope plus one scope per
account returned by `accounts_engine.list_scope_accounts_with_values()` (the same Trading-
account picker shared with the Portfolio Tearsheet and Monte Carlo Wealth Simulator).

## 2. Formula

For each scope, `compute_portfolio_heat()` calls `xray_engine.assemble_xray_report(scope)` and
reads three existing values from its `risk_metrics`:

- **VaR** — `var_95_1d` (parametric 1-day 95% VaR), expressed as a percentage of that scope's
  `portfolio_total_value`.
- **Max pairwise correlation** — `max_pairwise_correlation`, added to `assemble_xray_report()`
  alongside the pre-existing `avg_pairwise_correlation` (same off-diagonal correlation values
  already computed for the average, just also taking the max — not a second calculation pass).
- **Max drawdown** — `max_drawdown` (already computed via `xray_engine.native_max_drawdown()`
  on the scope's blended return series), taken as an absolute percentage.

Each input is normalized to a 0-100 sub-score via linear interpolation against its own
configurable Yellow/Red threshold (0-50 below Yellow, 50-100 between Yellow and Red, clamped
at 100 beyond Red — `risk_orchestrator_engine._sub_score()`). The PHI is the configured-weight
sum of the three sub-scores (default 40% VaR / 30% correlation / 30% drawdown), clamped 0-100,
then bucketed into GREEN/YELLOW/RED against configurable PHI thresholds (default 40/75).

## 3. Ticker Risk Contribution

For every ticker held in the `"all"` scope, `compute_ticker_risk_contributions()` derives the
same three-input, same-formula tier using ticker-level inputs instead of portfolio-level ones:

- **Marginal VaR contribution** — this ticker's `marginal_risk_contribution` (the Euler/
  marginal risk decomposition `assemble_xray_report()` already computes per holding) as a
  percentage of the sum of all holdings' marginal contributions.
- **Max pairwise correlation** — this ticker's own highest correlation with any other current
  holding (also added to `assemble_xray_report()`'s per-holding payload alongside the scope-
  wide max above).
- **Stop distance** — `(current_price - stock_signals.atr_stop_loss) / current_price`, a new,
  cheap calculation; a smaller (or negative) distance is riskier, so it's inverted before
  normalizing against the drawdown thresholds.

Both tables (`portfolio_heat_index`, `ticker_risk_contribution`) are latest-snapshot only —
no history — matching the style of `xray_correlation_matrix`/`market_pulse_cache`.

## 4. Visual-only "circuit breaker"

When the `"all"`-scope PHI is Red, `page_helpers.get_all_scope_heat_tier()` drives a
presentational-only warning badge ("RISK PAUSED") next to any BUY-flavored `overall_signal`
on the Portfolio, Watchlist, and Stock Detail pages. This is deliberately **not** a blocking
mechanism — the app has no trade execution to block, so "halting new buys" is a visibility
change only, not a gate on Position Sizing (that's Pillar A of the follow-up plan).

## 5. Settings

Settings → Position Sizing Defaults / X-Ray Allocation Targets column → "Risk Orchestrator"
card: enable/schedule time, the three weights (must sum to 100%), and the four threshold
pairs (PHI, VaR%, Max Correlation, Drawdown%). All default to the values above.
