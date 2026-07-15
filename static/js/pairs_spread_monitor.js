function _psmChartHeight() {
    return window.innerWidth < 768 ? 400 : 450;
}

function toggleFullscreen(wrapperId) {
    ChartFullscreen.toggle(wrapperId, { getHeight: _psmChartHeight });
}

function _psmRenderTable(results) {
    const tbody = document.getElementById('psm-tbody');
    document.getElementById('psm-count').textContent = `(${results.length})`;
    if (!results.length) {
        tbody.innerHTML = '<tr><td colspan="6" class="text-center p-4 text-muted">No correlated pairs found yet. Click "Run Scan Now" or enable the scheduled job in Settings.</td></tr>';
        return;
    }
    tbody.innerHTML = '';
    results.forEach(row => {
        const tr = document.createElement('tr');
        tr.className = 'yahoo-stats-row-clickable';
        const z = Number(row.zscore) || 0;
        tr.innerHTML = `
            <td class="tm-th-left">${escapeHtml(row.ticker_a)} / ${escapeHtml(row.ticker_b)}</td>
            <td class="tm-th-center">${Number(row.correlation).toFixed(2)}</td>
            <td class="tm-th-center">${z >= 0 ? '+' : ''}${z.toFixed(2)}</td>
            <td>${escapeHtml(row.direction || '')}</td>
            <td class="tm-th-center">${escapeHtml(row.currency || '—')}</td>
            <td class="tm-th-right tm-th-dimmed">${escapeHtml(row.scan_ts || '')}</td>
        `;
        tr.addEventListener('click', () => _psmLoadChart(row.ticker_a, row.ticker_b));
        tbody.appendChild(tr);
    });
}

function _psmLoadChart(tickerA, tickerB) {
    const wrapper = document.getElementById('psm-chart-wrapper');
    wrapper.classList.remove('d-none');
    document.getElementById('psm-chart').innerHTML = '<p class="text-muted p-3">Loading…</p>';
    fetch(`/api/pairs-spread/chart/${encodeURIComponent(tickerA)}/${encodeURIComponent(tickerB)}`)
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') throw new Error(data.message || 'Failed to load chart');
            _psmRenderChart(data.chart);
        })
        .catch(err => {
            document.getElementById('psm-chart').innerHTML = `<p class="text-danger p-3">${escapeHtml(err.message)}</p>`;
        });
}

function _psmRenderChart(chart) {
    const el = document.getElementById('psm-chart');
    const traces = [
        { x: chart.dates, y: chart.log_spread, type: 'scatter', mode: 'lines', name: 'Log Spread', line: { color: '#3987e5' } },
        { x: chart.dates, y: chart.dates.map(() => chart.mean), type: 'scatter', mode: 'lines', name: 'Mean', line: { color: '#888', dash: 'dash' } },
        { x: chart.dates, y: chart.dates.map(() => chart.upper_2sd), type: 'scatter', mode: 'lines', name: '+2σ', line: { color: '#c98500', dash: 'dot' } },
        { x: chart.dates, y: chart.dates.map(() => chart.lower_2sd), type: 'scatter', mode: 'lines', name: '-2σ', line: { color: '#c98500', dash: 'dot' } },
    ];
    const layout = {
        title: { text: `${chart.ticker_a} / ${chart.ticker_b} — Log Spread`, x: 0.5, xanchor: 'center' },
        template: 'plotly_dark', height: _psmChartHeight(),
        margin: { l: 50, r: 20, t: 50, b: 60 },
        legend: { orientation: 'h', yanchor: 'top', y: -0.15, xanchor: 'center', x: 0.5 },
        paper_bgcolor: '#111', plot_bgcolor: '#111', font: { color: '#ccc' },
        yaxis: { title: 'Log Spread', automargin: true },
    };
    Plotly.react(el, traces, layout, { responsive: true, displaylogo: false });
    Plotly.Plots.resize(el);
}

function _psmLoadResults() {
    fetch('/api/pairs-spread/results')
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') throw new Error(data.message || 'Failed to load');
            _psmRenderTable(data.results);
        })
        .catch(() => {
            document.getElementById('psm-tbody').innerHTML = '<tr><td colspan="6" class="text-center p-4 text-danger">Failed to load results.</td></tr>';
        });
}

function runPairsSpreadScanNow() {
    const btn = document.getElementById('psm-run-now-btn');
    const statusEl = document.getElementById('psm-run-status');
    btn.disabled = true;
    statusEl.textContent = 'Scanning…';
    fetch('/api/pairs-spread/run', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            statusEl.textContent = data.message || '';
            setTimeout(_psmLoadResults, 8000);
        })
        .catch(() => { statusEl.textContent = 'Request failed.'; })
        .finally(() => { setTimeout(() => { btn.disabled = false; }, 3000); });
}

document.addEventListener('DOMContentLoaded', _psmLoadResults);
