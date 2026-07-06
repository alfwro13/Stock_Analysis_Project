function _treasuryBillModal() {
    return bootstrap.Modal.getOrCreateInstance(document.getElementById('treasuryBillModal'));
}

function openBuyTreasuryBillModal(accountId) {
    const today = new Date().toISOString().slice(0, 10);
    document.getElementById('tbill-account-id').value = accountId;
    document.getElementById('tbill-purchase-date').value = today;
    document.getElementById('tbill-face-value').value = '';
    document.getElementById('tbill-purchase-price').value = '';
    document.getElementById('tbill-ytm').value = '';
    document.getElementById('tbill-maturity-date').value = '';
    document.getElementById('tbill-auto-reinvest').checked = false;
    document.getElementById('tbill-notes').value = '';
    document.getElementById('tbill-preview').innerHTML = '';
    document.getElementById('tbill-status').innerHTML = '';
    _treasuryBillModal().show();
    _onTBillInputChange();
}

// Freetrade shows Amount/Total Cost and an indicative YTM, never face value directly (the real
// yield isn't fixed until the Friday DMO tender) — so Face Value is auto-estimated from those,
// then left editable in case the operator later learns the exact redemption amount.
function _onTBillInputChange() {
    const purchaseDate = document.getElementById('tbill-purchase-date').value;
    const maturityField = document.getElementById('tbill-maturity-date');
    if (purchaseDate && !maturityField.value) {
        const d = new Date(purchaseDate);
        d.setDate(d.getDate() + 28);
        maturityField.value = d.toISOString().slice(0, 10);
    }
    const amount = parseFloat(document.getElementById('tbill-purchase-price').value);
    const ytm = parseFloat(document.getElementById('tbill-ytm').value);
    const maturityDate = maturityField.value;
    if (amount && !isNaN(ytm) && purchaseDate && maturityDate) {
        const days = (new Date(maturityDate) - new Date(purchaseDate)) / 86400000;
        if (days > 0) {
            document.getElementById('tbill-face-value').value = (amount * (1 + (ytm / 100) * (days / 365))).toFixed(2);
        }
    }
    _updateTBillPreview();
}

function _updateTBillPreview() {
    const preview = document.getElementById('tbill-preview');
    const faceValue = parseFloat(document.getElementById('tbill-face-value').value);
    const purchasePrice = parseFloat(document.getElementById('tbill-purchase-price').value);
    const purchaseDate = document.getElementById('tbill-purchase-date').value;
    const maturityDate = document.getElementById('tbill-maturity-date').value;
    if (!faceValue || !purchasePrice || !purchaseDate || !maturityDate) {
        preview.textContent = '';
        return;
    }
    if (purchasePrice >= faceValue) {
        preview.innerHTML = '<span class="msg-error">Amount must be less than face value.</span>';
        return;
    }
    const days = (new Date(maturityDate) - new Date(purchaseDate)) / 86400000;
    if (days <= 0) {
        preview.innerHTML = '<span class="msg-error">Maturity date must be after the start date.</span>';
        return;
    }
    const gain = faceValue - purchasePrice;
    const annualisedYield = (gain / purchasePrice) * (365 / days) * 100;
    preview.textContent = `Estimated gain of ${gain.toFixed(2)} over ${days} day(s) — approx. ${annualisedYield.toFixed(2)}% annualised. This is an estimate; edit Face Value if you know the exact redemption amount.`;
}

async function submitBuyTreasuryBill() {
    const status = document.getElementById('tbill-status');
    const accountId = document.getElementById('tbill-account-id').value;
    const ytm = document.getElementById('tbill-ytm').value;
    const body = {
        purchase_date: document.getElementById('tbill-purchase-date').value,
        face_value: parseFloat(document.getElementById('tbill-face-value').value),
        purchase_price: parseFloat(document.getElementById('tbill-purchase-price').value),
        maturity_date: document.getElementById('tbill-maturity-date').value,
        auto_reinvest: document.getElementById('tbill-auto-reinvest').checked,
        notes: document.getElementById('tbill-notes').value || null,
        indicative_ytm: ytm === '' ? null : parseFloat(ytm),
    };
    if (!body.purchase_date || !body.face_value || !body.purchase_price || !body.maturity_date) {
        status.innerHTML = '<span class="msg-error">Start date, amount, face value, and maturity date are all required.</span>';
        return;
    }
    status.innerHTML = '<span class="msg-info">Saving...</span>';
    try {
        const r = await fetch(`/api/accounts/${accountId}/treasury-bills`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await r.json();
        if (data.status === 'success') {
            _treasuryBillModal().hide();
            if (typeof window.onTransactionChanged === 'function') window.onTransactionChanged();
        } else {
            status.innerHTML = `<span class="msg-error">${data.message || 'Failed.'}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${e.message}</span>`;
    }
}

async function toggleTreasuryBillAutoReinvest(accountId, billId, checked) {
    try {
        const r = await fetch(`/api/accounts/${accountId}/treasury-bills/${billId}`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ auto_reinvest: checked }),
        });
        const data = await r.json();
        if (data.status !== 'success') {
            alert(data.message || 'Failed to update the Treasury Bill.');
        }
    } catch (e) {
        alert(e.message);
    }
}

async function deleteTreasuryBill(accountId, billId) {
    if (!confirm('Delete this Treasury Bill? This removes its purchase (and maturity, if matured) transaction too.')) return;
    try {
        const r = await fetch(`/api/accounts/${accountId}/treasury-bills/${billId}`, { method: 'DELETE' });
        const data = await r.json();
        if (data.status === 'success') {
            if (typeof window.onTransactionChanged === 'function') window.onTransactionChanged();
        } else {
            alert(data.message || 'Failed to delete the Treasury Bill.');
        }
    } catch (e) {
        alert(e.message);
    }
}

async function confirmTreasuryBillYtm(accountId, billId) {
    const status = document.getElementById(`tbill-ytm-confirm-status-${billId}`);
    const rate = parseFloat(document.getElementById(`tbill-ytm-confirm-rate-${billId}`).value);
    if (isNaN(rate)) {
        status.innerHTML = '<span class="msg-error">Enter a valid YTM.</span>';
        return;
    }
    status.innerHTML = '<span class="msg-info">Confirming...</span>';
    try {
        const r = await fetch(`/api/accounts/${accountId}/treasury-bills/${billId}/confirm-ytm`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirmed_ytm: rate }),
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

async function keepTreasuryBillEstimate(accountId, billId) {
    const status = document.getElementById(`tbill-ytm-confirm-status-${billId}`);
    status.innerHTML = '<span class="msg-info">Saving...</span>';
    try {
        const r = await fetch(`/api/accounts/${accountId}/treasury-bills/${billId}/confirm-ytm`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ confirmed_ytm: null }),
        });
        const data = await r.json();
        if (data.status === 'success') {
            location.reload();
        } else {
            status.innerHTML = `<span class="msg-error">${data.message || 'Failed.'}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${e.message}</span>`;
    }
}

document.addEventListener('DOMContentLoaded', () => {
    _initAccountDetailTable('treasuryBillsTable', [[3], [6], [5], [8], [4], [1], [0], [2], [7]], [[3, 'asc']]);
});
