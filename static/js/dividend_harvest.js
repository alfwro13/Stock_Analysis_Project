let divDataTable;
let divActiveBtn = null;

$.fn.dataTable.ext.errMode = 'none';

function setButtonLoading(btn, isLoading) {
    if (!btn) return;
    btn.disabled = isLoading;
    btn.textContent = isLoading ? '⏳ Loading…' : '🔄 Run Query';
}

$(document).ready(function() {
    divDataTable = $('#dividendTable').DataTable({
        deferRender: true,
        responsive: true,
        pageLength: 10,
        order: [[5, 'asc']],

        columnDefs: [
            { responsivePriority: 1, targets: [0, 4, 5] },
            { responsivePriority: 2, targets: [3, 6] },
            { responsivePriority: 3, targets: 7 },
            { responsivePriority: 4, targets: [1, 2] }
        ],

        columns: [
            { data: 'ticker', render: function(data) { return '<a href="/stock/' + data + '" class="ticker-link">' + data + '</a>'; } },
            { data: 'company_name' },
            { data: 'sector' },
            {
                data: 'close_price',
                render: function(data, type, row) {
                    return type === 'display' ? formatCurrency(data, row.currency) : data;
                }
            },
            { data: 'dividend_yield', render: function(data) { return `<span class="positive-val">${(data * 100).toFixed(2)}%</span>`; } },
            { data: 'ex_dividend_date' },
            { data: 'composite_score', render: function(data) {
                if (data == null) return '-';
                let cssClass = data >= 70 ? 'positive-val' : (data < 40 ? 'negative-val' : '');
                return `<span class="${cssClass}">${data}</span>`;
            } },
            { data: 'ml_confidence_score', render: function(data) {
                if (data == null) return '-';
                return `${data.toFixed(1)}%`;
            } }
        ]
    });

    $('#dividendTable').on('error.dt', function() {
        showTableError('#dividendTable', 8);
        setButtonLoading(divActiveBtn, false);
        divActiveBtn = null;
    });

    loadDividendData();
});

function loadDividendData(btn) {
    const minYieldInput = parseFloat(document.getElementById('divMinYield').value) || 2.0;
    const minYield = minYieldInput / 100.0;
    const minScore = parseInt(document.getElementById('divMinScore').value) || 50;
    divActiveBtn = btn || null;
    setButtonLoading(divActiveBtn, true);
    divDataTable.ajax.url(`/api/reports/dividends?min_yield=${minYield}&min_score=${minScore}`).load(function() {
        setButtonLoading(divActiveBtn, false);
        divActiveBtn = null;
    });
}
