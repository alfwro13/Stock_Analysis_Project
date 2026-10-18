async function triggerUpdate() {
    const btn = document.getElementById('updateBtn');
    btn.innerText = "Updating..."; btn.disabled = true;
    try {
        const job = await postJobTrigger('/api/update');
        alert(job.ok ? "Analysis initiated in the background! The system is building fresh mathematical models." : (job.message || "The server did not start the job."));
    } catch (e) { }
    setTimeout(() => { btn.innerText = "↻ Force Update Analysis Models"; btn.disabled = false; }, 15000);
}

async function runMaintenanceNow() {
    const btn = document.getElementById('maintenanceRunBtn');
    btn.innerText = "Running..."; btn.disabled = true;
    try {
        const resp = await fetch('/api/maintenance/run', { method: 'POST' });
        const data = await resp.json();
        if (data.status === 'success') {
            setBoxStatus('maintenance-dry-run-content', 'success', '✅ ' + data.message);
        } else {
            alert('Maintenance failed: ' + (data.message || 'Unknown error'));
        }
    } catch (e) {
        alert('Network error triggering maintenance.');
    }
    setTimeout(() => { btn.innerText = "▶ Run Now"; btn.disabled = false; }, 8000);
}

async function runMaintenanceDryRun() {
    const btn = document.getElementById('maintenanceDryRunBtn');
    const outputBox = document.getElementById('maintenance-dry-run-output');
    const content = document.getElementById('maintenance-dry-run-content');
    btn.innerText = "Scanning..."; btn.disabled = true;
    outputBox.classList.remove('d-none');
    content.innerText = 'Scanning file system...';
    try {
        const resp = await fetch('/api/maintenance/dry-run', { method: 'POST' });
        const data = await resp.json();
        if (data.status === 'success') {
            const r = data.results;
            const s = r.summary;
            let out = `DRY RUN — no files were deleted\n`;
            out += `Retention threshold : ${r.days_to_keep_files} days\n`;
            out += `Active tickers in DB: ${r.active_tickers_count}\n`;
            out += `─────────────────────────────────────────\n`;
            out += `Would DELETE  : ${s.delete_count} file(s)\n`;
            out += `Kept (active) : ${s.keep_active_count} file(s)\n`;
            out += `Kept (fresh)  : ${s.keep_fresh_count} file(s)\n`;
            if (r.would_delete.length > 0) {
                out += `\n── Files that WOULD be deleted ──\n`;
                r.would_delete.forEach(f => { out += `  ✗  ${f.file}  (${f.age_days}d old)\n`; });
            }
            if (r.would_keep_fresh.length > 0) {
                out += `\n── Files kept because they are not yet old enough ──\n`;
                r.would_keep_fresh.forEach(f => { out += `  ⏳  ${f.file}  (${f.reason})\n`; });
            }
            content.innerText = out;
        } else {
            content.innerText = 'Error: ' + (data.message || 'Unknown error');
        }
    } catch (e) {
        content.innerText = 'Network error during dry run.';
    }
    btn.innerText = "🔍 Dry Run"; btn.disabled = false;
}

async function fetchNetworkStatus() {
    try {
        const response = await fetch('/api/settings/network-status');
        const data = await response.json();
        const badge = document.getElementById('network-route-badge');
        const details = document.getElementById('network-route-details');
        badge.innerText = data.route;
        if (data.indicator === 'green') {
            setBoxStatus('network-route-badge', 'success', data.route);
        } else if (data.indicator === 'yellow') {
            setBoxStatus('network-route-badge', 'warning', data.route);
        } else {
            setBoxStatus('network-route-badge', 'error', data.route);
        }
        details.innerText = data.message;
    } catch (e) {
        console.error("Failed to fetch network status", e);
    }
}

async function fetchSystemChecks() {
    try {
        const data = await fetch('/api/system/checks').then(r => r.json());
        const banner = document.getElementById('system-checks-banner');
        if (!data.issues || data.issues.length === 0) { banner.hidden = true; return; }
        const hasError = data.issues.some(i => i.level === 'error');
        const colorClass = hasError ? 'macro-banner-red' : 'macro-banner-yellow';
        const icon = hasError ? '✖' : '⚠';
        const title = hasError ? 'System Errors Detected' : 'Configuration Warnings';
        const items = data.issues.map(i => `<li>${i.message}</li>`).join('');
        banner.className = `macro-banner ${colorClass} settings-health-banner`;
        banner.innerHTML = `<strong>${icon} ${title}</strong><ul class="system-checks-list">${items}</ul>`;
        banner.hidden = false;
    } catch (_) { }
}

async function testYahooIPv6() {
    const btn = document.getElementById('testIpv6Btn');
    const ipv6Addr = document.getElementById('YAHOO_IPV6_ADDRESS').value.trim();
    if (!ipv6Addr) {
        setBoxStatus('test-ipv6-msg', 'warning', "⚠️ Please enter an IPv6 address before testing.");
        return;
    }
    btn.disabled = true;
    btn.innerText = "⏳ Testing Socket...";
    setBoxStatus('test-ipv6-msg', 'info', "Negotiating IPv6 socket connection with Yahoo Finance edge nodes...");
    try {
        const response = await fetch('/api/settings/test-yahoo-ipv6', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ ipv6_address: ipv6Addr })
        });
        const result = await response.json();
        if (response.ok) {
            setBoxStatus('test-ipv6-msg', 'success', result.message);
            btn.classList.add('btn-green');
            btn.classList.remove('btn-cyan', 'btn-red');
        } else {
            setBoxStatus('test-ipv6-msg', 'error', result.message);
            btn.classList.add('btn-red');
            btn.classList.remove('btn-cyan', 'btn-green');
        }
    } catch (error) {
        setBoxStatus('test-ipv6-msg', 'error', "Network Error: Could not reach the backend API to initiate the test.");
        btn.classList.add('btn-red');
        btn.classList.remove('btn-cyan', 'btn-green');
    }
    setTimeout(() => {
        btn.disabled = false;
        btn.innerText = "🧪 Test IPv6 Connection";
        btn.classList.add('btn-cyan');
        btn.classList.remove('btn-green', 'btn-red');
    }, 3000);
}

let metricsLoaded = false;

function handleDiagnosticsToggle(detailsElement) {
    if (detailsElement.open && !metricsLoaded) {
        fetchSystemMetrics();
    }
    if (detailsElement.open) {
        loadBackupStatus();
    }
}

async function fetchSystemMetrics() {
    try {
        const response = await fetch('/api/system/metrics');
        const data = await response.json();
        if (response.ok && data.status === 'success') {
            const fmt = (num) => num.toLocaleString();
            const pct = (num, denom) => denom > 0 ? ((num / denom) * 100).toFixed(1) + '%' : '0.0%';

            document.getElementById('diag-univ-total').innerText = fmt(data.universe.total);
            document.getElementById('diag-univ-idx').innerText = fmt(data.universe.index);
            document.getElementById('diag-univ-idx-sub').innerText = `SP: ${data.universe.sp500} / FTSE: ${data.universe.ftse}`;
            document.getElementById('diag-univ-ft').innerText = fmt(data.universe.freetrade);
            const idxBase = data.universe.index;
            document.getElementById('diag-cov-ss').innerText = fmt(data.universe.coverage.stock_signals);
            document.getElementById('diag-cov-ss-pct').innerText = pct(data.universe.coverage.stock_signals, idxBase);
            document.getElementById('diag-cov-qs').innerText = fmt(data.universe.coverage.quant_signals);
            document.getElementById('diag-cov-qs-pct').innerText = pct(data.universe.coverage.quant_signals, idxBase);
            document.getElementById('diag-cov-ap').innerText = fmt(data.universe.coverage.asset_profiles);
            document.getElementById('diag-cov-ap-pct').innerText = pct(data.universe.coverage.asset_profiles, idxBase);

            if (data.ml.ensemble.exists) {
                document.getElementById('diag-ml-state').innerText = `${data.ml.ensemble.size_mb} MB`;
                document.getElementById('diag-ml-sub').innerText = `${data.ml.ensemble.mtime}`;
            } else {
                document.getElementById('diag-ml-state').innerText = "Missing";
                document.getElementById('diag-ml-state').style.color = "#ff4d4d";
                document.getElementById('diag-ml-sub').innerText = "Awaiting Training";
            }
            document.getElementById('diag-ml-feat').innerText = `${data.ml.feature_count} Dimensions`;
            {
                const cov = data.ml.inference_coverage ?? 0;
                const thr = data.ml.inference_threshold ?? 0;
                const trainSz = data.ml.train_universe_size ?? 0;
                const covEl = document.getElementById('diag-ml-cov');
                const covSub = document.getElementById('diag-ml-cov-sub');
                covEl.innerText = `${cov} tickers`;
                covEl.style.color = (thr > 0 && cov < thr) ? '#ff4d4d' : '#4caf50';
                covSub.innerText = thr > 0 ? `min ${thr} (25% of ${trainSz})` : 'Threshold unknown';
            }
            document.getElementById('diag-mac-hmm').innerText = fmt(data.ml.macro_hmm_outputs);
            document.getElementById('diag-mac-rf').innerText = fmt(data.ml.macro_rf_outputs);
            document.getElementById('diag-mac-ind').innerText = fmt(data.state.macro_ind);
            const staleCount = data.ml.anomaly_stale_count || 0;
            const staleLabel = staleCount > 0 ? ` <span style="color:#ff4d4d">(${staleCount} stale)</span>` : '';
            document.getElementById('diag-anomaly-cnt').innerHTML = `${fmt(data.ml.anomaly_model_count)} Models${staleLabel}`;
            if (data.ml.anomaly_model_count === 0) document.getElementById('diag-anomaly-cnt').style.color = "#ffaa00";
            document.getElementById('diag-notes-sent').innerText = `${fmt(data.state.notes_sent)} Sent`;
            document.getElementById('diag-notes-pending').innerText = `${fmt(data.state.notes_pending)} Pending`;
            if (data.state.notes_pending > 0) document.getElementById('diag-notes-pending').style.color = "#ffaa00";

            document.getElementById('diag-db-size').innerText = `${data.infra.db_size_mb} MB`;
            document.getElementById('diag-hist-size').innerText = `${data.infra.hist_size_mb} MB`;
            document.getElementById('diag-hist-cnt').innerText = `${fmt(data.infra.hist_cnt)} Files`;
            document.getElementById('diag-intra-size').innerText = `${data.infra.intra_size_mb} MB`;
            document.getElementById('diag-intra-cnt').innerText = `${fmt(data.infra.intra_cnt)} Files`;
            document.getElementById('diag-fund-cnt').innerText = `${fmt(data.universe.fundamentals_files)} Files`;
            document.getElementById('diag-json-port').innerText = `${fmt(data.universe.json_trackers.portfolio)} Items`;
            document.getElementById('diag-json-watch').innerText = `${fmt(data.universe.json_trackers.watchlist)} Items`;
            document.getElementById('diag-json-bl').innerText = `${fmt(data.universe.json_trackers.blacklist)} Items`;

            document.getElementById('diag-cpu').innerText = data.infra.cpu.join(', ');
            document.getElementById('diag-disk-tot').innerText = `${data.infra.disk_total_gb} GB`;
            document.getElementById('diag-disk-used').innerText = `${data.infra.disk_used_gb} GB`;
            document.getElementById('diag-disk-pct').innerText = `${data.infra.disk_pct}% Used`;

            if (data.scheduler_last_runs) {
                const sortMap = data.scheduler_last_runs_sort || {};
                document.querySelectorAll('[data-sched-key]').forEach(td => {
                    const key = td.getAttribute('data-sched-key');
                    const val = data.scheduler_last_runs[key];
                    td.dataset.sort = sortMap[key] || '';
                    if (val && val !== 'Never') {
                        td.innerText = val;
                        td.style.color = '';
                    } else {
                        td.innerText = 'Never';
                        td.style.color = '#666';
                    }
                });
            }

            document.getElementById('metrics-loading').classList.add('d-none');
            document.getElementById('metrics-content').classList.remove('d-none');
            metricsLoaded = true;
        } else {
            document.getElementById('metrics-loading').innerText = "❌ Failed to load diagnostics.";
            document.getElementById('metrics-loading').classList.replace('msg-cyan', 'msg-error');
        }
    } catch (error) {
        document.getElementById('metrics-loading').innerText = "❌ Network error fetching diagnostics.";
        document.getElementById('metrics-loading').classList.replace('msg-cyan', 'msg-error');
    }
}

let schedulerRunHistoryTable = null;

async function openSchedulerRunHistory(jobId, label) {
    const title = document.getElementById('schedulerRunHistoryTitle');
    const body = document.getElementById('schedulerRunHistoryBody');
    if (schedulerRunHistoryTable) {
        schedulerRunHistoryTable.destroy();
        schedulerRunHistoryTable = null;
    }
    title.textContent = `${label} — Last 20 Runs`;
    body.innerHTML = '<tr><td colspan="6" class="text-muted">Loading run history…</td></tr>';
    bootstrap.Modal.getOrCreateInstance(document.getElementById('schedulerRunHistoryModal')).show();

    try {
        const response = await fetch(`/api/system/scheduler-jobs/${encodeURIComponent(jobId)}/runs`, { cache: 'no-store' });
        const data = await response.json();
        if (!response.ok || data.status !== 'success') {
            body.innerHTML = `<tr><td colspan="6" class="text-danger">${escapeHtml(data.message || 'Failed to load run history.')}</td></tr>`;
            return;
        }
        if (!data.runs.length) {
            body.innerHTML = '<tr><td colspan="6" class="text-muted">No completed runs have been recorded yet.</td></tr>';
            return;
        }
        body.innerHTML = data.runs.map(run => {
            const source = run.trigger_source === 'manual' ? 'Manual' : 'Scheduled';
            const statusClass = run.status === 'success' ? 'text-bg-success' : 'text-bg-danger';
            const status = run.status === 'success' ? 'Success' : 'Error';
            return `<tr>
                <td class="font-monospace">${escapeHtml(run.started_at_display)}</td>
                <td class="font-monospace">${escapeHtml(run.finished_at_display)}</td>
                <td>${escapeHtml(formatDuration(run.duration_sec))}</td>
                <td>${source}</td>
                <td><span class="badge ${statusClass}">${status}</span></td>
                <td class="text-break">${escapeHtml(run.error_detail || '—')}</td>
            </tr>`;
        }).join('');
        schedulerRunHistoryTable = $('#schedulerRunHistoryTable').DataTable({
            responsive: true,
            pageLength: 20,
            lengthChange: false,
            searching: false,
            info: false,
            order: [],
            columnDefs: [
                { responsivePriority: 1, targets: [0, 2, 4] },
                { responsivePriority: 2, targets: [1, 3] },
                { responsivePriority: 3, targets: 5 }
            ]
        });
    } catch (error) {
        body.innerHTML = '<tr><td colspan="6" class="text-danger">Network error while loading run history.</td></tr>';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.scheduler-job-history-btn').forEach(button => {
        button.addEventListener('click', () => openSchedulerRunHistory(button.dataset.jobId, button.dataset.jobLabel));
        button.addEventListener('keydown', event => {
            if (event.key === 'Enter' || event.key === ' ') {
                event.preventDefault();
                button.click();
            }
        });
    });
});

let _schedSortAsc = true;
function sortSchedulerMatrix() {
    const tbody = document.querySelector('#scheduler-matrix tbody');
    if (!tbody) return;
    _schedSortAsc = !_schedSortAsc;
    const dir = _schedSortAsc ? 1 : -1;
    Array.from(tbody.rows)
        .sort((a, b) => {
            const av = a.querySelector('[data-sched-key]')?.dataset.sort || '';
            const bv = b.querySelector('[data-sched-key]')?.dataset.sort || '';
            return av === bv ? 0 : (av > bv ? dir : -dir);
        })
        .forEach(r => tbody.appendChild(r));
    const arrow = document.getElementById('sched-sort-arrow');
    if (arrow) arrow.textContent = _schedSortAsc ? '▲' : '▼';
}

function loadYahooApiStats() {
    fetch('/api/system/yahoo-api-stats')
        .then(r => r.json())
        .then(data => {
            const container = document.getElementById('yahoo-api-stats-container');
            if (!container) return;
            if (!data.rows || data.rows.length === 0) {
                container.innerHTML = '<p class="text-muted">No data yet — stats are recorded as Yahoo Finance requests are made.</p>';
                return;
            }
            let html = '<table class="table table-hover table-sm w-100"><thead><tr>'
                + '<th>Date</th><th>Total</th><th>IPv4</th><th>IPv6</th><th>429s</th><th>Errors</th>'
                + '<th><abbr title="ERROR-level lines yfinance itself logged (e.g. a ticker with no data for the requested range, a 404 on a module Yahoo does not support for that instrument) without raising an exception — so the request did not count as a failure above.">yfinance-logged</abbr></th>'
                + '</tr></thead><tbody>';
            data.rows.forEach(r => {
                const has429 = r.rate_limit_429 > 0;
                const hasErr = r.other_errors > 0;
                const hasYfLogged = r.yfinance_logged_errors > 0;
                html += '<tr class="yahoo-stats-row-clickable" title="Click to view usage detail chart" onclick="window.open(\'/yahoo-api-usage?date=' + r.date + '\', \'_blank\')">'
                    + '<td>' + r.date + '</td>'
                    + '<td>' + r.total_calls + '</td>'
                    + '<td>' + r.ipv4_calls + '</td>'
                    + '<td>' + r.ipv6_calls + '</td>'
                    + '<td class="' + (has429 ? 'text-warning' : '') + '">' + r.rate_limit_429 + '</td>'
                    + '<td class="' + (hasErr ? 'text-danger' : '') + '">' + r.other_errors + '</td>'
                    + '<td class="' + (hasYfLogged ? 'text-warning' : '') + '">' + r.yfinance_logged_errors + '</td>'
                    + '</tr>';
            });
            html += '</tbody></table>';
            container.innerHTML = html;
        })
        .catch(() => {
            const container = document.getElementById('yahoo-api-stats-container');
            if (container) container.innerHTML = '<p class="text-danger">Failed to load API stats.</p>';
        });
}

document.addEventListener('DOMContentLoaded', () => {
    fetchNetworkStatus();
    setInterval(fetchNetworkStatus, 10000);
    fetchSystemChecks();
    loadYahooApiStats();
});
