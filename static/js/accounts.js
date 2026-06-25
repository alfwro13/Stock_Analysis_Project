let _accountsCache = {};
let _txnCache = {};
let _txnLookupTimer = null;

function _escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _accountModal() {
    return bootstrap.Modal.getOrCreateInstance(document.getElementById('accountModal'));
}

function _txnModal() {
    return bootstrap.Modal.getOrCreateInstance(document.getElementById('txnModal'));
}

async function loadAccounts() {
    const list = document.getElementById('accounts-list');
    try {
        const r = await fetch('/api/accounts');
        const data = await r.json();
        _accountsCache = {};
        (data.accounts || []).forEach(a => { _accountsCache[a.id] = a; });
        _populateAccountSelect();
        if (!data.accounts || data.accounts.length === 0) {
            list.innerHTML = '<div class="col-12"><p class="text-muted">No accounts yet — create one to start tracking transactions.</p></div>';
            return;
        }
        list.innerHTML = data.accounts.map(_accountCardHtml).join('');
    } catch (e) {
        list.innerHTML = `<div class="col-12"><span class="msg-error">Failed to load accounts: ${e.message}</span></div>`;
    }
}

function _accountCardHtml(acc) {
    return `
    <div class="col-12 col-lg-6">
        <div class="guide-card h-100" id="account-card-${acc.id}">
            <div class="d-flex justify-content-between align-items-start">
                <div>
                    <h4 class="mb-0">${_escapeHtml(acc.name)}</h4>
                    <p class="text-muted small mb-0">${_escapeHtml(acc.currency)} &middot; initial cash ${acc.initial_cash}</p>
                    ${acc.note ? `<p class="text-secondary small mb-0">${_escapeHtml(acc.note)}</p>` : ''}
                </div>
                <div class="d-flex gap-2">
                    <button type="button" class="btn btn-outline-secondary btn-sm" onclick="openAccountModal(${acc.id})">Edit</button>
                    <button type="button" class="btn btn-outline-danger btn-sm" onclick="deleteAccount(${acc.id}, '${_escapeHtml(acc.name)}')">Delete</button>
                </div>
            </div>
            <div class="d-flex gap-2 mt-3">
                <button type="button" class="btn btn-primary btn-sm" onclick="openTxnModal(${acc.id})">+ Add Transaction</button>
                <button type="button" class="btn btn-outline-secondary btn-sm" onclick="toggleTransactions(${acc.id})">Show Transactions</button>
            </div>
            <div id="account-txns-${acc.id}" class="mt-3 d-none"></div>
        </div>
    </div>`;
}

function _populateAccountSelect() {
    const sel = document.getElementById('txn-account');
    const current = sel.value;
    sel.innerHTML = Object.values(_accountsCache).map(a => `<option value="${a.id}">${_escapeHtml(a.name)}</option>`).join('');
    if (current) sel.value = current;
}

function openAccountModal(id = null) {
    const acc = id ? _accountsCache[id] : null;
    document.getElementById('accountModalTitle').textContent = acc ? 'Edit Account' : 'New Account';
    document.getElementById('acct-id').value = acc ? acc.id : '';
    document.getElementById('acct-name').value = acc ? acc.name : '';
    document.getElementById('acct-currency').value = acc ? acc.currency : window.BASE_CURRENCY;
    document.getElementById('acct-cash').value = acc ? acc.initial_cash : 0;
    document.getElementById('acct-note').value = acc ? (acc.note || '') : '';
    document.getElementById('account-status').innerHTML = '';
    _accountModal().show();
}

async function saveAccount() {
    const status = document.getElementById('account-status');
    const id = document.getElementById('acct-id').value;
    const name = document.getElementById('acct-name').value.trim();
    const currency = document.getElementById('acct-currency').value.trim().toUpperCase();
    if (!name || !currency) {
        status.innerHTML = '<span class="msg-error">Name and currency are required.</span>';
        return;
    }
    const body = {
        name,
        currency,
        initial_cash: parseFloat(document.getElementById('acct-cash').value) || 0,
        note: document.getElementById('acct-note').value.trim() || null,
    };
    status.innerHTML = '<span class="msg-info">Saving...</span>';
    try {
        const r = await fetch(id ? `/api/accounts/${id}` : '/api/accounts', {
            method: id ? 'PUT' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await r.json();
        if (data.status === 'success') {
            _accountModal().hide();
            loadAccounts();
        } else {
            status.innerHTML = `<span class="msg-error">${data.message || 'Failed'}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${e.message}</span>`;
    }
}

async function deleteAccount(id, name) {
    if (!confirm(`Delete account "${name}"? Its transaction history will be preserved but the account will no longer appear in lists.`)) return;
    try {
        const r = await fetch(`/api/accounts/${id}`, { method: 'DELETE' });
        const data = await r.json();
        if (data.status === 'success') loadAccounts();
        else alert(data.message || 'Failed to delete account.');
    } catch (e) {
        alert(e.message);
    }
}

async function toggleTransactions(accountId) {
    const box = document.getElementById(`account-txns-${accountId}`);
    if (!box) return;
    if (!box.classList.contains('d-none')) {
        box.classList.add('d-none');
        return;
    }
    box.classList.remove('d-none');
    box.innerHTML = '<span class="msg-info">Loading...</span>';
    try {
        const r = await fetch(`/api/accounts/${accountId}/transactions`);
        const data = await r.json();
        (data.transactions || []).forEach(t => { _txnCache[t.id] = t; });
        box.innerHTML = _transactionsTableHtml(accountId, data.transactions || []);
    } catch (e) {
        box.innerHTML = `<span class="msg-error">${e.message}</span>`;
    }
}

function _transactionsTableHtml(accountId, txns) {
    if (!txns.length) return '<p class="text-muted small">No transactions yet.</p>';
    const rows = txns.map(t => `
        <tr>
            <td>${t.txn_date}</td>
            <td>${t.txn_type}</td>
            <td>${t.ticker || '—'}</td>
            <td>${t.quantity ?? '—'}</td>
            <td>${t.unit_price ?? '—'}</td>
            <td>${t.fee}</td>
            <td>${t.currency || '—'}</td>
            <td>
                <button type="button" class="btn btn-outline-secondary btn-sm" onclick="editTransaction(${accountId}, ${t.id})">Edit</button>
                <button type="button" class="btn btn-outline-danger btn-sm" onclick="deleteTransaction(${accountId}, ${t.id})">Delete</button>
            </td>
        </tr>`).join('');
    return `
    <table class="table table-sm table-hover">
        <thead><tr><th>Date</th><th>Type</th><th>Ticker</th><th>Qty</th><th>Price</th><th>Fee</th><th>Currency</th><th></th></tr></thead>
        <tbody>${rows}</tbody>
    </table>`;
}

function openTxnModal(accountId = null, txn = null) {
    document.getElementById('txnModalTitle').textContent = txn ? 'Edit Transaction' : 'Add Transaction';
    document.getElementById('txn-id').value = txn ? txn.id : '';
    document.getElementById('txn-account-id').value = accountId || '';
    document.getElementById('txn-account').value = accountId || (Object.values(_accountsCache)[0] || {}).id || '';
    document.getElementById('txn-type').value = txn ? txn.txn_type : 'Buy';
    document.getElementById('txn-ticker').value = txn ? (txn.ticker || '') : '';
    document.getElementById('txn-currency').value = txn ? (txn.currency || '') : '';
    document.getElementById('txn-company-name').value = txn ? (txn.company_name || '') : '';
    document.getElementById('txn-date').value = txn ? txn.txn_date : new Date().toISOString().slice(0, 10);
    document.getElementById('txn-quantity').value = txn ? (txn.quantity ?? '') : '';
    document.getElementById('txn-price').value = txn ? (txn.unit_price ?? '') : '';
    document.getElementById('txn-fee').value = txn ? txn.fee : 0;
    document.getElementById('txn-fx').value = txn ? (txn.exchange_rate ?? '') : '';
    document.getElementById('txn-update-cash').checked = txn ? !!txn.update_cash : true;
    document.getElementById('txn-notes').value = txn ? (txn.notes || '') : '';
    document.getElementById('txn-ticker-result').innerHTML = '';
    document.getElementById('txn-status').innerHTML = '';
    _txnModal().show();
}

function editTransaction(accountId, txnId) {
    openTxnModal(accountId, _txnCache[txnId]);
}

function _lookupTicker() {
    clearTimeout(_txnLookupTimer);
    const tickerInput = document.getElementById('txn-ticker');
    const resultEl = document.getElementById('txn-ticker-result');
    const ticker = tickerInput.value.trim().toUpperCase();
    if (!ticker) {
        resultEl.innerHTML = '';
        document.getElementById('txn-currency').value = '';
        document.getElementById('txn-company-name').value = '';
        return;
    }
    _txnLookupTimer = setTimeout(async () => {
        resultEl.innerHTML = '<span class="msg-info">Looking up...</span>';
        try {
            const r = await fetch(`/api/ticker-lookup?q=${encodeURIComponent(ticker)}`);
            const data = await r.json();
            if (data.status === 'success' && data.found) {
                document.getElementById('txn-currency').value = data.currency || '';
                document.getElementById('txn-company-name').value = data.company_name || '';
                resultEl.innerHTML = `<span class="msg-success">${_escapeHtml(data.company_name)} (${_escapeHtml(data.currency)}, ${_escapeHtml(data.quote_type)})</span>`;
            } else {
                resultEl.innerHTML = '<span class="msg-error">Ticker not found on Yahoo Finance.</span>';
            }
        } catch (e) {
            resultEl.innerHTML = `<span class="msg-error">${e.message}</span>`;
        }
    }, 400);
}

async function submitTransaction() {
    const status = document.getElementById('txn-status');
    const accountId = document.getElementById('txn-account').value;
    if (!accountId) {
        status.innerHTML = '<span class="msg-error">Select an account first.</span>';
        return;
    }
    const txnId = document.getElementById('txn-id').value;
    const body = {
        txn_type: document.getElementById('txn-type').value,
        txn_date: document.getElementById('txn-date').value,
        ticker: document.getElementById('txn-ticker').value.trim() || null,
        company_name: document.getElementById('txn-company-name').value || null,
        currency: document.getElementById('txn-currency').value || null,
        quantity: document.getElementById('txn-quantity').value === '' ? null : parseFloat(document.getElementById('txn-quantity').value),
        unit_price: document.getElementById('txn-price').value === '' ? null : parseFloat(document.getElementById('txn-price').value),
        fee: parseFloat(document.getElementById('txn-fee').value) || 0,
        exchange_rate: document.getElementById('txn-fx').value === '' ? null : parseFloat(document.getElementById('txn-fx').value),
        notes: document.getElementById('txn-notes').value.trim() || null,
        update_cash: document.getElementById('txn-update-cash').checked,
        price_in_pence: (document.getElementById('txn-currency').value || '') === 'GBp',
    };
    if (!body.txn_date) {
        status.innerHTML = '<span class="msg-error">Date is required.</span>';
        return;
    }
    status.innerHTML = '<span class="msg-info">Saving...</span>';
    try {
        const url = txnId ? `/api/accounts/${accountId}/transactions/${txnId}` : `/api/accounts/${accountId}/transactions`;
        const r = await fetch(url, {
            method: txnId ? 'PUT' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
        });
        const data = await r.json();
        if (data.status === 'success') {
            _txnModal().hide();
            const box = document.getElementById(`account-txns-${accountId}`);
            if (box && !box.classList.contains('d-none')) {
                box.classList.add('d-none');
                toggleTransactions(parseInt(accountId, 10));
            }
        } else {
            status.innerHTML = `<span class="msg-error">${data.message || 'Failed'}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${e.message}</span>`;
    }
}

async function deleteTransaction(accountId, txnId) {
    if (!confirm('Delete this transaction?')) return;
    try {
        const r = await fetch(`/api/accounts/${accountId}/transactions/${txnId}`, { method: 'DELETE' });
        const data = await r.json();
        if (data.status === 'success') {
            const box = document.getElementById(`account-txns-${accountId}`);
            box.classList.add('d-none');
            toggleTransactions(accountId);
        } else {
            alert(data.message || 'Failed to delete transaction.');
        }
    } catch (e) {
        alert(e.message);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    loadAccounts();
    document.getElementById('txn-ticker').addEventListener('input', _lookupTicker);
});
