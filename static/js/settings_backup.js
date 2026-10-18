async function refreshActiveJobs() {
    try {
        const resp = await fetch('/api/system/active-jobs', { cache: 'no-store' });
        const data = await resp.json();
        const restartBanner = document.getElementById('restart-pending-banner');
        if (restartBanner) restartBanner.classList.toggle('d-none', !data.restart_pending);
        const requirementsBanner = document.getElementById('requirements-changed-banner');
        if (requirementsBanner) {
            const install = data.dependency_install || {};
            requirementsBanner.classList.toggle('d-none', !(data.requirements_changed_pending || install.state === 'failed'));
            const change = data.scikit_learn_change_pending;
            if (install.state === 'failed') {
                requirementsBanner.textContent = `⚠️ Dependency installation failed and the service was not restarted. Run this in a shell on the server, then restart the service: ${install.command}`;
            } else if (install.state === 'running') {
                requirementsBanner.textContent = '⚠️ Dependencies are being installed. The service restarts automatically when the installation finishes.';
            } else {
                requirementsBanner.textContent = change
                    ? `⚠️ scikit-learn will change from ${change.from_version} to ${change.to_version}. Dependencies will be reinstalled before restart. After the app starts, affected model retraining will begin automatically, normally within seconds, and run one model family at a time.`
                    : '⚠️ requirements.lock and the installed packages differ. Dependencies will be reinstalled automatically before the service restarts.';
            }
        }
        const el = document.getElementById('active-jobs-display');
        if (!el) return;
        if (!data.busy) {
            el.innerHTML = '<span class="badge badge-success text-sm">No active processes — safe to restart</span>';
        } else {
            const now = Date.now();
            const averages = data.active_job_avg_sec || {};
            const items = Object.entries(data.active_jobs).map(([name, since]) => {
                const mins = Math.round((now - new Date(since + 'Z').getTime()) / 60000);
                const avg = averages[name] == null ? '' : ` · <abbr title="Mean duration of this job's last 20 successful runs.">avg</abbr> ${formatDuration(averages[name])}`;
                return `<li>${name} <span class="text-muted">(running ${mins} min${avg})</span></li>`;
            }).join('');
            el.innerHTML = `<div class="alert alert-warning mb-0"><strong>⚙️ Active processes — avoid restarting:</strong><ul class="mt-5 mb-0">${items}</ul></div>`;
        }
    } catch (_) { }
}
refreshActiveJobs();
setInterval(refreshActiveJobs, 30000);

async function waitForDependencyInstall(statusId, startMessage) {
    const started = Date.now();
    setStatus(statusId, 'warning', startMessage);
    while (true) {
        await new Promise(resolve => setTimeout(resolve, 5000));
        try {
            const resp = await fetch('/api/system/active-jobs', { cache: 'no-store' });
            const install = (await resp.json()).dependency_install || {};
            if (install.state === 'failed') {
                setStatus(statusId, 'error', `Dependency installation failed and the service was not restarted. Run this in a shell on the server, then restart the service: ${install.command}`);
                return false;
            }
            if (install.state !== 'running') return true;
        } catch (_) {
            return true;
        }
        const mins = Math.floor((Date.now() - started) / 60000);
        setStatus(statusId, 'warning', `Installing updated dependencies (${mins} min so far). The service restarts automatically when it finishes.`);
    }
}

async function restartSystem() {
    const btn = document.querySelector('button[onclick="restartSystem()"]');
    btn.disabled = true;
    setStatus('git-status-msg', 'warning', "Sending restart signal...");
    try {
        const response = await stepUpFetch('/api/system/restart', { method: 'POST' });
        const result = await response.json();
        if (response.status === 409) {
            setStatus('git-status-msg', 'warning', result.message);
            btn.disabled = false;
            return;
        }
        if (response.ok) {
            if (result.installing && !(await waitForDependencyInstall('git-status-msg', result.message))) {
                btn.disabled = false;
                return;
            }
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
        const response = await stepUpFetch('/api/system/force-restart', { method: 'POST' });
        const result = await response.json();
        if (response.ok) {
            if (result.installing && !(await waitForDependencyInstall('manual-actions-status', result.message))) {
                btn.disabled = false;
                return;
            }
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
        const resp = await stepUpFetch('/api/system/terminate-jobs', { method: 'POST' });
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
        const response = await stepUpFetch('/api/system/git-pull', { method: 'POST' });
        const result = await response.json();
        if (response.ok) {
            setStatus('git-status-msg', 'success', result.message);
            refreshActiveJobs();
        } else {
            setStatus('git-status-msg', 'error', result.message);
        }
    } catch (error) {
        setStatus('git-status-msg', 'error', "System Error running git pull.");
    }
    btn.disabled = false;
    btn.innerText = "⬇️ Pull Latest from GitHub";
}

let backupCardLoaded = false;

function handleBackupCardToggle(detailsElement) {
    if (detailsElement.open && !backupCardLoaded) {
        backupCardLoaded = true;
        loadBackupStatus();
    }
}

function toggleBackupLocationFields() {
    const isNfs = document.getElementById('BACKUP_LOCATION').value === 'nfs';
    document.getElementById('backup-local-path-group').classList.toggle('d-none', isNfs);
    document.getElementById('backup-nfs-server-group').classList.toggle('d-none', !isNfs);
    document.getElementById('backup-nfs-path-group').classList.toggle('d-none', !isNfs);
    document.getElementById('backup-nfs-setup-note').classList.toggle('d-none', !isNfs);
}

async function runBackupNow() {
    const btn = document.getElementById('backupRunBtn');
    btn.innerText = "Running..."; btn.disabled = true;
    try {
        const resp = await fetch('/api/backup/run', { method: 'POST' });
        const data = await resp.json();
        if (data.status === 'success') {
            setBoxStatus('backup-run-msg', 'success', '✅ ' + data.message);
        } else {
            setBoxStatus('backup-run-msg', 'error', '❌ ' + (data.message || 'Unknown error'));
        }
    } catch (e) {
        setBoxStatus('backup-run-msg', 'error', '❌ Network error triggering backup.');
    }
    setTimeout(() => { btn.innerText = "▶ Run Backup Now"; btn.disabled = false; loadBackupStatus(); }, 8000);
}

function _formatBytes(bytes) {
    if (!bytes) return '0 MB';
    return (bytes / (1024 * 1024)).toFixed(1) + ' MB';
}

async function loadBackupStatus() {
    try {
        const resp = await fetch('/api/backup/status', { cache: 'no-store' });
        const data = await resp.json();
        if (data.status !== 'success') return;

        const last = data.last_backup;
        const lastEl = document.getElementById('diag-backup-last');
        if (lastEl) {
            if (last) {
                lastEl.innerText = last.started_at || 'Unknown';
                const statusEl = document.getElementById('diag-backup-last-status');
                statusEl.innerText = last.status === 'success' ? 'Success' : ('Failed: ' + (last.error_message || 'Unknown error'));
                statusEl.style.color = last.status === 'success' ? '' : '#ff4d4d';
                document.getElementById('diag-backup-components').innerText = last.components || '--';
                document.getElementById('diag-backup-dest').innerText = last.destination || '--';
                document.getElementById('diag-backup-size').innerText = last.size_bytes ? _formatBytes(last.size_bytes) : '--';
            } else {
                lastEl.innerText = 'Never';
                document.getElementById('diag-backup-last-status').innerText = 'No backups recorded yet';
            }
            document.getElementById('diag-backup-count').innerText = data.stored_count;
            document.getElementById('diag-backup-total-size').innerText = _formatBytes(data.stored_size_bytes) + ' total';
        }

        const select = document.getElementById('backup-restore-select');
        if (select) {
            if (!data.backups || data.backups.length === 0) {
                select.replaceChildren(new Option('No backups available', ''));
            } else {
                select.replaceChildren(...data.backups.map(b => {
                    const label = `${b.filename} (${_formatBytes(b.size_bytes)}, ${b.mtime})` + (b.hash_recorded ? '' : ' — no recorded hash');
                    const option = new Option(label, b.filename);
                    option.dataset.hashRecorded = b.hash_recorded ? '1' : '0';
                    return option;
                }));
            }
            syncBackupTrustControl();
        }
    } catch (e) {
        const lastEl = document.getElementById('diag-backup-last');
        if (lastEl) lastEl.innerText = 'Error loading';
    }
}

function syncBackupTrustControl() {
    const select = document.getElementById('backup-restore-select');
    const group = document.getElementById('backup-trust-group');
    const checkbox = document.getElementById('backup-trust-unhashed');
    if (!select || !group || !checkbox) return;
    const selected = select.selectedOptions[0];
    const unhashed = !!selected && selected.value !== '' && selected.dataset.hashRecorded === '0';
    group.classList.toggle('d-none', !unhashed);
    if (!unhashed) checkbox.checked = false;
}

async function restoreSelectedBackup() {
    const select = document.getElementById('backup-restore-select');
    const filename = select.value;
    if (!filename) {
        setBoxStatus('backup-restore-msg', 'warning', '⚠️ Select a backup file first.');
        return;
    }
    if (!confirm(`Restore from "${filename}"? This overwrites the live database, data files, and models. Continue?`)) return;

    const btn = document.querySelector('button[onclick="restoreSelectedBackup()"]');
    btn.disabled = true;
    btn.innerText = '⏳ Restoring…';
    setBoxStatus('backup-restore-msg', 'info', '⏳ Restoring — do not navigate away…');
    try {
        const resp = await stepUpFetch('/api/backup/restore', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename, trust_unhashed: document.getElementById('backup-trust-unhashed').checked }),
        });
        const data = await resp.json();
        if (resp.ok && data.status === 'success') {
            setBoxStatus('backup-restore-msg', 'success', '✅ ' + data.message);
            loadBackupStatus();
        } else {
            setBoxStatus('backup-restore-msg', 'error', '❌ ' + (data.message || 'Restore failed.'));
        }
    } catch (e) {
        setBoxStatus('backup-restore-msg', 'error', '❌ Network error during restore.');
    }
    btn.disabled = false;
    btn.innerText = '⚠ Restore';
}
