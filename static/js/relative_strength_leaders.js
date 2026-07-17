$.fn.dataTable.ext.errMode = 'none';

$(document).ready(function() {
    const urlParams = new URLSearchParams(window.location.search);

    var leadersTable = $('#leadersTable').DataTable({
        ajax: {
            url: '/api/reports/leaders',
            error: function() { showTableError('#leadersTable', 8); }
        },
        deferRender: true,
        responsive: true,
        pageLength: 10,
        order: [[6, 'desc']],

        columnDefs: [
            { responsivePriority: 1, targets: [0, 6] },
            { responsivePriority: 2, targets: [5, 7] },
            { responsivePriority: 3, targets: 4 },
            { responsivePriority: 4, targets: [1, 2] }
        ],

        initComplete: function () {
            var column = this.api().column(3);
            var select = $('#leadersExchangeFilter');

            column.data().unique().sort().each(function(d) {
                if (d && d !== 'N/A') {
                    select.append('<option value="' + d + '">' + d + '</option>');
                }
            });

            const defaultExchange = urlParams.get('exchange') || 'NASDAQ';
            if (defaultExchange !== 'ALL' && select.find('option[value="' + defaultExchange + '"]').length > 0) {
                select.val(defaultExchange);
                column.search('^' + defaultExchange + '$', true, false).draw();
            }
        },

        columns: [
            { data: 'ticker', render: function(data) { return '<a href="/stock/' + data + '" class="ticker-link">' + data + '</a>'; } },
            { data: 'company_name' },
            { data: 'sector' },
            { data: 'exchange', visible: false, defaultContent: 'US' },
            { data: 'country', render: function(data) { return data || 'US'; } },
            {
                data: 'close_price',
                render: function(data, type, row) {
                    return type === 'display' ? formatCurrency(data, row.currency) : data;
                }
            },
            { data: 'rsi_14', render: function(data) { return `<span class="positive-val">${data.toFixed(2)}</span>`; } },
            { data: 'macd_hist', render: function(data) { return `<span class="positive-val">${data.toFixed(3)}</span>`; } }
        ]
    });

    $('#leadersExchangeFilter').on('change', function() {
        var val = $(this).val();
        if (val === 'ALL') {
            leadersTable.column(3).search('').draw();
        } else {
            leadersTable.column(3).search('^' + val + '$', true, false).draw();
        }
    });
});
