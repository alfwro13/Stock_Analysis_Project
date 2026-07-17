$.fn.dataTable.ext.errMode = 'none';

$(document).ready(function() {
    var qualityTable = $('#qualityTable').DataTable({
        ajax: {
            url: '/api/reports/quality-compounders',
            error: function() { showTableError('#qualityTable', 9); }
        },
        deferRender: true,
        responsive: true,
        pageLength: 10,
        order: [[8, 'desc'], [4, 'desc']],

        columnDefs: [
            { responsivePriority: 1, targets: [0, 8] },
            { responsivePriority: 2, targets: [3, 4] },
            { responsivePriority: 3, targets: [5, 7] },
            { responsivePriority: 4, targets: [1, 2, 6] }
        ],

        initComplete: function() {
            var column = this.api().column(2);
            var select = $('<select id="qualitySectorFilter" class="form-select me-2"><option value="ALL">All Sectors</option></select>');
            column.data().unique().sort().each(function(d) {
                if (d && d !== 'N/A') {
                    select.append('<option value="' + d + '">' + d + '</option>');
                }
            });
            $('#qualityTable_wrapper .dataTables_filter').prepend(select);
        },

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
            { data: 'roe_pct', render: function(data) { return `<span class="positive-val">${data.toFixed(2)}%</span>`; } },
            { data: 'margin_pct', render: function(data) { return `<span class="positive-val">${data.toFixed(2)}%</span>`; } },
            { data: 'debt_to_equity', render: function(data) { return data.toFixed(2); } },
            { data: 'trailing_pe', render: function(data) { return data.toFixed(2); } },
            { data: 'composite_score', render: function(data) { return `<span class="positive-val">${data}</span>`; } }
        ]
    });

    $(document).on('change', '#qualitySectorFilter', function() {
        var val = $(this).val();
        if (val === 'ALL') {
            qualityTable.column(2).search('').draw();
        } else {
            qualityTable.column(2).search('^' + val + '$', true, false).draw();
        }
    });
});
