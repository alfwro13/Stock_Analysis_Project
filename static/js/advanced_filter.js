window.AdvancedFilter = (function () {
    "use strict";

    var FMT_FAMILY = {
        pct_from_fraction: 'numeric', pct_raw: 'numeric', ratio2: 'numeric', int: 'numeric',
        price: 'numeric', price_raw: 'numeric', currency_usd: 'numeric', volume: 'numeric', client: 'numeric',
        text: 'text', date: 'date', bool01: 'bool'
    };
    var NUMERIC_SCALE = { pct_from_fraction: 100 };
    var MISSING_TEXT = new Set(['N/A', '-', '—', '']);

    var OPERATORS = {
        numeric: [
            { value: 'gt', label: '>' }, { value: 'gte', label: '≥' },
            { value: 'lt', label: '<' }, { value: 'lte', label: '≤' },
            { value: 'eq', label: '=' }, { value: 'neq', label: '≠' },
            { value: 'between', label: 'between' },
            { value: 'empty', label: 'is empty' }, { value: 'not_empty', label: 'is not empty' }
        ],
        text: [
            { value: 'contains', label: 'contains' }, { value: 'not_contains', label: 'does not contain' },
            { value: 'eq', label: 'equals' }, { value: 'neq', label: 'does not equal' },
            { value: 'empty', label: 'is empty' }, { value: 'not_empty', label: 'is not empty' }
        ],
        date: [
            { value: 'before', label: 'before' }, { value: 'after', label: 'after' }, { value: 'on', label: 'on' },
            { value: 'between', label: 'between' },
            { value: 'empty', label: 'is empty' }, { value: 'not_empty', label: 'is not empty' }
        ],
        bool: [
            { value: 'true', label: 'is true' }, { value: 'false', label: 'is false' }
        ]
    };

    function familyFor(col) {
        return (col && FMT_FAMILY[col.fmt]) || 'text';
    }

    function isMissingText(text) {
        return MISSING_TEXT.has((text || '').trim());
    }

    function init(opts) {
        var table = opts.table;
        var scope = opts.scope;
        var allColumns = opts.allColumns || [];
        var storageKey = scope + '_adv_filter';
        var modalEl = document.getElementById(opts.modalId);
        var bodyEl = document.getElementById(opts.bodyId);
        var bsModal = modalEl ? bootstrap.Modal.getOrCreateInstance(modalEl) : null;
        var conditions = [];
        var draft = [];

        function columnByKey(key) {
            return allColumns.find(function (c) { return c.key === key; });
        }

        function firstOperatorFor(family) {
            return (OPERATORS[family] || OPERATORS.text)[0].value;
        }

        function defaultRow() {
            var col = allColumns[0] || { key: '', fmt: 'text' };
            return { key: col.key, operator: firstOperatorFor(familyFor(col)), value: '', value2: '' };
        }

        function loadFromStorage() {
            try {
                var raw = localStorage.getItem(storageKey);
                var parsed = raw ? JSON.parse(raw) : [];
                return Array.isArray(parsed) ? parsed : [];
            } catch (e) {
                return [];
            }
        }

        function saveToStorage(conds) {
            try { localStorage.setItem(storageKey, JSON.stringify(conds)); } catch (e) {}
        }

        function cellFor(dataIndex, key) {
            var idx = allColumns.findIndex(function (c) { return c.key === key; });
            if (idx === -1) return null;
            try { return table.cell(dataIndex, idx).node(); } catch (e) { return null; }
        }

        function evaluateCondition(cond, dataIndex) {
            var col = columnByKey(cond.key);
            if (!col) return true;
            var cell = cellFor(dataIndex, cond.key);
            if (!cell) return true;
            var family = familyFor(col);
            var text = (cell.textContent || '').replace(/\s+/g, ' ').trim();

            if (family === 'bool') {
                var isTrue = cell.getAttribute('data-sort') === '1';
                return cond.operator === 'true' ? isTrue : !isTrue;
            }

            var missing = isMissingText(text);
            if (cond.operator === 'empty') return missing;
            if (cond.operator === 'not_empty') return !missing;
            if (missing) return false;

            if (family === 'numeric') {
                var scale = NUMERIC_SCALE[col.fmt] || 1;
                var raw = parseFloat(cell.getAttribute('data-sort'));
                if (isNaN(raw)) return false;
                var num = raw * scale;
                var v1 = parseFloat(cond.value);
                if (isNaN(v1) && cond.operator !== 'between') return true;
                switch (cond.operator) {
                    case 'gt': return num > v1;
                    case 'gte': return num >= v1;
                    case 'lt': return num < v1;
                    case 'lte': return num <= v1;
                    case 'eq': return num === v1;
                    case 'neq': return num !== v1;
                    case 'between':
                        var v2 = parseFloat(cond.value2);
                        if (isNaN(v1) || isNaN(v2)) return true;
                        return num >= Math.min(v1, v2) && num <= Math.max(v1, v2);
                    default: return true;
                }
            }

            if (family === 'date') {
                var sortStr = cell.getAttribute('data-sort') || '';
                if (!cond.value) return true;
                switch (cond.operator) {
                    case 'before': return sortStr < cond.value;
                    case 'after': return sortStr > cond.value;
                    case 'on': return sortStr === cond.value;
                    case 'between': return cond.value2 ? (sortStr >= cond.value && sortStr <= cond.value2) : true;
                    default: return true;
                }
            }

            // text family
            var hay = text.toLowerCase();
            var needle = (cond.value || '').toLowerCase();
            switch (cond.operator) {
                case 'contains': return hay.indexOf(needle) !== -1;
                case 'not_contains': return hay.indexOf(needle) === -1;
                case 'eq': return hay === needle;
                case 'neq': return hay !== needle;
                default: return true;
            }
        }

        $.fn.dataTable.ext.search.push(function (settings, data, dataIndex) {
            if (settings.nTable.id !== 'dataTable') return true;
            if (!conditions.length) return true;
            return conditions.every(function (c) { return evaluateCondition(c, dataIndex); });
        });

        var btn = document.createElement('button');
        btn.type = 'button';
        btn.id = 'advFilterBtn-' + scope;
        btn.className = opts.buttonClass || 'btn btn-sm btn-outline-secondary ms-2';
        var anchor = document.getElementById(opts.anchorId || 'dataTable_length');
        if (anchor) anchor.appendChild(btn);

        function refreshButton() {
            btn.textContent = conditions.length ? ('🔍 Filter (' + conditions.length + ')') : '🔍 Filter';
        }

        function valueInputsHtml(idx, cond, family) {
            if (family === 'bool') return '';
            if (cond.operator === 'empty' || cond.operator === 'not_empty') return '';
            var type = family === 'numeric' ? 'number' : (family === 'date' ? 'date' : 'text');
            var step = family === 'numeric' ? ' step="any"' : '';
            var html = '<input type="' + type + '"' + step + ' class="form-control form-control-sm adv-val-input" ' +
                'data-idx="' + idx + '" data-field="value" value="' + escapeHtml(cond.value || '') + '">';
            if (cond.operator === 'between') {
                html += '<span class="adv-filter-and">and</span>';
                html += '<input type="' + type + '"' + step + ' class="form-control form-control-sm adv-val-input" ' +
                    'data-idx="' + idx + '" data-field="value2" value="' + escapeHtml(cond.value2 || '') + '">';
            }
            return html;
        }

        function columnOptionsHtml(selectedKey) {
            var groups = {};
            var order = [];
            allColumns.forEach(function (col) {
                var group = col.type === 'core' ? 'Standard Columns' : (col.category || 'Other');
                if (!groups[group]) { groups[group] = []; order.push(group); }
                groups[group].push(col);
            });
            var html = '';
            order.forEach(function (group) {
                html += '<optgroup label="' + escapeHtml(group) + '">';
                groups[group].forEach(function (col) {
                    var sel = col.key === selectedKey ? ' selected' : '';
                    html += '<option value="' + escapeHtml(col.key) + '"' + sel + '>' + escapeHtml(col.label) + '</option>';
                });
                html += '</optgroup>';
            });
            return html;
        }

        function operatorOptionsHtml(family, selectedOp) {
            return (OPERATORS[family] || OPERATORS.text).map(function (op) {
                var sel = op.value === selectedOp ? ' selected' : '';
                return '<option value="' + op.value + '"' + sel + '>' + escapeHtml(op.label) + '</option>';
            }).join('');
        }

        function rowHtml(cond, idx) {
            var col = columnByKey(cond.key) || allColumns[0];
            var family = familyFor(col);
            return '<div class="adv-filter-row" data-idx="' + idx + '">' +
                '<select class="form-select form-select-sm adv-col-select" data-idx="' + idx + '">' + columnOptionsHtml(cond.key) + '</select>' +
                '<select class="form-select form-select-sm adv-op-select" data-idx="' + idx + '">' + operatorOptionsHtml(family, cond.operator) + '</select>' +
                '<span class="adv-filter-values">' + valueInputsHtml(idx, cond, family) + '</span>' +
                '<button type="button" class="adv-filter-remove-btn" data-idx="' + idx + '" aria-label="Remove condition">&times;</button>' +
                '</div>';
        }

        function renderRows() {
            if (!bodyEl) return;
            bodyEl.innerHTML = draft.map(rowHtml).join('') +
                '<button type="button" class="btn btn-sm btn-outline-primary adv-filter-add-btn">+ Add Condition</button>';

            bodyEl.querySelectorAll('.adv-col-select').forEach(function (sel) {
                sel.addEventListener('change', function () {
                    var idx = parseInt(sel.dataset.idx, 10);
                    var col = columnByKey(sel.value);
                    draft[idx].key = sel.value;
                    draft[idx].operator = firstOperatorFor(familyFor(col));
                    draft[idx].value = '';
                    draft[idx].value2 = '';
                    renderRows();
                });
            });
            bodyEl.querySelectorAll('.adv-op-select').forEach(function (sel) {
                sel.addEventListener('change', function () {
                    var idx = parseInt(sel.dataset.idx, 10);
                    draft[idx].operator = sel.value;
                    if (sel.value !== 'between') draft[idx].value2 = '';
                    renderRows();
                });
            });
            bodyEl.querySelectorAll('.adv-val-input').forEach(function (inp) {
                inp.addEventListener('input', function () {
                    var idx = parseInt(inp.dataset.idx, 10);
                    draft[idx][inp.dataset.field] = inp.value;
                });
            });
            bodyEl.querySelectorAll('.adv-filter-remove-btn').forEach(function (rmBtn) {
                rmBtn.addEventListener('click', function () {
                    draft.splice(parseInt(rmBtn.dataset.idx, 10), 1);
                    renderRows();
                });
            });
            var addBtn = bodyEl.querySelector('.adv-filter-add-btn');
            if (addBtn) {
                addBtn.addEventListener('click', function () {
                    draft.push(defaultRow());
                    renderRows();
                });
            }
        }

        function isConditionUsable(cond) {
            var col = columnByKey(cond.key);
            if (!col) return false;
            var family = familyFor(col);
            if (family === 'bool') return true;
            if (cond.operator === 'empty' || cond.operator === 'not_empty') return true;
            if (cond.operator === 'between') return cond.value !== '' && cond.value2 !== '';
            return cond.value !== '' && cond.value !== undefined && cond.value !== null;
        }

        function applyFilter(conds, persist) {
            conditions = (conds || []).slice();
            table.draw();
            refreshButton();
            if (persist !== false) saveToStorage(conditions);
        }

        function getCurrentFilter() {
            return conditions.length ? conditions.slice() : null;
        }

        if (modalEl) {
            var applyBtn = modalEl.querySelector('.adv-filter-apply-btn');
            var clearBtn = modalEl.querySelector('.adv-filter-clear-btn');
            if (applyBtn) {
                applyBtn.addEventListener('click', function () {
                    applyFilter(draft.filter(isConditionUsable));
                    bsModal.hide();
                });
            }
            if (clearBtn) {
                clearBtn.addEventListener('click', function () {
                    draft = [];
                    applyFilter([]);
                    renderRows();
                    bsModal.hide();
                });
            }
        }

        btn.addEventListener('click', function () {
            draft = conditions.length ? conditions.map(function (c) { return Object.assign({}, c); }) : [defaultRow()];
            renderRows();
            if (bsModal) bsModal.show();
        });

        applyFilter(loadFromStorage(), false);

        return { getCurrentFilter: getCurrentFilter, applyFilter: applyFilter };
    }

    return { init: init };
})();
