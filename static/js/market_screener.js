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

$(document).ready(function() {
    const urlParams = new URLSearchParams(window.location.search);
    const targetSector = urlParams.get('sector');
    const reqBullishCross = urlParams.get('bullish_cross') === 'true';
    const reqAbove50d = urlParams.get('above_50d') === 'true';

    if (targetSector || reqBullishCross || reqAbove50d) {
        let filterLabels = [];
        if (targetSector) filterLabels.push(`Sector: ${targetSector}`);
        if (reqBullishCross) filterLabels.push('Golden MACD Crosses Only');
        if (reqAbove50d) filterLabels.push('Price > 50D SMA');

        const bannerHtml = `
            <div class="active-filters-banner mb-3" id="activeFiltersBanner">
                <div>
                    <strong class="text-accent-cyan">Active Deep-Link Filters:</strong>
                    <span class="text-light ms-2 fw-bold">${filterLabels.join(' &nbsp;|&nbsp; ')}</span>
                </div>
                <button class="btn btn-danger btn-sm" onclick="window.location.href='/market-screener'">✖ Clear Filters</button>
            </div>
        `;
        $('#screenerTable').parent().prepend(bannerHtml);
    }

    // Core Multi-Filtering Engine
    $.fn.dataTable.ext.search.push(
        function(settings, data, dataIndex, rowData) {
            // Legacy URL Params Override
            if (targetSector && rowData.sector !== targetSector) return false;
            if (reqBullishCross && rowData.bullish_cross !== 1 && rowData.bullish_cross !== true) return false;
            if (reqAbove50d) {
                const price = parseFloat(rowData.close_price);
                const sma50 = parseFloat(rowData.sma_50);
                if (isNaN(price) || isNaN(sma50) || price <= sma50) return false;
            }

            // --- Dynamic UI Filters ---
            const typeVal = $('#typeFilter').val();
            if (typeVal !== 'ALL' && rowData.quote_type !== typeVal) return false;

            const exchVal = $('#exchangeFilter').val();
            if (exchVal !== 'ALL' && rowData.exchange !== exchVal) return false;

            const secVal = $('#sectorFilter').val();
            if (secVal !== 'ALL' && rowData.sector !== secVal) return false;

            // RSI Logic
            const rsiOp = $('#rsiOp').val();
            const rsiVal = parseFloat($('#rsiVal').val());
            const rsiData = parseFloat(rowData.rsi_14);
            if (rsiOp !== 'any' && !isNaN(rsiVal)) {
                if (isNaN(rsiData)) return false;
                if (rsiOp === 'gt' && rsiData <= rsiVal) return false;
                if (rsiOp === 'lt' && rsiData >= rsiVal) return false;
            }

            // MACD Logic
            const macdOp = $('#macdOp').val();
            const macdVal = parseFloat($('#macdVal').val());
            const macdData = parseFloat(rowData.macd_hist);
            if (macdOp !== 'any' && !isNaN(macdVal)) {
                if (isNaN(macdData)) return false;
                if (macdOp === 'gt' && macdData <= macdVal) return false;
                if (macdOp === 'lt' && macdData >= macdVal) return false;
            }

            return true;
        }
    );

    var table = $('#screenerTable').DataTable({
        ajax: '/api/screener-data',
        deferRender: true,
        responsive: true,
        pageLength: 25,
        order: [[0, 'asc']],
        language: { search: "🔍 Search Everything:" },
        dom: '<"top-controls"f>rt<"bottom-controls"<"bottom-left-controls"li>p>',

        columnDefs: [
            { responsivePriority: 1, targets: [0, 6] },
            { responsivePriority: 2, targets: [8, 9] },
            { responsivePriority: 3, targets: [13, 14] },
            { responsivePriority: 4, targets: [12, 16] },
            { responsivePriority: 5, targets: [1, 7, 15] },
            { responsivePriority: 6, targets: [2, 3, 4, 10, 11, 17, 18] }
        ],

        initComplete: function () {
            var exchCol = this.api().column(4);
            var secCol = this.api().column(3);

            var exchSelect = $('#exchangeFilter');
            var secSelect = $('#sectorFilter');

            exchCol.data().unique().sort().each( function ( d ) {
                if (d && d !== 'N/A') {
                    exchSelect.append( '<option value="'+d+'">'+d+'</option>' );
                }
            });

            secCol.data().unique().sort().each( function ( d ) {
                if (d && d !== 'N/A' && d !== 'Unclassified') {
                    secSelect.append( '<option value="'+d+'">'+d+'</option>' );
                }
            });

            // Set Asset Type to Equity and hide fund columns
            $('#typeFilter').val('EQUITY');
            table.column(17).visible(false); // Expense Ratio
            table.column(18).visible(false); // FT Link

            // Set Exchange (from URL or fallback to ALL)
            const defaultExchange = urlParams.get('exchange') || 'ALL';
            if (defaultExchange !== 'ALL' && exchSelect.find('option[value="' + defaultExchange + '"]').length > 0) {
                exchSelect.val(defaultExchange);
            } else {
                exchSelect.val('ALL');
            }

            // Set Sector default (Technology)
            const defaultSector = urlParams.get('sector') || 'Technology';
            if (defaultSector !== 'ALL' && secSelect.find('option[value="' + defaultSector + '"]').length > 0) {
                secSelect.val(defaultSector);
            }

            table.draw();
        },

        columns: [
            { data: 'ticker', render: function(data) { return '<a href="/stock/' + data + '" class="ticker-link">' + data + '</a>'; } },
            { data: 'company_name', render: function(data) { return data || 'N/A'; } },
            { data: 'subtitle', render: function(data) { return data || 'N/A'; } },
            { data: 'sector', render: function(data) { return data || 'N/A'; } },
            { data: 'exchange', render: function(data) { return data || 'US'; } },
            { data: 'quote_type', visible: false },
            {
                data: 'close_price',
                render: function(data, type, row) {
                    return type === 'display' ? formatCurrency(data, row.currency) : data;
                }
            },
            { data: 'volume', render: $.fn.dataTable.render.number(',', '.', 0, '') },
            {
                data: 'rsi_14',
                render: function(data) {
                    if (data === null || data === undefined) return 'N/A';
                    let cssClass = data > 70 ? 'negative-val' : (data < 30 ? 'positive-val' : '');
                    return `<span class="${cssClass}">${data.toFixed(2)}</span>`;
                }
            },
            {
                data: 'macd_hist',
                render: function(data) {
                    if (data === null || data === undefined) return 'N/A';
                    let cssClass = data > 0 ? 'positive-val' : 'negative-val';
                    return `<span class="${cssClass}">${data.toFixed(3)}</span>`;
                }
            },
            {
                data: 'sma_50',
                render: function(data, type, row) {
                    return type === 'display' ? formatCurrency(data, row.currency) : data;
                }
            },
            {
                data: 'sma_200',
                render: function(data, type, row) {
                    return type === 'display' ? formatCurrency(data, row.currency) : data;
                }
            },
            { data: 'volume_surge', render: function(data) { return data ? '<span class="badge-yes">YES</span>' : '<span class="badge-no">No</span>'; } },
            { data: 'bullish_cross', render: function(data) { return data ? '<span class="badge-yes">YES</span>' : '<span class="badge-no">No</span>'; } },
            {
                data: 'ml_confidence_score',
                render: function(data, type, row) {
                    if (data === null || data === undefined) return 'N/A';

                    if (type === 'display') {
                        let cssClass = data >= 40 ? 'metric-excellent' : (data < 20 ? 'metric-poor' : 'metric-neutral');
                        let html = `<span class="${cssClass}">${data.toFixed(1)}%</span>`;

                        if (row.composite_score !== undefined && row.composite_score !== null && row.composite_score !== 'N/A') {
                            let comp = parseInt(row.composite_score);
                            if (!isNaN(comp)) {
                                if (comp >= 70 && data < 20) {
                                    html += ` <div class="d-inline-block"><abbr title="High Quant Score but Low AI Confidence" class="no-decoration-abbr"><span class="setup-tag">⚠️ Divergence</span></abbr></div>`;
                                } else if (comp < 40 && data >= 40) {
                                    html += ` <div class="d-inline-block"><abbr title="Low Quant Score but High AI Confidence" class="no-decoration-abbr"><span class="setup-tag">🤖 AI Buy</span></abbr></div>`;
                                }
                            }
                        }
                        return html;
                    }
                    return data;
                }
            },
            {
                data: 'var_95',
                render: function(data, type, row) {
                    if (data === null || data === undefined) return 'N/A';

                    if (type === 'display') {
                        let cssClass = data > 0.05 ? 'metric-poor' : 'metric-neutral';
                        let cvarText = row.cvar_95 !== null && row.cvar_95 !== undefined ? `Log-Return Expected Shortfall (CVaR): ${(row.cvar_95 * 100).toFixed(2)}%` : 'CVaR: N/A';
                        return `<abbr title="${cvarText}" class="help-abbr"><span class="${cssClass}">${(data * 100).toFixed(2)}%</span></abbr>`;
                    }
                    return data;
                }
            },
            {
                data: 'sentiment_score',
                render: function(data, type) {
                    if (data === null || data === undefined) return 'N/A';

                    if (type === 'display') {
                        let sScore = parseFloat(data);
                        let sClass = '';
                        let sText = '';

                        if (sScore > 0.6) { sClass = 'sent-euphoria'; sText = 'Euphoria'; }
                        else if (sScore > 0.2) { sClass = 'sent-bullish'; sText = 'Bullish'; }
                        else if (sScore >= -0.2) { sClass = 'sent-neutral'; sText = 'Neutral'; }
                        else if (sScore > -0.6) { sClass = 'sent-bearish'; sText = 'Bearish'; }
                        else { sClass = 'sent-fear'; sText = 'Extreme Fear'; }

                        return `<span class="sent-badge ${sClass}">${sText} (${sScore.toFixed(3)})</span>`;
                    }
                    return data;
                }
            },
            { data: 'expense_ratio', render: function(data) { return data ? data + '%' : 'N/A'; } },
            { data: 'freetrade_link', render: function(data) { return data && data !== 'N/A' ? '<a href="' + data + '" target="_blank" class="text-accent-purple">KIID Document</a>' : 'N/A'; } }
        ]
    });

    // Live event listeners to trigger instant filtering
    $('#typeFilter, #exchangeFilter, #sectorFilter, #rsiOp, #macdOp').on('change', function() {
        if (this.id === 'typeFilter') {
            var val = $(this).val();
            if (val === 'EQUITY') {
                table.column(17).visible(false);
                table.column(18).visible(false);
            } else if (val === 'ETF' || val === 'MUTUALFUND') {
                table.column(17).visible(true);
                table.column(18).visible(true);
            } else {
                table.column(17).visible(true);
                table.column(18).visible(true);
            }
        }
        table.draw();
    });

    $('#rsiVal, #macdVal').on('keyup change', function() {
        table.draw();
    });

    $('#clearFiltersBtn').on('click', function() {
        $('#typeFilter').val('EQUITY');
        $('#exchangeFilter').val('ALL');
        $('#sectorFilter').val('Technology');

        $('#rsiOp').val('any');
        $('#rsiVal').val('');
        $('#macdOp').val('any');
        $('#macdVal').val('');

        table.column(17).visible(false);
        table.column(18).visible(false);

        table.search('').draw();
    });
});
