const _configId = window.ETF_CONFIG_ID;
const _currency = window.ETF_CURRENCY;
let _accuracyLoaded = false;
let _accuracyVisible = true;

function toggleAccuracy() {
    const body = document.getElementById('accuracy-body');
    const icon = document.getElementById('accuracy-toggle-icon');
    _accuracyVisible = !_accuracyVisible;
    if (_accuracyVisible) {
        body.classList.remove('d-none');
        icon.innerHTML = '&#9660;';
    } else {
        body.classList.add('d-none');
        icon.innerHTML = '&#9654;';
    }
    if (_accuracyVisible && !_accuracyLoaded) loadAccuracy();
}

function loadAccuracy() {
    fetch('/api/etf-predictors/' + _configId + '/predictions')
        .then(r => r.json())
        .then(data => {
            _accuracyLoaded = true;
            const no = data.next_open || {};
            const ui = data.us_open_impact || {};
            renderSummaryTiles(no.summary || {}, 'accuracy-summary-next');
            renderSummaryTiles(ui.summary || {}, 'accuracy-summary-impact');
            renderAccuracyTable(no.rows || [], 'accuracy-tbody-next');
            renderAccuracyTable(ui.rows || [], 'accuracy-tbody-impact');
        })
        .catch(() => {
            ['accuracy-tbody-next', 'accuracy-tbody-impact'].forEach(id => {
                document.getElementById(id).innerHTML =
                    '<tr><td colspan="8" style="text-align:center;color:#f88;">Failed to load accuracy data.</td></tr>';
            });
        });
}

function _variantTile(label, v) {
    v = v || {};
    const dir = v.direction_accuracy_pct != null ? v.direction_accuracy_pct + '%' : '—';
    const mae = v.mae != null ? v.mae.toFixed(4) : '—';
    return { label: label, value: dir, sub: 'MAE ' + mae + ' · n=' + (v.resolved_count || 0) };
}

function renderSummaryTiles(s, containerId) {
    const fmt = (v, suffix='') => v !== null && v !== undefined ? v + suffix : '—';
    const maeVal = s.mae != null ? s.mae.toFixed(4) + ' ' + _currency : '—';
    const tiles = [
        { label: 'Direction Accuracy', value: fmt(s.direction_accuracy_pct, '%'), sub: 'All time' },
        { label: 'Last 30 Days', value: fmt(s.last_30_direction_pct, '%'), sub: 'Direction' },
        { label: 'Last 10 Days', value: fmt(s.last_10_direction_pct, '%'), sub: 'Direction' },
        { label: 'Mean Abs Error', value: maeVal, sub: 'MAE' },
        { label: 'Mean Abs % Error', value: fmt(s.mape_pct, '%'), sub: 'MAPE' },
        { label: 'Predictions', value: (s.resolved_count || 0) + ' / ' + (s.total_predictions || 0), sub: 'Resolved / Total' },
        _variantTile('Bias-Corrected Dir.', s.bias_corrected),
        _variantTile('Blend Dir.', s.blended),
    ];
    document.getElementById(containerId).innerHTML = tiles.map(t =>
        `<div class="xray-metric-card">
            <div class="xray-metric-label">${t.label}</div>
            <div class="xray-metric-value">${t.value}</div>
            <div class="xray-metric-label xray-metric-sublabel">${t.sub}</div>
         </div>`
    ).join('');
}

function renderAccuracyTable(rows, tbodyId) {
    if (!rows.length) {
        document.getElementById(tbodyId).innerHTML =
            '<tr><td colspan="10" style="text-align:center;color:#888;">No predictions recorded yet.</td></tr>';
        return;
    }
    document.getElementById(tbodyId).innerHTML = rows.map(r => {
        const resolved = r.actual_open !== null;
        const correct = r.direction_correct;
        let rowColor = '';
        if (!resolved) rowColor = 'color:#888;';
        else if (correct === 1) rowColor = 'color:#4fd1a5;';
        else if (correct === 0) rowColor = 'color:#f87171;';
        const dirIcon = !resolved ? '&#9203;' : correct === 1 ? '&#10003;' : '&#10007;';
        return `<tr style="${rowColor}border-bottom:1px solid #2a2a2a;">
            <td style="padding:4px 8px;">${r.target_date}</td>
            <td style="text-align:right;padding:4px 8px;">${r.predicted_price != null ? r.predicted_price.toFixed(4) : '—'}</td>
            <td style="text-align:right;padding:4px 8px;">${r.bias_corrected_price != null ? r.bias_corrected_price.toFixed(4) : '—'}</td>
            <td style="text-align:right;padding:4px 8px;">${r.blended_price != null ? r.blended_price.toFixed(4) : '—'}</td>
            <td style="text-align:right;padding:4px 8px;">${r.actual_open != null ? r.actual_open.toFixed(4) : '—'}</td>
            <td style="text-align:right;padding:4px 8px;">${r.absolute_error != null ? r.absolute_error.toFixed(4) : '—'}</td>
            <td style="text-align:right;padding:4px 8px;">${r.predicted_change_pct != null ? (r.predicted_change_pct >= 0 ? '+' : '') + r.predicted_change_pct.toFixed(2) + '%' : '—'}</td>
            <td style="text-align:right;padding:4px 8px;">${r.actual_change_pct != null ? (r.actual_change_pct >= 0 ? '+' : '') + r.actual_change_pct.toFixed(2) + '%' : '—'}</td>
            <td style="text-align:center;padding:4px 8px;">${dirIcon}</td>
            <td style="padding:4px 8px;">${r.signal_source || '—'}</td>
        </tr>`;
    }).join('');
}

function toggleFullscreen(wrapperId) {
    const el = document.getElementById(wrapperId);
    if (!el) return;
    if (!document.fullscreenElement) {
        el.requestFullscreen().catch(() => {});
    } else {
        document.exitFullscreen();
    }
}

async function runNow(e) {
    const btn = e.target;
    const status = document.getElementById('run-status');
    btn.disabled = true;
    status.innerHTML = '<span class="msg-info">Running prediction&hellip;</span>';
    try {
        const r = await fetch('/api/etf-predictors/' + _configId + '/run', { method: 'POST' });
        const data = await r.json();
        if (data.status === 'success') {
            status.innerHTML = '<span class="msg-success">Prediction initiated &mdash; refresh in a moment.</span>';
        } else {
            status.innerHTML = `<span class="msg-error">${data.message || 'Failed'}</span>`;
            btn.disabled = false;
        }
    } catch (err) {
        status.innerHTML = `<span class="msg-error">${err.message}</span>`;
        btn.disabled = false;
    }
}

loadAccuracy();
