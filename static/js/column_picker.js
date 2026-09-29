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
        const defaultOrder = allColumns.map(function (col) { return col.key; });
        const dragMedia = window.matchMedia('(min-width: 769px) and (hover: hover) and (pointer: fine)');
        let applyingView = true;
        let saveQueue = Promise.resolve();

        function getColumnOrder() {
            return table.colReorder.order().map(function (idx) { return allColumns[idx].key; });
        }

        function applyOrder(keys) {
            table.colReorder.order((keys || defaultOrder).map(function (key) {
                return defaultOrder.indexOf(key);
            }), true);
        }

        new $.fn.dataTable.ColReorder(table, {
            fixedColumnsLeft: 1,
            realtime: false,
            enable: dragMedia.matches,
            // The completion callback runs once, after all internal column moves finish.
            reorderCallback: function () {
                if (applyingView) return;
                table.columns.adjust().draw(false);
                table.responsive.recalc();
                savePrefs();
            }
        });
        const tableNode = table.table().node();
        tableNode.classList.add('column-reorder-table');
        $(table.columns().header()).off('touchstart.ColReorder');
        function updateDragEnabled() {
            table.colReorder.enable(dragMedia.matches);
        }
        dragMedia.addEventListener('change', updateDragEnabled);
        table.on('destroy.dt', function () {
            dragMedia.removeEventListener('change', updateDragEnabled);
        });
        applyOrder(opts.prefs && opts.prefs.column_order);
        applyingView = false;
        table.columns.adjust().draw(false);
        table.responsive.recalc();

        function userWants(key) {
            return resolveVisible(key, allColumns, prefs);
        }

        function applyColumn(key) {
            const idx = allColumns.findIndex(function (c) { return c.key === key; });
            if (idx === -1) return;
            table.column(key + ':name').visible(userWants(key));
        }

        function savePrefs() {
            const body = JSON.stringify({
                scope: scope,
                hidden_core_columns: prefs.hidden_core_columns,
                shown_optional_columns: prefs.shown_optional_columns,
                column_order: getColumnOrder()
            });
            saveQueue = saveQueue.then(function () {
                return fetch('/api/ui-preferences/columns', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: body
                }).then(function (response) {
                    if (!response.ok) throw new Error('Column preferences could not be saved.');
                });
            }).catch(function () {
                alert('Column preferences could not be saved. Please try again.');
            });
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

        function applyView(columnKeys, columnOrder) {
            applyingView = true;
            applyOrder(columnOrder);
            const keySet = new Set(columnKeys);
            const hiddenCore = [];
            const shownOptional = [];
            allColumns.forEach(function (col) {
                if (col.pinned) return;
                const wants = keySet.has(col.key);
                if (col.type === 'optional') {
                    if (wants) shownOptional.push(col.key);
                } else if (!wants) {
                    hiddenCore.push(col.key);
                }
            });
            prefs.hidden_core_columns = hiddenCore;
            prefs.shown_optional_columns = shownOptional;
            allColumns.forEach(function (col) {
                table.column(col.key + ':name').visible(userWants(col.key), false);
            });
            table.columns.adjust().draw(false);
            table.responsive.recalc();
            applyingView = false;
            renderMenu();
            savePrefs();
        }

        function getCurrentVisibleKeys() {
            return getColumnOrder().filter(userWants);
        }

        renderMenu();

        return {
            isVisible: userWants,
            applyView: applyView,
            getCurrentVisibleKeys: getCurrentVisibleKeys,
            getColumnOrder: getColumnOrder,
            getDefaultOrder: function () { return defaultOrder.slice(); }
        };
    }

    function _columnSetsEqual(a, b) {
        if (a.length !== b.length) return false;
        const setA = new Set(a);
        return b.every(function (k) { return setA.has(k); });
    }

    function _filtersEqual(a, b) {
        const fa = a || null, fb = b || null;
        if (!fa && !fb) return true;
        if (!fa || !fb) return false;
        if (fa.logic !== fb.logic) return false;
        const ca = fa.conditions || [], cb = fb.conditions || [];
        if (ca.length !== cb.length) return false;
        return ca.every(function (x, i) {
            const y = cb[i];
            return x.key === y.key && x.operator === y.operator &&
                (x.value || '') === (y.value || '') && (x.value2 || '') === (y.value2 || '');
        });
    }

    function initViewsMenu(picker, opts) {
        const scope = opts.scope;
        const menuEl = document.getElementById(opts.menuId);
        let views = (opts.views || []).slice();
        let saveQueue = Promise.resolve();

        function saveViews() {
            const body = JSON.stringify({ scope: scope, views: views });
            saveQueue = saveQueue.then(function () {
                return fetch('/api/ui-preferences/views', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: body
                }).then(function (response) {
                    if (!response.ok) throw new Error('Views could not be saved.');
                });
            }).catch(function () {
                alert('Views could not be saved. Please try again.');
            });
        }

        function isViewActive(view) {
            if (!_columnSetsEqual(picker.getCurrentVisibleKeys(), view.columns || [])) return false;
            const order = view.column_order || picker.getDefaultOrder();
            if (!picker.getColumnOrder().every(function (key, idx) { return key === order[idx]; })) return false;
            const extra = (typeof opts.getExtraViewData === 'function') ? opts.getExtraViewData() : {};
            return _filtersEqual(extra.filter, view.filter);
        }

        function renderMenu() {
            if (!menuEl) return;
            let html = '';
            views.forEach(function (view, idx) {
                const active = isViewActive(view);
                html += '<div class="dropdown-item-text view-item' + (active ? ' view-item-active' : '') + '">' +
                    '<span class="view-name" role="button" data-idx="' + idx + '">' + escapeHtml(view.name) +
                    (active ? ' <span class="view-active-badge">&#10003; Active</span>' : '') + '</span>' +
                    '<button type="button" class="view-delete-btn" data-idx="' + idx + '" aria-label="Delete view">&times;</button>' +
                    '</div>';
            });
            html += '<div class="dropdown-divider"></div>' +
                '<div class="view-save-row">' +
                '<input type="text" class="form-control form-control-sm view-name-input" placeholder="View name…">' +
                '<button type="button" class="btn btn-sm btn-outline-primary view-save-btn">Save Current</button>' +
                '</div>';
            menuEl.innerHTML = html;

            menuEl.querySelectorAll('.view-name').forEach(function (el) {
                el.addEventListener('click', function () {
                    const view = views[parseInt(el.dataset.idx, 10)];
                    if (!view) return;
                    picker.applyView(view.columns, view.column_order);
                    if (typeof opts.onApplyView === 'function') opts.onApplyView(view);
                    renderMenu();
                });
            });
            menuEl.querySelectorAll('.view-delete-btn').forEach(function (btn) {
                btn.addEventListener('click', function (e) {
                    e.stopPropagation();
                    const view = views[parseInt(btn.dataset.idx, 10)];
                    if (!view || !confirm('Delete the view "' + view.name + '"? This cannot be undone.')) return;
                    views.splice(parseInt(btn.dataset.idx, 10), 1);
                    saveViews();
                    renderMenu();
                });
            });
            const input = menuEl.querySelector('.view-name-input');
            const saveBtn = menuEl.querySelector('.view-save-btn');
            if (input) input.addEventListener('click', function (e) { e.stopPropagation(); });
            if (saveBtn) {
                saveBtn.addEventListener('click', function (e) {
                    e.stopPropagation();
                    const name = (input.value || '').trim();
                    if (!name) return;
                    const columns = picker.getCurrentVisibleKeys();
                    const extra = (typeof opts.getExtraViewData === 'function') ? opts.getExtraViewData() : {};
                    const view = Object.assign({ name: name, columns: columns, column_order: picker.getColumnOrder() }, extra);
                    const existingIdx = views.findIndex(function (v) { return v.name === name; });
                    if (existingIdx !== -1) { views[existingIdx] = view; }
                    else { views.push(view); }
                    input.value = '';
                    saveViews();
                    renderMenu();
                });
            }
        }

        const dropdownEl = menuEl ? menuEl.closest('.dropdown') : null;
        if (dropdownEl) dropdownEl.addEventListener('show.bs.dropdown', renderMenu);

        renderMenu();
    }

    return { init: init, resolveVisible: resolveVisible, initViewsMenu: initViewsMenu };
})();
