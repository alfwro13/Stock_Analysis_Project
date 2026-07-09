let _registryCache = {};

function _regDomId(ticker) {
    return ticker.replace(/[^a-zA-Z0-9]/g, '_');
}

function _regExchangeOptionsHtml(currentValue) {
    const known = window.MARKET_EXCHANGE_LIST || [];
    const options = ['<option value="">— None —</option>'];
    // A row saved before this dropdown existed (free-text field) can hold a value that isn't
    // a recognized exchange key — e.g. a typo or a different case than "KRX" — which silently
    // fell back to "always open" instead of a real open/closed check. Surface it as its own
    // clearly-unrecognized option rather than silently dropping it, so the mistake is visible
    // and the user can pick the correct exchange instead of re-typing a guess.
    if (currentValue && !known.includes(currentValue)) {
        options.push(`<option value="${currentValue}" selected>${currentValue} (unrecognized — please re-select)</option>`);
    }
    known.forEach(ex => {
        options.push(`<option value="${ex}" ${ex === currentValue ? 'selected' : ''}>${ex}</option>`);
    });
    return options.join('');
}

function _regEditFormHtml(row) {
    const id = _regDomId(row.ticker);
    const p = `reg-edit-${id}`;
    return `
    <div id="reg-edit-form-${id}" style="display:none;margin-top:12px;background:#111;padding:14px;border-radius:6px;">
        <h6 style="color:#b366ff;margin:0 0 10px;">Edit: ${row.ticker}</h6>
        <div class="flex-gap-15">
            <div class="form-group flex-1 mb-0">
                <label>Display Name</label>
                <input type="text" id="${p}-display-name" value="${(row.display_name || '').replace(/"/g, '&quot;')}">
            </div>
            <div class="form-group flex-1 mb-0">
                <label>Region</label>
                <select id="${p}-region">
                    ${['Europe', 'US', 'Asia', 'Commodities_FX'].map(r => `<option value="${r}" ${row.region === r ? 'selected' : ''}>${r === 'Commodities_FX' ? 'Commodities & FX' : r}</option>`).join('')}
                </select>
            </div>
        </div>
        <div class="flex-gap-15 mt-10">
            <div class="form-group flex-1 mb-0">
                <label>Asset Type</label>
                <select id="${p}-asset-type">
                    ${['Index', 'Commodity', 'FX', 'Rate', 'Volatility'].map(t => `<option value="${t}" ${row.asset_type === t ? 'selected' : ''}>${t}</option>`).join('')}
                </select>
            </div>
            <div class="form-group flex-1 mb-0">
                <label>Exchange</label>
                <select id="${p}-exchange">
                    ${_regExchangeOptionsHtml(row.exchange)}
                </select>
            </div>
            <div class="form-group flex-1 mb-0">
                <label>Currency</label>
                <input type="text" id="${p}-currency" value="${row.currency || 'USD'}" style="text-transform:uppercase;" maxlength="3">
            </div>
        </div>
        <div class="flex-gap-15 mt-10">
            <div class="form-group flex-1 mb-0">
                <label>Future Ticker</label>
                <input type="text" id="${p}-future-ticker" value="${row.future_ticker || ''}" style="text-transform:uppercase;">
            </div>
            <div class="form-group flex-1 mb-0">
                <label>Future Display Name</label>
                <input type="text" id="${p}-future-display-name" value="${row.future_display_name || ''}">
            </div>
            <div class="form-group mb-0" style="justify-content:flex-end;padding-bottom:2px;">
                <label>&nbsp;</label>
                <div class="checkbox-group mb-0">
                    <input type="checkbox" id="${p}-invert-color" ${row.invert_color ? 'checked' : ''}>
                    <label for="${p}-invert-color" style="font-size:13px;">Invert color</label>
                </div>
                <div class="checkbox-group mb-0">
                    <input type="checkbox" id="${p}-is-pulse-tile" ${row.is_pulse_tile ? 'checked' : ''}>
                    <label for="${p}-is-pulse-tile" style="font-size:13px;">Static Market Pulse tile</label>
                </div>
                <div class="checkbox-group mb-0">
                    <input type="checkbox" id="${p}-is-pulse-mobile" ${row.is_pulse_mobile ? 'checked' : ''}>
                    <label for="${p}-is-pulse-mobile" style="font-size:13px;">Show on mobile</label>
                </div>
            </div>
        </div>
        <div class="flex-gap-15 mt-12">
            <button type="button" class="btn-save flex-1" style="max-width:140px;" onclick="saveRegistryEdit('${row.ticker}')">Save Changes</button>
            <button type="button" class="btn-test mt-0" style="background:#333;" onclick="toggleRegistryEditForm('${row.ticker}')">Cancel</button>
        </div>
        <div id="reg-edit-status-${id}" class="status-msg-sm mt-8"></div>
    </div>`;
}

async function loadRegistryList() {
    const list = document.getElementById('markets-registry-list');
    if (!list) return;
    try {
        const r = await fetch('/api/markets/registry');
        const data = await r.json();
        const rows = data.registry || [];
        _registryCache = {};
        rows.forEach(row => { _registryCache[row.ticker] = row; });
        if (!rows.length) {
            list.innerHTML = '<p class="text-muted text-sm">No tickers in the registry yet.</p>';
            return;
        }
        list.innerHTML = rows.map(row => {
            const id = _regDomId(row.ticker);
            const disabledBadge = row.enabled ? '' : ' <span class="text-muted text-sm">(disabled)</span>';
            return `
            <div id="reg-row-${id}" style="background:#1e1e1e;padding:12px;border-radius:5px;margin-bottom:10px;">
                <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;">
                    <div>
                        <strong style="color:#b366ff;">${row.ticker}</strong>
                        <span style="color:#ccc;margin-left:8px;">${row.display_name}</span>${disabledBadge}
                        <span style="color:#666;font-size:12px;margin-left:8px;">${row.region} · ${row.asset_type}${row.future_ticker ? ' · has future' : ''}</span>
                    </div>
                    <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                        <a href="/index/${encodeURIComponent(row.ticker)}" class="btn-test mt-0" style="font-size:11px;padding:4px 10px;color:#fff;text-decoration:none;">View</a>
                        <button type="button" class="btn-test mt-0" style="font-size:11px;padding:4px 10px;background:#2a3a5a;" onclick="toggleRegistryEditForm('${row.ticker}')">&#9998; Edit</button>
                        <button type="button" class="btn-danger" style="font-size:11px;padding:4px 10px;" onclick="deleteRegistryTicker('${row.ticker}')">Delete</button>
                    </div>
                </div>
                ${_regEditFormHtml(row)}
            </div>`;
        }).join('');
    } catch (e) {
        list.innerHTML = `<span class="msg-error">Failed to load: ${e.message}</span>`;
    }
}

function toggleAddRegistryForm() {
    document.getElementById('add-registry-form').classList.toggle('d-none');
}

function toggleRegistryEditForm(ticker) {
    const f = document.getElementById(`reg-edit-form-${_regDomId(ticker)}`);
    if (!f) return;
    f.style.display = f.style.display === 'none' ? 'block' : 'none';
}

function _registryBodyFromForm(fullPrefix) {
    const get = (name) => document.getElementById(`${fullPrefix}-${name}`);
    return {
        display_name: get('display-name')?.value.trim() || '',
        region: get('region')?.value || 'Europe',
        asset_type: get('asset-type')?.value || 'Index',
        exchange: get('exchange')?.value.trim() || null,
        currency: (get('currency')?.value.trim() || 'USD').toUpperCase(),
        future_ticker: get('future-ticker')?.value.trim().toUpperCase() || null,
        future_display_name: get('future-display-name')?.value.trim() || null,
        invert_color: get('invert-color')?.checked ?? false,
        is_pulse_tile: get('is-pulse-tile')?.checked ?? false,
        is_pulse_mobile: get('is-pulse-mobile')?.checked ?? true,
    };
}

async function saveNewRegistryTicker() {
    const status = document.getElementById('registry-add-status');
    const ticker = (document.getElementById('reg-new-ticker')?.value || '').trim().toUpperCase();
    if (!ticker) { status.innerHTML = '<span class="msg-error">Ticker is required.</span>'; return; }
    const body = { ticker, ..._registryBodyFromForm('reg-new') };
    if (!body.display_name) { status.innerHTML = '<span class="msg-error">Display name is required.</span>'; return; }
    status.innerHTML = '<span class="msg-info">Saving...</span>';
    try {
        const r = await fetch('/api/markets/registry', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        const data = await r.json();
        if (data.status === 'success') {
            status.innerHTML = '<span class="msg-success">Added.</span>';
            document.getElementById('reg-new-ticker').value = '';
            document.getElementById('reg-new-display-name').value = '';
            loadRegistryList();
        } else {
            status.innerHTML = `<span class="msg-error">${data.message || 'Failed'}</span>`;
        }
    } catch (e) { status.innerHTML = `<span class="msg-error">${e.message}</span>`; }
}

async function saveRegistryEdit(ticker) {
    const id = _regDomId(ticker);
    const statusEl = document.getElementById(`reg-edit-status-${id}`);
    const body = _registryBodyFromForm(`reg-edit-${id}`);
    if (!body.display_name) { if (statusEl) statusEl.innerHTML = '<span class="msg-error">Display name is required.</span>'; return; }
    if (statusEl) statusEl.innerHTML = '<span class="msg-info">Saving...</span>';
    try {
        const r = await fetch(`/api/markets/registry/${encodeURIComponent(ticker)}`, {
            method: 'PUT', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify(body),
        });
        const data = await r.json();
        if (data.status === 'success') {
            if (statusEl) statusEl.innerHTML = '<span class="msg-success">Saved.</span>';
            loadRegistryList();
        } else {
            if (statusEl) statusEl.innerHTML = `<span class="msg-error">${data.message || 'Failed'}</span>`;
        }
    } catch (e) { if (statusEl) statusEl.innerHTML = `<span class="msg-error">${e.message}</span>`; }
}

async function deleteRegistryTicker(ticker) {
    if (!confirm(`Remove ${ticker} from the Markets registry? It will disappear from the Markets page and Market Pulse, but historical price/sentiment data is preserved.`)) return;
    try {
        const r = await fetch(`/api/markets/registry/${encodeURIComponent(ticker)}`, { method: 'DELETE' });
        const data = await r.json();
        if (data.status === 'success') {
            loadRegistryList();
        } else {
            alert(data.message || 'Failed to delete.');
        }
    } catch (e) { alert(e.message); }
}

document.addEventListener('DOMContentLoaded', () => {
    if (document.getElementById('markets-registry-list')) loadRegistryList();
});
