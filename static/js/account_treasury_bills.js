function _treasuryBillModal() {
    return bootstrap.Modal.getOrCreateInstance(document.getElementById('treasuryBillModal'));
}

function openBuyTreasuryBillModal(accountId) {
    const today = new Date().toISOString().slice(0, 10);
    document.getElementById('tbill-account-id').value = accountId;
    document.getElementById('tbill-purchase-date').value = today;
    document.getElementById('tbill-face-value').value = '';
    document.getElementById('tbill-purchase-price').value = '';
    document.getElementById('tbill-maturity-date').value = '';
    document.getElementById('tbill-auto-reinvest').checked = false;
    document.getElementById('tbill-notes').value = '';
    document.getElementById('tbill-preview').innerHTML = '';
    document.getElementById('tbill-status').innerHTML = '';
    _treasuryBillModal().show();
    _onTBillDateChange();
}

function _onTBillDateChange() {
    const purchaseDate = document.getElementById('tbill-purchase-date').value;
    const maturityField = document.getElementById('tbill-maturity-date');
    if (purchaseDate && !maturityField.value) {
        const d = new Date(purchaseDate);
        d.setDate(d.getDate() + 28);
        maturityField.value = d.toISOString().slice(0, 10);
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
        preview.innerHTML = '<span class="msg-error">Purchase price must be less than face value.</span>';
        return;
    }
    const days = (new Date(maturityDate) - new Date(purchaseDate)) / 86400000;
    if (days <= 0) {
        preview.innerHTML = '<span class="msg-error">Maturity date must be after the purchase date.</span>';
        return;
    }
    const discount = faceValue - purchasePrice;
    const annualisedYield = (discount / purchasePrice) * (365 / days) * 100;
    preview.textContent = `Discount of ${discount.toFixed(2)} over ${days} day(s) — approx. ${annualisedYield.toFixed(2)}% annualised yield.`;
}

async function submitBuyTreasuryBill() {
    const status = document.getElementById('tbill-status');
    const accountId = document.getElementById('tbill-account-id').value;
    const body = {
        purchase_date: document.getElementById('tbill-purchase-date').value,
        face_value: parseFloat(document.getElementById('tbill-face-value').value),
        purchase_price: parseFloat(document.getElementById('tbill-purchase-price').value),
        maturity_date: document.getElementById('tbill-maturity-date').value,
        auto_reinvest: document.getElementById('tbill-auto-reinvest').checked,
        notes: document.getElementById('tbill-notes').value || null,
    };
    if (!body.purchase_date || !body.face_value || !body.purchase_price || !body.maturity_date) {
        status.innerHTML = '<span class="msg-error">Purchase date, face value, purchase price, and maturity date are all required.</span>';
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

document.addEventListener('DOMContentLoaded', () => {
    _initAccountDetailTable('treasuryBillsTable', [[3], [5], [4], [7], [0], [1], [2], [6]], [[3, 'asc']]);
});
