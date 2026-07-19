const PATTERN_LABELS = { regular: 'Head & Shoulders', inverse: 'Inverse Head & Shoulders' };

function _hsPhaseStyle(phase) {
    if (phase === 'CONFIRMED') return { color: '#ff4d4d', label: 'CONFIRMED' };
    if (phase === 'FORMING') return { color: '#ffaa00', label: 'FORMING' };
    return { color: '#888', label: phase || '—' };
}

function _hsBoolCell(value) {
    if (value === null || value === undefined) return '<span style="color:#555;">—</span>';
    return value
        ? '<span style="color:#4caf50;font-weight:600;">✓</span>'
        : '<span style="color:#666;">✗</span>';
}

function _hsChartHeight() {
    return window.innerWidth < 768 ? 400 : 420;
}

function toggleFullscreen(wrapperId) {
    ChartFullscreen.toggle(wrapperId, { getHeight: _hsChartHeight });
}

let _hsPendingTicker = null;

function _hsRenderPatternsTable(results) {
    const tbody = document.getElementById('hs-patterns-body');
    const empty = document.getElementById('hs-patterns-empty');
    if (!results.length) {
        tbody.innerHTML = '';
        empty.classList.remove('bubble-empty-hidden');
        return;
    }
    empty.classList.add('bubble-empty-hidden');
    tbody.innerHTML = '';
    results.forEach(row => {
        const tr = document.createElement('tr');
        tr.className = 'clickable';
        const style = _hsPhaseStyle(row.phase);
        const patternLabel = PATTERN_LABELS[row.pattern_type] || row.pattern_type || '—';
        tr.innerHTML = `
            <td><a href="/stock/${encodeURIComponent(row.ticker)}" style="color:#4da6ff;font-weight:600;text-decoration:none;">${escapeHtml(row.ticker)}</a></td>
            <td>${escapeHtml(patternLabel)}</td>
            <td><span style="color:${style.color};font-weight:700;">${style.label}</span></td>
            <td>${row.neck_value != null ? row.neck_value : '—'}</td>
            <td>${row.measured_target != null ? row.measured_target : '—'}</td>
            <td>${_hsBoolCell(row.volume_confirms)}</td>
            <td>${_hsBoolCell(row.rsi_divergence)}</td>
            <td>${row.pattern_r2 != null ? Number(row.pattern_r2).toFixed(2) : '—'}</td>
            <td style="color:#444;font-size:10px;">${escapeHtml(row.scan_ts || '')}</td>
        `;
        tr.addEventListener('click', () => _hsOpenDetail(row));
        tbody.appendChild(tr);
    });
}

function _hsOpenDetail(row) {
    document.getElementById('hs-detail-ticker').textContent = row.ticker;
    document.getElementById('hs-detail-body').innerHTML = `
        <p class="text-muted text-sm mb-1">
            Left shoulder ${row.l_shoulder_price} (${row.l_shoulder_date}) &rarr;
            Head ${row.head_price} (${row.head_date}) &rarr;
            Right shoulder ${row.r_shoulder_price} (${row.r_shoulder_date})
        </p>
        <p class="text-muted text-sm mb-0">
            Neckline value: ${row.neck_value != null ? row.neck_value : '—'} |
            Measured target: ${row.measured_target != null ? row.measured_target : '—'}
            ${row.breakout_date ? ` | Breakout: ${row.breakout_price} on ${row.breakout_date}` : ''}
        </p>
    `;

    const chartEl = document.getElementById('hs-detail-chart');
    if (chartEl.dataset.hasChart === '1' && window.Plotly) {
        Plotly.purge(chartEl);
        chartEl.dataset.hasChart = '';
    }
    chartEl.innerHTML = '<p class="text-muted p-3">Loading chart…</p>';

    _hsPendingTicker = row.ticker;
    bootstrap.Modal.getOrCreateInstance(document.getElementById('hs-chart-modal')).show();
}

function _hsLoadChart(ticker) {
    const chartEl = document.getElementById('hs-detail-chart');
    fetch(`/api/head-shoulders/chart/${encodeURIComponent(ticker)}`)
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') throw new Error(data.message || 'Failed to load chart');
            _hsRenderChart(chartEl, data.series, data.pattern);
        })
        .catch(err => {
            chartEl.innerHTML = `<p class="text-danger p-3">${escapeHtml(err.message)}</p>`;
        });
}

function _hsRenderChart(el, series, pattern) {
    el.innerHTML = '';
    const priceTrace = {
        x: series.dates, y: series.close, type: 'scatter', mode: 'lines',
        name: 'Close', line: { color: '#4da6ff', width: 1.5 },
    };
    const pivotDates = [pattern.l_shoulder_date, pattern.l_armpit_date, pattern.head_date, pattern.r_armpit_date, pattern.r_shoulder_date];
    const pivotPrices = [pattern.l_shoulder_price, pattern.l_armpit_price, pattern.head_price, pattern.r_armpit_price, pattern.r_shoulder_price];
    const pivotTrace = {
        x: pivotDates, y: pivotPrices, type: 'scatter', mode: 'markers+text',
        name: 'Pattern pivots',
        text: ['L Shoulder', 'L Armpit', 'Head', 'R Armpit', 'R Shoulder'],
        textposition: 'top center', textfont: { size: 10, color: '#ffaa00' },
        marker: { color: '#ffaa00', size: 8 },
    };
    const neckTrace = {
        x: [pattern.l_armpit_date, pattern.r_armpit_date], y: [pattern.l_armpit_price, pattern.r_armpit_price],
        type: 'scatter', mode: 'lines', name: 'Neckline', line: { color: '#888', width: 1, dash: 'dash' },
    };
    const traces = [priceTrace, neckTrace, pivotTrace];
    if (pattern.breakout_date) {
        traces.push({
            x: [pattern.breakout_date], y: [pattern.breakout_price], type: 'scatter', mode: 'markers+text',
            name: 'Breakout', text: ['Breakout'], textposition: 'bottom center',
            marker: { color: '#ff4d4d', size: 10, symbol: 'star' },
        });
    }
    const label = PATTERN_LABELS[pattern.pattern_type] || pattern.pattern_type || '';
    const layout = {
        title: { text: `${pattern.ticker} — ${label} (${pattern.phase})`, x: 0.5, xanchor: 'center' },
        template: 'plotly_dark', height: _hsChartHeight(),
        margin: { l: 50, r: 20, t: 50, b: 60 },
        legend: { orientation: 'h', yanchor: 'top', y: -0.15, xanchor: 'center', x: 0.5 },
        paper_bgcolor: '#111', plot_bgcolor: '#111', font: { color: '#ccc' },
        yaxis: { title: 'Price', automargin: true },
    };
    Plotly.newPlot(el, traces, layout, { responsive: true, displaylogo: false });
    el.dataset.hasChart = '1';
}

function _hsAccCell(acc, resolved) {
    if (!resolved) return '<span class="text-muted">Pending</span>';
    const cls = acc >= 60 ? 'text-green' : acc >= 50 ? 'text-warning' : 'text-red';
    return `<span class="${cls}">${acc}%</span>`;
}

function _hsRenderAccuracy(data) {
    const body = document.getElementById('hs-accuracy-body');
    const foot = document.getElementById('hs-accuracy-foot');
    const empty = document.getElementById('hs-accuracy-empty');
    const patterns = data.patterns || [];
    const overall = data.overall || {};

    if (!patterns.length) {
        body.innerHTML = '';
        foot.innerHTML = '';
        empty.classList.remove('bubble-empty-hidden');
        return;
    }
    empty.classList.add('bubble-empty-hidden');

    body.innerHTML = patterns.map(p => `
        <tr>
            <td>${escapeHtml(PATTERN_LABELS[p.pattern_type] || p.pattern_type)}</td>
            <td>${p.total || 0}</td>
            <td>${_hsAccCell(p.accuracy_14d, p.resolved_14d)}</td>
            <td class="text-muted">${p.resolved_14d || 0}</td>
            <td>${_hsAccCell(p.accuracy_30d, p.resolved_30d)}</td>
            <td class="text-muted">${p.resolved_30d || 0}</td>
        </tr>
    `).join('');

    foot.innerHTML = `
        <tr>
            <td><strong>Overall</strong></td>
            <td>${overall.total || 0}</td>
            <td>${_hsAccCell(overall.accuracy_14d, overall.resolved_14d)}</td>
            <td class="text-muted">${overall.resolved_14d || 0}</td>
            <td>${_hsAccCell(overall.accuracy_30d, overall.resolved_30d)}</td>
            <td class="text-muted">${overall.resolved_30d || 0}</td>
        </tr>
    `;
}

function _hsLoadResults() {
    fetch('/api/head-shoulders/results')
        .then(r => r.json())
        .then(data => {
            const results = data.results || [];
            _hsRenderPatternsTable(results);
            const ts = document.getElementById('hs-last-scan');
            if (ts && results.length) ts.textContent = 'Last scan: ' + (results[0].scan_ts || '—');
        })
        .catch(() => {
            const empty = document.getElementById('hs-patterns-empty');
            if (empty) { empty.classList.remove('bubble-empty-hidden'); empty.textContent = 'Failed to load data.'; }
        });
}

let _hsAccuracyLoaded = false;
function _hsLoadAccuracy() {
    if (_hsAccuracyLoaded) return;
    _hsAccuracyLoaded = true;
    fetch('/api/head-shoulders/accuracy')
        .then(r => r.json())
        .then(data => _hsRenderAccuracy(data))
        .catch(() => {});
}

function _hsSwitchTab(name) {
    document.querySelectorAll('.bubble-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
    document.querySelectorAll('.bubble-tab-panel').forEach(p => p.classList.toggle('active', p.dataset.panel === name));
    if (name === 'accuracy') _hsLoadAccuracy();
}

function _hsTriggerScan() {
    const btn = document.getElementById('hs-run-btn');
    btn.disabled = true; btn.textContent = 'Scanning…';
    fetch('/api/head-shoulders/run', { method: 'POST' })
        .then(r => r.json())
        .then(() => {
            btn.disabled = false; btn.textContent = 'Run Scan';
            setTimeout(_hsLoadResults, 5000);
        })
        .catch(() => { btn.disabled = false; btn.textContent = 'Run Scan'; });
}

function _hsTriggerBackfill() {
    const btn = document.getElementById('hs-backfill-btn');
    btn.disabled = true; btn.textContent = 'Backfilling…';
    fetch('/api/head-shoulders/backfill', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            btn.disabled = false; btn.textContent = 'Backfill Historical Data';
            alert(data.message || 'Backfill triggered.');
        })
        .catch(() => { btn.disabled = false; btn.textContent = 'Backfill Historical Data'; });
}

document.addEventListener('DOMContentLoaded', function () {
    _hsLoadResults();

    document.querySelectorAll('.bubble-tab').forEach(t => {
        t.addEventListener('click', () => _hsSwitchTab(t.dataset.tab));
    });

    document.getElementById('hs-run-btn').addEventListener('click', _hsTriggerScan);
    document.getElementById('hs-backfill-btn').addEventListener('click', _hsTriggerBackfill);
    document.getElementById('hs-chart-modal').addEventListener('shown.bs.modal', () => {
        if (_hsPendingTicker) {
            _hsLoadChart(_hsPendingTicker);
            _hsPendingTicker = null;
        }
    });
});
