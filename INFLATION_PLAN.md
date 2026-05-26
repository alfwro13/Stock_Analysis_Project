Below is the full Claude Code-ready execution plan. Save it as `INFLATION_PLAN.md` (or paste directly into Claude Code) and tell the agent to work through it one step at a time, waiting for confirmation before moving on.

---

# Task: Inflation Tracker (US + UK) + Market Sentiment Page UI Polish

## Context for the Agent

You are editing a FastAPI + raw `sqlite3` + Plotly + Jinja2 quant terminal. Strict rules apply:

- **NO PLACEHOLDERS.** When replacing code, provide the full block. Never use `# ... rest of code ...`.
- **Type hints** on all new Python functions (`List`, `Dict`, `Optional`, `pd.DataFrame`, etc.).
- **Logging** via `logger.info` / `logger.error`, not print.
- **Raw SQL** only — no ORM.
- **`try/except/finally`** around external/DB calls.
- **CSS lives in `static/css/styles.css`** — never inline.
- After each step, **STOP and wait for confirmation** before proceeding to the next. Do not chain steps.

## Execution Order

1. Database schema migration (`database.py`)
2. FRED ingestion + create-table sync (`macro_data_engine.py`)
3. Add two new chart functions + fix three legend bugs (`visuals.py`)
4. Wire data + charts into the route (`page_routes.py`)
5. Add two new chart modules to the template (`templates/market_sentiment.html`)
6. Trigger a manual data refresh + verify in browser

---

## STEP 1 — Schema Migration (`database.py`)

### Goal
Add `us_cpi_inflation` column to the auto-migration registry. On next app startup the `ALTER TABLE` runs automatically.

### File: `database.py`

### Find this exact block:

```python
    required_indicator_columns = {
        'us_m2': 'REAL', 'us_jobless_claims': 'REAL', 'us_high_yield_spread': 'REAL',
        'us_yield_curve': 'REAL', 'uk_m4': 'REAL', 'uk_corporate_spread': 'REAL',
        'uk_cpi_inflation': 'REAL', 'uk_claimant_count': 'REAL'
    }
```

### Replace with:

```python
    required_indicator_columns = {
        'us_m2': 'REAL', 'us_jobless_claims': 'REAL', 'us_high_yield_spread': 'REAL',
        'us_yield_curve': 'REAL', 'uk_m4': 'REAL', 'uk_corporate_spread': 'REAL',
        'uk_cpi_inflation': 'REAL', 'uk_claimant_count': 'REAL',
        'us_cpi_inflation': 'REAL'
    }
```

### STOP and confirm before continuing.

---

## STEP 2 — FRED Ingestion (`macro_data_engine.py`)

### Goal
Fetch `CPIAUCSL` from FRED, store it in `us_cpi_inflation`. Three small edits in this file.

### File: `macro_data_engine.py`

### Edit 2a — Update `setup_database()` CREATE TABLE block (for fresh installs)

Find this exact block:

```python
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS macro_indicators (
            date TEXT PRIMARY KEY,
            us_m2 REAL,
            us_jobless_claims REAL,
            us_high_yield_spread REAL,
            us_yield_curve REAL,
            uk_m4 REAL,
            uk_corporate_spread REAL,
            uk_cpi_inflation REAL,
            uk_claimant_count REAL
        )
    ''')
```

Replace with:

```python
    cursor.execute('''
        CREATE TABLE IF NOT EXISTS macro_indicators (
            date TEXT PRIMARY KEY,
            us_m2 REAL,
            us_jobless_claims REAL,
            us_high_yield_spread REAL,
            us_yield_curve REAL,
            uk_m4 REAL,
            uk_corporate_spread REAL,
            uk_cpi_inflation REAL,
            uk_claimant_count REAL,
            us_cpi_inflation REAL
        )
    ''')
```

### Edit 2b — Add `CPIAUCSL` to the FRED ticker list

Find:

```python
        fred_tickers = ['WM2NS', 'ICSA', 'BAMLH0A0HYM2', 'BAMLHE00EHY2EY', 'T10Y2Y']
```

Replace with:

```python
        fred_tickers = ['WM2NS', 'ICSA', 'BAMLH0A0HYM2', 'BAMLHE00EHY2EY', 'T10Y2Y', 'CPIAUCSL']
```

### Edit 2c — Update the bulk INSERT to include the new column

Find this exact block:

```python
    records = []
    for dt, row in merged_df.iterrows():
        records.append((
            dt.strftime("%Y-%m-%d"),
            float(row['WM2NS']) if 'WM2NS' in row and pd.notna(row['WM2NS']) else None,
            float(row['ICSA']) if 'ICSA' in row and pd.notna(row['ICSA']) else None,
            float(row['BAMLH0A0HYM2']) if 'BAMLH0A0HYM2' in row and pd.notna(row['BAMLH0A0HYM2']) else None,
            float(row['T10Y2Y']) if 'T10Y2Y' in row and pd.notna(row['T10Y2Y']) else None,
            float(row['LPMVWNM']) if 'LPMVWNM' in row and pd.notna(row['LPMVWNM']) else None,
            float(row['BAMLHE00EHY2EY']) if 'BAMLHE00EHY2EY' in row and pd.notna(row['BAMLHE00EHY2EY']) else None,
            float(row['D7G7']) if 'D7G7' in row and pd.notna(row['D7G7']) else None,
            float(row['BCJD']) if 'BCJD' in row and pd.notna(row['BCJD']) else None
        ))
```

Replace with:

```python
    records = []
    for dt, row in merged_df.iterrows():
        records.append((
            dt.strftime("%Y-%m-%d"),
            float(row['WM2NS']) if 'WM2NS' in row and pd.notna(row['WM2NS']) else None,
            float(row['ICSA']) if 'ICSA' in row and pd.notna(row['ICSA']) else None,
            float(row['BAMLH0A0HYM2']) if 'BAMLH0A0HYM2' in row and pd.notna(row['BAMLH0A0HYM2']) else None,
            float(row['T10Y2Y']) if 'T10Y2Y' in row and pd.notna(row['T10Y2Y']) else None,
            float(row['LPMVWNM']) if 'LPMVWNM' in row and pd.notna(row['LPMVWNM']) else None,
            float(row['BAMLHE00EHY2EY']) if 'BAMLHE00EHY2EY' in row and pd.notna(row['BAMLHE00EHY2EY']) else None,
            float(row['D7G7']) if 'D7G7' in row and pd.notna(row['D7G7']) else None,
            float(row['BCJD']) if 'BCJD' in row and pd.notna(row['BCJD']) else None,
            float(row['CPIAUCSL']) if 'CPIAUCSL' in row and pd.notna(row['CPIAUCSL']) else None
        ))
```

### Edit 2d — Update the `executemany` SQL statement

Find this exact block:

```python
        cursor.executemany('''
            INSERT OR IGNORE INTO macro_indicators (
                date, us_m2, us_jobless_claims, us_high_yield_spread, us_yield_curve,
                uk_m4, uk_corporate_spread, uk_cpi_inflation, uk_claimant_count
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', records)
```

Replace with:

```python
        cursor.executemany('''
            INSERT OR IGNORE INTO macro_indicators (
                date, us_m2, us_jobless_claims, us_high_yield_spread, us_yield_curve,
                uk_m4, uk_corporate_spread, uk_cpi_inflation, uk_claimant_count,
                us_cpi_inflation
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', records)
```

### STOP and confirm before continuing.

---

## STEP 3 — Charts + Legend Fixes (`visuals.py`)

### Goal
Two new chart functions for inflation. Three legend repositions on existing charts.

### File: `visuals.py`

### Edit 3a — Fix legend on `create_us_liquidity_chart`

Find:

```python
        title=dict(text="US Liquidity: S&P 500 vs M2 Money Supply", x=0.5, xanchor='center'),
        template="plotly_dark", height=350,
        margin=dict(l=20, r=20, t=50, b=20), hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5)
```

Replace with:

```python
        title=dict(text="US Liquidity: S&P 500 vs M2 Money Supply", x=0.5, xanchor='center'),
        template="plotly_dark", height=350,
        margin=dict(l=20, r=20, t=50, b=20), hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
```

### Edit 3b — Fix legend on `create_uk_liquidity_chart`

Find:

```python
        title=dict(text="UK Liquidity: FTSE 100 vs M4 Money Supply", x=0.5, xanchor='center'),
        template="plotly_dark", height=350,
        margin=dict(l=20, r=20, t=50, b=20), hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5)
```

Replace with:

```python
        title=dict(text="UK Liquidity: FTSE 100 vs M4 Money Supply", x=0.5, xanchor='center'),
        template="plotly_dark", height=350,
        margin=dict(l=20, r=20, t=50, b=20), hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
```

### Edit 3c — Add legend config to `create_yield_curve_chart`

Find:

```python
    fig.update_layout(
        title=dict(text="Yield Curve Radar: US 10-Year vs 2-Year Treasury Spread", x=0.5, xanchor='center'),
        template="plotly_dark",
        height=350,
        margin=dict(l=20, r=20, t=50, b=20),
        hovermode="x unified"
    )
```

Replace with:

```python
    fig.update_layout(
        title=dict(text="Yield Curve Radar: US 10-Year vs 2-Year Treasury Spread", x=0.5, xanchor='center'),
        template="plotly_dark",
        height=350,
        margin=dict(l=20, r=20, t=50, b=20),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
```

### Edit 3d — Add two new chart functions

Find this exact block (the existing US liquidity chart function signature):

```python
def create_us_liquidity_chart(df_spy: pd.DataFrame, df_m2: pd.DataFrame) -> str:
```

Insert the following TWO complete functions IMMEDIATELY BEFORE that line (so they sit just above `create_us_liquidity_chart`):

```python
def create_us_inflation_chart(df_spy: pd.DataFrame, df_cpi: pd.DataFrame) -> str:
    """
    Renders US Price Stability chart: CPI YoY % (orange, secondary axis) vs S&P 500 (cyan, primary axis).
    Computes YoY inflation from the raw CPIAUCSL level index using a 12-period (monthly) percent change.
    Reference lines plotted at 2% (Fed target) and 5% (danger zone).
    """
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    if not df_spy.empty and 'Close' in df_spy.columns:
        fig.add_trace(
            go.Scatter(
                x=df_spy.index, y=df_spy['Close'], name="S&P 500",
                line=dict(color="#00ffcc", width=2), connectgaps=True
            ),
            secondary_y=False
        )

    if not df_cpi.empty and 'value' in df_cpi.columns:
        df_cpi_local = df_cpi.copy()
        df_cpi_local['yoy'] = df_cpi_local['value'].pct_change(periods=12) * 100.0
        df_cpi_local = df_cpi_local.dropna(subset=['yoy'])

        if not df_cpi_local.empty:
            fig.add_trace(
                go.Scatter(
                    x=df_cpi_local.index, y=df_cpi_local['yoy'], name="US CPI YoY %",
                    line=dict(color="#ff8800", width=2, dash='dot'), connectgaps=True
                ),
                secondary_y=True
            )

    fig.add_hline(
        y=2.0, secondary_y=True, line_dash="dash", line_color="#00ff00",
        annotation_text="Target (2.0%)", annotation_position="bottom right",
        annotation_font_color="#00ff00"
    )
    fig.add_hline(
        y=5.0, secondary_y=True, line_dash="dash", line_color="#ff4d4d",
        annotation_text="Danger Zone (>5.0%)", annotation_position="top right",
        annotation_font_color="#ff4d4d"
    )

    fig.update_layout(
        title=dict(text="US Price Stability: CPI YoY vs S&P 500", x=0.5, xanchor='center'),
        template="plotly_dark", height=350,
        margin=dict(l=20, r=20, t=50, b=20), hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    fig.update_yaxes(title_text="S&P 500 Index", secondary_y=False, showgrid=False)
    fig.update_yaxes(title_text="CPI YoY (%)", secondary_y=True, showgrid=False)
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})


def create_uk_inflation_chart(df_ftse: pd.DataFrame, df_cpi: pd.DataFrame) -> str:
    """
    Renders UK Price Stability chart: CPI YoY % (orange, secondary axis) vs FTSE 100 (cyan, primary axis).
    Computes YoY inflation from the raw ONS D7G7 level index using a 12-period (monthly) percent change.
    Reference lines plotted at 2% (BoE target) and 5% (danger zone).
    """
    fig = make_subplots(specs=[[{"secondary_y": True}]])

    if not df_ftse.empty and 'Close' in df_ftse.columns:
        fig.add_trace(
            go.Scatter(
                x=df_ftse.index, y=df_ftse['Close'], name="FTSE 100",
                line=dict(color="#00ffff", width=2), connectgaps=True
            ),
            secondary_y=False
        )

    if not df_cpi.empty and 'value' in df_cpi.columns:
        df_cpi_local = df_cpi.copy()
        df_cpi_local['yoy'] = df_cpi_local['value'].pct_change(periods=12) * 100.0
        df_cpi_local = df_cpi_local.dropna(subset=['yoy'])

        if not df_cpi_local.empty:
            fig.add_trace(
                go.Scatter(
                    x=df_cpi_local.index, y=df_cpi_local['yoy'], name="UK CPI YoY %",
                    line=dict(color="#ff8800", width=2, dash='dot'), connectgaps=True
                ),
                secondary_y=True
            )

    fig.add_hline(
        y=2.0, secondary_y=True, line_dash="dash", line_color="#00ff00",
        annotation_text="Target (2.0%)", annotation_position="bottom right",
        annotation_font_color="#00ff00"
    )
    fig.add_hline(
        y=5.0, secondary_y=True, line_dash="dash", line_color="#ff4d4d",
        annotation_text="Danger Zone (>5.0%)", annotation_position="top right",
        annotation_font_color="#ff4d4d"
    )

    fig.update_layout(
        title=dict(text="UK Price Stability: CPI YoY vs FTSE 100", x=0.5, xanchor='center'),
        template="plotly_dark", height=350,
        margin=dict(l=20, r=20, t=50, b=20), hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    fig.update_yaxes(title_text="FTSE 100 Index", secondary_y=False, showgrid=False)
    fig.update_yaxes(title_text="CPI YoY (%)", secondary_y=True, showgrid=False)
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})


```

### STOP and confirm before continuing.

---

## STEP 4 — Route Handler (`page_routes.py`)

### Goal
Slice CPI columns from `macro_indicators`, render the two new charts, pass them into the template context.

### File: `page_routes.py`

### Edit 4a — Add CPI slicing in the existing macro_indicators block

Find this exact block:

```python
                df_m2 = df_indicators[['us_m2']].rename(columns={'us_m2': 'value'}).dropna().sort_index() if 'us_m2' in df_indicators.columns else pd.DataFrame()
                df_us_hy = df_indicators[['us_high_yield_spread']].rename(columns={'us_high_yield_spread': 'value'}).dropna().sort_index() if 'us_high_yield_spread' in df_indicators.columns else pd.DataFrame()
                df_m4 = df_indicators[['uk_m4']].rename(columns={'uk_m4': 'value'}).dropna().sort_index() if 'uk_m4' in df_indicators.columns else pd.DataFrame()
                df_uk_ig = df_indicators[['uk_corporate_spread']].rename(columns={'uk_corporate_spread': 'value'}).dropna().sort_index() if 'uk_corporate_spread' in df_indicators.columns else pd.DataFrame()
                df_yield_curve = df_indicators[['us_yield_curve']].rename(columns={'us_yield_curve': 'value'}).dropna().sort_index() if 'us_yield_curve' in df_indicators.columns else pd.DataFrame()
            else:
                df_m2, df_us_hy, df_m4, df_uk_ig, df_yield_curve = pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
        except Exception as e:
            print(f"[DEBUG] Error processing macro indicators matrix: {e}")
            df_m2, df_us_hy, df_m4, df_uk_ig, df_yield_curve = pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
```

Replace with:

```python
                df_m2 = df_indicators[['us_m2']].rename(columns={'us_m2': 'value'}).dropna().sort_index() if 'us_m2' in df_indicators.columns else pd.DataFrame()
                df_us_hy = df_indicators[['us_high_yield_spread']].rename(columns={'us_high_yield_spread': 'value'}).dropna().sort_index() if 'us_high_yield_spread' in df_indicators.columns else pd.DataFrame()
                df_m4 = df_indicators[['uk_m4']].rename(columns={'uk_m4': 'value'}).dropna().sort_index() if 'uk_m4' in df_indicators.columns else pd.DataFrame()
                df_uk_ig = df_indicators[['uk_corporate_spread']].rename(columns={'uk_corporate_spread': 'value'}).dropna().sort_index() if 'uk_corporate_spread' in df_indicators.columns else pd.DataFrame()
                df_yield_curve = df_indicators[['us_yield_curve']].rename(columns={'us_yield_curve': 'value'}).dropna().sort_index() if 'us_yield_curve' in df_indicators.columns else pd.DataFrame()
                df_us_cpi = df_indicators[['us_cpi_inflation']].rename(columns={'us_cpi_inflation': 'value'}).dropna().sort_index() if 'us_cpi_inflation' in df_indicators.columns else pd.DataFrame()
                df_uk_cpi = df_indicators[['uk_cpi_inflation']].rename(columns={'uk_cpi_inflation': 'value'}).dropna().sort_index() if 'uk_cpi_inflation' in df_indicators.columns else pd.DataFrame()
            else:
                df_m2, df_us_hy, df_m4, df_uk_ig, df_yield_curve = pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
                df_us_cpi, df_uk_cpi = pd.DataFrame(), pd.DataFrame()
        except Exception as e:
            print(f"[DEBUG] Error processing macro indicators matrix: {e}")
            df_m2, df_us_hy, df_m4, df_uk_ig, df_yield_curve = pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame(), pd.DataFrame()
            df_us_cpi, df_uk_cpi = pd.DataFrame(), pd.DataFrame()
```

### Edit 4b — Import the new chart functions

Locate the existing import in `page_routes.py` that brings in `create_us_liquidity_chart`, `create_yield_curve_chart`, etc. from `visuals`. (It will look like `from visuals import ...`.) Add `create_us_inflation_chart` and `create_uk_inflation_chart` to that import list. **Do not duplicate the import line — extend the existing one.**

### Edit 4c — Generate chart HTML inside the try block

Find this exact block:

```python
        # Generate chart HTML
        us_liquidity_html = create_us_liquidity_chart(df_spy, df_m2)
        us_credit_html = create_us_credit_chart(df_us_hy)
        uk_liquidity_html = create_uk_liquidity_chart(df_ftse, df_m4)
        uk_credit_html = create_uk_credit_chart(df_uk_ig)
        yield_curve_html = create_yield_curve_chart(df_yield_curve)
```

Replace with:

```python
        # Generate chart HTML
        us_liquidity_html = create_us_liquidity_chart(df_spy, df_m2)
        us_credit_html = create_us_credit_chart(df_us_hy)
        uk_liquidity_html = create_uk_liquidity_chart(df_ftse, df_m4)
        uk_credit_html = create_uk_credit_chart(df_uk_ig)
        yield_curve_html = create_yield_curve_chart(df_yield_curve)
        us_inflation_html = create_us_inflation_chart(df_spy, df_us_cpi)
        uk_inflation_html = create_uk_inflation_chart(df_ftse, df_uk_cpi)
```

### Edit 4d — Update the except-block fallback

Find this exact block:

```python
    except Exception as e:
        print(f"[DEBUG] Fatal error in market_sentiment route: {e}")
        macro_regime = None
        urgent_events = []
        us_events = []
        uk_events = []
        us_liquidity_html = "<p>Data unavailable.</p>"
        us_credit_html = "<p>Data unavailable.</p>"
        uk_liquidity_html = "<p>Data unavailable.</p>"
        uk_credit_html = "<p>Data unavailable.</p>"
        yield_curve_html = "<p>Data unavailable.</p>"
```

Replace with:

```python
    except Exception as e:
        print(f"[DEBUG] Fatal error in market_sentiment route: {e}")
        macro_regime = None
        urgent_events = []
        us_events = []
        uk_events = []
        us_liquidity_html = "<p>Data unavailable.</p>"
        us_credit_html = "<p>Data unavailable.</p>"
        uk_liquidity_html = "<p>Data unavailable.</p>"
        uk_credit_html = "<p>Data unavailable.</p>"
        yield_curve_html = "<p>Data unavailable.</p>"
        us_inflation_html = "<p>Data unavailable.</p>"
        uk_inflation_html = "<p>Data unavailable.</p>"
```

### Edit 4e — Add new charts to the template context

Find this exact block:

```python
    return templates.TemplateResponse(
        request=request, 
        name="market_sentiment.html", 
        context={
            "sentiment_html": get_sentiment_html(), 
            "vix_spy_html": get_vix_spy_html(),
            "yield_equity_html": get_yield_equity_html(),
            "uk_yield_equity_html": get_uk_yield_equity_html(),
            "ftse_gbp_html": get_ftse_gbp_html(),
            "us_liquidity_html": us_liquidity_html,
            "us_credit_html": us_credit_html,
            "uk_liquidity_html": uk_liquidity_html,
            "uk_credit_html": uk_credit_html,
            "yield_curve_html": yield_curve_html,
            "regime_data": regime_data,
            "macro_regime": macro_regime,
            "urgent_events": urgent_events,
            "us_events": us_events,
            "uk_events": uk_events,
            "unread_count": get_unread_count(),
            "config": load_config()
        }
    )
```

Replace with:

```python
    return templates.TemplateResponse(
        request=request, 
        name="market_sentiment.html", 
        context={
            "sentiment_html": get_sentiment_html(), 
            "vix_spy_html": get_vix_spy_html(),
            "yield_equity_html": get_yield_equity_html(),
            "uk_yield_equity_html": get_uk_yield_equity_html(),
            "ftse_gbp_html": get_ftse_gbp_html(),
            "us_liquidity_html": us_liquidity_html,
            "us_credit_html": us_credit_html,
            "uk_liquidity_html": uk_liquidity_html,
            "uk_credit_html": uk_credit_html,
            "yield_curve_html": yield_curve_html,
            "us_inflation_html": us_inflation_html,
            "uk_inflation_html": uk_inflation_html,
            "regime_data": regime_data,
            "macro_regime": macro_regime,
            "urgent_events": urgent_events,
            "us_events": us_events,
            "uk_events": uk_events,
            "unread_count": get_unread_count(),
            "config": load_config()
        }
    )
```

### STOP and confirm before continuing.

---

## STEP 5 — Template Insertion (`templates/market_sentiment.html`)

### Goal
Insert two new `.sentiment-module` blocks: one in the US column between Liquidity and Credit, one in the UK column between Liquidity and Credit.

### File: `templates/market_sentiment.html`

### Edit 5a — Insert US inflation module

Find this exact block:

```html
            <div class="sentiment-module module-default">
                <div id="us-liquidity-wrapper" class="chart-wrapper">
                    <button class="fullscreen-btn" onclick="toggleFullscreen('us-liquidity-wrapper')">⛶ Fullscreen</button>
                    {{ us_liquidity_html | safe }}
                </div>
                <div class="sentiment-footer">
                    <span class="sentiment-footer-title">US Liquidity (M2) Explained:</span>
                    <div class="sentiment-footer-text">
                        M2 Money Supply represents the raw "fuel" in the economy—literally the amount of cash sitting in bank accounts and circulation. Expanding liquidity makes it incredibly easy for the stock market to rise. If the stock market is rising while this yellow line is falling, the rally is running on empty fumes.
                    </div>
                </div>
            </div>

            <div class="sentiment-module module-default">
                <div id="us-credit-wrapper" class="chart-wrapper">
```

Replace with:

```html
            <div class="sentiment-module module-default">
                <div id="us-liquidity-wrapper" class="chart-wrapper">
                    <button class="fullscreen-btn" onclick="toggleFullscreen('us-liquidity-wrapper')">⛶ Fullscreen</button>
                    {{ us_liquidity_html | safe }}
                </div>
                <div class="sentiment-footer">
                    <span class="sentiment-footer-title">US Liquidity (M2) Explained:</span>
                    <div class="sentiment-footer-text">
                        M2 Money Supply represents the raw "fuel" in the economy—literally the amount of cash sitting in bank accounts and circulation. Expanding liquidity makes it incredibly easy for the stock market to rise. If the stock market is rising while this yellow line is falling, the rally is running on empty fumes.
                    </div>
                </div>
            </div>

            <div class="sentiment-module module-default">
                <div id="us-inflation-wrapper" class="chart-wrapper">
                    <button class="fullscreen-btn" onclick="toggleFullscreen('us-inflation-wrapper')">⛶ Fullscreen</button>
                    {{ us_inflation_html | safe }}
                </div>
                <div class="sentiment-footer">
                    <span class="sentiment-footer-title">US Inflation (CPI YoY) Explained:</span>
                    <div class="sentiment-footer-text">
                        Inflation is the rate at which everything around you is becoming more expensive. The Federal Reserve targets 2% as the healthy "sweet spot". When CPI spikes above 5%, the Fed panics and aggressively raises interest rates to cool the economy — which historically crushes the stock market. A falling CPI line is rocket fuel for risk assets.
                    </div>
                </div>
            </div>

            <div class="sentiment-module module-default">
                <div id="us-credit-wrapper" class="chart-wrapper">
```

### Edit 5b — Insert UK inflation module

Find this exact block:

```html
            <div class="sentiment-module module-cyan">
                <div id="uk-liquidity-wrapper" class="chart-wrapper">
                    <button class="fullscreen-btn" onclick="toggleFullscreen('uk-liquidity-wrapper')">⛶ Fullscreen</button>
                    {{ uk_liquidity_html | safe }}
                </div>
                <div class="sentiment-footer">
                    <span class="sentiment-footer-title">UK Liquidity (M4) Explained:</span>
                    <div class="sentiment-footer-text">
                        M4 represents the total cash flowing through the UK economy. A growing money supply acts as a safety net, supporting business expansion and stock market rallies. A contracting money supply pulls that safety net away, making it very hard for the market to grow.
                    </div>
                </div>
            </div>

            <div class="sentiment-module module-purple">
                <div id="uk-credit-wrapper" class="chart-wrapper">
```

Replace with:

```html
            <div class="sentiment-module module-cyan">
                <div id="uk-liquidity-wrapper" class="chart-wrapper">
                    <button class="fullscreen-btn" onclick="toggleFullscreen('uk-liquidity-wrapper')">⛶ Fullscreen</button>
                    {{ uk_liquidity_html | safe }}
                </div>
                <div class="sentiment-footer">
                    <span class="sentiment-footer-title">UK Liquidity (M4) Explained:</span>
                    <div class="sentiment-footer-text">
                        M4 represents the total cash flowing through the UK economy. A growing money supply acts as a safety net, supporting business expansion and stock market rallies. A contracting money supply pulls that safety net away, making it very hard for the market to grow.
                    </div>
                </div>
            </div>

            <div class="sentiment-module module-default">
                <div id="uk-inflation-wrapper" class="chart-wrapper">
                    <button class="fullscreen-btn" onclick="toggleFullscreen('uk-inflation-wrapper')">⛶ Fullscreen</button>
                    {{ uk_inflation_html | safe }}
                </div>
                <div class="sentiment-footer">
                    <span class="sentiment-footer-title">UK Inflation (CPI YoY) Explained:</span>
                    <div class="sentiment-footer-text">
                        CPI tracks the average rise in UK consumer prices — from groceries to energy bills. The Bank of England's mandate is to keep this at 2%. When inflation breaks above 5%, the BoE is forced to hike rates aggressively, increasing borrowing costs for UK households and businesses, which acts as a major drag on the FTSE 100. Falling inflation gives the BoE room to cut rates and fuel equity rallies.
                    </div>
                </div>
            </div>

            <div class="sentiment-module module-purple">
                <div id="uk-credit-wrapper" class="chart-wrapper">
```

### STOP and confirm before continuing.

---

## STEP 6 — Verification & Data Backfill

### Step 6a — Restart the FastAPI dev server
The auto-migration in `database.py` runs on startup. Confirm in logs:

```
[MIGRATION] Adding Phase 1 Yield Curve column 'us_cpi_inflation' to macro_indicators...
```

If you don't see that log line, the migration didn't fire — investigate before continuing.

### Step 6b — Trigger a manual Macro Data refresh
Hit the existing endpoint that runs `update_macro_indicators` (likely `POST /api/macro/run-pipeline` based on the codebase pattern). Wait for the "Macro Data Update completed successfully" notification.

### Step 6c — Verify DB has US CPI data
Run from a shell or DB browser:

```sql
SELECT date, us_cpi_inflation, uk_cpi_inflation 
FROM macro_indicators 
WHERE us_cpi_inflation IS NOT NULL 
ORDER BY date DESC 
LIMIT 5;
```

Expect: recent dates with `us_cpi_inflation` values around 290–320 (raw CPI level), `uk_cpi_inflation` values around 130–140.

### Step 6d — Load `/market-sentiment` in browser
Expected outcome:
- US column shows new "US Price Stability: CPI YoY vs S&P 500" chart between Liquidity and Credit modules
- UK column shows new "UK Price Stability: CPI YoY vs FTSE 100" chart between Liquidity and Credit modules
- US Liquidity, UK Liquidity, and Yield Curve charts all now have legends in the top-right corner (consistent with other charts)
- Both CPI lines show YoY % (not raw index levels), with green 2% target line and red 5% danger line annotations

---

## Rollback Notes

If anything goes wrong, all changes are additive — no existing columns or functions are removed. To roll back:
1. Revert each file edit (git).
2. The `us_cpi_inflation` column can stay in the DB harmlessly.

---

## Acceptance Criteria

✅ `us_cpi_inflation` column exists in `macro_indicators`
✅ Column is populated with recent CPI values after manual pipeline run
✅ Two new chart functions exist in `visuals.py`
✅ Three legend fixes applied (US Liquidity, UK Liquidity, Yield Curve)
✅ `/market-sentiment` renders both new charts without errors
✅ YoY computation is correct (visual sanity: most recent value should be in the ~2-4% range as of late 2025/early 2026)
✅ No regressions on other charts