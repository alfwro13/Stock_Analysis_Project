function renderPositionSizing() {
    document.querySelectorAll(".ps-cell").forEach(function (cell) {
        const row = cell.closest("tr");
        const entryPrice = parseFloat(cell.dataset.entryPrice);
        const atrPctRaw = cell.dataset.atrPct;
        const atrPct = atrPctRaw === "" ? null : parseFloat(atrPctRaw);
        const currency = cell.dataset.currency || "USD";

        const result = window.PositionSizing.calculateForRow(entryPrice, atrPct, currency);
        const sharesCell = row ? row.querySelector('[data-col-key="shares"]') : null;
        const stopCell = row ? row.querySelector('[data-col-key="stop_price"]') : null;
        const riskCell = row ? row.querySelector('[data-col-key="risk_amount"]') : null;

        if (result.positionValue != null && result.shares != null && result.shares > 0) {
            cell.textContent = window.PositionSizing.formatCurrency(result.positionValue, window.BASE_CURRENCY);
            cell.setAttribute("data-sort", result.positionValue);
            if (sharesCell) {
                sharesCell.textContent = result.shares.toLocaleString();
                sharesCell.setAttribute("data-sort", result.shares);
            }
            if (stopCell) {
                stopCell.textContent = window.PositionSizing.formatCurrency(result.stopPrice, currency);
                stopCell.setAttribute("data-sort", result.stopPrice);
            }
            if (riskCell) {
                riskCell.textContent = window.PositionSizing.formatCurrency(result.riskAmount, window.BASE_CURRENCY);
                riskCell.setAttribute("data-sort", result.riskAmount);
            }
        } else {
            cell.textContent = "—";
            if (sharesCell) sharesCell.textContent = "—";
            if (stopCell) stopCell.textContent = "—";
            if (riskCell) riskCell.textContent = "—";
        }
    });
}

$(document).ready(function () {
    renderPositionSizing();

    var allCols = window.WATCHLIST_COLUMNS || [];
    var colPrefs = window.WATCHLIST_COLUMN_PREFS || { hidden_core_columns: [], shown_optional_columns: [] };
    var hiddenIndices = [];
    allCols.forEach(function (col, idx) {
        if (!ColumnPicker.resolveVisible(col.key, allCols, colPrefs)) hiddenIndices.push(idx);
    });

    var table = $('#dataTable').DataTable({
        responsive: true,
        pageLength: 50,
        lengthMenu: [[10, 25, 50, 100, 250, -1], [10, 25, 50, 100, 250, 'All']],
        deferRender: true,
        dom: 'lrtip',
        order: [],
        columnDefs: [
            { responsivePriority: 1, targets: 0 },    // Ticker — always visible
            { responsivePriority: 2, targets: -1 },   // Signal — always visible
            { responsivePriority: 3, targets: 2 },    // Price
            { responsivePriority: 4, targets: 16 },    // Score
            { visible: false, targets: hiddenIndices }
        ]
    });
    window._watchlistTable = table;

    var picker = ColumnPicker.init({
        table: table,
        scope: 'watchlist',
        allColumns: allCols,
        prefs: colPrefs,
        menuId: 'columnPickerMenu'
    });

    var advFilter = AdvancedFilter.init({
        table: table,
        scope: 'watchlist',
        allColumns: allCols,
        modalId: 'advFilterModal',
        bodyId: 'advFilterBody',
        anchorId: 'dataTable_length',
        buttonClass: 'btn btn-sm btn-primary ms-2'
    });

    ColumnPicker.initViewsMenu(picker, {
        scope: 'watchlist',
        menuId: 'viewsPickerMenu',
        views: window.WATCHLIST_VIEWS,
        getExtraViewData: function () { return { filter: advFilter.getCurrentFilter() }; },
        onApplyView: function (view) { advFilter.applyFilter(view.filter || []); }
    });

    applyStickyTheadOffset();
    window.addEventListener('resize', applyStickyTheadOffset);

    try { if (localStorage.getItem('watchlist_heatmap_active')) _wlEnterHeatmapMode(); } catch (e) {}

    $('#dataTable tbody').on('click', 'tr:not(.child)', function (e) {
        if ($(e.target).closest('a').length) return;
        if ($(e.target).closest('.dtr-control').length) return;
        $(this).find('.dtr-control').trigger('click');
    });

    $('#customSearchInput').on('keyup', function () {
        $('#customSearchClear').toggle(Boolean(this.value));
        table.search(this.value).draw();
    });

    $('#customSearchClear').on('click', function () {
        $('#customSearchInput').val('').trigger('keyup');
    });

    $('#signalFilter').on('change', function () {
        var val = $(this).val();
        if (val === 'ALL') { table.column(23).search('').draw(); }
        else { table.column(23).search('^' + val + '$', true, false).draw(); }
    });

    $('#tagFilter').on('change', function () {
        $('#candleFilter').val('ALL');
        var val = $(this).val();
        if (val === 'ALL') { table.column(22).search('').draw(); }
        else { table.column(22).search(exactTagSearchPattern(val), true, false).draw(); }
    });

    $('#candleFilter').on('change', function () {
        $('#tagFilter').val('ALL');
        var val = $(this).val();
        if (val === 'ALL') { table.column(22).search('').draw(); }
        else { table.column(22).search(exactTagSearchPattern(val), true, false).draw(); }
    });

    var scoreMin = null, scoreMax = null;
    $.fn.dataTable.ext.search.push(function (settings, data) {
        if (settings.nTable.id !== 'dataTable') return true;
        if (scoreMin === null) return true;
        var score = parseFloat(data[16]);
        if (isNaN(score)) return false;
        return score >= scoreMin && (scoreMax === null || score <= scoreMax);
    });

    $('#scoreFilter').on('change', function () {
        var val = $(this).val();
        if (val === 'ALL') { scoreMin = null; scoreMax = null; }
        else if (val === '75') { scoreMin = 75; scoreMax = null; }
        else if (val === '60') { scoreMin = 60; scoreMax = 74; }
        else if (val === '40') { scoreMin = 40; scoreMax = 59; }
        else if (val === '0') { scoreMin = 0; scoreMax = 39; }
        table.draw();
    });

    var sectorSelected = 'ALL';
    $.fn.dataTable.ext.search.push(function (settings, data, dataIndex) {
        if (settings.nTable.id !== 'dataTable') return true;
        if (sectorSelected === 'ALL') return true;
        var node = table.row(dataIndex).node();
        return Boolean(node) && node.dataset.sector === sectorSelected;
    });

    $('#sectorFilter').on('change', function () {
        sectorSelected = $(this).val();
        table.draw();
    });

    $(document).on('click', '.change-period-btn', function () {
        changePeriod(this.dataset.period);
    });
});

// ── Change Period (1D/5D/1M/6M/YTD/1Y) ─────────────────────────────────────────
function _setChangePeriodCookie(period) {
    document.cookie = 'watchlist_change_period=' + period + ';path=/;max-age=31536000';
}

function _setChangePeriodButtons(active) {
    document.querySelectorAll('.change-period-btn').forEach(function (btn) {
        var isActive = btn.dataset.period === active;
        btn.classList.toggle('btn-primary', isActive);
        btn.classList.toggle('btn-outline-secondary', !isActive);
    });
}

function _pctFromAnchor(livePrice, anchorClose) {
    var live = parseFloat(livePrice);
    var anchor = parseFloat(anchorClose);
    if (!isFinite(live) || !isFinite(anchor) || anchor === 0) return null;
    return (live - anchor) / anchor * 100;
}
window._pctFromAnchor = _pctFromAnchor;

function _applyChangeCell(rowEl, pct, isPositive, isStale) {
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
window._applyChangeCell = _applyChangeCell;

function _recomputeChangeColumn(period) {
    if (!window._watchlistTable) return;
    window._watchlistTable.rows().nodes().each(function (rowEl) {
        if (rowEl.classList.contains('child')) return;
        var pct, isPositive;
        if (period === '1d') {
            var pct1d = rowEl.dataset.day1ChangePct;
            pct = (pct1d === '' || pct1d === undefined) ? null : parseFloat(pct1d);
            isPositive = rowEl.dataset.day1IsPositive === '1';
        } else {
            pct = _pctFromAnchor(rowEl.dataset.livePrice, rowEl.dataset['close' + period]);
            isPositive = pct !== null && pct >= 0;
        }
        _applyChangeCell(rowEl, pct, isPositive);
    });
    window._watchlistTable.rows().invalidate('dom').draw(false);
}

function changePeriod(period) {
    window.WATCHLIST_CHANGE_PERIOD = period;
    _setChangePeriodButtons(period);
    _setChangePeriodCookie(period);
    _recomputeChangeColumn(period);
}
