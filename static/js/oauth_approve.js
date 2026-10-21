(function () {
    const heading = document.getElementById('approve-heading');
    const message = document.getElementById('approve-message');
    const hint = document.getElementById('approve-hint');
    const redirectPrefix = document.body.dataset.redirectPrefix + '?';
    const TERMINAL = {
        denied: ['Request denied', 'The request was denied.'],
        expired: ['Request expired', 'The request expired. Start the connection again from Claude.'],
        done: ['Request completed', 'This request was already completed. You can close this page.'],
        unknown: ['No request found', 'Start the connection again from Claude.'],
    };

    function show(title, text, code) {
        heading.textContent = title;
        message.textContent = text;
        if (code) {
            const badge = document.createElement('strong');
            badge.className = 'approve-code';
            badge.textContent = code;
            message.append(' ', badge);
        }
        hint.hidden = !code;
    }

    async function poll() {
        let data;
        try {
            const res = await fetch('/oauth/approve-status', { cache: 'no-store', credentials: 'same-origin' });
            if (!res.ok) {
                show('Unavailable', 'Remote access is not available right now.');
                return;
            }
            data = await res.json();
        } catch (e) {
            setTimeout(poll, 3000);
            return;
        }
        if (data.state === 'pending' && typeof data.code === 'string') {
            show('Waiting for approval', 'Your code is', data.code);
            setTimeout(poll, 2000);
        } else if (data.state === 'approved' && typeof data.redirect === 'string' && data.redirect.startsWith(redirectPrefix)) {
            show('Approved', 'Returning to Claude…');
            window.location.assign(data.redirect);
        } else {
            const [title, text] = TERMINAL[data.state] || TERMINAL.unknown;
            show(title, text);
        }
    }

    poll();
})();
