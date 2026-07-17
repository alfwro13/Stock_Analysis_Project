function _pmaPct(value) {
    return value != null ? `${Number(value).toFixed(1)}%` : '—';
}

function _pmaRenderSummary(overall) {
    document.getElementById('pma-summary-total').textContent = overall.total ?? 0;
    document.getElementById('pma-summary-resolved').textContent = overall.resolved ?? 0;
    document.getElementById('pma-summary-pending').textContent = overall.pending ?? 0;
    document.getElementById('pma-summary-direction').textContent = _pmaPct(overall.direction_accuracy);
    document.getElementById('pma-summary-band').textContent = _pmaPct(overall.within_band_accuracy);
}

function _pmaRenderTable(rows) {
    const tbody = document.getElementById('pma-tbody');
    document.getElementById('pma-count').textContent = `(${rows.length})`;
    if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center p-4 text-muted">No predictions logged yet — the nightly ML Inference job populates this data.</td></tr>';
        return;
    }
    tbody.innerHTML = '';
    rows.forEach(row => {
        const tr = document.createElement('tr');
        const resolved = row.resolved || 0;
        tr.innerHTML = `
            <td class="tm-th-left"><a href="/stock/${encodeURIComponent(row.ticker)}" class="ticker-link">${escapeHtml(row.ticker)}</a></td>
            <td class="tm-th-left">${escapeHtml(row.company_name || '—')}</td>
            <td class="tm-th-right">${row.total ?? 0}</td>
            <td class="tm-th-right">${resolved}</td>
            <td class="tm-th-right">${row.pending ?? 0}</td>
            <td class="tm-th-right">${resolved ? _pmaPct(row.direction_accuracy) : 'Pending'}</td>
            <td class="tm-th-right">${resolved ? _pmaPct(row.within_band_accuracy) : 'Pending'}</td>
        `;
        tbody.appendChild(tr);
    });
}

function _pmaLoad() {
    fetch('/api/predicted-movers/accuracy')
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') throw new Error(data.message || 'Failed to load');
            _pmaRenderSummary(data.overall || {});
            _pmaRenderTable(data.by_ticker || []);
        })
        .catch(() => {
            document.getElementById('pma-tbody').innerHTML = '<tr><td colspan="7" class="text-center p-4 text-danger">Failed to load accuracy data.</td></tr>';
        });
}

document.addEventListener('DOMContentLoaded', _pmaLoad);
