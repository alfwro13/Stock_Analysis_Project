# AI Sector Contagion Monitor

The AI Sector Contagion Monitor tracks the broader AI ecosystem — not just semiconductors, but hyperscalers, cloud platforms, and AI-adjacent companies. It identifies whether a sharp move in one major AI name is spreading ("contagion") or is isolated.

Page route: `GET /ai-contagion`

---

## 1. Tickers Monitored

| Ticker | Company | Category |
|--------|---------|----------|
| NVDA | NVIDIA | Semiconductor (red) |
| AMD | Advanced Micro Devices | Semiconductor (red) |
| AVGO | Broadcom | Semiconductor (red) |
| GOOGL | Alphabet | Hyperscaler (blue) |
| MSFT | Microsoft | Hyperscaler (blue) |
| META | Meta Platforms | Hyperscaler (blue) |
| AAPL | Apple | Hyperscaler (blue) |
| ORCL | Oracle | Cloud (teal) |
| AMZN | Amazon | Cloud (teal) |
| TSLA | Tesla | AI-adjacent (orange) |

The basket deliberately spans beyond pure semiconductors. It adds hyperscalers and cloud companies that are major AI investors or infrastructure providers alongside the semi names, to capture the full contagion surface.

---

## 2. Charts

### 30-Day Normalised Performance (`create_ai_contagion_performance_chart`)

All tickers normalised to 100 at the start of the 30-day window. A value of 95 means the stock is down 5% over the period.

- Colour coding: red = semis, blue = hyperscalers, teal = cloud, orange = TSLA
- Base-100 reference line drawn at `y=100`
- `hovermode="x unified"` for synchronised cross-hair

### Intraday Normalised Performance (`create_ai_contagion_performance_chart` with intraday data)

Today's 5-minute bars normalised to 100 at NYSE open (09:30 ET). Only rendered when intraday data is available (hidden on weekends/market-closed). Uses `prepost=False` — regular session only, since pre/post-market data is sparse for this use case.

### Pairwise Correlation Heatmap (`create_ai_contagion_correlation_heatmap`)

Pearson correlation matrix computed over the trailing 20 trading days of daily returns (`df.pct_change()`). Uses `go.Heatmap` with `colorscale="RdBu_r"` — deep blue = strongly correlated (stocks move together), deep red = strongly inverse, white = uncorrelated.

High correlation across the full grid means the sector is moving as one: a drawdown in any single name is likely to spread. Low or heterogeneous correlation means moves are company-specific.

---

## 3. Data Fetching

`ai_contagion_engine.get_ai_contagion_data(days=30)` handles all data fetching:

- Daily: `yahoo_engine.get_price_history(_AI_PAGE_TICKERS, period="35d", interval="1d")` truncated to `days`
- Intraday: `yahoo_engine.get_intraday(_AI_PAGE_TICKERS, period="1d", interval="5m", prepost=False)`

Both calls use the existing `yahoo_engine` cache layer. Intraday failures are caught and the intraday chart section is silently omitted from the page.

---

## 4. Key Files

| File | Role |
|------|------|
| `ai_contagion_engine.py` | `get_ai_contagion_data()`, `_AI_PAGE_TICKERS` constant, flash-crash detector |
| `visuals.py` | `create_ai_contagion_performance_chart()`, `create_ai_contagion_correlation_heatmap()`, `_AI_COLORS` |
| `page_routes.py` | `GET /ai-contagion` route |
| `templates/ai_contagion.html` | Page template |
| `templates/tools.html` | Tool card entry |
