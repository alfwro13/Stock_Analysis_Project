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
    if (type === 'Adjustment') {
        window._activitiesTable.column(1).search('Adjustment', false, true).draw();
        return;
    }
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

async function confirmAutotopup(accountId, pendingId) {
    const status = document.getElementById(`autotopup-confirm-status-${pendingId}`);
    const amount = parseFloat(document.getElementById(`autotopup-confirm-amount-${pendingId}`).value);
    const txnDate = document.getElementById(`autotopup-confirm-date-${pendingId}`).value;
    if (isNaN(amount) || !txnDate) {
        status.innerHTML = '<span class="msg-error">Enter a valid amount and date.</span>';
        return;
    }
    status.innerHTML = '<span class="msg-info">Confirming...</span>';
    try {
        const r = await fetch(`/api/accounts/${accountId}/autotopup/confirm`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pending_id: pendingId, amount, txn_date: txnDate }),
        });
        const data = await r.json();
        if (data.status === 'success') {
            location.reload();
        } else {
            status.innerHTML = `<span class="msg-error">${data.message || 'Failed to confirm.'}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${e.message}</span>`;
    }
}

async function dismissAutotopup(accountId, pendingId) {
    const status = document.getElementById(`autotopup-confirm-status-${pendingId}`);
    status.innerHTML = '<span class="msg-info">Dismissing...</span>';
    try {
        const r = await fetch(`/api/accounts/${accountId}/autotopup/dismiss`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ pending_id: pendingId }),
        });
        const data = await r.json();
        if (data.status === 'success') {
            location.reload();
        } else {
            status.innerHTML = `<span class="msg-error">${data.message || 'Failed to dismiss.'}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${e.message}</span>`;
    }
}

function _reconcileCashModal() {
    return bootstrap.Modal.getOrCreateInstance(document.getElementById('reconcileCashModal'));
}

function openReconcileModal(accountId) {
    document.getElementById('reconcile-account-id').value = accountId;
    document.getElementById('reconcile-actual-balance').value = '';
    document.getElementById('reconcile-preview').innerHTML = '';
    document.getElementById('reconcile-status').innerHTML = '';
    const computed = (window.ACCOUNT_SUMMARY && window.ACCOUNT_SUMMARY.cash_balance) || 0;
    document.getElementById('reconcile-computed').textContent =
        `App's computed cash balance: ${computed.toFixed(2)} ${window.BASE_CURRENCY}`;
    _reconcileCashModal().show();
}

function _updateReconcilePreview() {
    const preview = document.getElementById('reconcile-preview');
    const actual = parseFloat(document.getElementById('reconcile-actual-balance').value);
    if (isNaN(actual)) {
        preview.textContent = '';
        return;
    }
    const computed = (window.ACCOUNT_SUMMARY && window.ACCOUNT_SUMMARY.cash_balance) || 0;
    const delta = Math.round((actual - computed) * 100) / 100;
    if (Math.abs(delta) < 0.005) {
        preview.textContent = 'Already balanced — no adjustment needed.';
        return;
    }
    const sign = delta > 0 ? '+' : '';
    preview.textContent = `This will book a ${sign}${delta.toFixed(2)} ${window.BASE_CURRENCY} adjustment.`;
}

async function submitReconcile() {
    const status = document.getElementById('reconcile-status');
    const accountId = document.getElementById('reconcile-account-id').value;
    const actual = parseFloat(document.getElementById('reconcile-actual-balance').value);
    if (isNaN(actual)) {
        status.innerHTML = '<span class="msg-error">Enter the actual balance.</span>';
        return;
    }
    status.innerHTML = '<span class="msg-info">Saving...</span>';
    try {
        const r = await fetch(`/api/accounts/${accountId}/reconcile-cash`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ actual_balance: actual }),
        });
        const data = await r.json();
        if (data.status === 'success' && data.txn_id) {
            _reconcileCashModal().hide();
            if (typeof window.onTransactionChanged === 'function') window.onTransactionChanged();
        } else if (data.status === 'success') {
            status.innerHTML = `<span class="msg-success">${data.message || 'Already balanced.'}</span>`;
        } else {
            status.innerHTML = `<span class="msg-error">${data.message || 'Failed.'}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${e.message}</span>`;
    }
}
