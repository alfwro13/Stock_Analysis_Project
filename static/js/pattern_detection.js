// New pattern families only need an entry here for a human-readable label — the table,
// filters, and per-ticker detail page all work generically off whatever family/points/lines
// a result carries, with no further changes needed (see assets/pattern_detection.md).
const PATTERN_FAMILY_LABELS = {
    head_shoulders: 'Head & Shoulders',
    double_top_bottom: 'Double Top / Bottom',
    flag: 'Flag',
    triangle: 'Triangle',
    volatility_squeeze: 'Volatility Squeeze',
    narrow_range: 'Narrow Range (NR4/NR7)',
    candlestick_trigger: 'Candlestick Trigger',
};
const PATTERN_TYPE_LABELS = {
    regular: 'Head & Shoulders',
    inverse: 'Inverse Head & Shoulders',
    double_top: 'Double Top',
    double_bottom: 'Double Bottom',
    bull_flag: 'Bull Flag',
    bear_flag: 'Bear Flag',
    ascending: 'Ascending Triangle',
    descending: 'Descending Triangle',
    volatility_squeeze: 'Volatility Squeeze',
    volatility_squeeze_bullish: 'Volatility Squeeze (Bullish)',
    volatility_squeeze_bearish: 'Volatility Squeeze (Bearish)',
    nr4: 'NR4 Narrow Range',
    nr7: 'NR7 Narrow Range',
    nr4_bullish: 'NR4 Breakout (Bullish)',
    nr4_bearish: 'NR4 Breakout (Bearish)',
    nr7_bullish: 'NR7 Breakout (Bullish)',
    nr7_bearish: 'NR7 Breakout (Bearish)',
    bullish_engulfing: 'Bullish Engulfing',
    bearish_engulfing: 'Bearish Engulfing',
    hammer: 'Hammer',
    shooting_star: 'Shooting Star',
};

function _pdFamilyLabel(family) {
    return PATTERN_FAMILY_LABELS[family] || family || '—';
}

function _pdPatternTypeLabel(patternType) {
    return PATTERN_TYPE_LABELS[patternType] || patternType || '—';
}

// FORMING is always orange regardless of direction (not yet resolved); once CONFIRMED, the
// tag reflects direction — red for bearish, green for bullish. Shared with the
// Portfolio/Watchlist/Stock Detail "Setups & Tags" badges (see templates/partials/_macros.html).
function _pdTagClass(phase, direction) {
    if (phase === 'FORMING') return 'pattern-tag-forming';
    return direction === 'up' ? 'pattern-tag-bullish' : 'pattern-tag-bearish';
}

let _pdAllResults = [];
let _pdPortfolioTickers = new Set();
let _pdWatchlistTickers = new Set();
let _pdActiveFamily = '';
let _pdActiveDirection = '';
let _pdActiveScope = 'portfolio';

function _pdBuildFamilyFilter(results) {
    const select = document.getElementById('pd-family-filter');
    const families = Array.from(new Set(results.map(r => r.pattern_family))).sort();
    const current = select.value;
    select.innerHTML = '<option value="">All Families</option>' + families.map(f =>
        `<option value="${escapeHtml(f)}">${escapeHtml(_pdFamilyLabel(f))}</option>`
    ).join('');
    if (families.includes(current)) select.value = current;
}

function _pdGroupByTicker(results) {
    const byTicker = new Map();
    results.forEach(row => {
        if (!byTicker.has(row.ticker)) byTicker.set(row.ticker, []);
        byTicker.get(row.ticker).push(row);
    });
    return Array.from(byTicker.entries()).map(([ticker, patterns]) => ({
        ticker,
        patterns,
        lastScanTs: patterns.reduce((max, p) => (p.scan_ts || '') > max ? (p.scan_ts || '') : max, ''),
    })).sort((a, b) => b.lastScanTs.localeCompare(a.lastScanTs));
}

function _pdInScope(ticker) {
    if (_pdActiveScope === 'portfolio') return _pdPortfolioTickers.has(ticker);
    if (_pdActiveScope === 'watchlist') return _pdWatchlistTickers.has(ticker);
    return true;
}

function _pdRenderPatternsTable() {
    const tbody = document.getElementById('pd-patterns-body');
    const empty = document.getElementById('pd-patterns-empty');
    let filtered = _pdActiveFamily
        ? _pdAllResults.filter(r => r.pattern_family === _pdActiveFamily)
        : _pdAllResults;
    if (_pdActiveDirection) filtered = filtered.filter(r => r.direction === _pdActiveDirection);
    const grouped = _pdGroupByTicker(filtered).filter(g => _pdInScope(g.ticker));

    if (!grouped.length) {
        tbody.innerHTML = '';
        empty.classList.remove('bubble-empty-hidden');
        return;
    }
    empty.classList.add('bubble-empty-hidden');
    tbody.innerHTML = '';
    grouped.forEach(({ ticker, patterns, lastScanTs }) => {
        const tr = document.createElement('tr');
        tr.className = 'clickable';
        const badges = patterns.map(p => {
            const label = `${_pdPatternTypeLabel(p.pattern_type)} (${p.phase === 'CONFIRMED' ? 'Confirmed' : 'Forming'})`;
            return `<span class="setup-tag ${_pdTagClass(p.phase, p.direction)}">${escapeHtml(label)}</span>`;
        }).join('');
        tr.innerHTML = `
            <td><a href="/pattern-detection/${encodeURIComponent(ticker)}" style="color:#4da6ff;font-weight:600;text-decoration:none;">${escapeHtml(ticker)}</a></td>
            <td>${badges}</td>
            <td style="color:#444;font-size:10px;">${escapeHtml(lastScanTs || '')}</td>
        `;
        tr.addEventListener('click', () => { window.location.href = `/pattern-detection/${encodeURIComponent(ticker)}`; });
        tbody.appendChild(tr);
    });
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
            <td>${escapeHtml(_pdPatternTypeLabel(p.pattern_type))}</td>
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
            _pdPortfolioTickers = new Set(data.portfolio_tickers || []);
            _pdWatchlistTickers = new Set(data.watchlist_tickers || []);
            _pdBuildFamilyFilter(_pdAllResults);
            _pdRenderPatternsTable();
            const ts = document.getElementById('pd-last-scan');
            const latest = _pdAllResults.reduce((max, r) => (r.scan_ts || '') > max ? (r.scan_ts || '') : max, '');
            if (ts && latest) ts.textContent = 'Last scan: ' + latest;
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

    document.getElementById('pd-direction-filter').addEventListener('change', (e) => {
        _pdActiveDirection = e.target.value;
        _pdRenderPatternsTable();
    });

    document.getElementById('pd-scope-filter').addEventListener('change', (e) => {
        _pdActiveScope = e.target.value;
        _pdRenderPatternsTable();
    });

    document.getElementById('pd-run-btn').addEventListener('click', _pdTriggerScan);
    document.getElementById('pd-backfill-btn').addEventListener('click', _pdTriggerBackfill);
});
