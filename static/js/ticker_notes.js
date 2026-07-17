function tnFormatTimestamp(utcStr) {
    if (!utcStr) return '';
    return new Date(utcStr.replace(' ', 'T') + 'Z').toLocaleString();
}

function tnRenderNoteEntry(note) {
    const updated = note.updated_at
        ? '<span class="tn-note-updated">(edited ' + escapeHtml(tnFormatTimestamp(note.updated_at)) + ')</span>'
        : '';
    return (
        '<div class="ticker-note-entry" data-note-id="' + note.id + '">' +
            '<div class="ticker-note-meta">' +
                '<span class="ticker-note-date">' + escapeHtml(tnFormatTimestamp(note.created_at)) + '</span> ' + updated +
                '<span class="ticker-note-actions">' +
                    '<a href="javascript:void(0)" class="tn-edit-link" data-note-id="' + note.id + '">Edit</a>' +
                    '<a href="javascript:void(0)" class="tn-delete-link" data-note-id="' + note.id + '">Delete</a>' +
                '</span>' +
            '</div>' +
            '<div class="ticker-note-body" id="tn-note-body-' + note.id + '">' + escapeHtml(note.note_text) + '</div>' +
        '</div>'
    );
}

function tnRenderTable(entries) {
    const tbody = document.getElementById('tn-tbody');
    document.getElementById('tn-count').textContent = entries.length ? '(' + entries.length + ')' : '';
    if (!entries.length) {
        tbody.innerHTML = '<tr><td colspan="4" class="text-center p-4 text-muted">No ticker notes yet. Add one from a Stock Detail page.</td></tr>';
        return;
    }
    let html = '';
    entries.forEach((entry, idx) => {
        const latest = entry.notes[0];
        const rowId = 'tn-row-' + idx;
        html +=
            '<tr class="tn-ticker-row" data-ticker="' + escapeHtml(entry.ticker) + '" data-row-id="' + rowId + '">' +
                '<td><a href="/stock/' + encodeURIComponent(entry.ticker) + '" class="tn-stock-link">' + escapeHtml(entry.ticker) + '</a></td>' +
                '<td class="text-muted">' + escapeHtml(entry.company_name || '') + '</td>' +
                '<td class="tm-th-center">' + entry.notes.length + '</td>' +
                '<td class="tm-th-right text-muted">' + escapeHtml(tnFormatTimestamp(latest.created_at)) + '</td>' +
            '</tr>' +
            '<tr class="tn-detail-row d-none" id="tn-detail-' + rowId + '" data-ticker="' + escapeHtml(entry.ticker) + '">' +
                '<td colspan="4">' +
                    '<div class="ticker-notes-list">' +
                        entry.notes.map(n => tnRenderNoteEntry(n)).join('') +
                    '</div>' +
                '</td>' +
            '</tr>';
    });
    tbody.innerHTML = html;
}

function tnLoad() {
    fetch('/api/ticker-notes')
        .then(r => r.json())
        .then(data => tnRenderTable(data.tickers || []))
        .catch(() => {
            document.getElementById('tn-tbody').innerHTML =
                '<tr><td colspan="4" class="text-center p-4 text-muted">Failed to load ticker notes.</td></tr>';
        });
}

function tnEditNote(noteId) {
    const bodyEl = document.getElementById('tn-note-body-' + noteId);
    const currentText = bodyEl.dataset.raw !== undefined ? bodyEl.dataset.raw : bodyEl.textContent;
    bodyEl.dataset.raw = currentText;
    bodyEl.innerHTML = '';
    const textarea = document.createElement('textarea');
    textarea.className = 'form-control mb-2';
    textarea.maxLength = 1000;
    textarea.id = 'tn-edit-textarea-' + noteId;
    textarea.value = currentText;
    const saveBtn = document.createElement('button');
    saveBtn.type = 'button';
    saveBtn.className = 'btn btn-sm btn-primary';
    saveBtn.textContent = 'Save';
    saveBtn.dataset.noteId = noteId;
    saveBtn.classList.add('tn-save-edit-btn');
    const cancelBtn = document.createElement('button');
    cancelBtn.type = 'button';
    cancelBtn.className = 'btn btn-sm btn-secondary';
    cancelBtn.textContent = 'Cancel';
    cancelBtn.addEventListener('click', tnLoad);
    bodyEl.appendChild(textarea);
    bodyEl.appendChild(saveBtn);
    bodyEl.appendChild(document.createTextNode(' '));
    bodyEl.appendChild(cancelBtn);
}

function tnSaveEdit(ticker, noteId) {
    const noteText = document.getElementById('tn-edit-textarea-' + noteId).value.trim();
    if (!noteText) return;
    fetch('/api/ticker/' + encodeURIComponent(ticker) + '/notes/' + noteId, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ note_text: noteText }),
    })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success') tnLoad();
        });
}

function tnDeleteNote(ticker, noteId) {
    if (!confirm('Delete this note? This cannot be undone.')) return;
    fetch('/api/ticker/' + encodeURIComponent(ticker) + '/notes/' + noteId, { method: 'DELETE' })
        .then(r => r.json())
        .then(data => {
            if (data.status === 'success') tnLoad();
        });
}

document.addEventListener('DOMContentLoaded', function () {
    tnLoad();

    document.getElementById('tn-tbody').addEventListener('click', function (e) {
        const stockLink = e.target.closest('.tn-stock-link');
        if (stockLink) return;

        const editLink = e.target.closest('.tn-edit-link');
        if (editLink) {
            tnEditNote(editLink.dataset.noteId);
            return;
        }

        const deleteLink = e.target.closest('.tn-delete-link');
        if (deleteLink) {
            const ticker = e.target.closest('.tn-detail-row').dataset.ticker;
            tnDeleteNote(ticker, deleteLink.dataset.noteId);
            return;
        }

        const saveEditBtn = e.target.closest('.tn-save-edit-btn');
        if (saveEditBtn) {
            const ticker = e.target.closest('.tn-detail-row').dataset.ticker;
            tnSaveEdit(ticker, saveEditBtn.dataset.noteId);
            return;
        }

        const row = e.target.closest('.tn-ticker-row');
        if (row) {
            document.getElementById('tn-detail-' + row.dataset.rowId).classList.toggle('d-none');
        }
    });
});
