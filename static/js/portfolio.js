$(document).ready(function () {
    if (window.innerWidth > 768) return;

    var table = window._portfolioTable;
    if (!table) return;

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
        var cells = tr.querySelectorAll('td');
        // Column indices (0-based) match template order:
        // 0=Ticker 1=Company 2=Price 3=Change 4=GlobalValue 5=GlobalPnL
        // 6=50D 7=200D 8=PEG 9=PLPEG 10=StopLoss 11=RSI 12=MLConf
        // 13=VaR 14=Sentiment 15=Earnings 16=Score 17=Tags 18=Signal
        var company   = cells[1]  ? cells[1].textContent.trim() : '';
        var trend50   = cells[6]  ? cells[6].innerHTML          : '';
        var trend200  = cells[7]  ? cells[7].innerHTML          : '';
        var sentiment = cells[14] ? cells[14].innerHTML         : '';
        var score     = cells[16] ? cells[16].textContent.trim(): '';
        var tags      = cells[17] ? cells[17].innerHTML         : '';
        var signal    = cells[18] ? cells[18].innerHTML         : '';

        return '<div class="mob-detail">'
            + '<div class="mob-detail-company">' + company + '</div>'
            + '<div class="mob-detail-row">'
            +   '<span class="mob-detail-lbl">50D</span>' + trend50
            +   '&nbsp;&nbsp;<span class="mob-detail-lbl">200D</span>' + trend200
            + '</div>'
            + '<div class="mob-detail-row">'
            +   '<span class="mob-detail-lbl">Sentiment</span>' + sentiment
            + '</div>'
            + '<div class="mob-detail-row">'
            +   '<span class="mob-detail-lbl">Score</span><span>' + score + '</span>'
            +   '&nbsp;&nbsp;' + signal
            + '</div>'
            + '<div class="mob-detail-tags">' + tags + '</div>'
            + '</div>';
    }
});
