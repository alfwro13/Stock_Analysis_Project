let _srmScope = 'portfolio_watchlist';
let _srmWindow = '63';

function _srmPct(fraction) {
    return fraction != null ? `${(Number(fraction) * 100).toFixed(2)}%` : '—';
}

function _srmRenderTable(results) {
    const tbody = document.getElementById('srm-tbody');
    document.getElementById('srm-count').textContent = `(${results.length})`;
    if (!results.length) {
        tbody.innerHTML = '<tr><td colspan="10" class="text-center p-4 text-muted">No results yet — the Sector-Relative Momentum job populates this data.</td></tr>';
        return;
    }
    tbody.innerHTML = '';
    results.forEach(row => {
        const tr = document.createElement('tr');
        const link = `<a href="/stock/${encodeURIComponent(row.ticker)}" class="ticker-link">${escapeHtml(row.ticker)}</a>`;
        const company = escapeHtml(row.company_name || '—');
        if (row.status !== 'ok') {
            const peers = row.peer_count != null ? ` (${row.peer_count} peer${row.peer_count === 1 ? '' : 's'})` : '';
            tr.innerHTML = `
                <td class="tm-th-left">${link}</td>
                <td class="tm-th-left">${company}</td>
                <td class="tm-th-left">${escapeHtml(row.sector || '—')}</td>
                <td colspan="6" class="tm-th-right tm-th-muted">Unavailable — ${escapeHtml(row.status_label)}${escapeHtml(peers)}</td>
                <td class="tm-th-right tm-th-dimmed">${escapeHtml(row.as_of_date || '')}</td>
            `;
        } else {
            const rel = Number(row.relative_return_pp);
            const relClass = rel >= 0 ? 'text-success' : 'text-danger';
            tr.innerHTML = `
                <td class="tm-th-left">${link}</td>
                <td class="tm-th-left">${company}</td>
                <td class="tm-th-left">${escapeHtml(row.sector || '—')}</td>
                <td class="tm-th-right tm-th-dimmed">${escapeHtml(row.window_start_date || '')}</td>
                <td class="tm-th-right">${_srmPct(row.own_return)}</td>
                <td class="tm-th-right">${_srmPct(row.peer_return)}</td>
                <td class="tm-th-right ${relClass}">${rel >= 0 ? '+' : ''}${rel.toFixed(2)}</td>
                <td class="tm-th-right">${row.rank}</td>
                <td class="tm-th-right">${row.cohort_size}</td>
                <td class="tm-th-right tm-th-dimmed">${escapeHtml(row.as_of_date || '')}</td>
            `;
        }
        tbody.appendChild(tr);
    });
}

function _srmLoadResults() {
    fetch(`/api/sector-relative-momentum/results?scope=${encodeURIComponent(_srmScope)}&window=${encodeURIComponent(_srmWindow)}`)
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') throw new Error(data.message || 'Failed to load');
            _srmRenderTable(data.results);
        })
        .catch(() => {
            document.getElementById('srm-tbody').innerHTML = '<tr><td colspan="10" class="text-center p-4 text-danger">Failed to load results.</td></tr>';
        });
}

document.addEventListener('DOMContentLoaded', () => {
    _srmLoadResults();
    document.querySelectorAll('input[name="srm-scope"]').forEach(el => el.addEventListener('change', () => {
        _srmScope = document.querySelector('input[name="srm-scope"]:checked').value;
        _srmLoadResults();
    }));
    document.querySelectorAll('input[name="srm-window"]').forEach(el => el.addEventListener('change', () => {
        _srmWindow = document.querySelector('input[name="srm-window"]:checked').value;
        _srmLoadResults();
    }));
});
