let mrDataTable;
let mrActiveBtn = null;

$.fn.dataTable.ext.errMode = 'none';

function setButtonLoading(btn, isLoading) {
    if (!btn) return;
    btn.disabled = isLoading;
    btn.textContent = isLoading ? '⏳ Loading…' : '🔄 Run Query';
}

$(document).ready(function() {
    mrDataTable = $('#mrTable').DataTable({
        deferRender: true,
        responsive: true,
        pageLength: 10,
        order: [[5, 'asc']],

        columnDefs: [
            { responsivePriority: 1, targets: [0, 5] },
            { responsivePriority: 2, targets: [4, 7] },
            { responsivePriority: 3, targets: 6 },
            { responsivePriority: 4, targets: [1, 2, 3] }
        ],

        columns: [
            { data: 'ticker', render: function(data) { return '<a href="/stock/' + data + '" class="ticker-link">' + data + '</a>'; } },
            { data: 'company_name' },
            { data: 'sector' },
            { data: 'country', render: function(data) { return data || 'US'; } },
            {
                data: 'close_price',
                render: function(data, type, row) {
                    return type === 'display' ? formatCurrency(data, row.currency) : data;
                }
            },
            { data: 'rsi_14', render: function(data) { return `<span class="negative-val">${data.toFixed(2)}</span>`; } },
            {
                data: 'sma_200',
                render: function(data, type, row) {
                    return type === 'display' ? formatCurrency(data, row.currency) : data;
                }
            },
            { data: 'distance_from_200d_pct', render: function(data) { return `<span class="positive-val">+${data.toFixed(2)}%</span>`; } }
        ]
    });

    $('#mrTable').on('error.dt', function() {
        showTableError('#mrTable', 8);
        setButtonLoading(mrActiveBtn, false);
        mrActiveBtn = null;
    });

    loadMeanReversionData();
});

function loadMeanReversionData(btn) {
    const maxRsi = document.getElementById('mrMaxRSI').value || 30;
    const minSmaDistance = parseFloat(document.getElementById('mrMinSmaDistance').value) || 0;
    mrActiveBtn = btn || null;
    setButtonLoading(mrActiveBtn, true);
    mrDataTable.ajax.url(`/api/reports/mean-reversion?max_rsi=${maxRsi}&min_sma_distance=${minSmaDistance}`).load(function() {
        setButtonLoading(mrActiveBtn, false);
        mrActiveBtn = null;
    });
}
