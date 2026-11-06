const _REMINDER_TYPE_LABELS = { pay_in: 'Pay In', admin_fee: 'Admin Fee' };
let _remindersCache = [];

function _remindersModal() {
    return bootstrap.Modal.getOrCreateInstance(document.getElementById('remindersModal'));
}

function _remindersAccountId() {
    return document.getElementById('reminders-account-id').value;
}

function showReminderList() {
    document.getElementById('reminders-list-view').classList.remove('d-none');
    document.getElementById('reminders-form-view').classList.add('d-none');
    document.getElementById('reminders-status').innerHTML = '';
}

function _renderReminders() {
    const list = document.getElementById('reminders-list');
    if (!_remindersCache.length) {
        list.innerHTML = '<p class="text-muted small mb-0">No reminders yet.</p>';
        return;
    }
    list.innerHTML = `<table class="table table-hover w-100">
        <thead><tr><th>Type</th><th>Schedule</th><th>Status</th><th></th></tr></thead>
        <tbody>${_remindersCache.map(r => `<tr>
            <td>${escapeHtml(_REMINDER_TYPE_LABELS[r.reminder_type] || r.reminder_type)}</td>
            <td>${escapeHtml(r.cadence === 'monthly' ? 'Monthly' : r.cadence)}, day ${Number(r.day_of_month)}</td>
            <td>${r.enabled ? 'Enabled' : 'Disabled'}</td>
            <td class="text-end text-nowrap">
                <button type="button" class="btn btn-outline-secondary btn-sm" onclick="showReminderForm(${Number(r.id)})">Edit</button>
                <button type="button" class="btn btn-outline-danger btn-sm" onclick="deleteReminder(${Number(r.id)})">Delete</button>
            </td>
        </tr>`).join('')}</tbody></table>`;
}

async function _loadReminders() {
    const status = document.getElementById('reminders-status');
    try {
        const r = await fetch(`/api/accounts/${_remindersAccountId()}/reminders`);
        const data = await r.json();
        if (data.status !== 'success') {
            status.innerHTML = `<span class="msg-error">${escapeHtml(data.message || 'Failed to load reminders.')}</span>`;
            return;
        }
        _remindersCache = data.reminders;
        _renderReminders();
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${escapeHtml(e.message)}</span>`;
    }
}

function openRemindersModal(accountId) {
    document.getElementById('reminders-account-id').value = accountId;
    _remindersCache = [];
    _renderReminders();
    showReminderList();
    _loadReminders();
    _remindersModal().show();
}

function showReminderForm(reminderId) {
    const reminder = reminderId === null ? null : _remindersCache.find(r => r.id === reminderId);
    document.getElementById('reminders-edit-id').value = reminder ? reminder.id : '';
    document.getElementById('reminder-type').value = reminder ? reminder.reminder_type : 'pay_in';
    document.getElementById('reminder-cadence').value = reminder ? reminder.cadence : 'monthly';
    document.getElementById('reminder-day').value = reminder ? reminder.day_of_month : 1;
    document.getElementById('reminder-enabled').checked = reminder ? !!reminder.enabled : false;
    document.getElementById('reminders-status').innerHTML = '';
    document.getElementById('reminders-list-view').classList.add('d-none');
    document.getElementById('reminders-form-view').classList.remove('d-none');
}

async function saveReminder() {
    const status = document.getElementById('reminders-status');
    const editId = document.getElementById('reminders-edit-id').value;
    const url = `/api/accounts/${_remindersAccountId()}/reminders${editId ? `/${editId}` : ''}`;
    status.innerHTML = '<span class="msg-info">Saving...</span>';
    try {
        const r = await fetch(url, {
            method: editId ? 'PUT' : 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                reminder_type: document.getElementById('reminder-type').value,
                cadence: document.getElementById('reminder-cadence').value,
                day_of_month: parseInt(document.getElementById('reminder-day').value, 10),
                enabled: document.getElementById('reminder-enabled').checked,
            }),
        });
        const data = await r.json();
        if (data.status === 'success') {
            showReminderList();
            _loadReminders();
        } else {
            status.innerHTML = `<span class="msg-error">${escapeHtml(data.message || 'Failed to save.')}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${escapeHtml(e.message)}</span>`;
    }
}

async function deleteReminder(reminderId) {
    if (!confirm('Delete this reminder? Tasks it already announced stay pending on the account page.')) return;
    const status = document.getElementById('reminders-status');
    try {
        const r = await fetch(`/api/accounts/${_remindersAccountId()}/reminders/${reminderId}`, { method: 'DELETE' });
        const data = await r.json();
        if (data.status === 'success') {
            status.innerHTML = '';
            _loadReminders();
        } else {
            status.innerHTML = `<span class="msg-error">${escapeHtml(data.message || 'Failed to delete.')}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${escapeHtml(e.message)}</span>`;
    }
}
