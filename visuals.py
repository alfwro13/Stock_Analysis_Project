import hashlib
import numpy as np
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta, date
from plotly.subplots import make_subplots
import pandas as pd
import ta
import textwrap
from quant_signals import get_candlestick_patterns
import time_engine

_EXCHANGE_DELAYS = {
    'GBp': 15, 'GBP': 15,  # LSE — Yahoo Finance free-tier delay
    'EUR': 15,              # Euronext and other European exchanges
}


def _intraday_market_tz(ticker: str, currency: str) -> str:
    return time_engine.get_user_tz().key


def create_intraday_chart(df, ticker, s1=None, s2=None, live_pattern_name=None, live_pattern_tooltip=None, live_pattern_score=None, include_plotlyjs='cdn', market_tz=None, data_delay_minutes=0):
    # market_tz: parquet stores naive UTC; if provided the index is converted before plotting. data_delay_minutes > 0 adds amber warning.
    if market_tz and not df.empty:
        df = df.copy()
        df.index = df.index.tz_localize('UTC').tz_convert(market_tz).tz_localize(None)

    last_date = df.index[-1].date().strftime('%d-%b-%Y') if not df.empty else 'Today'

    fig = go.Figure(data=[go.Candlestick(
        x=df.index, open=df['Open'], high=df['High'], low=df['Low'], close=df['Close'], name="Intraday"
    )])
    
    if s1 is not None:
        fig.add_hline(y=s1, line_dash="dash", line_color="#00ffcc", annotation_text="S1 Support", annotation_position="top left", annotation_font_color="#00ffcc")
    if s2 is not None:
        fig.add_hline(y=s2, line_dash="dash", line_color="#00ffcc", annotation_text="S2 Support", annotation_position="top left", annotation_font_color="#00ffcc")
    
    title_text = f"{last_date} Pulse (5-Minute Intervals) - {ticker}"
    if data_delay_minutes > 0:
        title_text += f"<br><span style='color:#ffaa00; font-size: 12px;'>⚠ Data may be delayed up to {data_delay_minutes} min (exchange policy)</span>"
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

    return fig.to_html(full_html=False, include_plotlyjs=include_plotlyjs, config=clean_config)


def create_macro_chart(df, df_baseline, ticker):
    # Compute long-window indicators on the full history before truncating so
    # MA_200 (needs 200 rows) and OBV (cumulative, benefits from full context)
    # always have valid values in the 126-day display window.
    df['MA_200'] = df['Close'].rolling(window=200).mean()
    df['OBV'] = ta.volume.OnBalanceVolumeIndicator(close=df['Close'], volume=df['Volume']).on_balance_volume()

    df = df.tail(126).copy()

    df['MA_21'] = df['Close'].rolling(window=21).mean()
    df['MA_50'] = df['Close'].rolling(window=50).mean()

    indicator_bb = ta.volatility.BollingerBands(close=df["Close"], window=20, window_dev=2)
    df['BB_High'] = indicator_bb.bollinger_hband()
    df['BB_Low'] = indicator_bb.bollinger_lband()

    if df_baseline is not None:
        baseline_aligned = df_baseline['Close'].reindex(df.index, method='ffill')
        df['RS_Line'] = df['Close'] / baseline_aligned
        normalization_factor = df['Close'].iloc[0] / df['RS_Line'].iloc[0]
        df['RS_Normalized'] = df['RS_Line'] * normalization_factor

    df['RSI'] = ta.momentum.RSIIndicator(close=df['Close'], window=14).rsi()
    
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

    colors = ['green' if row['Close'] >= row['Open'] else 'red' for _, row in df.iterrows()]
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


def create_anomaly_score_chart(df: pd.DataFrame, ticker: str, threshold: float = 0.7) -> str:
    # df must have ['anomaly_score', 'close_price'] with DatetimeIndex; uses one continuous line with per-point marker colours so the series is never visually split.
    fig = make_subplots(
        rows=2, cols=1,
        shared_xaxes=True,
        row_heights=[0.6, 0.4],
        vertical_spacing=0.06,
        subplot_titles=(f"{ticker} — Isolation Forest Anomaly Score (90d)", "Close Price"),
    )

    # Shaded alert zone — subtle red band above the threshold
    fig.add_hrect(
        y0=threshold, y1=1.01,
        fillcolor="#ff4d4d", opacity=0.08,
        line_width=0,
        row=1, col=1,
    )

    # Single continuous line — one unbroken series, per-point marker colours
    marker_colors = [
        '#ff4d4d' if s > threshold else '#00ffcc'
        for s in df['anomaly_score']
    ]
    fig.add_trace(
        go.Scatter(
            x=df.index,
            y=df['anomaly_score'],
            mode='lines+markers',
            name='Anomaly Score',
            line=dict(color='#888888', width=1.5),
            marker=dict(color=marker_colors, size=5),
        ),
        row=1, col=1,
    )

    fig.add_hline(
        y=threshold,
        line_dash="dot",
        line_color="#ffaa00",
        annotation_text=f"Alert Threshold ({threshold})",
        annotation_position="top right",
        annotation_font_color="#ffaa00",
        row=1, col=1,
    )

    fig.add_trace(
        go.Scatter(
            x=df.index, y=df['close_price'],
            mode='lines',
            name='Close',
            line=dict(color='white', width=1.5),
            showlegend=False,
        ),
        row=2, col=1,
    )

    fig.update_layout(
        template="plotly_dark",
        height=500,
        margin=dict(l=20, r=20, t=60, b=20),
        hovermode="x unified",
        hoverlabel=dict(align="left"),
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="center", x=0.5),
    )
    fig.update_yaxes(title_text="Anomaly Score", range=[0, 1], row=1, col=1)
    fig.update_yaxes(title_text="Close Price", row=2, col=1)
    fig.update_xaxes(rangeslider_visible=False)

    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})


def create_anomaly_feature_radar(features: dict, ticker: str) -> str:
    # features: {'volume_ratio', 'rsi_14', 'daily_return_pct', 'sma50_dist_pct', 'hist_vol_20', 'beta'} normalised to [0, 1] against typical market bounds.
    # (label, feature_key, range_lo, range_hi) — ranges represent typical market bounds
    AXES = [
        ('Volume Ratio',   'volume_ratio',     0.0,   3.0),
        ('RSI (14)',       'rsi_14',           0.0, 100.0),
        ('Daily Return %', 'daily_return_pct', -10.0, 10.0),
        ('SMA50 Dist %',   'sma50_dist_pct',  -20.0, 20.0),
        ('Hist Vol 20d',   'hist_vol_20',       0.0,   0.8),
        ('Beta',           'beta',              0.0,   3.0),
    ]

    labels, norm_vals, hover = [], [], []
    for label, key, lo, hi in AXES:
        raw = float(features.get(key) or 0.0)
        n = max(0.0, min(1.0, (raw - lo) / (hi - lo))) if hi != lo else 0.0
        labels.append(label)
        norm_vals.append(round(n, 3))
        hover.append(f"{label}: {raw:.3f}")

    labels += [labels[0]]
    norm_vals += [norm_vals[0]]
    hover += [hover[0]]

    fig = go.Figure(go.Scatterpolar(
        r=norm_vals,
        theta=labels,
        fill='toself',
        fillcolor='rgba(0, 255, 204, 0.12)',
        line=dict(color='#00ffcc', width=2),
        text=hover,
        hovertemplate='%{text}<extra></extra>',
    ))
    fig.update_layout(
        template='plotly_dark',
        polar=dict(
            radialaxis=dict(
                visible=True, range=[0, 1],
                tickvals=[0.25, 0.5, 0.75, 1.0],
                tickfont=dict(size=9),
            ),
            angularaxis=dict(tickfont=dict(size=11)),
        ),
        height=300,
        margin=dict(l=100, r=80, t=50, b=20),
        title=dict(text=f"{ticker} — Feature Snapshot", font=dict(size=13, color='#ccc'), x=0.5),
        showlegend=False,
    )
    return fig.to_html(
        full_html=False, include_plotlyjs=False,
        config={'responsive': True, 'displaylogo': False},
    )


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


def create_etf_correlation_chart(
    etf_ticker: str,
    constituent_tickers: list,
    normalized_df: "pd.DataFrame",
    rolling_corr: "pd.Series",
) -> str:
    """Generic correlation chart for any ETF + constituent basket."""
    if normalized_df.empty:
        fig = go.Figure()
        fig.update_layout(template="plotly_dark", height=650,
                          title=dict(text=f"{etf_ticker} Correlation — No Data", x=0.5))
        return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})

    fig = make_subplots(
        rows=2, cols=1, shared_xaxes=True,
        row_heights=[0.62, 0.38], vertical_spacing=0.06,
        subplot_titles=(
            "Normalised Performance (Base = 100)",
            f"30-Day Rolling Correlation: {etf_ticker} vs Constituent Basket",
        ),
    )

    etf_color = "#00ffff"
    constituent_palette = [
        "#f44336", "#ff9800", "#ffeb3b", "#4caf50", "#2196f3",
        "#9c27b0", "#e91e63", "#00bcd4", "#8bc34a", "#ff5722",
        "#3f51b5", "#009688", "#ffc107", "#673ab7", "#cddc39",
        "#f06292", "#4dd0e1", "#aed581", "#7986cb", "#ffb74d",
    ]
    color_map: dict = {}
    ci = 0
    for t in normalized_df.columns:
        if t == etf_ticker:
            color_map[t] = etf_color
        elif "=" in t:
            color_map[t] = "#ffeb3b"
        else:
            color_map[t] = constituent_palette[ci % len(constituent_palette)]
            ci += 1

    for ticker in [c for c in normalized_df.columns if c != etf_ticker] + [etf_ticker]:
        color = color_map.get(ticker, "#888888")
        width = 2.5 if ticker == etf_ticker else 1.2
        dash = "dot" if "=" in ticker else "solid"
        fig.add_trace(go.Scatter(
            x=normalized_df.index, y=normalized_df[ticker],
            name=ticker, line=dict(color=color, width=width, dash=dash),
            connectgaps=True, hovertemplate=f"{ticker}: %{{y:.1f}}<extra></extra>",
        ), row=1, col=1)

    if not rolling_corr.dropna().empty:
        fig.add_trace(go.Scatter(
            x=rolling_corr.index, y=rolling_corr.values,
            name="30D Correlation", line=dict(color="#00ffff", width=2),
            connectgaps=True, hovertemplate="Corr: %{y:.3f}<extra></extra>",
        ), row=2, col=1)
        fig.add_hline(y=0.7,  line_dash="dash", line_color="#4caf50", line_width=1, row=2, col=1)
        fig.add_hline(y=0.0,  line_dash="dot",  line_color="#666666", line_width=1, row=2, col=1)
        fig.add_hline(y=-0.7, line_dash="dash", line_color="#f44336", line_width=1, row=2, col=1)

    fig.update_layout(
        title=dict(text=f"{etf_ticker} vs Constituent Basket — Normalised Performance & Correlation",
                   x=0.5, xanchor="center", font=dict(size=14), y=0.98),
        template="plotly_dark", height=720,
        margin=dict(l=20, r=20, t=130, b=160), hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.06, xanchor="left", x=0,
                    font=dict(size=9), tracegroupgap=2),
    )
    fig.update_yaxes(title_text="Indexed (100 = start)", row=1, col=1, showgrid=True, gridcolor="#333333")
    fig.update_yaxes(title_text="Pearson r", row=2, col=1, showgrid=True, gridcolor="#333333", range=[-1.1, 1.1])
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})


def create_etf_prediction_chart(
    etf_ticker: str,
    currency: str,
    etf_hist: "pd.Series",
    prediction: dict,
) -> str:
    """Generic prediction chart: recent closes + predicted next open star + regression CI band."""
    fig = go.Figure()
    ccy = currency or ""

    if etf_hist is not None and not etf_hist.empty:
        fig.add_trace(go.Scatter(
            x=etf_hist.index, y=etf_hist.values,
            name=f"{etf_ticker} Close",
            line=dict(color="#00ffff", width=2), connectgaps=True,
            hovertemplate=f"Close: %{{y:.4f}}<extra></extra>",
        ))

    last_close = prediction.get("last_etf_close")
    predicted_price = prediction.get("predicted_price")
    reg = prediction.get("regression_engine")

    if last_close and etf_hist is not None and not etf_hist.empty:
        fig.add_hline(
            y=last_close, line_dash="dash", line_color="#888888", line_width=1,
            annotation_text=f"Last Close: {last_close:.4f}",
            annotation_position="top right",
            annotation_font_color="#888888", annotation_font_size=11,
        )

    if predicted_price and etf_hist is not None and not etf_hist.empty:
        last_date = etf_hist.index[-1]
        next_date = last_date + pd.offsets.BDay(1)

        if reg and reg.get("lower_bound") and reg.get("upper_bound"):
            fig.add_trace(go.Scatter(
                x=[next_date, next_date], y=[reg["upper_bound"], reg["lower_bound"]],
                fill="toself", fillcolor="rgba(187, 134, 252, 0.15)",
                line=dict(color="rgba(187, 134, 252, 0.3)", width=1),
                name="95% CI", hovertemplate="CI: %{y:.4f}<extra></extra>",
            ))

        change_pct = prediction.get("predicted_change_pct", 0)
        fig.add_trace(go.Scatter(
            x=[next_date], y=[predicted_price],
            mode="markers+text",
            name=f"Predicted: {predicted_price:.4f}",
            marker=dict(color="#bb86fc", size=16, symbol="star"),
            text=[f"{predicted_price:.4f} ({change_pct:+.2f}%)"],
            textposition="top right",
            textfont=dict(color="#bb86fc", size=11),
            hovertemplate=f"Predicted: {predicted_price:.4f} ({change_pct:+.2f}%)<extra></extra>",
        ))

    fig.update_layout(
        title=dict(text=f"{etf_ticker} — Historical Close + Predicted Next Open ({ccy})",
                   x=0.5, xanchor="center"),
        template="plotly_dark", height=450,
        margin=dict(l=20, r=20, t=90, b=100), hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.08, xanchor="left", x=0,
                    font=dict(size=9), tracegroupgap=2),
    )
    fig.update_yaxes(title_text=ccy, showgrid=True, gridcolor="#333333")
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})


def create_etf_contributions_chart(etf_ticker: str, contributions: list) -> str:
    """Generic holdings contributions bar chart."""
    if not contributions:
        fig = go.Figure()
        fig.update_layout(template="plotly_dark", height=350,
                          title=dict(text="Holdings Contributions — No Data", x=0.5))
        return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})

    sorted_items = sorted(contributions, key=lambda x: abs(x.get("contribution_pct", 0)), reverse=True)
    tickers = [c["ticker"] for c in sorted_items]
    values = [c["contribution_pct"] for c in sorted_items]
    colors = ["#4caf50" if v >= 0 else "#f44336" for v in values]
    weights = [f"{c['weight'] * 100:.1f}%" for c in sorted_items]
    ret_key = "return_pct" if "return_pct" in sorted_items[0] else "us_return_pct"

    fig = go.Figure(go.Bar(
        x=values, y=tickers, orientation="h",
        marker=dict(color=colors),
        customdata=list(zip(weights, [c[ret_key] for c in sorted_items])),
        hovertemplate="<b>%{y}</b><br>Contribution: %{x:+.3f}%<br>Weight: %{customdata[0]}<br>Return: %{customdata[1]:+.2f}%<extra></extra>",
    ))
    fig.add_vline(x=0, line_color="#555555", line_width=1)
    fig.update_layout(
        title=dict(text=f"{etf_ticker} — Holdings Weighted Contribution to Predicted Move (%)",
                   x=0.5, xanchor="center"),
        template="plotly_dark",
        height=max(300, len(tickers) * 32 + 100),
        margin=dict(l=20, r=20, t=50, b=40),
        xaxis=dict(title="Contribution (%)", showgrid=True, gridcolor="#333333", zeroline=False),
        yaxis=dict(autorange="reversed"),
        showlegend=False,
    )
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})


def create_etf_overlay_chart(
    etf_ticker: str,
    etf_exchange: str,
    constituent_exchanges: "list[str]",
    etf_series: "pd.Series",
    constituent_series: "dict[str, pd.Series]",
    etf_last_close: float,
    prediction: "dict | None" = None,
    next_open_date: "date | None" = None,
    constituent_prev_closes: "dict[str, float] | None" = None,
    now_utc: "datetime | None" = None,
    trading_date: "date | None" = None,
    session_relationship: str = "behind",
) -> str:
    """Generic time-aligned intraday overlay chart."""
    if prediction is None:
        prediction = {}
    if not constituent_exchanges:
        constituent_exchanges = ["NYSE"]
    user_tz = time_engine.get_user_tz()

    if now_utc is None:
        now_utc = datetime.now(timezone.utc).replace(tzinfo=None)

    now_local = pd.Timestamp(now_utc).tz_localize("UTC").tz_convert(user_tz).tz_localize(None)
    x_start = now_local - pd.Timedelta(hours=20)
    x_end = now_local + pd.Timedelta(hours=10)

    def _to_local(s: "pd.Series") -> "pd.Series":
        if s.empty:
            return s
        idx = pd.DatetimeIndex(s.index)
        if idx.tz is None:
            idx = idx.tz_localize("UTC")
        return s.set_axis(idx.tz_convert(user_tz).tz_localize(None))

    def _to_pct(s: "pd.Series", ref: float) -> "pd.Series":
        return (s / ref - 1) * 100

    def _break_overnight_gaps(s: "pd.Series", gap_hours: float = 1.5) -> "pd.Series":
        if len(s) < 2:
            return s
        parts = []
        for i in range(len(s) - 1):
            parts.append(s.iloc[i : i + 1])
            gap = (s.index[i + 1] - s.index[i]).total_seconds() / 3600
            if gap > gap_hours:
                mid = s.index[i] + (s.index[i + 1] - s.index[i]) / 2
                parts.append(pd.Series([float("nan")], index=[mid]))
        parts.append(s.iloc[-1:])
        return pd.concat(parts)

    palette = [
        "#f44336", "#ff9800", "#ffeb3b", "#4caf50", "#2196f3",
        "#9c27b0", "#e91e63", "#00bcd4", "#8bc34a", "#ff5722",
        "#3f51b5", "#009688", "#ffc107", "#673ab7", "#cddc39",
        "#f06292", "#4dd0e1", "#aed581", "#7986cb", "#ffb74d",
    ]

    # Reserved exchange colours; all others get a deterministic hash-picked colour pair
    _reserved_colors = {
        "LSE":  ("#00cccc", "#888888"),
        "NYSE": ("#f6ad55", "#f87171"),
    }
    _open_palette  = ["#84cc16", "#c084fc", "#f472b6", "#fde047", "#60a5fa", "#2dd4bf", "#fb923c", "#a78bfa", "#34d399"]
    _close_palette = ["#4d7c0f", "#7e22ce", "#9d174d", "#b45309", "#1e3a8a", "#0f766e", "#c2410c", "#6d28d9", "#065f46"]

    def _exchange_colors(exchange: str) -> "tuple[str, str]":
        if exchange in _reserved_colors:
            return _reserved_colors[exchange]
        idx = int(hashlib.md5(exchange.encode()).hexdigest(), 16) % len(_open_palette)
        return _open_palette[idx], _close_palette[idx]

    fig = go.Figure()

    etf_local = _to_local(etf_series)
    if not etf_local.empty and etf_last_close > 0:
        etf_gapped = _break_overnight_gaps(etf_local)
        fig.add_trace(go.Scatter(
            x=etf_gapped.index,
            y=_to_pct(etf_gapped, etf_last_close).values,
            name=etf_ticker,
            line=dict(color="#00ffff", width=2.5),
            connectgaps=False,
            hovertemplate=f"{etf_ticker}: %{{y:+.2f}}%<extra></extra>",
        ))

    for i, (ticker, series) in enumerate(constituent_series.items()):
        c_local = _to_local(series)
        if c_local.empty:
            continue
        ref = (constituent_prev_closes or {}).get(ticker) or float(c_local.dropna().iloc[0])
        if ref == 0:
            continue
        color = palette[i % len(palette)]
        c_gapped = _break_overnight_gaps(c_local)
        fig.add_trace(go.Scatter(
            x=c_gapped.index,
            y=_to_pct(c_gapped, ref).values,
            name=ticker,
            line=dict(color=color, width=1.2),
            opacity=0.75,
            connectgaps=False,
            hovertemplate=f"{ticker}: %{{y:+.2f}}%<extra></extra>",
        ))

    # Exchange marker specs: ETF first, then each constituent; y-positions staggered to prevent label collision.
    _open_y_positions  = [0.97, 0.91, 0.85, 0.79, 0.73, 0.67]
    _close_y_positions = [1.00, 0.94, 0.88, 0.82, 0.76, 0.70]
    _event_specs = []
    _seen_exchanges: list[str] = []

    def _add_exchange_spec(exchange: str) -> None:
        if exchange in _seen_exchanges:
            return
        _seen_exchanges.append(exchange)
        idx = len(_seen_exchanges) - 1
        open_col, close_col = _exchange_colors(exchange)
        y_open  = _open_y_positions[min(idx, len(_open_y_positions) - 1)]
        y_close = _close_y_positions[min(idx, len(_close_y_positions) - 1)]
        _event_specs.append((exchange, True,  f"{exchange} Open",  open_col,  "dot",  y_open,  1.2))
        _event_specs.append((exchange, False, f"{exchange} Close", close_col, "dash", y_close, 1.5))

    _add_exchange_spec(etf_exchange)
    for _con_exch in constituent_exchanges:
        _add_exchange_spec(_con_exch)

    _check_start = (x_start - pd.Timedelta(hours=2)).date()
    _check_end = (x_end + pd.Timedelta(hours=2)).date()
    _cur = _check_start
    while _cur <= _check_end:
        if _cur.weekday() < 5:
            for _exch, _is_open, _label, _color, _dash, _y, _width in _event_specs:
                try:
                    _open_t, _close_t = time_engine.market_window_utc(_exch)
                    _t = _open_t if _is_open else _close_t
                    _utc_dt = datetime.combine(_cur, _t)
                    _local_dt = pd.Timestamp(_utc_dt).tz_localize("UTC").tz_convert(user_tz).tz_localize(None)
                    if not (x_start <= _local_dt <= x_end):
                        continue
                    _dt_str = str(_local_dt)
                    fig.add_shape(
                        type="line", x0=_dt_str, x1=_dt_str, y0=0, y1=1, yref="paper",
                        line=dict(dash=_dash, color=_color, width=_width),
                    )
                    fig.add_annotation(
                        x=_dt_str, y=_y, yref="paper",
                        text=f"{_label} {_local_dt.strftime('%H:%M')}",
                        showarrow=False, font=dict(color=_color, size=10),
                        xanchor="left", yanchor="top",
                    )
                except Exception:
                    pass
        _cur += timedelta(days=1)

    now_str = str(now_local)
    fig.add_shape(
        type="line", x0=now_str, x1=now_str, y0=0, y1=1, yref="paper",
        line=dict(dash="dot", color="rgba(255,255,255,0.35)", width=1.0),
    )
    fig.add_annotation(
        x=now_str, y=0.85, yref="paper",
        text=f"Now {now_local.strftime('%H:%M')}",
        showarrow=False, font=dict(color="rgba(255,255,255,0.5)", size=10),
        xanchor="left", yanchor="top",
    )

    # Prediction star — ETF open for ahead/same sessions; constituent open for behind (US → UK) intraday.
    pred_price = prediction.get("predicted_price")
    pred_pct = prediction.get("predicted_change_pct", 0)
    signal_source = prediction.get("signal_source", "daily_close")
    if pred_price and etf_last_close > 0 and next_open_date is not None:
        is_intraday = signal_source in ("intraday_premarket", "intraday_live")
        use_constituent_open = session_relationship == "behind" and is_intraday
        if use_constituent_open:
            # Star at primary constituent exchange open (LSE ETF with US constituents case)
            _primary = constituent_exchanges[0]
            _c_open_t, _c_close_t = time_engine.market_window_utc(_primary)
            _now_c = datetime.now(timezone.utc).replace(tzinfo=None)
            _today_c = datetime.now(timezone.utc).date()
            if _today_c.weekday() == 5:
                _c_target = _today_c + timedelta(days=2)
            elif _today_c.weekday() == 6:
                _c_target = _today_c + timedelta(days=1)
            elif _now_c < datetime.combine(_today_c, _c_close_t):
                _c_target = _today_c
            elif _today_c.weekday() == 4:
                _c_target = _today_c + timedelta(days=3)
            else:
                _c_target = _today_c + timedelta(days=1)
            pred_dt_utc = datetime.combine(_c_target, _c_open_t)
        else:
            # Star at ETF next open (Asia → UK, or post-close / daily-close signal)
            etf_open_t, _ = time_engine.market_window_utc(etf_exchange)
            pred_dt_utc = datetime.combine(next_open_date, etf_open_t)
        pred_dt = pd.Timestamp(pred_dt_utc).tz_localize("UTC").tz_convert(user_tz).tz_localize(None)
        fig.add_trace(go.Scatter(
            x=[pred_dt],
            y=[pred_pct],
            mode="markers+text",
            name=f"Predicted: {pred_price:.4f}",
            marker=dict(color="#bb86fc", size=18, symbol="star"),
            text=[f"{pred_price:.4f} ({pred_pct:+.2f}%)"],
            textposition="top right",
            textfont=dict(color="#bb86fc", size=11),
            hovertemplate=f"Predicted: {pred_price:.4f} ({pred_pct:+.2f}%)<extra></extra>",
        ))

    fig.update_layout(
        title=dict(
            text=f"{etf_ticker} Intraday vs Constituents — Time-Aligned",
            x=0.5, xanchor="center", font=dict(size=13),
        ),
        template="plotly_dark",
        height=580,
        margin=dict(l=20, r=20, t=80, b=160),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="top", y=-0.06, xanchor="left", x=0,
                    font=dict(size=9), tracegroupgap=2),
    )
    fig.update_yaxes(
        title_text="% Change from Previous Close",
        ticksuffix="%",
        zeroline=True, zerolinecolor="#555555", zerolinewidth=1.5,
        showgrid=True, gridcolor="#333333",
    )
    fig.update_xaxes(range=[str(x_start), str(x_end)], showgrid=True, gridcolor="#333333")
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})


def create_ai_contagion_performance_chart(ticker_dfs: dict, period_label: str = "30-Day") -> str:
    if not ticker_dfs:
        fig = go.Figure()
        fig.update_layout(template="plotly_dark", height=480, title=dict(text="AI Sector Performance — No Data", x=0.5))
        return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})

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
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})


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
        return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})

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
    return fig.to_html(full_html=False, include_plotlyjs='cdn', config={'responsive': True, 'displaylogo': False})