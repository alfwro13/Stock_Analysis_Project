var _WC_SECTOR_COLORS = ['#3987e5', '#199e70', '#c98500', '#008300', '#9085e9', '#e66767', '#d55181', '#d95926'];
var _WC_OTHER_COLOR = '#898781';
var _WC_SIGNAL_COLORS = {
    'STRONG BUY': '#00ff00',
    'BULLISH / HOLD': '#85e085',
    'NEUTRAL': '#cccccc',
    'BEARISH / CAUTION': '#ff4d4d'
};

function _wcGetVisibleRows() {
    var rows = [];
    if (window._watchlistTable) {
        window._watchlistTable.rows({ filter: 'applied' }).nodes().each(function (node) {
            if (!node.classList.contains('child')) rows.push(node);
        });
    } else {
        document.querySelectorAll('#dataTable tbody tr:not(.child)').forEach(function (r) { rows.push(r); });
    }
    return rows;
}

function _wcRenderSectorDonut() {
    var el = document.getElementById('sector-donut-chart');
    if (!el) return;

    var counts = {};
    _wcGetVisibleRows().forEach(function (r) {
        var sector = r.dataset.sector || 'Unclassified';
        counts[sector] = (counts[sector] || 0) + 1;
    });
    var entries = Object.keys(counts).map(function (k) { return [k, counts[k]]; });
    entries.sort(function (a, b) { return b[1] - a[1]; });

    if (!entries.length) {
        el.innerHTML = '<p class="text-muted small mb-0">No data to display.</p>';
        return;
    }

    var top = entries.slice(0, _WC_SECTOR_COLORS.length);
    var restCount = entries.slice(_WC_SECTOR_COLORS.length).reduce(function (s, e) { return s + e[1]; }, 0);
    var labels = top.map(function (e) { return e[0]; });
    var values = top.map(function (e) { return e[1]; });
    var colors = top.map(function (_, i) { return _WC_SECTOR_COLORS[i]; });
    if (restCount > 0) {
        labels.push('Other');
        values.push(restCount);
        colors.push(_WC_OTHER_COLOR);
    }

    Plotly.react(el, [{
        type: 'pie',
        hole: 0.55,
        labels: labels,
        values: values,
        marker: { colors: colors, line: { color: '#1a1a19', width: 2 } },
        textinfo: 'label+percent',
        textposition: 'outside',
        automargin: true,
        hovertemplate: '%{label}: %{value} tickers (%{percent})<extra></extra>'
    }], {
        title: { text: 'Sector Allocation', x: 0.5, xanchor: 'center', font: { color: '#fff' } },
        showlegend: false,
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        margin: { t: 40, b: 20, l: 10, r: 10 },
        height: 380
    }, { responsive: true, displayModeBar: false });
}

function _wcRenderScoreRsiScatter() {
    var el = document.getElementById('score-rsi-scatter-chart');
    if (!el) return;

    var bySignal = {};
    _wcGetVisibleRows().forEach(function (r) {
        var score = parseFloat(r.dataset.compositeScore);
        var rsi = parseFloat(r.dataset.rsi);
        if (!isFinite(score) || !isFinite(rsi)) return;
        var signal = r.dataset.signal || 'NEUTRAL';
        if (!bySignal[signal]) bySignal[signal] = { x: [], y: [], ticker: [] };
        bySignal[signal].x.push(score);
        bySignal[signal].y.push(rsi);
        bySignal[signal].ticker.push(r.dataset.ticker);
    });

    var traces = Object.keys(bySignal).map(function (signal) {
        var d = bySignal[signal];
        return {
            type: 'scatter', mode: 'markers', name: signal,
            x: d.x, y: d.y, text: d.ticker,
            marker: { color: _WC_SIGNAL_COLORS[signal] || '#4da6ff', size: 9, line: { color: '#1a1a19', width: 1 } },
            hovertemplate: '%{text}<br>Score: %{x}<br>RSI: %{y}<extra></extra>'
        };
    });

    if (!traces.length) {
        el.innerHTML = '<p class="text-muted small mb-0">No data to display.</p>';
        return;
    }

    Plotly.react(el, traces, {
        title: { text: 'Composite Score vs RSI', x: 0.5, xanchor: 'center', font: { color: '#fff' } },
        xaxis: { title: 'Composite Score', range: [0, 100], gridcolor: '#333', color: '#ccc' },
        yaxis: { title: 'RSI (14)', range: [0, 100], gridcolor: '#333', color: '#ccc', automargin: true },
        shapes: [
            { type: 'line', x0: 0, x1: 100, y0: 30, y1: 30, line: { color: '#555', dash: 'dot', width: 1 } },
            { type: 'line', x0: 0, x1: 100, y0: 70, y1: 70, line: { color: '#555', dash: 'dot', width: 1 } }
        ],
        legend: { orientation: 'h', yanchor: 'top', y: -0.2, xanchor: 'center', x: 0.5, font: { color: '#ccc' } },
        paper_bgcolor: 'rgba(0,0,0,0)',
        plot_bgcolor: 'rgba(0,0,0,0)',
        margin: { t: 40, b: 80, l: 50, r: 10 },
        height: 380
    }, { responsive: true, displayModeBar: false });
}

function renderWatchlistCharts() {
    _wcRenderSectorDonut();
    _wcRenderScoreRsiScatter();
}

$(document).ready(function () {
    renderWatchlistCharts();
    $('#dataTable').on('draw.dt', renderWatchlistCharts);
});
