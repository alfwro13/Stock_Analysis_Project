function _autotopupModal() {
    return bootstrap.Modal.getOrCreateInstance(document.getElementById('autotopupModal'));
}

function _updateAutoTopupFrequencyFields() {
    const isWeekly = document.getElementById('autotopup-frequency').value === 'weekly';
    document.getElementById('autotopup-day-of-month-group').classList.toggle('d-none', isWeekly);
    document.getElementById('autotopup-day-of-week-group').classList.toggle('d-none', !isWeekly);
}

function openAutoTopupModal(accountId, accountObj) {
    const acc = accountObj || (typeof _accountsCache !== 'undefined' ? _accountsCache[accountId] : null);
    document.getElementById('autotopup-account-id').value = accountId;
    document.getElementById('autotopup-enabled').checked = !!(acc && acc.autotopup_enabled);
    document.getElementById('autotopup-amount').value = acc && acc.autotopup_amount != null ? acc.autotopup_amount : '';
    document.getElementById('autotopup-frequency').value = (acc && acc.autotopup_frequency) || 'monthly';
    document.getElementById('autotopup-day-of-month').value = acc && acc.autotopup_day_of_month != null ? acc.autotopup_day_of_month : 1;
    document.getElementById('autotopup-day-of-week').value = acc && acc.autotopup_day_of_week != null ? acc.autotopup_day_of_week : 1;
    document.getElementById('autotopup-notes').value = (acc && acc.autotopup_notes) || '';
    document.getElementById('autotopup-config-status').innerHTML = '';
    _updateAutoTopupFrequencyFields();
    _autotopupModal().show();
}

async function saveAutoTopupConfig() {
    const status = document.getElementById('autotopup-config-status');
    const enabled = document.getElementById('autotopup-enabled').checked;
    const frequency = document.getElementById('autotopup-frequency').value;
    const accountId = document.getElementById('autotopup-account-id').value;
    status.innerHTML = '<span class="msg-info">Saving...</span>';
    try {
        const r = await fetch(`/api/accounts/${accountId}/autotopup-config`, {
            method: 'PUT',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                enabled,
                amount: parseFloat(document.getElementById('autotopup-amount').value) || null,
                frequency,
                day_of_month: frequency === 'monthly' ? parseInt(document.getElementById('autotopup-day-of-month').value, 10) : null,
                day_of_week: frequency === 'weekly' ? parseInt(document.getElementById('autotopup-day-of-week').value, 10) : null,
                notes: document.getElementById('autotopup-notes').value.trim() || null,
            }),
        });
        const data = await r.json();
        if (data.status === 'success') {
            status.innerHTML = '<span class="msg-success">Saved.</span>';
            if (typeof loadAccounts === 'function') loadAccounts();
        } else {
            status.innerHTML = `<span class="msg-error">${data.message || 'Failed to save.'}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${e.message}</span>`;
    }
}
