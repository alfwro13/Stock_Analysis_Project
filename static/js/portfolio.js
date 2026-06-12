$(document).ready(function () {
    if (window.innerWidth > 768) return;

    // Apply summary row layout via JS as well as CSS (defensive against cache)
    var summaryRow = document.querySelector('.summary-row');
    if (summaryRow) {
        summaryRow.style.flexWrap = 'wrap';
        summaryRow.style.padding = '10px';
        summaryRow.style.gap = '6px';
    }
    var actionLinks = document.querySelector('.portfolio-action-links');
    if (actionLinks) actionLinks.style.display = 'none';

    var table = window._portfolioTable;
    if (!table) return;

    // Hide non-essential columns via DataTables API (CSS alone can be overridden by DT)
    // 1=Company 6=50D 7=200D 8=PEG 9=PLPEG 10=StopLoss 11=RSI 12=MLConf
    // 13=VaR 14=Sentiment 15=Earnings 16=Score 17=Tags 18=Signal
    [1, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18].forEach(function (i) {
        table.column(i).visible(false);
    });

    // Accordion: one expanded row at a time
    $('#dataTable tbody').on('click', 'tr.live-asset-row', function () {
        var tr = this;
        var row = table.row(tr);

        if (row.child.isShown()) {
            row.child.hide();
            $(tr).removeClass('row-expanded');
        } else {
            table.rows('.row-expanded').every(function () {
                this.child.hide();
                $(this.node()).removeClass('row-expanded');
            });
            row.child(_buildDetail(tr)).show();
            $(tr).addClass('row-expanded');
        }
    });

    function _buildDetail(tr) {
        // Read from server-rendered data-* attributes — immune to DataTables DOM manipulation
        var d = tr.dataset;
        var company  = d.detailCompany  || '';
        var trend50  = d.detailTrend50  || '';
        var trend200 = d.detailTrend200 || '';
        var score    = d.detailScore    || '';
        var signal   = d.detailSignal   || '';
        var sentRaw  = d.detailSentiment;
        var mlRaw    = d.detailMl;
        var tagNames = (d.tags || '').trim().split(/\s+/).filter(Boolean);

        var sentScore = (sentRaw !== '' && sentRaw !== undefined) ? parseFloat(sentRaw) : null;
        var mlScore   = (mlRaw  !== '' && mlRaw  !== undefined) ? parseFloat(mlRaw)   : null;

        // Trend badges
        var t50Html  = trend50  ? '<span class="' + (trend50  === 'UP' ? 'trend-up' : 'trend-down') + '">' + trend50  + '</span>' : 'N/A';
        var t200Html = trend200 ? '<span class="' + (trend200 === 'UP' ? 'trend-up' : 'trend-down') + '">' + trend200 + '</span>' : 'N/A';

        // Sentiment badge
        var sentHtml = 'N/A';
        if (sentScore !== null && !isNaN(sentScore)) {
            var sc, sl;
            if      (sentScore > 0.6)  { sc = 'sent-euphoria'; sl = 'Euphoria'; }
            else if (sentScore > 0.2)  { sc = 'sent-bullish';  sl = 'Bullish'; }
            else if (sentScore >= -0.2) { sc = 'sent-neutral';  sl = 'Neutral'; }
            else if (sentScore > -0.6) { sc = 'sent-bearish';  sl = 'Bearish'; }
            else                        { sc = 'sent-fear';     sl = 'Extreme Fear'; }
            sentHtml = '<span class="sent-badge ' + sc + '">' + sl + ' (' + sentScore.toFixed(3) + ')</span>';
        }

        // Signal span
        var sigClass = signal.replace(/ /g, '-').replace(/\//g, '-');
        var sigHtml  = signal ? '<span class="' + sigClass + '">' + signal + '</span>' : '';

        // Setup tag pills
        var tagsHtml = tagNames.map(function (t) {
            return '<span class="setup-tag">' + t + '</span>';
        }).join(' ');

        // Special computed tags
        var comp = parseInt(score, 10);
        if (!isNaN(comp) && mlScore !== null) {
            if      (comp >= 70 && mlScore < 20)  tagsHtml += ' <span class="setup-tag">⚠️ AI Disconnect</span>';
            else if (comp < 40  && mlScore >= 40) tagsHtml += ' <span class="setup-tag">🤖 AI Buy</span>';
        }

        return '<div class="mob-detail">'
            + '<div class="mob-detail-company">' + company + '</div>'
            + '<div class="mob-detail-row"><span class="mob-detail-lbl">50D</span> ' + t50Html
            + '  <span class="mob-detail-lbl">200D</span> ' + t200Html + '</div>'
            + '<div class="mob-detail-row"><span class="mob-detail-lbl">Sentiment</span> ' + sentHtml + '</div>'
            + '<div class="mob-detail-row"><span class="mob-detail-lbl">Score</span> ' + score
            + '  ' + sigHtml + '</div>'
            + (tagsHtml ? '<div class="mob-detail-tags">' + tagsHtml + '</div>' : '')
            + '</div>';
    }
});
