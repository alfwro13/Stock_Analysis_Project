function _houseChartHeight() {
    // Mobile can't go below 400 — static/css/styles.css forces a 400px min-height
    // on .js-plotly-plot under 768px (for the Macro chart); a smaller value here
    // leaves the chart pinned short inside a taller, CSS-floored container.
    return window.innerWidth < 768 ? 400 : 350;
}

// This chart is embedded server-side (visuals.py's fig.to_html()) rather than created via
// the Plotly JS API, and its `config.responsive` never actually reacts to container size
// changes (rotation, fullscreen) — so width/height must be relayout'd explicitly on every resize.
function _relayoutHouseChart(height) {
    const plotEl = document.querySelector('#house-chart-wrapper .js-plotly-plot');
    if (!plotEl || !window.Plotly) return;
    Plotly.relayout(plotEl, { width: plotEl.getBoundingClientRect().width, height });
}

function toggleFullscreen(wrapperId) {
    const wrapper = document.getElementById(wrapperId);
    if (!wrapper) return;
    const isFullscreen = wrapper.classList.contains('is-fullscreen');
    const btn = wrapper.querySelector('.fullscreen-btn');
    if (isFullscreen) {
        wrapper.classList.remove('is-fullscreen');
        if (btn) btn.innerHTML = '&#9638; Fullscreen';
        _relayoutHouseChart(_houseChartHeight());
    } else {
        wrapper.classList.add('is-fullscreen');
        if (btn) btn.innerHTML = '&#10006; Exit Fullscreen';
        _relayoutHouseChart(window.innerHeight - 120);
    }
}

document.addEventListener('DOMContentLoaded', () => {
    _relayoutHouseChart(_houseChartHeight());
    window.addEventListener('resize', () => {
        const wrapper = document.getElementById('house-chart-outer-wrapper');
        const isFullscreen = wrapper && wrapper.classList.contains('is-fullscreen');
        _relayoutHouseChart(isFullscreen ? window.innerHeight - 120 : _houseChartHeight());
    });
});

window.onTransactionChanged = function () {
    location.reload();
};
