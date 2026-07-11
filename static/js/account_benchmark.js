function _benchmarkModal() {
    return bootstrap.Modal.getOrCreateInstance(document.getElementById('benchmarkModal'));
}

function _benchmarkTickerRowHtml(ticker, displayName) {
    return `<div class="benchmark-ticker-row flex-gap-15 mb-10">
        <input type="text" class="benchmark-ticker-input" placeholder="Ticker (e.g. URTH)" value="${_escapeHtml(ticker || '')}">
        <input type="text" class="benchmark-name-input" placeholder="Display Name" value="${_escapeHtml(displayName || '')}">
        <button type="button" class="btn-danger" onclick="removeBenchmarkTickerRow(this)">&times;</button>
    </div>`;
}

function addBenchmarkTickerRow(ticker, displayName) {
    document.getElementById('benchmark-ticker-rows').insertAdjacentHTML('beforeend', _benchmarkTickerRowHtml(ticker, displayName));
}

function removeBenchmarkTickerRow(btn) {
    btn.closest('.benchmark-ticker-row').remove();
}

async function openBenchmarkModal(accountId) {
    document.getElementById('benchmark-account-id').value = accountId;
    document.getElementById('benchmark-config-status').innerHTML = '';
    const rows = document.getElementById('benchmark-ticker-rows');
    rows.innerHTML = '';
    document.getElementById('benchmark-cpi-target').value = 4.0;
    try {
        const r = await fetch(`/api/accounts/${accountId}/benchmark-config`);
        const data = await r.json();
        if (data.status === 'success') {
            document.getElementById('benchmark-cpi-target').value = data.cpi_target_pct;
            data.tickers.forEach(t => addBenchmarkTickerRow(t.ticker, t.display_name));
        }
    } catch (e) {
        document.getElementById('benchmark-config-status').innerHTML = `<span class="msg-error">${e.message}</span>`;
    }
    _benchmarkModal().show();
}

async function saveBenchmarkConfig() {
    const status = document.getElementById('benchmark-config-status');
    const accountId = document.getElementById('benchmark-account-id').value;
    const cpiTarget = parseFloat(document.getElementById('benchmark-cpi-target').value);
    if (Number.isNaN(cpiTarget)) {
        status.innerHTML = '<span class="msg-error">UK CPI + Target (%) must be a number.</span>';
        return;
    }
    const tickers = Array.from(document.querySelectorAll('#benchmark-ticker-rows .benchmark-ticker-row'))
        .map(row => ({
            ticker: row.querySelector('.benchmark-ticker-input').value.trim(),
            display_name: row.querySelector('.benchmark-name-input').value.trim(),
        }))
        .filter(t => t.ticker && t.display_name);
    status.innerHTML = '<span class="msg-info">Saving...</span>';
    try {
        const r = await fetch(`/api/accounts/${accountId}/benchmark-config`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ cpi_target_pct: cpiTarget, tickers }),
        });
        const data = await r.json();
        if (data.status === 'success') {
            status.innerHTML = '<span class="msg-success">Saved.</span>';
        } else {
            status.innerHTML = `<span class="msg-error">${data.message || 'Failed to save.'}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${e.message}</span>`;
    }
}
