function _edpPct(value) {
    return value != null ? `${Number(value).toFixed(1)}%` : '—';
}

function _edpChartHeight() {
    return window.innerWidth < 768 ? 400 : 450;
}

function toggleFullscreen(wrapperId) {
    ChartFullscreen.toggle(wrapperId, { getHeight: _edpChartHeight });
}

function _edpRenderSummary(overall) {
    document.getElementById('edp-summary-total').textContent = overall.total ?? 0;
    document.getElementById('edp-summary-1d').textContent = overall.resolved_1d ? _edpPct(overall.accuracy_1d) : 'Pending';
    document.getElementById('edp-summary-5d').textContent = overall.resolved_5d ? _edpPct(overall.accuracy_5d) : 'Pending';
    document.getElementById('edp-summary-20d').textContent = overall.resolved_20d ? _edpPct(overall.accuracy_20d) : 'Pending';
}

function _edpRenderTable(rows) {
    const tbody = document.getElementById('edp-tbody');
    document.getElementById('edp-count').textContent = `(${rows.length})`;
    if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="9" class="text-center p-4 text-muted">No predictions logged yet — the daily Overnight Quant Scan populates this data for tickers with earnings in the next few days.</td></tr>';
        return;
    }
    tbody.innerHTML = '';
    rows.forEach(row => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td class="tm-th-left">${escapeHtml(row.ticker)}</td>
            <td class="tm-th-left">${escapeHtml(row.company_name || '—')}</td>
            <td class="tm-th-right">${row.total ?? 0}</td>
            <td class="tm-th-right">${row.resolved_1d ?? 0}</td>
            <td class="tm-th-right">${row.resolved_1d ? _edpPct(row.accuracy_1d) : 'Pending'}</td>
            <td class="tm-th-right">${row.resolved_5d ?? 0}</td>
            <td class="tm-th-right">${row.resolved_5d ? _edpPct(row.accuracy_5d) : 'Pending'}</td>
            <td class="tm-th-right">${row.resolved_20d ?? 0}</td>
            <td class="tm-th-right">${row.resolved_20d ? _edpPct(row.accuracy_20d) : 'Pending'}</td>
        `;
        tbody.appendChild(tr);
    });
}

function _edpRenderChart(data) {
    const el = document.getElementById('edp-chart');
    if (!data.offsets || !data.offsets.length) {
        el.innerHTML = "<p class='text-muted p-3'>No resolved earnings events yet to chart an average price path.</p>";
        return;
    }
    const layout = {
        title: { text: 'Average Post-Earnings Price Path', x: 0.5, xanchor: 'center' },
        template: 'plotly_dark', height: _edpChartHeight(),
        margin: { l: 50, r: 20, t: 50, b: 60 },
        legend: { orientation: 'h', yanchor: 'top', y: -0.15, xanchor: 'center', x: 0.5 },
        xaxis: { title: 'Trading days from earnings (0 = pre-earnings close)' },
        yaxis: { title: 'Avg % change from pre-earnings close', automargin: true },
    };
    Plotly.react(el,
        [{ x: data.offsets, y: data.avg_pct, mode: 'lines+markers', name: 'Avg cumulative move',
           hovertemplate: '%{x} trading days: %{y:.2f}%<extra></extra>' }],
        layout, { responsive: true, displaylogo: false });
    Plotly.Plots.resize(el);
}

function _edpLoad() {
    fetch('/api/earnings-volatility/accuracy')
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') throw new Error(data.message || 'Failed to load');
            _edpRenderSummary(data.overall || {});
            _edpRenderTable(data.by_ticker || []);
        })
        .catch(() => {
            document.getElementById('edp-tbody').innerHTML = '<tr><td colspan="9" class="text-center p-4 text-danger">Failed to load accuracy data.</td></tr>';
        });

    fetch('/api/earnings-volatility/drift-path')
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') throw new Error(data.message || 'Failed to load');
            _edpRenderChart(data);
        })
        .catch(() => {
            const el = document.getElementById('edp-chart');
            if (el) el.innerHTML = "<p class='text-danger p-3'>Failed to load drift-path chart.</p>";
        });
}

document.addEventListener('DOMContentLoaded', _edpLoad);
