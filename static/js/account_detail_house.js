function _houseChartHeight() {
    // Mobile can't go below 400 — static/css/styles.css forces a 400px min-height
    // on .js-plotly-plot under 768px (for the Macro chart); a smaller value here
    // leaves the chart pinned short inside a taller, CSS-floored container.
    return window.innerWidth < 768 ? 400 : 350;
}

// This chart is embedded server-side (visuals.py's fig.to_html()) rather than created via
// the Plotly JS API, and its `config.responsive` never actually reacts to container size
// changes (rotation, fullscreen) — so width/height must be relayout'd explicitly on every resize.
const _HOUSE_CHART_OPTS = { getHeight: _houseChartHeight, forceWidth: true };

function toggleFullscreen(wrapperId) {
    ChartFullscreen.toggle(wrapperId, _HOUSE_CHART_OPTS);
}

document.addEventListener('DOMContentLoaded', () => {
    ChartFullscreen.relayoutForCurrentState('house-chart-outer-wrapper', _HOUSE_CHART_OPTS);
    window.addEventListener('resize', () => {
        ChartFullscreen.relayoutForCurrentState('house-chart-outer-wrapper', _HOUSE_CHART_OPTS);
    });
});

window.onTransactionChanged = function () {
    location.reload();
};
