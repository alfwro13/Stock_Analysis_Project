// A new pattern family needs nothing added here to render correctly: its checkboxes are
// built from whichever patterns the API returns for this ticker, grouped purely by the
// server-resolved `direction` field ("up"/"down"), and its color falls back to a
// deterministic palette pick if PD_PATTERN_COLORS has no entry for its pattern_type yet.
const PD_PATTERN_TYPE_LABELS = {
    regular: 'Head & Shoulders',
    inverse: 'Inverse Head & Shoulders',
    double_top: 'Double Top',
    double_bottom: 'Double Bottom',
    bull_flag: 'Bull Flag',
    bear_flag: 'Bear Flag',
    ascending: 'Ascending Triangle',
    descending: 'Descending Triangle',
};
const PD_PATTERN_COLORS = {
    regular: '#ff4d4d',
    double_top: '#ff9900',
    inverse: '#4caf50',
    double_bottom: '#22b8cf',
    bull_flag: '#2ecc71',
    bear_flag: '#e74c3c',
    ascending: '#f1c40f',
    descending: '#9b59b6',
};
const PD_FALLBACK_PALETTE = ['#ff4d4d', '#ff9900', '#4caf50', '#22b8cf', '#9b59b6', '#e91e8c', '#3498db', '#f1c40f'];

let _pdSeries = null;
let _pdPatterns = [];
let _pdEnabled = new Set();

function _pdPatternKey(p) {
    return `${p.pattern_family}:${p.pattern_type}`;
}

function _pdPatternLabel(p) {
    const base = PD_PATTERN_TYPE_LABELS[p.pattern_type] || p.pattern_type;
    return `${base} (${p.phase === 'CONFIRMED' ? 'Confirmed' : 'Forming'})`;
}

function _pdColorForType(patternType) {
    if (PD_PATTERN_COLORS[patternType]) return PD_PATTERN_COLORS[patternType];
    let hash = 0;
    for (let i = 0; i < patternType.length; i++) hash = (hash * 31 + patternType.charCodeAt(i)) >>> 0;
    return PD_FALLBACK_PALETTE[hash % PD_FALLBACK_PALETTE.length];
}

function _pdHexToRgba(hex, alpha) {
    const r = parseInt(hex.slice(1, 3), 16), g = parseInt(hex.slice(3, 5), 16), b = parseInt(hex.slice(5, 7), 16);
    return `rgba(${r},${g},${b},${alpha})`;
}

function _pdChartHeight() {
    return window.innerWidth < 768 ? 400 : 480;
}

function toggleFullscreen(wrapperId) {
    ChartFullscreen.toggle(wrapperId, { getHeight: _pdChartHeight });
}

function _pdBuildPatternTraces(pattern) {
    const color = _pdColorForType(pattern.pattern_type);
    const points = pattern.points || [];
    const key = _pdPatternKey(pattern);
    const traces = [];

    if (points.length >= 2) {
        traces.push({
            x: points.map(p => p.date).concat([points[0].date]),
            y: points.map(p => p.price).concat([points[0].price]),
            type: 'scatter', mode: 'lines', fill: 'toself',
            fillcolor: _pdHexToRgba(color, 0.20),
            line: { color: _pdHexToRgba(color, 0.7), width: 1 },
            name: _pdPatternLabel(pattern), legendgroup: key, hoverinfo: 'skip',
        });
    }

    (pattern.lines || []).forEach(line => {
        traces.push({
            x: [line.date_from, line.date_to], y: [line.price_from, line.price_to],
            type: 'scatter', mode: 'lines',
            line: { color, width: 1.5, dash: line.dash ? 'dash' : 'solid' },
            name: line.label || 'Key level', legendgroup: key, showlegend: false,
        });
    });

    traces.push({
        x: points.map(p => p.date), y: points.map(p => p.price), type: 'scatter', mode: 'markers+text',
        text: points.map(p => p.label), textposition: 'top center', textfont: { size: 9, color },
        marker: { color, size: 7 }, legendgroup: key, showlegend: false,
    });

    if (pattern.breakout_date) {
        traces.push({
            x: [pattern.breakout_date], y: [pattern.breakout_price], type: 'scatter', mode: 'markers',
            marker: { color, size: 10, symbol: 'star' }, legendgroup: key, showlegend: false,
        });
    }

    return traces;
}

function _pdRenderChart() {
    const el = document.getElementById('pd-detail-chart');
    if (!_pdSeries) return;
    const priceTrace = {
        x: _pdSeries.dates, y: _pdSeries.close, type: 'scatter', mode: 'lines',
        name: 'Close', line: { color: '#4da6ff', width: 1.5 },
    };
    let traces = [priceTrace];
    _pdPatterns.filter(p => _pdEnabled.has(_pdPatternKey(p))).forEach(p => {
        traces = traces.concat(_pdBuildPatternTraces(p));
    });
    const layout = {
        title: { text: `${window.PD_TICKER} — Detected Patterns`, x: 0.5, xanchor: 'center' },
        template: 'plotly_dark', height: _pdChartHeight(),
        margin: { l: 50, r: 20, t: 50, b: 60 },
        legend: { orientation: 'h', yanchor: 'top', y: -0.15, xanchor: 'center', x: 0.5 },
        paper_bgcolor: '#111', plot_bgcolor: '#111', font: { color: '#ccc' },
        yaxis: { title: 'Price', automargin: true },
    };
    Plotly.react(el, traces, layout, { responsive: true, displaylogo: false });
}

function _pdBuildCheckboxGroup(containerId, patterns) {
    const container = document.getElementById(containerId);
    container.innerHTML = patterns.map(p => {
        const key = _pdPatternKey(p);
        const color = _pdColorForType(p.pattern_type);
        return `
            <div class="checkbox-group mb-1">
                <input type="checkbox" class="pd-pattern-checkbox" data-key="${key}" checked>
                <label style="color:${color};">${escapeHtml(_pdPatternLabel(p))}</label>
            </div>`;
    }).join('');
}

function _pdUpdateMasterState(masterId, groupSelector) {
    const master = document.getElementById(masterId);
    const children = document.querySelectorAll(groupSelector);
    const checkedCount = Array.from(children).filter(c => c.checked).length;
    master.checked = children.length > 0 && checkedCount === children.length;
    master.indeterminate = checkedCount > 0 && checkedCount < children.length;
}

function _pdOnChildToggle(masterId, groupSelector) {
    document.querySelectorAll(groupSelector).forEach(cb => {
        if (cb.checked) _pdEnabled.add(cb.dataset.key); else _pdEnabled.delete(cb.dataset.key);
    });
    _pdUpdateMasterState(masterId, groupSelector);
    _pdRenderChart();
}

function _pdOnMasterToggle(masterId, groupSelector) {
    const master = document.getElementById(masterId);
    master.indeterminate = false;
    document.querySelectorAll(groupSelector).forEach(cb => {
        cb.checked = master.checked;
        if (cb.checked) _pdEnabled.add(cb.dataset.key); else _pdEnabled.delete(cb.dataset.key);
    });
    _pdRenderChart();
}

function _pdWireGroup(masterId, containerId) {
    const groupSelector = `#${containerId} .pd-pattern-checkbox`;
    document.querySelectorAll(groupSelector).forEach(cb => {
        cb.addEventListener('change', () => _pdOnChildToggle(masterId, groupSelector));
    });
    document.getElementById(masterId).addEventListener('change', () => _pdOnMasterToggle(masterId, groupSelector));
    _pdUpdateMasterState(masterId, groupSelector);
}

function _pdLoadTickerPatterns() {
    fetch(`/api/pattern-detection/chart/${encodeURIComponent(window.PD_TICKER)}`)
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') throw new Error(data.message || 'Failed to load patterns');
            _pdSeries = data.series;
            _pdPatterns = data.patterns || [];
            _pdEnabled = new Set(_pdPatterns.map(_pdPatternKey));

            if (!_pdPatterns.length) {
                document.getElementById('pd-detail-body').classList.add('d-none');
                document.getElementById('pd-detail-empty').classList.remove('bubble-empty-hidden');
                return;
            }

            const bullish = _pdPatterns.filter(p => p.direction === 'up');
            const bearish = _pdPatterns.filter(p => p.direction === 'down');

            _pdBuildCheckboxGroup('pd-bullish-children', bullish);
            _pdBuildCheckboxGroup('pd-bearish-children', bearish);
            document.getElementById('pd-bull-group').classList.toggle('d-none', !bullish.length);
            document.getElementById('pd-bear-group').classList.toggle('d-none', !bearish.length);
            _pdWireGroup('pd-master-bullish', 'pd-bullish-children');
            _pdWireGroup('pd-master-bearish', 'pd-bearish-children');

            _pdRenderChart();
        })
        .catch(err => {
            document.getElementById('pd-detail-chart').innerHTML = `<p class="text-danger p-3">${escapeHtml(err.message)}</p>`;
        });
}

document.addEventListener('DOMContentLoaded', _pdLoadTickerPatterns);
