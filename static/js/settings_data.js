async function triggerFreetradeSync() {
    const btn = document.getElementById('ftSyncBtn');
    btn.innerText = "Syncing Universe...";
    btn.disabled = true;
    setBoxStatus('ft-sync-status-msg', 'warning', "⏳ Sync initiated in the background. Resolving ISINs...");
    try {
        const job = await postJobTrigger('/api/trigger-freetrade-sync');
        if (job.ok) {
            setBoxStatus('ft-sync-status-msg', 'success', "✅ Sync initiated successfully.");
        } else {
            setStatus('ft-sync-status-msg', 'error', job.message || "Failed to initiate sync. Server returned an error.");
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
        const job = await postJobTrigger('/api/universe/sync-indices');
        alert(job.ok ? "Index Constituent scraping initiated in background. Check notifications for updates." : (job.message || "The server did not start the job."));
    } catch (error) {
        alert("Failed to initiate Scraper. Network or server error.");
    }
}

async function triggerFundamentalsProfiler() {
    try {
        const job = await postJobTrigger('/api/universe/sync-profiler');
        alert(job.ok ? "Fundamentals Profiler initiated in background. Check notifications for updates." : (job.message || "The server did not start the job."));
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
        const job = await postJobTrigger('/api/universe/deep-sync');
        alert(job.ok ? "Universe Deep Sync Pipeline initiated in background. Sequencing fundamentals → technicals → momentum/volatility/relative-strength backfill → ML inference for the full index universe. Estimated runtime: 30–45 minutes. Check notifications for progress." : (job.message || "The server did not start the job."));
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
        const job = await postJobTrigger('/api/trigger-universe-update');
        alert(job.ok ? "Market Universe update initiated in background. Check notifications for progress updates." : (job.message || "The server did not start the job."));
    } catch (error) {
        alert("Failed to initiate Universe Update. Network or server error.");
    }
}

async function triggerUniverseQuantScan() {
    try {
        const job = await postJobTrigger('/api/trigger-universe-quant-scan');
        alert(job.ok ? "Full 4K Quant Scan initiated in background. This will take over an hour. Check notifications for progress updates." : (job.message || "The server did not start the job."));
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

        const excluded = b.excluded_count || 0;
        const scopeStr = firewall
            ? "Freetrade Firewall is ON (only index ∩ tradable assets are in scope)"
            : "Freetrade Firewall is OFF (full market universe in scope)";
        const excludedStr = excluded > 0
            ? ` ${excluded.toLocaleString()} blacklisted, synthetic or ignored tickers are not counted.`
            : "";
        let explanation;
        if (eligible === 0) {
            explanation = `${scopeStr}. No eligible tickers — check Freetrade Sync and Index Scraper.${excludedStr}`;
        } else if (pending === 0) {
            explanation = `${scopeStr}. All ${eligible.toLocaleString()} eligible tickers are profiled and fresh (TTL: 90 days). No work for the profiler.${excludedStr}`;
        } else {
            explanation = `${scopeStr}. ${pending.toLocaleString()} of ${eligible.toLocaleString()} eligible tickers need a profile (missing or stale).${excludedStr}`;
        }
        hintEl.innerText = explanation;

        const runEl = document.getElementById('profiler-run-hint');
        const last = data.last_run || {};
        const lastStr = last.display
            ? `Last run: ${last.display} (${last.status || 'unknown'}).`
            : "Last run: never.";
        const refreshedStr = `${(b.refreshed_24h_count || 0).toLocaleString()} profiles refreshed in the last 24 hours.`;
        let etaStr = "";
        if (pending > 0) {
            const runs = b.runs_to_clear || 0;
            etaStr = b.days_to_clear
                ? ` Queue clears in about ${runs.toLocaleString()} runs of ${(b.batch_size || 0).toLocaleString()} (≈ ${b.days_to_clear.toLocaleString()} days on the current schedule).`
                : ` Queue clears in about ${runs.toLocaleString()} runs of ${(b.batch_size || 0).toLocaleString()}; the schedule is disabled.`;
        }
        runEl.innerText = `${lastStr} ${refreshedStr}${etaStr}`;
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
        const res = await stepUpFetch('/api/save-fred-api-key', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ FRED_API_KEY: secretValue('FRED_API_KEY').trim(), CLEAR_FRED_API_KEY: secretClear('FRED_API_KEY') }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) markSecretSaved('FRED_API_KEY');
        setStatus('fred-status-msg', res.ok ? 'success' : 'error',
            res.ok ? 'FRED API key saved.' : (data.detail || 'Failed to save.'));
    } catch (e) {
        setStatus('fred-status-msg', 'error', 'Network error while saving.');
    } finally {
        btn.disabled = false;
        btn.innerText = '💾 Save FRED API Key';
    }
}

async function loadBlacklist() {
    const countEl = document.getElementById('blacklist-count');
    const listEl = document.getElementById('blacklist-list');
    try {
        const response = await fetch('/api/universe/blacklist');
        const data = await response.json();
        if (!response.ok || data.status !== 'success') {
            setStatus('blacklist-status-msg', 'error', 'Failed to load the blacklist.');
            return;
        }
        countEl.innerText = data.count.toLocaleString();
        listEl.innerText = data.count ? data.tickers.join(', ') : 'No tickers are blacklisted.';
    } catch (error) {
        setStatus('blacklist-status-msg', 'error', 'Network error while loading the blacklist.');
    }
}

async function removeFromBlacklist() {
    const input = document.getElementById('BLACKLIST_REMOVE_TICKERS');
    const tickers = input.value.split(',').map(s => s.trim()).filter(Boolean);
    if (!tickers.length) {
        setStatus('blacklist-status-msg', 'warning', 'Enter at least one ticker.');
        return;
    }
    setStatus('blacklist-status-msg', 'info', 'Removing...');
    try {
        const response = await fetch('/api/universe/blacklist/remove', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ tickers }),
        });
        const data = await response.json().catch(() => ({}));
        if (!response.ok || data.status !== 'success') {
            setStatus('blacklist-status-msg', 'error', data.message || data.detail || 'Failed to update the blacklist.');
            return;
        }
        input.value = '';
        setStatus('blacklist-status-msg', 'success', data.message);
        loadBlacklist();
    } catch (error) {
        setStatus('blacklist-status-msg', 'error', 'Network error while updating the blacklist.');
    }
}

function _gfetcherTime(value, stored) {
    if (!value) return '—';
    const d = new Date(stored ? value.replace(' ', 'T') + 'Z' : value);
    return isNaN(d) ? value : d.toLocaleString();
}

function _gfetcherRoute(r) {
    const state = r.cooling
        ? `paused until ${_gfetcherTime(r.cooldown_until)} (${r.cooldown_reason})`
        : (r.consecutive_refusals ? `available (refused ${r.consecutive_refusals}x in a row)` : 'available');
    return `${state}; last answer ${_gfetcherTime(r.last_success_at)}`;
}

async function loadGfetcherStatus() {
    const box = document.getElementById('gfetcher-health');
    if (!box) return;
    try {
        const response = await fetch('/api/gfetcher/status');
        const data = await response.json();
        if (!response.ok || data.status !== 'success') {
            setStatus('gfetcher-status-msg', 'error', 'Failed to load the Google News Fetcher status.');
            return;
        }
        const policy = data.publishers || {};
        const count = prefix => Object.entries(policy).filter(([k]) => k.startsWith(prefix)).reduce((n, [, v]) => n + v, 0);
        const rows = [
            ['Last run', `${_gfetcherTime(data.last_run, true)}${data.last_status ? ' (' + data.last_status + ')' : ''}`],
            ...data.routes.map(r => [`Route ${r.route}`, _gfetcherRoute(r)]),
            ['Requests (24 h)', `${data.requests_24h} sent, ${data.refusals_24h} refused`],
            ['Articles', `${data.articles.stored} stored, ${data.articles.added} added in 24 h, ${data.articles.with_text} with text`],
            ['Publisher rules', `${count('allow')} allowed, ${count('block_paywall')} paywall (dropped), ${count('block_bots')} bot block (headline only)`],
        ];
        const table = document.createElement('table');
        table.className = 'diag-table';
        const body = table.createTBody();
        rows.forEach(([label, value]) => {
            const tr = body.insertRow();
            const th = document.createElement('th');
            th.textContent = label;
            tr.appendChild(th);
            tr.insertCell().textContent = value;
        });
        box.replaceChildren(table);
        document.getElementById('gfetcher-run-btn').disabled = !data.enabled;
    } catch (error) {
        setStatus('gfetcher-status-msg', 'error', 'Network error while loading the Google News Fetcher status.');
    }
}

async function triggerGfetcherRun() {
    const btn = document.getElementById('gfetcher-run-btn');
    btn.disabled = true;
    setStatus('gfetcher-status-msg', 'info', 'Queuing...');
    try {
        const { ok, message } = await postJobTrigger('/api/gfetcher/run-now');
        setStatus('gfetcher-status-msg', ok ? 'success' : 'warning', message || (ok ? 'Queued.' : 'Not started.'));
    } catch (error) {
        setStatus('gfetcher-status-msg', 'error', 'Network error — check server logs.');
    } finally {
        setTimeout(() => { btn.disabled = false; loadGfetcherStatus(); }, 4000);
    }
}

const GFETCHER_POLICIES = [['allow', 'Allow'], ['block_paywall', 'Paywall (drop)'], ['block_bots', 'Bot block (headline only)']];

function _gfetcherTable(headers, rows) {
    const table = document.createElement('table');
    table.className = 'diag-table';
    const head = table.createTHead().insertRow();
    headers.forEach(h => {
        const th = document.createElement('th');
        th.textContent = h;
        head.appendChild(th);
    });
    const body = table.createTBody();
    rows.forEach(cells => {
        const tr = body.insertRow();
        cells.forEach(cell => {
            const td = tr.insertCell();
            if (cell instanceof Node) td.appendChild(cell); else td.textContent = cell == null ? '—' : String(cell);
        });
    });
    return table;
}

async function loadGfetcherSymbols() {
    const box = document.getElementById('gfetcher-symbols');
    if (!box) return;
    try {
        const response = await fetch('/api/gfetcher/symbols');
        const data = await response.json();
        if (!response.ok || data.status !== 'success') {
            setStatus('gfetcher-probe-msg', 'error', 'Failed to load the symbol mappings.');
            return;
        }
        if (!data.symbols.length) {
            box.textContent = 'No ticker has been looked up yet.';
            return;
        }
        box.replaceChildren(_gfetcherTable(['Ticker', 'Google', 'Status', 'Page title', 'Checked'], data.symbols.map(s => [
            s.ticker,
            s.google_symbol ? `${s.google_symbol}:${s.google_exchange}` : null,
            s.last_probe_status === 'found' ? 'found' : 'no Google page',
            s.verified_title,
            _gfetcherTime(s.verified_at, true),
        ])));
    } catch (error) {
        setStatus('gfetcher-probe-msg', 'error', 'Network error while loading the symbol mappings.');
    }
}

async function triggerGfetcherProbe() {
    const btn = document.getElementById('gfetcher-probe-btn');
    btn.disabled = true;
    setStatus('gfetcher-probe-msg', 'info', 'Queuing...');
    try {
        const { ok, message } = await postJobTrigger('/api/gfetcher/probe');
        setStatus('gfetcher-probe-msg', ok ? 'success' : 'warning', message || (ok ? 'Queued.' : 'Not started.'));
    } catch (error) {
        setStatus('gfetcher-probe-msg', 'error', 'Network error — check server logs.');
    } finally {
        setTimeout(() => { btn.disabled = false; loadGfetcherSymbols(); }, 4000);
    }
}

function _gfetcherPolicySelect(row) {
    const select = document.createElement('select');
    select.className = 'form-select form-select-sm';
    GFETCHER_POLICIES.forEach(([value, label]) => select.add(new Option(label, value, false, value === row.policy)));
    select.addEventListener('change', () => saveGfetcherPublisherRule(row.domain, select.value));
    return select;
}

function _gfetcherLearnedButton(row) {
    if (row.source !== 'manual') return 'learned';
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'btn btn-secondary btn-sm mt-0';
    btn.textContent = 'Use learned';
    btn.addEventListener('click', () => clearGfetcherPublisherRule(row.domain));
    return btn;
}

async function loadGfetcherPublishers() {
    const box = document.getElementById('gfetcher-publishers');
    if (!box) return;
    try {
        const response = await fetch('/api/gfetcher/publishers');
        const data = await response.json();
        if (!response.ok || data.status !== 'success') {
            setStatus('gfetcher-rule-msg', 'error', 'Failed to load the publisher rules.');
            return;
        }
        box.replaceChildren(_gfetcherTable(['Domain', 'Rule', 'Set by', 'Text / short', 'Blocked / paywall / unreachable', 'Updated'], data.publishers.map(r => [
            r.domain,
            _gfetcherPolicySelect(r),
            _gfetcherLearnedButton(r),
            `${r.full_count} / ${r.short_count}`,
            `${r.blocked_count} / ${r.paywall_count} / ${r.unreachable_count}`,
            _gfetcherTime(r.updated_at, true),
        ])));
    } catch (error) {
        setStatus('gfetcher-rule-msg', 'error', 'Network error while loading the publisher rules.');
    }
}

async function saveGfetcherPublisherRule(domain, policy) {
    try {
        const response = await fetch('/api/gfetcher/publishers', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ domain, policy }),
        });
        const data = await response.json().catch(() => ({}));
        setStatus('gfetcher-rule-msg', response.ok ? 'success' : 'error', data.message || 'Could not save the rule.');
        if (response.ok) loadGfetcherPublishers();
        return response.ok;
    } catch (error) {
        setStatus('gfetcher-rule-msg', 'error', 'Network error while saving the rule.');
        return false;
    }
}

async function addGfetcherPublisherRule() {
    const input = document.getElementById('gfetcher-rule-domain');
    const domain = input.value.trim();
    if (!domain) {
        setStatus('gfetcher-rule-msg', 'warning', 'Enter a domain such as example.com.');
        return;
    }
    if (await saveGfetcherPublisherRule(domain, document.getElementById('gfetcher-rule-policy').value)) input.value = '';
}

async function clearGfetcherPublisherRule(domain) {
    try {
        const response = await fetch('/api/gfetcher/publishers/' + encodeURIComponent(domain), { method: 'DELETE' });
        const data = await response.json().catch(() => ({}));
        setStatus('gfetcher-rule-msg', response.ok ? 'success' : 'error', data.message || 'Could not reset the rule.');
        if (response.ok) loadGfetcherPublishers();
    } catch (error) {
        setStatus('gfetcher-rule-msg', 'error', 'Network error while resetting the rule.');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    loadBlacklist();
    loadGfetcherStatus();
    const symbolsBox = document.getElementById('gfetcher-symbols-box');
    if (symbolsBox) symbolsBox.addEventListener('toggle', () => { if (symbolsBox.open) loadGfetcherSymbols(); });
    const publishersBox = document.getElementById('gfetcher-publishers-box');
    if (publishersBox) publishersBox.addEventListener('toggle', () => { if (publishersBox.open) loadGfetcherPublishers(); });
    const ruleDomain = document.getElementById('gfetcher-rule-domain');
    if (ruleDomain) ruleDomain.addEventListener('keydown', event => {
        if (event.key === 'Enter') {
            event.preventDefault();
            addGfetcherPublisherRule();
        }
    });
    fetchProfilerQueueStatus();
    setInterval(fetchProfilerQueueStatus, 30000);
});
