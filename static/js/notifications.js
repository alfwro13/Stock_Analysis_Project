let lastNotificationId = window.LAST_NOTIFICATION_ID || 0;

const TYPES_STORAGE_KEY = 'notif_active_types';
const UNREAD_STORAGE_KEY = 'notif_unread_only';

function loadStoredTypes() {
    try {
        const raw = localStorage.getItem(TYPES_STORAGE_KEY);
        const arr = raw ? JSON.parse(raw) : null;
        return Array.isArray(arr) ? arr : null;
    } catch (_) {
        return null;
    }
}

function saveActiveTypes() {
    try {
        localStorage.setItem(TYPES_STORAGE_KEY, JSON.stringify(Array.from(activeTypes)));
    } catch (_) {}
}

function saveUnreadOnly() {
    try {
        localStorage.setItem(UNREAD_STORAGE_KEY, String(unreadOnly));
    } catch (_) {}
}

const storedTypes = loadStoredTypes();
let activeTypes = new Set(storedTypes || []);
let unreadOnly = localStorage.getItem(UNREAD_STORAGE_KEY) === 'true';

function updateFilterButtonStates() {
    document.querySelectorAll('.filter-btn[data-filter]').forEach(b => {
        const isActive = b.dataset.filter === 'all' ? activeTypes.size === 0 : activeTypes.has(b.dataset.filter);
        b.classList.toggle('active', isActive);
    });
    const unreadBtn = document.getElementById('unread-filter-btn');
    if (unreadBtn) unreadBtn.classList.toggle('active', unreadOnly);
}

function applyFilters() {
    document.querySelectorAll('.notification-list .notification-card[data-type]').forEach(card => {
        const type = card.dataset.type || '';
        const typeMatch = activeTypes.size === 0 || activeTypes.has(type);
        const unreadMatch = !unreadOnly || card.classList.contains('unread');
        card.style.display = (typeMatch && unreadMatch) ? '' : 'none';
    });
}

function toggleTypeFilter(btn) {
    const filter = btn.dataset.filter;
    if (filter === 'all') {
        activeTypes.clear();
    } else if (activeTypes.has(filter)) {
        activeTypes.delete(filter);
    } else {
        activeTypes.add(filter);
    }
    saveActiveTypes();
    updateFilterButtonStates();
    applyFilters();
}

function toggleUnreadFilter() {
    unreadOnly = !unreadOnly;
    saveUnreadOnly();
    updateFilterButtonStates();
    applyFilters();
}

updateFilterButtonStates();
applyFilters();

setInterval(async () => {
    try {
        const response = await fetch(`/api/notifications/latest?last_id=${lastNotificationId}`);
        const result = await response.json();

        if (response.ok && result.status === 'success') {
            const notes = result.notifications;
            if (notes.length > 0) {
                const list = document.querySelector('.notification-list');

                const emptyMsg = document.getElementById('empty-state-msg');
                if (emptyMsg) emptyMsg.remove();

                notes.forEach(note => {
                    const card = document.createElement('div');
                    card.className = 'notification-card unread';
                    card.dataset.type = note.type;
                    if (!(activeTypes.size === 0 || activeTypes.has(note.type))) {
                        card.style.display = 'none';
                    }
                    card.innerHTML = `
                        <div class="notification-header">
                            <span class="notification-type">${note.type} Alert</span>
                            <span>${note.timestamp}</span>
                        </div>
                        <div class="notification-body">${note.text}</div>
                    `;
                    list.prepend(card);
                    lastNotificationId = Math.max(lastNotificationId, note.id);
                });

                const badge = document.getElementById('nav-badge');
                if (badge) {
                    badge.innerText = parseInt(badge.innerText || '0') + notes.length;
                    badge.classList.remove('d-none');
                    badge.classList.add('d-inline-block');
                }
            }
        }
    } catch (error) {
        console.error("Failed to poll notifications:", error);
    }
}, 15000);

async function markAllAsRead() {
    const btn = document.getElementById('mark-read-btn');
    btn.disabled = true;
    btn.innerText = "Processing...";

    try {
        const response = await fetch('/api/notifications/mark-read', { method: 'POST' });

        if (response.ok) {
            document.querySelectorAll('.notification-card.unread').forEach(card => card.classList.remove('unread'));
            applyFilters();

            const badge = document.getElementById('nav-badge');
            if (badge) {
                badge.classList.remove('d-inline-block');
                badge.classList.add('d-none');
            }

            btn.innerText = "✓ All Caught Up";
        } else {
            alert("Failed to mark notifications as read.");
            btn.innerText = "✓ Mark All as Read";
            btn.disabled = false;
        }
    } catch (error) {
        alert("Network error occurred.");
        btn.innerText = "✓ Mark All as Read";
        btn.disabled = false;
    }
}

async function purgeAllNotifications() {
    if (!confirm("Are you sure you want to permanently delete all notifications? This action cannot be undone.")) {
        return;
    }

    const btn = document.getElementById('purge-btn');
    const originalText = btn.innerText;
    btn.disabled = true;
    btn.innerText = "Processing...";

    try {
        const response = await fetch('/api/notifications/purge', { method: 'POST' });

        if (response.ok) {
            document.querySelector('.notification-list').innerHTML = `
                <div class="notification-card" id="empty-state-msg">
                    <div class="notification-body text-center text-muted-dark">
                        No notifications generated yet. Wait for a background scan to complete.
                    </div>
                </div>
            `;

            const badge = document.getElementById('nav-badge');
            if (badge) {
                badge.classList.remove('d-inline-block');
                badge.classList.add('d-none');
            }

            btn.innerText = "✓ Purged";
        } else {
            alert("Failed to purge notifications.");
            btn.innerText = originalText;
            btn.disabled = false;
        }
    } catch (error) {
        alert("Network error occurred.");
        btn.innerText = originalText;
        btn.disabled = false;
    }
}
