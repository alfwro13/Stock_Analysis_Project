$.fn.dataTable.ext.errMode = 'none';

$(document).ready(function() {
    const urlParams = new URLSearchParams(window.location.search);

    var sectorTable = $('#sectorTable').DataTable({
        ajax: {
            url: '/api/reports/sectors',
            error: function() { showTableError('#sectorTable', 6); }
        },
        deferRender: true,
        responsive: true,
        paging: false,
        info: false,
        searching: true,
        dom: 't',
        order: [[3, 'desc']],

        columnDefs: [
            { responsivePriority: 1, targets: 0 },
            { responsivePriority: 2, targets: 3 },
            { responsivePriority: 3, targets: 4 },
            { responsivePriority: 4, targets: [2, 5] }
        ],

        initComplete: function () {
            var column = this.api().column(1);
            var select = $('#sectorExchangeFilter');

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
            {
                data: 'sector',
                render: function(data) {
                    const encodedSector = encodeURIComponent(data);
                    return `<a href="/market-screener?sector=${encodedSector}" class="text-accent-bold" title="View all ${data} stocks">${data}</a>`;
                }
            },
            { data: 'exchange', visible: false, defaultContent: 'US' },
            { data: 'total_stocks' },
            {
                data: 'avg_rsi',
                render: function(data) {
                    if (data == null) return '—';
                    let cssClass = data > 60 ? 'positive-val' : (data < 40 ? 'negative-val' : '');
                    return `<span class="${cssClass}">${data.toFixed(2)}</span>`;
                }
            },
            {
                data: 'pct_above_50d',
                render: function(data, type, row) {
                    if (data == null) return '—';
                    const encodedSector = encodeURIComponent(row.sector);
                    return `<a href="/market-screener?sector=${encodedSector}&above_50d=true" class="text-light text-decoration-dotted" title="Filter Screener for ${row.sector} > 50D SMA">${data.toFixed(2)}%</a>`;
                }
            },
            {
                data: 'pct_bullish_cross',
                render: function(data, type, row) {
                    if (data == null) return '—';
                    const encodedSector = encodeURIComponent(row.sector);
                    return `<a href="/market-screener?sector=${encodedSector}&bullish_cross=true" class="text-light text-decoration-dotted" title="Filter Screener for ${row.sector} Golden Crosses">${data.toFixed(2)}%</a>`;
                }
            }
        ]
    });

    $('#sectorExchangeFilter').on('change', function() {
        var val = $(this).val();
        if (val === 'ALL') {
            sectorTable.column(1).search('').draw();
        } else {
            sectorTable.column(1).search('^' + val + '$', true, false).draw();
        }
    });
});
