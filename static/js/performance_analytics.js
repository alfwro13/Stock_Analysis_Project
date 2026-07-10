var selectedAccountId = "all";

var METRIC_GROUPS = [
    {
        containerId: "pt-cards-risk_adjusted_ratios",
        groupKey: "risk_adjusted_ratios",
        metrics: [
            { key: "sortino_ratio", label: "Sortino Ratio", fmt: "num",
              tip: "Like the Sharpe ratio, but only penalises downside volatility — upside swings don't count against it." },
            { key: "calmar_ratio", label: "Calmar Ratio", fmt: "num",
              tip: "Annualised return divided by the maximum drawdown — return earned per unit of the worst peak-to-trough loss." },
            { key: "omega_ratio", label: "Omega Ratio", fmt: "num",
              tip: "Ratio of total gains to total losses relative to the risk-free rate. Above 1 means gains outweighed losses." },
            { key: "profit_factor", label: "Profit Factor", fmt: "num",
              tip: "Sum of all positive daily returns divided by the absolute sum of all negative daily returns." },
        ],
    },
    {
        containerId: "pt-cards-drawdown_analytics",
        groupKey: "drawdown_analytics",
        metrics: [
            { key: "max_drawdown", label: "Max Drawdown", fmt: "pct",
              tip: "The largest peak-to-trough decline in portfolio value over the cached history, computed from daily returns." },
            { key: "longest_drawdown_days", label: "Longest Drawdown", fmt: "days",
              tip: "The longest continuous stretch the portfolio spent below a previous high." },
            { key: "time_underwater_days", label: "Time Underwater", fmt: "days",
              tip: "Days since the portfolio last reached a new high." },
            { key: "ulcer_index", label: "Ulcer Index", fmt: "num",
              tip: "Measures the depth and duration of drawdowns combined — higher values mean deeper, longer-lasting declines." },
        ],
    },
    {
        containerId: "pt-cards-distribution_tail_stats",
        groupKey: "distribution_tail_stats",
        metrics: [
            { key: "best_day", label: "Best Day", fmt: "pct", tip: "The single best daily return in the cached history." },
            { key: "worst_day", label: "Worst Day", fmt: "pct", tip: "The single worst daily return in the cached history." },
            { key: "best_month", label: "Best Month", fmt: "pct", tip: "The best calendar month's compounded return in the cached history." },
            { key: "worst_month", label: "Worst Month", fmt: "pct", tip: "The worst calendar month's compounded return in the cached history." },
            { key: "tail_ratio", label: "Tail Ratio", fmt: "num",
              tip: "Ratio of the 95th-percentile daily gain to the 5th-percentile daily loss — above 1 means the best days outsized the worst." },
        ],
    },
    {
        containerId: "pt-cards-win_loss_stats",
        groupKey: "win_loss_stats",
        metrics: [
            { key: "win_rate", label: "Win Rate", fmt: "pct", tip: "Percentage of cached trading days with a positive return." },
            { key: "avg_win", label: "Average Win", fmt: "pct", tip: "Mean daily return on winning days." },
            { key: "avg_loss", label: "Average Loss", fmt: "pct", tip: "Mean daily return on losing days." },
            { key: "payoff_ratio", label: "Win/Loss Ratio", fmt: "num",
              tip: "Average win divided by the absolute average loss — the payoff ratio." },
            { key: "max_consecutive_wins", label: "Max Consecutive Wins", fmt: "days",
              tip: "The longest streak of consecutive positive-return days." },
            { key: "max_consecutive_losses", label: "Max Consecutive Losses", fmt: "days",
              tip: "The longest streak of consecutive negative-return days." },
        ],
    },
];

function _fmt_gbp(value) {
    if (value === null || value === undefined) return "—";
    if (value >= 1e6) return "£" + (value / 1e6).toFixed(2) + "M";
    if (value >= 1e3) return "£" + (value / 1e3).toFixed(1) + "K";
    return "£" + Math.round(value).toLocaleString();
}

function _num(v, dec) {
    if (v === null || v === undefined) return "—";
    return v.toFixed(dec !== undefined ? dec : 2);
}

function _pct(v, dec) {
    if (v === null || v === undefined) return "—";
    return (v * 100).toFixed(dec !== undefined ? dec : 2) + "%";
}

function _days(v) {
    if (v === null || v === undefined) return "—";
    return v + (v === 1 ? " day" : " days");
}

function _fmtMetric(v, fmt) {
    if (fmt === "pct") return _pct(v);
    if (fmt === "days") return _days(v);
    return _num(v);
}

function _card(label, value, tipText) {
    var labelHtml = tipText
        ? '<abbr title="' + tipText.replace(/"/g, "&quot;") + '">' + label + "</abbr>"
        : label;
    return '<div class="xray-metric-card">'
        + '<div class="xray-metric-label">' + labelHtml + "</div>"
        + '<div class="xray-metric-value">' + value + "</div>"
        + "</div>";
}

function renderPlaceholderCards() {
    METRIC_GROUPS.forEach(function (group) {
        var container = document.getElementById(group.containerId);
        var html = "";
        group.metrics.forEach(function (m) {
            html += _card(m.label, "—", m.tip);
        });
        container.innerHTML = html;
    });
}

function renderMetricCards(metricsData) {
    METRIC_GROUPS.forEach(function (group) {
        var container = document.getElementById(group.containerId);
        var groupData = metricsData[group.groupKey] || {};
        var html = "";
        group.metrics.forEach(function (m) {
            html += _card(m.label, _fmtMetric(groupData[m.key], m.fmt), m.tip);
        });
        container.innerHTML = html;
    });
}

function _showWarning(text) {
    var el = document.getElementById("pt-warning");
    el.textContent = text;
    el.classList.remove("d-none");
}

function _hideWarning() {
    document.getElementById("pt-warning").classList.add("d-none");
}

var CHART_IDS = ["pt-chart-underwater", "pt-chart-cumulative-growth", "pt-chart-monthly-heatmap", "pt-chart-histogram"];

function _clearCharts() {
    CHART_IDS.forEach(function (id) {
        document.getElementById(id).innerHTML = "";
    });
}

function _ptChartHeight() {
    // Mobile can't go below 400 — static/css/styles.css forces a 400px min-height
    // on .js-plotly-plot under 768px; a smaller value here leaves the chart pinned
    // short inside a taller, CSS-floored container.
    return window.innerWidth < 768 ? 400 : 350;
}

var PT_CHART_WRAPPER_IDS = [
    "pt-chart-underwater-outer",
    "pt-chart-cumulative-growth-outer",
    "pt-chart-monthly-heatmap-outer",
    "pt-chart-histogram-outer",
];

// These charts are drawn via the JS API (Plotly.react), which normally auto-tracks its
// container's width on its own — but relayout()ing height only freezes Plotly's internally-
// recorded width at whatever it was before the .is-fullscreen class was toggled, so the
// fullscreen chart rendered at half the viewport width instead of the full width. Always
// relayout width alongside height (opts.forceWidth), measured fresh off the DOM.
var _PT_CHART_OPTS = { forceWidth: true, getHeight: _ptChartHeight };

function toggleFullscreen(wrapperId) {
    ChartFullscreen.toggle(wrapperId, _PT_CHART_OPTS);
}

window.addEventListener("resize", function () {
    PT_CHART_WRAPPER_IDS.forEach(function (id) {
        ChartFullscreen.relayoutForCurrentState(id, _PT_CHART_OPTS);
    });
});

function _renderUnderwaterChart(underwater) {
    var el = document.getElementById("pt-chart-underwater");
    if (!underwater || !underwater.length) {
        el.innerHTML = "<p class='text-muted'>No drawdown data available.</p>";
        return;
    }
    var traces = [{
        x: underwater.map(function (d) { return d.date; }),
        y: underwater.map(function (d) { return d.value * 100; }),
        name: "Drawdown", fill: "tozeroy",
        fillcolor: "rgba(239,85,59,0.25)", line: { color: "rgba(239,85,59,0.9)", width: 1.5 },
        hovertemplate: "%{x}: %{y:.2f}%<extra></extra>",
    }];
    var layout = {
        title: { text: "Underwater / Drawdown", x: 0.5, xanchor: "center" },
        template: "plotly_dark", height: _ptChartHeight(),
        margin: { l: 50, r: 20, t: 50, b: 60 },
        legend: { orientation: "h", yanchor: "top", y: -0.15, xanchor: "center", x: 0.5 },
        paper_bgcolor: "#1e1e1e", plot_bgcolor: "#1e1e1e", font: { color: "#ccc" },
        yaxis: { title: "Drawdown %", ticksuffix: "%", automargin: true, gridcolor: "#333" },
        xaxis: { gridcolor: "#333" },
    };
    Plotly.react(el, traces, layout, { responsive: true, displaylogo: false });
}

function _renderCumulativeGrowthChart(cg) {
    var el = document.getElementById("pt-chart-cumulative-growth");
    if (!cg) {
        el.innerHTML = "<p class='text-muted'>No benchmark-comparison data available for this scope.</p>";
        return;
    }
    var traces = [
        { x: cg.dates, y: cg.portfolio, name: "Portfolio", line: { color: "#00ffcc", width: 2 } },
        { x: cg.dates, y: cg.benchmark, name: "Benchmark (SWDA.L — MSCI World)", line: { color: "#bb86fc", width: 1.5, dash: "dot" } },
    ];
    var layout = {
        title: { text: "Cumulative Growth vs. Benchmark (indexed to 100)", x: 0.5, xanchor: "center" },
        template: "plotly_dark", height: _ptChartHeight(), hovermode: "x unified",
        margin: { l: 50, r: 20, t: 50, b: 60 },
        legend: { orientation: "h", yanchor: "top", y: -0.15, xanchor: "center", x: 0.5 },
        paper_bgcolor: "#1e1e1e", plot_bgcolor: "#1e1e1e", font: { color: "#ccc" },
        yaxis: { title: "Growth of 100", automargin: true, gridcolor: "#333" },
        xaxis: { gridcolor: "#333" },
    };
    Plotly.react(el, traces, layout, { responsive: true, displaylogo: false });
}

function _renderMonthlyHeatmap(hm) {
    var el = document.getElementById("pt-chart-monthly-heatmap");
    if (!hm || !hm.years.length) {
        el.innerHTML = "<p class='text-muted'>No monthly data available yet.</p>";
        return;
    }
    var monthLabels = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
    var z = hm.matrix.map(function (row) {
        return row.map(function (v) { return v === null ? null : v * 100; });
    });
    var trace = {
        z: z, x: monthLabels, y: hm.years.map(String), type: "heatmap",
        colorscale: [[0, "#ef553b"], [0.5, "#1e1e1e"], [1, "#00cc96"]], zmid: 0,
        hovertemplate: "%{y} %{x}: %{z:.2f}%<extra></extra>",
        colorbar: { title: "%", tickformat: ".0f" },
    };
    var layout = {
        title: { text: "Monthly Returns Heatmap", x: 0.5, xanchor: "center" },
        template: "plotly_dark", height: _ptChartHeight(),
        margin: { l: 50, r: 20, t: 50, b: 60 },
        paper_bgcolor: "#1e1e1e", plot_bgcolor: "#1e1e1e", font: { color: "#ccc" },
        yaxis: { automargin: true },
    };
    Plotly.react(el, [trace], layout, { responsive: true, displaylogo: false });
}

function _renderHistogramChart(hist) {
    var el = document.getElementById("pt-chart-histogram");
    if (!hist || !hist.returns.length) {
        el.innerHTML = "<p class='text-muted'>No return data available.</p>";
        return;
    }
    var traces = [{
        x: hist.returns.map(function (v) { return v * 100; }),
        type: "histogram", name: "Daily Returns",
        marker: { color: "rgba(99,110,250,0.6)" }, nbinsx: 40,
    }];
    var meanPct = hist.mean * 100;
    var varPct = hist.var_95 * 100;
    var layout = {
        title: { text: "Daily Return Distribution", x: 0.5, xanchor: "center" },
        template: "plotly_dark", height: _ptChartHeight(),
        margin: { l: 50, r: 20, t: 50, b: 60 },
        legend: { orientation: "h", yanchor: "top", y: -0.15, xanchor: "center", x: 0.5 },
        paper_bgcolor: "#1e1e1e", plot_bgcolor: "#1e1e1e", font: { color: "#ccc" },
        yaxis: { title: "Frequency", automargin: true, gridcolor: "#333" },
        xaxis: { title: "Daily Return %", ticksuffix: "%", gridcolor: "#333" },
        shapes: [
            { type: "line", x0: meanPct, x1: meanPct, y0: 0, y1: 1, yref: "paper",
              line: { color: "#00ffcc", dash: "dash", width: 1.5 } },
            { type: "line", x0: varPct, x1: varPct, y0: 0, y1: 1, yref: "paper",
              line: { color: "#ef553b", dash: "dot", width: 1.5 } },
        ],
        annotations: [
            { x: meanPct, y: 1, yref: "paper", yanchor: "bottom", text: "Mean", showarrow: false, font: { color: "#00ffcc", size: 10 } },
            { x: varPct, y: 1, yref: "paper", yanchor: "bottom", text: "VaR 95%", showarrow: false, font: { color: "#ef553b", size: 10 } },
        ],
    };
    Plotly.react(el, traces, layout, { responsive: true, displaylogo: false });
}

function renderCharts(charts) {
    _renderUnderwaterChart(charts.underwater);
    _renderCumulativeGrowthChart(charts.cumulative_growth);
    _renderMonthlyHeatmap(charts.monthly_heatmap);
    _renderHistogramChart(charts.histogram);
}

function loadReport(accountId) {
    _hideWarning();
    fetch("/api/performance-analytics/report?account_id=" + encodeURIComponent(accountId))
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
            if (data.status !== "success") {
                renderPlaceholderCards();
                _clearCharts();
                _showWarning(data.message || "Failed to load the performance report.");
                return;
            }
            if (!data.metrics) {
                renderPlaceholderCards();
                _clearCharts();
                _showWarning(
                    (data.data_warnings && data.data_warnings.length)
                        ? data.data_warnings.join(" ")
                        : "Not enough cached return history for this scope yet."
                );
                return;
            }
            renderMetricCards(data.metrics);
            renderCharts(data.charts);
            if (data.data_warnings && data.data_warnings.length) {
                _showWarning(data.data_warnings.join(" "));
            }
        })
        .catch(function () {
            renderPlaceholderCards();
            _clearCharts();
            _showWarning("Request failed — check server logs.");
        });
}

function _setActiveTile(btn, accountId) {
    document.querySelectorAll(".mc-account-tile").forEach(function (t) {
        t.classList.remove("mc-account-tile--active");
    });
    btn.classList.add("mc-account-tile--active");
    selectedAccountId = accountId;
    loadReport(accountId);
}

function loadAccounts() {
    fetch("/api/performance-analytics/accounts")
        .then(function (resp) { return resp.json(); })
        .then(function (data) {
            if (data.status !== "success" || !data.accounts || !data.accounts.length) return;
            var bar = document.getElementById("pt-accounts-bar");
            var container = document.getElementById("pt-accounts-tiles");

            var tiles = data.accounts.map(function (acc) {
                return { id: acc.id, name: acc.name, value: acc.value };
            });
            tiles.push({ id: "all", name: "Global (All Accounts)", value: data.total, isTotal: true });

            tiles.forEach(function (tile) {
                var btn = document.createElement("button");
                btn.type = "button";
                btn.className = "mc-account-tile";
                btn.innerHTML =
                    '<div class="mc-account-tile-name">' + tile.name + "</div>"
                    + '<div class="mc-account-tile-value">' + _fmt_gbp(tile.value) + "</div>";
                btn.addEventListener("click", function () { _setActiveTile(btn, tile.id); });
                if (tile.isTotal) {
                    btn.classList.add("mc-account-tile--active");
                }
                container.appendChild(btn);
            });

            bar.classList.remove("d-none");
        })
        .catch(function () {});
}

function initPage() {
    renderPlaceholderCards();
    loadAccounts();
    loadReport(selectedAccountId);
}

document.addEventListener("DOMContentLoaded", initPage);
