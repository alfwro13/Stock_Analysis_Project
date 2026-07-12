# **🏛️ Sovereign Interest Rate Systematic Risk Architecture**

This specification document outlines the mathematical, database, and software architecture governing sovereign interest-rate systemic risk within the Quantamental Web Terminal.

## **1\. Macroeconomic Foundations: The Gravity of Interest Rates**

In institutional finance, interest rates function as the "gravitational pull" on all financial assets. When interest rates rise, the present value of future corporate cash flows shrinks. This compression impacts stock prices through two primary channels:

### **A. Discounted Cash Flow (DCF) Multiple Compression**

Equity valuations are calculated by discounting projected future cash flows back to the present day. The discount rate is heavily tied to the "risk-free" rate of return offered by government bonds. When government bond yields surge, the risk-free rate climbs, and — because the discount rate sits in the denominator of the DCF formula — any upward movement in sovereign yields mathematically shrinks the present value of all future cash flows. This effect is exponentially worse for high-growth tech companies (high-multiple stocks) because their most substantial cash flows are expected far in the future, compounding the discounting penalty.

### **B. Leverage & Debt Refinancing Vulnerability**

For highly leveraged companies, a high interest rate environment represents an immediate cash-drain threat. Corporations with high Debt-to-Equity ratios must refinance their maturing short-term debt at significantly higher coupon rates. This increases interest expenses, directly compressing net profit margins and raising default risks.

## **2\. Mathematical Implementations in the App**

The terminal implements **three** distinct, independently-scoped engines around sovereign yields — a daily per-country threat classifier, an intraday shock alert, and a per-asset rolling correlation. They read different tickers and run on different schedules; do not assume they share thresholds or a single "systemic threat level."

### **A. Daily Systemic Threat Classification — `regime_engine.py:calculate_systemic_macro_threat()`**

Runs once per day (as part of the `market_regime_job`, alongside `calculate_market_regime()`). Unlike the old unified model, the **US and UK sides are scored and stored completely independently** — there is no single blended `systemic_threat_level`; there are `us_threat_level` and `uk_threat_level` columns, each with its own thresholds.

**US side** is derived from **`^TNX` (the 10-Year US Treasury Note), not `^TYX` (the 30-Year Bond)**. `^TYX` is still fetched and stored (`tyx_close`) for display/reference, but it does not feed the threat classification.

**UK side** is derived from the scraped `UK_GILT_BASELINE.parquet` series (10Y Gilt, via `gilt_engine.py`), falling back to `^TNX`'s own value if the baseline parquet is missing.

For each side, the engine computes a 3-trading-day yield velocity in **basis points**, using a date-based lookback (not a fixed `iloc[-4]` offset) so it survives weekends/holidays:

```
us_velocity_bps  = (curr_tnx  - tnx_4_days_ago)  * 100.0
gilt_velocity_bps = (curr_gilt - gilt_4_days_ago) * 100.0
```

**Thresholds** (`regime_engine.py`, calibrated to the post-2022 rate environment — see Section 4 for the full table):

```
# US (^TNX-based)
if us_velocity_bps >= 30.0 or curr_tnx >= 4.75:
    us_threat_level = "RED"
elif us_velocity_bps >= 15.0 or curr_tnx >= 4.25:
    us_threat_level = "YELLOW"
else:
    us_threat_level = "GREEN"

# UK (Gilt-based)
if gilt_velocity_bps >= 30.0 or curr_gilt >= 5.0:
    uk_threat_level = "RED"
elif gilt_velocity_bps >= 15.0 or curr_gilt >= 4.5:
    uk_threat_level = "YELLOW"
else:
    uk_threat_level = "GREEN"
```

Both results are written to `macro_regimes` (see Section 3) and immediately feed `classify_macro_regime()`, which layers on the broader business-cycle label (`Expansion`/`Late Cycle`/`Contraction`/`Recovery`/`Risk-On`) used elsewhere in the app.

### **B. Intraday Yield Shock Warning — `intraday_orchestrator.py`**

A **separate, narrower** engine that runs inside the 5-minute intraday scan loop during active market hours. It watches exactly two tickers, `["^TYX", "SPY"]` — **this is the one place in the app that genuinely reacts to the 30Y Treasury**, and it has no UK Gilt component at all (the message-building branch for a non-`^TYX` ticker is currently dead code, since `^TYX` is the only yield ticker in that list).

It compares the current intraday price to the session's opening price:

```
m_spike = ((m_curr - m_open) / m_open) * 100.0
if m_spike >= _MACRO_YIELD_SURGE_PCT:   # 1.5%
    ...fire "SYSTEMIC MACRO ALERT" via notification_engine.notify()...
```

`_MACRO_YIELD_SURGE_PCT = 1.5` (a module-level constant in `intraday_orchestrator.py`). The alert is dispatched through the unified notification router (`notify()`, source `Macro`) rather than a direct Nextcloud call, and is deduplicated/cooldown-gated via `_evaluate_alert_gate()` like the other intraday condition-severity alerts (see AGENTS.md rule 19).

### **C. 60-Day Rolling Asset-Yield Correlation — `quant_signals.py`**

To determine if an individual stock is sensitive to interest rate fluctuations, the nightly Quant Scan (`QuantEngine.analyze_ticker()`) calculates the rolling Pearson correlation coefficient between the daily returns of the asset and the daily returns of a sovereign-yield benchmark over a 60-day window:

```python
yield_baseline = "UK_GILT_BASELINE" if is_uk_asset else "TYX_BASELINE"
...
rolling_corr = asset_returns.rolling(window=60).corr(yield_returns)
```

**This correlation deliberately still benchmarks US assets against the 30Y Treasury (`TYX_BASELINE.parquet`)**, not the 10Y — a genuine, intentional difference from Section A's threat classifier, which switched to 10Y. A `TNX_BASELINE.parquet` file is fetched and cached (`data_engine.py`) but is not currently consumed by this correlation calculation. UK assets are benchmarked against `UK_GILT_BASELINE`, same as Section A.

The result is stored per-ticker in `stock_signals.yield_correlation`. **The threshold:** a correlation ≤ **-0.3** indicates a strong inverse relationship, meaning the stock's price tends to fall whenever bond yields rise — this is the exact value the Stock Detail page checks (Section 5B).

## 3\. Database Schema & Storage Structures

```sql
-- Table tracking the daily per-country sovereign yield threat classification
CREATE TABLE IF NOT EXISTS macro_regimes (
    date TEXT PRIMARY KEY,
    tyx_close REAL,              -- US 30Y Treasury close yield (display/reference only — not used in threat classification)
    tnx_close REAL,              -- US 10Y Treasury close yield (drives us_threat_level)
    dxy_close REAL,              -- US Dollar Index close
    uk_gilt_close REAL,          -- UK 10Y Gilt close yield (drives uk_threat_level)
    gbpusd_close REAL,           -- GBP/USD exchange rate
    us_yield_velocity REAL,      -- 3-day US yield rate of change, in basis points
    us_threat_level TEXT,        -- GREEN, YELLOW, or RED
    uk_yield_velocity REAL,      -- 3-day UK gilt rate of change, in basis points
    uk_threat_level TEXT         -- GREEN, YELLOW, or RED
    -- plus market-turbulence/regime-label columns written by calculate_market_regime()
    -- and classify_macro_regime() — see db_schema.py for the full column set.
);

-- Table storing individual asset profiles with the yield-sensitivity correlation
CREATE TABLE IF NOT EXISTS stock_signals (
    ticker TEXT PRIMARY KEY,
    current_price REAL,
    trailing_pe REAL,
    debt_to_equity REAL,
    yield_correlation REAL,      -- 60-day rolling Pearson correlation vs. TYX_BASELINE (US) or UK_GILT_BASELINE (UK)
    composite_score INTEGER,
    overall_signal TEXT,
    educational_notes TEXT,
    setup_tags TEXT
);
```

There is no `systemic_threat_level` or `threat_source` column — those belonged to an earlier, unified design that was superseded by the independent `us_threat_level`/`uk_threat_level` split.

## 4\. Threat Level Thresholds & Classifications

The `regime_engine.py:calculate_systemic_macro_threat()` function maps each country's yield level and 3-day velocity into three independent danger zones. **US and UK have separate thresholds — they are not symmetric.**

| Level | US (`^TNX`) | UK (Gilt) |
|---|---|---|
| 🔴 RED | velocity ≥ 30 bps/3-day, or level ≥ 4.75% | velocity ≥ 30 bps/3-day, or level ≥ 5.0% |
| 🟡 YELLOW | velocity ≥ 15 bps/3-day, or level ≥ 4.25% | velocity ≥ 15 bps/3-day, or level ≥ 4.5% |
| 🟢 GREEN | below both YELLOW conditions | below both YELLOW conditions |

### 🔴 RED THREAT (Catastrophic Liquidation)

- **Meaning:** Bond markets are experiencing extreme liquidation pressure, or yields have breached structural containment levels. Expect immediate valuation compression.

### 🟡 YELLOW THREAT (Multiple Compression Warning)

- **Meaning:** Rates are rising rapidly, indicating capital is exiting high-growth equities to lock in risk-free sovereign debt.

### 🟢 GREEN THREAT (Stable Environment)

- **Meaning:** Sovereign bond markets are calm, creating a supportive background for equities.

These thresholds are calibrated to the post-2022 rate environment and are intentionally hardcoded in `regime_engine.py` rather than user-configurable.

## 5\. Downstream Code Workflows: How Your App Protects You

### A. Intraday Yield Shock Warning (`intraday_orchestrator.py`)

See Section 2B. This is the only real-time (5-minute) reaction to sovereign yields — a `^TYX` intraday spike of ≥1.5% versus the session open fires a Nextcloud alert (via `notification_engine.notify()`) warning that "the cost of capital is experiencing a violent intraday shock."

### B. The Stock Detail Warning Badge (`templates/stock_detail.html`)

When you open an individual stock's page, `page_routes.py` queries the database for the stock's `yield_correlation`, `trailing_pe`, and `debt_to_equity`. If the stock is both leveraged/expensive and has a strong negative yield correlation, it displays a warning badge:

```jinja
{% if stock.yield_correlation is not none and stock.yield_correlation <= -0.3 %}
    ...
    <span class="yield-sensitive-tag">⚠️ Yield Sensitive (Macro Trap)</span>
    ...
{% endif %}
```

This warning keeps you informed of underlying macro vulnerabilities whenever you review an asset. Note this badge is driven by Section 2C's 30Y-benchmarked correlation, not by Section A's 10Y-benchmarked daily threat level — a stock can show "Yield Sensitive" independent of what today's `us_threat_level`/`uk_threat_level` reads.

### C. US/UK Threat Summary Cards (Portfolio page)

`templates/partials/risk_summary.html` renders two independent floating widget cards — "🇺🇸 US 10Y Treasury" and "🇬🇧 UK 10Y Gilt" — each colored per its own `us_threat_level`/`uk_threat_level` and showing its own 3-day velocity in bps, sourced directly from `macro_regimes`. See AGENTS.md rule 18 for the shared floating-widgets card convention this partial follows.
