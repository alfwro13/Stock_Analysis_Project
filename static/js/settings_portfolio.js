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
