let _wlItems = [];
let _wlSearchTimer = null;

function _wlAddTickerModal() {
    return bootstrap.Modal.getOrCreateInstance(document.getElementById('addTickerModal'));
}

function _wlPopulateFilters() {
    const exchangeSel = document.getElementById('wl-exchange-filter');
    const typeSel = document.getElementById('wl-type-filter');
    const exchanges = [...new Set(_wlItems.map(i => i.exchange).filter(Boolean))].sort();
    const types = [...new Set(_wlItems.map(i => i.quote_type).filter(Boolean))].sort();
    const currentExchange = exchangeSel.value;
    const currentType = typeSel.value;
    exchangeSel.innerHTML = '<option value="">All Exchanges</option>' +
        exchanges.map(e => `<option value="${escapeHtml(e)}">${escapeHtml(e)}</option>`).join('');
    typeSel.innerHTML = '<option value="">All Types</option>' +
        types.map(t => `<option value="${escapeHtml(t)}">${escapeHtml(t)}</option>`).join('');
    if (exchanges.includes(currentExchange)) exchangeSel.value = currentExchange;
    if (types.includes(currentType)) typeSel.value = currentType;
}

function _wlRenderRows() {
    const tbody = document.getElementById('wl-items-tbody');
    const search = document.getElementById('wl-search').value.trim().toLowerCase();
    const exchangeFilter = document.getElementById('wl-exchange-filter').value;
    const typeFilter = document.getElementById('wl-type-filter').value;

    const visible = _wlItems.filter(i => {
        if (exchangeFilter && i.exchange !== exchangeFilter) return false;
        if (typeFilter && i.quote_type !== typeFilter) return false;
        if (search) {
            const haystack = `${i.ticker} ${i.company_name || ''}`.toLowerCase();
            if (!haystack.includes(search)) return false;
        }
        return true;
    });

    tbody.innerHTML = visible.map(i => `
        <tr>
            <td><input type="checkbox" class="form-check-input wl-row-check" data-id="${i.id}"></td>
            <td>${escapeHtml(i.ticker)}</td>
            <td>${escapeHtml(i.company_name || '—')}</td>
            <td>${escapeHtml(i.exchange || '—')}</td>
            <td>${escapeHtml(i.quote_type || '—')}</td>
        </tr>
    `).join('');

    document.getElementById('wl-empty-msg').classList.toggle('d-none', _wlItems.length > 0);
    document.getElementById('wl-select-all').checked = false;
    _wlUpdateDeleteButton();

    tbody.querySelectorAll('.wl-row-check').forEach(cb => cb.addEventListener('change', _wlUpdateDeleteButton));
}

function _wlUpdateDeleteButton() {
    const anyChecked = document.querySelectorAll('.wl-row-check:checked').length > 0;
    document.getElementById('wl-delete-selected').disabled = !anyChecked;
}

async function loadWatchlistItems() {
    try {
        const r = await fetch(`/api/accounts/${window.ACCOUNT_ID}/watchlist-items`);
        const data = await r.json();
        _wlItems = data.items || [];
    } catch (e) {
        _wlItems = [];
    }
    _wlPopulateFilters();
    _wlRenderRows();
}

function openAddTickerModal() {
    document.getElementById('wl-add-search').value = '';
    document.getElementById('wl-add-results').innerHTML = '';
    document.getElementById('wl-add-status').innerHTML = '';
    _wlAddTickerModal().show();
    setTimeout(() => document.getElementById('wl-add-search').focus(), 200);
}

async function _wlRunSearch(query) {
    const results = document.getElementById('wl-add-results');
    if (!query.trim()) {
        results.innerHTML = '';
        return;
    }
    try {
        const r = await fetch(`/api/ticker-search?q=${encodeURIComponent(query.trim())}`);
        const data = await r.json();
        const matches = data.results || [];
        if (matches.length === 0) {
            results.innerHTML = '<p class="text-muted small mb-0">No matches found.</p>';
            return;
        }
        results.innerHTML = '<div class="list-group">' + matches.map(m => `
            <button type="button" class="list-group-item list-group-item-action" onclick="addTickerToWatchlist('${escapeHtml(m.ticker)}')">
                <strong>${escapeHtml(m.ticker)}</strong> — ${escapeHtml(m.company_name || '')}
                <span class="text-muted small">${escapeHtml(m.quote_type || '')}</span>
            </button>
        `).join('') + '</div>';
    } catch (e) {
        results.innerHTML = `<span class="msg-error">Search failed: ${e.message}</span>`;
    }
}

async function addTickerToWatchlist(ticker) {
    const status = document.getElementById('wl-add-status');
    status.innerHTML = '<span class="msg-info">Adding...</span>';
    try {
        const r = await fetch(`/api/accounts/${window.ACCOUNT_ID}/watchlist-items`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticker }),
        });
        const data = await r.json();
        if (r.ok && data.status === 'success') {
            _wlAddTickerModal().hide();
            await loadWatchlistItems();
        } else {
            status.innerHTML = `<span class="msg-error">${escapeHtml(data.message || 'Failed to add ticker.')}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${e.message}</span>`;
    }
}

async function deleteSelectedWatchlistItems() {
    const ids = [...document.querySelectorAll('.wl-row-check:checked')].map(cb => parseInt(cb.dataset.id, 10));
    if (ids.length === 0) return;
    try {
        await fetch(`/api/accounts/${window.ACCOUNT_ID}/watchlist-items/bulk-delete`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ids }),
        });
        await loadWatchlistItems();
    } catch (e) {
        // loadWatchlistItems failure leaves the table as-is; user can retry the delete
    }
}

document.addEventListener('DOMContentLoaded', () => {
    _wlItems = window.WATCHLIST_ITEMS || [];
    _wlPopulateFilters();
    _wlRenderRows();

    document.getElementById('wl-search').addEventListener('input', _wlRenderRows);
    document.getElementById('wl-exchange-filter').addEventListener('change', _wlRenderRows);
    document.getElementById('wl-type-filter').addEventListener('change', _wlRenderRows);

    document.getElementById('wl-select-all').addEventListener('change', (e) => {
        document.querySelectorAll('.wl-row-check').forEach(cb => { cb.checked = e.target.checked; });
        _wlUpdateDeleteButton();
    });

    document.getElementById('wl-add-search').addEventListener('input', (e) => {
        clearTimeout(_wlSearchTimer);
        _wlSearchTimer = setTimeout(() => _wlRunSearch(e.target.value), 300);
    });
});
