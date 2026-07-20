window._heatmapMode = false;
var _HEATMAP_KEY = 'portfolio_heatmap_active';

function toggleHeatmap() {
    if (!window._heatmapMode) { _enterHeatmapMode(); } else { _exitHeatmapMode(); }
}

function _enterHeatmapMode() {
    if (window._xrayMode) return;
    window._heatmapMode = true;
    try { localStorage.setItem(_HEATMAP_KEY, '1'); } catch(e) {}
    var tbl = _getTableContainer();
    if (tbl) tbl.style.display = 'none';
    var panel = document.getElementById('heatmap-panel');
    if (panel) {
        panel.style.display = 'block';
        _buildHeatmap(panel);
    }
    var lnk = document.getElementById('heatmap-link');
    if (lnk) lnk.innerHTML = '&larr; Table';
}

function _exitHeatmapMode() {
    window._heatmapMode = false;
    try { localStorage.removeItem(_HEATMAP_KEY); } catch(e) {}
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
        document.querySelectorAll('#dataTable tbody tr:not(.child)').forEach(function (r) { rows.push(r); });
    }

    var items = rows.map(function (row) {
        return {
            ticker: row.dataset.ticker || '',
            change: parseFloat(row.dataset.changePct),
            currency: row.dataset.currency || '',
            extendedSession: row.dataset.extendedSession || '',
            extendedPrice: parseFloat(row.dataset.extendedPrice),
            extendedChangePct: parseFloat(row.dataset.extendedChangePct)
        };
    });
    window.HeatmapTreemap.render(panel, items, !!window.SHOW_EXTENDED_HOURS);
}
