window.ColumnPicker = (function () {
    "use strict";

    function resolveVisible(key, allColumns, prefs) {
        const col = (allColumns || []).find(function (c) { return c.key === key; });
        if (!col) return true;
        if (col.pinned) return true;
        if (col.type === 'optional') {
            return (prefs.shown_optional_columns || []).indexOf(key) !== -1;
        }
        return (prefs.hidden_core_columns || []).indexOf(key) === -1;
    }

    function init(opts) {
        const table = opts.table;
        const scope = opts.scope;
        const allColumns = opts.allColumns || [];
        const prefs = {
            hidden_core_columns: (opts.prefs && opts.prefs.hidden_core_columns) || [],
            shown_optional_columns: (opts.prefs && opts.prefs.shown_optional_columns) || []
        };
        const menuEl = document.getElementById(opts.menuId);
        const filterOverrides = {};

        function userWants(key) {
            return resolveVisible(key, allColumns, prefs);
        }

        function effectiveVisible(key) {
            const wants = userWants(key);
            if (Object.prototype.hasOwnProperty.call(filterOverrides, key)) {
                return wants && filterOverrides[key];
            }
            return wants;
        }

        function applyColumn(key) {
            const idx = allColumns.findIndex(function (c) { return c.key === key; });
            if (idx === -1) return;
            table.column(idx).visible(effectiveVisible(key));
        }

        function savePrefs() {
            fetch('/api/ui-preferences/columns', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({
                    scope: scope,
                    hidden_core_columns: prefs.hidden_core_columns,
                    shown_optional_columns: prefs.shown_optional_columns
                })
            }).catch(function () {});
        }

        function setUserPref(key, visible) {
            const col = allColumns.find(function (c) { return c.key === key; });
            if (!col || col.pinned) return;
            if (col.type === 'optional') {
                const set = new Set(prefs.shown_optional_columns);
                if (visible) { set.add(key); } else { set.delete(key); }
                prefs.shown_optional_columns = Array.from(set);
            } else {
                const set = new Set(prefs.hidden_core_columns);
                if (visible) { set.delete(key); } else { set.add(key); }
                prefs.hidden_core_columns = Array.from(set);
            }
            applyColumn(key);
            savePrefs();
        }

        function renderMenu() {
            if (!menuEl) return;
            const groups = {};
            const order = [];
            allColumns.forEach(function (col) {
                const group = col.type === 'core' ? 'Standard Columns' : (col.category || 'Other');
                if (!groups[group]) { groups[group] = []; order.push(group); }
                groups[group].push(col);
            });
            let html = '';
            order.forEach(function (group) {
                html += '<h6 class="dropdown-header">' + escapeHtml(group) + '</h6>';
                groups[group].forEach(function (col) {
                    const checked = userWants(col.key) ? 'checked' : '';
                    const disabled = col.pinned ? 'disabled' : '';
                    const domId = 'colpick-' + scope + '-' + col.key;
                    html += '<div class="form-check column-picker-item">' +
                        '<input class="form-check-input" type="checkbox" id="' + escapeHtml(domId) + '" data-key="' + escapeHtml(col.key) + '" ' + checked + ' ' + disabled + '>' +
                        '<label class="form-check-label" for="' + escapeHtml(domId) + '">' + escapeHtml(col.label) + '</label>' +
                        '</div>';
                });
            });
            menuEl.innerHTML = html;
            menuEl.querySelectorAll('input[type=checkbox]').forEach(function (cb) {
                cb.addEventListener('change', function () {
                    setUserPref(cb.dataset.key, cb.checked);
                });
            });
        }

        renderMenu();

        return {
            isVisible: effectiveVisible,
            applyFilterOverride: function (key, visible) {
                filterOverrides[key] = visible;
                applyColumn(key);
            }
        };
    }

    return { init: init, resolveVisible: resolveVisible };
})();
