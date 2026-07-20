window._wlHeatmapMode = false;
var _WL_HEATMAP_KEY = 'watchlist_heatmap_active';

function toggleHeatmap() {
    if (!window._wlHeatmapMode) { _wlEnterHeatmapMode(); } else { _wlExitHeatmapMode(); }
}

// #dataTable_length holds the Add Ticker/Change Period/Advanced Filter buttons appended by
// other scripts — hide only the table itself and its info/paginate/processing rows so that
// toolbar stays usable while the heatmap is showing, instead of hiding the whole wrapper.
function _wlSetTableRowsVisible(visible) {
    ['dataTable', 'dataTable_info', 'dataTable_paginate', 'dataTable_processing'].forEach(function (id) {
        var el = document.getElementById(id);
        if (el) el.style.display = visible ? '' : 'none';
    });
}

function _wlEnterHeatmapMode() {
    window._wlHeatmapMode = true;
    try { localStorage.setItem(_WL_HEATMAP_KEY, '1'); } catch (e) {}
    _wlSetTableRowsVisible(false);
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
    _wlSetTableRowsVisible(true);
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

$(document).ready(function () {
    $('#dataTable').on('draw.dt', function () {
        if (window._wlHeatmapMode) {
            var panel = document.getElementById('heatmap-panel');
            if (panel) _wlBuildHeatmap(panel);
        }
    });
});
