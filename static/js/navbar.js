// Global notification poller + data-freshness badge for the Bootstrap navbar in base.html.
// Runs on every non-embed page so background alerts are never missed.
let globalLastNotificationId = null;

function initGlobalBrowserNotifications() {
    if (!("Notification" in window)) return;
    if (Notification.permission !== "granted" && Notification.permission !== "denied") {
        Notification.requestPermission();
    }
}

async function pollGlobalSystemNotifications() {
    if (!("Notification" in window) || Notification.permission !== "granted") return;

    try {
        const response = await fetch(`/api/notifications/latest?last_id=${globalLastNotificationId || 0}`);
        const result = await response.json();

        if (response.ok && result.status === 'success') {
            const notes = result.notifications;
            if (notes.length > 0) {
                if (globalLastNotificationId === null) {
                    globalLastNotificationId = notes[notes.length - 1].id;
                } else {
                    if (notes.length > 5) {
                        const n = new Notification('🚨 Quantamental: Multiple Alerts', {
                            body: `You have ${notes.length} new notifications. Visit the Notifications page to review them.`,
                            icon: '/assets/logo_small.png'
                        });
                        n.onclick = function () { window.open("/notifications", "_blank"); n.close(); };
                    } else {
                        notes.forEach(note => {
                            const n = new Notification(`🚨 Quantamental: ${note.type} Alert`, {
                                body: note.text.substring(0, 120) + (note.text.length > 120 ? '...' : ''),
                                icon: '/assets/logo_small.png'
                            });
                            n.onclick = function () { window.open("/notifications", "_blank"); n.close(); };
                        });
                    }
                    notes.forEach(note => {
                        globalLastNotificationId = Math.max(globalLastNotificationId, note.id);
                    });

                    const badge = document.getElementById('nav-badge');
                    if (badge) {
                        badge.innerText = parseInt(badge.innerText || '0') + notes.length;
                    }
                }
            } else if (globalLastNotificationId === null) {
                globalLastNotificationId = 0;
            }
        }
    } catch (error) {
        console.error("Failed to poll system notifications globally:", error);
    }
}

async function loadFreshnessBadge() {
    const slot = document.getElementById('freshness-badge-slot');
    if (!slot) return;
    try {
        const resp = await fetch('/api/freshness');
        if (!resp.ok) { slot.style.display = 'none'; return; }
        const d = await resp.json();
        const modelLabel = d.model_date
            ? `Model: ${d.model_date} (${d.model_days_ago}d ago)`
            : 'Model: not trained';
        const pricesLabel = d.prices_date ? `Prices: ${d.prices_date}` : 'Prices: no data';
        slot.innerHTML =
            `<span class="${d.model_state}">${modelLabel}</span>` +
            `<span class="${d.prices_state}">${pricesLabel}</span>`;
    } catch (e) { /* fail silently — badge is non-critical */ }
}

document.addEventListener('DOMContentLoaded', () => {
    initGlobalBrowserNotifications();
    loadFreshnessBadge();
    setInterval(pollGlobalSystemNotifications, 15000);
    pollGlobalSystemNotifications();
});
