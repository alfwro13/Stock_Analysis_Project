let _wlAddSearchTimer = null;

function _wlEscapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function _wlAddTickerModal() {
    return bootstrap.Modal.getOrCreateInstance(document.getElementById('addTickerModal'));
}

function openAddTickerModal() {
    document.getElementById('wl-add-search').value = '';
    document.getElementById('wl-add-results').innerHTML = '';
    document.getElementById('wl-add-status').innerHTML = '';
    _wlAddTickerModal().show();
    setTimeout(() => document.getElementById('wl-add-search').focus(), 200);
}

async function _wlRunAddSearch(query) {
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
            <button type="button" class="list-group-item list-group-item-action" onclick="addTickerToWatchlist('${_wlEscapeHtml(m.ticker)}')">
                <strong>${_wlEscapeHtml(m.ticker)}</strong> — ${_wlEscapeHtml(m.company_name || '')}
                <span class="text-muted small">${_wlEscapeHtml(m.quote_type || '')}</span>
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
        const r = await fetch('/api/watchlist/add', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ticker }),
        });
        const data = await r.json();
        if (r.ok && data.status === 'success') {
            status.innerHTML = '<span class="msg-success">Added — refreshing…</span>';
            window.location.reload();
        } else {
            status.innerHTML = `<span class="msg-error">${_wlEscapeHtml(data.message || 'Failed to add ticker.')}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${e.message}</span>`;
    }
}

$(document).ready(function () {
    $('#dataTable_length').append('<button type="button" id="addTickerBtn" class="btn btn-sm btn-primary ms-2" onclick="openAddTickerModal()">+ Add Ticker</button>');

    $('#wl-add-search').on('input', function () {
        clearTimeout(_wlAddSearchTimer);
        var query = this.value;
        _wlAddSearchTimer = setTimeout(function () { _wlRunAddSearch(query); }, 300);
    });
});
