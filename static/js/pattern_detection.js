// New pattern families only need an entry here for a human-readable label — the table,
// filter, and chart renderer below all work generically off whatever family/points/lines
// a result carries, with no further changes needed (see assets/pattern_detection.md).
const PATTERN_FAMILY_LABELS = {
    head_shoulders: 'Head & Shoulders',
    double_top_bottom: 'Double Top / Bottom',
};
const PATTERN_TYPE_LABELS = {
    regular: 'Head & Shoulders',
    inverse: 'Inverse Head & Shoulders',
    double_top: 'Double Top',
    double_bottom: 'Double Bottom',
};

function _pdFamilyLabel(family) {
    return PATTERN_FAMILY_LABELS[family] || family || '—';
}

function _pdPatternLabel(patternType) {
    return PATTERN_TYPE_LABELS[patternType] || patternType || '—';
}

function _pdPhaseStyle(phase) {
    if (phase === 'CONFIRMED') return { color: '#ff4d4d', label: 'CONFIRMED' };
    if (phase === 'FORMING') return { color: '#ffaa00', label: 'FORMING' };
    return { color: '#888', label: phase || '—' };
}

function _pdBoolCell(value) {
    if (value === null || value === undefined) return '<span style="color:#555;">—</span>';
    return value
        ? '<span style="color:#4caf50;font-weight:600;">✓</span>'
        : '<span style="color:#666;">✗</span>';
}

function _pdChartHeight() {
    return window.innerWidth < 768 ? 400 : 420;
}

function toggleFullscreen(wrapperId) {
    ChartFullscreen.toggle(wrapperId, { getHeight: _pdChartHeight });
}

let _pdPendingRow = null;
let _pdAllResults = [];
let _pdActiveFamily = '';

function _pdBuildFamilyFilter(results) {
    const select = document.getElementById('pd-family-filter');
    const families = Array.from(new Set(results.map(r => r.pattern_family))).sort();
    const current = select.value;
    select.innerHTML = '<option value="">All patterns</option>' + families.map(f =>
        `<option value="${escapeHtml(f)}">${escapeHtml(_pdFamilyLabel(f))}</option>`
    ).join('');
    if (families.includes(current)) select.value = current;
}

function _pdRenderPatternsTable() {
    const tbody = document.getElementById('pd-patterns-body');
    const empty = document.getElementById('pd-patterns-empty');
    const results = _pdActiveFamily
        ? _pdAllResults.filter(r => r.pattern_family === _pdActiveFamily)
        : _pdAllResults;
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
        const style = _pdPhaseStyle(row.phase);
        const keyLevel = (row.lines && row.lines.length) ? row.lines[0].price_to : null;
        tr.innerHTML = `
            <td><a href="/stock/${encodeURIComponent(row.ticker)}" style="color:#4da6ff;font-weight:600;text-decoration:none;">${escapeHtml(row.ticker)}</a></td>
            <td>${escapeHtml(_pdPatternLabel(row.pattern_type))}</td>
            <td><span style="color:${style.color};font-weight:700;">${style.label}</span></td>
            <td>${keyLevel != null ? keyLevel : '—'}</td>
            <td>${row.measured_target != null ? row.measured_target : '—'}</td>
            <td>${_pdBoolCell(row.volume_confirms)}</td>
            <td>${_pdBoolCell(row.rsi_divergence)}</td>
            <td>${row.pattern_r2 != null ? Number(row.pattern_r2).toFixed(2) : '—'}</td>
            <td style="color:#444;font-size:10px;">${escapeHtml(row.scan_ts || '')}</td>
        `;
        tr.addEventListener('click', () => _pdOpenDetail(row));
        tbody.appendChild(tr);
    });
}

function _pdOpenDetail(row) {
    document.getElementById('pd-detail-ticker').textContent = row.ticker;
    const pointsTxt = (row.points || []).map(p => `${p.label} ${p.price} (${p.date})`).join(' &rarr; ');
    const keyLevel = (row.lines && row.lines.length) ? row.lines[0].price_to : null;
    document.getElementById('pd-detail-body').innerHTML = `
        <p class="text-muted text-sm mb-1">${pointsTxt}</p>
        <p class="text-muted text-sm mb-0">
            Key level: ${keyLevel != null ? keyLevel : '—'} |
            Measured target: ${row.measured_target != null ? row.measured_target : '—'}
            ${row.breakout_date ? ` | Breakout: ${row.breakout_price} on ${row.breakout_date}` : ''}
        </p>
    `;

    const chartEl = document.getElementById('pd-detail-chart');
    if (chartEl.dataset.hasChart === '1' && window.Plotly) {
        Plotly.purge(chartEl);
        chartEl.dataset.hasChart = '';
    }
    chartEl.innerHTML = '<p class="text-muted p-3">Loading chart…</p>';

    _pdPendingRow = row;
    bootstrap.Modal.getOrCreateInstance(document.getElementById('pd-chart-modal')).show();
}

function _pdLoadChart(row) {
    const chartEl = document.getElementById('pd-detail-chart');
    fetch(`/api/pattern-detection/chart/${encodeURIComponent(row.ticker)}/${encodeURIComponent(row.pattern_family)}`)
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') throw new Error(data.message || 'Failed to load chart');
            _pdRenderChart(chartEl, data.series, data.pattern);
        })
        .catch(err => {
            chartEl.innerHTML = `<p class="text-danger p-3">${escapeHtml(err.message)}</p>`;
        });
}

function _pdRenderChart(el, series, pattern) {
    el.innerHTML = '';
    const priceTrace = {
        x: series.dates, y: series.close, type: 'scatter', mode: 'lines',
        name: 'Close', line: { color: '#4da6ff', width: 1.5 },
    };
    const points = pattern.points || [];
    const pointTrace = {
        x: points.map(p => p.date), y: points.map(p => p.price), type: 'scatter', mode: 'markers+text',
        name: 'Pattern points',
        text: points.map(p => p.label),
        textposition: 'top center', textfont: { size: 10, color: '#ffaa00' },
        marker: { color: '#ffaa00', size: 8 },
    };
    const lineTraces = (pattern.lines || []).map(line => ({
        x: [line.date_from, line.date_to], y: [line.price_from, line.price_to],
        type: 'scatter', mode: 'lines', name: line.label || 'Line',
        line: { color: '#888', width: 1, dash: line.dash ? 'dash' : 'solid' },
    }));
    const traces = [priceTrace, ...lineTraces, pointTrace];
    if (pattern.breakout_date) {
        traces.push({
            x: [pattern.breakout_date], y: [pattern.breakout_price], type: 'scatter', mode: 'markers+text',
            name: 'Breakout', text: ['Breakout'], textposition: 'bottom center',
            marker: { color: '#ff4d4d', size: 10, symbol: 'star' },
        });
    }
    const label = _pdPatternLabel(pattern.pattern_type);
    const layout = {
        title: { text: `${pattern.ticker} — ${label} (${pattern.phase})`, x: 0.5, xanchor: 'center' },
        template: 'plotly_dark', height: _pdChartHeight(),
        margin: { l: 50, r: 20, t: 50, b: 60 },
        legend: { orientation: 'h', yanchor: 'top', y: -0.15, xanchor: 'center', x: 0.5 },
        paper_bgcolor: '#111', plot_bgcolor: '#111', font: { color: '#ccc' },
        yaxis: { title: 'Price', automargin: true },
    };
    Plotly.newPlot(el, traces, layout, { responsive: true, displaylogo: false });
    el.dataset.hasChart = '1';
}

function _pdAccCell(acc, resolved) {
    if (!resolved) return '<span class="text-muted">Pending</span>';
    const cls = acc >= 60 ? 'text-green' : acc >= 50 ? 'text-warning' : 'text-red';
    return `<span class="${cls}">${acc}%</span>`;
}

function _pdRenderAccuracy(data) {
    const body = document.getElementById('pd-accuracy-body');
    const foot = document.getElementById('pd-accuracy-foot');
    const empty = document.getElementById('pd-accuracy-empty');
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
            <td>${escapeHtml(_pdPatternLabel(p.pattern_type))}</td>
            <td>${p.total || 0}</td>
            <td>${_pdAccCell(p.accuracy_14d, p.resolved_14d)}</td>
            <td class="text-muted">${p.resolved_14d || 0}</td>
            <td>${_pdAccCell(p.accuracy_30d, p.resolved_30d)}</td>
            <td class="text-muted">${p.resolved_30d || 0}</td>
        </tr>
    `).join('');

    foot.innerHTML = `
        <tr>
            <td><strong>Overall</strong></td>
            <td>${overall.total || 0}</td>
            <td>${_pdAccCell(overall.accuracy_14d, overall.resolved_14d)}</td>
            <td class="text-muted">${overall.resolved_14d || 0}</td>
            <td>${_pdAccCell(overall.accuracy_30d, overall.resolved_30d)}</td>
            <td class="text-muted">${overall.resolved_30d || 0}</td>
        </tr>
    `;
}

function _pdLoadResults() {
    fetch('/api/pattern-detection/results')
        .then(r => r.json())
        .then(data => {
            _pdAllResults = data.results || [];
            _pdBuildFamilyFilter(_pdAllResults);
            _pdRenderPatternsTable();
            const ts = document.getElementById('pd-last-scan');
            if (ts && _pdAllResults.length) ts.textContent = 'Last scan: ' + (_pdAllResults[0].scan_ts || '—');
        })
        .catch(() => {
            const empty = document.getElementById('pd-patterns-empty');
            if (empty) { empty.classList.remove('bubble-empty-hidden'); empty.textContent = 'Failed to load data.'; }
        });
}

let _pdAccuracyLoaded = false;
function _pdLoadAccuracy() {
    if (_pdAccuracyLoaded) return;
    _pdAccuracyLoaded = true;
    fetch('/api/pattern-detection/accuracy')
        .then(r => r.json())
        .then(data => _pdRenderAccuracy(data))
        .catch(() => {});
}

function _pdSwitchTab(name) {
    document.querySelectorAll('.bubble-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
    document.querySelectorAll('.bubble-tab-panel').forEach(p => p.classList.toggle('active', p.dataset.panel === name));
    if (name === 'accuracy') _pdLoadAccuracy();
}

function _pdTriggerScan() {
    const btn = document.getElementById('pd-run-btn');
    btn.disabled = true; btn.textContent = 'Scanning…';
    fetch('/api/pattern-detection/run', { method: 'POST' })
        .then(r => r.json())
        .then(() => {
            btn.disabled = false; btn.textContent = 'Run Scan';
            setTimeout(_pdLoadResults, 5000);
        })
        .catch(() => { btn.disabled = false; btn.textContent = 'Run Scan'; });
}

function _pdTriggerBackfill() {
    const btn = document.getElementById('pd-backfill-btn');
    btn.disabled = true; btn.textContent = 'Backfilling…';
    fetch('/api/pattern-detection/backfill', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            btn.disabled = false; btn.textContent = 'Backfill Historical Data';
            alert(data.message || 'Backfill triggered.');
        })
        .catch(() => { btn.disabled = false; btn.textContent = 'Backfill Historical Data'; });
}

document.addEventListener('DOMContentLoaded', function () {
    _pdLoadResults();

    document.querySelectorAll('.bubble-tab').forEach(t => {
        t.addEventListener('click', () => _pdSwitchTab(t.dataset.tab));
    });

    document.getElementById('pd-family-filter').addEventListener('change', (e) => {
        _pdActiveFamily = e.target.value;
        _pdRenderPatternsTable();
    });

    document.getElementById('pd-run-btn').addEventListener('click', _pdTriggerScan);
    document.getElementById('pd-backfill-btn').addEventListener('click', _pdTriggerBackfill);
    document.getElementById('pd-chart-modal').addEventListener('shown.bs.modal', () => {
        if (_pdPendingRow) {
            _pdLoadChart(_pdPendingRow);
            _pdPendingRow = null;
        }
    });
});
