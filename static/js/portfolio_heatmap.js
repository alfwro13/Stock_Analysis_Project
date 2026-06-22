window._heatmapMode = false;

function toggleHeatmap() {
    if (!window._heatmapMode) { _enterHeatmapMode(); } else { _exitHeatmapMode(); }
}

function _enterHeatmapMode() {
    if (window._xrayMode) return;
    window._heatmapMode = true;
    var tbl = _getTableContainer();
    if (tbl) tbl.style.display = 'none';
    var panel = document.getElementById('heatmap-panel');
    if (panel) {
        panel.style.display = 'flex';
        _buildHeatmap(panel);
    }
    var lnk = document.getElementById('heatmap-link');
    if (lnk) lnk.innerHTML = '&larr; Table';
}

function _exitHeatmapMode() {
    window._heatmapMode = false;
    var tbl = _getTableContainer();
    if (tbl) tbl.style.display = '';
    var panel = document.getElementById('heatmap-panel');
    if (panel) {
        panel.style.display = 'none';
        panel.innerHTML = '';
    }
    var lnk = document.getElementById('heatmap-link');
    if (lnk) lnk.innerHTML = '&#9638; Heatmap';
}

function _buildHeatmap(panel) {
    var rows = [];
    if (window._portfolioTable) {
        window._portfolioTable.rows({ filter: 'applied' }).nodes().each(function (node) {
            if (!node.classList.contains('child')) rows.push(node);
        });
    } else {
        document.querySelectorAll('#dataTable tbody tr:not(.child)').forEach(function (r) {
            rows.push(r);
        });
    }

    var items = rows.map(function (row) {
        return {
            ticker: row.dataset.ticker || '',
            change: parseFloat(row.dataset.changePct || '0')
        };
    }).filter(function (d) { return d.ticker; });

    if (!items.length) {
        panel.innerHTML = '<p class="heatmap-empty">No data to display.</p>';
        return;
    }

    var maxAbs = Math.max.apply(null, items.map(function (d) { return Math.abs(d.change); }));
    var html = '';
    items.forEach(function (d) {
        var abs  = Math.abs(d.change);
        var norm = maxAbs > 0 ? abs / maxAbs : 0.5;
        var size = Math.round(70 + norm * 130);
        var cls  = d.change > 0.001 ? 'heatmap-tile-gain'
                 : d.change < -0.001 ? 'heatmap-tile-loss'
                 : 'heatmap-tile-flat';
        var sign  = d.change > 0 ? '+' : '';
        var label = sign + d.change.toFixed(2) + '%';
        html += '<div class="heatmap-tile ' + cls + '" style="width:' + size + 'px;height:' + size + 'px;">'
              +   '<span class="heatmap-tile-ticker">' + d.ticker + '</span>'
              +   '<span class="heatmap-tile-change">' + label + '</span>'
              + '</div>';
    });
    panel.innerHTML = html;
}
