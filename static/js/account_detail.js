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

function _pensionContributionModal() {
    return bootstrap.Modal.getOrCreateInstance(document.getElementById('pensionContributionModal'));
}

function _pensionFeeModal() {
    return bootstrap.Modal.getOrCreateInstance(document.getElementById('pensionFeeModal'));
}

async function _fetchPriceAtDate(accountId, date) {
    if (!date) return null;
    try {
        const r = await fetch(`/api/accounts/${accountId}/price-history/at-date?date=${encodeURIComponent(date)}`);
        const data = await r.json();
        return data.status === 'success' ? data.price : null;
    } catch (e) {
        return null;
    }
}

async function _fetchPensionUnitsAsOf(accountId, date) {
    if (!date) return null;
    try {
        const r = await fetch(`/api/accounts/${accountId}/pension/units-as-of?date=${encodeURIComponent(date)}`);
        const data = await r.json();
        return data.status === 'success' ? data.units : null;
    } catch (e) {
        return null;
    }
}

function openPensionContributionModal(accountId) {
    document.getElementById('pension-contrib-account-id').value = accountId;
    document.getElementById('pension-contrib-date').value = new Date().toISOString().slice(0, 10);
    document.getElementById('pension-contrib-amount').value = '';
    document.getElementById('pension-contrib-price').value = '';
    document.getElementById('pension-contrib-preview').innerHTML = '';
    document.getElementById('pension-contrib-status').innerHTML = '';
    _pensionContributionModal().show();
    _onPensionContribDateChange();
}

async function _onPensionContribDateChange() {
    const accountId = document.getElementById('pension-contrib-account-id').value;
    const date = document.getElementById('pension-contrib-date').value;
    const price = await _fetchPriceAtDate(accountId, date);
    document.getElementById('pension-contrib-price').value = price === null ? '' : price;
    _updatePensionContribPreview();
}

function _updatePensionContribPreview() {
    const preview = document.getElementById('pension-contrib-preview');
    const amount = parseFloat(document.getElementById('pension-contrib-amount').value);
    const price = parseFloat(document.getElementById('pension-contrib-price').value);
    if (!amount || !price) {
        preview.textContent = '';
        return;
    }
    preview.textContent = `This will add ${(amount / price).toFixed(6)} units.`;
}

async function submitPensionContribution() {
    const status = document.getElementById('pension-contrib-status');
    const accountId = document.getElementById('pension-contrib-account-id').value;
    const txnDate = document.getElementById('pension-contrib-date').value;
    const amount = parseFloat(document.getElementById('pension-contrib-amount').value);
    if (!txnDate || !amount || amount <= 0) {
        status.innerHTML = '<span class="msg-error">Date and a positive amount are required.</span>';
        return;
    }
    const priceOverride = document.getElementById('pension-contrib-price').value;
    status.innerHTML = '<span class="msg-info">Saving...</span>';
    try {
        const r = await fetch(`/api/accounts/${accountId}/pension/contribution`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                txn_date: txnDate,
                amount,
                unit_price: priceOverride === '' ? null : parseFloat(priceOverride),
            }),
        });
        const data = await r.json();
        if (data.status === 'success') {
            _pensionContributionModal().hide();
            if (typeof window.onTransactionChanged === 'function') window.onTransactionChanged();
        } else {
            status.innerHTML = `<span class="msg-error">${data.message || 'Failed.'}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${e.message}</span>`;
    }
}

let _pensionFeeUnitsBefore = null;

function _pensionFeeMode() {
    return document.querySelector('input[name="pension-fee-mode"]:checked').value;
}

function _onPensionFeeModeChange() {
    const mode = _pensionFeeMode();
    document.getElementById('pension-fee-units-after-group').classList.toggle('d-none', mode !== 'after');
    document.getElementById('pension-fee-units-removed-group').classList.toggle('d-none', mode !== 'removed');
    _updatePensionFeePreview();
}

function openPensionFeeModal(accountId) {
    document.getElementById('pension-fee-account-id').value = accountId;
    document.getElementById('pension-fee-date').value = new Date().toISOString().slice(0, 10);
    document.getElementById('pension-fee-mode-after').checked = true;
    document.getElementById('pension-fee-units-after').value = '';
    document.getElementById('pension-fee-units-removed').value = '';
    document.getElementById('pension-fee-price').value = '';
    document.getElementById('pension-fee-units-before').textContent = '';
    document.getElementById('pension-fee-preview').innerHTML = '';
    document.getElementById('pension-fee-status').innerHTML = '';
    _pensionFeeUnitsBefore = null;
    _onPensionFeeModeChange();
    _pensionFeeModal().show();
    _onPensionFeeDateChange();
}

async function _onPensionFeeDateChange() {
    const accountId = document.getElementById('pension-fee-account-id').value;
    const date = document.getElementById('pension-fee-date').value;
    const [price, unitsBefore] = await Promise.all([
        _fetchPriceAtDate(accountId, date),
        _fetchPensionUnitsAsOf(accountId, date),
    ]);
    document.getElementById('pension-fee-price').value = price === null ? '' : price;
    _pensionFeeUnitsBefore = unitsBefore;
    document.getElementById('pension-fee-units-before').textContent =
        unitsBefore === null ? '' : `Units currently held: ${unitsBefore}`;
    _updatePensionFeePreview();
}

function _updatePensionFeePreview() {
    const preview = document.getElementById('pension-fee-preview');
    const price = parseFloat(document.getElementById('pension-fee-price').value);
    const mode = _pensionFeeMode();
    let removed;
    if (mode === 'after') {
        const unitsAfter = parseFloat(document.getElementById('pension-fee-units-after').value);
        if (_pensionFeeUnitsBefore === null || isNaN(unitsAfter)) {
            preview.textContent = '';
            return;
        }
        removed = _pensionFeeUnitsBefore - unitsAfter;
    } else {
        removed = parseFloat(document.getElementById('pension-fee-units-removed').value);
        if (isNaN(removed)) {
            preview.textContent = '';
            return;
        }
    }
    if (isNaN(price)) {
        preview.textContent = '';
        return;
    }
    if (removed <= 0 || (_pensionFeeUnitsBefore !== null && removed > _pensionFeeUnitsBefore)) {
        preview.innerHTML = '<span class="msg-error">Units removed must be positive and no more than the units currently held.</span>';
        return;
    }
    const currency = (window.CURRENT_ACCOUNT && window.CURRENT_ACCOUNT.currency) || '';
    preview.textContent = `This will remove ${removed.toFixed(6)} units, costing ${(removed * price).toFixed(2)} ${currency}.`;
}

async function submitPensionFee() {
    const status = document.getElementById('pension-fee-status');
    const accountId = document.getElementById('pension-fee-account-id').value;
    const txnDate = document.getElementById('pension-fee-date').value;
    const mode = _pensionFeeMode();
    const unitsAfter = document.getElementById('pension-fee-units-after').value;
    const unitsRemoved = document.getElementById('pension-fee-units-removed').value;
    if (!txnDate || (mode === 'after' ? unitsAfter === '' : unitsRemoved === '')) {
        status.innerHTML = '<span class="msg-error">Date and the units field are required.</span>';
        return;
    }
    const priceOverride = document.getElementById('pension-fee-price').value;
    status.innerHTML = '<span class="msg-info">Saving...</span>';
    try {
        const r = await fetch(`/api/accounts/${accountId}/pension/fee`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                txn_date: txnDate,
                units_after: mode === 'after' ? parseFloat(unitsAfter) : null,
                units_removed: mode === 'removed' ? parseFloat(unitsRemoved) : null,
                unit_price: priceOverride === '' ? null : parseFloat(priceOverride),
            }),
        });
        const data = await r.json();
        if (data.status === 'success') {
            status.innerHTML = `<span class="msg-success">Removed ${data.units_removed} units (cost ${data.fee_cost}).</span>`;
            _pensionFeeModal().hide();
            if (typeof window.onTransactionChanged === 'function') window.onTransactionChanged();
        } else {
            status.innerHTML = `<span class="msg-error">${data.message || 'Failed.'}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${e.message}</span>`;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    (window.ACCOUNT_TRANSACTIONS || []).forEach(t => { _txnCache[t.id] = t; });
    _initAccountDetailTable('holdingsTable', [[0], [1], [6], [4], [5], [7], [8], [2], [3]]);
    _initAccountDetailTable('closedTable', [[0], [1], [5], [3], [4], [6], [7], [2]]);
    window._activitiesTable = _initAccountDetailTable('activitiesTable', [[0], [1], [8], [7], [2], [3], [4], [5], [6]]);
    _initAccountDetailTable('cashTable', [[0], [2], [3], [1]]);
});
