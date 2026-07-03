/* Bubble Radar — all Jinja values injected via window.BUBBLE_RADAR_CFG */
(function () {
    'use strict';

    const watchThreshold = window.BUBBLE_RADAR_CFG.watchThreshold;
    const flagThreshold  = window.BUBBLE_RADAR_CFG.flagThreshold;

    function flagClass(flag) {
        if (flag === 'bubble') return 'bubble-flag-bubble';
        if (flag === 'watch')  return 'bubble-flag-watch';
        return '';
    }

    function flagLabel(flag) {
        if (flag === 'bubble') return 'Bubble Risk';
        if (flag === 'watch')  return 'Watch';
        return '';
    }

    function scoreFillClass(score) {
        if (score >= flagThreshold)  return 'bubble-score-fill-bubble';
        if (score >= watchThreshold) return 'bubble-score-fill-watch';
        return 'bubble-score-fill-low';
    }

    function scoreBar(score) {
        const fillCls = scoreFillClass(score);
        const pct = Math.min(100, score);
        return `<span class="bubble-score-bar" style="width:60px;display:inline-block;vertical-align:middle;">` +
               `<span class="bubble-score-fill ${fillCls}" style="width:${pct}%;display:inline-block;"></span></span>` +
               `<span style="font-family:monospace;font-size:12px;">${score}</span>`;
    }

    function fmt(v, decimals) {
        if (v === null || v === undefined) return '<span style="color:#444;">—</span>';
        return parseFloat(v).toFixed(decimals !== undefined ? decimals : 2);
    }

    function renderFlaggedTable(results) {
        const tbody = document.getElementById('br-flagged-body');
        const empty = document.getElementById('br-flagged-empty');
        if (!results || !results.length) {
            if (tbody) tbody.innerHTML = '';
            if (empty) empty.style.display = 'block';
            return;
        }
        if (empty) empty.style.display = 'none';
        tbody.innerHTML = results.map(r => `
            <tr class="clickable" data-ticker="${r.ticker}" onclick="brShowDetail('${r.ticker}')">
                <td><strong style="color:#e0e0e0;">${r.ticker}</strong><br>
                    <span style="font-size:11px;color:#666;">${r.company_name || ''}</span></td>
                <td><span class="${flagClass(r.flag)}">${flagLabel(r.flag)}</span></td>
                <td>${scoreBar(Math.round(r.bubble_score))}</td>
                <td style="font-family:monospace;font-size:12px;">${fmt(r.sma_ext_pct, 1)}%</td>
                <td style="font-family:monospace;font-size:12px;">${fmt(r.rsi_avg_20d, 1)}</td>
                <td style="font-family:monospace;font-size:12px;">${fmt(r.ps_ratio, 1)}×</td>
                <td style="font-family:monospace;font-size:12px;">${fmt(r.peg_ratio, 2)}</td>
                <td style="font-size:11px;color:#666;">${r.scan_date || ''}</td>
            </tr>`).join('');
    }

    function renderHistoryTable(results) {
        const tbody = document.getElementById('br-history-body');
        const empty = document.getElementById('br-history-empty');
        if (!results || !results.length) {
            if (tbody) tbody.innerHTML = '';
            if (empty) empty.style.display = 'block';
            return;
        }
        if (empty) empty.style.display = 'none';
        tbody.innerHTML = results.map(r => {
            const o4  = r.outcome_4w  || '—';
            const o8  = r.outcome_8w  || '—';
            const o12 = r.outcome_12w || '—';
            function outcomeCell(o) {
                if (o === 'correct')   return `<span style="color:#4caf50;font-weight:700;">✓</span>`;
                if (o === 'incorrect') return `<span style="color:#ff4d4d;font-weight:700;">✗</span>`;
                return '<span style="color:#444;">—</span>';
            }
            return `<tr>
                <td><strong style="color:#e0e0e0;">${r.ticker}</strong></td>
                <td><span class="${flagClass(r.flag_level)}">${flagLabel(r.flag_level)}</span></td>
                <td style="font-size:11px;color:#888;">${r.flagged_date}</td>
                <td style="font-family:monospace;font-size:12px;">${fmt(r.price_at_flag, 2)}</td>
                <td>${outcomeCell(o4)}</td>
                <td>${outcomeCell(o8)}</td>
                <td>${outcomeCell(o12)}</td>
            </tr>`;
        }).join('');
    }

    window.brShowDetail = function (ticker) {
        const panel = document.getElementById('br-detail-panel');
        const title = document.getElementById('br-detail-ticker');
        const body  = document.getElementById('br-detail-body');
        if (!panel) return;
        panel.style.display = 'block';
        title.textContent = ticker;
        body.innerHTML = '<div style="color:#555;font-size:12px;">Loading…</div>';
        fetch(`/api/bubble-radar/ticker/${ticker}`)
            .then(r => r.json())
            .then(data => {
                if (!data.result) {
                    body.innerHTML = '<div class="bubble-empty">No data available for this ticker yet.</div>';
                    return;
                }
                const r = data.result;
                const metrics = r.metric_scores || {};
                const rows = Object.entries(metrics).map(([k, m]) => {
                    const score = m.score || 0;
                    const barW  = Math.min(100, score * 4);
                    const barCls = score >= 8 ? 'bubble-score-fill-bubble' : score >= 4 ? 'bubble-score-fill-watch' : 'bubble-score-fill-low';
                    const val = (m.value !== null && m.value !== undefined)
                        ? parseFloat(m.value).toFixed(2)
                        : '—';
                    return `<div class="bubble-metric-row">
                        <span class="bubble-metric-label">${m.label}</span>
                        <span class="bubble-metric-val">${val}</span>
                        <span class="bubble-score-bar" style="width:80px;display:inline-block;">
                            <span class="bubble-score-fill ${barCls}" style="width:${barW}%;display:inline-block;"></span>
                        </span>
                        <span class="bubble-metric-score" style="color:${score > 0 ? '#ffaa00' : '#444'};">${score}pts</span>
                    </div>`;
                }).join('');
                body.innerHTML = `
                    <div style="display:flex;align-items:center;gap:12px;margin-bottom:14px;">
                        <span class="${flagClass(r.flag)}">${flagLabel(r.flag)}</span>
                        <span style="font-size:20px;font-weight:700;font-family:monospace;">${Math.round(r.bubble_score)}<span style="font-size:13px;color:#666;">/100</span></span>
                        <span style="font-size:12px;color:#555;">as of ${r.scan_date}</span>
                    </div>
                    ${rows}`;
            })
            .catch(() => {
                body.innerHTML = '<div class="bubble-empty">Failed to load metric detail.</div>';
            });
    };

    function switchTab(name) {
        document.querySelectorAll('.bubble-tab').forEach(t => t.classList.toggle('active', t.dataset.tab === name));
        document.querySelectorAll('.bubble-tab-panel').forEach(p => p.classList.toggle('active', p.dataset.panel === name));
        if (name === 'history') loadHistory();
    }

    let historyLoaded = false;
    function loadHistory() {
        if (historyLoaded) return;
        historyLoaded = true;
        fetch('/api/bubble-radar/history')
            .then(r => r.json())
            .then(d => renderHistoryTable(d.results || []))
            .catch(() => {
                const tbody = document.getElementById('br-history-body');
                if (tbody) tbody.innerHTML = '<tr><td colspan="7" class="bubble-empty">Failed to load history.</td></tr>';
            });
    }

    function triggerScan() {
        const btn = document.getElementById('br-run-btn');
        if (btn) { btn.disabled = true; btn.textContent = 'Scanning…'; }
        fetch('/api/bubble-radar/run', { method: 'POST' })
            .then(r => r.json())
            .then(() => {
                if (btn) { btn.disabled = false; btn.textContent = 'Run Scan'; }
                setTimeout(loadFlagged, 5000);
            })
            .catch(() => { if (btn) { btn.disabled = false; btn.textContent = 'Run Scan'; } });
    }

    function loadFlagged() {
        fetch('/api/bubble-radar/data')
            .then(r => r.json())
            .then(d => {
                renderFlaggedTable(d.results || []);
                const ts = document.getElementById('br-last-scan');
                if (ts && d.results && d.results.length) {
                    ts.textContent = 'Last scan: ' + (d.results[0].scan_date || '—');
                }
            })
            .catch(() => {
                const empty = document.getElementById('br-flagged-empty');
                if (empty) { empty.style.display = 'block'; empty.textContent = 'Failed to load data.'; }
            });
    }

    document.addEventListener('DOMContentLoaded', function () {
        loadFlagged();

        document.querySelectorAll('.bubble-tab').forEach(t => {
            t.addEventListener('click', () => switchTab(t.dataset.tab));
        });

        const runBtn = document.getElementById('br-run-btn');
        if (runBtn) runBtn.addEventListener('click', triggerScan);

        const closeBtn = document.getElementById('br-detail-close');
        if (closeBtn) closeBtn.addEventListener('click', () => {
            const panel = document.getElementById('br-detail-panel');
            if (panel) panel.style.display = 'none';
        });
    });
}());
