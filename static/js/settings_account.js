async function triggerGhostfolioSync() {
    const btn = document.getElementById('syncBtn');
    btn.innerText = "Syncing JSON..."; btn.disabled = true;
    try {
        const job = await postJobTrigger('/api/sync-ghostfolio');
        alert(job.ok ? "Sync initiated in the background! Wait a few moments, then hit 'Force Update Analysis'." : (job.message || "The server did not start the job."));
    } catch (e) { }
    setTimeout(() => { btn.innerText = "⬇️ Sync Ghostfolio Data"; btn.disabled = false; }, 5000);
}

function showGeneratedSecret(id, value) {
    document.getElementById(id).value = value;
    document.getElementById(id + '_once').classList.remove('d-none');
    const state = document.getElementById(id + '_state');
    state.textContent = 'Set. The new value is shown below, once.';
    state.classList.replace('text-warning', 'text-success');
}

function copyGeneratedSecret(id, btn) {
    const input = document.getElementById(id);
    if (!navigator.clipboard) {
        input.select();
        return;
    }
    navigator.clipboard.writeText(input.value).then(() => {
        const orig = btn.innerText;
        btn.innerText = 'Copied!';
        setTimeout(() => { btn.innerText = orig; }, 1500);
    }).catch(() => input.select());
}

async function generateApiKey() {
    if (!confirm('Generate a new API key? The current key will stop working immediately.')) return;
    setStatus('ua-apikey-msg', 'info', 'Generating…');
    const res = await stepUpFetch('/api/generate-api-key', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
        showGeneratedSecret('ua_api_key', data.api_key);
        setStatus('ua-apikey-msg', 'success', 'New API key generated and saved. Copy it now — it will not be shown again.');
    } else {
        setStatus('ua-apikey-msg', 'error', data.detail || 'Failed to generate key.');
    }
}

async function toggleMcpServer(box) {
    const enabled = box.checked;
    setStatus('mcp-enabled-msg', 'info', 'Saving…');
    const options = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }) };
    const res = enabled ? await stepUpFetch('/api/mcp-server/enabled', options) : await fetch('/api/mcp-server/enabled', options);
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
        setStatus('mcp-enabled-msg', 'success', enabled ? 'MCP Server enabled.' : 'MCP Server disabled. Requests to /mcp are refused.');
    } else {
        box.checked = !enabled;
        setStatus('mcp-enabled-msg', 'error', data.detail || 'Failed to save.');
    }
}

async function loadMcpKeys() {
    const body = document.getElementById('mcp-keys-body');
    const res = await fetch('/api/mcp-keys');
    if (!res.ok) {
        body.innerHTML = '<tr><td colspan="4" class="text-muted">Could not load the keys.</td></tr>';
        return;
    }
    const keys = (await res.json()).keys;
    if (!keys.length) {
        body.innerHTML = '<tr><td colspan="4" class="text-warning">No MCP keys yet — create one below.</td></tr>';
        return;
    }
    body.innerHTML = keys.map(k => `<tr><td>${escapeHtml(k.name)}</td><td>${escapeHtml(k.created_at)}</td><td>${escapeHtml(k.last_used_at || 'Never')}</td>`
        + `<td><button type="button" class="btn btn-danger mt-0" data-key-id="${escapeHtml(String(k.id))}" data-key-name="${escapeHtml(k.name)}">🗑️ Delete</button></td></tr>`).join('');
}

async function createMcpKey() {
    const input = document.getElementById('mcp_key_name');
    const name = input.value.trim();
    if (!name) { setStatus('mcp-key-msg', 'error', 'Enter a name for the key.'); return; }
    setStatus('mcp-key-msg', 'info', 'Creating…');
    const res = await stepUpFetch('/api/mcp-keys', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ name }) });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
        document.getElementById('mcp_new_key').value = data.key;
        document.getElementById('mcp_key_once').classList.remove('d-none');
        input.value = '';
        setStatus('mcp-key-msg', 'success', 'Key created. Copy it now — it will not be shown again.');
        loadMcpKeys();
    } else {
        setStatus('mcp-key-msg', 'error', typeof data.detail === 'string' ? data.detail : 'Failed to create the key.');
    }
}

async function deleteMcpKey(id, name) {
    if (!confirm(`Delete the MCP key "${name}"? Any client using it stops working immediately.`)) return;
    setStatus('mcp-key-msg', 'info', 'Deleting…');
    const res = await stepUpFetch('/api/mcp-keys/' + encodeURIComponent(id), { method: 'DELETE' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
        setStatus('mcp-key-msg', 'success', 'Key deleted.');
        loadMcpKeys();
    } else {
        setStatus('mcp-key-msg', 'error', typeof data.detail === 'string' ? data.detail : 'Failed to delete the key.');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const url = document.getElementById('mcp_endpoint_url');
    if (url) url.textContent = window.location.origin + '/mcp';
    const keysBody = document.getElementById('mcp-keys-body');
    if (!keysBody) return;
    keysBody.addEventListener('click', e => {
        const btn = e.target.closest('button[data-key-id]');
        if (btn) deleteMcpKey(btn.dataset.keyId, btn.dataset.keyName);
    });
    loadMcpKeys();
});

function updatePolicyCharCount() {
    document.getElementById('policy-char-count').textContent = document.getElementById('investment_policy_text').value.length;
}

async function loadInvestmentPolicy() {
    const box = document.getElementById('investment_policy_text');
    setStatus('policy-load-msg', 'info', 'Loading…');
    const res = await fetch('/api/investment-policy');
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
        setStatus('policy-load-msg', 'error', 'Could not load the policy.');
        return;
    }
    box.value = data.text;
    updatePolicyCharCount();
    document.getElementById('policy-save-msg').textContent = '';
    const loadMsg = document.getElementById('policy-load-msg');
    if (data.load_error) {
        setStatus('policy-load-msg', 'warning', data.load_error + ' Saving replaces it.');
    } else if (!data.configured) {
        setStatus('policy-load-msg', 'warning', 'No policy saved yet. The text below is the suggested template: replace the placeholders with your own rules before saving, or Claude will read them as yours.');
    } else {
        loadMsg.textContent = '';
        loadMsg.className = '';
    }
}

async function saveInvestmentPolicy() {
    const text = document.getElementById('investment_policy_text').value;
    setStatus('policy-save-msg', 'info', 'Saving…');
    const res = await fetch('/api/investment-policy', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ text }) });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
        await loadInvestmentPolicy();
        setStatus('policy-save-msg', 'success', data.configured ? 'Policy saved. Claude reads it on its next request.' : 'The policy is empty, so Claude is told it is not configured.');
    } else {
        setStatus('policy-save-msg', 'error', typeof data.detail === 'string' ? data.detail : 'Failed to save the policy. Check that it is within the character limit.');
    }
}

document.addEventListener('DOMContentLoaded', () => {
    const box = document.getElementById('investment_policy_text');
    if (!box) return;
    box.addEventListener('input', updatePolicyCharCount);
    loadInvestmentPolicy();
});

async function generateEmbedToken() {
    if (!confirm('Generate a new embed token? The current token will stop working immediately.')) return;
    setStatus('ua-embedtoken-msg', 'info', 'Generating…');
    const res = await stepUpFetch('/api/generate-embed-token', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
        showGeneratedSecret('ua_embed_token', data.embed_token);
        setStatus('ua-embedtoken-msg', 'success', 'New embed token generated and saved. Copy it now — it will not be shown again.');
    } else {
        setStatus('ua-embedtoken-msg', 'error', data.detail || 'Failed to generate token.');
    }
}

async function changePassword() {
    const current = document.getElementById('ua_current_password').value;
    const newPw   = document.getElementById('ua_new_password').value;
    const confirm = document.getElementById('ua_confirm_password').value;
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
    if (!secretIsSet('API_TOKEN')) {
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

function toggleGhostfolioFields() {
    const enabled = document.getElementById('GHOSTFOLIO_ENABLED').checked;
    document.getElementById('ghostfolio-fields-group').classList.toggle('d-none', !enabled);
}

async function saveSmtpSettings() {
    const btn = document.querySelector('button[onclick="saveSmtpSettings()"]');
    btn.disabled = true;
    btn.innerText = '⏳ Saving…';
    setStatus('smtp-status-msg', 'info', 'Saving…');
    try {
        const res = await stepUpFetch('/api/save-smtp-settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(smtpPayload()),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) markSecretSaved('SMTP_PASS');
        setStatus('smtp-status-msg', res.ok ? 'success' : 'error', res.ok ? data.message : (data.detail || 'Failed to save.'));
    } catch (e) {
        setStatus('smtp-status-msg', 'error', 'Network error while saving.');
    } finally {
        btn.disabled = false;
        btn.innerText = '💾 Save Mail Settings';
    }
}

async function sendTestEmail() {
    const btn = document.querySelector('button[onclick="sendTestEmail()"]');
    btn.disabled = true;
    btn.innerText = '⏳ Sending…';
    setStatus('smtp-status-msg', 'info', 'Sending test email…');
    try {
        const res = await stepUpFetch('/api/send-test-email', {
            method: 'POST',
        });
        const data = await res.json().catch(() => ({}));
        setStatus('smtp-status-msg', res.ok ? 'success' : 'error', res.ok ? data.message : (data.detail || 'Send failed.'));
    } catch (e) {
        setStatus('smtp-status-msg', 'error', 'Network error while sending.');
    } finally {
        btn.disabled = false;
        btn.innerText = '🧪 Send Test Email';
    }
}

async function saveGhostfolioSettings() {
    const btn = document.querySelector('button[onclick="saveGhostfolioSettings()"]');
    btn.disabled = true;
    btn.innerText = '⏳ Saving…';
    setStatus('ghostfolio-creds-msg', 'info', 'Saving…');
    try {
        const res = await stepUpFetch('/api/save-ghostfolio-settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                GHOSTFOLIO_URL: document.getElementById('GHOSTFOLIO_URL').value.trim(),
                GHOSTFOLIO_TOKEN: secretValue('API_TOKEN').trim(),
                CLEAR_GHOSTFOLIO_TOKEN: secretClear('API_TOKEN'),
            }),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) markSecretSaved('API_TOKEN');
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
        APP_PASSWORD: secretValue('APP_PASSWORD'),
        CLEAR_APP_PASSWORD: secretClear('APP_PASSWORD'),
        CONVERSATION_TOKEN: secretValue('CONVERSATION_TOKEN').trim(),
        CLEAR_CONVERSATION_TOKEN: secretClear('CONVERSATION_TOKEN'),
    };
    try {
        const res = await stepUpFetch('/api/save-nextcloud-settings', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (res.ok) {
            markSecretSaved('APP_PASSWORD');
            markSecretSaved('CONVERSATION_TOKEN');
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
        const res = await stepUpFetch('/api/test-nextcloud-message', { method: 'POST' });
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
        const res = await stepUpFetch('/api/save-account-email', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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
        const res = await stepUpFetch('/api/change-username', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
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
        const res = await stepUpFetch('/api/rotate-app-secret', {
            method: 'POST',
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

async function logoutEverywhere() {
    if (!confirm('This signs out every browser and device, including this one. Continue?')) return;
    const btn = document.querySelector('button[onclick="logoutEverywhere()"]');
    btn.disabled = true;
    setStatus('ua-logout-all-msg', 'info', 'Signing out…');
    try {
        const res = await stepUpFetch('/api/logout-everywhere', { method: 'POST' });
        if (res.ok) {
            window.location.href = '/login';
            return;
        }
        const data = await res.json().catch(() => ({}));
        setStatus('ua-logout-all-msg', 'error', data.detail || 'Failed to sign out everywhere.');
    } catch (e) {
        setStatus('ua-logout-all-msg', 'error', 'Network error while signing out.');
    }
    btn.disabled = false;
}
