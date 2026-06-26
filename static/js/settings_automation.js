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

async function triggerAccountValueSnapshot() {
    const btn = document.querySelector('button[onclick="triggerAccountValueSnapshot()"]');
    if (btn) { btn.disabled = true; btn.textContent = '⏳ Queued…'; }
    try {
        await fetch('/api/accounts/value-snapshot/trigger', { method: 'POST' });
        alert("Account Value Snapshot job queued in background. Check System Notifications for completion.");
    } catch (error) {
        alert("Failed to queue Account Value Snapshot job. Network or server error.");
    } finally {
        if (btn) { btn.disabled = false; btn.innerHTML = '&#9654;&#65039; Run Now'; }
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
