window.ChangePeriod = (function () {
    "use strict";

    function pctFromAnchor(livePrice, anchorClose) {
        var live = parseFloat(livePrice);
        var anchor = parseFloat(anchorClose);
        if (!isFinite(live) || !isFinite(anchor) || anchor === 0) return null;
        return (live - anchor) / anchor * 100;
    }
    window._pctFromAnchor = pctFromAnchor;

    // Shared with macro_cards.js so the live-poll tick and the button click never diverge
    // on how the Change cell is rendered.
    function applyChangeCell(rowEl, pct, isPositive, isStale) {
        var changeEl = document.getElementById('change-' + rowEl.dataset.ticker);
        if (!changeEl) return;
        if (isStale === undefined) isStale = changeEl.classList.contains('stale-text');
        var cell = changeEl.closest('td');
        if (pct === null || pct === undefined || !isFinite(pct)) {
            changeEl.innerText = 'N/A';
            changeEl.className = isStale ? 'stale-text' : '';
            rowEl.setAttribute('data-change-pct', '');
            if (cell) cell.setAttribute('data-sort', 0);
        } else {
            var sign = isPositive ? '+' : '';
            changeEl.innerText = sign + pct.toFixed(2) + '%';
            changeEl.className = isStale ? 'stale-text' : (isPositive ? 'trend-up' : 'trend-down');
            rowEl.setAttribute('data-change-pct', pct);
            if (cell) cell.setAttribute('data-sort', pct);
        }
    }
    window._applyChangeCell = applyChangeCell;

    // Only one page (Portfolio or Watchlist) ever has an active instance at a time.
    var _current = null;

    // Delegated on document (not the button group itself) so it keeps working when the
    // buttons are appended after page load, e.g. Watchlist's DataTables-length-anchored group.
    document.addEventListener('click', function (e) {
        var btn = e.target.closest('.change-period-btn[data-period]');
        if (btn && _current) _current.changePeriod(btn.dataset.period);
    });

    function init(opts) {
        var table = opts.table;
        var cookieName = opts.cookieName;
        var globalVar = opts.globalVar;
        var onChange = opts.onChange;

        function setButtons(active) {
            document.querySelectorAll('.change-period-btn[data-period]').forEach(function (btn) {
                var isActive = btn.dataset.period === active;
                btn.classList.toggle('btn-primary', isActive);
                btn.classList.toggle('btn-outline-secondary', !isActive);
            });
        }

        function setCookie(period) {
            document.cookie = cookieName + '=' + period + ';path=/;max-age=31536000';
        }

        function recompute(period) {
            if (!table) return;
            table.rows().nodes().each(function (rowEl) {
                if (rowEl.classList.contains('child')) return;
                var pct, isPositive;
                if (period === '1d') {
                    var pct1d = rowEl.dataset.day1ChangePct;
                    pct = (pct1d === '' || pct1d === undefined) ? null : parseFloat(pct1d);
                    isPositive = rowEl.dataset.day1IsPositive === '1';
                } else {
                    pct = pctFromAnchor(rowEl.dataset.livePrice, rowEl.dataset['close' + period]);
                    isPositive = pct !== null && pct >= 0;
                }
                applyChangeCell(rowEl, pct, isPositive);
            });
            table.rows().invalidate('dom').draw(false);
        }

        function changePeriod(period) {
            window[globalVar] = period;
            setButtons(period);
            setCookie(period);
            recompute(period);
            if (onChange) onChange(period);
        }

        setButtons(window[globalVar] || '1d');

        _current = { changePeriod: changePeriod, setButtons: setButtons };
        return _current;
    }

    return { init: init };
})();
