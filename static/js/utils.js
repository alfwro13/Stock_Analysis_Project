function escapeHtml(s) {
    return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
}

function escapeRegExp(s) {
    return String(s ?? '').replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
}

function exactTagSearchPattern(val) {
    return '(^|\\s)' + escapeRegExp(val) + '(\\s|$)';
}

function formatCurrency(value, currencyCode) {
    let num = value === null || value === undefined ? NaN : parseFloat(value);
    if (isNaN(num)) return '—';

    if (currencyCode === 'GBp') {
        num = num / 100.0;
        currencyCode = 'GBP';
    }
    if (!currencyCode) {
        return num.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
    }
    try {
        return new Intl.NumberFormat('en-US', {
            style: 'currency',
            currency: currencyCode,
            maximumFractionDigits: 2,
        }).format(num);
    } catch (e) {
        return currencyCode + ' ' + num.toFixed(2);
    }
}

function showTableError(tableSelector, colSpan) {
    $(tableSelector + ' tbody').html(
        '<tr><td colspan="' + colSpan + '" class="table-error-cell">⚠️ Failed to load data. Please refresh the page or try again.</td></tr>'
    );
}

function applyStickyTheadOffset() {
    var navbar = document.querySelector('.app-navbar');
    if (navbar) {
        document.documentElement.style.setProperty('--sticky-thead-top', navbar.getBoundingClientRect().height + 'px');
    }
}

function formatDuration(seconds) {
    if (seconds === null || seconds === undefined) return '—';
    const total = Math.max(0, Math.round(Number(seconds)));
    const hours = Math.floor(total / 3600);
    const minutes = Math.floor((total % 3600) / 60);
    const secs = total % 60;
    if (hours) return `${hours}h ${minutes}m ${secs}s`;
    if (minutes) return `${minutes}m ${secs}s`;
    return `${secs}s`;
}
