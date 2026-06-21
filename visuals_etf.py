import hashlib
import plotly.graph_objects as go
from datetime import datetime, timezone, timedelta, date
from plotly.subplots import make_subplots
import pandas as pd
import time_engine


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
        return fig.to_html(full_html=False, include_plotlyjs=False, config={'responsive': True, 'displaylogo': False})

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
    return fig.to_html(full_html=False, include_plotlyjs=False, config={'responsive': True, 'displaylogo': False})


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
    return fig.to_html(full_html=False, include_plotlyjs=False, config={'responsive': True, 'displaylogo': False})


def create_etf_contributions_chart(etf_ticker: str, contributions: list) -> str:
    """Generic holdings contributions bar chart."""
    if not contributions:
        fig = go.Figure()
        fig.update_layout(template="plotly_dark", height=350,
                          title=dict(text="Holdings Contributions — No Data", x=0.5))
        return fig.to_html(full_html=False, include_plotlyjs=False, config={'responsive': True, 'displaylogo': False})

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
    return fig.to_html(full_html=False, include_plotlyjs=False, config={'responsive': True, 'displaylogo': False})


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
        now_utc = datetime.now(timezone.utc)

    _now_ts = pd.Timestamp(now_utc)
    if _now_ts.tz is None:
        _now_ts = _now_ts.tz_localize(timezone.utc)
    now_local = _now_ts.tz_convert(user_tz).tz_localize(None)
    x_start = now_local - pd.Timedelta(hours=20)
    x_end = now_local + pd.Timedelta(hours=10)

    def _to_local(s: "pd.Series") -> "pd.Series":
        if s.empty:
            return s
        idx = pd.DatetimeIndex(s.index)
        if idx.tz is None:
            idx = idx.tz_localize(timezone.utc)
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
                    _local_dt = pd.Timestamp(_utc_dt).tz_localize(timezone.utc).tz_convert(user_tz).tz_localize(None)
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
            _now_c = datetime.now(timezone.utc)
            _today_c = _now_c.date()
            if _today_c.weekday() == 5:
                _c_target = _today_c + timedelta(days=2)
            elif _today_c.weekday() == 6:
                _c_target = _today_c + timedelta(days=1)
            elif _now_c < datetime.combine(_today_c, _c_close_t, tzinfo=timezone.utc):
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
        pred_dt = pd.Timestamp(pred_dt_utc).tz_localize(timezone.utc).tz_convert(user_tz).tz_localize(None)
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
    return fig.to_html(full_html=False, include_plotlyjs=False, config={'responsive': True, 'displaylogo': False})
