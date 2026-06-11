        // Populate Dip Radar reset time in user's local timezone
        (function() {
            function formatDipResetLocalTime() {
                try {
                    const now = new Date();
                    const nyDate = new Intl.DateTimeFormat('sv-SE', { timeZone: 'America/New_York' }).format(now);
                    const [y, m, d] = nyDate.split('-').map(Number);
                    for (const utcHour of [20, 21]) {
                        const candidate = new Date(Date.UTC(y, m - 1, d, utcHour, 5));
                        const nyHour = parseInt(
                            new Intl.DateTimeFormat('en', {
                                timeZone: 'America/New_York', hour: 'numeric', hour12: false
                            }).format(candidate)
                        );
                        if (nyHour === 16) {
                            return candidate.toLocaleTimeString([], {
                                hour: '2-digit', minute: '2-digit', timeZoneName: 'short'
                            });
                        }
                    }
                } catch(e) {}
                return '16:05 ET';
            }
            document.addEventListener('DOMContentLoaded', function() {
                document.querySelectorAll('.dip-reset-time').forEach(function(el) {
                    el.textContent = formatDipResetLocalTime();
                });
            });
        })();

        let CONFIRM_TOKEN = window.CONFIRM_TOKEN;
        const accountsDataNode = document.getElementById('discoveredAccountsData').textContent;
        let currentDiscoveredAccounts = JSON.parse(accountsDataNode);
        const macroInitState = JSON.parse(document.getElementById('macroInitState').textContent);

        function setStatus(elId, type, msg) {
            const el = document.getElementById(elId);
            el.innerText = (type === 'success' ? '✅ ' : type === 'error' ? '❌ ' : type === 'warning' ? '⚠️ ' : '⏳ ') + msg;
            el.className = 'status-msg-sm ' + (type === 'success' ? 'msg-success' : type === 'error' ? 'msg-error' : type === 'warning' ? 'msg-warning' : 'msg-info');
        }

        function setBoxStatus(elId, type, htmlMsg) {
            const el = document.getElementById(elId);
            el.style.display = 'block';
            el.innerHTML = htmlMsg;
            el.className = 'status-msg-sm ' + (type === 'success' ? 'box-success' : type === 'error' ? 'box-error' : type === 'warning' ? 'box-warning' : 'box-info');
        }

        function copyRssFeedUrl() {
            const url = document.getElementById('RSS_FEED_URL').value;
            navigator.clipboard.writeText(url).then(() => {
                const btn = document.querySelector('button[onclick="copyRssFeedUrl()"]');
                const orig = btn.innerText;
                btn.innerText = 'Copied!';
                setTimeout(() => { btn.innerText = orig; }, 1500);
            });
        }

        function requestBrowserNotificationPermission() {
            if (!("Notification" in window)) {
                setStatus("browser-notif-msg", "error", "Your browser does not support desktop notifications.");
                return;
            }
            Notification.requestPermission().then(permission => {
                if (permission === "granted") {
                    setStatus("browser-notif-msg", "success", "Permissions granted! You will receive system alerts while the dashboard is running.");
                    new Notification("Quantamental Alerts Enabled", {
                        body: "You will securely receive intraday and system alerts natively here.",
                        icon: "/assets/logo_small.png"
                    });
                } else {
                    setStatus("browser-notif-msg", "error", "Permissions denied. Check your browser settings.");
                }
            });
        }

        async function triggerGhostfolioSync() {
            const btn = document.getElementById('syncBtn');
            btn.innerText = "Syncing JSON..."; btn.disabled = true;
            try { await fetch('/api/sync-ghostfolio', { method: 'POST' }); alert("Sync initiated in the background! Wait a few moments, then hit 'Force Update Analysis'."); } catch (e) { }
            setTimeout(() => { btn.innerText = "⬇️ Sync Ghostfolio Data"; btn.disabled = false; }, 5000);
        }

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
                    setBoxStatus && setBoxStatus('maintenance-dry-run-content', 'success', '✅ ' + data.message);
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
            const current  = document.getElementById('ua_current_password').value;
            const newPw    = document.getElementById('ua_new_password').value;
            const confirm  = document.getElementById('ua_confirm_password').value;
            const msgEl    = document.getElementById('ua-pw-msg');

            setStatus('ua-pw-msg', 'info', 'Updating…');

            const res = await fetch('/api/change-password', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
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

        async function testInsiderAlert() {
            const btn = document.querySelector('button[onclick="testInsiderAlert()"]');
            btn.disabled = true;
            btn.innerText = "Scanning SEC Filings...";
            document.getElementById('test-insider-msg').innerText = "";

            try {
                const response = await fetch('/api/test-insider-alert', { method: 'POST' });
                const result = await response.json();
                
                if (response.ok) {
                    setStatus('test-insider-msg', 'success', result.message);
                } else {
                    setStatus('test-insider-msg', 'error', result.message);
                }
            } catch (error) {
                setStatus('test-insider-msg', 'error', "Internal Server/Network Error.");
            }
            
            btn.disabled = false;
            btn.innerText = "🧪 Run Test Insider Check";
        }

        async function testEarningsAlert() {
            const btn = document.querySelector('button[onclick="testEarningsAlert()"]');
            btn.disabled = true;
            btn.innerText = "Checking Database...";
            document.getElementById('test-earnings-msg').innerText = "";

            try {
                const response = await fetch('/api/test-earnings-alert', { method: 'POST' });
                const result = await response.json();
                
                if (response.ok) {
                    setStatus('test-earnings-msg', 'success', result.message);
                } else {
                    setStatus('test-earnings-msg', 'error', result.message);
                }
            } catch (error) {
                setStatus('test-earnings-msg', 'error', "Internal Server/Network Error.");
            }
            
            btn.disabled = false;
            btn.innerText = "🧪 Run Test Earnings Alert";
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
            } catch (_) { /* fail silently */ }
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

                    // Wait briefly for the process to actually die before polling
                    await new Promise(resolve => setTimeout(resolve, 3000));

                    const maxWait = 60000;
                    const pollInterval = 2000;
                    const started = Date.now();

                    const poll = setInterval(async () => {
                        const elapsed = Math.round((Date.now() - started) / 1000);
                        setStatus('git-status-msg', 'success', `Waiting for service to come back up... (${elapsed}s)`);

                        try {
                            const check = await fetch('/', { method: 'HEAD', cache: 'no-store' });
                            // 5xx = nginx is up but app is still down; anything else = app is back
                            if (check.status < 500) {
                                clearInterval(poll);
                                window.location.reload();
                            }
                        } catch (_) {
                            // Network error — still down, keep polling
                        }

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

        async function testSentimentAlert() {
            const btn = document.querySelector('button[onclick="testSentimentAlert()"]');
            btn.disabled = true;
            btn.innerText = "Running Pipeline... (Takes ~10s)";
            setStatus('test-status-msg', 'info', "Fetching data, generating image, and pushing to Nextcloud...");

            try {
                const response = await fetch('/api/test-sentiment-alert', { method: 'POST' });
                const result = await response.json();
                
                if (response.ok) {
                    setStatus('test-status-msg', 'success', result.message);
                } else {
                    setStatus('test-status-msg', 'error', result.message);
                }
            } catch (error) {
                setStatus('test-status-msg', 'error', "Internal Server/Network Error.");
            }
            
            btn.disabled = false;
            btn.innerText = "🧪 Run Test Alert Now";
        }

        async function gitPull() {
            const btn = document.querySelector('.btn-danger');
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

        async function triggerQuantScan() {
            try {
                await fetch('/api/trigger-quant-scan', { method: 'POST' });
                alert("Portfolio Quant Scan initiated in background. Check notifications for progress updates.");
            } catch (error) {
                alert("Failed to initiate Quant Scan. Network or server error.");
            }
        }

        async function triggerMorningBriefing() {
            const statusEl = document.getElementById('morning-briefing-status');
            statusEl.textContent = "Generating… this may take 30–60 seconds.";
            try {
                const resp = await fetch('/api/trigger-morning-briefing', { method: 'POST' });
                const data = await resp.json();
                statusEl.textContent = data.message || "Morning Briefing generation started.";
            } catch (error) {
                statusEl.textContent = "Failed to start generation. Network or server error.";
            }
        }

        async function triggerSMGBPredictor() {
            const statusEl = document.getElementById('smgb-predictor-status');
            statusEl.textContent = "Running prediction… this may take 10–20 seconds.";
            try {
                const resp = await fetch('/api/smgb-prediction');
                const data = await resp.json();
                if (data.status === 'success') {
                    const sign = data.predicted_change_pct >= 0 ? '+' : '';
                    statusEl.textContent = `Done — £${data.predicted_price} (${sign}${data.predicted_change_pct?.toFixed(2)}%) | signal: ${data.signal_source}`;
                } else {
                    statusEl.textContent = data.error || "Prediction failed.";
                }
            } catch (error) {
                statusEl.textContent = "Failed to run prediction. Network or server error.";
            }
        }

        // ── ETF Predictor settings JS ─────────────────────────────────────────
        let _etfConfigCache = {};

        const _etfUserTz = window.ETF_USER_TZ;
        function _etfOffsetMin() {
            const ref = new Date(); ref.setUTCHours(12, 0, 0, 0);
            const parts = new Intl.DateTimeFormat('en', {timeZone: _etfUserTz, hour:'numeric', minute:'numeric', hour12:false}).formatToParts(ref);
            const h = +parts.find(p => p.type === 'hour').value;
            const m = +parts.find(p => p.type === 'minute').value;
            let off = (h - 12) * 60 + m;
            if (off < -660) off += 1440;
            return off;
        }
        function _etfTzAbbr() {
            return new Intl.DateTimeFormat('en', {timeZone: _etfUserTz, timeZoneName:'short'}).formatToParts(new Date()).find(p => p.type === 'timeZoneName')?.value || 'local';
        }
        function _utcHhmToLocal(hhmm) {
            const [h, m] = hhmm.split(':').map(Number);
            const t = ((h * 60 + m + _etfOffsetMin()) % 1440 + 1440) % 1440;
            return String(Math.floor(t / 60)).padStart(2,'0') + ':' + String(t % 60).padStart(2,'0');
        }
        function _localHhmToUtc(hhmm) {
            const [h, m] = hhmm.split(':').map(Number);
            const t = ((h * 60 + m - _etfOffsetMin()) % 1440 + 1440) % 1440;
            return String(Math.floor(t / 60)).padStart(2,'0') + ':' + String(t % 60).padStart(2,'0');
        }

        function _etfConstituentRowHtml(ticker = '', weight = '') {
            const t = ticker ? ` value="${ticker}"` : '';
            const w = weight !== '' ? ` value="${weight}"` : '';
            return `<div class="etf-constituent-row flex-gap-15 mb-10">
                <input type="text" class="etf-c-ticker" placeholder="Ticker"${t} style="flex:1;text-transform:uppercase;">
                <input type="number" class="etf-c-weight" placeholder="Weight %" step="0.01" min="0" style="width:100px;"${w}>
                <button type="button" class="btn-danger" style="padding:6px 10px;" onclick="removeConstituentRow(this)">×</button>
            </div>`;
        }

        function _etfEditFormHtml(cfg) {
            const cRows = cfg.constituents.map(c =>
                _etfConstituentRowHtml(c.ticker, (c.weight * 100).toFixed(4))
            ).join('');
            return `
            <div id="etf-edit-form-${cfg.id}" style="display:none;margin-top:12px;background:#111;padding:14px;border-radius:6px;">
                <h6 style="color:#b366ff;margin:0 0 10px;">Edit: ${cfg.etf_ticker}</h6>
                <div class="flex-gap-15">
                    <div class="form-group flex-1 mb-0">
                        <label>Name</label>
                        <input type="text" id="etf-edit-name-${cfg.id}" value="${cfg.name.replace(/"/g,'&quot;')}">
                    </div>
                    <div class="form-group flex-1 mb-0">
                        <label>ETF Ticker</label>
                        <input type="text" id="etf-edit-ticker-${cfg.id}" value="${cfg.etf_ticker}" style="text-transform:uppercase;">
                    </div>
                </div>
                <div style="margin-top:12px;background:#1a1a1a;padding:10px;border-radius:5px;border:1px solid #2a2a2a;">
                    <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:6px;">
                        <span style="color:#aaa;font-size:12px;font-weight:600;">IMPORT FROM JSON</span>
                        <button type="button" class="btn-test mt-0" style="font-size:11px;padding:3px 10px;background:#333;" onclick="toggleEtfJsonImport('edit-${cfg.id}')">Toggle</button>
                    </div>
                    <div id="etf-json-import-edit-${cfg.id}" style="display:none;">
                        <p style="color:#888;font-size:12px;margin:0 0 6px;">Paste array or map, then click Import to overwrite constituent rows.</p>
                        <textarea id="etf-json-edit-${cfg.id}" rows="3" placeholder='[{"ticker":"AAPL","weight":7.5}]' style="width:100%;font-size:12px;font-family:monospace;background:#111;color:#ccc;border:1px solid #333;border-radius:4px;padding:8px;box-sizing:border-box;resize:vertical;"></textarea>
                        <button type="button" class="btn-test mt-8" style="font-size:12px;" onclick="importEtfJson('etf-edit-constituents-${cfg.id}','etf-json-edit-${cfg.id}','etf-edit-status-${cfg.id}')">Import JSON</button>
                    </div>
                </div>
                <div style="margin-top:10px;">
                    <label style="color:#aaa;font-size:13px;display:block;margin-bottom:6px;">Constituents</label>
                    <div id="etf-edit-constituents-${cfg.id}">${cRows}</div>
                    <div class="flex-gap-15 mt-10">
                        <button type="button" class="btn-test mt-0" onclick="addConstituentRow('etf-edit-constituents-${cfg.id}')">+ Add Row</button>
                        <button type="button" class="btn-test mt-0" style="background:#333;" onclick="normaliseWeights('etf-edit-constituents-${cfg.id}')">⚖ Normalise</button>
                        <button type="button" class="btn-test mt-0" style="background:#1a3a4a;" onclick="checkEtfConfig('etf-edit-ticker-${cfg.id}','etf-edit-constituents-${cfg.id}','etf-edit-status-${cfg.id}')">&#10003; Check Config</button>
                    </div>
                </div>
                <div style="margin-top:10px;display:grid;grid-template-columns:1fr 1fr 1fr;gap:10px;">
                    <div class="form-group mb-0">
                        <label>Pre-run (${_etfTzAbbr()})</label>
                        <input type="time" id="etf-edit-pre-${cfg.id}" value="${_utcHhmToLocal(cfg.pre_run_time)}">
                    </div>
                    <div class="form-group mb-0">
                        <label>Post-run (${_etfTzAbbr()})</label>
                        <input type="time" id="etf-edit-post-${cfg.id}" value="${_utcHhmToLocal(cfg.post_run_time)}">
                    </div>
                    <div class="form-group mb-0" style="justify-content:flex-end;padding-bottom:2px;">
                        <label>&nbsp;</label>
                        <div class="checkbox-group mb-0">
                            <input type="checkbox" id="etf-edit-sched-${cfg.id}" ${cfg.auto_schedule ? 'checked' : ''}>
                            <label for="etf-edit-sched-${cfg.id}" style="font-size:13px;">Auto-schedule</label>
                        </div>
                        <div class="checkbox-group mb-0">
                            <input type="checkbox" id="etf-edit-en-${cfg.id}" ${cfg.enabled ? 'checked' : ''}>
                            <label for="etf-edit-en-${cfg.id}" style="font-size:13px;">Enabled</label>
                        </div>
                    </div>
                </div>
                <div class="flex-gap-15 mt-12">
                    <button type="button" class="btn-save flex-1" style="max-width:140px;" onclick="saveEtfEdit(${cfg.id})">Save Changes</button>
                    <button type="button" class="btn-test mt-0" style="background:#333;" onclick="toggleEtfEditForm(${cfg.id})">Cancel</button>
                </div>
                <div id="etf-edit-status-${cfg.id}" class="status-msg-sm mt-8"></div>
            </div>`;
        }

        async function loadEtfPredictors() {
            const list = document.getElementById('etf-predictor-list');
            if (!list) return;
            try {
                const r = await fetch('/api/etf-predictors');
                const data = await r.json();
                if (!data.configs || data.configs.length === 0) {
                    list.innerHTML = '<p style="color:#888;font-size:13px;">No predictors configured yet.</p>';
                    _etfConfigCache = {};
                    return;
                }
                _etfConfigCache = {};
                data.configs.forEach(c => { _etfConfigCache[c.id] = c; });
                list.innerHTML = data.configs.map(cfg => `
                    <div id="etf-cfg-${cfg.id}" style="background:#1e1e1e;padding:12px;border-radius:5px;margin-bottom:10px;">
                        <div style="display:flex;justify-content:space-between;align-items:flex-start;flex-wrap:wrap;gap:8px;">
                            <div>
                                <strong style="color:#b366ff;">${cfg.etf_ticker}</strong>
                                <span style="color:#ccc;margin-left:8px;">${cfg.name}</span>
                                <span style="color:#666;font-size:12px;margin-left:8px;">${cfg.constituents.length} constituents</span>
                            </div>
                            <div style="display:flex;gap:8px;align-items:center;flex-wrap:wrap;">
                                <label style="font-size:12px;color:#aaa;cursor:pointer;">
                                    <input type="checkbox" ${cfg.enabled ? 'checked' : ''} onchange="toggleEtfEnabled(${cfg.id}, this.checked)" style="cursor:pointer;">
                                    Enabled
                                </label>
                                <label style="font-size:12px;color:#aaa;cursor:pointer;">
                                    <input type="checkbox" ${cfg.auto_schedule ? 'checked' : ''} onchange="toggleEtfSchedule(${cfg.id}, this.checked)" style="cursor:pointer;">
                                    Auto-schedule
                                </label>
                                <button type="button" class="btn-test mt-0" style="font-size:11px;padding:4px 10px;background:#2a3a5a;" onclick="toggleEtfEditForm(${cfg.id})">&#9998; Edit</button>
                                <button type="button" class="btn-test mt-0" style="font-size:11px;padding:4px 10px;" onclick="runEtfNow(${cfg.id})">&#9654; Run</button>
                                <a href="/etf-predictor/${cfg.id}" class="btn-test mt-0" style="font-size:11px;padding:4px 10px;color:#fff;text-decoration:none;">View</a>
                                <button type="button" class="btn-danger" style="font-size:11px;padding:4px 10px;" onclick="deleteEtfPredictor(${cfg.id}, '${cfg.etf_ticker}')">Delete</button>
                            </div>
                        </div>
                        <div style="margin-top:8px;font-size:12px;color:#888;">
                            Pre: ${_utcHhmToLocal(cfg.pre_run_time)} ${_etfTzAbbr()} &nbsp;|&nbsp; Post: ${_utcHhmToLocal(cfg.post_run_time)} ${_etfTzAbbr()}
                            &nbsp;|&nbsp; ${cfg.constituents.map(h => h.ticker).join(', ')}
                        </div>
                        <div id="etf-status-${cfg.id}" class="status-msg-sm" style="margin-top:6px;"></div>
                        ${_etfEditFormHtml(cfg)}
                    </div>`).join('');
            } catch (e) {
                list.innerHTML = `<span class="msg-error">Failed to load: ${e.message}</span>`;
            }
        }

        function toggleAddEtfForm() {
            const f = document.getElementById('add-etf-form');
            f.style.display = f.style.display === 'none' ? 'block' : 'none';
        }

        function toggleEtfEditForm(id) {
            const f = document.getElementById(`etf-edit-form-${id}`);
            if (!f) return;
            f.style.display = f.style.display === 'none' ? 'block' : 'none';
        }

        function toggleEtfJsonImport(suffix) {
            const el = document.getElementById(`etf-json-import-${suffix}`);
            if (el) el.style.display = el.style.display === 'none' ? 'block' : 'none';
        }

        function importEtfJson(containerId, textareaId, statusId) {
            const raw = (document.getElementById(textareaId)?.value || '').trim();
            const statusEl = document.getElementById(statusId);
            if (!raw) { if (statusEl) statusEl.innerHTML = '<span class="msg-error">Paste JSON first.</span>'; return; }
            let parsed;
            try { parsed = JSON.parse(raw); } catch (e) {
                if (statusEl) statusEl.innerHTML = `<span class="msg-error">Invalid JSON: ${e.message}</span>`;
                return;
            }
            let rows = [];
            if (Array.isArray(parsed)) {
                rows = parsed.map(item => {
                    if (item.ticker !== undefined) return { ticker: String(item.ticker).toUpperCase(), weight: parseFloat(item.weight) || 0 };
                    const keys = Object.keys(item);
                    if (keys.length === 2) return { ticker: String(item[keys[0]] || item.t || keys[0]).toUpperCase(), weight: parseFloat(item[keys[1]] || item.w) || 0 };
                    return null;
                }).filter(Boolean);
            } else if (typeof parsed === 'object') {
                rows = Object.entries(parsed).map(([k, v]) => ({ ticker: k.toUpperCase(), weight: parseFloat(v) || 0 }));
            }
            if (!rows.length) { if (statusEl) statusEl.innerHTML = '<span class="msg-error">Could not parse any rows from that JSON.</span>'; return; }
            const container = document.getElementById(containerId);
            if (!container) return;
            container.innerHTML = rows.map(r => _etfConstituentRowHtml(r.ticker, r.weight)).join('');
            if (statusEl) statusEl.innerHTML = `<span class="msg-success">Imported ${rows.length} constituent(s).</span>`;
        }

        function addConstituentRow(containerId) {
            const c = document.getElementById(containerId);
            const div = document.createElement('div');
            div.innerHTML = _etfConstituentRowHtml();
            c.appendChild(div.firstElementChild);
        }

        function removeConstituentRow(btn) {
            const container = btn.closest('[id^="etf-"]');
            if (!container) return;
            const rows = container.querySelectorAll('.etf-constituent-row');
            if (rows.length > 1) btn.closest('.etf-constituent-row').remove();
        }

        function normaliseWeights(containerId) {
            const c = document.getElementById(containerId);
            const inputs = c.querySelectorAll('.etf-c-weight');
            const total = Array.from(inputs).reduce((s, i) => s + (parseFloat(i.value) || 0), 0);
            if (total <= 0) return;
            inputs.forEach(i => { const v = parseFloat(i.value) || 0; i.value = (v / total * 100).toFixed(2); });
        }

        function getConstituentsFromContainer(containerId) {
            const c = document.getElementById(containerId);
            return Array.from(c.querySelectorAll('.etf-constituent-row')).map(row => ({
                ticker: (row.querySelector('.etf-c-ticker').value || '').trim().toUpperCase(),
                weight: parseFloat(row.querySelector('.etf-c-weight').value) || 0
            })).filter(h => h.ticker && h.weight > 0);
        }

        async function checkEtfConfig(tickerInputId, containerId, statusId) {
            const statusEl = document.getElementById(statusId);
            const etfTicker = (document.getElementById(tickerInputId)?.value || '').trim().toUpperCase();
            const constituents = getConstituentsFromContainer(containerId);
            if (!etfTicker) { if (statusEl) statusEl.innerHTML = '<span class="msg-error">Enter an ETF ticker first.</span>'; return; }
            if (!constituents.length) { if (statusEl) statusEl.innerHTML = '<span class="msg-error">Add at least one constituent.</span>'; return; }
            if (statusEl) statusEl.innerHTML = '<span class="msg-info">Checking tickers with Yahoo Finance… this may take a moment.</span>';
            try {
                const r = await fetch('/api/etf-predictors/validate', {
                    method: 'POST', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ etf_ticker: etfTicker, constituents })
                });
                const data = await r.json();
                if (data.status !== 'success') { if (statusEl) statusEl.innerHTML = `<span class="msg-error">${data.message || 'Validation failed.'}</span>`; return; }
                const etfOk = data.etf.valid;
                const allOk = data.constituents.every(c => c.valid);
                const badTickers = data.constituents.filter(c => !c.valid).map(c => c.ticker);
                const warnWeight = !data.weight_ok;
                const lines = [];
                lines.push(etfOk
                    ? `<span class="msg-success">ETF ${data.etf.ticker}: &#10003; ${data.etf.name || 'found'}</span>`
                    : `<span class="msg-error">ETF ${data.etf.ticker}: &#10007; not found on Yahoo Finance</span>`);
                if (badTickers.length)
                    lines.push(`<span class="msg-error">Unknown tickers: ${badTickers.join(', ')}</span>`);
                else
                    lines.push(`<span class="msg-success">All ${data.constituents.length} constituent(s) found &#10003;</span>`);
                lines.push(warnWeight
                    ? `<span class="msg-warning">Total weight = ${data.total_weight.toFixed(2)} — consider normalising to 100%</span>`
                    : `<span class="msg-success">Total weight = ${data.total_weight.toFixed(2)} &#10003;</span>`);
                if (statusEl) statusEl.innerHTML = lines.join('<br>');
            } catch (e) { if (statusEl) statusEl.innerHTML = `<span class="msg-error">${e.message}</span>`; }
        }

        async function saveNewEtfPredictor() {
            const status = document.getElementById('etf-add-status');
            const name = document.getElementById('etf-new-name').value.trim();
            const ticker = document.getElementById('etf-new-ticker').value.trim().toUpperCase();
            const constituents = getConstituentsFromContainer('etf-new-constituents');
            if (!name || !ticker) { status.innerHTML = '<span class="msg-error">Name and ETF ticker are required.</span>'; return; }
            if (constituents.length === 0) { status.innerHTML = '<span class="msg-error">Add at least one constituent with a positive weight.</span>'; return; }
            status.innerHTML = '<span class="msg-info">Saving...</span>';
            try {
                const r = await fetch('/api/etf-predictors', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        name, etf_ticker: ticker, constituents,
                        enabled: document.getElementById('etf-new-enabled').checked,
                        auto_schedule: document.getElementById('etf-new-auto-schedule').checked,
                        pre_run_time: _localHhmToUtc(document.getElementById('etf-new-pre-time').value),
                        post_run_time: _localHhmToUtc(document.getElementById('etf-new-post-time').value),
                    })
                });
                const data = await r.json();
                if (data.status === 'success') {
                    status.innerHTML = '<span class="msg-success">Predictor created.</span>';
                    document.getElementById('add-etf-form').style.display = 'none';
                    loadEtfPredictors();
                } else {
                    status.innerHTML = `<span class="msg-error">${data.message || 'Failed'}</span>`;
                }
            } catch (e) { status.innerHTML = `<span class="msg-error">${e.message}</span>`; }
        }

        async function saveEtfEdit(id) {
            const statusEl = document.getElementById(`etf-edit-status-${id}`);
            const name = (document.getElementById(`etf-edit-name-${id}`)?.value || '').trim();
            const ticker = (document.getElementById(`etf-edit-ticker-${id}`)?.value || '').trim().toUpperCase();
            const constituents = getConstituentsFromContainer(`etf-edit-constituents-${id}`);
            if (!name || !ticker) { if (statusEl) statusEl.innerHTML = '<span class="msg-error">Name and ETF ticker required.</span>'; return; }
            if (!constituents.length) { if (statusEl) statusEl.innerHTML = '<span class="msg-error">Add at least one constituent.</span>'; return; }
            if (statusEl) statusEl.innerHTML = '<span class="msg-info">Saving...</span>';
            try {
                const r = await fetch(`/api/etf-predictors/${id}`, {
                    method: 'PUT', headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({
                        name, etf_ticker: ticker, constituents,
                        enabled: document.getElementById(`etf-edit-en-${id}`)?.checked ?? true,
                        auto_schedule: document.getElementById(`etf-edit-sched-${id}`)?.checked ?? false,
                        pre_run_time: _localHhmToUtc(document.getElementById(`etf-edit-pre-${id}`)?.value || '13:30'),
                        post_run_time: _localHhmToUtc(document.getElementById(`etf-edit-post-${id}`)?.value || '22:00'),
                    })
                });
                const data = await r.json();
                if (data.status === 'success') {
                    if (statusEl) statusEl.innerHTML = '<span class="msg-success">Saved.</span>';
                    loadEtfPredictors();
                } else {
                    if (statusEl) statusEl.innerHTML = `<span class="msg-error">${data.message || 'Failed'}</span>`;
                }
            } catch (e) { if (statusEl) statusEl.innerHTML = `<span class="msg-error">${e.message}</span>`; }
        }

        async function _putEtfConfig(id, overrides) {
            const cfg = _etfConfigCache[id];
            if (!cfg) return { status: 'error', message: 'Config not in cache — reload.' };
            const body = { name: cfg.name, etf_ticker: cfg.etf_ticker, constituents: cfg.constituents,
                enabled: cfg.enabled, auto_schedule: cfg.auto_schedule,
                pre_run_time: cfg.pre_run_time, post_run_time: cfg.post_run_time, ...overrides };
            const r = await fetch(`/api/etf-predictors/${id}`, {
                method: 'PUT', headers: {'Content-Type': 'application/json'},
                body: JSON.stringify(body)
            });
            return r.json().catch(() => ({ status: 'error' }));
        }

        async function toggleEtfEnabled(id, enabled) {
            const el = document.getElementById(`etf-status-${id}`);
            if (el) el.innerHTML = '<span class="msg-info">Saving...</span>';
            const data = await _putEtfConfig(id, { enabled });
            if (_etfConfigCache[id]) _etfConfigCache[id].enabled = enabled;
            if (el) el.innerHTML = data.status === 'success'
                ? '<span class="msg-success">Updated.</span>'
                : `<span class="msg-error">${data.message || 'Failed'}</span>`;
        }

        async function toggleEtfSchedule(id, auto_schedule) {
            const el = document.getElementById(`etf-status-${id}`);
            if (el) el.innerHTML = '<span class="msg-info">Saving...</span>';
            const data = await _putEtfConfig(id, { auto_schedule });
            if (_etfConfigCache[id]) _etfConfigCache[id].auto_schedule = auto_schedule;
            if (el) el.innerHTML = data.status === 'success'
                ? `<span class="msg-success">Schedule ${auto_schedule ? 'enabled' : 'disabled'}.</span>`
                : `<span class="msg-error">${data.message || 'Failed'}</span>`;
        }

        async function runEtfNow(id) {
            const el = document.getElementById(`etf-status-${id}`);
            if (el) el.innerHTML = '<span class="msg-info">Initiating...</span>';
            try {
                const r = await fetch(`/api/etf-predictors/${id}/run`, { method: 'POST' });
                const data = await r.json();
                if (el) el.innerHTML = data.status === 'success'
                    ? '<span class="msg-success">Prediction initiated — check Notifications for result.</span>'
                    : `<span class="msg-error">${data.message || 'Failed'}</span>`;
            } catch (e) { if (el) el.innerHTML = `<span class="msg-error">${e.message}</span>`; }
        }

        async function deleteEtfPredictor(id, ticker) {
            if (!confirm(`Delete predictor for ${ticker}? Prediction history will be preserved.`)) return;
            const el = document.getElementById(`etf-status-${id}`);
            if (el) el.innerHTML = '<span class="msg-info">Deleting...</span>';
            try {
                const r = await fetch(`/api/etf-predictors/${id}`, { method: 'DELETE' });
                const data = await r.json();
                if (data.status === 'success') { loadEtfPredictors(); }
                else { if (el) el.innerHTML = `<span class="msg-error">${data.message || 'Failed'}</span>`; }
            } catch (e) { if (el) el.innerHTML = `<span class="msg-error">${e.message}</span>`; }
        }

        // Load ETF predictors when the Tools <details> section opens
        document.addEventListener('DOMContentLoaded', () => {
            const toolsDetails = document.querySelector('#etf-predictors-section')?.closest('details');
            if (toolsDetails) {
                toolsDetails.addEventListener('toggle', () => {
                    if (toolsDetails.open) loadEtfPredictors();
                });
                if (toolsDetails.open) loadEtfPredictors();
            }
            document.querySelectorAll('.etf-tz-label').forEach(el => el.textContent = _etfTzAbbr());
            const _preEl = document.getElementById('etf-new-pre-time');
            const _postEl = document.getElementById('etf-new-post-time');
            if (_preEl) _preEl.value = _utcHhmToLocal('13:30');
            if (_postEl) _postEl.value = _utcHhmToLocal('22:00');
        });

        async function triggerLunchBriefing() {
            const statusEl = document.getElementById('lunch-briefing-status');
            statusEl.textContent = "Generating… this may take 30–60 seconds.";
            try {
                const resp = await fetch('/api/trigger-lunch-briefing', { method: 'POST' });
                const data = await resp.json();
                statusEl.textContent = data.message || "Lunchtime Briefing generation started.";
            } catch (error) {
                statusEl.textContent = "Failed to start generation. Network or server error.";
            }
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

        async function triggerXrayRiskCache() {
            const btn = document.querySelector('button[onclick="triggerXrayRiskCache()"]');
            if (btn) { btn.disabled = true; btn.textContent = '⏳ Queued…'; }
            try {
                await fetch('/api/xray/trigger', { method: 'POST' });
                alert("X-ray Risk Cache job queued in background.\nThis fetches 1-year returns from Yahoo Finance and may take 30–60 seconds.\nCheck System Notifications for completion.");
            } catch (error) {
                alert("Failed to queue X-ray Risk Cache job. Network or server error.");
            } finally {
                if (btn) { btn.disabled = false; btn.innerHTML = '&#9654;&#65039; Run Now'; }
            }
        }

        async function triggerEarningsScan() {
            try {
                await fetch('/api/trigger-earnings-scan', { method: 'POST' });
                alert("Earnings Volatility Scan initiated in background. Check notifications for progress updates.");
            } catch (error) {
                alert("Failed to initiate Earnings Scan. Network or server error.");
            }
        }

        async function triggerSentimentScan() {
            try {
                await fetch('/api/trigger-sentiment-scan', { method: 'POST' });
                alert("Sentiment Scan initiated in background. Check notifications for progress updates.");
            } catch (error) {
                alert("Failed to initiate Sentiment Scan. Network or server error.");
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

        async function triggerMLBackfill() {
            const btn = document.querySelector('button[onclick="triggerMLBackfill()"]');
            btn.disabled = true;
            btn.innerText = "▶️ Initiating Backfill...";
            
            try {
                await fetch('/api/ml/trigger-backfill', { method: 'POST' });
                alert("ML Historical Backfill initiated in the background. Check system notifications for progress.");
            } catch (error) {
                alert("Failed to initiate ML Backfill. Network or server error.");
            }
            
            setTimeout(() => {
                btn.disabled = false;
                btn.innerText = "▶️ Run Backfill Now";
            }, 3000);
        }

        async function triggerMLTraining() {
            const btn = document.querySelector('button[onclick="triggerMLTraining()"]');
            btn.disabled = true;
            btn.innerText = "▶️ Initiating Training...";
            
            try {
                await fetch('/api/ml/trigger-training', { method: 'POST' });
                alert("Global ML Walk-Forward Training initiated in the background. Check system notifications for progress.");
            } catch (error) {
                alert("Failed to initiate ML Training. Network or server error.");
            }
            
            setTimeout(() => {
                btn.disabled = false;
                btn.innerText = "▶️ Run Training Now";
            }, 3000);
        }

        async function triggerMLInference() {
            const btn = document.querySelector('button[onclick="triggerMLInference()"]');
            btn.disabled = true;
            btn.innerText = "▶️ Initiating Inference...";
            
            try {
                await fetch('/api/ml/trigger-inference', { method: 'POST' });
                alert("Daily ML Inference initiated in the background. Check system notifications for progress.");
            } catch (error) {
                alert("Failed to initiate ML Inference. Network or server error.");
            }
            
            setTimeout(() => {
                btn.disabled = false;
                btn.innerText = "▶️ Run Inference Now";
            }, 3000);
        }

        async function triggerAnomalyTraining() {
            const btn = document.querySelector('button[onclick="triggerAnomalyTraining()"]');
            btn.disabled = true;
            btn.innerText = "▶️ Training Models...";

            try {
                await fetch('/api/ml/trigger-anomaly-training', { method: 'POST' });
                alert("Isolation Forest training initiated in the background. Check system notifications for progress.");
            } catch (error) {
                alert("Failed to initiate anomaly training. Network or server error.");
            }

            setTimeout(() => {
                btn.disabled = false;
                btn.innerText = "▶️ Train Models Now";
            }, 3000);
        }

        async function triggerMacroInit() {
            const btn = document.querySelector('button[onclick="triggerMacroInit()"]');
            btn.disabled = true;
            btn.innerText = "⚙️ Initializing Macro Engine...";
            
            try {
                await fetch('/api/macro/init-pipeline', { method: 'POST' });
                alert("Macro AI Pipeline initialized in the background. It will seed the calendar, sync events, and train the models sequentially. Check system notifications for progress. The page will reload once complete to update the button state.");
            } catch (error) {
                alert("Failed to initiate Macro AI Pipeline. Network or server error.");
            }
            
            setTimeout(() => {
                btn.disabled = false;
                btn.innerText = "⚙️ Initialize Macro AI Pipeline";
            }, 3000);
        }

        async function triggerMacroRun() {
            const btn = document.querySelector('button[onclick="triggerMacroRun()"]');
            btn.disabled = true;
            btn.innerText = "▶️ Running Macro Inference...";
            
            try {
                await fetch('/api/macro/run-pipeline', { method: 'POST' });
                alert("Macro AI Run initiated in the background. Check system notifications for progress.");
            } catch (error) {
                alert("Failed to run Macro AI Pipeline. Network or server error.");
            }
            
            setTimeout(() => {
                btn.disabled = false;
                btn.innerText = "▶️ Run Now";
            }, 3000);
        }

        async function loadDipRadarMonitors() {
            const container = document.getElementById('dip-radar-monitor-list');
            try {
                const resp = await fetch('/api/intraday-monitor/list');
                const data = await resp.json();
                const monitors = data.monitors || [];
                if (monitors.length === 0) {
                    container.innerHTML = '<p style="color:#888;font-size:13px;">No tickers armed for today\'s session.</p>';
                    return;
                }
                container.innerHTML = monitors.map(m => `
                    <div style="display:flex;justify-content:space-between;align-items:center;padding:8px 0;border-bottom:1px solid #3a3a3a;">
                        <span class="text-orange font-bold">${m.ticker}</span>
                        <span style="color:${m.is_active ? '#00ff00' : '#888'};font-size:13px;">${m.is_active ? '● Active' : '○ Inactive'}</span>
                        ${m.is_active ? `<button class="btn-test mt-0" style="padding:4px 10px;font-size:12px;" onclick="disableDipMonitor('${m.ticker}')">Disable</button>` : '<span style="color:#555;font-size:12px;">—</span>'}
                    </div>`).join('');
            } catch(e) {
                container.innerHTML = '<p style="color:#f44336;font-size:13px;">Failed to load monitors.</p>';
            }
        }

        async function disableDipMonitor(ticker) {
            try {
                await fetch('/api/intraday-monitor/remove', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ ticker: ticker })
                });
            } catch(e) { console.error('disableDipMonitor error:', e); }
            loadDipRadarMonitors();
        }

        // Load Dip Radar monitor list on page load
        loadDipRadarMonitors();

        async function triggerAIContagionScan() {
            const btn = document.querySelector('button[onclick="triggerAIContagionScan()"]');
            const msgEl = document.getElementById('ai-contagion-scan-msg');
            btn.disabled = true;
            btn.innerText = "⏳ Scanning...";
            msgEl.innerHTML = '';
            try {
                const resp = await fetch('/api/ai-contagion/trigger', { method: 'POST' });
                const data = await resp.json();
                const color = data.status === 'success' ? '#4caf50' : '#f44336';
                msgEl.innerHTML = `<span style="color:${color}; font-size:13px;">${data.message}</span>`;
            } catch (err) {
                msgEl.innerHTML = `<span style="color:#f44336; font-size:13px;">Request failed: ${err.message}</span>`;
            } finally {
                setTimeout(() => { btn.disabled = false; btn.innerText = "▶ Run Scan Now"; }, 3000);
            }
        }

        async function triggerTrapMonitorScan() {
            const btn = document.querySelector('button[onclick="triggerTrapMonitorScan()"]');
            const msgEl = document.getElementById('trap-monitor-msg');
            btn.disabled = true;
            btn.innerText = "⏳ Scanning...";
            msgEl.innerHTML = '';
            try {
                const resp = await fetch('/api/trap-monitor/run', { method: 'POST' });
                const data = await resp.json();
                const color = data.status === 'success' ? '#4caf50' : '#f44336';
                msgEl.innerHTML = `<span style="color:${color}; font-size:13px;">${data.message}</span>`;
            } catch (err) {
                msgEl.innerHTML = `<span style="color:#f44336; font-size:13px;">Request failed: ${err.message}</span>`;
            } finally {
                setTimeout(() => { btn.disabled = false; btn.innerText = "▶ Run Scan Now"; }, 3000);
            }
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
            } catch (_) {}
        }

        document.addEventListener('DOMContentLoaded', () => {
            initGlobalBrowserNotifications();
            setInterval(pollGlobalSystemNotifications, 15000);
            pollGlobalSystemNotifications();

            // Poll Network Status
            fetchNetworkStatus();
            setInterval(fetchNetworkStatus, 10000); // Update badge every 10s

            // NEW: Poll Profiler Queue Status
            fetchProfilerQueueStatus();
            setInterval(fetchProfilerQueueStatus, 30000); // Refresh every 30 seconds

            fetchSystemChecks();
        });

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

                // Colour-code Pending Now: orange if work to do, green if quiet
                pendingEl.classList.remove('text-orange', 'text-green', 'text-muted');
                pendingEl.classList.add(pending > 0 ? 'text-orange' : 'text-green');

                // Colour-code Stale: orange if any, muted if zero
                staleEl.classList.remove('text-orange', 'text-muted');
                staleEl.classList.add(stale > 0 ? 'text-orange' : 'text-muted');

                // Generate an institutional-grade explanation string
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

                    // 1. Universe & Coverage
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

                    // 2. ML Artifacts
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

                    // 3. Storage & JSON
                    document.getElementById('diag-db-size').innerText = `${data.infra.db_size_mb} MB`;
                    document.getElementById('diag-hist-size').innerText = `${data.infra.hist_size_mb} MB`;
                    document.getElementById('diag-hist-cnt').innerText = `${fmt(data.infra.hist_cnt)} Files`;
                    document.getElementById('diag-intra-size').innerText = `${data.infra.intra_size_mb} MB`;
                    document.getElementById('diag-intra-cnt').innerText = `${fmt(data.infra.intra_cnt)} Files`;
                    
                    document.getElementById('diag-fund-cnt').innerText = `${fmt(data.universe.fundamentals_files)} Files`;
                    document.getElementById('diag-json-port').innerText = `${fmt(data.universe.json_trackers.portfolio)} Items`;
                    document.getElementById('diag-json-watch').innerText = `${fmt(data.universe.json_trackers.watchlist)} Items`;
                    document.getElementById('diag-json-bl').innerText = `${fmt(data.universe.json_trackers.blacklist)} Items`;

                    // 4. Infrastructure
                    document.getElementById('diag-cpu').innerText = data.infra.cpu.join(', ');
                    document.getElementById('diag-disk-tot').innerText = `${data.infra.disk_total_gb} GB`;
                    document.getElementById('diag-disk-used').innerText = `${data.infra.disk_used_gb} GB`;
                    document.getElementById('diag-disk-pct').innerText = `${data.infra.disk_pct}% Used`;

                    // 5. Scheduler Last Run
                    if (data.scheduler_last_runs) {
                        document.querySelectorAll('[data-sched-key]').forEach(td => {
                            const key = td.getAttribute('data-sched-key');
                            const val = data.scheduler_last_runs[key];
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

        async function saveSettings(silent = false) {
            const btn = document.querySelector('.btn-save');
            
            if (!silent) {
                btn.disabled = true;
                btn.innerText = "Saving Configuration...";
            }
            
            const activeAccounts = [];
            const checkboxes = document.querySelectorAll('.ghostfolio-account-checkbox');
            checkboxes.forEach((cb) => {
                if(cb.checked) {
                    activeAccounts.push(cb.value);
                }
            });

            // Harvest checked days into arrays
            const quantDays = Array.from(document.querySelectorAll('.quant-day:checked')).map(cb => cb.value);
            const earnDays = Array.from(document.querySelectorAll('.earn-day:checked')).map(cb => cb.value);
            const dispatchDays = Array.from(document.querySelectorAll('.dispatch-day:checked')).map(cb => cb.value);
            const lunchDispatchDays = Array.from(document.querySelectorAll('.lunch-dispatch-day:checked')).map(cb => cb.value);
            const universeDays = Array.from(document.querySelectorAll('.universe-day:checked')).map(cb => cb.value);
            const mlBackfillDays = Array.from(document.querySelectorAll('.ml-backfill-day:checked')).map(cb => cb.value);
            const mlTrainingDays = Array.from(document.querySelectorAll('.ml-training-day:checked')).map(cb => cb.value);
            const mlInferenceDays = Array.from(document.querySelectorAll('.ml-inference-day:checked')).map(cb => cb.value);
            const indexDays = Array.from(document.querySelectorAll('.index-day:checked')).map(cb => cb.value);
            const profilerDays = Array.from(document.querySelectorAll('.profiler-day:checked')).map(cb => cb.value);
            const udsDays = Array.from(document.querySelectorAll('.uds-day:checked')).map(cb => cb.value);
            const activeIndices = Array.from(document.querySelectorAll('.index-target:checked')).map(cb => cb.value);
            
            const payload = {
                "SERVER_URL": document.getElementById('SERVER_URL').value,
                "PORT": parseInt(document.getElementById('PORT').value),
                "BASE_CURRENCY": document.getElementById('BASE_CURRENCY').value,
                "USER_TIMEZONE": document.getElementById('USER_TIMEZONE').value.trim(),
                "HOME_EXCHANGE": document.getElementById('HOME_EXCHANGE').value,
                "IGNORED_TICKERS": document.getElementById('IGNORED_TICKERS').value.split(',').map(s => s.trim()).filter(Boolean),
                "FILE_LOGGING": {
                    "ENABLED": document.getElementById('FILE_LOGGING_ENABLED').checked,
                    "LEVEL": document.getElementById('FILE_LOGGING_LEVEL').value,
                    "DAYS_TO_KEEP": parseInt(document.getElementById('FILE_LOGGING_DAYS_TO_KEEP').value) || 30,
                    "ARCHIVE": document.getElementById('FILE_LOGGING_ARCHIVE').checked,
                    "LOG_DIR": document.getElementById('FILE_LOGGING_LOG_DIR').value.trim() || 'logs'
                },
                "YAHOO_IPV6_ADDRESS": document.getElementById('YAHOO_IPV6_ADDRESS').value.trim(),
                "NETWORK_FAULT_NOTIFY_NEXTCLOUD": document.getElementById('NETWORK_FAULT_NOTIFY_NEXTCLOUD').checked,
                "GHOSTFOLIO_ACCOUNTS": {
                    "discovered": currentDiscoveredAccounts,
                    "active": activeAccounts
                },
                "UI_PREFERENCES": {
                    "LIVE_PORTFOLIO": document.getElementById('LIVE_PORTFOLIO').checked,
                    "LIVE_WATCHLIST": document.getElementById('LIVE_WATCHLIST').checked,
                    "LIVE_DETAILS": document.getElementById('LIVE_DETAILS').checked,
                    "FREETRADE_ONLY_MODE": document.getElementById('FREETRADE_ONLY_MODE').checked,
                    "REFRESH_RATE": parseInt(document.getElementById('REFRESH_RATE').value) || 60
                },
                "POSITION_SIZING": {
                    "ACCOUNT_VALUE": parseFloat(document.getElementById('POSITION_SIZING_ACCOUNT_VALUE').value) || 10000,
                    "RISK_PCT":      parseFloat(document.getElementById('POSITION_SIZING_RISK_PCT').value) || 1.0,
                    "STOP_MULTIPLE": parseFloat(document.getElementById('POSITION_SIZING_STOP_MULTIPLE').value) || 2.0
                },
                "SCHEDULING": {
                    "GHOSTFOLIO_SYNC": {
                        "ENABLED": document.getElementById('GHOSTFOLIO_SYNC_ENABLED').checked,
                        "FREQUENCY": document.getElementById('GHOSTFOLIO_SYNC_FREQ').value,
                        "INTERVAL_HOURS": parseInt(document.getElementById('GHOSTFOLIO_SYNC_INTERVAL').value) || 0,
                        "TIME": document.getElementById('GHOSTFOLIO_SYNC_TIME').value
                    },
                    "FREETRADE_SYNC": {
                        "ENABLED": document.getElementById('FREETRADE_SYNC_ENABLED').checked,
                        "FREQUENCY": document.getElementById('FREETRADE_SYNC_FREQ').value,
                        "TIME": document.getElementById('FREETRADE_SYNC_TIME').value
                    },
                    "QUANT_ANALYSIS": {
                        "ENABLED": document.getElementById('QUANT_ANALYSIS_ENABLED').checked,
                        "FREQUENCY": document.getElementById('QUANT_ANALYSIS_FREQ').value,
                        "INTERVAL_HOURS": parseInt(document.getElementById('QUANT_ANALYSIS_INTERVAL').value) || 0,
                        "TIME": document.getElementById('QUANT_ANALYSIS_TIME').value
                    },
                    "SENTIMENT_ENGINE": {
                        "ENABLED": document.getElementById('SENTIMENT_ENGINE_ENABLED').checked,
                        "FREQUENCY": document.getElementById('SENTIMENT_ENGINE_FREQ').value,
                        "START_TIME": document.getElementById('SENTIMENT_ENGINE_START').value,
                        "END_TIME": document.getElementById('SENTIMENT_ENGINE_END').value,
                        "INTERVAL_HOURS": parseInt(document.getElementById('SENTIMENT_ENGINE_INTERVAL').value) || 4
                    },
                    "NEWS_FEED": {
                        "ENABLED": document.getElementById('NEWS_FEED_ENABLED').checked,
                        "FREQUENCY": document.getElementById('NEWS_FEED_FREQ').value,
                        "START_TIME": document.getElementById('NEWS_FEED_START').value,
                        "END_TIME": document.getElementById('NEWS_FEED_END').value,
                        "INTERVAL_HOURS": parseInt(document.getElementById('NEWS_FEED_INTERVAL').value) || 4,
                        "MAX_PER_TICKER": parseInt(document.getElementById('NEWS_FEED_MAX_PER').value) || 5,
                        "MAX_AGE_DAYS": parseInt(document.getElementById('NEWS_FEED_MAX_AGE').value) || 7
                    },
                    "CRASH_ALERTS": {
                        "ENABLED": document.getElementById('CRASH_ALERTS_SCHED_ENABLED').checked,
                        "FREQUENCY": document.getElementById('CRASH_ALERTS_FREQ').value,
                        "START_TIME": document.getElementById('CRASH_ALERTS_START').value,
                        "END_TIME": document.getElementById('CRASH_ALERTS_END').value,
                        "INTERVAL_MINUTES": parseInt(document.getElementById('CRASH_ALERTS_MINUTES').value) || 10,
                        "FLASH_CRASH_THRESHOLD": parseFloat(document.getElementById('CRASH_FLASH_THRESHOLD').value)
                    },
                    "MOONSHOT_ALERTS": {
                        "ENABLED": document.getElementById('MOONSHOT_ALERTS_SCHED_ENABLED').checked,
                        "FREQUENCY": document.getElementById('MOONSHOT_ALERTS_FREQ').value,
                        "START_TIME": document.getElementById('MOONSHOT_ALERTS_START').value,
                        "END_TIME": document.getElementById('MOONSHOT_ALERTS_END').value,
                        "INTERVAL_MINUTES": parseInt(document.getElementById('MOONSHOT_ALERTS_MINUTES').value) || 10,
                        "SPIKE_PERCENT": parseFloat(document.getElementById('MOONSHOT_SPIKE_PERCENT').value),
                        "SPIKE_DAYS": parseInt(document.getElementById('MOONSHOT_SPIKE_DAYS').value),
                        "SMA_LENGTH": parseInt(document.getElementById('MOONSHOT_SMA_LENGTH').value),
                        "SMA_GAP_PERCENT": parseFloat(document.getElementById('MOONSHOT_SMA_GAP_PERCENT').value)
                    },
                    "MAINTENANCE": {
                        "ENABLED": document.getElementById('MAINTENANCE_ENABLED').checked,
                        "DAY_OF_WEEK": document.getElementById('MAINTENANCE_DAY').value,
                        "TIME": document.getElementById('MAINTENANCE_TIME').value,
                        "DAYS_TO_KEEP_FILES": parseInt(document.getElementById('MAINTENANCE_DAYS_TO_KEEP_FILES').value) || 60
                    },
                    "QUANT_ENGINE": {
                        "DAYS": quantDays,
                        "TIME": document.getElementById('QUANT_ENGINE_TIME').value
                    },
                    "EARNINGS_ENGINE": {
                        "DAYS": earnDays,
                        "TIME": document.getElementById('EARNINGS_ENGINE_TIME').value
                    },
                    "DISPATCHER": {
                        "ENABLED": document.getElementById('DISPATCHER_ENABLED').checked,
                        "DAYS": dispatchDays,
                        "TIME": document.getElementById('DISPATCHER_TIME').value
                    },
                    "LUNCH_DISPATCHER": {
                        "ENABLED": document.getElementById('LUNCH_DISPATCHER_ENABLED').checked,
                        "DAYS": lunchDispatchDays,
                        "TIME": document.getElementById('LUNCH_DISPATCHER_TIME').value
                    },
                    "SYNC_INDICES": {
                        "ENABLED": document.getElementById('SYNC_INDICES_ENABLED').checked,
                        "INDICES": activeIndices,
                        "DAYS": indexDays,
                        "TIME": document.getElementById('SYNC_INDICES_TIME').value
                    },
                    "PROFILER_ENGINE": {
                        "ENABLED": document.getElementById('PROFILER_ENGINE_ENABLED').checked,
                        "DAYS": profilerDays,
                        "TIME": document.getElementById('PROFILER_ENGINE_TIME').value,
                        "BATCH_SIZE": parseInt(document.getElementById('PROFILER_BATCH_SIZE').value) || 250
                    },
                    "UNIVERSE_DEEP_SYNC": {
                        "ENABLED": document.getElementById('UNIVERSE_DEEP_SYNC_ENABLED').checked,
                        "DAYS": udsDays,
                        "TIME": document.getElementById('UNIVERSE_DEEP_SYNC_TIME').value
                    },
                    "UNIVERSE_ENGINE": {
                        "ENABLED": document.getElementById('UNIVERSE_ENGINE_ENABLED').checked,
                        "DAYS": universeDays,
                        "TIME": document.getElementById('UNIVERSE_ENGINE_TIME').value
                    },
                    "ML_BACKFILL": {
                        "ENABLED": document.getElementById('ML_BACKFILL_ENABLED').checked,
                        "DAYS": mlBackfillDays,
                        "TIME": document.getElementById('ML_BACKFILL_TIME').value
                    },
                    "ML_TRAINING": {
                        "ENABLED": document.getElementById('ML_TRAINING_ENABLED').checked,
                        "DAYS": mlTrainingDays,
                        "TIME": document.getElementById('ML_TRAINING_TIME').value
                    },
                    "ML_INFERENCE": {
                        "ENABLED": document.getElementById('ML_INFERENCE_ENABLED').checked,
                        "DAYS": mlInferenceDays,
                        "TIME": document.getElementById('ML_INFERENCE_TIME').value
                    },
                    "MACRO_ENGINE": {
                        "ENABLED": document.getElementById('MACRO_ENGINE_ENABLED').checked,
                        "INITIALIZED": macroInitState,
                        "CALENDAR_TIME": document.getElementById('MACRO_CALENDAR_TIME').value,
                        "DATA_DAY": document.getElementById('MACRO_DATA_DAY').value,
                        "DATA_TIME": document.getElementById('MACRO_DATA_TIME').value
                    },
                    "AI_CONTAGION": {
                        "ENABLED": document.getElementById('AI_CONTAGION_SCHED_ENABLED').checked,
                        "FREQUENCY": document.getElementById('AI_CONTAGION_FREQ').value,
                        "START_TIME": document.getElementById('AI_CONTAGION_START').value,
                        "END_TIME": document.getElementById('AI_CONTAGION_END').value,
                        "INTERVAL_MINUTES": parseInt(document.getElementById('AI_CONTAGION_INTERVAL').value) || 15
                    },
                    "SMGB_PREDICTOR": {
                        "ENABLED": document.getElementById('SMGB_PREDICTOR_ENABLED').checked,
                        "PRE_US_OPEN_TIME": document.getElementById('SMGB_PRE_US_OPEN_TIME').value,
                        "POST_US_CLOSE_TIME": document.getElementById('SMGB_POST_US_CLOSE_TIME').value,
                        "SEND_NEXTCLOUD": document.getElementById('SMGB_PREDICTOR_SEND_NEXTCLOUD').checked
                    },
                    "TRAP_MONITORS": {
                        "ENABLED": document.getElementById('TRAP_MONITOR_ENABLED').checked,
                        "BULL_TRAP": document.getElementById('TRAP_BULL_ENABLED').checked,
                        "BEAR_TRAP": document.getElementById('TRAP_BEAR_ENABLED').checked,
                        "CAPITULATION": document.getElementById('TRAP_CAP_ENABLED').checked,
                        "WYCKOFF": document.getElementById('TRAP_WYK_ENABLED').checked,
                        "MONITOR_PORTFOLIO": document.getElementById('TRAP_MONITOR_PORTFOLIO').checked,
                        "FREQUENCY": document.getElementById('TRAP_MONITOR_FREQ').value,
                        "START_TIME": document.getElementById('TRAP_MONITOR_START').value,
                        "END_TIME": document.getElementById('TRAP_MONITOR_END').value,
                        "INTERVAL_MINUTES": parseInt(document.getElementById('TRAP_MONITOR_INTERVAL').value) || 30
                    }
                },
                "NOTIFICATIONS": {
                    "MARKET_SENTIMENT": {
                        "ENABLED": document.getElementById('FNG_ENABLED').checked,
                        "TIME": document.getElementById('FNG_TIME').value,
                        "FREQUENCY": document.getElementById('FNG_FREQUENCY').value
                    },
                    "EARNINGS_ALERTS": {
                        "ENABLED": document.getElementById('EARNINGS_ENABLED').checked,
                        "TIME": document.getElementById('EARNINGS_TIME').value,
                        "DAYS_AHEAD": parseInt(document.getElementById('EARNINGS_DAYS_AHEAD').value),
                        "ALERT_TYPE": document.getElementById('EARNINGS_ALERT_TYPE').value
                    },
                    "INSIDER_TRADING": {
                        "ENABLED_PORTFOLIO": document.getElementById('INSIDER_ENABLED_PORTFOLIO').checked,
                        "ENABLED_WATCHLIST": document.getElementById('INSIDER_ENABLED_WATCHLIST').checked,
                        "TIME": document.getElementById('INSIDER_TIME').value,
                        "FREQUENCY": document.getElementById('INSIDER_FREQUENCY').value,
                        "MIN_VALUE": parseInt(document.getElementById('INSIDER_MIN_VALUE').value),
                        "DAYS_BACK": parseInt(document.getElementById('INSIDER_DAYS_BACK').value)
                    },
                    "CRASH_ALERTS": {
                        "DROP_PERCENT": parseFloat(document.getElementById('CRASH_DROP_PERCENT').value),
                        "DROP_DAYS": parseInt(document.getElementById('CRASH_DROP_DAYS').value),
                        "SMA_LENGTH": parseInt(document.getElementById('CRASH_SMA_LENGTH').value),
                        "SMA_GAP_PERCENT": parseFloat(document.getElementById('CRASH_SMA_GAP_PERCENT').value),
                        "FLASH_CRASH_THRESHOLD": parseFloat(document.getElementById('CRASH_FLASH_THRESHOLD').value)
                    },
                    "MOONSHOT_ALERTS": {
                        "SPIKE_PERCENT": parseFloat(document.getElementById('MOONSHOT_SPIKE_PERCENT').value),
                        "SPIKE_DAYS": parseInt(document.getElementById('MOONSHOT_SPIKE_DAYS').value),
                        "SMA_LENGTH": parseInt(document.getElementById('MOONSHOT_SMA_LENGTH').value),
                        "SMA_GAP_PERCENT": parseFloat(document.getElementById('MOONSHOT_SMA_GAP_PERCENT').value)
                    },
                    "RSS_FEED": {
                        "ENABLED": document.getElementById('RSS_FEED_ENABLED').checked
                    },
                    "AI_CONTAGION": {
                        "ENABLED": document.getElementById('AI_CONTAGION_ENABLED').checked,
                        "LEADER_THRESHOLD_PCT": parseFloat(document.getElementById('AI_CONTAGION_LEADER_THRESHOLD').value),
                        "ETF_CONFIRMATION_THRESHOLD_PCT": parseFloat(document.getElementById('AI_CONTAGION_ETF_THRESHOLD').value),
                        "VOLUME_SPIKE_MULTIPLIER": parseFloat(document.getElementById('AI_CONTAGION_VOLUME_MULT').value),
                        "BELLWETHER_TICKERS": document.getElementById('AI_CONTAGION_BELLWETHERS').value
                            .split(/[,\s]+/).map(s => s.trim()).filter(Boolean),
                        "ETF_BASKET": document.getElementById('AI_CONTAGION_ETFS').value
                            .split(/[,\s]+/).map(s => s.trim()).filter(Boolean),
                        "COOLDOWN_MINUTES": parseFloat(document.getElementById('AI_CONTAGION_COOLDOWN').value),
                        "RETRIGGER_PERCENT": parseFloat(document.getElementById('AI_CONTAGION_RETRIGGER').value),
                        "REARM_PERCENT": parseFloat(document.getElementById('AI_CONTAGION_REARM').value)
                    },
                    "TRAP_MONITOR_ALERTS": {
                        "NEXTCLOUD_ENABLED": document.getElementById('TRAP_NEXTCLOUD_ENABLED').checked,
                        "COOLDOWN_MINUTES": parseFloat(document.getElementById('TRAP_COOLDOWN').value),
                        "RETRIGGER_PERCENT": parseFloat(document.getElementById('TRAP_RETRIGGER').value),
                        "REARM_PERCENT": parseFloat(document.getElementById('TRAP_REARM').value),
                        "PROXY_TICKERS": document.getElementById('TRAP_PROXY_TICKERS').value
                            .split(/[,\s]+/).map(s => s.trim().toUpperCase()).filter(Boolean)
                    },
                    "DIP_RADAR_NEXTCLOUD": document.getElementById('DIP_RADAR_NEXTCLOUD').checked
                },
                "XRAY_TARGETS": (function() {
                    function xt(id) {
                        const v = (document.getElementById(id)?.value ?? '').trim();
                        return v === '' ? null : parseFloat(v);
                    }
                    return {
                        "market_development": {
                            "Developed Markets": { "min": xt('XT_DEV_MIN'), "max": xt('XT_DEV_MAX') },
                            "Emerging Markets":  { "min": xt('XT_EM_MIN'),  "max": xt('XT_EM_MAX') }
                        },
                        "regional_clusters": {
                            "North America":    { "min": xt('XT_NA_MIN'),    "max": xt('XT_NA_MAX') },
                            "Europe":           { "min": xt('XT_EU_MIN'),    "max": xt('XT_EU_MAX') },
                            "Japan":            { "min": xt('XT_JP_MIN'),    "max": xt('XT_JP_MAX') },
                            "Asia-Pacific":     { "min": xt('XT_AP_MIN'),    "max": xt('XT_AP_MAX') },
                            "Emerging Markets": { "min": xt('XT_RC_EM_MIN'), "max": xt('XT_RC_EM_MAX') }
                        },
                        "country_concentration": {
                            "United States":  { "min": null, "max": xt('XT_CC_US') },
                            "China":          { "min": null, "max": xt('XT_CC_CN') },
                            "Japan":          { "min": null, "max": xt('XT_CC_JP') },
                            "United Kingdom": { "min": null, "max": xt('XT_CC_GB') }
                        },
                        "sector_targets": {
                            "Technology":             { "min": null, "max": xt('XT_SEC_TECH') },
                            "Financials":             { "min": null, "max": xt('XT_SEC_FIN')  },
                            "Healthcare":             { "min": null, "max": xt('XT_SEC_HLTH') },
                            "Consumer Cyclical":      { "min": null, "max": xt('XT_SEC_CC')   },
                            "Industrials":            { "min": null, "max": xt('XT_SEC_IND')  },
                            "Communication Services": { "min": null, "max": xt('XT_SEC_COMM') },
                            "Consumer Staples":       { "min": null, "max": xt('XT_SEC_CS')   },
                            "Energy":                 { "min": null, "max": xt('XT_SEC_ENE')  },
                            "Materials":              { "min": null, "max": xt('XT_SEC_MAT')  },
                            "Utilities":              { "min": null, "max": xt('XT_SEC_UTIL') },
                            "Real Estate":            { "min": null, "max": xt('XT_SEC_RE')   }
                        },
                        "asset_class_targets": {
                            "ETF":          { "min": xt('XT_AC_ETF_MIN'), "max": xt('XT_AC_ETF_MAX') },
                            "Equity":       { "min": xt('XT_AC_EQ_MIN'),  "max": xt('XT_AC_EQ_MAX')  },
                            "Fixed Income": { "min": xt('XT_AC_FI_MIN'),  "max": xt('XT_AC_FI_MAX')  },
                            "Commodity":    { "min": xt('XT_AC_COM_MIN'), "max": xt('XT_AC_COM_MAX') }
                        },
                        "concentration_targets": {
                            "max_single_position_pct": xt('XT_CONC_MAX_POS'),
                            "top5_weight_max_pct":     xt('XT_CONC_TOP5'),
                            "top10_weight_max_pct":    xt('XT_CONC_TOP10'),
                            "hhi_max":                 xt('XT_CONC_HHI')
                        },
                        "risk_metric_targets": {
                            "portfolio_beta_min":      xt('XT_RISK_BETA_MIN'),
                            "portfolio_beta_max":      xt('XT_RISK_BETA_MAX'),
                            "annualized_vol_max_pct":  xt('XT_RISK_VOL_MAX'),
                            "sharpe_ratio_min":        xt('XT_RISK_SHARPE_MIN'),
                            "max_drawdown_max_pct":    xt('XT_RISK_DD_MAX'),
                            "avg_correlation_max":     xt('XT_RISK_CORR_MAX')
                        },
                        "income_targets": {
                            "dividend_yield_min_pct": xt('XT_INC_DIV_MIN')
                        }
                    };
                })()
            };

            try {
                const response = await fetch('/api/settings', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Confirm-Token': CONFIRM_TOKEN },
                    body: JSON.stringify(payload)
                });
                
                const result = await response.json();
                
                if (!silent) {
                    if (response.ok) {
                        setStatus('status-msg', 'success', "Settings saved. Background schedulers restarted dynamically.");
                    } else {
                        const errMsg = result.message || (result.detail ? (Array.isArray(result.detail) ? result.detail.map(d => d.loc.join('.') + ': ' + d.msg).join('; ') : result.detail) : 'Unknown error');
                        setStatus('status-msg', 'error', errMsg);
                    }
                }
            } catch (error) {
                if (!silent) {
                    setStatus('status-msg', 'error', "Network Error while saving.");
                }
            }
            
            if (!silent) {
                setTimeout(() => { 
                    btn.disabled = false; 
                    btn.innerText = "💾 Save & Apply System Settings"; 
                    document.getElementById('status-msg').innerText = "";
                }, 5000);
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

        async function saveHFToken() {
            const btn = document.querySelector('button[onclick="saveHFToken()"]');
            btn.disabled = true;
            btn.innerText = '⏳ Saving…';
            setStatus('hf-status-msg', 'info', 'Saving…');
            try {
                const res = await fetch('/api/save-hf-token', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Confirm-Token': CONFIRM_TOKEN },
                    body: JSON.stringify({ HF_TOKEN: document.getElementById('HF_TOKEN').value.trim() }),
                });
                const data = await res.json().catch(() => ({}));
                setStatus('hf-status-msg', res.ok ? 'success' : 'error',
                    res.ok ? 'HF Token saved.' : (data.detail || 'Failed to save.'));
            } catch (e) {
                setStatus('hf-status-msg', 'error', 'Network error while saving.');
            } finally {
                btn.disabled = false;
                btn.innerText = '💾 Save HF Token';
            }
        }

        async function testHFToken() {
            const btn = document.querySelector('button[onclick="testHFToken()"]');
            btn.disabled = true;
            btn.innerText = '⏳ Verifying…';
            setStatus('hf-status-msg', 'info', 'Verifying token…');
            try {
                const res = await fetch('/api/test-hf-token', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'X-Confirm-Token': CONFIRM_TOKEN },
                    body: JSON.stringify({ HF_TOKEN: document.getElementById('HF_TOKEN').value.trim() }),
                });
                const data = await res.json().catch(() => ({}));
                setStatus('hf-status-msg', res.ok ? 'success' : 'error',
                    res.ok ? (data.message || 'Token is valid.') : (data.detail || 'Verification failed.'));
            } catch (e) {
                setStatus('hf-status-msg', 'error', 'Network error while verifying.');
            } finally {
                btn.disabled = false;
                btn.innerText = '🧪 Verify Token';
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

        // Settings search
        (function() {
            const searchInput = document.getElementById('settingsSearch');
            const cards = document.querySelectorAll('details.settings-card');
            const noResults = document.getElementById('noSettingsResults');

            searchInput.addEventListener('input', function() {
                const query = this.value.toLowerCase().trim();
                let found = 0;

                cards.forEach(card => {
                    const matches = query === '' || card.innerText.toLowerCase().includes(query);
                    card.style.display = matches ? '' : 'none';
                    if (matches) {
                        found++;
                        if (query !== '' && !card.hasAttribute('ontoggle')) {
                            card.setAttribute('open', '');
                        }
                    } else if (query !== '') {
                        card.removeAttribute('open');
                    }
                });

                noResults.style.display = (found === 0 && query !== '') ? 'block' : 'none';
            });
        })();
