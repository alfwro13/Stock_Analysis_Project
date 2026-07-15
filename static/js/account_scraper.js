function _scraperModal() {
    return bootstrap.Modal.getOrCreateInstance(document.getElementById('scraperModal'));
}

function openScraperModal(accountId, accountObj) {
    const acc = accountObj || (typeof _accountsCache !== 'undefined' ? _accountsCache[accountId] : null);
    document.getElementById('scraper-account-id').value = accountId;
    document.getElementById('scraper-url').value = acc ? (acc.scraper_url || '') : '';
    document.getElementById('scraper-selector').value = acc ? (acc.scraper_selector || '') : '';
    document.getElementById('scraper-headers').value = acc ? (acc.scraper_headers || '{}') : '{}';
    document.getElementById('scraper-time').value = acc ? (acc.scrape_time || '02:00') : '02:00';
    document.getElementById('scraper-enabled').checked = !!(acc && acc.scraper_enabled);
    document.getElementById('scraper-csv').value = '';
    document.getElementById('scraper-config-status').innerHTML = '';
    document.getElementById('scraper-csv-status').innerHTML = '';
    _scraperModal().show();
}

function _parseScraperHeaders(status) {
    const raw = document.getElementById('scraper-headers').value.trim() || '{}';
    try {
        return JSON.parse(raw);
    } catch (e) {
        status.innerHTML = '<span class="msg-error">HTTP Request Headers must be valid JSON.</span>';
        return null;
    }
}

async function testScraperConfig() {
    const status = document.getElementById('scraper-config-status');
    const headers = _parseScraperHeaders(status);
    if (headers === null) return;
    const url = document.getElementById('scraper-url').value.trim();
    const selector = document.getElementById('scraper-selector').value.trim();
    if (!url || !selector) {
        status.innerHTML = '<span class="msg-error">Url and Selector are required.</span>';
        return;
    }
    const accountId = document.getElementById('scraper-account-id').value;
    status.innerHTML = '<span class="msg-info">Testing...</span>';
    try {
        const r = await fetch(`/api/accounts/${accountId}/scraper/test`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ url, selector, headers }),
        });
        const data = await r.json();
        status.innerHTML = data.status === 'success'
            ? `<span class="msg-success">Extracted price: ${data.price}</span>`
            : `<span class="msg-error">${escapeHtml(data.message || 'Test failed.')}</span>`;
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${escapeHtml(e.message)}</span>`;
    }
}

async function saveScraperConfig() {
    const status = document.getElementById('scraper-config-status');
    const headers = _parseScraperHeaders(status);
    if (headers === null) return;
    const url = document.getElementById('scraper-url').value.trim();
    const selector = document.getElementById('scraper-selector').value.trim();
    if (!url || !selector) {
        status.innerHTML = '<span class="msg-error">Url and Selector are required.</span>';
        return;
    }
    const accountId = document.getElementById('scraper-account-id').value;
    status.innerHTML = '<span class="msg-info">Saving...</span>';
    try {
        const r = await fetch(`/api/accounts/${accountId}/scraper-config`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                scraper_url: url,
                scraper_selector: selector,
                scraper_headers: headers,
                scrape_time: document.getElementById('scraper-time').value || '02:00',
                scraper_enabled: document.getElementById('scraper-enabled').checked,
            }),
        });
        const data = await r.json();
        if (data.status === 'success') {
            status.innerHTML = '<span class="msg-success">Saved.</span>';
            if (typeof loadAccounts === 'function') loadAccounts();
        } else {
            status.innerHTML = `<span class="msg-error">${escapeHtml(data.message || 'Failed to save.')}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${escapeHtml(e.message)}</span>`;
    }
}

async function runScraperNow() {
    const status = document.getElementById('scraper-config-status');
    const accountId = document.getElementById('scraper-account-id').value;
    status.innerHTML = '<span class="msg-info">Scraping...</span>';
    try {
        const r = await fetch(`/api/accounts/${accountId}/scraper/run-now`, { method: 'POST' });
        const data = await r.json();
        if (data.status === 'success') {
            status.innerHTML = `<span class="msg-success">Recorded price: ${data.price}</span>`;
            if (typeof window.onTransactionChanged === 'function') window.onTransactionChanged();
        } else {
            status.innerHTML = `<span class="msg-error">${escapeHtml(data.message || 'Scrape failed.')}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${escapeHtml(e.message)}</span>`;
    }
}

async function importScraperCsv() {
    const status = document.getElementById('scraper-csv-status');
    const csvText = document.getElementById('scraper-csv').value;
    if (!csvText.trim()) {
        status.innerHTML = '<span class="msg-error">Paste CSV data first.</span>';
        return;
    }
    const accountId = document.getElementById('scraper-account-id').value;
    status.innerHTML = '<span class="msg-info">Importing...</span>';
    try {
        const r = await fetch(`/api/accounts/${accountId}/price-history/import-csv`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ csv_text: csvText }),
        });
        const data = await r.json();
        if (data.status === 'success') {
            status.innerHTML = `<span class="msg-success">${escapeHtml(data.message)}</span>`;
            if (typeof window.onTransactionChanged === 'function') window.onTransactionChanged();
        } else {
            status.innerHTML = `<span class="msg-error">${escapeHtml(data.message || 'Import failed.')}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${escapeHtml(e.message)}</span>`;
    }
}
