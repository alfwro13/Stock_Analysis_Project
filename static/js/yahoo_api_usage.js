const _YAU_PALETTE = ['#3987e5', '#199e70', '#c98500', '#008300', '#9085e9', '#e66767', '#d55181', '#d95926'];
const _YAU_MAX_SERIES = 7;
const _YAU_ERROR_COLOR = '#d03b3b';

function _yauChartHeight() {
    return window.innerWidth < 768 ? 400 : 450;
}

function toggleFullscreen(wrapperId) {
    const wrapper = document.getElementById(wrapperId);
    if (!wrapper) return;
    const isFullscreen = wrapper.classList.contains('is-fullscreen');
    const btn = wrapper.querySelector('.fullscreen-btn');
    const plotEl = wrapper.querySelector('.js-plotly-plot');
    if (isFullscreen) {
        wrapper.classList.remove('is-fullscreen');
        if (btn) btn.innerHTML = '&#9638; Fullscreen';
        if (plotEl && window.Plotly) Plotly.relayout(plotEl, { height: _yauChartHeight() });
    } else {
        wrapper.classList.add('is-fullscreen');
        if (btn) btn.innerHTML = '&#10006; Exit Fullscreen';
        if (plotEl && window.Plotly) Plotly.relayout(plotEl, { height: window.innerHeight - 120 });
    }
    window.dispatchEvent(new Event('resize'));
}

function _yauCollapseSeries(jobLabels, series) {
    const totals = jobLabels.map(label => series[label].reduce((a, b) => a + b, 0));
    const order = jobLabels.map((label, i) => [label, totals[i]]).sort((a, b) => b[1] - a[1]);
    const kept = order.slice(0, _YAU_MAX_SERIES).map(o => o[0]);
    const overflow = order.slice(_YAU_MAX_SERIES).map(o => o[0]);
    const result = {};
    kept.forEach(label => { result[label] = series[label]; });
    if (overflow.length) {
        const bucketCount = series[jobLabels[0]].length;
        const other = new Array(bucketCount).fill(0);
        overflow.forEach(label => series[label].forEach((v, i) => { other[i] += v; }));
        result['Other'] = other;
    }
    return result;
}

function _yauRenderChart(data) {
    const el = document.getElementById('yau-chart');
    if (!data.buckets || !data.job_labels.length) {
        el.innerHTML = "<p class='text-muted'>No Yahoo Finance requests recorded for this date.</p>";
        return;
    }
    const series = _yauCollapseSeries(data.job_labels, data.series);
    const labels = Object.keys(series);
    const traces = labels.map((label, i) => ({
        x: data.buckets,
        y: series[label],
        name: label,
        type: 'bar',
        marker: { color: _YAU_PALETTE[i % _YAU_PALETTE.length] },
        hovertemplate: label + ': %{y}<extra></extra>',
    }));

    const totals = data.buckets.map((_, i) => labels.reduce((sum, label) => sum + series[label][i], 0));
    const errorX = [], errorY = [], errorText = [];
    data.buckets.forEach((b, i) => {
        if (data.errors_by_bucket[i] > 0) {
            errorX.push(b);
            errorY.push(totals[i]);
            errorText.push(data.errors_by_bucket[i] + ' rate-limit/error response(s)');
        }
    });
    if (errorX.length) {
        traces.push({
            x: errorX, y: errorY, text: errorText,
            type: 'scatter', mode: 'markers', name: 'Rate-limit / Errors',
            marker: { color: _YAU_ERROR_COLOR, size: 10, symbol: 'triangle-up' },
            hovertemplate: '%{text}<extra></extra>',
        });
    }

    const layout = {
        title: { text: 'Requests by 15-minute interval', x: 0.5, xanchor: 'center' },
        template: 'plotly_dark', height: _yauChartHeight(), barmode: 'stack',
        margin: { l: 20, r: 20, t: 50, b: 60 }, hovermode: 'x unified',
        legend: { orientation: 'h', yanchor: 'top', y: -0.15, xanchor: 'center', x: 0.5 },
        paper_bgcolor: '#111', plot_bgcolor: '#111', font: { color: '#ccc' },
        xaxis: { title: 'Local time', tickangle: -45 },
        yaxis: { title: 'API calls', showgrid: true, gridcolor: '#333333', automargin: true },
    };
    Plotly.react(el, traces, layout, { responsive: true, displaylogo: false });
    Plotly.Plots.resize(el);
}

function _yauLoad() {
    fetch(`/api/system/yahoo-api-stats/${window.YAHOO_API_USAGE_DATE}`)
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') throw new Error(data.message || 'Failed to load');
            _yauRenderChart(data);
        })
        .catch(() => {
            const el = document.getElementById('yau-chart');
            if (el) el.innerHTML = "<p class='text-danger'>Failed to load API usage detail.</p>";
        });
}

document.addEventListener('DOMContentLoaded', _yauLoad);
