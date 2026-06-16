let lastNotificationId = window.LAST_NOTIFICATION_ID || 0;
let activeFilter = 'all';

function setFilter(btn) {
    activeFilter = btn.dataset.filter;
    document.querySelectorAll('.filter-btn').forEach(b => b.classList.remove('active'));
    btn.classList.add('active');
    document.querySelectorAll('.notification-list .notification-card').forEach(card => {
        const type = card.dataset.type || '';
        card.style.display = (activeFilter === 'all' || type === activeFilter) ? '' : 'none';
    });
}

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
                    if (activeFilter !== 'all' && note.type !== activeFilter) {
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
