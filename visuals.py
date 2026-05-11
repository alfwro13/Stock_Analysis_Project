# visuals.py
import plotly.graph_objects as go
from plotly.subplots import make_subplots
import pandas as pd
import ta
from quant_signals import get_candlestick_patterns

def create_intraday_chart(df, ticker, s1=None, s2=None, live_pattern_name=None, live_pattern_tooltip=None):
    """
    Generates a high-resolution, short-term chart using 5-minute data 
    for the current trading day. Conditionally plots algorithmic floors (S1/S2).
    """
    # Extract the exact date dynamically from the dataframe index
    last_date = df.index[-1].date().strftime('%d-%b-%Y') if not df.empty else 'Today'
    
    fig = go.Figure(data=[go.Candlestick(
        x=df.index,
        open=df['Open'],
        high=df['High'],
        low=df['Low'],
        close=df['Close'],
        name="Intraday"
    )])
    
    # Plot Pivot Point Floors if they were successfully calculated
    if s1 is not None:
        fig.add_hline(y=s1, line_dash="dash", line_color="#00ffcc", 
                      annotation_text="S1 Support", annotation_position="top left", 
                      annotation_font_color="#00ffcc")
    if s2 is not None:
        fig.add_hline(y=s2, line_dash="dash", line_color="#00ffcc", 
                      annotation_text="S2 Support", annotation_position="top left", 
                      annotation_font_color="#00ffcc")
    
    title_text = f"{last_date} Pulse (5-Minute Intervals) - {ticker}"
    if live_pattern_name:
        title_text += f"<br><span style='color:#ffaa00; font-size: 13px;'><i>Live Formation: {live_pattern_name}</i></span>"
    
    fig.update_layout(
        template="plotly_dark",
        height=400,
        margin=dict(l=20, r=20, t=60, b=20),
        xaxis_rangeslider_visible=False,
        # Centering the title to avoid the fullscreen button
        title=dict(text=title_text, x=0.5)
    )
    return fig.to_html(full_html=False, include_plotlyjs='cdn')

def create_macro_chart(df, df_sp500, ticker):
    """
    Generates the 5-Row Institutional Macro Chart.
    """
    df = df.tail(126).copy() # Last 6 months
    
    # 1. Moving Averages & Bollinger Bands
    df['MA_21'] = df['Close'].rolling(window=21).mean()
    df['MA_50'] = df['Close'].rolling(window=50).mean()
    df['MA_200'] = df['Close'].rolling(window=200).mean()
    
    indicator_bb = ta.volatility.BollingerBands(close=df["Close"], window=20, window_dev=2)
    df['BB_High'] = indicator_bb.bollinger_hband()
    df['BB_Low'] = indicator_bb.bollinger_lband()
    
    # 2. Relative Strength Line (vs S&P 500)
    if df_sp500 is not None:
        sp500_aligned = df_sp500['Close'].reindex(df.index, method='ffill')
        df['RS_Line'] = df['Close'] / sp500_aligned
        normalization_factor = df['Close'].iloc[0] / df['RS_Line'].iloc[0]
        df['RS_Normalized'] = df['RS_Line'] * normalization_factor

    # 3. Oscillators & Volume
    df['RSI'] = ta.momentum.RSIIndicator(close=df['Close'], window=14).rsi()
    df['OBV'] = ta.volume.OnBalanceVolumeIndicator(close=df['Close'], volume=df['Volume']).on_balance_volume()
    
    # 4. MACD
    macd = ta.trend.MACD(close=df['Close'])
    df['MACD_Line'] = macd.macd()
    df['MACD_Signal'] = macd.macd_signal()
    df['MACD_Hist'] = macd.macd_diff()

    # Create 5 Subplots
    fig = make_subplots(
        rows=5, cols=1, shared_xaxes=True, 
        row_heights=[0.4, 0.15, 0.15, 0.15, 0.15],
        vertical_spacing=0.03,
        subplot_titles=(
            f"{ticker} Macro Trend", "Volume", "RSI (14)", "MACD (Trend Reversals)", "On-Balance Volume"
        )
    )

    # ROW 1: Price, MAs, and RS Line
    fig.add_trace(go.Candlestick(x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="Price"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA_21'], line=dict(color='#00ffcc', width=1.5), name="21D MA"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA_50'], line=dict(color='yellow', width=1.5), name="50D MA"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MA_200'], line=dict(color='white', width=2), name="200D MA"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['BB_High'], line=dict(color='gray', dash='dash'), name="BB Upper"), row=1, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['BB_Low'], line=dict(color='gray', dash='dash'), name="BB Lower"), row=1, col=1)
    
    if df_sp500 is not None:
        fig.add_trace(go.Scatter(x=df.index, y=df['RS_Normalized'], line=dict(color='cyan', width=2), name="RS vs S&P500"), row=1, col=1)

    # ALGORITHMIC CHART ANNOTATIONS (Last 14 Days Only)
    # Detects patterns and overlays visual triangles on the primary candlestick chart
    if len(df) >= 15:
        last_14_days = df.tail(15)
        for i in range(1, len(last_14_days)):
            prev_row = last_14_days.iloc[i-1]
            curr_row = last_14_days.iloc[i]
            
            patterns = get_candlestick_patterns(prev_row, curr_row)
            for p in patterns:
                # Bullish / Neutral patterns get green upward arrows below the candle
                if p["score"] >= 0:
                    fig.add_annotation(
                        row=1, col=1, x=curr_row.name, y=curr_row['Low'],
                        yshift=-15, text="▲", hovertext=f"<b>{p['name']}</b><br>{p['tooltip']}",
                        showarrow=False, font=dict(color="#00ff00", size=14)
                    )
                # Bearish patterns get red downward arrows above the candle
                else:
                    fig.add_annotation(
                        row=1, col=1, x=curr_row.name, y=curr_row['High'],
                        yshift=15, text="▼", hovertext=f"<b>{p['name']}</b><br>{p['tooltip']}",
                        showarrow=False, font=dict(color="#ff4d4d", size=14)
                    )

    # ROW 2: Volume
    colors = ['green' if row['Close'] >= row['Open'] else 'red' for index, row in df.iterrows()]
    fig.add_trace(go.Bar(x=df.index, y=df['Volume'], marker_color=colors, name="Volume"), row=2, col=1)

    # ROW 3: RSI (With Text Annotations)
    fig.add_trace(go.Scatter(x=df.index, y=df['RSI'], line=dict(color='purple', width=2), name="RSI"), row=3, col=1)
    fig.add_hline(y=70, line_dash="dot", line_color="red", row=3, col=1)
    fig.add_hline(y=30, line_dash="dot", line_color="green", row=3, col=1)
    fig.add_annotation(row=3, col=1, x=df.index[0], y=70, text="Overbought (70)", showarrow=False, font=dict(color="red", size=10), xanchor="left", yshift=8)
    fig.add_annotation(row=3, col=1, x=df.index[0], y=30, text="Oversold (30)", showarrow=False, font=dict(color="green", size=10), xanchor="left", yshift=-8)

    # ROW 4: MACD
    fig.add_trace(go.Scatter(x=df.index, y=df['MACD_Line'], line=dict(color='blue', width=1.5), name="MACD"), row=4, col=1)
    fig.add_trace(go.Scatter(x=df.index, y=df['MACD_Signal'], line=dict(color='orange', width=1.5), name="Signal"), row=4, col=1)
    macd_colors = ['green' if val >= 0 else 'red' for val in df['MACD_Hist']]
    fig.add_trace(go.Bar(x=df.index, y=df['MACD_Hist'], marker_color=macd_colors, name="Histogram"), row=4, col=1)

    # ROW 5: OBV
    fig.add_trace(go.Scatter(x=df.index, y=df['OBV'], line=dict(color='lightblue', width=2), name="OBV"), row=5, col=1)

    fig.update_layout(
        template="plotly_dark", 
        height=1200, 
        margin=dict(l=20, r=20, t=80, b=20), 
        xaxis_rangeslider_visible=False,
        legend=dict(
            orientation="h",
            yanchor="bottom",
            y=1.02,
            xanchor="center",
            x=0.5
        )
    )
    return fig.to_html(full_html=False, include_plotlyjs='cdn')