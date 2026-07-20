// A new pattern family needs nothing added here to render correctly: its checkboxes are
// built from whichever patterns the API returns for this ticker, grouped purely by the
// server-resolved `direction` field ("up"/"down"), and its color falls back to a
// deterministic palette pick if PD_PATTERN_COLORS has no entry for its pattern_type yet.
const PD_PATTERN_TYPE_LABELS = {
    regular: 'Head & Shoulders',
    inverse: 'Inverse Head & Shoulders',
    double_top: 'Double Top',
    double_bottom: 'Double Bottom',
};
const PD_PATTERN_COLORS = {
    regular: '#ff4d4d',
    double_top: '#ff9900',
    inverse: '#4caf50',
    double_bottom: '#22b8cf',
};
const PD_FALLBACK_PALETTE = ['#ff4d4d', '#ff9900', '#4caf50', '#22b8cf', '#9b59b6', '#e91e8c', '#3498db', '#f1c40f'];

// Plain-language, 2-3 sentence explanation per pattern_type — shown at the bottom of this
// page for whichever patterns are actually present. A new pattern family should add one
// entry here (see assets/pattern_detection.md).
const PD_PATTERN_EXPLANATIONS = {
    regular: "A Head & Shoulders pattern suggests a stock that's been rising may be about to turn downward. It forms three peaks — a smaller one, then a taller one in the middle, then another smaller one — sitting above a support line (the \"neckline\"). If the price breaks below that line, it's read as a signal that buyers are losing control and the uptrend may be ending.",
    inverse: "An Inverse Head & Shoulders is the mirror image, suggesting a stock that's been falling may be about to turn upward. It forms three troughs — with the middle one the deepest — sitting below a resistance line. A break above that line is read as a signal that sellers are losing control and the downtrend may be ending.",
    double_top: "A Double Top forms when a rising stock hits the same price ceiling twice without breaking through, with a dip in between. It suggests buyers have tried and failed twice to push the price to a new high, and a drop below the dip's level is read as a signal of a possible downturn.",
    double_bottom: "A Double Bottom forms when a falling stock hits the same price floor twice without breaking below it, with a bounce in between. It suggests sellers have tried and failed twice to push the price to a new low, and a rise above the bounce's level is read as a signal of a possible upturn.",
};

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
        // FORMING is always orange regardless of direction (not yet resolved); CONFIRMED
        // reflects direction — red bearish, green bullish. Same tag classes used everywhere
        // else a pattern is tagged (Portfolio/Watchlist/Stock Detail, the list-page badges).
        const tagClass = p.phase === 'FORMING' ? 'pattern-tag-forming' : (p.direction === 'up' ? 'pattern-tag-bullish' : 'pattern-tag-bearish');
        return `
            <div class="checkbox-group mb-1">
                <input type="checkbox" class="pd-pattern-checkbox" data-key="${key}" checked>
                <label><span class="setup-tag ${tagClass}">${escapeHtml(_pdPatternLabel(p))}</span></label>
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

function _pdRenderExplanations() {
    const el = document.getElementById('pd-explanations');
    const seen = new Set();
    const cards = [];
    _pdPatterns.forEach(p => {
        if (seen.has(p.pattern_type)) return;
        seen.add(p.pattern_type);
        const explanation = PD_PATTERN_EXPLANATIONS[p.pattern_type];
        if (!explanation) return;
        cards.push(`
            <div class="settings-panel mb-2">
                <h5 class="mb-1">${escapeHtml(PD_PATTERN_TYPE_LABELS[p.pattern_type] || p.pattern_type)}</h5>
                <p class="text-muted small mb-0">${escapeHtml(explanation)}</p>
            </div>`);
    });
    el.innerHTML = cards.join('');
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
            _pdRenderExplanations();
        })
        .catch(err => {
            document.getElementById('pd-detail-chart').innerHTML = `<p class="text-danger p-3">${escapeHtml(err.message)}</p>`;
        });
}

document.addEventListener('DOMContentLoaded', _pdLoadTickerPatterns);
