$.fn.dataTable.ext.errMode = 'none';

$(document).ready(function() {
    var garpTable = $('#garpTable').DataTable({
        ajax: {
            url: '/api/reports/garp-tenbaggers',
            error: function() { showTableError('#garpTable', 10); }
        },
        deferRender: true,
        responsive: true,
        pageLength: 10,
        order: [[4, 'asc']],

        columnDefs: [
            { responsivePriority: 1, targets: [0, 4] },
            { responsivePriority: 2, targets: [3, 5] },
            { responsivePriority: 3, targets: [9, 6] },
            { responsivePriority: 4, targets: [1, 2, 7, 8] }
        ],

        initComplete: function() {
            var api = this.api();
            var filterBar = $('<span class="rpt-filter-bar"></span>');

            var sectorSelect = $('<select id="garpSectorFilter" class="form-select"><option value="ALL">All Sectors</option></select>');
            api.column(2).data().unique().sort().each(function(d) {
                if (d && d !== 'N/A') sectorSelect.append('<option value="' + d + '">' + d + '</option>');
            });

            var mlWrapper = $('<span class="rpt-ml-wrapper"></span>');
            mlWrapper.append('<label for="garpMinMl" class="rpt-ml-label">Min ML %</label>');
            mlWrapper.append('<input type="number" id="garpMinMl" class="form-control input-width-small" min="0" max="100" step="5" value="0">');

            filterBar.append(sectorSelect).append(mlWrapper);
            $('#garpTable_wrapper .dataTables_filter').prepend(filterBar);
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
            { data: 'peter_lynch_peg', render: function(data) {
                if (data == null) return '—';
                return `<span class="positive-val">${data.toFixed(3)}</span>`;
            } },
            { data: 'revenue_growth_pct', render: function(data) {
                if (data == null) return '—';
                return `<span class="positive-val">${data.toFixed(1)}%</span>`;
            } },
            { data: 'roe_pct', render: function(data) {
                if (data == null) return '—';
                return `<span class="positive-val">${data.toFixed(1)}%</span>`;
            } },
            { data: 'forward_pe', render: function(data) {
                if (data == null) return '—';
                return data.toFixed(2);
            } },
            { data: 'market_cap', render: function(data, type) {
                if (data == null) return '—';
                if (type === 'sort' || type === 'type') return data;
                if (data >= 1e12) return `$${(data / 1e12).toFixed(2)}T`;
                if (data >= 1e9)  return `$${(data / 1e9).toFixed(2)}B`;
                return `$${(data / 1e6).toFixed(0)}M`;
            } },
            { data: 'ml_confidence_score', render: function(data) {
                if (data == null) return '<span class="text-secondary">—</span>';
                let cssClass = data >= 60 ? 'positive-val' : (data < 30 ? 'negative-val' : '');
                return `<span class="${cssClass}">${data.toFixed(1)}%</span>`;
            } }
        ]
    });

    $.fn.dataTable.ext.search.push(function(settings, data, dataIndex, rowData) {
        if (settings.nTable.id !== 'garpTable') return true;
        var threshold = parseFloat($('#garpMinMl').val());
        if (isNaN(threshold) || threshold <= 0) return true;
        var ml = rowData.ml_confidence_score;
        if (ml == null) return false;
        return ml >= threshold;
    });

    $(document).on('input change', '#garpMinMl', function() { garpTable.draw(); });
    $(document).on('change', '#garpSectorFilter', function() {
        var val = $(this).val();
        if (val === 'ALL') {
            garpTable.column(2).search('').draw();
        } else {
            garpTable.column(2).search('^' + val + '$', true, false).draw();
        }
    });
});
