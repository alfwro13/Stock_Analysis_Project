/* Forensic Screener — all Jinja values injected via window.FORENSIC_CFG */
(function () {
    'use strict';

    function fmt(v, dp) {
        if (v === null || v === undefined) return '<span class="text-secondary">N/A</span>';
        return parseFloat(v).toFixed(dp !== undefined ? dp : 2);
    }

    function fscoreBadge(v) {
        if (v === null || v === undefined) return '<span class="text-secondary">N/A</span>';
        const n = parseInt(v, 10);
        const cls = n >= 7 ? 'bg-success' : (n >= 4 ? 'bg-warning text-dark' : 'bg-danger');
        return `<span class="badge ${cls}">${n}/9</span>`;
    }

    function altmanCell(v) {
        if (v === null || v === undefined) return '<span class="text-secondary">N/A</span>';
        const f = parseFloat(v);
        const cls = f > 2.6 ? 'text-success' : (f > 1.1 ? 'text-warning' : 'text-danger');
        return `<span class="${cls} fw-semibold">${f.toFixed(2)}</span>`;
    }

    function beneishCell(v) {
        if (v === null || v === undefined) return '<span class="text-secondary">N/A</span>';
        const f = parseFloat(v);
        const cls = f > -1.78 ? 'text-danger' : (f > -2.22 ? 'text-warning' : 'text-success');
        return `<span class="${cls} fw-semibold">${f.toFixed(3)}</span>`;
    }

    function flagBadges(r) {
        const badges = [];
        if (r.flag_piotroski) badges.push('<span class="badge bg-danger me-1">F&lt;4</span>');
        if (r.flag_altman)    badges.push('<span class="badge bg-danger me-1">Z&lt;1.81</span>');
        if (r.flag_beneish)   badges.push('<span class="badge bg-warning text-dark me-1">M&gt;-1.78</span>');
        return badges.length ? badges.join('') : '<span class="text-success small">&#10003; Clean</span>';
    }

    function renderTable(results) {
        const loading = document.getElementById('forensic-loading');
        const empty   = document.getElementById('forensic-empty');
        const table   = document.getElementById('forensic-table');
        const tbody   = document.getElementById('forensic-tbody');

        if (loading) loading.style.display = 'none';

        if (!results || !results.length) {
            if (empty) empty.style.display = '';
            return;
        }

        if (empty) empty.style.display = 'none';
        if (table) table.style.display = '';

        tbody.innerHTML = results.map(r => `
            <tr>
                <td><strong>${r.ticker}</strong></td>
                <td>${r.company_name}</td>
                <td><span class="text-secondary small">${r.sector}</span></td>
                <td>${fscoreBadge(r.piotroski_f_score)}</td>
                <td>${altmanCell(r.altman_z_score)}</td>
                <td>${beneishCell(r.beneish_m_score)}</td>
                <td>${flagBadges(r)}</td>
                <td class="text-secondary small">${r.forensic_last_updated || '—'}</td>
            </tr>`).join('');
    }

    function loadScores() {
        fetch('/api/forensic-scores')
            .then(r => r.json())
            .then(d => renderTable(d.results || []))
            .catch(() => {
                const loading = document.getElementById('forensic-loading');
                if (loading) loading.textContent = 'Failed to load scores.';
            });
    }

    function triggerJob(endpoint, btnId, label) {
        const btn = document.getElementById(btnId);
        if (btn) { btn.disabled = true; btn.textContent = 'Running…'; }
        const csrf = window.getCSRFToken ? window.getCSRFToken() : '';
        fetch(endpoint, { method: 'POST', headers: { 'Content-Type': 'application/json', 'X-CSRFToken': csrf } })
            .then(r => r.json())
            .then(d => {
                if (btn) { btn.disabled = false; btn.textContent = label; }
                const msg = d.message || 'Triggered. Check System Notifications for progress.';
                alert(msg);
            })
            .catch(() => {
                if (btn) { btn.disabled = false; btn.textContent = label; }
                alert('Request failed. Check System Notifications.');
            });
    }

    document.addEventListener('DOMContentLoaded', function () {
        loadScores();

        const btnFetch = document.getElementById('btn-run-fetch');
        if (btnFetch) {
            btnFetch.addEventListener('click', function () {
                triggerJob('/api/forensic-scores/run-fetch', 'btn-run-fetch', 'Run Now');
            });
        }

        const btnScore = document.getElementById('btn-run-score');
        if (btnScore) {
            btnScore.addEventListener('click', function () {
                triggerJob('/api/forensic-scores/run-score', 'btn-run-score', 'Run Now');
                setTimeout(loadScores, 10000);
            });
        }
    });
}());
