function _edpPct(value) {
    return value != null ? `${Number(value).toFixed(1)}%` : '—';
}

function _edpSignedPct(value) {
    if (value == null) return '—';
    const num = Number(value);
    return `${num > 0 ? '+' : ''}${num.toFixed(2)}%`;
}

function _edpResultCell(directionCorrect) {
    if (directionCorrect === null || directionCorrect === undefined) return '<span class="text-muted">Pending</span>';
    return directionCorrect
        ? '<span class="text-success">&#10003; Correct</span>'
        : '<span class="text-danger">&#10007; Wrong</span>';
}

function _edpHorizonCells(event, horizon) {
    const predicted = event[`predicted_pct_${horizon}d`];
    const actual = event[`actual_pct_${horizon}d`];
    return `
        <td class="tm-th-right">${_edpSignedPct(predicted)}</td>
        <td class="tm-th-right">${predicted != null && actual == null ? '<span class="text-muted">Pending</span>' : _edpSignedPct(actual)}</td>
        <td class="tm-th-center">${predicted == null ? '—' : _edpResultCell(event[`direction_correct_${horizon}d`])}</td>
    `;
}

function _edpEventsTableHtml(events) {
    if (!events.length) {
        return '<div class="text-center p-3 text-muted">No individual earnings events logged yet.</div>';
    }
    const bodyRows = events.map(event => `
        <tr>
            <td class="tm-th-left">${escapeHtml(event.earnings_date)}</td>
            ${_edpHorizonCells(event, 1)}
            ${_edpHorizonCells(event, 5)}
            ${_edpHorizonCells(event, 20)}
        </tr>
    `).join('');
    return `
        <table class="tm-table w-100 edp-detail-table">
            <thead>
                <tr>
                    <th class="tm-th-muted tm-th-left">Earnings Date</th>
                    <th class="tm-th-amber tm-th-right">1D Pred</th>
                    <th class="tm-th-amber tm-th-right">1D Actual</th>
                    <th class="tm-th-amber tm-th-center">1D Result</th>
                    <th class="tm-th-amber tm-th-right">5D Pred</th>
                    <th class="tm-th-amber tm-th-right">5D Actual</th>
                    <th class="tm-th-amber tm-th-center">5D Result</th>
                    <th class="tm-th-amber tm-th-right">20D Pred</th>
                    <th class="tm-th-amber tm-th-right">20D Actual</th>
                    <th class="tm-th-amber tm-th-center">20D Result</th>
                </tr>
            </thead>
            <tbody>${bodyRows}</tbody>
        </table>
    `;
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
        tr.className = 'edp-ticker-row';
        tr.innerHTML = `
            <td class="tm-th-left"><span class="edp-caret">&#9656;</span> ${escapeHtml(row.ticker)}</td>
            <td class="tm-th-left">${escapeHtml(row.company_name || '—')}</td>
            <td class="tm-th-right">${row.total ?? 0}</td>
            <td class="tm-th-right">${row.resolved_1d ?? 0}</td>
            <td class="tm-th-right">${row.resolved_1d ? _edpPct(row.accuracy_1d) : 'Pending'}</td>
            <td class="tm-th-right">${row.resolved_5d ?? 0}</td>
            <td class="tm-th-right">${row.resolved_5d ? _edpPct(row.accuracy_5d) : 'Pending'}</td>
            <td class="tm-th-right">${row.resolved_20d ?? 0}</td>
            <td class="tm-th-right">${row.resolved_20d ? _edpPct(row.accuracy_20d) : 'Pending'}</td>
        `;

        const detailTr = document.createElement('tr');
        detailTr.className = 'edp-detail-row d-none';
        const detailTd = document.createElement('td');
        detailTd.colSpan = 9;
        detailTd.className = 'p-0';
        detailTr.appendChild(detailTd);

        let expanded = false;
        let rendered = false;
        tr.addEventListener('click', () => {
            expanded = !expanded;
            tr.querySelector('.edp-caret').innerHTML = expanded ? '&#9662;' : '&#9656;';
            if (!rendered) {
                detailTd.innerHTML = _edpEventsTableHtml(row.events || []);
                rendered = true;
            }
            detailTr.classList.toggle('d-none', !expanded);
        });

        tbody.appendChild(tr);
        tbody.appendChild(detailTr);
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
