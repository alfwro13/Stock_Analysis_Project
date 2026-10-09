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
            cell.textContent = formatCurrency(result.positionValue, window.BASE_CURRENCY);
            cell.setAttribute("data-sort", result.positionValue);
            if (sharesCell) {
                sharesCell.textContent = result.shares.toLocaleString();
                sharesCell.setAttribute("data-sort", result.shares);
            }
            if (stopCell) {
                stopCell.textContent = formatCurrency(result.stopPrice, currency);
                stopCell.setAttribute("data-sort", result.stopPrice);
            }
            if (riskCell) {
                riskCell.textContent = formatCurrency(result.riskAmount, window.BASE_CURRENCY);
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
        initComplete: function () {
            document.getElementById('dataTable').classList.remove('dt-init-pending');
        },
        columns: allCols.map(function (col) { return { name: col.key }; }),
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
        if (val === 'ALL') { table.column('signal:name').search('').draw(); }
        else { table.column('signal:name').search('^' + val + '$', true, false).draw(); }
    });

    $('#tagFilter').on('change', function () {
        $('#candleFilter').val('ALL');
        var val = $(this).val();
        if (val === 'ALL') { table.column('setup_tags:name').search('').draw(); }
        else { table.column('setup_tags:name').search(exactTagSearchPattern(val), true, false).draw(); }
    });

    $('#candleFilter').on('change', function () {
        $('#tagFilter').val('ALL');
        var val = $(this).val();
        if (val === 'ALL') { table.column('setup_tags:name').search('').draw(); }
        else { table.column('setup_tags:name').search(exactTagSearchPattern(val), true, false).draw(); }
    });

    var scoreMin = null, scoreMax = null;
    $.fn.dataTable.ext.search.push(function (settings, data) {
        if (settings.nTable.id !== 'dataTable') return true;
        if (scoreMin === null) return true;
        var score = parseFloat(data[table.column('score:name').index()]);
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

    // Change Period (1D/5D/1M/6M/YTD/1Y) — shared engine in static/js/change_period.js.
    // The button group is appended later by watchlist_add_ticker.js, which re-syncs the
    // active button via window._watchlistChangePeriod.setButtons() once it exists.
    window._watchlistChangePeriod = ChangePeriod.init({
        table: table,
        cookieName: 'watchlist_change_period',
        globalVar: 'WATCHLIST_CHANGE_PERIOD'
    });
});

(() => {
    let quotes = window.WATCHLIST_FX_STATUS || [];
    const currencies = window.WATCHLIST_FX_CURRENCIES || [];
    const modal = document.getElementById('watchlistFxModal');
    const details = document.getElementById('watchlistFxDetails');
    const progress = document.getElementById('watchlistFxProgress');
    const refreshButton = document.getElementById('watchlistFxRefresh');
    let refreshing = false;
    let reading = false;
    let pollTimer = null;
    const outcomes = new Map();

    window.renderWatchlistFxBadge = function () {
        const oldest = quotes.reduce((result, quote) =>
            quote.updated_at !== null && (!result || quote.updated_at < result.updated_at) ? quote : result, null);
        const label = quotes.length === 0 ? 'FX: not required'
            : quotes.some(quote => !quote.available) ? 'FX: unavailable'
            : `FX: ${oldest.updated_display}`;
        const embedButton = document.getElementById('watchlistFxEmbed');
        if (embedButton) embedButton.textContent = label;
        const slot = document.getElementById('freshness-badge-slot');
        const prices = slot && slot.lastElementChild;
        if (!prices) return;
        let button = document.getElementById('watchlistFxBadge');
        if (!button) {
            prices.appendChild(document.createTextNode('. '));
            button = document.createElement('button');
            button.type = 'button';
            button.id = 'watchlistFxBadge';
            button.className = 'fx-status-trigger';
            button.dataset.bsToggle = 'modal';
            button.dataset.bsTarget = '#watchlistFxModal';
            button.setAttribute('aria-label', 'Open FX status');
            prices.appendChild(button);
        }
        button.textContent = label;
    };

    function renderDetails() {
        details.replaceChildren();
        if (!quotes.length) details.textContent = 'No FX conversion is required.';
        quotes.forEach(quote => {
            const row = document.createElement('p');
            row.className = quote.stale ? 'text-warning mb-2' : 'mb-2';
            const state = !quote.available ? 'conversion unavailable' : quote.stale ? 'using cached rate' : 'rate up to date';
            const updated = quote.updated_display ? `Last successful update: ${quote.updated_display}.` : 'No successful update cached.';
            const outcome = outcomes.get(quote.pair);
            const next = outcome || (quote.stale ? 'Refresh requested in the background; reload to see updated values.' : '');
            row.textContent = `FX ${quote.pair}: ${state}. ${updated} ${next}`;
            details.appendChild(row);
        });
        refreshButton.disabled = refreshing || !quotes.length;
        window.renderWatchlistFxBadge();
    }

    async function readStatus() {
        if (reading || refreshing || !currencies.length) return;
        reading = true;
        try {
            const response = await fetch(`/api/fx/status?currencies=${encodeURIComponent(currencies.join(','))}`);
            const result = await response.json();
            if (!response.ok || result.status !== 'success') throw new Error('Could not read FX status.');
            quotes = result.quotes;
            renderDetails();
        } catch (error) {
            progress.textContent = 'Could not read FX status. Cached details are still shown.';
        } finally {
            reading = false;
        }
    }

    refreshButton.addEventListener('click', async () => {
        if (refreshing || reading) return;
        refreshing = true;
        refreshButton.disabled = true;
        outcomes.clear();
        const pairs = quotes.map(quote => ({pair: quote.pair, currency: quote.pair.slice(0, 3)}));
        let succeeded = 0;
        let completed = 0;
        for (const quote of pairs) {
            progress.textContent = `Refreshing ${quote.pair} — ${succeeded}/${pairs.length} refreshed, ${completed}/${pairs.length} completed.`;
            try {
                const response = await fetch('/api/fx/refresh', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({currency: quote.currency})
                });
                const result = await response.json();
                if (!response.ok) throw new Error('Refresh request failed.');
                result.quotes.forEach(updated => {
                    quotes = quotes.map(existing => existing.pair === updated.pair ? updated : existing);
                });
                if (result.status === 'success') {
                    succeeded++;
                    outcomes.set(quote.pair, 'Refresh complete; reload to apply updated values.');
                } else {
                    outcomes.set(quote.pair, result.message);
                }
            } catch (error) {
                outcomes.set(quote.pair, 'Refresh request failed; cached details retained.');
            }
            completed++;
            renderDetails();
            progress.textContent = `${succeeded}/${pairs.length} refreshed, ${completed}/${pairs.length} completed.`;
        }
        refreshing = false;
        renderDetails();
        progress.textContent += ' Reload to apply updated values.';
    });

    modal.addEventListener('shown.bs.modal', () => {
        readStatus();
        pollTimer = setInterval(readStatus, 2000);
    });
    modal.addEventListener('hidden.bs.modal', () => {
        clearInterval(pollTimer);
        pollTimer = null;
    });
    renderDetails();
})();
