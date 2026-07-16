let _psmScope = 'portfolio_watchlist';
let _psmPendingPair = null;

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
        tbody.innerHTML = '<tr><td colspan="7" class="text-center p-4 text-muted">No correlated pairs found yet. Click "Run Scan Now" or enable the scheduled job in Settings.</td></tr>';
        return;
    }
    tbody.innerHTML = '';
    results.forEach(row => {
        const tr = document.createElement('tr');
        tr.className = 'yahoo-stats-row-clickable';
        const z = Number(row.zscore) || 0;
        const companyA = row.company_name_a || '—';
        const companyB = row.company_name_b || '—';
        tr.innerHTML = `
            <td class="tm-th-left">${escapeHtml(row.ticker_a)} / ${escapeHtml(row.ticker_b)}</td>
            <td class="tm-th-left">${escapeHtml(companyA)} / ${escapeHtml(companyB)}</td>
            <td class="tm-th-center">${Number(row.correlation).toFixed(2)}</td>
            <td class="tm-th-center">${z >= 0 ? '+' : ''}${z.toFixed(2)}</td>
            <td>${escapeHtml(row.direction || '')}</td>
            <td class="tm-th-center">${escapeHtml(row.currency || '—')}</td>
            <td class="tm-th-right tm-th-dimmed">${escapeHtml(row.scan_ts || '')}</td>
        `;
        tr.addEventListener('click', () => _psmOpenChart(row.ticker_a, row.ticker_b));
        tbody.appendChild(tr);
    });
}

function _psmOpenChart(tickerA, tickerB) {
    _psmPendingPair = [tickerA, tickerB];
    document.getElementById('psm-chart-modal-title').textContent = `${tickerA} / ${tickerB}`;
    document.getElementById('psm-chart-stats').innerHTML = '';

    const chartEl = document.getElementById('psm-chart');
    if (chartEl.dataset.hasChart === '1' && window.Plotly) {
        Plotly.purge(chartEl);
        chartEl.dataset.hasChart = '';
    }
    chartEl.innerHTML = '<p class="text-muted p-3">Loading…</p>';

    bootstrap.Modal.getOrCreateInstance(document.getElementById('psm-chart-modal')).show();
}

function _psmLoadChart(tickerA, tickerB) {
    fetch(`/api/pairs-spread/chart/${encodeURIComponent(tickerA)}/${encodeURIComponent(tickerB)}`)
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') throw new Error(data.message || 'Failed to load chart');
            _psmRenderStats(data.chart);
            _psmRenderChart(data.chart);
        })
        .catch(err => {
            document.getElementById('psm-chart').innerHTML = `<p class="text-danger p-3">${escapeHtml(err.message)}</p>`;
        });
}

function _psmRenderStats(chart) {
    const el = document.getElementById('psm-chart-stats');
    const corr = chart.correlation != null ? chart.correlation.toFixed(2) : '—';
    const z = chart.zscore != null ? (chart.zscore >= 0 ? '+' : '') + chart.zscore.toFixed(2) : '—';
    el.innerHTML = `
        <div class="mb-2">
            <span class="badge text-bg-secondary me-2"><abbr title="Pearson correlation of daily returns over the trailing 252-day window. Ranges from -1 (always move oppositely) to +1 (always move together); 0 means no relationship. This pair only appears here because it cleared the configured threshold (default 0.7).">Correlation</abbr>: ${corr}</span>
            <span class="badge text-bg-secondary"><abbr title="How many standard deviations today's log-spread is from its own trailing-year average. Near 0 means the pair is trading at its normal historical relationship; a magnitude of 2 or more is a statistically unusual divergence — the kind of move this monitor alerts on.">Z-Score</abbr>: ${z}</span>
        </div>
        <p class="text-muted text-sm mb-0">Both lines are indexed to 100 at the start of the window, so you can see at a glance which ticker has done better. A correlated pair that pulls apart like this is expected to eventually converge back together — that's the mean-reversion signal this monitor is built to catch.</p>
    `;
}

function _psmRenderChart(chart) {
    const el = document.getElementById('psm-chart');
    const traces = [
        { x: chart.dates, y: chart.normalized_a, type: 'scatter', mode: 'lines', name: chart.ticker_a, line: { color: '#3987e5' } },
        { x: chart.dates, y: chart.normalized_b, type: 'scatter', mode: 'lines', name: chart.ticker_b, line: { color: '#c98500' } },
    ];
    const layout = {
        title: { text: `${chart.ticker_a} vs ${chart.ticker_b} — indexed to 100 at window start`, x: 0.5, xanchor: 'center' },
        template: 'plotly_dark', height: _psmChartHeight(),
        margin: { l: 50, r: 20, t: 50, b: 60 },
        legend: { orientation: 'h', yanchor: 'top', y: -0.15, xanchor: 'center', x: 0.5 },
        paper_bgcolor: '#111', plot_bgcolor: '#111', font: { color: '#ccc' },
        yaxis: { title: 'Indexed price', automargin: true },
    };
    Plotly.newPlot(el, traces, layout, { responsive: true, displaylogo: false });
    el.dataset.hasChart = '1';
}

function _psmLoadResults() {
    fetch(`/api/pairs-spread/results?scope=${encodeURIComponent(_psmScope)}`)
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') throw new Error(data.message || 'Failed to load');
            _psmRenderTable(data.results);
        })
        .catch(() => {
            document.getElementById('psm-tbody').innerHTML = '<tr><td colspan="7" class="text-center p-4 text-danger">Failed to load results.</td></tr>';
        });
}

function runPairsSpreadScanNow() {
    const btn = document.getElementById('psm-run-now-btn');
    const statusEl = document.getElementById('psm-run-status');
    const isUniverse = _psmScope === 'universe';
    const endpoint = isUniverse ? '/api/pairs-spread/run-universe' : '/api/pairs-spread/run';
    btn.disabled = true;
    statusEl.textContent = 'Scanning…';
    fetch(endpoint, { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            statusEl.textContent = data.message || '';
            setTimeout(_psmLoadResults, isUniverse ? 60000 : 8000);
        })
        .catch(() => { statusEl.textContent = 'Request failed.'; })
        .finally(() => { setTimeout(() => { btn.disabled = false; }, 3000); });
}

function _psmOnScopeChange() {
    _psmScope = document.querySelector('input[name="psm-scope"]:checked').value;
    const isUniverse = _psmScope === 'universe';
    document.getElementById('psm-universe-note').classList.toggle('d-none', !isUniverse);
    document.getElementById('psm-run-now-btn').innerHTML = isUniverse ? '&#9654; Run Universe Scan' : '&#9654; Run Scan Now';
    _psmLoadResults();
}

document.addEventListener('DOMContentLoaded', () => {
    _psmLoadResults();
    document.querySelectorAll('input[name="psm-scope"]').forEach(el => el.addEventListener('change', _psmOnScopeChange));
    document.getElementById('psm-chart-modal').addEventListener('shown.bs.modal', () => {
        if (_psmPendingPair) {
            _psmLoadChart(_psmPendingPair[0], _psmPendingPair[1]);
            _psmPendingPair = null;
        }
    });
});
