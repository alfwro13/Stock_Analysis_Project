async function triggerGhostfolioSync() {
    const btn = document.getElementById('syncBtn');
    btn.innerText = "Syncing JSON..."; btn.disabled = true;
    try { await fetch('/api/sync-ghostfolio', { method: 'POST' }); alert("Sync initiated in the background! Wait a few moments, then hit 'Force Update Analysis'."); } catch (e) { }
    setTimeout(() => { btn.innerText = "⬇️ Sync Ghostfolio Data"; btn.disabled = false; }, 5000);
}

async function triggerUpdate() {
    const btn = document.getElementById('updateBtn');
    btn.innerText = "Updating..."; btn.disabled = true;
    try { await fetch('/api/update', { method: 'POST' }); alert("Analysis initiated in the background! The system is building fresh mathematical models."); } catch (e) { }
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
    outputBox.style.display = 'block';
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

function toggleApiKeyVisibility() {
    const input = document.getElementById('ua_api_key');
    const btn = event.currentTarget;
    if (input.type === 'password') {
        input.type = 'text';
        btn.textContent = '🙈 Hide';
    } else {
        input.type = 'password';
        btn.textContent = '👁 Show';
    }
}

async function generateApiKey() {
    if (!confirm('Generate a new API key? The current key will stop working immediately.')) return;
    setStatus('ua-apikey-msg', 'info', 'Generating…');
    const res = await fetch('/api/generate-api-key', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
        const input = document.getElementById('ua_api_key');
        input.value = data.api_key;
        input.type = 'text';
        setStatus('ua-apikey-msg', 'success', 'New API key generated and saved.');
    } else {
        setStatus('ua-apikey-msg', 'error', data.detail || 'Failed to generate key.');
    }
}

async function changePassword() {
    const current = document.getElementById('ua_current_password').value;
    const newPw   = document.getElementById('ua_new_password').value;
    const confirm = document.getElementById('ua_confirm_password').value;
    setStatus('ua-pw-msg', 'info', 'Updating…');
    const res = await fetch('/api/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json', 'X-Confirm-Token': CONFIRM_TOKEN },
        body: JSON.stringify({ current_password: current, new_password: newPw, confirm_password: confirm }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
        setStatus('ua-pw-msg', 'success', 'Password updated successfully.');
        document.getElementById('ua_current_password').value = '';
        document.getElementById('ua_new_password').value = '';
        document.getElementById('ua_confirm_password').value = '';
    } else {
        setStatus('ua-pw-msg', 'error', data.detail || 'Failed to update password.');
    }
}

async function discoverGhostfolioAccounts() {
    const btn = document.querySelector('button[onclick="discoverGhostfolioAccounts()"]');
    const token = document.getElementById('API_TOKEN').value;
    if (!token) {
        alert("Please enter and save your Ghostfolio URL and API Token before running discovery.");
        return;
    }
    btn.disabled = true;
    btn.innerText = "Querying Ghostfolio API...";
    document.getElementById('discovery-status-msg').innerText = "";
    try {
        await saveSettings(true);
        const response = await fetch('/api/ghostfolio/discover', { method: 'POST' });
        const result = await response.json();
        if (response.ok) {
            setStatus('discovery-status-msg', 'success', result.message);
            setTimeout(() => { window.location.reload(); }, 1500);
        } else {
            setStatus('discovery-status-msg', 'error', result.message);
            btn.disabled = false;
            btn.innerText = "🔍 Discover Accounts on Server";
        }
    } catch (error) {
        setStatus('discovery-status-msg', 'error', "Network Error.");
        btn.disabled = false;
        btn.innerText = "🔍 Discover Accounts on Server";
    }
}

async function refreshActiveJobs() {
    try {
        const resp = await fetch('/api/system/active-jobs', { cache: 'no-store' });
        const data = await resp.json();
        const el = document.getElementById('active-jobs-display');
        if (!el) return;
        if (!data.busy) {
            el.innerHTML = '<span class="badge badge-success text-sm">No active processes — safe to restart</span>';
        } else {
            const now = Date.now();
            const items = Object.entries(data.active_jobs).map(([name, since]) => {
                const mins = Math.round((now - new Date(since + 'Z').getTime()) / 60000);
                return `<li>${name} <span class="text-muted">(running ${mins} min)</span></li>`;
            }).join('');
            el.innerHTML = `<div class="alert alert-warning mb-0"><strong>⚙️ Active processes — avoid restarting:</strong><ul class="mt-5 mb-0">${items}</ul></div>`;
        }
    } catch (_) { }
}
refreshActiveJobs();
setInterval(refreshActiveJobs, 30000);

async function restartSystem() {
    const btn = document.querySelector('button[onclick="restartSystem()"]');
    btn.disabled = true;
    setStatus('git-status-msg', 'warning', "Sending restart signal...");
    try {
        const response = await fetch('/api/system/restart', { method: 'POST', headers: { 'X-Confirm-Token': CONFIRM_TOKEN } });
        const result = await response.json();
        if (response.status === 409) {
            setStatus('git-status-msg', 'warning', result.message);
            btn.disabled = false;
            return;
        }
        if (response.ok) {
            setStatus('git-status-msg', 'success', "Signal sent. Waiting for service to come back up...");
            await new Promise(resolve => setTimeout(resolve, 3000));
            const maxWait = 60000;
            const pollInterval = 2000;
            const started = Date.now();
            const poll = setInterval(async () => {
                const elapsed = Math.round((Date.now() - started) / 1000);
                setStatus('git-status-msg', 'success', `Waiting for service to come back up... (${elapsed}s)`);
                try {
                    const check = await fetch('/', { method: 'HEAD', cache: 'no-store' });
                    if (check.status < 500) { clearInterval(poll); window.location.reload(); }
                } catch (_) { }
                if (Date.now() - started > maxWait) {
                    clearInterval(poll);
                    setStatus('git-status-msg', 'error', "Service is taking longer than expected. Please refresh manually.");
                    btn.disabled = false;
                }
            }, pollInterval);
        } else {
            setStatus('git-status-msg', 'error', result.message);
            btn.disabled = false;
        }
    } catch (error) {
        setStatus('git-status-msg', 'error', "Network Error.");
        btn.disabled = false;
    }
}

async function forceRestart() {
    const btn = document.querySelector('button[onclick="forceRestart()"]');
    btn.disabled = true;
    setStatus('manual-actions-status', 'warning', "Sending force restart signal...");
    try {
        const response = await fetch('/api/system/force-restart', { method: 'POST', headers: { 'X-Confirm-Token': CONFIRM_TOKEN } });
        const result = await response.json();
        if (response.ok) {
            setStatus('manual-actions-status', 'success', "Signal sent. Waiting for service to come back up...");
            await new Promise(resolve => setTimeout(resolve, 3000));
            const maxWait = 60000;
            const pollInterval = 2000;
            const started = Date.now();
            const poll = setInterval(async () => {
                const elapsed = Math.round((Date.now() - started) / 1000);
                setStatus('manual-actions-status', 'success', `Waiting for service to come back up... (${elapsed}s)`);
                try {
                    const check = await fetch('/', { method: 'HEAD', cache: 'no-store' });
                    if (check.status < 500) { clearInterval(poll); window.location.reload(); }
                } catch (_) { }
                if (Date.now() - started > maxWait) {
                    clearInterval(poll);
                    setStatus('manual-actions-status', 'error', "Service is taking longer than expected. Please refresh manually.");
                    btn.disabled = false;
                }
            }, pollInterval);
        } else {
            setStatus('manual-actions-status', 'error', result.message);
            btn.disabled = false;
        }
    } catch (_) {
        setStatus('manual-actions-status', 'error', "Network error.");
        btn.disabled = false;
    }
}

async function showTerminateConfirm() {
    const panel = document.getElementById('terminate-confirm-panel');
    const list = document.getElementById('terminate-job-list');
    try {
        const resp = await fetch('/api/system/active-jobs', { cache: 'no-store' });
        const data = await resp.json();
        if (!data.busy) {
            setStatus('manual-actions-status', 'warning', "No active jobs to terminate.");
            return;
        }
        const now = Date.now();
        list.innerHTML = Object.entries(data.active_jobs).map(([name, since]) => {
            const mins = Math.round((now - new Date(since + 'Z').getTime()) / 60000);
            return `<li><strong>${name}</strong> <span class="text-muted">(running ${mins} min)</span></li>`;
        }).join('');
    } catch (_) {
        list.innerHTML = '<li>Could not retrieve job list.</li>';
    }
    panel.classList.remove('d-none');
    document.getElementById('manual-actions-status').innerHTML = '';
}

function cancelTerminate() {
    document.getElementById('terminate-confirm-panel').classList.add('d-none');
}

async function confirmTerminate() {
    const btn = document.querySelector('button[onclick="confirmTerminate()"]');
    btn.disabled = true;
    btn.innerText = "Terminating...";
    try {
        const resp = await fetch('/api/system/terminate-jobs', { method: 'POST', headers: { 'X-Confirm-Token': CONFIRM_TOKEN } });
        const data = await resp.json();
        if (resp.ok) {
            const names = data.terminated || [];
            const msg = names.length
                ? `Cleared ${names.length} job(s): ${names.join(', ')}. They will not resume.`
                : "No active jobs found — registry was already empty.";
            document.getElementById('terminate-confirm-panel').classList.add('d-none');
            setStatus('manual-actions-status', 'success', msg);
            refreshActiveJobs();
        } else {
            setStatus('manual-actions-status', 'error', data.message || "Failed to terminate jobs.");
        }
    } catch (_) {
        setStatus('manual-actions-status', 'error', "Network error.");
    }
    btn.disabled = false;
    btn.innerText = "Confirm — Terminate All Listed Jobs";
}

async function gitPull() {
    const btn = document.querySelector('button[onclick="gitPull()"]');
    btn.disabled = true;
    btn.innerText = "Pulling repository...";
    document.getElementById('git-status-msg').innerText = "";
    try {
        const response = await fetch('/api/system/git-pull', { method: 'POST', headers: { 'X-Confirm-Token': CONFIRM_TOKEN } });
        const result = await response.json();
        if (response.ok) {
            setStatus('git-status-msg', 'success', result.message);
        } else {
            setStatus('git-status-msg', 'error', result.message);
        }
    } catch (error) {
        setStatus('git-status-msg', 'error', "System Error running git pull.");
    }
    btn.disabled = false;
    btn.innerText = "⬇️ Pull Latest from GitHub";
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

async function saveGhostfolioSettings() {
    const btn = document.querySelector('button[onclick="saveGhostfolioSettings()"]');
    btn.disabled = true;
    btn.innerText = '⏳ Saving…';
    setStatus('ghostfolio-creds-msg', 'info', 'Saving…');
    try {
        const res = await fetch('/api/save-ghostfolio-settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Confirm-Token': CONFIRM_TOKEN },
            body: JSON.stringify({
                GHOSTFOLIO_URL: document.getElementById('GHOSTFOLIO_URL').value.trim(),
                GHOSTFOLIO_TOKEN: document.getElementById('API_TOKEN').value.trim(),
            }),
        });
        const data = await res.json().catch(() => ({}));
        setStatus('ghostfolio-creds-msg', res.ok ? 'success' : 'error',
            res.ok ? 'Ghostfolio credentials saved.' : (data.detail || 'Failed to save.'));
    } catch (e) {
        setStatus('ghostfolio-creds-msg', 'error', 'Network error while saving.');
    } finally {
        btn.disabled = false;
        btn.innerText = '💾 Save Ghostfolio Credentials';
    }
}

async function saveNextcloudSettings() {
    const btn = document.querySelector('button[onclick="saveNextcloudSettings()"]');
    btn.disabled = true;
    btn.innerText = '⏳ Saving…';
    setStatus('nc-status-msg', 'info', 'Saving…');
    const payload = {
        NEXTCLOUD_URL: document.getElementById('NEXTCLOUD_URL').value.trim(),
        BOT_USERNAME: document.getElementById('BOT_USERNAME').value.trim(),
        APP_PASSWORD: document.getElementById('APP_PASSWORD').value,
        CONVERSATION_TOKEN: document.getElementById('CONVERSATION_TOKEN').value.trim(),
    };
    try {
        const res = await fetch('/api/save-nextcloud-settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Confirm-Token': CONFIRM_TOKEN },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            setStatus('nc-status-msg', 'success', 'Nextcloud settings saved to .env.');
        } else {
            setStatus('nc-status-msg', 'error', data.detail || 'Failed to save.');
        }
    } catch (e) {
        setStatus('nc-status-msg', 'error', 'Network error while saving.');
    } finally {
        btn.disabled = false;
        btn.innerText = '💾 Save Nextcloud Settings';
    }
}

async function testNextcloudMessage() {
    const btn = document.querySelector('button[onclick="testNextcloudMessage()"]');
    btn.disabled = true;
    btn.innerText = '⏳ Sending…';
    setStatus('nc-status-msg', 'info', 'Sending test message…');
    try {
        const res = await fetch('/api/test-nextcloud-message', { method: 'POST', headers: { 'X-Confirm-Token': CONFIRM_TOKEN } });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            setStatus('nc-status-msg', 'success', data.message || 'Test message sent successfully.');
        } else {
            setStatus('nc-status-msg', 'error', data.message || 'Send failed.');
        }
    } catch (e) {
        setStatus('nc-status-msg', 'error', 'Network error while sending test.');
    } finally {
        btn.disabled = false;
        btn.innerText = '🧪 Send Test Message';
    }
}

async function saveAccountEmail() {
    const btn = document.querySelector('button[onclick="saveAccountEmail()"]');
    btn.disabled = true;
    btn.innerText = '⏳ Saving…';
    setStatus('ua-email-msg', 'info', 'Saving…');
    try {
        const res = await fetch('/api/save-account-email', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Confirm-Token': CONFIRM_TOKEN },
            body: JSON.stringify({ email: document.getElementById('ua_account_email').value }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            setStatus('ua-email-msg', 'success', 'Email saved.');
        } else {
            setStatus('ua-email-msg', 'error', data.detail || 'Failed to save email.');
        }
    } catch (e) {
        setStatus('ua-email-msg', 'error', 'Network error while saving.');
    } finally {
        btn.disabled = false;
        btn.innerText = '💾 Save Email';
    }
}

async function changeUsername() {
    const btn = document.querySelector('button[onclick="changeUsername()"]');
    btn.disabled = true;
    btn.innerText = '⏳ Saving…';
    setStatus('ua-username-msg', 'info', 'Updating…');
    try {
        const res = await fetch('/api/change-username', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Confirm-Token': CONFIRM_TOKEN },
            body: JSON.stringify({ new_username: document.getElementById('ua_new_username').value }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            const newName = document.getElementById('ua_new_username').value.trim();
            document.getElementById('ua-username-display').innerText = newName;
            document.getElementById('ua_new_username').value = '';
            setStatus('ua-username-msg', 'success', 'Username updated.');
        } else {
            setStatus('ua-username-msg', 'error', data.detail || 'Failed to update username.');
        }
    } catch (e) {
        setStatus('ua-username-msg', 'error', 'Network error while saving.');
    } finally {
        btn.disabled = false;
        btn.innerText = '👤 Update Username';
    }
}

async function rotateAppSecret() {
    if (!confirm('Rotating the app secret will log out ALL active sessions immediately. Continue?')) return;
    const btn = document.querySelector('button[onclick="rotateAppSecret()"]');
    btn.disabled = true;
    btn.innerText = '⏳ Rotating…';
    setStatus('ua-secret-msg', 'info', 'Rotating…');
    try {
        const res = await fetch('/api/rotate-app-secret', {
            method: 'POST',
            headers: { 'X-Confirm-Token': CONFIRM_TOKEN },
        });
        const data = await res.json().catch(() => ({}));
        setStatus('ua-secret-msg', res.ok ? 'success' : 'error',
            res.ok ? 'App secret rotated. All sessions have been invalidated.' : (data.detail || 'Failed to rotate.'));
    } catch (e) {
        setStatus('ua-secret-msg', 'error', 'Network error while rotating.');
    } finally {
        btn.disabled = false;
        btn.innerText = '🔄 Rotate App Secret';
    }
}

async function rotateConfirmToken() {
    if (!confirm('Rotating the confirm token will update CONFIRM_TOKEN on this page. Continue?')) return;
    const btn = document.querySelector('button[onclick="rotateConfirmToken()"]');
    btn.disabled = true;
    btn.innerText = '⏳ Rotating…';
    setStatus('ua-confirmtoken-msg', 'info', 'Rotating…');
    try {
        const res = await fetch('/api/rotate-confirm-token', {
            method: 'POST',
            headers: { 'X-Confirm-Token': CONFIRM_TOKEN },
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            if (data.new_token) CONFIRM_TOKEN = data.new_token;
            setStatus('ua-confirmtoken-msg', 'success', 'Confirm token rotated and updated in-page.');
        } else {
            setStatus('ua-confirmtoken-msg', 'error', data.detail || 'Failed to rotate.');
        }
    } catch (e) {
        setStatus('ua-confirmtoken-msg', 'error', 'Network error while rotating.');
    } finally {
        btn.disabled = false;
        btn.innerText = '🔄 Rotate Confirm Token';
    }
}

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

document.addEventListener('DOMContentLoaded', () => {
    fetchNetworkStatus();
    setInterval(fetchNetworkStatus, 10000);
    fetchSystemChecks();
});
