function _pensionChartHeight() {
    // Mobile can't go below 400 — static/css/styles.css forces a 400px min-height
    // on .js-plotly-plot under 768px (for the Macro chart); a smaller value here
    // leaves the chart pinned short inside a taller, CSS-floored container.
    return window.innerWidth < 768 ? 400 : 350;
}

const _PENSION_CHART_SELECTORS = ['#pension-price-chart-wrapper', '#pension-value-chart-wrapper'];

// These charts are embedded server-side (visuals.py's fig.to_html()) rather than created via
// the Plotly JS API, and their `config.responsive` never actually reacts to container size
// changes (rotation, fullscreen) — so width/height must be relayout'd explicitly on every resize.
function _relayoutPensionChart(sel, height) {
    const plotEl = document.querySelector(`${sel} .js-plotly-plot`);
    if (!plotEl || !window.Plotly) return;
    Plotly.relayout(plotEl, { width: plotEl.getBoundingClientRect().width, height });
}

function toggleFullscreen(wrapperId) {
    const wrapper = document.getElementById(wrapperId);
    if (!wrapper) return;
    const isFullscreen = wrapper.classList.contains('is-fullscreen');
    const btn = wrapper.querySelector('.fullscreen-btn');
    const sel = `#${wrapper.querySelector('[id$="-chart-wrapper"]').id}`;
    if (isFullscreen) {
        wrapper.classList.remove('is-fullscreen');
        if (btn) btn.innerHTML = '&#9638; Fullscreen';
        _relayoutPensionChart(sel, _pensionChartHeight());
    } else {
        wrapper.classList.add('is-fullscreen');
        if (btn) btn.innerHTML = '&#10006; Exit Fullscreen';
        _relayoutPensionChart(sel, window.innerHeight - 120);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    _PENSION_CHART_SELECTORS.forEach(sel => _relayoutPensionChart(sel, _pensionChartHeight()));
    window.addEventListener('resize', () => {
        _PENSION_CHART_SELECTORS.forEach(sel => {
            const wrapper = document.querySelector(sel).closest('.chart-wrapper');
            const isFullscreen = wrapper && wrapper.classList.contains('is-fullscreen');
            _relayoutPensionChart(sel, isFullscreen ? window.innerHeight - 120 : _pensionChartHeight());
        });
    });
});

window.onTransactionChanged = function () {
    location.reload();
};

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

let _pensionContribUnitsBefore = null;

function openPensionContributionModal(accountId) {
    document.getElementById('pension-contrib-account-id').value = accountId;
    document.getElementById('pension-contrib-date').value = new Date().toISOString().slice(0, 10);
    document.getElementById('pension-contrib-amount').value = '';
    document.getElementById('pension-contrib-price').value = '';
    document.getElementById('pension-contrib-preview').innerHTML = '';
    document.getElementById('pension-contrib-status').innerHTML = '';
    _pensionContribUnitsBefore = null;
    _pensionContributionModal().show();
    _onPensionContribDateChange();
}

async function _onPensionContribDateChange() {
    const accountId = document.getElementById('pension-contrib-account-id').value;
    const date = document.getElementById('pension-contrib-date').value;
    const [price, unitsBefore] = await Promise.all([
        _fetchPriceAtDate(accountId, date),
        _fetchPensionUnitsAsOf(accountId, date),
    ]);
    document.getElementById('pension-contrib-price').value = price === null ? '' : price;
    _pensionContribUnitsBefore = unitsBefore;
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
    const unitsAdded = amount / price;
    if (_pensionContribUnitsBefore === null) {
        preview.textContent = `This will add ${unitsAdded.toFixed(6)} units.`;
        return;
    }
    const newTotal = _pensionContribUnitsBefore + unitsAdded;
    preview.textContent = `This will add ${unitsAdded.toFixed(6)} units (new total: ${newTotal.toFixed(6)} units).`;
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
    $('#pensionActivitiesTable').DataTable({
        responsive: true,
        pageLength: 25,
        order: [[0, 'desc']],
        columnDefs: [
            { responsivePriority: 1, targets: [0] },
            { responsivePriority: 2, targets: [1] },
            { responsivePriority: 3, targets: [8] },
            { responsivePriority: 4, targets: [5] },
            { responsivePriority: 5, targets: [3] },
            { responsivePriority: 6, targets: [4] },
            { responsivePriority: 7, targets: [6] },
            { responsivePriority: 8, targets: [2] },
            { responsivePriority: 9, targets: [7] },
        ],
    });
});
