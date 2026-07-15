function _deleteAccountModal() {
    return bootstrap.Modal.getOrCreateInstance(document.getElementById('deleteAccountModal'));
}

function openDeleteAccountModal() {
    document.getElementById('delete-account-confirm-checkbox').checked = false;
    document.getElementById('delete-account-confirm-btn').disabled = true;
    document.getElementById('delete-account-status').innerHTML = '';
    _deleteAccountModal().show();
}

function _onDeleteAccountCheckboxChange() {
    document.getElementById('delete-account-confirm-btn').disabled =
        !document.getElementById('delete-account-confirm-checkbox').checked;
}

async function confirmDeleteAccount() {
    const status = document.getElementById('delete-account-status');
    const accountId = window.CURRENT_ACCOUNT.id;
    status.innerHTML = '<span class="msg-info">Deleting...</span>';
    try {
        const r = await fetch(`/api/accounts/${accountId}`, { method: 'DELETE' });
        const data = await r.json();
        if (data.status === 'success') {
            window.location.href = '/accounts';
        } else {
            status.innerHTML = `<span class="msg-error">${escapeHtml(data.message || 'Failed to delete account.')}</span>`;
        }
    } catch (e) {
        status.innerHTML = `<span class="msg-error">${escapeHtml(e.message)}</span>`;
    }
}
