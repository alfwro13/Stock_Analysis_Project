# visuals.py
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import ta
import textwrap
from quant_signals import get_candlestick_patterns

def create_intraday_chart(df, ticker, s1=None, s2=None, live_pattern_name=None, live_pattern_tooltip=None, live_pattern_score=None):
    """
    Generates a high-resolution, short-term chart using 5-minute data 
    for the current trading day. Conditionally plots algorithmic floors (S1/S2).
    """
    last_date = df.index[-1].date().strftime('%d-%b-%Y') if not df.empty else 'Today'
    
    fig = go.Figure(data=[go.Candlestick(
        x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="Intraday"
    )])
    
    if s1 is not None:
        fig.add_hline(y=s1, line_dash="dash", line_color="#00ffcc", annotation_text="S1 Support", annotation_position="top left", annotation_font_color="#00ffcc")
    if s2 is not None:
        fig.add_hline(y=s2, line_dash="dash", line_color="#00ffcc", annotation_text="S2 Support", annotation_position="top left", annotation_font_color="#00ffcc")
    
    title_text = f"{last_date} Pulse (5-Minute Intervals) - {ticker}"
    if live_pattern_name:
        title_text += f"<br><span style='color:#ffaa00; font-size: 13px;'><i>Live Formation: {live_pattern_name} (Hover over chart marker for details)</i></span>"

    if live_pattern_name and live_pattern_tooltip and not df.empty:
        last_row = df.iloc[-1]
        score = live_pattern_score if live_pattern_score is not None else 0
        
        wrapped_tooltip = "<br>".join(textwrap.wrap(live_pattern_tooltip, width=45))
        
        if score > 0:
            fig.add_annotation(x=last_row.name, y=last_row['Low'], yshift=-15, text="▲", hovertext=f"<b>{live_pattern_name}</b><br>{wrapped_tooltip}", showarrow=False, font=dict(color="#00ff00", size=18))
        elif score < 0:
            fig.add_annotation(x=last_row.name, y=last_row['High'], yshift=15, text="▼", hovertext=f"<b>{live_pattern_name}</b><br>{wrapped_tooltip}", showarrow=False, font=dict(color="#ff4d4d", size=18))
        else:
            fig.add_annotation(x=last_row.name, y=last_row['Low'], yshift=-15, text="◆", hovertext=f"<b>{live_pattern_name}</b><br>{wrapped_tooltip}", showarrow=False, font=dict(color="#ffaa00", size=16))
    
    fig.update_layout(
        template="plotly_dark",
        height=400,
        margin=dict(l=20, r=20, t=60, b=20),
        xaxis_rangeslider_visible=False,
        title=dict(text=title_text, x=0.5, xanchor='center'),
        hovermode="x unified",
        hoverlabel=dict(align="left")
    )

    clean_config = {
        'responsive': True,
        'displaylogo': False
    }

    return fig.to_html(full_html=False, include_plotlyjs='cdn', config=clean_config)


def create_macro_chart(df, df_baseline, ticker):
    """
    Generates the 5-Row Institutional Macro Chart.
    """
    df = df.tail(126).copy()
    
    df['MA_21'] = df['Close'].rolling(window=21).mean()
    df['MA_50'] = df['Close'].rolling(window=50).mean()
    df['MA_200'] = df['Close'].rolling(window=200).mean()
    
    indicator_bb = ta.volatility.BollingerBands(close=df["Close"], window=20, window_dev=2)
    df['BB_High'] = indicator_bb.bollinger_hband()
    df['BB_Low'] = indicator_bb.bollinger_lband()
    
    if df_baseline is not None:
        baseline_aligned = df_baseline['Close'].reindex(df.index, method='ffill')
        df['RS_Line'] = df['Close'] / baseline_aligned
        normalization_factor = df['Close'].iloc[0] / df['RS_Line'].iloc[0]
        df['RS_Normalized'] = df['RS_Line'] * normalization_factor

    df['RSI'] = ta.momentum.RSIIndicator(close=df['Close'], window=14).rsi()
    df['OBV'] = ta.volume.OnBalanceVolumeIndicator(close=df['Close'], volume=df['Volume']).on_balance_volume()
    
    macd = ta.trend.MACD(close=df['Close'])
    df['MACD_Line'] = macd.macd()
    df['MACD_Signal'] = macd.macd_signal()
    df['MACD_Hist'] = macd.macd_diff()

    fig = make_subplots(
        rows=5, cols=1, shared_xaxes=True, 
        row_heights=[0.4, 0.15, 0.15, 0.15, 0.15],
        vertical_spacing=0.03,
        subplot_titles=(f"{ticker} Macro Trend", "Volume", "RSI (14)", "MACD (Trend Reversals)", "On-Balance Volume")
    )

    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="Price"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA_21'], line=dict(color='#00ffcc', width=1.5), name="21D MA"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA_50'], line=dict(color='yellow', width=1.5), name="50D MA"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA_200'], line=dict(color='white', width=2), name="200D MA"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['BB_High'], line=dict(color='gray', dash='dash'), name="BB Upper"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['BB_Low'], line=dict(color='gray', dash='dash'), name="BB Lower"), row=1, col=1)
    
    if df_baseline is not None:
        fig.add_trace(go.Scatter(x=df.index, y=df['RS_Normalized'], line=dict(color='cyan', width=2), name="RS vs Benchmark"), row=1, col=1)

    if len(df) >= 16:
        last_14_days = df.tail(16)
        for i in range(2, len(last_14_days)):
            prev2_row = last_14_days.iloc[i-2]
            prev1_row = last_14_days.iloc[i-1]
            curr_row = last_14_days.iloc[i]
            
            patterns = get_candlestick_patterns(prev2_row, prev1_row, curr_row)
            for p in patterns:
                wrapped_tooltip = "<br>".join(textwrap.wrap(p['tooltip'], width=45))
                if p["score"] > 0:
                    fig.add_annotation(row=1, col=1, x=curr_row.name, y=curr_row['Low'], yshift=-15, text="▲", hovertext=f"<b>{p['name']}</b><br>{wrapped_tooltip}", showarrow=False, font=dict(color="#00ff00", size=14))
                elif p["score"] < 0:
                    fig.add_annotation(row=1, col=1, x=curr_row.name, y=curr_row['High'], yshift=15, text="▼", hovertext=f"<b>{p['name']}</b><br>{wrapped_tooltip}", showarrow=False, font=dict(color="#ff4d4d", size=14))
                else:
                    fig.add_annotation(row=1, col=1, x=curr_row.name, y=curr_row['Low'], yshift=-15, text="◆", hovertext=f"<b>{p['name']}</b><br>{wrapped_tooltip}", showarrow=False, font=dict(color="#ffaa00", size=12))

    colors = ['green' if row['Close'] >= row['Open'] else 'red' for index, row in df.iterrows()]
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], marker_color=colors, name="Volume"), row=2, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=df['RSI'], line=dict(color='purple', width=2), name="RSI"), row=3, col=1)
    fig.add_hline(y=70, line_dash="dot", line_color="red", row=3, col=1)
    fig.add_hline(y=30, line_dash="dot", line_color="green", row=3, col=1)
    fig.add_annotation(row=3, col=1, x=df.index[0], y=70, text="Overbought (70)", showarrow=False, font=dict(color="red", size=10), xanchor="left", yshift=8)
    fig.add_annotation(row=3, col=1, x=df.index[0], y=30, text="Oversold (30)", showarrow=False, font=dict(color="green", size=10), xanchor="left", yshift=-8)

    fig.add_trace(go.Scatter(x=df.index, y=df['MACD_Line'], line=dict(color='blue', width=1.5), name="MACD"), row=4, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MACD_Signal'], line=dict(color='orange', width=1.5), name="Signal"), row=4, col=1)
    macd_colors = ['green' if val >= 0 else 'red' for val in df['MACD_Hist']]
    fig.add_trace(go.Bar(x=df.index, y=df['MACD_Hist'], marker_color=macd_colors, name="Histogram"), row=4, col=1)

    fig.add_trace(go.Scatter(x=df.index, y=df['OBV'], line=dict(color='lightblue', width=2), name="OBV"), row=5, col=1)

    fig.update_layout(
        template="plotly_dark", 
        height=1200, 
        margin=dict(l=20, r=20, t=80, b=20), 
        xaxis_rangeslider_visible=False,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
        hovermode="x unified",
        hoverlabel=dict(align="left")
    )

    clean_config = {
        'responsive': True,
        'displaylogo': False
    }

    return fig.to_html(full_html=False, include_plotlyjs='cdn', config=clean_config)

def create_us_inflation_chart(df_spy: pd.DataFrame, df_cpi: pd.DataFrame) -> str:
    """
    Renders US Price Stability chart: CPI YoY % (orange, secondary axis) vs S&P 500 (cyan, primary axis).

    Input contract:
        df_cpi['value'] contains the RAW CPIAUCSL index level (e.g. ~316 in 2025), forward-filled daily.

    Transformation:
        1. Resample to month-start frequency, taking the first valid observation per month.
           This collapses the daily forward-fill back to the underlying monthly cadence.
        2. Apply pct_change(periods=12) to compute true 12-month (year-over-year) change.

    Reference lines: 2% (Fed target) and 5% (danger zone).
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
        df_cpi_local = df_cpi.copy().sort_index()
        # Collapse daily forward-fill back to true monthly frequency
        monthly_series = df_cpi_local['value'].resample('MS').first().dropna()
        # True 12-month YoY % change
        yoy_series = (monthly_series.pct_change(periods=12) * 100.0).dropna()

        if not yoy_series.empty:
            fig.add_trace(
                go.Scatter(
                    x=yoy_series.index, y=yoy_series.values, name="US CPI YoY %",
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
    The raw ONS D7G7 series is ALREADY an annualized percentage rate, so no transformation is needed.
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

    fig.update_layout(
        title=dict(text="UK Price Stability: CPI YoY vs FTSE 100", x=0.5, xanchor='center'),
        template="plotly_dark", height=350,
        margin=dict(l=20, r=20, t=50, b=20), hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    fig.update_yaxes(title_text="FTSE 100 Index", secondary_y=False, showgrid=False)
    fig.update_yaxes(title_text="CPI YoY (%)", secondary_y=True, showgrid=False)
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})


def create_us_liquidity_chart(df_spy: pd.DataFrame, df_m2: pd.DataFrame) -> str:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    if not df_spy.empty and 'Close' in df_spy.columns:
        fig.add_trace(go.Scatter(x=df_spy.index, y=df_spy['Close'], name="S&P 500", line=dict(color="#00ffcc", width=2), connectgaps=True), secondary_y=False)
    if not df_m2.empty and 'value' in df_m2.columns:
        fig.add_trace(go.Scatter(x=df_m2.index, y=df_m2['value'], name="US M2 Supply", line=dict(color="#ffaa00", width=2, dash='dot'), connectgaps=True), secondary_y=True)

    fig.update_layout(
        title=dict(text="US Liquidity: S&P 500 vs M2 Money Supply", x=0.5, xanchor='center'),
        template="plotly_dark", height=350,
        margin=dict(l=20, r=20, t=50, b=20), hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    fig.update_yaxes(title_text="S&P 500 Index", secondary_y=False, showgrid=False)
    fig.update_yaxes(title_text="M2 Supply (Trillions)", secondary_y=True, showgrid=False)
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})

def create_us_credit_chart(df_spread: pd.DataFrame) -> str:
    fig = go.Figure()
    if not df_spread.empty and 'value' in df_spread.columns:
        fig.add_trace(go.Scatter(x=df_spread.index, y=df_spread['value'], name="High Yield Spread", line=dict(color="#ff4d4d", width=2), connectgaps=True))

    fig.add_hline(y=5.0, line_dash="dash", line_color="#ff4d4d", annotation_text="Danger Zone (> 5.0%)", annotation_position="top left", annotation_font_color="#ff4d4d")

    fig.update_layout(
        title=dict(text="US Credit Stress: ICE BofA High Yield Spread", x=0.5, xanchor='center'),
        template="plotly_dark", height=350,
        margin=dict(l=20, r=20, t=50, b=20), hovermode="x unified"
    )
    fig.update_yaxes(title_text="Spread (%)", showgrid=True, gridcolor="#333333")
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})

def create_uk_liquidity_chart(df_ftse: pd.DataFrame, df_m4: pd.DataFrame) -> str:
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    if not df_ftse.empty and 'Close' in df_ftse.columns:
        fig.add_trace(go.Scatter(x=df_ftse.index, y=df_ftse['Close'], name="FTSE 100", line=dict(color="#00ffff", width=2), connectgaps=True), secondary_y=False)
    if not df_m4.empty and 'value' in df_m4.columns:
        fig.add_trace(go.Scatter(x=df_m4.index, y=df_m4['value'], name="UK M4 Supply", line=dict(color="#bb86fc", width=2, dash='dot'), connectgaps=True), secondary_y=True)

    fig.update_layout(
        title=dict(text="UK Liquidity: FTSE 100 vs M4 Money Supply", x=0.5, xanchor='center'),
        template="plotly_dark", height=350,
        margin=dict(l=20, r=20, t=50, b=20), hovermode="x unified", legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    fig.update_yaxes(title_text="FTSE 100 Index", secondary_y=False, showgrid=False)
    fig.update_yaxes(title_text="M4 Supply (Billions)", secondary_y=True, showgrid=False)
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})

def create_uk_credit_chart(df_spread: pd.DataFrame) -> str:
    fig = go.Figure()
    if not df_spread.empty and 'value' in df_spread.columns:
        fig.add_trace(go.Scatter(x=df_spread.index, y=df_spread['value'], name="UK IG Spread", line=dict(color="#ff4d4d", width=2), connectgaps=True))

    fig.add_hline(y=3.0, line_dash="dash", line_color="#ff4d4d", annotation_text="Danger Zone (> 3.0%)", annotation_position="top left", annotation_font_color="#ff4d4d")

    fig.update_layout(
        title=dict(text="UK Credit Stress: Sterling Corporate Spread", x=0.5, xanchor='center'),
        template="plotly_dark", height=350,
        margin=dict(l=20, r=20, t=50, b=20), hovermode="x unified"
    )
    fig.update_yaxes(title_text="Spread (%)", showgrid=True, gridcolor="#333333")
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})

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

    fig.update_layout(
        title=dict(text="Yield Curve Radar: US 10-Year vs 2-Year Treasury Spread", x=0.5, xanchor='center'),
        template="plotly_dark",
        height=350,
        margin=dict(l=20, r=20, t=50, b=20),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1)
    )
    fig.update_yaxes(title_text="Spread (%)", showgrid=True, gridcolor="#333333")

    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})