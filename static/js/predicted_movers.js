let _pmScope = 'portfolio_watchlist';
let _pmSort = 'movers';

function _pmRenderTable(results) {
    const tbody = document.getElementById('pm-tbody');
    document.getElementById('pm-count').textContent = `(${results.length})`;
    if (!results.length) {
        tbody.innerHTML = '<tr><td colspan="7" class="text-center p-4 text-muted">No predicted movers found yet — the nightly ML Inference job populates this data.</td></tr>';
        return;
    }
    tbody.innerHTML = '';
    results.forEach(row => {
        const tr = document.createElement('tr');
        const pct = Number(row.predicted_move_pct) || 0;
        const pctClass = pct >= 0 ? 'text-success' : 'text-danger';
        tr.innerHTML = `
            <td class="tm-th-left">${escapeHtml(row.ticker)}</td>
            <td class="tm-th-left">${escapeHtml(row.company_name || '—')}</td>
            <td class="tm-th-right">${row.current_price != null ? Number(row.current_price).toFixed(2) : '—'} ${escapeHtml(row.currency || '')}</td>
            <td class="tm-th-right ${pctClass}">${pct >= 0 ? '+' : ''}${pct.toFixed(2)}%</td>
            <td class="tm-th-right">${row.price_q10 != null ? Number(row.price_q10).toFixed(2) : '—'}</td>
            <td class="tm-th-right">${row.price_q90 != null ? Number(row.price_q90).toFixed(2) : '—'}</td>
            <td class="tm-th-right tm-th-dimmed">${escapeHtml(row.quant_signals_date || '')}</td>
        `;
        tbody.appendChild(tr);
    });
}

function _pmLoadResults() {
    fetch(`/api/predicted-movers/leaderboard?scope=${encodeURIComponent(_pmScope)}&sort=${encodeURIComponent(_pmSort)}`)
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') throw new Error(data.message || 'Failed to load');
            _pmRenderTable(data.results);
        })
        .catch(() => {
            document.getElementById('pm-tbody').innerHTML = '<tr><td colspan="7" class="text-center p-4 text-danger">Failed to load results.</td></tr>';
        });
}

function _pmOnScopeChange() {
    _pmScope = document.querySelector('input[name="pm-scope"]:checked').value;
    _pmLoadResults();
}

function _pmOnSortChange() {
    _pmSort = document.querySelector('input[name="pm-sort"]:checked').value;
    _pmLoadResults();
}

document.addEventListener('DOMContentLoaded', () => {
    _pmLoadResults();
    document.querySelectorAll('input[name="pm-scope"]').forEach(el => el.addEventListener('change', _pmOnScopeChange));
    document.querySelectorAll('input[name="pm-sort"]').forEach(el => el.addEventListener('change', _pmOnSortChange));
});
