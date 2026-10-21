const MCP_REMOTE_POLL_MS = 3000;
let mcpRemoteTimer = null;
let mcpRemoteHasClient = false;

function mcpRemoteCell(text) {
    const td = document.createElement('td');
    td.textContent = text == null || text === '' ? '—' : String(text);
    return td;
}

function mcpRemoteMessageRow(body, text, cssClass) {
    const td = document.createElement('td');
    td.colSpan = 5;
    td.className = cssClass;
    td.textContent = text;
    const tr = document.createElement('tr');
    tr.append(td);
    body.replaceChildren(tr);
}

function mcpRemoteErrorText(data, fallback) {
    if (typeof data.detail === 'string') return data.detail;
    if (Array.isArray(data.detail)) return data.detail.map(d => d.msg).join('; ');
    return data.message || fallback;
}

function renderMcpRemoteRequests(requests, pairingOpen) {
    const body = document.getElementById('mcp-remote-requests-body');
    if (!requests.length) {
        mcpRemoteMessageRow(body, pairingOpen ? 'No connection requests yet.' : 'No connection requests. Open a pairing window first.', 'text-muted');
        return;
    }
    body.replaceChildren(...requests.map(r => {
        const tr = document.createElement('tr');
        const code = mcpRemoteCell(r.code);
        code.className = 'font-monospace';
        const agent = mcpRemoteCell(r.user_agent);
        agent.className = 'text-break';
        const actions = document.createElement('td');
        const approve = document.createElement('button');
        approve.type = 'button';
        approve.className = 'btn btn-primary mt-0';
        approve.dataset.action = 'approve';
        approve.dataset.requestId = r.id;
        approve.textContent = '✅ Approve';
        const deny = document.createElement('button');
        deny.type = 'button';
        deny.className = 'btn btn-danger mt-0';
        deny.dataset.action = 'deny';
        deny.dataset.requestId = r.id;
        deny.textContent = '🚫 Deny';
        actions.append(approve, ' ', deny);
        tr.append(code, mcpRemoteCell(r.requested_display), mcpRemoteCell(r.address), agent, actions);
        return tr;
    }));
}

function renderMcpRemoteApps(apps) {
    const body = document.getElementById('mcp-remote-apps-body');
    if (!apps.length) {
        mcpRemoteMessageRow(body, 'No connected apps.', 'text-muted');
        return;
    }
    body.replaceChildren(...apps.map(a => {
        const tr = document.createElement('tr');
        tr.append(mcpRemoteCell(a.client_id), mcpRemoteCell(a.connected_display), mcpRemoteCell(a.expires_display),
            mcpRemoteCell(a.last_used_display || 'Never'), mcpRemoteCell(a.last_address));
        return tr;
    }));
}

function renderMcpRemote(data) {
    document.getElementById('MCP_REMOTE_ENABLED').checked = data.enabled;
    const hostInput = document.getElementById('mcp_public_host');
    if (document.activeElement !== hostInput && hostInput.dataset.edited !== '1') hostInput.value = data.public_host;
    const endpoint = document.getElementById('mcp_remote_endpoint');
    endpoint.textContent = data.endpoint_url || 'Save a public host first.';
    if (data.public_host && !data.host_active) {
        setStatus('mcp-remote-host-msg', 'warning', 'The saved host is not active yet. Restart the service to apply it.');
    }
    mcpRemoteHasClient = !!data.client;
    document.getElementById('mcp_remote_client_id').textContent = data.client
        ? 'Client ID ' + data.client.client_id + ' (created ' + data.client.created_display + ')'
        : 'No OAuth client yet.';
    document.getElementById('mcp-remote-client-btn').textContent = data.client ? '🔄 Rotate OAuth Client Secret' : '🔑 Create OAuth Client';
    const state = document.getElementById('mcp_remote_pairing_state');
    const minutes = Math.ceil(data.pairing.seconds_left / 60);
    state.textContent = data.pairing.open
        ? 'Pairing window open until ' + data.pairing.until_display + ' (' + minutes + ' min left).'
        : 'Pairing window closed.';
    state.className = data.pairing.open ? 'text-warning' : 'text-muted';
    renderMcpRemoteRequests(data.requests, data.pairing.open);
    renderMcpRemoteApps(data.apps);
}

async function loadMcpRemote() {
    try {
        const res = await fetch('/api/mcp-remote');
        if (res.ok) renderMcpRemote(await res.json());
    } catch (e) {
        // the next poll retries
    }
}

function stopMcpRemotePolling() {
    if (mcpRemoteTimer) clearInterval(mcpRemoteTimer);
    mcpRemoteTimer = null;
}

function startMcpRemotePolling() {
    loadMcpRemote();
    if (mcpRemoteTimer) return;
    mcpRemoteTimer = setInterval(() => { if (!document.hidden) loadMcpRemote(); }, MCP_REMOTE_POLL_MS);
}

async function toggleMcpRemote(box) {
    const enabled = box.checked;
    setStatus('mcp-remote-enabled-msg', 'info', 'Saving…');
    const options = { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ enabled }) };
    const res = enabled ? await stepUpFetch('/api/mcp-remote/enabled', options) : await fetch('/api/mcp-remote/enabled', options);
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
        setStatus('mcp-remote-enabled-msg', 'success', enabled ? 'Remote Access enabled.' : 'Remote Access disabled. The public host answers 404.');
    } else {
        box.checked = !enabled;
        setStatus('mcp-remote-enabled-msg', 'error', mcpRemoteErrorText(data, 'Failed to save.'));
    }
}

async function saveMcpPublicHost() {
    const input = document.getElementById('mcp_public_host');
    setStatus('mcp-remote-host-msg', 'info', 'Saving…');
    const res = await stepUpFetch('/api/settings', {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ MCP_PUBLIC_HOST: input.value.trim() }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
        input.dataset.edited = '';
        setStatus('mcp-remote-host-msg', 'success', 'Saved. Restart the service for the new host to take effect.');
        loadMcpRemote();
    } else {
        setStatus('mcp-remote-host-msg', 'error', mcpRemoteErrorText(data, 'Failed to save.'));
    }
}

async function createMcpRemoteClient() {
    if (mcpRemoteHasClient && !confirm('Rotate the client secret? Every connected app is signed out and must be added to Claude again.')) return;
    setStatus('mcp-remote-client-msg', 'info', 'Creating…');
    const res = await stepUpFetch('/api/mcp-remote/client', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
        document.getElementById('mcp_remote_new_client_id').value = data.client_id;
        document.getElementById('mcp_remote_new_secret').value = data.client_secret;
        document.getElementById('mcp_remote_client_once').classList.remove('d-none');
        setStatus('mcp-remote-client-msg', 'success', 'OAuth client created. Copy the secret now — it will not be shown again.');
        loadMcpRemote();
    } else {
        setStatus('mcp-remote-client-msg', 'error', mcpRemoteErrorText(data, 'Failed to create the OAuth client.'));
    }
}

async function openMcpPairingWindow() {
    setStatus('mcp-remote-pairing-msg', 'info', 'Opening…');
    const res = await stepUpFetch('/api/mcp-remote/pairing', { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
        setStatus('mcp-remote-pairing-msg', 'success', 'Pairing window open. Start the connection from Claude now.');
        loadMcpRemote();
    } else {
        setStatus('mcp-remote-pairing-msg', 'error', mcpRemoteErrorText(data, 'Failed to open the pairing window.'));
    }
}

async function decideMcpRemoteRequest(action, requestId) {
    setStatus('mcp-remote-request-msg', 'info', action === 'approve' ? 'Approving…' : 'Denying…');
    const url = '/api/mcp-remote/requests/' + encodeURIComponent(requestId) + '/' + action;
    const res = action === 'approve' ? await stepUpFetch(url, { method: 'POST' }) : await fetch(url, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
        setStatus('mcp-remote-request-msg', 'success', action === 'approve' ? 'Request approved.' : 'Request denied.');
    } else {
        setStatus('mcp-remote-request-msg', 'error', mcpRemoteErrorText(data, 'Failed to save the decision.'));
    }
    loadMcpRemote();
}

document.addEventListener('DOMContentLoaded', () => {
    const card = document.getElementById('mcp-server-card');
    if (!card) return;
    document.getElementById('mcp_public_host').addEventListener('input', e => { e.target.dataset.edited = '1'; });
    document.getElementById('mcp-remote-requests-body').addEventListener('click', e => {
        const btn = e.target.closest('button[data-request-id]');
        if (btn) decideMcpRemoteRequest(btn.dataset.action, btn.dataset.requestId);
    });
    card.addEventListener('toggle', () => { if (card.open) startMcpRemotePolling(); else stopMcpRemotePolling(); });
    if (card.open) startMcpRemotePolling();
});
