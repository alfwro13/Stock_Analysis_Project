import plotly.graph_objects as go
import pandas as pd


_AI_COLORS = {
    "NVDA":  "#f44336",
    "AMD":   "#f44336",
    "AVGO":  "#f44336",
    "GOOGL": "#4da6ff",
    "MSFT":  "#4da6ff",
    "META":  "#4da6ff",
    "AAPL":  "#4da6ff",
    "ORCL":  "#00ffcc",
    "AMZN":  "#00ffcc",
    "TSLA":  "#ff9800",
}


def create_ai_contagion_performance_chart(ticker_dfs: dict, period_label: str = "30-Day") -> str:
    if not ticker_dfs:
        fig = go.Figure()
        fig.update_layout(template="plotly_dark", height=480, title=dict(text="AI Sector Performance — No Data", x=0.5))
        return fig.to_html(full_html=False, include_plotlyjs=False, config={'responsive': True, 'displaylogo': False})

    fig = go.Figure()
    for ticker, df in ticker_dfs.items():
        if df is None or df.empty or "Close" not in df.columns:
            continue
        series = df["Close"].dropna()
        if series.empty or float(series.iloc[0]) == 0:
            continue
        normalized = series / float(series.iloc[0]) * 100
        color = _AI_COLORS.get(ticker, "#888888")
        fig.add_trace(go.Scatter(
            x=normalized.index,
            y=normalized.values,
            name=ticker,
            line=dict(color=color, width=1.8),
            hovertemplate=f"{ticker}: %{{y:.1f}}<extra></extra>",
        ))

    fig.add_hline(y=100, line_dash="dot", line_color="#555555", line_width=1)
    fig.update_layout(
        title=dict(text=f"AI Ecosystem — {period_label} Normalised Performance (Base = 100)", x=0.5, xanchor="center"),
        template="plotly_dark",
        height=480,
        margin=dict(l=20, r=20, t=70, b=20),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=1.10, xanchor="right", x=1, font=dict(size=10)),
    )
    fig.update_yaxes(title_text="Indexed (100 = start)", showgrid=True, gridcolor="#333333")
    return fig.to_html(full_html=False, include_plotlyjs=False, config={'responsive': True, 'displaylogo': False})


def create_ai_contagion_correlation_heatmap(ticker_dfs: dict, window: int = 20) -> str:
    returns = {}
    for ticker, df in ticker_dfs.items():
        if df is None or df.empty or "Close" not in df.columns:
            continue
        r = df["Close"].dropna().pct_change().dropna()
        if not r.empty:
            returns[ticker] = r

    if len(returns) < 2:
        fig = go.Figure()
        fig.update_layout(template="plotly_dark", height=500, title=dict(text="AI Correlation — Insufficient Data", x=0.5))
        return fig.to_html(full_html=False, include_plotlyjs=False, config={'responsive': True, 'displaylogo': False})

    ret_df = pd.DataFrame(returns).dropna()
    corr = ret_df.tail(window).corr()
    tickers = corr.columns.tolist()
    z = corr.values
    text = [[f"{v:.2f}" for v in row] for row in z]

    fig = go.Figure(go.Heatmap(
        z=z,
        x=tickers,
        y=tickers,
        text=text,
        texttemplate="%{text}",
        colorscale="RdBu_r",
        zmin=-1,
        zmax=1,
        hoverongaps=False,
        hovertemplate="%{y} / %{x}: %{z:.3f}<extra></extra>",
    ))
    fig.update_layout(
        title=dict(text=f"AI Sector Pairwise Correlation (trailing {window} days)", x=0.5, xanchor="center"),
        template="plotly_dark",
        height=500,
        margin=dict(l=20, r=20, t=70, b=20),
    )
    return fig.to_html(full_html=False, include_plotlyjs=False, config={'responsive': True, 'displaylogo': False})
