# SMGB.L Morning Price Predictor

The SMGB.L Morning Price Predictor estimates what the VanEck Semiconductor UCITS ETF (SMGB.L, London Stock Exchange, priced in GBP) will open at on the next UK trading morning. It uses the ETF's actual top-10 semiconductor holdings, post-UK-close US intraday prices as the primary signal, and a GBP/USD FX adjustment.

Page route: `GET /uk-etf-forecast`

---

## 1. ETF Holdings & Weights

SMGB.L is a **semiconductor ETF**, not an AI or broad-tech ETF. Its top 10 holdings are all semiconductor companies. The weights below are sourced from the VanEck SMGB.L factsheet (top 10 = 79.52% of the fund). They are normalised to sum to 100% for the tracked basket, since the remaining ~20% of holdings are not individually tracked.

| Ticker | Company | Raw ETF weight | Normalised weight |
|--------|---------|---------------|-------------------|
| MU | Micron Technology | 11.67% | 14.68% |
| AMD | Advanced Micro Devices | 11.10% | 13.96% |
| INTC | Intel | 8.77% | 11.03% |
| AVGO | Broadcom | 8.67% | 10.90% |
| NVDA | NVIDIA | 8.55% | 10.75% |
| TSM | Taiwan Semiconductor (ADR) | 7.98% | 10.04% |
| ASML | ASML Holding | 7.58% | 9.53% |
| LRCX | Lam Research | 5.48% | 6.89% |
| AMAT | Applied Materials | 5.25% | 6.60% |
| TXN | Texas Instruments | 4.47% | 5.62% |

**Note on TSM:** The ETF holds TSFA:DUS (TSMC listed on Düsseldorf). The predictor uses the US-listed ADR `TSM` because it has pre/post-market data availability via Yahoo Finance.

If yfinance can successfully fetch live holdings data (`yahoo_engine.get_fund_holdings`), those live weights are used instead. The hardcoded weights serve as the fallback and are stored in `smgb_predictor._KNOWN_HOLDINGS`.

---

## 2. Signal Priority

The prediction engine tries three signal sources in order, falling back when data is unavailable:

| Priority | Source | `signal_source` field | Description |
|----------|--------|----------------------|-------------|
| 1 | Post-UK-close US intraday | `intraday_post_close` | US prices from after 16:30 BST — the most actionable window (US markets continue trading until 21:00 BST) |
| 2 | US pre-market | `intraday_premarket` | US pre-market prices (04:00–09:30 ET) for morning runs before LSE opens |
| 3 | Prior daily closes | `daily_close` | Fallback when no intraday data is available (weekends after US close, data outages) |

The `signal_source` field is included in the prediction dict and displayed as a badge on the page.

### Critical distinction: post-close return vs daily return

The intraday return is computed as:

```
return = (current_US_price / US_price_at_16:30_BST) − 1
```

**Not** `current / prior_daily_close − 1`. Using the prior daily close would produce a 2-day cumulative return (Thursday → Friday post-market), overstating the expected gap. Using the price at UK-close time captures only the move that happened *after* SMGB.L stopped trading — which is the actual gap signal.

Implementation: `smgb_predictor._compute_intraday_returns()`

---

## 3. Prediction Models

Two models run in parallel; the holdings model is primary when available.

### Holdings model (`compute_holdings_prediction`)

1. For each holding, compute `us_return` (intraday if available, else daily close return)
2. Weighted sum: `weighted_equity_return = Σ weight_i × us_return_i`
3. FX adjustment: `fx_adjustment = −(GBPUSD_now / GBPUSD_prev − 1)`
   - Rising GBPUSD = USD weakens = negative adjustment (GBX-priced ETF holding USD assets worth less in GBP)
4. `total_return = weighted_equity_return + fx_adjustment`
5. `predicted_price = last_smgb_close × (1 + total_return)`

Requires at least 3 holdings with data. Falls back to known weights when live holdings fetch fails.

### Regression model (`compute_regression_prediction`)

60-day OLS: `smgb_next_morning_return = α + β × avg_US_basket_return`

- Dependent variable: SMGB.L next-morning open return `(Open_t / Close_{t-1}) − 1`
- Independent variable: equal-weighted average daily return of the 10 semiconductor tickers
- Requires ≥20 observations
- Outputs: predicted price, 95% confidence interval `(±1.96 × residual_std)`, α, β, R²

The regression model always uses **daily closes** regardless of signal source, because it models the historical overnight-gap relationship, not the current intraday move. Its output provides the confidence interval shown on the prediction chart.

---

## 4. Weekend & Holiday Handling

- `_last_trading_date()` returns today on weekdays, Friday on Saturday, and Friday on Sunday
- Intraday data is fetched with `period="5d"` (not `"1d"`) so the last trading day's bars are always available
- `get_smgb_next_open_date()` returns the next trading day: Monday if today is Friday or Saturday, else tomorrow
- The prediction marker on the overlay chart is positioned at `next_open_date 08:00 LSE-local`

---

## 5. Charts

### Time-aligned intraday overlay (`create_smgb_overlay_chart`)

The main new chart. Shows a single wall-clock timeline so the market overlap window is clearly visible.

- **Primary Y-axis (GBP £):** SMGB.L intraday bars for the last trading day (LSE open 08:00 → close 16:30 BST)
- **Secondary Y-axis (scaled):** All 10 US semiconductor tickers from NYSE open (≈14:30 BST) onward, including post-close and after-hours. Each US ticker is scaled so its value equals SMGB.L's last close at the anchor point — this anchors them visually to the same starting level so relative moves are comparable
- **Vertical dashed line:** LSE close at 16:30 BST (drawn via `add_shape` + `add_annotation`, not `add_vline`, to avoid a Plotly annotation-mean bug with string datetime values)
- **Purple star:** Predicted SMGB.L open at 08:00 BST on `next_open_date`

X-axis timestamps are converted from naive UTC to the user's display timezone via `time_engine.get_user_tz()` before plotting.

### Historical prediction chart (`create_smgb_prediction_chart`)

25-day daily close history + predicted open star + 95% CI band (when regression model is available).

### Holdings contributions chart (`create_smgb_contributions_chart`)

Horizontal bar chart showing each holding's weighted contribution to the predicted move. Green = positive, red = negative. Only shown when the holdings model ran successfully.

### Normalised performance & correlation chart (`create_smgb_correlation_chart`)

- Top panel: all 10 tickers + SMGB.L + GBPUSD normalised to 100 at window start
- Bottom panel: rolling 30-day Pearson correlation between SMGB.L and the **equal-weighted** average of the 10 tickers (equal-weight used here only; the prediction model uses known ETF weights)

---

## 6. Portfolio Impact Tile

If SMGB.L is found in `data/portfolio.json`, the page shows a tile with:

- Predicted P&L at next open (predicted value − current value, in GBP)
- Total unrealised P&L at predicted open (predicted value − cost basis)
- Share count and average buy price from the portfolio file

Computed in `page_routes._smgb_portfolio_position()` and `uk_etf_forecast_page()`.

---

## 7. Key Files

| File | Role |
|------|------|
| `smgb_predictor.py` | All data fetching, return computation, prediction engines |
| `visuals.py` | `create_smgb_overlay_chart`, `create_smgb_prediction_chart`, `create_smgb_contributions_chart`, `create_smgb_correlation_chart` |
| `page_routes.py` | `GET /uk-etf-forecast` route, portfolio P&L tile |
| `templates/uk_impact.html` | Page template |
| `static/css/styles.css` | `.xray-metric-card--cyan`, `.xray-metric-card--red` added for portfolio tile |

---

## 8. Data Flow

```
GET /uk-etf-forecast
    │
    ├── run_smgb_prediction()
    │     ├── fetch_daily_closes()          → 65d daily OHLCV for all tickers
    │     ├── fetch_intraday_data(5d)       → 5-min bars with prepost=True
    │     ├── _compute_intraday_returns()   → post-close return vs UK-close price
    │     ├── fetch_smgb_holdings()         → live ETF weights (fallback: _KNOWN_HOLDINGS)
    │     ├── compute_holdings_prediction() → weighted return + FX adjustment
    │     └── compute_regression_prediction() → OLS α+β with 95% CI
    │
    ├── get_correlation_data(60d)           → normalised perf + rolling corr
    │
    ├── get_intraday_overlay_data()
    │     ├── _last_trading_date()          → handles weekends
    │     ├── fetch_intraday_data(5d)       → SMGB.L + 10 US semis
    │     └── run_smgb_prediction()         → prediction for chart marker
    │
    ├── _smgb_portfolio_position()          → SMGB.L from portfolio.json
    │
    └── Render uk_impact.html with 4 charts + portfolio tile
```
