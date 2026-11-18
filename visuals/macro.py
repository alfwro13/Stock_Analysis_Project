import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd

SENTIMENT_CHART_HEIGHT = 420


def apply_sentiment_chart_layout(fig: go.Figure, title: str) -> None:
    fig.update_layout(
        title=dict(text=title, x=0.5, xanchor='center'),
        template="plotly_dark", height=SENTIMENT_CHART_HEIGHT,
        margin=dict(l=20, r=20, t=50, b=70), hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.15, xanchor="center", x=0.5)
    )
    fig.update_yaxes(automargin=True)


def create_us_inflation_chart(df_spy: pd.DataFrame, df_cpi: pd.DataFrame) -> str:
    # df_cpi['value'] is us_cpi_inflation from the DB, already stored as YoY% by macro_data_engine.
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
        df_cpi_local = df_cpi.copy().sort_index()
        monthly_series = df_cpi_local['value'].resample('MS').first().dropna()

        if not monthly_series.empty:
            fig.add_trace(
                go.Scatter(
                    x=monthly_series.index, y=monthly_series.values, name="US CPI YoY %",
                    line=dict(color="#ff8800", width=2, dash='dot'), connectgaps=True
                ),
                secondary_y=True
            )

    # Explicitly anchor annotations to the secondary Y-axis (y2) to prevent chart collision
    fig.add_hline(y=2.0, secondary_y=True, line_dash="dash", line_color="#00ff00")
    fig.add_annotation(
        x=0.99, y=2.0, xref="paper", yref="y2",
        text="Target (2.0%)", showarrow=False,
        font=dict(color="#00ff00"), xanchor="right", yanchor="bottom"
    )

    fig.add_hline(y=5.0, secondary_y=True, line_dash="dash", line_color="#ff4d4d")
    fig.add_annotation(
        x=0.99, y=5.0, xref="paper", yref="y2",
        text="Danger Zone (>5.0%)", showarrow=False,
        font=dict(color="#ff4d4d"), xanchor="right", yanchor="bottom"
    )

    apply_sentiment_chart_layout(fig, "US Price Stability: CPI YoY vs S&P 500")
    fig.update_yaxes(title_text="S&P 500 Index", secondary_y=False, showgrid=False)
    fig.update_yaxes(title_text="CPI YoY (%)", secondary_y=True, showgrid=False)
    return fig.to_html(full_html=False, include_plotlyjs=False, config={'responsive': True, 'displaylogo': False})


def create_uk_inflation_chart(df_ftse: pd.DataFrame, df_cpi: pd.DataFrame) -> str:
    # ONS D7G7 is already an annualised YoY % rate — no transformation needed (unlike US CPIAUCSL which is a raw index level).
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
        df_cpi_local = df_cpi.copy().sort_index()
        # D7G7 is already the YoY % rate. Resample to strip daily forward-fill noise.
        monthly_series = df_cpi_local['value'].resample('MS').first().dropna()

        if not monthly_series.empty:
            fig.add_trace(
                go.Scatter(
                    x=monthly_series.index, y=monthly_series.values, name="UK CPI YoY %",
                    line=dict(color="#ff8800", width=2, dash='dot'), connectgaps=True
                ),
                secondary_y=True
            )

    # Explicitly anchor annotations to the secondary Y-axis (y2) to prevent chart collision
    fig.add_hline(y=2.0, secondary_y=True, line_dash="dash", line_color="#00ff00")
    fig.add_annotation(
        x=0.99, y=2.0, xref="paper", yref="y2",
        text="Target (2.0%)", showarrow=False,
        font=dict(color="#00ff00"), xanchor="right", yanchor="bottom"
    )

    fig.add_hline(y=5.0, secondary_y=True, line_dash="dash", line_color="#ff4d4d")
    fig.add_annotation(
        x=0.99, y=5.0, xref="paper", yref="y2",
        text="Danger Zone (>5.0%)", showarrow=False,
        font=dict(color="#ff4d4d"), xanchor="right", yanchor="bottom"
    )

    apply_sentiment_chart_layout(fig, "UK Price Stability: CPI YoY vs FTSE 100")
    fig.update_yaxes(title_text="FTSE 100 Index", secondary_y=False, showgrid=False)
    fig.update_yaxes(title_text="CPI YoY (%)", secondary_y=True, showgrid=False)
    return fig.to_html(full_html=False, include_plotlyjs=False, config={'responsive': True, 'displaylogo': False})


def create_us_liquidity_chart(df_spy: pd.DataFrame, df_m2: pd.DataFrame) -> str:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    if not df_spy.empty and 'Close' in df_spy.columns:
        fig.add_trace(go.Scatter(x=df_spy.index, y=df_spy['Close'], name="S&P 500", line=dict(color="#00ffcc", width=2), connectgaps=True), secondary_y=False)
    if not df_m2.empty and 'value' in df_m2.columns:
        fig.add_trace(go.Scatter(x=df_m2.index, y=df_m2['value'], name="US M2 Supply", line=dict(color="#ffaa00", width=2, dash='dot'), connectgaps=True), secondary_y=True)

    apply_sentiment_chart_layout(fig, "US Liquidity: S&P 500 vs M2 Money Supply")
    fig.update_yaxes(title_text="S&P 500 Index", secondary_y=False, showgrid=False)
    fig.update_yaxes(title_text="M2 Supply (Trillions)", secondary_y=True, showgrid=False)
    return fig.to_html(full_html=False, include_plotlyjs=False, config={'responsive': True, 'displaylogo': False})


def create_us_credit_chart(df_spread: pd.DataFrame) -> str:
    fig = go.Figure()
    if not df_spread.empty and 'value' in df_spread.columns:
        fig.add_trace(go.Scatter(x=df_spread.index, y=df_spread['value'], name="High Yield Spread", line=dict(color="#ff4d4d", width=2), connectgaps=True))

    fig.add_hline(y=5.0, line_dash="dash", line_color="#ff4d4d", annotation_text="Danger Zone (> 5.0%)", annotation_position="top left", annotation_font_color="#ff4d4d")

    apply_sentiment_chart_layout(fig, "US Credit Stress: ICE BofA High Yield Spread")
    fig.update_yaxes(title_text="Spread (%)", showgrid=True, gridcolor="#333333")
    return fig.to_html(full_html=False, include_plotlyjs=False, config={'responsive': True, 'displaylogo': False})


def create_uk_liquidity_chart(df_ftse: pd.DataFrame, df_m4: pd.DataFrame) -> str:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    if not df_ftse.empty and 'Close' in df_ftse.columns:
        fig.add_trace(go.Scatter(x=df_ftse.index, y=df_ftse['Close'], name="FTSE 100", line=dict(color="#00ffff", width=2), connectgaps=True), secondary_y=False)
    if not df_m4.empty and 'value' in df_m4.columns:
        fig.add_trace(go.Scatter(x=df_m4.index, y=df_m4['value'], name="UK M4 Supply", line=dict(color="#bb86fc", width=2, dash='dot'), connectgaps=True), secondary_y=True)

    apply_sentiment_chart_layout(fig, "UK Liquidity: FTSE 100 vs M4 Money Supply")
    fig.update_yaxes(title_text="FTSE 100 Index", secondary_y=False, showgrid=False)
    fig.update_yaxes(title_text="M4 Supply (Billions)", secondary_y=True, showgrid=False)
    return fig.to_html(full_html=False, include_plotlyjs=False, config={'responsive': True, 'displaylogo': False})


def create_uk_credit_chart(df_spread: pd.DataFrame) -> str:
    fig = go.Figure()
    if not df_spread.empty and 'value' in df_spread.columns:
        fig.add_trace(go.Scatter(x=df_spread.index, y=df_spread['value'], name="UK IG Spread", line=dict(color="#ff4d4d", width=2), connectgaps=True))

    fig.add_hline(y=3.0, line_dash="dash", line_color="#ff4d4d", annotation_text="Danger Zone (> 3.0%)", annotation_position="top left", annotation_font_color="#ff4d4d")

    apply_sentiment_chart_layout(fig, "UK Credit Stress: Sterling Corporate Spread")
    fig.update_yaxes(title_text="Spread (%)", showgrid=True, gridcolor="#333333")
    return fig.to_html(full_html=False, include_plotlyjs=False, config={'responsive': True, 'displaylogo': False})


def create_yield_curve_chart(df_curve: pd.DataFrame) -> str:
    fig = go.Figure()

    if not df_curve.empty and 'value' in df_curve.columns:
        fig.add_trace(go.Scatter(
            x=df_curve.index, y=df_curve['value'],
            name="10Y-2Y Spread",
            line=dict(color="#b366ff", width=2),
            connectgaps=True
        ))

        df_inverted = df_curve['value'].clip(upper=0)
        fig.add_trace(go.Scatter(
            x=df_curve.index, y=df_inverted,
            name="Inverted Zone",
            mode='lines',
            line=dict(width=0),
            fill='tozeroy',
            fillcolor='rgba(255, 77, 77, 0.3)',
            showlegend=False,
            hoverinfo='skip',
            connectgaps=True
        ))

    fig.add_hline(y=0.0, line_dash="solid", line_color="#ff4d4d",
                  annotation_text="Inversion Threshold (Recession Warning)",
                  annotation_position="top left",
                  annotation_font_color="#ff4d4d")

    apply_sentiment_chart_layout(fig, "Yield Curve Radar: US 10-Year vs 2-Year Treasury Spread")
    fig.update_yaxes(title_text="Spread (%)", showgrid=True, gridcolor="#333333")

    return fig.to_html(full_html=False, include_plotlyjs=False, config={'responsive': True, 'displaylogo': False})


_YIELD_GDP_LABELS = {
    "US": ("10Y Treasury Yield", "US Nominal GDP YoY"),
    "UK": ("10Y Gilt Yield", "UK Nominal GDP YoY"),
}


def create_yield_vs_gdp_chart(df_yield: pd.DataFrame, df_gdp: pd.DataFrame, region: str) -> str:
    if df_yield.empty or df_gdp.empty:
        return "<p>Data unavailable.</p>"
    yield_name, gdp_name = _YIELD_GDP_LABELS[region]

    gdp = df_gdp['value'].sort_index()
    yld = df_yield['value'].sort_index()
    yld = yld[yld.index >= gdp.index.min()]
    if yld.empty:
        return "<p>Data unavailable.</p>"
    releases = gdp[gdp.ne(gdp.shift())]
    gdp_on_yield_dates = releases.reindex(releases.index.union(yld.index)).interpolate(method='time').reindex(yld.index)
    gap = yld - gdp_on_yield_dates
    upper = pd.concat([yld, gdp_on_yield_dates], axis=1).max(axis=1)

    fig = go.Figure()
    fig.add_trace(go.Scatter(
        x=yld.index, y=yld, name=yield_name, line=dict(color="#4da6ff", width=2), connectgaps=True,
        customdata=gap, hovertemplate="%{y:.2f}% (gap vs GDP %{customdata:+.2f} pp)"
    ))
    fig.add_trace(go.Scatter(
        x=gdp_on_yield_dates.index, y=gdp_on_yield_dates, name=gdp_name,
        line=dict(color="#b366ff", width=2, dash='dot'), connectgaps=True, hovertemplate="%{y:.2f}%"
    ))
    fig.add_trace(go.Scatter(
        x=yld.index, y=yld, mode='lines', line=dict(width=0), showlegend=False, hoverinfo='skip'
    ))
    fig.add_trace(go.Scatter(
        x=upper.index, y=upper, name="Yield below growth", mode='lines', line=dict(width=0),
        fill='tonexty', fillcolor='rgba(0, 200, 120, 0.25)', hoverinfo='skip'
    ))
    fig.add_trace(go.Scatter(
        x=gdp_on_yield_dates.index, y=gdp_on_yield_dates, mode='lines', line=dict(width=0),
        showlegend=False, hoverinfo='skip'
    ))
    fig.add_trace(go.Scatter(
        x=upper.index, y=upper, name="Yield above growth", mode='lines', line=dict(width=0),
        fill='tonexty', fillcolor='rgba(255, 90, 60, 0.30)', hoverinfo='skip'
    ))

    apply_sentiment_chart_layout(fig, f"{region} Yield vs Nominal GDP: {yield_name} vs GDP Growth")
    fig.update_yaxes(title_text="% per year", showgrid=True, gridcolor="#333333")
    return fig.to_html(full_html=False, include_plotlyjs=False, config={'responsive': True, 'displaylogo': False})
