// Automatically injects the CSRF token into every non-GET fetch call.
// starlette-csrf sets a readable "csrftoken" cookie on each response.
(function () {
    const _fetch = window.fetch;
    window.fetch = function (url, options) {
        options = options || {};
        const method = (options.method || 'GET').toUpperCase();
        if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
            const token = document.cookie.split(';')
                .map(c => c.trim())
                .find(c => c.startsWith('csrftoken='))
                ?.split('=')[1];
            if (token) {
                options.headers = Object.assign({}, options.headers || {}, { 'x-csrftoken': token });
            }
        }
        return _fetch.call(this, url, options);
    };
})();
