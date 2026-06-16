function renderPositionSizing() {
    document.querySelectorAll(".ps-cell").forEach(function (cell) {
        const entryPrice = parseFloat(cell.dataset.entryPrice);
        const atrPctRaw = cell.dataset.atrPct;
        const atrPct = atrPctRaw === "" ? null : parseFloat(atrPctRaw);
        const currency = cell.dataset.currency || "USD";

        const result = window.PositionSizing.calculateForRow(entryPrice, atrPct, currency);

        if (result.positionValue != null && result.shares != null && result.shares > 0) {
            cell.textContent = window.PositionSizing.formatCurrency(result.positionValue, window.BASE_CURRENCY);
            cell.setAttribute("data-sort", result.positionValue);
            const sharesCell = cell.nextElementSibling;
            if (sharesCell && sharesCell.classList.contains("ps-cell-shares")) {
                sharesCell.textContent = result.shares.toLocaleString();
                sharesCell.setAttribute("data-sort", result.shares);
            }
        } else {
            cell.textContent = "—";
            const sharesCell = cell.nextElementSibling;
            if (sharesCell && sharesCell.classList.contains("ps-cell-shares")) {
                sharesCell.textContent = "—";
            }
        }
    });
}

$(document).ready(function () {
    renderPositionSizing();

    var table = $('#dataTable').DataTable({
        responsive: true,
        pageLength: 50,
        deferRender: true,
        dom: 'lrtip',
        order: [],
        columnDefs: [
            { responsivePriority: 1, targets: 0 },    // Ticker — always visible
            { responsivePriority: 2, targets: -1 },   // Signal — always visible
            { responsivePriority: 3, targets: 2 },    // Price
            { responsivePriority: 4, targets: 16 }     // Score
        ]
    });

    $('#customSearchInput').on('keyup', function () {
        table.search(this.value).draw();
    });

    $('#signalFilter').on('change', function () {
        var val = $(this).val();
        if (val === 'ALL') { table.column(18).search('').draw(); }
        else { table.column(18).search('^' + val + '$', true, false).draw(); }
    });

    $('#tagFilter').on('change', function () {
        $('#candleFilter').val('ALL');
        var val = $(this).val();
        if (val === 'ALL') { table.column(17).search('').draw(); }
        else { table.column(17).search(val).draw(); }
    });

    $('#candleFilter').on('change', function () {
        $('#tagFilter').val('ALL');
        var val = $(this).val();
        if (val === 'ALL') { table.column(17).search('').draw(); }
        else { table.column(17).search(val, false, false).draw(); }
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
});
