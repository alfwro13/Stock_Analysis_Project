function _edpPct(value) {
    return value != null ? `${Number(value).toFixed(1)}%` : '—';
}

function _edpRenderSummary(overall) {
    document.getElementById('edp-summary-total').textContent = overall.total ?? 0;
    document.getElementById('edp-summary-1d').textContent = overall.resolved_1d ? _edpPct(overall.accuracy_1d) : 'Pending';
    document.getElementById('edp-summary-5d').textContent = overall.resolved_5d ? _edpPct(overall.accuracy_5d) : 'Pending';
    document.getElementById('edp-summary-20d').textContent = overall.resolved_20d ? _edpPct(overall.accuracy_20d) : 'Pending';
}

function _edpRenderTable(rows) {
    const tbody = document.getElementById('edp-tbody');
    document.getElementById('edp-count').textContent = `(${rows.length})`;
    if (!rows.length) {
        tbody.innerHTML = '<tr><td colspan="9" class="text-center p-4 text-muted">No predictions logged yet — the daily Overnight Quant Scan populates this data for tickers with earnings in the next few days.</td></tr>';
        return;
    }
    tbody.innerHTML = '';
    rows.forEach(row => {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td class="tm-th-left">${escapeHtml(row.ticker)}</td>
            <td class="tm-th-left">${escapeHtml(row.company_name || '—')}</td>
            <td class="tm-th-right">${row.total ?? 0}</td>
            <td class="tm-th-right">${row.resolved_1d ?? 0}</td>
            <td class="tm-th-right">${row.resolved_1d ? _edpPct(row.accuracy_1d) : 'Pending'}</td>
            <td class="tm-th-right">${row.resolved_5d ?? 0}</td>
            <td class="tm-th-right">${row.resolved_5d ? _edpPct(row.accuracy_5d) : 'Pending'}</td>
            <td class="tm-th-right">${row.resolved_20d ?? 0}</td>
            <td class="tm-th-right">${row.resolved_20d ? _edpPct(row.accuracy_20d) : 'Pending'}</td>
        `;
        tbody.appendChild(tr);
    });
}

function _edpLoad() {
    fetch('/api/earnings-volatility/accuracy')
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') throw new Error(data.message || 'Failed to load');
            _edpRenderSummary(data.overall || {});
            _edpRenderTable(data.by_ticker || []);
        })
        .catch(() => {
            document.getElementById('edp-tbody').innerHTML = '<tr><td colspan="9" class="text-center p-4 text-danger">Failed to load accuracy data.</td></tr>';
        });
}

document.addEventListener('DOMContentLoaded', _edpLoad);
