window._wlHeatmapMode = false;
var _WL_HEATMAP_KEY = 'watchlist_heatmap_active';

function toggleHeatmap() {
    if (!window._wlHeatmapMode) { _wlEnterHeatmapMode(); } else { _wlExitHeatmapMode(); }
}

function _wlGetTableContainer() {
    return document.getElementById('dataTable_wrapper') || document.getElementById('dataTable');
}

function _wlEnterHeatmapMode() {
    window._wlHeatmapMode = true;
    try { localStorage.setItem(_WL_HEATMAP_KEY, '1'); } catch (e) {}
    var tbl = _wlGetTableContainer();
    if (tbl) tbl.style.display = 'none';
    var panel = document.getElementById('heatmap-panel');
    if (panel) {
        panel.style.display = 'block';
        _wlBuildHeatmap(panel);
    }
    var lnk = document.getElementById('heatmap-link');
    if (lnk) lnk.innerHTML = '&larr; Table';
}

function _wlExitHeatmapMode() {
    window._wlHeatmapMode = false;
    try { localStorage.removeItem(_WL_HEATMAP_KEY); } catch (e) {}
    var tbl = _wlGetTableContainer();
    if (tbl) tbl.style.display = '';
    var panel = document.getElementById('heatmap-panel');
    if (panel) {
        panel.style.display = 'none';
        panel.innerHTML = '';
    }
    var lnk = document.getElementById('heatmap-link');
    if (lnk) lnk.innerHTML = '&#9638; Heatmap';
}

function _wlBuildHeatmap(panel) {
    var rows = [];
    if (window._watchlistTable) {
        window._watchlistTable.rows({ filter: 'applied' }).nodes().each(function (node) {
            if (!node.classList.contains('child')) rows.push(node);
        });
    } else {
        document.querySelectorAll('#dataTable tbody tr:not(.child)').forEach(function (r) { rows.push(r); });
    }

    var items = rows.map(function (row) {
        return { ticker: row.dataset.ticker || '', change: parseFloat(row.dataset.changePct) };
    });
    window.HeatmapTreemap.render(panel, items);
}
