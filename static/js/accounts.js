let _accountsCache = {};
let _txnCache = {};
let _txnLookupTimer = null;

function _escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _formatThousands(value) {
    return Number(value).toFixed(2).replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
}

function _accountModal() {
    return bootstrap.Modal.getOrCreateInstance(document.getElementById('accountModal'));
}

function _txnModal() {
    return bootstrap.Modal.getOrCreateInstance(document.getElementById('txnModal'));
}

function _csvImportModal() {
    return bootstrap.Modal.getOrCreateInstance(document.getElementById('csvImportModal'));
}

async function loadAccounts() {
    const list = document.getElementById('accounts-list');
    try {
        const r = await fetch('/api/accounts');
        const data = await r.json();
        _accountsCache = {};
        (data.accounts || []).forEach(a => { _accountsCache[a.id] = a; });
        _populateAccountSelect();
        if (!list) return;
        if (!data.accounts || data.accounts.length === 0) {
            list.innerHTML = '<div class="col-12"><p class="text-muted">No accounts yet — create one to start tracking transactions.</p></div>';
            return;
        }
        list.innerHTML = data.accounts.map(_accountCardHtml).join('');
    } catch (e) {
        if (list) list.innerHTML = `<div class="col-12"><span class="msg-error">Failed to load accounts: ${e.message}</span></div>`;
    }
}

const _ACCOUNT_CASH_TILE_LABELS = { House: 'Initial Purchase', Pension: 'Current Balance' };

function _accountCashLine(acc) {
    const label = _ACCOUNT_CASH_TILE_LABELS[acc.account_type];
    if (acc.account_type === 'Pension') {
        return `${label}: ${_formatThousands(acc.current_balance ?? acc.initial_cash)} ${_escapeHtml(acc.currency)}`;
    }
    if (acc.account_type === 'House') {
        const current = acc.current_balance ?? acc.initial_cash;
        const gain = acc.initial_cash ? ((current - acc.initial_cash) / acc.initial_cash) * 100 : 0;
        return `${label}: ${_formatThousands(acc.initial_cash)} ${_escapeHtml(acc.currency)} &middot; Current Estimate: ${_formatThousands(current)} ${_escapeHtml(acc.currency)} &middot; Value gain: ${gain.toFixed(2)}%`;
    }
    return `${label}: ${acc.initial_cash} ${_escapeHtml(acc.currency)}`;
}

const _WATCHLIST_BREAKDOWN_LABELS = { equity: 'Equity', etf: 'ETF', fund: 'Fund', other: 'Other' };

function _watchlistBreakdownText(acc) {
    const breakdown = acc.watchlist_breakdown || {};
    const parts = Object.keys(_WATCHLIST_BREAKDOWN_LABELS)
        .filter(key => breakdown[key])
        .map(key => `${breakdown[key]} ${_WATCHLIST_BREAKDOWN_LABELS[key]}`);
    return parts.length ? ` (${parts.join(', ')})` : '';
}

function _accountStatsLine(acc) {
    if (acc.account_type === 'Watchlist') {
        const count = acc.watchlist_count ?? 0;
        return `${count} ticker${count === 1 ? '' : 's'}${_watchlistBreakdownText(acc)}`;
    }
    if (acc.account_type === 'Trading') {
        return `Holdings: ${acc.holdings_count ?? 0} &middot; Equity: ${_formatThousands(acc.equity_value ?? 0)} ${_escapeHtml(acc.currency)} &middot; Cash: ${_formatThousands(acc.cash_balance ?? 0)} ${_escapeHtml(acc.currency)}`;
    }
    return _accountCashLine(acc);
}

function _scraperStatusBadgeHtml(acc) {
    if (!acc.scraper_enabled) return '';
    const status = acc.scraper_last_status;
    if (status === 'success') return '<span class="badge bg-success" title="Last scraper run succeeded.">&#9679;</span>';
    if (status === 'error') return '<span class="badge bg-danger" title="Last scraper run failed.">&#9679;</span>';
    return '';
}

const _ACCOUNT_DETAIL_URL_SUFFIX = { Pension: '/pension', House: '/house' };

function _accountCardHtml(acc) {
    const isWatchlist = acc.account_type === 'Watchlist';
    const isPension = acc.account_type === 'Pension';
    const isScraperType = acc.account_type === 'House' || isPension;
    const isTrading = acc.account_type === 'Trading';
    const detailUrl = `/accounts/${acc.id}${_ACCOUNT_DETAIL_URL_SUFFIX[acc.account_type] || ''}`;
    return `
    <div class="col-12 col-lg-6">
        <div class="guide-card h-100" id="account-card-${acc.id}">
            <div class="d-flex justify-content-between align-items-start">
                <div>
                    <h4 class="mb-0">${_escapeHtml(acc.name)} <span class="account-badge">${_escapeHtml(acc.account_type)}</span></h4>
                    <p class="text-muted account-stats-line mb-0">${_accountStatsLine(acc)}</p>
                    ${acc.note ? `<p class="text-secondary small mb-0">${_escapeHtml(acc.note)}</p>` : ''}
                </div>
                <div class="d-flex gap-2">
                    <button type="button" class="btn btn-outline-secondary btn-sm" onclick="openAccountModal(${acc.id})">Edit</button>
                    ${isScraperType ? `<button type="button" class="btn btn-outline-secondary btn-sm" onclick="openScraperModal(${acc.id})">&#9881; Scraper ${_scraperStatusBadgeHtml(acc)}</button>` : ''}
                </div>
            </div>
            <div class="d-flex gap-2 mt-3">
                <a href="${detailUrl}" class="btn btn-outline-primary btn-sm">View Details</a>
                ${isTrading ? `<a href="/portfolio?account_id=acct:${acc.id}&xray=1" class="btn btn-outline-info btn-sm">&#128302; X-ray</a>` : ''}
                ${isWatchlist || isScraperType ? '' : `
                <button type="button" class="btn btn-primary btn-sm" onclick="openTxnModal(${acc.id})">+ Add Transaction</button>
                <button type="button" class="btn btn-outline-secondary btn-sm" onclick="toggleTransactions(${acc.id})">Show Transactions</button>
                <button type="button" class="btn btn-outline-secondary btn-sm" onclick="importCsv(${acc.id})">Import from CSV</button>
                <a href="/api/accounts/${acc.id}/export" class="btn btn-outline-secondary btn-sm">Export to CSV</a>`}
            </div>
            <div id="account-import-status-${acc.id}" class="status-msg-sm mt-2"></div>
            <div id="account-txns-${acc.id}" class="mt-3 d-none"></div>
        </div>
    </div>`;
}

function _populateAccountSelect() {
    const sel = document.getElementById('txn-account');
    const current = sel.value;
    sel.innerHTML = Object.values(_accountsCache)
        .filter(a => a.account_type !== 'Watchlist')
        .map(a => `<option value="${a.id}">${_escapeHtml(a.name)}</option>`).join('');
    if (current) sel.value = current;
}

function _populateCurrencySelect() {
    const sel = document.getElementById('txn-currency');
    sel.innerHTML = (window.ACCOUNT_CURRENCIES || ['GBP', 'GBp', 'USD', 'EUR'])
        .map(c => `<option value="${_escapeHtml(c)}">${_escapeHtml(c)}</option>`).join('');
}

function _setCurrencySelectValue(currency) {
    const sel = document.getElementById('txn-currency');
    if (!currency) return;
    if (!Array.from(sel.options).some(o => o.value === currency)) {
        sel.insertAdjacentHTML('beforeend', `<option value="${_escapeHtml(currency)}">${_escapeHtml(currency)}</option>`);
    }
    sel.value = currency;
}

function _populateToAccountSelect() {
    const sourceId = document.getElementById('txn-account').value;
    const sel = document.getElementById('txn-to-account');
    const current = sel.value;
    sel.innerHTML = Object.values(_accountsCache)
        .filter(a => String(a.id) !== String(sourceId) && a.account_type !== 'Watchlist')
        .map(a => `<option value="${a.id}">${_escapeHtml(a.name)}</option>`).join('');
    if (current) sel.value = current;
}

const _ACCOUNT_FIELD_LABELS = {
    House: { cash: 'Purchase Value', date: 'Purchase Date' },
    Pension: { cash: 'Opening Balance', date: 'Opening Balance Date' },
};

function _updateAccountFieldLabelsForType() {
    const type = document.getElementById('acct-type').value;
    const labels = _ACCOUNT_FIELD_LABELS[type] || { cash: 'Initial Cash', date: 'Opening Date' };
    document.getElementById('acct-cash-label').textContent = labels.cash;
    document.getElementById('acct-opened-date-label').textContent = labels.date;
    document.getElementById('acct-pension-start-date-group').classList.toggle('d-none', type !== 'Pension');
    document.getElementById('acct-opening-balance-units-group').classList.toggle('d-none', type !== 'Pension');
    document.getElementById('acct-pension-ticker-label-group').classList.toggle('d-none', type !== 'Pension');
}

function openAccountModal(id = null) {
    const acc = id ? _accountsCache[id] : null;
    const isEditing = !!acc;
    document.getElementById('accountModalTitle').textContent = acc ? 'Edit Account' : 'New Account';
    document.getElementById('acct-id').value = acc ? acc.id : '';
    document.getElementById('acct-name').value = acc ? acc.name : '';
    document.getElementById('acct-currency').value = acc ? acc.currency : window.BASE_CURRENCY;
    document.getElementById('acct-type').value = acc ? acc.account_type : 'Trading';
    document.getElementById('acct-type').classList.toggle('d-none', isEditing);
    document.getElementById('acct-type-readonly').classList.toggle('d-none', !isEditing);
    document.getElementById('acct-type-readonly').value = acc ? acc.account_type : '';
    document.getElementById('acct-cash').value = acc ? acc.initial_cash : 0;
    document.getElementById('acct-opening-balance-units').value = acc ? (acc.opening_balance_units ?? '') : '';
    document.getElementById('acct-opened-date').value = acc ? (acc.opened_date || '') : '';
    document.getElementById('acct-pension-start-date').value = acc ? (acc.pension_start_date || '') : '';
    document.getElementById('acct-pension-ticker-label').value = acc ? (acc.pension_ticker_label || '') : '';
    document.getElementById('acct-note').value = acc ? (acc.note || '') : '';
    document.getElementById('account-status').innerHTML = '';
    const isWatchlist = isEditing && acc.account_type === 'Watchlist';
    document.getElementById('acct-currency-group').classList.toggle('d-none', isWatchlist);
    document.getElementById('acct-cash-group').classList.toggle('d-none', isWatchlist);
    document.getElementById('acct-opened-date-group').classList.toggle('d-none', isWatchlist);
    _updateAccountFieldLabelsForType();
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
    const existingAcc = id ? _accountsCache[id] : null;
    const body = {
        name,
        currency,
        account_type: existingAcc ? existingAcc.account_type : document.getElementById('acct-type').value,
        initial_cash: parseFloat(document.getElementById('acct-cash').value) || 0,
        opening_balance_units: document.getElementById('acct-opening-balance-units').value === ''
            ? null : parseFloat(document.getElementById('acct-opening-balance-units').value),
        opened_date: document.getElementById('acct-opened-date').value || null,
        pension_start_date: document.getElementById('acct-pension-start-date').value || null,
        pension_ticker_label: document.getElementById('acct-pension-ticker-label').value.trim() || null,
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

function _skippedRowsHtml(skippedRows) {
    if (!skippedRows || skippedRows.length === 0) return '';
    const items = skippedRows.map(r =>
        `<li>${_escapeHtml(r.date || '?')} — ${_escapeHtml(r.ticker || '?')}: ${_escapeHtml(r.reason)}</li>`
    ).join('');
    return `<ul class="small text-muted mb-0 mt-1">${items}</ul>`;
}

function importCsv(accountId) {
    document.getElementById('csv-import-account-id').value = accountId;
    document.getElementById('csv-import-file').value = '';
    document.getElementById('csv-import-status').innerHTML = '';
    _csvImportModal().show();
}

async function confirmImportCsv() {
    const accountId = document.getElementById('csv-import-account-id').value;
    const fileInput = document.getElementById('csv-import-file');
    const status = document.getElementById('csv-import-status');
    const file = fileInput.files[0];
    if (!file) {
        status.innerHTML = '<span class="msg-error">Choose a CSV file.</span>';
        return;
    }
    status.innerHTML = '<span class="msg-info">Importing...</span>';
    const formData = new FormData();
    formData.append('file', file);
    try {
        const r = await fetch(`/api/accounts/${accountId}/import-csv`, { method: 'POST', body: formData });
        const data = await r.json();
        const pageStatus = document.getElementById(`account-import-status-${accountId}`);
        if (data.status === 'success') {
            if (pageStatus) {
                pageStatus.innerHTML = `<span class="msg-success">${_escapeHtml(data.message)}</span>`
                    + _skippedRowsHtml(data.skipped_rows);
            }
            _csvImportModal().hide();
            const box = document.getElementById(`account-txns-${accountId}`);
            if (box && !box.classList.contains('d-none')) {
                box.classList.add('d-none');
                toggleTransactions(accountId);
            }
            if (typeof window.onTransactionChanged === 'function') window.onTransactionChanged();
        } else {
            status.innerHTML = `<span class="msg-error">${_escapeHtml(data.message || 'Import failed.')}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${_escapeHtml(e.message)}</span>`;
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

function _updateTxnFieldsForType() {
    const type = document.getElementById('txn-type').value;
    const isCash = type === 'Cash';
    const isTransfer = type === 'Transfer';
    const hideAssetFields = isCash || isTransfer;
    document.getElementById('txn-ticker-group').classList.toggle('d-none', hideAssetFields);
    document.getElementById('txn-currency-group').classList.toggle('d-none', hideAssetFields);
    document.getElementById('txn-quantity-group').classList.toggle('d-none', hideAssetFields);
    document.getElementById('txn-to-account-group').classList.toggle('d-none', !isTransfer);
    document.getElementById('txn-price-hint').classList.toggle('d-none', !hideAssetFields);
    if (isTransfer) _populateToAccountSelect();
    const abbr = document.getElementById('txn-price-abbr');
    const hint = document.getElementById('txn-price-hint');
    if (isTransfer) {
        abbr.textContent = 'Amount';
        abbr.title = 'Amount to transfer between the two accounts.';
        hint.textContent = 'Enter a positive amount — the direction is set by the Account / To Account fields above.';
    } else if (isCash) {
        abbr.textContent = 'Amount';
        abbr.title = 'Positive amount = deposit. Negative amount = withdrawal.';
        hint.textContent = 'Positive amount = deposit. Negative amount = withdrawal.';
    } else {
        abbr.textContent = 'Unit Price';
        abbr.title = "Per-share price in the transaction's native trade currency.";
    }
    _refreshTxnCurrencyUI();
    _updateTxnTotalPreview();
}

function _refreshTxnCurrencyUI() {
    const type = document.getElementById('txn-type').value;
    const hideAssetFields = type === 'Cash' || type === 'Transfer';
    const base = window.BASE_CURRENCY || '';
    // Cash/Transfer are always base-currency — the (hidden) currency select's last value is stale.
    const currency = hideAssetFields ? base : document.getElementById('txn-currency').value;
    const feeLabel = document.getElementById('txn-fee-currency-label');
    if (feeLabel) feeLabel.textContent = currency ? `(${currency})` : '';

    const showFx = !hideAssetFields && currency && currency !== base;
    document.getElementById('txn-fx-group').classList.toggle('d-none', !showFx);
}

async function _onTxnCurrencyOrDateChange() {
    _refreshTxnCurrencyUI();
    const currency = document.getElementById('txn-currency').value;
    const base = window.BASE_CURRENCY || '';
    const type = document.getElementById('txn-type').value;
    const hideAssetFields = type === 'Cash' || type === 'Transfer';
    const fxInput = document.getElementById('txn-fx');
    if (hideAssetFields) {
        _updateTxnTotalPreview();
        return;
    }
    if (!currency || currency === base) {
        fxInput.value = 1.0;
        _updateTxnTotalPreview();
        return;
    }
    const date = document.getElementById('txn-date').value || new Date().toISOString().slice(0, 10);
    try {
        const r = await fetch(`/api/fx-rate?currency=${encodeURIComponent(currency)}&date=${encodeURIComponent(date)}`);
        const data = await r.json();
        if (data.status === 'success') fxInput.value = data.rate;
    } catch (e) {
        // leave whatever was there — user can still enter a rate manually
    }
    _updateTxnTotalPreview();
}

function _updateTxnTotalPreview() {
    const preview = document.getElementById('txn-total-preview');
    if (!preview) return;
    const type = document.getElementById('txn-type').value;
    const base = window.BASE_CURRENCY || '';
    if (type === 'Transfer') {
        const amount = parseFloat(document.getElementById('txn-price').value);
        preview.textContent = isNaN(amount) ? '' : `Total: ${amount.toFixed(2)} ${base}`;
        return;
    }
    const qty = document.getElementById('txn-quantity').value === '' ? 1.0 : parseFloat(document.getElementById('txn-quantity').value);
    const price = parseFloat(document.getElementById('txn-price').value);
    const fx = document.getElementById('txn-fx').value === '' ? 1.0 : parseFloat(document.getElementById('txn-fx').value);
    const currency = document.getElementById('txn-currency').value || base;
    if (isNaN(price) || isNaN(qty) || isNaN(fx)) {
        preview.textContent = '';
        return;
    }
    const totalNative = qty * price;
    const totalBase = totalNative * fx;
    preview.textContent = `Total: ${totalNative.toFixed(2)} ${currency} = ${totalBase.toFixed(2)} ${base}`;
}

function openTxnModal(accountId = null, txn = null) {
    document.getElementById('txnModalTitle').textContent = txn ? 'Edit Transaction' : 'Add Transaction';
    document.getElementById('txn-id').value = txn ? txn.id : '';
    document.getElementById('txn-account-id').value = accountId || '';
    document.getElementById('txn-account').value = accountId || (Object.values(_accountsCache)[0] || {}).id || '';
    document.getElementById('txn-type').value = txn ? txn.txn_type : 'Buy';
    document.getElementById('txn-ticker').value = txn ? (txn.ticker || '') : '';
    document.getElementById('txn-isin').value = txn ? (txn.isin || '') : '';
    _setCurrencySelectValue(txn ? (txn.currency || window.BASE_CURRENCY) : window.BASE_CURRENCY);
    document.getElementById('txn-company-name').value = txn ? (txn.company_name || '') : '';
    document.getElementById('txn-date').value = txn ? txn.txn_date : new Date().toISOString().slice(0, 10);
    document.getElementById('txn-quantity').value = txn ? (txn.quantity ?? '') : '';
    document.getElementById('txn-price').value = txn ? (txn.unit_price ?? '') : '';
    document.getElementById('txn-fee').value = txn ? txn.fee : 0;
    document.getElementById('txn-fx').value = txn ? (txn.exchange_rate ?? '') : '';
    document.getElementById('txn-notes').value = txn ? (txn.notes || '') : '';
    document.getElementById('txn-ticker-result').innerHTML = '';
    document.getElementById('txn-status').innerHTML = '';
    _updateTxnFieldsForType();
    _txnModal().show();
}

function editTransaction(accountId, txnId) {
    const txn = _txnCache[txnId];
    if (txn && txn.txn_type === 'Transfer') {
        alert("Transfers can't be edited — delete it and record a new one instead.");
        return;
    }
    openTxnModal(accountId, txn);
}

function _lookupTicker() {
    clearTimeout(_txnLookupTimer);
    const tickerInput = document.getElementById('txn-ticker');
    const resultEl = document.getElementById('txn-ticker-result');
    const ticker = tickerInput.value.trim().toUpperCase();
    if (!ticker) {
        resultEl.innerHTML = '';
        document.getElementById('txn-company-name').value = '';
        return;
    }
    _txnLookupTimer = setTimeout(async () => {
        resultEl.innerHTML = '<span class="msg-info">Looking up...</span>';
        try {
            const r = await fetch(`/api/ticker-lookup?q=${encodeURIComponent(ticker)}`);
            const data = await r.json();
            if (data.status === 'success' && data.found) {
                _setCurrencySelectValue(data.currency);
                document.getElementById('txn-company-name').value = data.company_name || '';
                resultEl.innerHTML = `<span class="msg-success">${_escapeHtml(data.company_name)} (${_escapeHtml(data.currency)}, ${_escapeHtml(data.quote_type)})</span>`;
                _onTxnCurrencyOrDateChange();
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
    const txnType = document.getElementById('txn-type').value;
    const txnDate = document.getElementById('txn-date').value;
    if (!txnDate) {
        status.innerHTML = '<span class="msg-error">Date is required.</span>';
        return;
    }

    if (txnType === 'Transfer') {
        const toAccountId = document.getElementById('txn-to-account').value;
        const amount = parseFloat(document.getElementById('txn-price').value);
        if (!toAccountId) {
            status.innerHTML = '<span class="msg-error">Select a destination account.</span>';
            return;
        }
        if (!amount || amount <= 0) {
            status.innerHTML = '<span class="msg-error">Enter a positive amount.</span>';
            return;
        }
        status.innerHTML = '<span class="msg-info">Saving...</span>';
        try {
            const r = await fetch(`/api/accounts/${accountId}/transfer`, {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    to_account_id: parseInt(toAccountId, 10),
                    amount,
                    txn_date: txnDate,
                    fee: parseFloat(document.getElementById('txn-fee').value) || 0,
                    notes: document.getElementById('txn-notes').value.trim() || null,
                }),
            });
            const data = await r.json();
            if (data.status === 'success') {
                _txnModal().hide();
                if (typeof window.onTransactionChanged === 'function') window.onTransactionChanged();
            } else {
                status.innerHTML = `<span class="msg-error">${data.message || 'Failed'}</span>`;
            }
        } catch (e) {
            status.innerHTML = `<span class="msg-error">${e.message}</span>`;
        }
        return;
    }

    const body = {
        txn_type: txnType,
        txn_date: txnDate,
        ticker: document.getElementById('txn-ticker').value.trim() || null,
        isin: document.getElementById('txn-isin').value.trim() || null,
        company_name: document.getElementById('txn-company-name').value || null,
        currency: document.getElementById('txn-currency').value || null,
        quantity: document.getElementById('txn-quantity').value === '' ? null : parseFloat(document.getElementById('txn-quantity').value),
        unit_price: document.getElementById('txn-price').value === '' ? null : parseFloat(document.getElementById('txn-price').value),
        fee: parseFloat(document.getElementById('txn-fee').value) || 0,
        exchange_rate: document.getElementById('txn-fx').value === '' ? null : parseFloat(document.getElementById('txn-fx').value),
        notes: document.getElementById('txn-notes').value.trim() || null,
        update_cash: true,
        price_in_pence: (document.getElementById('txn-currency').value || '') === 'GBp',
    };
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
            if (typeof window.onTransactionChanged === 'function') window.onTransactionChanged();
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
            if (box) {
                box.classList.add('d-none');
                toggleTransactions(accountId);
            }
            if (typeof window.onTransactionChanged === 'function') window.onTransactionChanged();
        } else {
            alert(data.message || 'Failed to delete transaction.');
        }
    } catch (e) {
        alert(e.message);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    loadAccounts();
    _populateCurrencySelect();
    document.getElementById('txn-ticker').addEventListener('input', _lookupTicker);
});
