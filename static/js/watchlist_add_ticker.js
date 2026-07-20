let _wlAddSearchTimer = null;

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
            <button type="button" class="list-group-item list-group-item-action" onclick="addTickerToWatchlist('${escapeHtml(m.ticker)}')">
                <strong>${escapeHtml(m.ticker)}</strong> — ${escapeHtml(m.company_name || '')}
                <span class="text-muted small">${escapeHtml(m.quote_type || '')}</span>
            </button>
        `).join('') + '</div>';
    } catch (e) {
        results.innerHTML = `<span class="msg-error">Search failed: ${escapeHtml(e.message)}</span>`;
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
            status.innerHTML = `<span class="msg-error">${escapeHtml(data.message || 'Failed to add ticker.')}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${escapeHtml(e.message)}</span>`;
    }
}

$(document).ready(function () {
    $('#dataTable_length').append(
        '<div class="btn-group change-period-group ms-2" id="changePeriodGroup" role="group" aria-label="Change Period">'
        + '<button type="button" class="btn btn-sm btn-outline-secondary change-period-btn" data-period="1d">1D</button>'
        + '<button type="button" class="btn btn-sm btn-outline-secondary change-period-btn" data-period="5d">5D</button>'
        + '<button type="button" class="btn btn-sm btn-outline-secondary change-period-btn" data-period="1m">1M</button>'
        + '<button type="button" class="btn btn-sm btn-outline-secondary change-period-btn" data-period="6m">6M</button>'
        + '<button type="button" class="btn btn-sm btn-outline-secondary change-period-btn" data-period="ytd">YTD</button>'
        + '<button type="button" class="btn btn-sm btn-outline-secondary change-period-btn" data-period="1y">1Y</button>'
        + '</div>'
    );
    $('#dataTable_length').append('<button type="button" id="addTickerBtn" class="btn btn-sm btn-primary ms-2" onclick="openAddTickerModal()">+ Add Ticker</button>');
    window._watchlistChangePeriod.setButtons(window.WATCHLIST_CHANGE_PERIOD || '1d');

    $('#wl-add-search').on('input', function () {
        clearTimeout(_wlAddSearchTimer);
        var query = this.value;
        _wlAddSearchTimer = setTimeout(function () { _wlRunAddSearch(query); }, 300);
    });
});
