async function triggerFreetradeSync() {
    const btn = document.getElementById('ftSyncBtn');
    btn.innerText = "Syncing Universe...";
    btn.disabled = true;
    setBoxStatus('ft-sync-status-msg', 'warning', "⏳ Sync initiated in the background. Resolving ISINs...");
    try {
        const response = await fetch('/api/trigger-freetrade-sync', { method: 'POST' });
        if (response.ok) {
            setBoxStatus('ft-sync-status-msg', 'success', "✅ Sync initiated successfully.");
        } else {
            setStatus('ft-sync-status-msg', 'error', "Failed to initiate sync. Server returned an error.");
        }
    } catch (e) {
        setStatus('ft-sync-status-msg', 'error', "Failed to initiate sync. Check your network or server logs.");
    }
    setTimeout(() => {
        btn.innerText = "⬇️ Sync Freetrade Universe";
        btn.disabled = false;
    }, 5000);
}

async function scanImportDirectory() {
    const container = document.getElementById('csvSelectContainer');
    const select = document.getElementById('serverCsvSelect');
    setStatus('upload-status-msg', 'info', "🔍 Scanning...");
    container.classList.add('d-none');
    container.classList.remove('flex-gap-15');
    try {
        const response = await fetch('/api/universe/imports/list');
        const result = await response.json();
        if (response.ok && result.status === 'success') {
            if (result.files && result.files.length > 0) {
                select.innerHTML = '';
                result.files.forEach(file => {
                    const opt = document.createElement('option');
                    opt.value = file;
                    opt.innerText = file;
                    select.appendChild(opt);
                });
                container.classList.remove('d-none');
                container.classList.add('flex-gap-15');
                setStatus('upload-status-msg', 'success', `Found ${result.files.length} CSV file(s).`);
            } else {
                setStatus('upload-status-msg', 'warning', "No CSV files found in the data/imports/ directory.");
            }
        } else {
            setStatus('upload-status-msg', 'error', result.message || "Could not scan directory.");
        }
    } catch (error) {
        setStatus('upload-status-msg', 'error', "Network Error during scan.");
    }
}

async function importServerCSV() {
    const select = document.getElementById('serverCsvSelect');
    const btn = document.getElementById('importBtn');
    const filename = select.value;
    if (!filename) {
        setStatus('upload-status-msg', 'error', "Please select a file to import.");
        return;
    }
    btn.disabled = true;
    btn.innerText = "📥 Importing...";
    setStatus('upload-status-msg', 'info', `📤 Processing ${filename}...`);
    try {
        const response = await fetch('/api/universe/import/server', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename: filename })
        });
        const result = await response.json();
        if (response.ok) {
            setStatus('upload-status-msg', 'success', result.message);
        } else {
            setStatus('upload-status-msg', 'error', result.message);
        }
    } catch (error) {
        setStatus('upload-status-msg', 'error', "Network Error during import.");
    }
    btn.disabled = false;
    btn.innerText = "📥 Import Selected";
}

async function triggerIndexScrape() {
    try {
        await fetch('/api/universe/sync-indices', { method: 'POST' });
        alert("Index Constituent scraping initiated in background. Check notifications for updates.");
    } catch (error) {
        alert("Failed to initiate Scraper. Network or server error.");
    }
}

async function triggerFundamentalsProfiler() {
    try {
        await fetch('/api/universe/sync-profiler', { method: 'POST' });
        alert("Fundamentals Profiler initiated in background. Check notifications for updates.");
    } catch (error) {
        alert("Failed to initiate Profiler. Network or server error.");
    }
}

async function triggerUniverseDeepSync() {
    const btn = document.querySelector('button[onclick="triggerUniverseDeepSync()"]');
    if (btn) {
        btn.disabled = true;
        btn.innerText = "▶️ Initiating Sync...";
    }
    try {
        await fetch('/api/universe/deep-sync', { method: 'POST' });
        alert("Universe Deep Sync Pipeline initiated in background. Sequencing fundamentals → metadata → technicals → ML inference for the full index universe. Estimated runtime: 30–45 minutes. Check notifications for progress.");
    } catch (error) {
        alert("Failed to initiate Universe Deep Sync. Network or server error.");
    }
    setTimeout(() => {
        if (btn) {
            btn.disabled = false;
            btn.innerText = "▶️ Run Sync Now";
        }
    }, 3000);
}

async function triggerUniverseUpdate() {
    try {
        await fetch('/api/trigger-universe-update', { method: 'POST' });
        alert("Market Universe update initiated in background. Check notifications for progress updates.");
    } catch (error) {
        alert("Failed to initiate Universe Update. Network or server error.");
    }
}

async function triggerUniverseQuantScan() {
    try {
        await fetch('/api/trigger-universe-quant-scan', { method: 'POST' });
        alert("Full 4K Quant Scan initiated in background. This will take over an hour. Check notifications for progress updates.");
    } catch (error) {
        alert("Failed to initiate Full Quant Scan. Network or server error.");
    }
}

async function triggerNewsFetch() {
    const btn = document.querySelector('button[onclick="triggerNewsFetch()"]');
    const status = document.getElementById('news-fetch-status');
    btn.disabled = true;
    btn.textContent = '⏳ Queuing...';
    status.textContent = '';
    try {
        const resp = await fetch('/api/news-feed/run-now', { method: 'POST' });
        const data = await resp.json();
        status.textContent = data.message || 'Queued.';
        status.style.color = '#4ec957';
    } catch (error) {
        status.textContent = 'Error — check server logs.';
        status.style.color = '#ff4d4d';
    } finally {
        setTimeout(() => {
            btn.disabled = false;
            btn.textContent = '▶ Fetch News Now';
            status.textContent = '';
        }, 4000);
    }
}

async function fetchProfilerQueueStatus() {
    const eligibleEl = document.getElementById('profiler-eligible-badge');
    const profiledEl = document.getElementById('profiler-profiled-badge');
    const staleEl    = document.getElementById('profiler-stale-badge');
    const pendingEl  = document.getElementById('profiler-pending-badge');
    const hintEl     = document.getElementById('profiler-status-hint');
    try {
        const response = await fetch('/api/universe/profiler-status');
        const data = await response.json();
        if (!response.ok || data.status !== 'success') {
            hintEl.innerText = "Failed to fetch profiler queue status.";
            return;
        }
        const b = data.breakdown || {};
        const eligible = b.eligible_count || 0;
        const profiled = b.profiled_count || 0;
        const stale    = b.stale_count    || 0;
        const pending  = b.pending_count  || 0;
        const firewall = b.firewall_active === 1;

        eligibleEl.innerText = eligible.toLocaleString();
        profiledEl.innerText = profiled.toLocaleString();
        staleEl.innerText    = stale.toLocaleString();
        pendingEl.innerText  = pending.toLocaleString();

        pendingEl.classList.remove('text-orange', 'text-green', 'text-muted');
        pendingEl.classList.add(pending > 0 ? 'text-orange' : 'text-green');
        staleEl.classList.remove('text-orange', 'text-muted');
        staleEl.classList.add(stale > 0 ? 'text-orange' : 'text-muted');

        const scopeStr = firewall
            ? "Freetrade Firewall is ON (only index ∩ tradable assets are in scope)"
            : "Freetrade Firewall is OFF (full market universe in scope)";
        let explanation;
        if (eligible === 0) {
            explanation = `${scopeStr}. No eligible tickers — check Freetrade Sync and Index Scraper.`;
        } else if (pending === 0) {
            explanation = `${scopeStr}. All ${eligible.toLocaleString()} eligible tickers are profiled and fresh (TTL: 90 days). No work for the profiler.`;
        } else {
            explanation = `${scopeStr}. ${pending.toLocaleString()} of ${eligible.toLocaleString()} eligible tickers need a profile (missing or stale).`;
        }
        hintEl.innerText = explanation;
    } catch (e) {
        console.error("Failed to fetch profiler status", e);
        hintEl.innerText = "Failed to fetch profiler queue status.";
    }
}

async function saveFredApiKey() {
    const btn = document.querySelector('button[onclick="saveFredApiKey()"]');
    btn.disabled = true;
    btn.innerText = '⏳ Saving…';
    setStatus('fred-status-msg', 'info', 'Saving…');
    try {
        const res = await fetch('/api/save-fred-api-key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json', 'X-Confirm-Token': CONFIRM_TOKEN },
            body: JSON.stringify({ FRED_API_KEY: document.getElementById('FRED_API_KEY').value.trim() }),
        });
        const data = await res.json().catch(() => ({}));
        setStatus('fred-status-msg', res.ok ? 'success' : 'error',
            res.ok ? 'FRED API key saved.' : (data.detail || 'Failed to save.'));
    } catch (e) {
        setStatus('fred-status-msg', 'error', 'Network error while saving.');
    } finally {
        btn.disabled = false;
        btn.innerText = '💾 Save FRED API Key';
    }
}

document.addEventListener('DOMContentLoaded', () => {
    fetchProfilerQueueStatus();
    setInterval(fetchProfilerQueueStatus, 30000);
});
