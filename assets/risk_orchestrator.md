# Portfolio Heat Index (Risk Orchestrator)

Synthesizes three previously-independent risk signals — VaR, correlation, and drawdown —
into a single 0-100 Portfolio Heat Index (PHI) per account scope, plus a per-ticker Risk
Contribution tier. Mirrors `quant_signals.py`'s composite "System Verdict" score, but risk-
focused and portfolio-scoped rather than per-ticker-technicals-scoped.

Page routes: `GET /portfolio-heat-index` (Reports hub), plus compact widgets on `/portfolio`
(the `"all"` scope) and `/accounts/{id}` (that account's own scope).
Engine: `risk_orchestrator_engine.py`
Scheduler job: `risk_orchestrator_job` (daily Mon-Fri, config `SCHEDULING.RISK_ORCHESTRATOR`)
DB tables: `portfolio_heat_index`, `ticker_risk_contribution`, `pretrade_check_log` (§6)

Phase 1 (scoring + dashboard) shipped first; Pillar A (pre-trade gatekeeper, §6 below) shipped
next; Pillar C2 (critical escalations, §7 below) shipped after that. Pillar C1 (daily digest)
remains open — see `audit/risk_orchestrator_plan.md` for that follow-up plan.

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
change only, not a gate on Position Sizing.

## 5. Settings

Settings → Position Sizing Defaults / X-Ray Allocation Targets column → "Risk Orchestrator"
card: enable/schedule time, the three weights (must sum to 100%), and the four threshold
pairs (PHI, VaR%, Max Correlation, Drawdown%). All default to the values above.

## 6. Pre-Trade Gatekeeper (Pillar A)

An on-demand, advisory-only what-if check surfaced in the Stock Detail page's Position Sizing
(Risk Parity) panel: as the user adjusts Account Size / Risk per Trade / Stop Multiple, the panel
also asks "if I added this position's value to my portfolio right now, what would happen to my
VaR and correlation risk?" — using the exact same thresholds/weights as the passive PHI dashboard
above, not a parallel scoring system. `GET /api/risk-orchestrator/pretrade-check` (see
`assets/api_reference.md` §30) wraps `risk_orchestrator_engine.evaluate_pretrade_check()`.

**What's simulated vs. reused.** `xray_engine.simulate_scope_with_hypothetical_holding(scope,
ticker, additional_value)` recomputes only the VaR and max-pairwise-correlation halves of
`risk_metrics` — it adds a synthetic holding to the scope's weight vector, sources that ticker's
own return series (preferring the cached `xray_returns_cache` row for an already-held ticker,
falling back to a live parquet read via `fetch_close_returns_from_parquet()` for one that isn't),
computes its correlation against every other currently-held ticker (from the cached correlation
matrix where available, or a fresh pairwise correlation against the cached series otherwise), and
re-derives the same `Sigma_ij = vol_i * vol_j * rho_ij` / parametric-VaR math `assemble_xray_report()`
already uses. **Max drawdown is deliberately not re-simulated** — it needs a full historical
portfolio-return blend, not just a covariance recompute — so `evaluate_pretrade_check()` reuses the
scope's current (unmodified) `max_drawdown` for that sub-score.

**Verdict.** The three sub-scores (recomputed VaR/correlation + reused drawdown) feed the same
`_sub_score()`/`_tier_for()` normalization as PHI, producing an `approve` / `warn` / `reject`
verdict from the resulting GREEN/YELLOW/RED tier, plus `breached_constraint` naming the worst RED
sub-metric (falling back to the worst YELLOW one if nothing is RED). On `warn`/`reject`, a binary
search (`risk_orchestrator_engine._suggest_reduced_value()`, ≤6 iterations) finds the largest
smaller `value` that resolves to a better tier, returned as `suggested_reduced_value` — `null` when
no smaller size helps (e.g. the breach is driven by `Drawdown`, which doesn't move with this
position's size). Like the PHI Red badge, the verdict is **advisory only**: this app has no trade
execution to actually block a purchase.

**Audit trail.** Every check call is logged to `pretrade_check_log` (ticker, scope, proposed
value, verdict, breached constraint, PHI score, VaR%, max correlation, suggested reduced value,
timestamp) — append-only, since the same ticker can be checked many times a day as the user
adjusts size on the panel, unlike the snapshot-only `portfolio_heat_index`/`ticker_risk_contribution`
tables above.

## 7. Critical Escalations (Pillar C2)

Instant Nextcloud/in-app alerts for three computed severity conditions, each dispatched through
`notification_engine.notify()` and deduplicated via the shared `IntradayOrchestrator._evaluate_alert_gate()`
worsened/recovered/cooldown model (AGENTS.md rule 19) — **not** the static-threshold daily gate
HoldingLimit/AI Contagion use, since PHI/correlation/stop-distance are all computed severity
scores that can worsen or recover, not fixed user-set thresholds:

1. **PHI Critical** (`risk_orchestrator_phi_critical`) — a scope's PHI tier reaches RED.
2. **Correlation Spike** (`risk_orchestrator_correlation_spike`) — a scope's max pairwise
   correlation tier reaches RED.
3. **Stop Breach** (`risk_orchestrator_stop_breach`) — a held ticker's live price falls to or
   through its `stock_signals.atr_stop_loss`.

**Cadence.** PHI Critical and Correlation Spike are evaluated once daily, immediately after
`risk_orchestrator_job`'s scan persists `portfolio_heat_index` — `scheduler_jobs._fire_risk_orchestrator_critical_alerts()`
reads back every scope via `risk_orchestrator_engine.get_critical_scopes()` and fires per RED
scope. Stop Breach runs on the existing 5-minute `intraday_orchestrator.py` scan loop, since a
price can cross a stop far faster than the daily schedule — checked in the same per-ticker pass
as `_check_holding_limits()`, for held tickers only (not Watchlist-only target rows).

**Gate polarity.** `_evaluate_alert_gate()` needs a "worsening vs. recovery" direction per engine:
`AtrStopBreach` mirrors Crash (falling further below the stop is worsening; rising back above it
is recovery — `current_price` here is a real price). `PhiCritical`/`CorrelationSpike` mirror
Moonshot (rising further is worsening; falling back is recovery — `current_price` here carries the
PHI score / max-correlation value, not a price, the same substitution TrapMonitor already uses
for `ema_distance`).

**Settings.** One shared cooldown block, `NOTIFICATIONS.RISK_ORCHESTRATOR_ALERTS` (enable,
cooldown minutes, retrigger %, rearm %), covers all three conditions — split into per-condition
blocks only if real usage shows they need to diverge. Configured in the same "Risk Orchestrator"
settings card as the PHI thresholds/weights above.
