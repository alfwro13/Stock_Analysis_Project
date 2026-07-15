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

// These 4 charts are embedded server-side (visuals_etf.py's fig.to_html()) rather than created
// via the Plotly JS API, so config.responsive never reacts to container size changes (resize,
// rotation, fullscreen) — width/height must be relayout'd explicitly, per AGENTS.md Rule 18.
const _ETF_CHART_WRAPPERS = [
    { outer: 'etf-overlay-outer-wrapper', inner: 'etf-overlay-wrapper' },
    { outer: 'etf-pred-outer-wrapper', inner: 'etf-pred-wrapper' },
    { outer: 'etf-contrib-outer-wrapper', inner: 'etf-contrib-wrapper' },
    { outer: 'etf-corr-outer-wrapper', inner: 'etf-corr-wrapper' },
];

function _etfDesktopHeight(plotEl) {
    // Captures the height Python originally rendered (fixed per chart, or dynamic-by-holdings-count
    // for the contributions chart) once, before any relayout overrides it — avoids duplicating that
    // formula here and drifting out of sync with visuals_etf.py.
    if (plotEl._etfDesktopHeight == null) {
        plotEl._etfDesktopHeight = (plotEl.layout && plotEl.layout.height) || 450;
    }
    return plotEl._etfDesktopHeight;
}

function _etfDesktopMarginBottom(plotEl) {
    if (plotEl._etfDesktopMarginB == null) {
        plotEl._etfDesktopMarginB = (plotEl.layout && plotEl.layout.margin && plotEl.layout.margin.b) || 60;
    }
    return plotEl._etfDesktopMarginB;
}

// Some charts here reserve a generous bottom margin (desktop-height-tuned, for a below-plot
// legend that can wrap several rows with up to ~20 constituents) — at the 400px mobile floor
// that fixed pixel margin becomes a much larger fraction of the chart, showing as dead space
// below the plotted data. Shrink it on the non-fullscreen mobile branch only; fullscreen and
// desktop still get the room the legend may need to wrap.
function _etfChartOpts() {
    return {
        forceWidth: true,
        getHeight: function (plotEl) {
            const isMobile = window.innerWidth < 768;
            return isMobile ? 400 : _etfDesktopHeight(plotEl);
        },
        getExtraProps: function (isFullscreen, plotEl) {
            const isMobile = window.innerWidth < 768;
            const desktopMarginB = _etfDesktopMarginBottom(plotEl);
            const marginB = (isMobile && !isFullscreen) ? Math.min(80, desktopMarginB) : desktopMarginB;
            return { 'margin.b': marginB };
        },
    };
}

function toggleFullscreen(outerWrapperId, innerWrapperId) {
    ChartFullscreen.toggle(outerWrapperId, Object.assign({ innerWrapperId }, _etfChartOpts()));
}

function _relayoutEtfChartSafe(outerWrapperId, innerWrapperId) {
    // One chart's relayout throwing (e.g. Plotly not yet finished initialising that div) must
    // not abort the forEach and silently skip relayout for the remaining charts on the page.
    try {
        ChartFullscreen.relayoutForCurrentState(outerWrapperId, Object.assign({ innerWrapperId }, _etfChartOpts()));
    } catch (e) {
        console.error('ETF chart relayout failed for', innerWrapperId, e);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    _ETF_CHART_WRAPPERS.forEach(w => _relayoutEtfChartSafe(w.outer, w.inner));
    window.addEventListener('resize', () => {
        _ETF_CHART_WRAPPERS.forEach(w => _relayoutEtfChartSafe(w.outer, w.inner));
    });
});

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
            status.innerHTML = `<span class="msg-error">${escapeHtml(data.message || 'Failed')}</span>`;
            btn.disabled = false;
        }
    } catch (err) {
        status.innerHTML = `<span class="msg-error">${err.message}</span>`;
        btn.disabled = false;
    }
}

loadAccuracy();
