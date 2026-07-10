// These charts are server-rendered (visuals_ai.py's fig.to_html()), so config.responsive
// never actually reacts to container size changes (rotation, fullscreen) — width/height
// must be relayout'd explicitly, per AGENTS.md rule 18.
const _AI_CONTAGION_CHART_WRAPPERS = [
    { outer: 'ai-perf-daily-outer-wrapper', inner: 'ai-perf-daily-wrapper' },
    { outer: 'ai-perf-intraday-outer-wrapper', inner: 'ai-perf-intraday-wrapper' },
    { outer: 'ai-corr-outer-wrapper', inner: 'ai-corr-wrapper' },
];
const _aiContagionChartDefaultHeights = {};

function _captureAiContagionChartDefaultHeights() {
    _AI_CONTAGION_CHART_WRAPPERS.forEach(function (w) {
        const plotEl = document.querySelector('#' + w.inner + ' .js-plotly-plot');
        if (plotEl && plotEl.layout) _aiContagionChartDefaultHeights[w.inner] = plotEl.layout.height;
    });
}

function _aiContagionChartOpts(innerWrapperId) {
    return { forceWidth: true, getHeight: function () { return _aiContagionChartDefaultHeights[innerWrapperId]; } };
}

function toggleFullscreen(outerWrapperId, innerWrapperId) {
    ChartFullscreen.toggle(outerWrapperId, Object.assign({ innerWrapperId }, _aiContagionChartOpts(innerWrapperId)));
}

document.addEventListener('DOMContentLoaded', _captureAiContagionChartDefaultHeights);

window.addEventListener('resize', function () {
    _AI_CONTAGION_CHART_WRAPPERS.forEach(function (w) {
        ChartFullscreen.relayoutForCurrentState(w.outer, Object.assign({ innerWrapperId: w.inner }, _aiContagionChartOpts(w.inner)));
    });
});
