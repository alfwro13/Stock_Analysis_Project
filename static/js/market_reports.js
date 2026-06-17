let mrDataTable;
let divDataTable;
let mrActiveBtn = null;
let divActiveBtn = null;

$.fn.dataTable.ext.errMode = 'none';

function formatCurrency(value, currencyCode) {
    if (value === null || value === undefined) return 'N/A';
    let num = parseFloat(value);
    if (isNaN(num)) return 'N/A';

    let symbol = '$';
    if (currencyCode === 'GBp') {
        num = num / 100.0;
        symbol = '£';
    } else if (currencyCode === 'GBP') {
        symbol = '£';
    } else if (currencyCode === 'EUR') {
        symbol = '€';
    } else if (currencyCode && currencyCode !== 'USD') {
        return num.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2}) + ' ' + currencyCode;
    }
    return symbol + num.toLocaleString('en-US', {minimumFractionDigits: 2, maximumFractionDigits: 2});
}

function setButtonLoading(btn, isLoading) {
    if (!btn) return;
    btn.disabled = isLoading;
    btn.textContent = isLoading ? '⏳ Loading…' : '🔄 Run Query';
}

function showTableError(tableSelector, colSpan) {
    $(tableSelector + ' tbody').html(
        '<tr><td colspan="' + colSpan + '" class="table-error-cell">⚠️ Failed to load data. Please refresh the page or try again.</td></tr>'
    );
}

$(document).ready(function() {
    const urlParams = new URLSearchParams(window.location.search);

    // Sector Table
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

    // Leaders Table
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

    // Quality Compounders Table
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

    // GARP Tenbaggers Table
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

    // Quality on Sale Table
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

    // Mean Reversion Table
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

    // Dividend Harvest Table
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

    loadMeanReversionData();
    loadDividendData();
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
