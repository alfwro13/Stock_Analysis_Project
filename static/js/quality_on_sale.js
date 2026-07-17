$.fn.dataTable.ext.errMode = 'none';

$(document).ready(function() {
    var qosTable = $('#qosTable').DataTable({
        ajax: {
            url: '/api/reports/quality-on-sale',
            error: function() { showTableError('#qosTable', 10); }
        },
        deferRender: true,
        responsive: true,
        pageLength: 10,
        order: [[9, 'desc'], [4, 'asc']],

        columnDefs: [
            { responsivePriority: 1, targets: [0, 9] },
            { responsivePriority: 2, targets: [3, 4] },
            { responsivePriority: 3, targets: [5, 7] },
            { responsivePriority: 4, targets: [1, 2, 6, 8] }
        ],

        initComplete: function() {
            var column = this.api().column(2);
            var select = $('<select id="qosSectorFilter" class="form-select me-2"><option value="ALL">All Sectors</option></select>');
            column.data().unique().sort().each(function(d) {
                if (d && d !== 'N/A') {
                    select.append('<option value="' + d + '">' + d + '</option>');
                }
            });
            $('#qosTable_wrapper .dataTables_filter').prepend(select);
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
            {
                data: 'pct_above_52w_low',
                render: function(data) {
                    if (data == null) return '—';
                    let cssClass = data <= 5 ? 'negative-val' : (data <= 10 ? 'text-warning' : '');
                    return `<span class="${cssClass}">+${data.toFixed(1)}%</span>`;
                }
            },
            { data: 'roe_pct', render: function(data) {
                if (data == null) return '—';
                return `<span class="positive-val">${data.toFixed(1)}%</span>`;
            } },
            { data: 'debt_to_equity', render: function(data) {
                if (data == null) return '—';
                return (data / 100).toFixed(2) + 'x';
            } },
            { data: 'margin_pct', render: function(data) {
                if (data == null) return '—';
                return `<span class="positive-val">${data.toFixed(1)}%</span>`;
            } },
            { data: 'trailing_pe', render: function(data) {
                if (data == null) return '—';
                return data.toFixed(1);
            } },
            { data: 'composite_score', render: function(data) {
                if (data == null) return '—';
                let cssClass = data >= 70 ? 'positive-val' : (data < 40 ? 'negative-val' : '');
                return `<span class="${cssClass}">${data}</span>`;
            } }
        ]
    });

    $(document).on('change', '#qosSectorFilter', function() {
        var val = $(this).val();
        if (val === 'ALL') {
            qosTable.column(2).search('').draw();
        } else {
            qosTable.column(2).search('^' + val + '$', true, false).draw();
        }
    });
});
