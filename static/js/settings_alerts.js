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
        } catch (e) { }
        return '16:05 ET';
    }
    document.addEventListener('DOMContentLoaded', function() {
        document.querySelectorAll('.dip-reset-time').forEach(function(el) {
            el.textContent = formatDipResetLocalTime();
        });
    });
})();

function copyRssFeedUrl() {
    const url = document.getElementById('RSS_FEED_URL').value;
    navigator.clipboard.writeText(url).then(() => {
        const btn = document.querySelector('button[onclick="copyRssFeedUrl()"]');
        const orig = btn.innerText;
        btn.innerText = 'Copied!';
        setTimeout(() => { btn.innerText = orig; }, 1500);
    });
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
                <span class="text-orange font-bold">${escapeHtml(m.ticker)}</span>
                <span style="color:${m.is_active ? '#00ff00' : '#888'};font-size:13px;">${m.is_active ? '● Active' : '○ Inactive'}</span>
                ${m.is_active ? `<button class="btn-test mt-0" style="padding:4px 10px;font-size:12px;" data-ticker="${escapeHtml(m.ticker)}" onclick="disableDipMonitor(this.dataset.ticker)">Disable</button>` : '<span style="color:#555;font-size:12px;">—</span>'}
            </div>`).join('');
    } catch (e) {
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
    } catch (e) { console.error('disableDipMonitor error:', e); }
    loadDipRadarMonitors();
}

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
        msgEl.innerHTML = `<span style="color:${color}; font-size:13px;">${escapeHtml(data.message)}</span>`;
    } catch (err) {
        msgEl.innerHTML = `<span style="color:#f44336; font-size:13px;">Request failed: ${escapeHtml(err.message)}</span>`;
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
        msgEl.innerHTML = `<span style="color:${color}; font-size:13px;">${escapeHtml(data.message)}</span>`;
    } catch (err) {
        msgEl.innerHTML = `<span style="color:#f44336; font-size:13px;">Request failed: ${escapeHtml(err.message)}</span>`;
    } finally {
        setTimeout(() => { btn.disabled = false; btn.innerText = "▶ Run Scan Now"; }, 3000);
    }
}

async function triggerAlertRefereeTraining() {
    const btn = document.querySelector('button[onclick="triggerAlertRefereeTraining()"]');
    const msgEl = document.getElementById('alert-referee-msg');
    btn.disabled = true;
    btn.innerText = "⏳ Training...";
    msgEl.innerHTML = '';
    try {
        const resp = await fetch('/api/alert-referee/train', { method: 'POST' });
        const data = await resp.json();
        const color = data.status === 'success' ? '#4caf50' : '#f44336';
        msgEl.innerHTML = `<span style="color:${color}; font-size:13px;">${escapeHtml(data.message)}</span>`;
        setTimeout(fetchAlertRefereeReadiness, 4000);
    } catch (err) {
        msgEl.innerHTML = `<span style="color:#f44336; font-size:13px;">Request failed: ${escapeHtml(err.message)}</span>`;
    } finally {
        setTimeout(() => { btn.disabled = false; btn.innerText = "▶ Run Training Now"; }, 3000);
    }
}

function fetchAlertRefereeReadiness() {
    const el = document.getElementById('alert-referee-readiness');
    if (!el) return;
    fetch('/api/alert-referee/status')
        .then(r => r.json())
        .then(data => {
            if (data.status !== 'success') { el.textContent = 'Unable to load readiness.'; return; }
            const r = data.readiness || {};
            const current = r.current || 0;
            const target = r.target || 0;
            const pending = r.pending || 0;
            const pct = target > 0 ? Math.min(100, Math.round((current / target) * 100)) : 0;
            let etaText = 'not enough history yet to estimate';
            if (r.ready_for_active) {
                etaText = 'ready for Active mode';
            } else if (r.eta_date) {
                etaText = `~${escapeHtml(r.eta_date)} at the current pace`;
            }
            const model = data.latest_model;
            const modelLine = model
                ? `Last trained ${escapeHtml(model.trained_at)} on ${model.sample_count} samples (train accuracy ${(model.train_accuracy * 100).toFixed(0)}%).`
                : 'No model trained yet.';
            el.innerHTML =
                `<div class="mb-10">${current} / ${target} resolved signals with recorded features (${pct}%) — ${etaText}.</div>` +
                `<div class="mb-10 text-muted">${pending} more already have features recorded and are awaiting their 14-day outcome (leading indicator — not yet counted above).</div>` +
                `<div class="mb-10">${modelLine}</div>` +
                `<div>Shadow log: ${data.log_total || 0} evaluations recorded, ${data.log_vetoed || 0} would-veto.</div>`;
        })
        .catch(() => { el.textContent = 'Unable to load readiness.'; });
}

document.addEventListener('DOMContentLoaded', fetchAlertRefereeReadiness);

async function triggerForensicFetch() {
    const btn = document.querySelector('button[onclick="triggerForensicFetch()"]');
    const msgEl = document.getElementById('forensic-msg');
    btn.disabled = true;
    btn.innerText = "⏳ Running...";
    msgEl.innerHTML = '';
    try {
        const resp = await fetch('/api/forensic-scores/run-fetch', { method: 'POST' });
        const data = await resp.json();
        const color = data.status === 'success' ? '#4caf50' : '#f44336';
        msgEl.innerHTML = `<span style="color:${color}; font-size:13px;">${escapeHtml(data.message)}</span>`;
    } catch (err) {
        msgEl.innerHTML = `<span style="color:#f44336; font-size:13px;">Request failed: ${escapeHtml(err.message)}</span>`;
    } finally {
        setTimeout(() => { btn.disabled = false; btn.innerText = "▶ Run Fetch Now"; }, 3000);
    }
}

async function triggerForensicScore() {
    const btn = document.querySelector('button[onclick="triggerForensicScore()"]');
    const msgEl = document.getElementById('forensic-msg');
    btn.disabled = true;
    btn.innerText = "⏳ Running...";
    msgEl.innerHTML = '';
    try {
        const resp = await fetch('/api/forensic-scores/run-score', { method: 'POST' });
        const data = await resp.json();
        const color = data.status === 'success' ? '#4caf50' : '#f44336';
        msgEl.innerHTML = `<span style="color:${color}; font-size:13px;">${escapeHtml(data.message)}</span>`;
    } catch (err) {
        msgEl.innerHTML = `<span style="color:#f44336; font-size:13px;">Request failed: ${escapeHtml(err.message)}</span>`;
    } finally {
        setTimeout(() => { btn.disabled = false; btn.innerText = "▶ Run Scores Now"; }, 3000);
    }
}

async function triggerBubbleRadarScan() {
    const btn = document.querySelector('button[onclick="triggerBubbleRadarScan()"]');
    const msgEl = document.getElementById('bubble-radar-msg');
    btn.disabled = true;
    btn.innerText = "⏳ Scanning...";
    msgEl.innerHTML = '';
    try {
        const resp = await fetch('/api/bubble-radar/run', { method: 'POST' });
        const data = await resp.json();
        const color = data.status === 'success' ? '#4caf50' : '#f44336';
        msgEl.innerHTML = `<span style="color:${color}; font-size:13px;">${escapeHtml(data.message)}</span>`;
    } catch (err) {
        msgEl.innerHTML = `<span style="color:#f44336; font-size:13px;">Request failed: ${escapeHtml(err.message)}</span>`;
    } finally {
        setTimeout(() => { btn.disabled = false; btn.innerText = "▶ Run Scan Now"; }, 3000);
    }
}

async function triggerPairsSpreadScan() {
    const btn = document.querySelector('button[onclick="triggerPairsSpreadScan()"]');
    const msgEl = document.getElementById('pairs-spread-monitor-msg');
    btn.disabled = true;
    btn.innerText = "⏳ Scanning...";
    msgEl.innerHTML = '';
    try {
        const resp = await fetch('/api/pairs-spread/run', { method: 'POST' });
        const data = await resp.json();
        const color = data.status === 'success' ? '#4caf50' : '#f44336';
        msgEl.innerHTML = `<span style="color:${color}; font-size:13px;">${escapeHtml(data.message)}</span>`;
    } catch (err) {
        msgEl.innerHTML = `<span style="color:#f44336; font-size:13px;">Request failed: ${escapeHtml(err.message)}</span>`;
    } finally {
        setTimeout(() => { btn.disabled = false; btn.innerText = "▶ Run Scan Now"; }, 3000);
    }
}
