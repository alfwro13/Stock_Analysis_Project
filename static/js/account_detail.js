function toggleFullscreen(wrapperId) {
    const el = document.getElementById(wrapperId);
    if (!el) return;
    if (!document.fullscreenElement) {
        el.requestFullscreen().catch(() => {});
    } else {
        document.exitFullscreen();
    }
}

window.onTransactionChanged = function () {
    location.reload();
};

function _initAccountDetailTable(id, priorities) {
    const el = document.getElementById(id);
    if (!el) return null;
    return $(el).DataTable({
        responsive: true,
        pageLength: 25,
        columnDefs: priorities.map((targets, priority) => ({ responsivePriority: priority + 1, targets })),
    });
}

function filterActivitiesByType(type) {
    if (!window._activitiesTable) return;
    window._activitiesTable.column(1).search(type ? `^${type}$` : '', true, false).draw();
}

document.addEventListener('DOMContentLoaded', () => {
    (window.ACCOUNT_TRANSACTIONS || []).forEach(t => { _txnCache[t.id] = t; });
    _initAccountDetailTable('holdingsTable', [[0], [1], [6], [4], [5], [7], [8], [2], [3]]);
    _initAccountDetailTable('closedTable', [[0], [1], [5], [3], [4], [6], [7], [2]]);
    window._activitiesTable = _initAccountDetailTable('activitiesTable', [[0], [1], [8], [7], [2], [3], [4], [5], [6]]);
    _initAccountDetailTable('cashTable', [[0], [2], [3], [1]]);
    initAccountValueChart();
});

function _setAcctChartPeriodCookie(period) {
    document.cookie = 'acct_chart_period=' + period + ';path=/;max-age=31536000';
}

function _renderAccountValueChart(data) {
    const el = document.getElementById('acct-chart-wrapper');
    if (!data.length) {
        el.innerHTML = "<p class='text-muted'>No value history yet — check back after the next nightly snapshot.</p>";
        return;
    }
    const dates = data.map(d => d.snapshot_date);
    const traces = [
        { x: dates, y: data.map(d => d.total_value), name: 'Total Value', line: { color: '#00ffcc', width: 2 }, connectgaps: true },
        { x: dates, y: data.map(d => d.cash_value), name: 'Cash', line: { color: '#bb86fc', width: 1.5, dash: 'dot' }, connectgaps: true },
        { x: dates, y: data.map(d => d.net_contributions), name: 'Net Contributions', line: { color: '#ffb74d', width: 1.5, dash: 'dash' }, connectgaps: true },
    ];
    const layout = {
        title: { text: 'Account Value Over Time', x: 0.5, xanchor: 'center' },
        template: 'plotly_dark', height: 350,
        margin: { l: 20, r: 20, t: 50, b: 20 }, hovermode: 'x unified',
        legend: { orientation: 'h', yanchor: 'bottom', y: 1.02, xanchor: 'right', x: 1 },
        paper_bgcolor: '#111', plot_bgcolor: '#111', font: { color: '#ccc' },
        yaxis: { title: 'Value', showgrid: true, gridcolor: '#333333' },
    };
    Plotly.react(el, traces, layout, { responsive: true, displaylogo: false });
}

function _setAcctPeriodButtons(active) {
    document.querySelectorAll('.acct-period-btn').forEach(btn => {
        const isActive = btn.dataset.period === active;
        btn.classList.toggle('btn-primary', isActive);
        btn.classList.toggle('btn-outline-secondary', !isActive);
    });
}

function fetchAccountValueHistory(period) {
    _setAcctPeriodButtons(period);
    _setAcctChartPeriodCookie(period);
    fetch(`/api/accounts/${window.CURRENT_ACCOUNT.id}/value-history?period=${period}`)
        .then(r => r.json())
        .then(json => _renderAccountValueChart(json.data || []))
        .catch(e => console.error('Account value history fetch failed:', e));
}

function initAccountValueChart() {
    if (!document.getElementById('acct-chart-wrapper')) return;
    document.querySelectorAll('.acct-period-btn').forEach(btn => {
        btn.addEventListener('click', () => fetchAccountValueHistory(btn.dataset.period));
    });
    _setAcctPeriodButtons(window.ACCT_CHART_PERIOD || 'max');
    _renderAccountValueChart(window.ACCT_CHART_INITIAL || []);
}
