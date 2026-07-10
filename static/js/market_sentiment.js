$(document).ready(function() {
    $('#usCalendarTable').DataTable({
        deferRender: true,
        responsive: true,
        order: [[0, 'asc']],
        pageLength: 10,
        lengthChange: false,
        searching: false,
        info: false
    });

    $('#ukCalendarTable').DataTable({
        deferRender: true,
        responsive: true,
        order: [[0, 'asc']],
        pageLength: 10,
        lengthChange: false,
        searching: false,
        info: false
    });
});

// All charts on this page are server-rendered (visuals.py's fig.to_html()), so
// config.responsive never actually reacts to container size changes (rotation,
// fullscreen) — width/height must be relayout'd explicitly, per AGENTS.md rule 18.
const _SENTIMENT_CHART_IDS = [
    'us-fg-wrapper', 'us-vix-wrapper', 'us-yield-wrapper', 'us-yield-curve-wrapper',
    'us-liquidity-wrapper', 'us-inflation-wrapper', 'us-credit-wrapper',
    'uk-gbp-wrapper', 'uk-yield-wrapper', 'uk-liquidity-wrapper', 'uk-inflation-wrapper', 'uk-credit-wrapper',
];
const _sentimentChartDefaultHeights = {};

function _captureSentimentChartDefaultHeights() {
    _SENTIMENT_CHART_IDS.forEach(function (id) {
        const wrapper = document.getElementById(id);
        const plotEl = wrapper && wrapper.querySelector('.js-plotly-plot');
        if (plotEl && plotEl.layout) _sentimentChartDefaultHeights[id] = plotEl.layout.height;
    });
}

function _sentimentChartOpts(wrapperId) {
    return { forceWidth: true, getHeight: function () { return _sentimentChartDefaultHeights[wrapperId]; } };
}

function toggleFullscreen(wrapperId) {
    ChartFullscreen.toggle(wrapperId, _sentimentChartOpts(wrapperId));
}

document.addEventListener('DOMContentLoaded', _captureSentimentChartDefaultHeights);

window.addEventListener('resize', function () {
    _SENTIMENT_CHART_IDS.forEach(function (id) {
        ChartFullscreen.relayoutForCurrentState(id, _sentimentChartOpts(id));
    });
});
