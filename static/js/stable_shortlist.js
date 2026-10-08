const _slPanes = {};

function _slNum(value, digits) {
    return value != null ? Number(value).toFixed(digits) : '—';
}

function _slSigned(value, digits, suffix) {
    if (value == null) return '—';
    const n = Number(value);
    return `${n >= 0 ? '+' : ''}${n.toFixed(digits)}${suffix}`;
}

function _slReturnClass(value) {
    if (value == null) return '';
    return Number(value) >= 0 ? 'text-success' : 'text-danger';
}

function _slTickerCells(row) {
    return `<td class="tm-th-left"><a href="/stock/${encodeURIComponent(row.ticker)}" class="ticker-link">${escapeHtml(row.ticker)}</a></td>
            <td class="tm-th-left">${escapeHtml(row.company_name || '—')}</td>`;
}

function _slMovement(row) {
    if (row.prev_rank == null) return '<span class="text-info">new</span>';
    const delta = row.prev_rank - row.rank;
    if (delta === 0) return '—';
    return delta > 0 ? `<span class="text-success">&#9650; ${delta}</span>` : `<span class="text-danger">&#9660; ${-delta}</span>`;
}

function _slSignalHeader(signal) {
    return signal === 'ml_upside'
        ? '<th class="tm-th-amber tm-th-right"><abbr title="Predicted move from the reference close to the middle of the 10-trading-day quantile price band.">Predicted Upside</abbr></th>'
        : '<th class="tm-th-amber tm-th-right"><abbr title="0-100 composite quant score on the signal date.">Quant Score</abbr></th>';
}

function _slSignalCell(signal, row) {
    if (row.signal_value == null) return '<td class="tm-th-right">—</td>';
    return signal === 'ml_upside'
        ? `<td class="tm-th-right ${_slReturnClass(row.signal_value)}">${_slSigned(row.signal_value, 2, '%')}</td>`
        : `<td class="tm-th-right">${_slNum(row.signal_value, 0)}</td>`;
}

function _slMembersHeader(signal) {
    const band = signal === 'ml_upside'
        ? '<th class="tm-th-muted tm-th-right">Q10</th><th class="tm-th-muted tm-th-right">Q90</th>' : '';
    return `<tr>
        <th class="tm-th-amber tm-th-right">Rank</th>
        <th class="tm-th-blue tm-th-left">Ticker</th>
        <th class="tm-th-muted tm-th-left">Company</th>
        <th class="tm-th-muted tm-th-left">Sector</th>
        ${_slSignalHeader(signal)}${band}
        <th class="tm-th-muted tm-th-right"><abbr title="The completed close the signal was computed on. Outcomes are measured from this price.">Reference Close</abbr></th>
        <th class="tm-th-dimmed tm-th-right">Signal Date</th>
        <th class="tm-th-muted tm-th-right"><abbr title="Consecutive weekly snapshots this name has been on the list, including the latest.">Weeks Held</abbr></th>
        <th class="tm-th-muted tm-th-right"><abbr title="Change in rank since the previous snapshot.">Rank Move</abbr></th>
        <th class="tm-th-muted tm-th-left">Status</th>
    </tr>`;
}

function _slMemberRow(signal, row) {
    const band = signal === 'ml_upside'
        ? `<td class="tm-th-right">${_slNum(row.price_q10, 2)}</td><td class="tm-th-right">${_slNum(row.price_q90, 2)}</td>` : '';
    return `<tr>
        <td class="tm-th-right">${row.rank}</td>
        ${_slTickerCells(row)}
        <td class="tm-th-left">${escapeHtml(row.sector || '—')}</td>
        ${_slSignalCell(signal, row)}${band}
        <td class="tm-th-right">${_slNum(row.reference_close, 2)}</td>
        <td class="tm-th-right tm-th-dimmed">${escapeHtml(row.signal_date || '')}</td>
        <td class="tm-th-right">${row.cycles_held}</td>
        <td class="tm-th-right">${_slMovement(row)}</td>
        <td class="tm-th-left">${escapeHtml(row.reason_label)}</td>
    </tr>`;
}

function _slRenderMeta(pane, data) {
    const meta = document.getElementById(`sl-${pane.id}-meta`);
    const snap = data.snapshot;
    const sched = data.schedule;
    const days = (sched.days || []).join(', ').toUpperCase();
    const when = sched.enabled ? `Next snapshots: ${escapeHtml(days)} at ${escapeHtml(sched.time || '')} (local).` : 'The weekly snapshot job is switched off in Settings.';
    if (!snap) {
        meta.innerHTML = `No snapshot has been taken for this list yet. ${when}`;
        return;
    }
    const c = snap.config;
    meta.innerHTML = `Snapshot ${escapeHtml(snap.cycle_key)} taken ${escapeHtml(snap.decision_local)} from ${escapeHtml(snap.signal_version || 'unknown signal')};
        newest signal dated ${escapeHtml(snap.signal_as_of || '—')}. ${snap.candidate_count} names tracked, ${snap.member_count} on the list.
        Settings used: list size ${c.TOPK}, at most ${c.N_DROP} swaps per snapshot, ${c.HOLD_THRESH} snapshot(s) minimum hold, ${c.SECTOR_CAP} per sector${data.signal_type === 'quant_score' ? `, minimum score ${c.MIN_QUANT_SCORE}` : ''}. ${when}`;
}

function _slRenderMembers(pane, data) {
    const tbody = document.getElementById(`sl-${pane.id}-tbody`);
    document.getElementById(`sl-${pane.id}-thead`).innerHTML = _slMembersHeader(pane.signal);
    document.getElementById(`sl-${pane.id}-count`).textContent = `(${data.members.length})`;
    const cols = pane.signal === 'ml_upside' ? 12 : 10;
    if (!data.snapshot) {
        tbody.innerHTML = `<tr><td colspan="${cols}" class="text-center p-4 text-muted">No snapshot yet — the weekly Stable Shortlist job (or Run Now) creates the first one.</td></tr>`;
        return;
    }
    if (!data.members.length) {
        tbody.innerHTML = `<tr><td colspan="${cols}" class="text-center p-4 text-muted">No name qualified at the latest snapshot.</td></tr>`;
        return;
    }
    tbody.innerHTML = data.members.map(r => _slMemberRow(pane.signal, r)).join('');
}

function _slRenderChanges(pane, data) {
    const tbody = document.getElementById(`sl-${pane.id}-changes`);
    if (!data.changes.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center p-3 text-muted">No names entered or left at the latest snapshot.</td></tr>';
        return;
    }
    tbody.innerHTML = data.changes.map(r => {
        const entered = r.reason === 'entered';
        const label = entered ? '<span class="text-success">Entered</span>' : '<span class="text-danger">Dropped</span>';
        const why = entered ? r.reason_label : `${r.reason_label}${r.ineligible_label ? ` (${r.ineligible_label})` : ''}`;
        return `<tr>${_slTickerCells(r)}<td class="tm-th-left">${label}</td><td class="tm-th-left">${escapeHtml(why)}</td></tr>`;
    }).join('');
}

function _slRenderOthers(pane, data) {
    document.getElementById(`sl-${pane.id}-others-count`).textContent = `(${data.others.length})`;
    document.getElementById(`sl-${pane.id}-others-thead`).innerHTML = `<tr>
        <th class="tm-th-amber tm-th-right">Rank</th>
        <th class="tm-th-blue tm-th-left">Ticker</th>
        <th class="tm-th-muted tm-th-left">Company</th>
        <th class="tm-th-muted tm-th-left">Sector</th>
        ${_slSignalHeader(pane.signal)}
        <th class="tm-th-muted tm-th-left">Status</th>
    </tr>`;
    document.getElementById(`sl-${pane.id}-others`).innerHTML = data.others.map(r => `<tr>
        <td class="tm-th-right">${r.rank != null ? r.rank : '—'}</td>
        ${_slTickerCells(r)}
        <td class="tm-th-left">${escapeHtml(r.sector || '—')}</td>
        ${_slSignalCell(pane.signal, r)}
        <td class="tm-th-left">${escapeHtml(r.ineligible_label || r.reason_label)}</td>
    </tr>`).join('');
}

function _slRenderEvaluation(pane, data) {
    const ev = data.evaluation;
    const s = ev.summary || {};
    const summary = document.getElementById(`sl-${pane.id}-eval-summary`);
    const body = document.getElementById(`sl-${pane.id}-eval`);
    if (!ev.snapshots.length) {
        const pending = s.snapshots_pending ? ` ${s.snapshots_pending} snapshot(s) are still waiting for their 10 trading sessions to pass.` : '';
        summary.textContent = `No snapshot has finished its 10-session follow-up yet.${pending}`;
        body.innerHTML = '';
        return;
    }
    const parts = [
        `${s.snapshots_evaluated} snapshot(s) followed`,
        `members averaged ${_slSigned(s.avg_member_return_pct, 2, '%')} against ${_slSigned(s.avg_other_return_pct, 2, '%')} for the other tracked names`,
        `members ahead in ${s.snapshots_beating_others} of ${s.snapshots_comparable} snapshot(s)`,
    ];
    if (s.direction_accuracy != null) {
        parts.push(`predicted direction right ${_slNum(s.direction_accuracy, 1)}% of the time, final price inside the Q10–Q90 band ${_slNum(s.within_band_accuracy, 1)}%`);
    }
    if (s.snapshots_pending) parts.push(`${s.snapshots_pending} more still pending`);
    summary.textContent = parts.join('; ') + '.';
    body.innerHTML = ev.snapshots.map(r => `<tr>
        <td class="tm-th-left">${escapeHtml(r.cycle_key)}</td>
        <td class="tm-th-right">${r.member_count}</td>
        <td class="tm-th-right ${_slReturnClass(r.member_avg_return_pct)}">${_slSigned(r.member_avg_return_pct, 2, '%')}</td>
        <td class="tm-th-right">${r.other_count}</td>
        <td class="tm-th-right ${_slReturnClass(r.other_avg_return_pct)}">${_slSigned(r.other_avg_return_pct, 2, '%')}</td>
        <td class="tm-th-right ${_slReturnClass(r.excess_pct)}">${_slSigned(r.excess_pct, 2, ' pp')}</td>
    </tr>`).join('');
}

function _slLoad(pane) {
    fetch(`/api/predicted-movers/shortlist?signal=${encodeURIComponent(pane.signal)}&scope=${encodeURIComponent(pane.scope)}`)
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') throw new Error(data.message || 'Failed to load');
            _slRenderMeta(pane, data);
            _slRenderMembers(pane, data);
            _slRenderChanges(pane, data);
            _slRenderOthers(pane, data);
            _slRenderEvaluation(pane, data);
        })
        .catch(() => {
            document.getElementById(`sl-${pane.id}-tbody`).innerHTML = '<tr><td class="text-center p-4 text-danger">Failed to load the shortlist.</td></tr>';
        });
}

function _slRun(pane) {
    const btn = document.getElementById(`sl-${pane.id}-run`);
    const msg = document.getElementById(`sl-${pane.id}-run-msg`);
    btn.disabled = true;
    fetch('/api/predicted-movers/shortlist/run', { method: 'POST' })
        .then(r => r.json())
        .then(data => {
            msg.textContent = data.message || '';
            setTimeout(() => _slLoad(pane), 8000);
        })
        .catch(() => { msg.textContent = 'Request failed.'; })
        .finally(() => setTimeout(() => { btn.disabled = false; }, 3000));
}

function _slInitPane(el) {
    const pane = { id: el.dataset.slId, signal: el.dataset.slSignal, scope: 'portfolio', loaded: false };
    _slPanes[pane.id] = pane;
    el.querySelectorAll(`input[name="sl-${pane.id}-scope"]`).forEach(radio => radio.addEventListener('change', () => {
        pane.scope = radio.value;
        _slLoad(pane);
    }));
    document.getElementById(`sl-${pane.id}-run`).addEventListener('click', () => _slRun(pane));
}

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.sl-pane').forEach(_slInitPane);
    document.querySelectorAll('#pm-tabs button[data-sl-id]').forEach(tab => tab.addEventListener('shown.bs.tab', () => {
        const pane = _slPanes[tab.dataset.slId];
        if (!pane.loaded) {
            pane.loaded = true;
            _slLoad(pane);
        }
    }));
});
